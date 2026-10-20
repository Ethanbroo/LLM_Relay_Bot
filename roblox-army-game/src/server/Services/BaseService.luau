--!strict
--[[
	BaseService (server)
	Runs the home base economy: unlocking revenue sections and the Combat
	Arena, upgrading sections, the sell/stock loop, manual restocking,
	employees (auto-restock) and the gates between areas.

	How a revenue section works (docs/ARCHITECTURE.md §6.2):
		* Every `saleInterval` seconds it makes one sale: it pays `incomePerSale`
		  (2x boost applies) and uses 1 stock.
		* At 0 stock it STOPS selling ("OUT OF STOCK!") until the player restocks
		  it by hand (hold the "Restock" prompt in front of it).
		* With an employee it restocks itself 3 seconds after reaching 0.
		Numbers come from Shared/Economy (SectionStats, prices).

	Requests the client can send (Net actions, all re-validated here):
		UnlockSlot(slotId)          buy a revenue section or the Combat Arena slot
		UpgradeSection(slotId)      raise a section's level (more income + stock)
		Restock(slotId)             refill a section's stock (free, manual work)
		HireEmployeeCash(slotId)    hire an employee for cash (20-run price)
		AssignEmployeeToken(slotId) use an employee token bought with Robux earlier
		UnlockGate(area)            open the gate from `area` into `area + 1`
	Every request checks: data loaded, Base mode, valid arguments, the area is
	unlocked, the player stands close to the right spot (Layout.INTERACT_RANGE)
	and the cost (DataService:SpendCash).

	Push events on the "Base" channel (Net.Fire("Base", player, msg, payload)):
		"Income"     { [slotId] = cashPaid }   once per second when sales happened
		"Restocked"  { slotId = string, by = "player" | "employee" }
		"Employee"   { slotId = string }        an employee was assigned

	Public API for other services (docs/ARCHITECTURE.md §4.3):
		BaseService:AssignEmployee(player, slotId): boolean
		BaseService:ApplyTimeSkip(player, seconds): number    -- cash paid (not 2x-boosted)
		BaseService:MarkBarrierDefeated(player, area)
		BaseService:GetPassiveRate(player): number            -- $/s of sections that can sell
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Net = require(Shared.Net)
local Types = require(Shared.Types)

type PlayerData = Types.PlayerData
type SectionState = Types.SectionState

local BaseService = {}
BaseService.Priority = 50

local Services: { [string]: any } = {}

-- Seconds an employee waits after stock reaches 0 before restocking. [REQUIREMENT §6.2]
local EMPLOYEE_RESTOCK_DELAY = 3
-- Revenue loop rate (1 Hz, well under the 30 Hz server limit).
local LOOP_INTERVAL = 1
-- [PROPOSAL] Never catch up more than 5 s of selling after a server hiccup.
local MAX_TICK = 5
-- [PROPOSAL] Safety cap for one time skip (the longest boost is 24 h).
local MAX_TIME_SKIP = 48 * 3600
-- Extra height tolerance for proximity checks (players may be jumping).
local MAX_HEIGHT_DIFF = 20

-- slot id -> where it lives
type SlotInfo = { area: number, index: number, def: Config.SlotDef }
local slotInfo: { [string]: SlotInfo } = {}
for areaIndex, areaDef in Config.Areas do
	for slotIndex, slotDef in areaDef.slots do
		slotInfo[slotDef.id] = { area = areaIndex, index = slotIndex, def = slotDef }
	end
end

-- Per-player timers that do not need saving.
type Runtime = {
	accum: { [string]: number }, -- seconds collected towards the next sale
	emptyFor: { [string]: number }, -- seconds an employee section has been at 0 stock
}
local runtime: { [Player]: Runtime } = {}
local loopStarted = false

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function dataService(): any
	return Services.DataService
end

local function getData(player: Player): PlayerData?
	local ds = dataService()
	if not ds then
		return nil
	end
	return ds:Get(player)
end

local function markDirty(player: Player)
	local ds = dataService()
	if ds then
		ds:MarkDirty(player)
	end
end

local function getRuntime(player: Player): Runtime
	local rt = runtime[player]
	if not rt then
		rt = { accum = {}, emptyFor = {} }
		runtime[player] = rt
	end
	return rt
end

local function isFiniteNumber(value: any): boolean
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function isInteger(value: any): boolean
	return isFiniteNumber(value) and value == math.floor(value)
end

local function inBaseMode(player: Player): boolean
	local modeService = Services.ModeService
	if not modeService then
		return true
	end
	return modeService:GetMode(player) == "Base"
end

-- Is the player's character standing within INTERACT_RANGE of `point`?
-- (Horizontal distance, with a generous height tolerance for jumping.)
local function isNear(player: Player, point: Vector3): boolean
	local character = player.Character
	if not character then
		return false
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	if not (root and root:IsA("BasePart")) then
		return false
	end
	local offset = root.Position - point
	local flat = Vector2.new(offset.X, offset.Z).Magnitude
	return flat <= Layout.INTERACT_RANGE and math.abs(offset.Y) <= MAX_HEIGHT_DIFF
end

-- Revenue section the player owns, or nil.
local function ownedSection(data: PlayerData, slotId: any): (SectionState?, SlotInfo?)
	if type(slotId) ~= "string" then
		return nil, nil
	end
	local info = slotInfo[slotId]
	if not info or info.def.kind ~= "revenue" then
		return nil, nil
	end
	local section = data.base.sections[slotId]
	if type(section) ~= "table" then
		return nil, info
	end
	return section, info
end

-- Repair anything odd in saved section data (old saves, bad edits).
local function sanitize(data: PlayerData)
	local base = data.base
	if type(base.sections) ~= "table" then
		base.sections = {}
	end
	if type(base.barriers) ~= "table" then
		base.barriers = {}
	end
	if not isInteger(base.highestArea) then
		base.highestArea = 1
	end
	base.highestArea = math.clamp(base.highestArea, 1, #Config.Areas)
	base.arenaUnlocked = base.arenaUnlocked == true
	for slotId, section in base.sections do
		local info = slotInfo[slotId]
		if type(section) ~= "table" or not info or info.def.kind ~= "revenue" then
			-- Unknown or broken entry: leave it alone, the loops skip it.
			continue
		end
		local level = if isFiniteNumber(section.level) then math.floor(section.level) else 1
		section.level = math.clamp(level, 1, Economy.SectionMaxLevel)
		local capacity = Economy.SectionStats(slotId, section.level).stockCapacity
		local stock = if isFiniteNumber(section.stock) then math.floor(section.stock) else capacity
		section.stock = math.clamp(stock, 0, capacity)
		section.employee = section.employee == true
	end
end

-- The last thing every action does: pay, mutate, sync.
local function fail(message: string): Net.Result
	return Net.Fail(message)
end

-- Shared first checks for every request. Returns data or an error result.
local function begin(player: Player): (PlayerData?, Net.Result?)
	local data = getData(player)
	if not data then
		return nil, fail("Your data is still loading, try again in a moment")
	end
	if not inBaseMode(player) then
		return nil, fail("You can only do that at your base")
	end
	return data, nil
end

-------------------------------------------------------------------------------
-- Public API (§4.3)
-------------------------------------------------------------------------------

-- Give an unlocked revenue section an employee. Used by HireEmployeeCash,
-- AssignEmployeeToken and the Robux receipt (ShopService). No cost, no
-- proximity check here: callers have already validated/paid.
function BaseService:AssignEmployee(player: Player, slotId: string): boolean
	local data = getData(player)
	if not data then
		return false
	end
	local section = ownedSection(data, slotId)
	if not section or section.employee then
		return false
	end
	section.employee = true
	-- If it is already empty the revenue loop restocks it 3 s from now.
	getRuntime(player).emptyFor[slotId] = 0
	markDirty(player)
	Net.Fire("Base", player, "Employee", { slotId = slotId })
	return true
end

-- Pay the revenue the player's sections would make in `seconds` seconds.
-- Rule (§6.2): sales = floor(seconds / saleInterval) per section; with an
-- employee all sales are paid, without one only min(sales, stock) and the
-- stock goes down by that much. Not 2x-boosted. Returns the cash paid.
function BaseService:ApplyTimeSkip(player: Player, seconds: number): number
	local data = getData(player)
	if not data or not isFiniteNumber(seconds) or seconds <= 0 then
		return 0
	end
	seconds = math.min(seconds, MAX_TIME_SKIP)
	local total = 0
	local perSlot: { [string]: number } = {}
	local changed = false
	for slotId, section in data.base.sections do
		local info = slotInfo[slotId]
		if type(section) ~= "table" or not info or info.def.kind ~= "revenue" then
			continue
		end
		local stats = Economy.SectionStats(slotId, section.level)
		if stats.saleInterval <= 0 then
			continue
		end
		local sales = math.floor(seconds / stats.saleInterval + 1e-9)
		if not section.employee then
			sales = math.min(sales, math.max(0, math.floor(section.stock)))
			if sales > 0 then
				section.stock -= sales
				changed = true
			end
		end
		if sales > 0 then
			local amount = Format.RoundCents(sales * stats.incomePerSale)
			total += amount
			perSlot[slotId] = amount
		end
	end
	if changed then
		markDirty(player)
	end
	total = Format.RoundCents(total)
	if total <= 0 then
		return 0
	end
	local ds = dataService()
	local paid = if ds then ds:AddCash(player, total, { source = "timeskip" }) else 0
	if paid > 0 then
		Net.Fire("Base", player, "Income", perSlot)
	end
	return paid
end

-- The barrier squad guarding `area`'s exit gate has been beaten (COMBAT).
function BaseService:MarkBarrierDefeated(player: Player, area: number)
	local data = getData(player)
	if not data or not isInteger(area) then
		return
	end
	if area < 1 or area >= #Config.Areas or area > data.base.highestArea then
		return
	end
	local key = tostring(area)
	if data.base.barriers[key] == true then
		return
	end
	data.base.barriers[key] = true
	markDirty(player)
	if area == data.base.highestArea then
		local nextArea = Config.Areas[area + 1]
		Net.Notify(
			player,
			("Barrier beaten! Unlock %s at the gate for %s"):format(
				nextArea.name,
				Format.Money(Economy.GatePrice(area))
			),
			"success"
		)
	end
end

-- Current $/s of every revenue section that can sell right now
-- (has stock, or has an employee who keeps it stocked). Not 2x-boosted.
function BaseService:GetPassiveRate(player: Player): number
	local data = getData(player)
	if not data then
		return 0
	end
	local rate = 0
	for slotId, section in data.base.sections do
		local info = slotInfo[slotId]
		if type(section) == "table" and info and info.def.kind == "revenue" then
			if section.employee or section.stock > 0 then
				rate += Economy.SectionStats(slotId, section.level).incomePerSecond
			end
		end
	end
	return rate
end

-------------------------------------------------------------------------------
-- Revenue loop (1 Hz, one loop for the whole server)
-------------------------------------------------------------------------------
-- [PROPOSAL] Sections keep selling while the player is in a run or a fight
-- (they are passive income); they only stop when the stock runs out.
local function tickPlayer(player: Player, data: PlayerData, dt: number)
	local rt = getRuntime(player)
	local ds = dataService()
	local income: { [string]: number } = {}
	local anyIncome = false
	local changed = false

	for slotId, section in data.base.sections do
		local info = slotInfo[slotId]
		if type(section) ~= "table" or not info or info.def.kind ~= "revenue" then
			continue
		end
		local stats = Economy.SectionStats(slotId, section.level)
		if stats.saleInterval <= 0 then
			continue
		end

		if section.stock > 0 then
			rt.emptyFor[slotId] = nil
			local t = (rt.accum[slotId] or 0) + dt
			local sales = 0
			-- 1e-6 tolerance so 0.1 + 0.2 style rounding never skips a sale.
			while t + 1e-6 >= stats.saleInterval and section.stock > 0 do
				t -= stats.saleInterval
				section.stock -= 1
				sales += 1
			end
			if section.stock <= 0 then
				section.stock = 0
				t = 0 -- stopped: the timer restarts after restocking
				rt.emptyFor[slotId] = 0
			end
			rt.accum[slotId] = math.max(0, t)
			if sales > 0 then
				changed = true
				local paid = if ds
					then ds:AddCash(player, sales * stats.incomePerSale, { boostable = true, source = "section" })
					else 0
				if paid > 0 then
					income[slotId] = paid
					anyIncome = true
				end
			end
		else
			-- Out of stock: no sales. An employee refills it after 3 seconds.
			rt.accum[slotId] = 0
			if section.employee then
				local waited = (rt.emptyFor[slotId] or 0) + dt
				if waited + 1e-6 >= EMPLOYEE_RESTOCK_DELAY then
					section.stock = stats.stockCapacity
					rt.emptyFor[slotId] = nil
					changed = true
					Net.Fire("Base", player, "Restocked", { slotId = slotId, by = "employee" })
				else
					rt.emptyFor[slotId] = waited
				end
			end
		end
	end

	if changed then
		markDirty(player)
	end
	if anyIncome then
		Net.Fire("Base", player, "Income", income)
	end
end

local function startLoop()
	if loopStarted then
		return -- never run two loops (that would double-pay)
	end
	loopStarted = true
	task.spawn(function()
		local last = os.clock()
		while true do
			task.wait(LOOP_INTERVAL)
			local now = os.clock()
			local dt = math.clamp(now - last, 0, MAX_TICK)
			last = now
			local ds = dataService()
			if ds then
				for _, player in ds:GetLoadedPlayers() do
					local data = ds:Get(player)
					if data and player.Parent then
						-- xpcall: one broken save can never stop everyone's income.
						xpcall(tickPlayer, function(err: any)
							warn(("[BaseService] revenue tick failed for %s: %s"):format(player.Name, tostring(err)))
						end, player, data, dt)
					end
				end
			end
		end
	end)
end

-------------------------------------------------------------------------------
-- Request handlers
-------------------------------------------------------------------------------
local function registerHandlers()
	Net.Handle("UnlockSlot", function(player: Player, slotId: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		if type(slotId) ~= "string" or not slotInfo[slotId] then
			return fail("Unknown section")
		end
		local info = slotInfo[slotId]
		if info.area > data.base.highestArea then
			return fail("Unlock " .. Config.Areas[info.area].name .. " first")
		end
		local isArena = info.def.kind == "arena"
		if isArena then
			if data.base.arenaUnlocked then
				return fail("Already unlocked")
			end
		elseif data.base.sections[slotId] ~= nil then
			return fail("Already unlocked")
		end
		if not isNear(player, Layout.SlotCenter(info.area, info.index)) then
			return fail("Walk onto the unlock pad first")
		end
		local price = Economy.SlotUnlockPrice(slotId)
		if not isFiniteNumber(price) or price < 0 then
			return fail("This cannot be bought")
		end
		if not dataService():SpendCash(player, price) then
			return fail("Not enough cash! You need " .. Format.Money(price))
		end
		if isArena then
			data.base.arenaUnlocked = true
		else
			local stats = Economy.SectionStats(slotId, 1)
			data.base.sections[slotId] = { level = 1, stock = stats.stockCapacity, employee = false }
			data.stats.sectionsUnlocked += 1
			local rt = getRuntime(player)
			rt.accum[slotId] = 0
			rt.emptyFor[slotId] = nil
		end
		markDirty(player)
		return Net.Ok({ slotId = slotId })
	end)

	Net.Handle("UpgradeSection", function(player: Player, slotId: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		local section, info = ownedSection(data, slotId)
		if not section or not info then
			return fail("Unlock this section first")
		end
		local price = Economy.SectionUpgradePrice(info.def.id, section.level)
		if price == nil then
			return fail("Already at max level")
		end
		if not isNear(player, Layout.SlotFrontPosition(info.area, info.index)) then
			return fail("Walk up to the front of the shop first")
		end
		if not dataService():SpendCash(player, price) then
			return fail("Not enough cash! You need " .. Format.Money(price))
		end
		-- [PROPOSAL] Upgrading raises income and stock capacity but does not
		-- refill the current stock (restocking stays a separate action).
		section.level = math.min(section.level + 1, Economy.SectionMaxLevel)
		markDirty(player)
		return Net.Ok({ slotId = info.def.id, level = section.level })
	end)

	Net.Handle("Restock", function(player: Player, slotId: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		local section, info = ownedSection(data, slotId)
		if not section or not info then
			return fail("Unlock this section first")
		end
		local capacity = Economy.SectionStats(info.def.id, section.level).stockCapacity
		if section.stock >= capacity then
			return fail("Already fully stocked")
		end
		if not isNear(player, Layout.SlotFrontPosition(info.area, info.index)) then
			return fail("Walk up to the front of the shop first")
		end
		-- [PROPOSAL] Restocking is free: it is manual work, not a purchase.
		section.stock = capacity
		local rt = getRuntime(player)
		rt.emptyFor[info.def.id] = nil
		markDirty(player)
		Net.Fire("Base", player, "Restocked", { slotId = info.def.id, by = "player" })
		return Net.Ok({ slotId = info.def.id, stock = capacity })
	end)

	Net.Handle("HireEmployeeCash", function(player: Player, slotId: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		local section, info = ownedSection(data, slotId)
		if not section or not info then
			return fail("Unlock this section first")
		end
		if section.employee then
			return fail("This section already has an employee")
		end
		if not isNear(player, Layout.SlotHirePosition(info.area, info.index)) then
			return fail("Walk up to the Hire me tile first")
		end
		local price = Economy.EmployeeCashPrice(info.def.id, data.base.highestArea)
		if not isFiniteNumber(price) or price < 0 then
			return fail("This cannot be bought")
		end
		if not dataService():SpendCash(player, price) then
			return fail("Not enough cash! You need " .. Format.Money(price))
		end
		if not BaseService:AssignEmployee(player, info.def.id) then
			-- Should never happen (checked above), but never take cash for nothing.
			dataService():AddCash(player, price, { source = "refund" })
			return fail("Could not hire, your cash was refunded")
		end
		return Net.Ok({ slotId = info.def.id })
	end)

	Net.Handle("AssignEmployeeToken", function(player: Player, slotId: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		local tokens = data.employeeTokens
		if not isFiniteNumber(tokens) or tokens < 1 then
			return fail("You have no employee tokens")
		end
		local section, info = ownedSection(data, slotId)
		if not section or not info then
			return fail("Unlock this section first")
		end
		if section.employee then
			return fail("This section already has an employee")
		end
		if not isNear(player, Layout.SlotHirePosition(info.area, info.index)) then
			return fail("Walk up to the Hire me tile first")
		end
		data.employeeTokens = math.max(0, math.floor(tokens) - 1)
		if not BaseService:AssignEmployee(player, info.def.id) then
			data.employeeTokens += 1
			markDirty(player)
			return fail("Could not assign the employee")
		end
		markDirty(player)
		return Net.Ok({ slotId = info.def.id })
	end)

	Net.Handle("UnlockGate", function(player: Player, area: any)
		local data, err = begin(player)
		if not data then
			return err
		end
		if not isInteger(area) or area < 1 or area >= #Config.Areas then
			return fail("Unknown gate")
		end
		if area < data.base.highestArea then
			return fail("This gate is already open")
		end
		if area > data.base.highestArea then
			return fail("Open the gates before this one first")
		end
		if data.base.barriers[tostring(area)] ~= true then
			return fail("Defeat the barrier squad first!")
		end
		if not isNear(player, Layout.GatePosition(area)) then
			return fail("Walk up to the gate first")
		end
		local price = Economy.GatePrice(area)
		if not isFiniteNumber(price) or price < 0 then
			return fail("This gate cannot be opened")
		end
		if not dataService():SpendCash(player, price) then
			return fail("Not enough cash! You need " .. Format.Money(price))
		end
		data.base.highestArea = area + 1
		markDirty(player)
		local name = Config.Areas[area + 1].name
		Net.Notify(player, ("Area unlocked: %s!"):format(name), "success")
		return Net.Ok({ area = area + 1 })
	end)
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function BaseService:Init(registry: { [string]: any })
	Services = registry
	registerHandlers()
end

function BaseService:Start()
	local ds = dataService()
	if not ds then
		warn("[BaseService] DataService missing, base economy disabled")
		return
	end
	ds.PlayerLoaded:Connect(function(player: Player, data: PlayerData)
		sanitize(data)
		runtime[player] = nil
		markDirty(player)
	end)
	-- Players whose data loaded before we connected.
	for _, player in ds:GetLoadedPlayers() do
		local data = ds:Get(player)
		if data then
			sanitize(data)
		end
	end
	Players.PlayerRemoving:Connect(function(player)
		runtime[player] = nil
	end)
	startLoop()
end

return BaseService
