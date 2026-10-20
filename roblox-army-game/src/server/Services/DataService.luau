--!strict
--[[
	DataService
	Loads, saves and syncs PlayerData.

	API (server):
		DataService:Get(player): PlayerData?          -- live table, mutate it then MarkDirty
		DataService:WaitFor(player, timeout?): PlayerData?
		DataService:MarkDirty(player)                  -- sync to client soon + save later
		DataService:AddCash(player, amount, opts?): number
			opts.boostable = true -> multiplied by BoostService:GetCashMultiplier (2x boost)
			opts.source = "runner" | "section" | "combat" | ... (for logging)
			returns the amount actually added
		DataService:CanAfford(player, amount): boolean
		DataService:SpendCash(player, amount): boolean -- false = not enough cash
		DataService:SaveNow(player): boolean
		DataService.PlayerLoaded: Signal(player, data)
		DataService.PlayerRemoving: Signal(player, data)  -- before the final save

	Without DataStore access (e.g. an unpublished place in Studio) the game still
	runs; progress just is not saved. See README.md to enable saving in Studio.
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

local Services: { [string]: any } = {}
local store: DataStore? = nil
local profiles: { [Player]: PlayerData } = {}
local dirtySave: { [Player]: boolean } = {}
local dirtySync: { [Player]: boolean } = {}
local loading: { [Player]: boolean } = {}

local MAX_RECEIPTS = 60

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
	local ok, result = pcall(function()
		return DataStoreService:GetDataStore(Config.DataStoreName)
	end)
	if not ok then
		warn("[DataService] DataStores unavailable, progress will NOT be saved: " .. tostring(result))
		return
	end
	-- In Studio, a place without "Enable Studio Access to API Services" errors here.
	local probeOk, probeErr = pcall(function()
		(result :: DataStore):GetAsync("__probe")
	end)
	if not probeOk then
		warn("[DataService] DataStores unavailable, progress will NOT be saved: " .. tostring(probeErr))
		return
	end
	store = result
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
					Net.Fire("Sync", player, data)
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
			for _, data in profiles do
				data.stats.playSeconds += 1
			end
			if sinceSave >= Config.AutosaveInterval then
				sinceSave = 0
				for player in dirtySave do
					task.spawn(function()
						self:SaveNow(player)
					end)
				end
			end
		end
	end)

	game:BindToClose(function()
		if RunService:IsStudio() and not store then
			return
		end
		local pending = 0
		for _, player in Players:GetPlayers() do
			pending += 1
			task.spawn(function()
				self:SaveNow(player)
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
	-- Give openStore a moment on server start.
	local waited = 0
	while store == nil and waited < 3 do
		task.wait(0.25)
		waited += 0.25
	end

	local data: PlayerData
	local activeStore = store
	if activeStore then
		local ok, saved = withRetries("load " .. player.Name, function()
			return activeStore:GetAsync(keyFor(player))
		end)
		if not ok then
			loading[player] = nil
			player:Kick("Could not load your save data. Please rejoin in a minute (your progress is safe).")
			return
		end
		data = if saved ~= nil then Types.Reconcile(saved) else Types.DefaultData()
	else
		data = Types.DefaultData()
	end

	loading[player] = nil
	if not player.Parent then
		return -- left while loading
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
	self:SaveNow(player)
	profiles[player] = nil
	dirtySave[player] = nil
	dirtySync[player] = nil
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
		table.insert(list, player)
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
	if not data or amount ~= amount or amount <= 0 then
		return 0
	end
	local final = amount
	if opts and opts.boostable then
		local boostService = Services.BoostService
		if boostService and boostService.GetCashMultiplier then
			final *= boostService:GetCashMultiplier(player)
		end
	end
	final = Format.RoundCents(final)
	data.cash = Format.RoundCents(data.cash + final)
	data.lifetimeCash = Format.RoundCents(data.lifetimeCash + final)
	self:MarkDirty(player)
	return final
end

function DataService:CanAfford(player: Player, amount: number): boolean
	local data = profiles[player]
	return data ~= nil and amount == amount and data.cash + 1e-6 >= amount
end

function DataService:SpendCash(player: Player, amount: number): boolean
	local data = profiles[player]
	if not data or amount ~= amount or amount < 0 then
		return false
	end
	if data.cash + 1e-6 < amount then
		return false
	end
	data.cash = math.max(0, Format.RoundCents(data.cash - amount))
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

function DataService:SaveNow(player: Player): boolean
	local data = profiles[player]
	local activeStore = store
	if not data or not activeStore then
		return false
	end
	dirtySave[player] = nil
	local ok = withRetries("save " .. player.Name, function()
		activeStore:UpdateAsync(keyFor(player), function()
			return data
		end)
		return true
	end)
	if not ok then
		dirtySave[player] = true
	end
	return ok
end

function DataService:IsSaving(): boolean
	return store ~= nil
end

return DataService
