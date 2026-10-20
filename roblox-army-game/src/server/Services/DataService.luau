--!strict
--[[
	DataService
	Loads, saves and syncs PlayerData.

	API (server):
		DataService:Get(player): PlayerData?          -- live table, mutate it then MarkDirty
		DataService:WaitFor(player, timeout?): PlayerData?
		DataService:MarkDirty(player)                  -- sync to client soon + save later
		DataService:AddCash(player, amount, opts?): number   -- rounded to whole dollars
			opts.boostable = true -> multiplied by BoostService:GetCashMultiplier (2x boost)
			opts.source = "runner" | "section" | "combat" | ... (for logging)
			returns the amount actually added
		DataService:CanAfford(player, amount): boolean
		DataService:SpendCash(player, amount): boolean -- false = not enough cash
		DataService:SaveNow(player): boolean           -- true only when the save really reached the DataStore
		DataService:IsSaving(): boolean                -- false = running without saving (Studio without API access)
		DataService:HasReceipt(player, purchaseId) / AddReceipt(player, purchaseId)
		DataService.PlayerLoaded: Signal(player, data)
		DataService.PlayerRemoving: Signal(player, data)  -- before the final save

	How saving stays safe:
	  * A live server never hands out a blank save. Until the DataStore answers,
	    joining players wait (and are asked to rejoin after a minute) instead of
	    getting a fresh profile that could later overwrite their real one.
	  * Session lock: every save record carries `_lock = { jobId, t }`. A server
	    loads a player only when no OTHER live server holds the lock (it waits up
	    to ~20 s for the previous server's final save, then takes over a stale
	    lock). A server that lost the lock never writes again, so an old session
	    can never roll back a newer one.
	  * Only profiles that were loaded from the DataStore are ever written back.

	Without DataStore access in Studio (an unpublished place, or "Enable Studio
	Access to API Services" off) the game still runs; progress just is not
	saved. See README.md to turn saving on.
]]

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Format = require(Shared.Format)
local Net = require(Shared.Net)
local Signal = require(Shared.Signal)
local Types = require(Shared.Types)

type PlayerData = Types.PlayerData

local DataService = {}
DataService.Priority = 100 -- initialised first
DataService.PlayerLoaded = Signal.new()
DataService.PlayerRemoving = Signal.new()

local IS_STUDIO = RunService:IsStudio()

local Services: { [string]: any } = {}
local store: DataStore? = nil
-- "pending" until the DataStore answered, then "ready" or (Studio only) "unavailable".
local storeState = "pending"
local profiles: { [Player]: PlayerData } = {}
local dirtySave: { [Player]: boolean } = {}
local dirtySync: { [Player]: boolean } = {}
local loading: { [Player]: boolean } = {}
local loadedFromStore: { [Player]: boolean } = {}
local lockLost: { [Player]: boolean } = {}
-- Set while a player's final (lock-releasing) save runs: no other save may
-- start for them, so a late autosave can never take the lock back.
local releasing: { [Player]: boolean } = {}

local MAX_RECEIPTS = 60
local LOAD_WAIT_LIMIT = 60 -- live servers: seconds to wait for the DataStore before asking to rejoin
local LOCK_WAIT = 20 -- seconds to wait for another server's final save before taking over its lock
-- A lock not refreshed for this long belongs to a server that stopped (crash).
-- Locks are refreshed on every save (autosave every Config.AutosaveInterval s).
local LOCK_STALE = math.max(180, Config.AutosaveInterval * 3)

local function keyFor(player: Player): string
	return "u_" .. player.UserId
end

local function withRetries<T>(label: string, fn: () -> T): (boolean, T?)
	local delaySeconds = 1
	for attempt = 1, 4 do
		local ok, result = pcall(fn)
		if ok then
			return true, result
		end
		warn(("[DataService] %s failed (attempt %d): %s"):format(label, attempt, tostring(result)))
		if attempt < 4 then
			task.wait(delaySeconds)
			delaySeconds *= 2
		end
	end
	return false, nil
end

local function openStore()
	local attempt = 0
	while true do
		attempt += 1
		local lastError: any = nil
		local ok, result = pcall(function()
			return DataStoreService:GetDataStore(Config.DataStoreName)
		end)
		if ok then
			-- In Studio, a place without "Enable Studio Access to API Services" errors here.
			local probeOk, probeErr = pcall(function()
				(result :: DataStore):GetAsync("__probe")
			end)
			if probeOk then
				store = result
				storeState = "ready"
				return
			end
			lastError = probeErr
		else
			lastError = result
		end

		if IS_STUDIO and attempt >= 2 then
			warn("[DataService] DataStores unavailable in Studio, progress will NOT be saved: " .. tostring(lastError))
			storeState = "unavailable"
			return
		end
		-- Live server: never give up (players wait instead of getting a blank save).
		warn(("[DataService] DataStore not ready yet (attempt %d): %s"):format(attempt, tostring(lastError)))
		task.wait(math.min(30, 2 ^ attempt))
	end
end

-- True when `lock` belongs to another server that is still alive.
local function lockedByOther(lock: any): boolean
	if type(lock) ~= "table" then
		return false
	end
	if lock.jobId == game.JobId then
		return false
	end
	local t = tonumber(lock.t)
	return t ~= nil and os.time() - t < LOCK_STALE
end

-- Loads the record and takes the session lock in one atomic UpdateAsync.
-- Returns the data, or nil when the DataStore kept failing.
local function loadWithLock(activeStore: DataStore, player: Player): PlayerData?
	local key = keyFor(player)
	local started = os.clock()
	local forceTakeover = false
	while player.Parent do
		local loaded: PlayerData? = nil
		local blocked = false
		local ok = withRetries("load " .. player.Name, function()
			loaded = nil
			blocked = false
			activeStore:UpdateAsync(key, function(old: any)
				-- UpdateAsync may run this callback more than once (write
				-- conflicts): only the LAST run decides the outcome.
				loaded = nil
				blocked = false
				if old ~= nil and not forceTakeover and lockedByOther(old._lock) then
					blocked = true
					return nil -- cancel the write: another server still owns this save
				end
				local record = if old ~= nil then Types.Reconcile(old) else Types.DefaultData()
				-- Saves from before whole-dollar money may hold cents.
				record.cash = math.floor(record.cash)
				record.lifetimeCash = math.floor(record.lifetimeCash);
				(record :: any)._lock = { jobId = game.JobId, t = os.time() }
				loaded = record
				return record
			end)
			return true
		end)
		if not ok then
			return nil
		end
		if loaded then
			return loaded
		end
		if blocked then
			if os.clock() - started >= LOCK_WAIT then
				-- The other server never released the save (e.g. it is shutting
				-- down slowly). Take over; that server can no longer write.
				warn(("[DataService] taking over the save lock for %s"):format(player.Name))
				forceTakeover = true
			else
				task.wait(3)
			end
		end
	end
	return nil
end

function DataService:Init(registry: { [string]: any })
	Services = registry
	task.spawn(openStore)

	Net.Handle("RequestSync", function(player)
		if profiles[player] then
			dirtySync[player] = true
		end
		return nil
	end)
end

function DataService:Start()
	Players.PlayerAdded:Connect(function(player)
		self:_load(player)
	end)
	for _, player in Players:GetPlayers() do
		task.spawn(function()
			self:_load(player)
		end)
	end
	Players.PlayerRemoving:Connect(function(player)
		self:_release(player)
	end)

	-- Batched client sync (at most ~7 times per second per player).
	task.spawn(function()
		while true do
			task.wait(0.15)
			for player in dirtySync do
				dirtySync[player] = nil
				local data = profiles[player]
				if data and player.Parent then
					local ok, err = pcall(function()
						Net.Fire("Sync", player, data)
					end)
					if not ok then
						warn(("[DataService] sync to %s failed: %s"):format(player.Name, tostring(err)))
					end
				end
			end
		end
	end)

	-- Play-time counter + autosave.
	task.spawn(function()
		local sinceSave = 0
		while true do
			task.wait(1)
			sinceSave += 1
			for player, data in profiles do
				local ok = pcall(function()
					data.stats.playSeconds += 1
				end)
				if not ok then
					warn("[DataService] bad stats table for " .. player.Name)
				end
			end
			if sinceSave >= Config.AutosaveInterval then
				sinceSave = 0
				-- Save every loaded profile (not only changed ones) so each
				-- session lock is refreshed and never looks abandoned.
				for player in profiles do
					if not releasing[player] then
						task.spawn(function()
							self:SaveNow(player)
						end)
					end
				end
			end
		end
	end)

	game:BindToClose(function()
		if storeState ~= "ready" then
			return
		end
		local pending = 0
		for _, player in Players:GetPlayers() do
			pending += 1
			task.spawn(function()
				releasing[player] = true
				self:_saveRecord(player, true)
				pending -= 1
			end)
		end
		local started = os.clock()
		while pending > 0 and os.clock() - started < 25 do
			task.wait(0.1)
		end
	end)
end

function DataService:_load(player: Player)
	if profiles[player] or loading[player] then
		return
	end
	loading[player] = true

	-- Wait for the DataStore decision. A live server never falls back to a
	-- blank profile: that blank profile could later overwrite the real save.
	local started = os.clock()
	while storeState == "pending" and player.Parent do
		if not IS_STUDIO and os.clock() - started > LOAD_WAIT_LIMIT then
			loading[player] = nil
			player:Kick("Our save servers are busy. Please rejoin in a minute (your progress is safe).")
			return
		end
		task.wait(0.25)
	end
	if not player.Parent then
		loading[player] = nil
		return
	end

	local data: PlayerData
	local activeStore = store
	if storeState == "ready" and activeStore then
		local loaded = loadWithLock(activeStore, player)
		if not loaded then
			loading[player] = nil
			if player.Parent then
				player:Kick("Could not load your save data. Please rejoin in a minute (your progress is safe).")
			end
			return
		end
		data = loaded
		loadedFromStore[player] = true
	else
		-- Studio without DataStore access: play with a fresh profile, never saved.
		data = Types.DefaultData()
	end

	loading[player] = nil
	if not player.Parent then
		-- Left while loading: give the lock back so the next server is not delayed.
		if loadedFromStore[player] then
			profiles[player] = data
			releasing[player] = true
			self:_saveRecord(player, true)
			releasing[player] = nil
			profiles[player] = nil
			loadedFromStore[player] = nil
			dirtySave[player] = nil
		end
		return
	end
	profiles[player] = data
	player:SetAttribute("DataLoaded", true)
	dirtySync[player] = true
	self.PlayerLoaded:Fire(player, data)
end

function DataService:_release(player: Player)
	local data = profiles[player]
	if not data then
		return
	end
	self.PlayerRemoving:Fire(player, data)
	releasing[player] = true
	self:_saveRecord(player, true)
	releasing[player] = nil
	profiles[player] = nil
	dirtySave[player] = nil
	dirtySync[player] = nil
	loadedFromStore[player] = nil
	lockLost[player] = nil
end

function DataService:Get(player: Player): PlayerData?
	return profiles[player]
end

function DataService:WaitFor(player: Player, timeout: number?): PlayerData?
	local limit = timeout or 30
	local started = os.clock()
	while not profiles[player] and player.Parent and os.clock() - started < limit do
		task.wait(0.1)
	end
	return profiles[player]
end

function DataService:GetLoadedPlayers(): { Player }
	local list = {}
	for player in profiles do
		if not releasing[player] then
			table.insert(list, player)
		end
	end
	return list
end

function DataService:MarkDirty(player: Player)
	if profiles[player] then
		dirtySave[player] = true
		dirtySync[player] = true
	end
end

function DataService:AddCash(player: Player, amount: number, opts: { boostable: boolean?, source: string? }?): number
	local data = profiles[player]
	if not data or amount ~= amount or amount <= 0 or amount == math.huge then
		return 0
	end
	local final = amount
	if opts and opts.boostable then
		local boostService = Services.BoostService
		if boostService and boostService.GetCashMultiplier then
			final *= boostService:GetCashMultiplier(player)
		end
	end
	final = Format.RoundWhole(final)
	if final <= 0 then
		return 0
	end
	data.cash = Format.RoundWhole(data.cash + final)
	data.lifetimeCash = Format.RoundWhole(data.lifetimeCash + final)
	self:MarkDirty(player)
	return final
end

function DataService:CanAfford(player: Player, amount: number): boolean
	local data = profiles[player]
	return data ~= nil and amount == amount and data.cash + 1e-6 >= amount
end

function DataService:SpendCash(player: Player, amount: number): boolean
	local data = profiles[player]
	if not data or amount ~= amount or amount < 0 or amount == math.huge then
		return false
	end
	if data.cash + 1e-6 < amount then
		return false
	end
	data.cash = math.max(0, Format.RoundWhole(data.cash - amount))
	self:MarkDirty(player)
	return true
end

-- Remember a processed purchase (MarketplaceService receipt idempotency).
function DataService:HasReceipt(player: Player, purchaseId: string): boolean
	local data = profiles[player]
	return data ~= nil and data.receipts[purchaseId] == true
end

function DataService:AddReceipt(player: Player, purchaseId: string)
	local data = profiles[player]
	if not data then
		return
	end
	data.receipts[purchaseId] = true
	table.insert(data.receiptOrder, purchaseId)
	while #data.receiptOrder > MAX_RECEIPTS do
		local oldest = table.remove(data.receiptOrder, 1)
		if oldest then
			data.receipts[oldest] = nil
		end
	end
	self:MarkDirty(player)
end

-- Writes the profile. `release` = true drops the session lock (final save).
-- Returns true only when the record really reached the DataStore.
function DataService:_saveRecord(player: Player, release: boolean): boolean
	local data = profiles[player]
	local activeStore = store
	if not data or not activeStore or not loadedFromStore[player] or lockLost[player] then
		return false
	end
	if releasing[player] and not release then
		return false -- the final save is running; nothing may be written after it
	end
	dirtySave[player] = nil
	local cancelled = false
	local ok = withRetries("save " .. player.Name, function()
		cancelled = false
		activeStore:UpdateAsync(keyFor(player), function(old: any)
			cancelled = false
			if type(old) == "table" and lockedByOther(old._lock) then
				cancelled = true
				return nil -- another server owns this save now: never overwrite it
			end
			if releasing[player] and not release then
				return nil -- the player started leaving while this save waited
			end
			(data :: any)._lock = if release then nil else { jobId = game.JobId, t = os.time() }
			return data
		end)
		return true
	end)
	if cancelled then
		lockLost[player] = true
		warn(("[DataService] %s's save is owned by another server; this server stops saving it"):format(player.Name))
		return false
	end
	if not ok and profiles[player] and not releasing[player] then
		dirtySave[player] = true
	end
	return ok
end

function DataService:SaveNow(player: Player): boolean
	return self:_saveRecord(player, false)
end

function DataService:IsSaving(): boolean
	return storeState == "ready"
end

return DataService
