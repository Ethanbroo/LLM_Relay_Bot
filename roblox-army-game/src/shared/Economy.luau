--!strict
--[[
	Economy
	Every price and income in the game, derived from ONE measured number: the
	best-case cash of the army runner. Pure (no services, no Instances),
	deterministic and memoized, so the server, the client and the Lune tests /
	scripts/simulate.luau all see the same numbers. Tunables: Shared/EconomyDefs.

	Public API (docs/ARCHITECTURE.md §4.1). `area` = index into Config.Areas.
		Economy.RunBenchmark(area): number           R(area): average best-case payout of the 10 benchmark runs
		Economy.TenRunBenchmark(area): number        B10(area): sum of the 10 best-case runs
		Economy.BenchmarkDetails(area): { upgradeLevel, runs = { { level, payout } }, total }
		Economy.ReferenceUpgrades(area): { troops, damage, capacity }
		Economy.SlotUnlockPrice(slotId): number      revenue section or the Combat Arena slot
		Economy.GatePrice(area): number              gate from `area` into `area + 1` (strictly increasing)
		Economy.SectionStats(slotId, level): { incomePerSale, saleInterval, stockCapacity, incomePerSecond }
		Economy.SectionMaxLevel: number
		Economy.SectionUpgradePrice(slotId, currentLevel): number?   nil at max level
		Economy.EmployeeCashPrice(slotId, playerHighestArea): number
		Economy.ReferencePassiveRate(area): number   $/s of all level-1 sections in areas <= area
		Economy.RunnerUpgradeMaxLevel: number
		Economy.RunnerUpgradePrice(kind, currentLevel): number?      kind = "troops" | "damage" | "capacity"
		Economy.RunnerStats(upgrades, area): { startTroops, damage, capacity }
		Economy.RunnerUpgradeEffect(kind, level): string             "+1 troop", "+4% damage", "+6% carry", "MAX"
		Economy.BoostValue(boostId, playerArea): number
		Economy.BoostCoinPrice(boostId, playerArea): number
		Economy.VehicleCoinPrice(vehicleId, playerArea): number
		Economy.CombatLevelReward(level): number
		Economy.BarrierCombatLevel(area): number
		Economy.EquipmentUpgradePrice(rarity, currentLevel): number? nil at the rarity's max level
		Economy.RunDurationSeconds(): number
	Extras (not in the contract, handy for UIs and tools):
		Economy.SlotArea(slotId): number?            area index of a slot id
		Economy.AreaUnlockTotal(area): number        all 4 slots + the exit gate of an area
	Economy.TimeSkipPayout(sections, seconds): (total, perSlot)
	                                             what a time skip of `seconds` pays the player's
	                                             sections RIGHT NOW (data.base.sections);
	                                             perSlot[slotId] = { paid, sales, stockUsed }
	Economy.TimeSkipValue(boostId, playerArea, preview): number
	                                             min(BoostValue, preview): value used for the price
	Economy.TimeSkipCoinPrice(boostId, playerArea, preview): number
	                                             (1 - NetGainShare) x TimeSkipValue, rounded DOWN
	                                             to whole dollars (never more than 0.8 x preview)
	Economy.TimeSkipWorthOffering(boostId, playerArea, preview): boolean
	                                             random offers only show a time skip when the preview
	                                             is >= TimeSkipOfferMinShare (25%) of its reference
	Economy.TimeSkipOfferMinShare: number
		Economy._ClearCache()                        tests: forget memoized values (e.g. after
		                                             switching Config.Benchmark.Mode)

	Definitions (answers to the brief's open questions; all [PROPOSAL]):
	* 10-run benchmark (question 3): the benchmark runs of area a are the courses
	  (a, L0 .. L0+9) with L0 = 1 + 10*(a-1). Each is solved EXACTLY with
	  RunnerSim.BestCase using the reference player of that area:
	  ReferenceUpgrades(a) = every permanent upgrade at level
	  (a-1) * Config.Benchmark.ReferenceUpgradeLevelsPerArea.
	  B10(a) = sum of the 10 best-case payouts, R(a) = B10(a) / 10.
	* "Three area upgrades" = the first three revenue sections of an area,
	  following Config.Benchmark.Mode:
	    "CombinedSections" -> the three together cost B10 (split by income weight).
	    "EachSection"      -> each of the three costs B10.
	  The 4th revenue section costs the same $ per income weight as the first
	  three; the Combat Arena slot (a2s4) costs EconomyDefs.Slots.ArenaShare x B10.
	* Gate a -> a+1 costs GateShare x B10(a), and always more than the gate before.
	* Employee cash price (question 4): Config.Employee.RunsEquivalent (20) x R,
	  i.e. 20 best-case benchmark runs of the section's area ("SectionArea") or
	  of the player's highest area ("PlayerArea").
	* Sections: a full area of level-1 sections earns PassiveShare (39%) of the
	  active runner rate R / RunDurationSeconds().
	* Boost value V ("used properly", question 10): a 2x multiplier doubles your
	  earnings while you play runs for its duration -> extra = minutes*60 /
	  RunDurationSeconds() x R(area). A time skip's REFERENCE value is
	  hours*3600 x ReferencePassiveRate(area) (every shop of every area owned,
	  all with employees). Coin price = (1 - NetGainShare) x V = 0.8 V:
	  80% less net gain than the Robux route, still +25% return.
	* Time skip cash price: a skip only pays what the player's own shops
	  would earn (sections without an employee stop when their stock runs
	  out), so the price uses V = min(reference, TimeSkipPayout preview) and a
	  cash time skip ALWAYS returns +25% of what was paid.
	* Vehicles: priceFactor x GatePrice(max(playerArea, vehicle.area)).
	* Combat: the barrier of area a is combat level 3a-1. A perfect clear of a
	  combat level pays ~3 best-case runs of the area the level belongs to
	  (geometric interpolation between areas, strictly increasing).
	Money is WHOLE dollars everywhere (owner request: no cents). Every price,
	payout, income per sale and reward this module returns is a whole number:
	  * plain prices (slots, gates, upgrades, vehicles, employees) are rounded
	    to the nearest dollar (Format.RoundWhole), at least $1; the first three
	    sections of an area are split with largest-remainder rounding so they
	    still sum EXACTLY to B10, and each gate is at least ceil(5%) dearer;
	  * the 80%-rule coin prices (boosts, time skips) are rounded DOWN, so
	    value / price stays >= 1.25;
	  * a section's incomePerSale is max(1, RoundWhole(x)) and its saleInterval
	    is stretched by (rounded / x), so incomePerSecond is unchanged; its
	    stockCapacity is scaled by (x / rounded) (whole sales, at least 5), so
	    a full stock still lasts the designed StockCapacity x SaleInterval.
	Rates per second (incomePerSecond, ReferencePassiveRate) stay fractional.
]]

local Shared = script.Parent
local Config = require(Shared:WaitForChild("Config"))
local EconomyDefs = require(Shared:WaitForChild("EconomyDefs"))
local Format = require(Shared:WaitForChild("Format"))
local Types = require(Shared:WaitForChild("Types"))
local RunnerFolder = Shared:WaitForChild("Runner")
local RunnerCourse = require(RunnerFolder:WaitForChild("RunnerCourse"))
local RunnerDefs = require(RunnerFolder:WaitForChild("RunnerDefs"))
local RunnerSim = require(RunnerFolder:WaitForChild("RunnerSim"))

export type BenchmarkRun = { level: number, payout: number }
export type BenchmarkDetails = { upgradeLevel: number, runs: { BenchmarkRun }, total: number }
export type Upgrades = { troops: number, damage: number, capacity: number }
export type RunnerStats = { startTroops: number, damage: number, capacity: number }
type SectionState = Types.SectionState
export type TimeSkipSlot = { paid: number, sales: number, stockUsed: number }
export type SectionStats =
	{ incomePerSale: number, saleInterval: number, stockCapacity: number, incomePerSecond: number }

type SlotInfo = { area: number, index: number, def: Config.SlotDef }

local Economy = {}

Economy.SectionMaxLevel = EconomyDefs.Sections.MaxLevel
Economy.RunnerUpgradeMaxLevel = EconomyDefs.Runner.MaxLevel

local AREA_COUNT = #Config.Areas
local round = Format.RoundWhole

-- A whole-dollar price of at least $1.
local function price(value: number): number
	return math.max(1, round(value))
end

-------------------------------------------------------------------------------
-- Small helpers
-------------------------------------------------------------------------------
local function num(value: any, fallback: number): number
	if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
		return fallback
	end
	return value
end

-- Any area >= 1 (benchmarks beyond the last area are extrapolated).
local function anyArea(area: any): number
	return math.max(1, math.floor(num(area, 1)))
end

-- An area that exists in Config.Areas.
local function realArea(area: any): number
	return math.clamp(math.floor(num(area, 1)), 1, AREA_COUNT)
end

-- Slot id -> where it is.
local slotLookup: { [string]: SlotInfo } = {}
for areaIndex, areaDef in Config.Areas do
	for slotIndex, slotDef in areaDef.slots do
		slotLookup[slotDef.id] = { area = areaIndex, index = slotIndex, def = slotDef }
	end
end

local boostLookup: { [string]: Config.BoostDef } = {}
for _, boost in Config.Boosts do
	boostLookup[boost.id] = boost
end

local vehicleLookup: { [string]: Config.VehicleDef } = {}
for _, vehicle in Config.Vehicles do
	vehicleLookup[vehicle.id] = vehicle
end

-- Memo tables (cleared by Economy._ClearCache).
local benchCache: { [number]: BenchmarkDetails } = {}
local gateCache: { [number]: number } = {}

function Economy._ClearCache()
	table.clear(benchCache)
	table.clear(gateCache)
end

function Economy.SlotArea(slotId: string): number?
	local info = slotLookup[slotId]
	return if info then info.area else nil
end

function Economy.RunDurationSeconds(): number
	-- Running time to the enemy base + menus / summary overhead.
	return RunnerDefs.BOSS_DISTANCE / RunnerDefs.RUN_SPEED + RunnerDefs.RUN_OVERHEAD_SECONDS
end

-------------------------------------------------------------------------------
-- Permanent runner upgrades
-------------------------------------------------------------------------------
local function referenceLevel(area: number): number
	local perArea = num(Config.Benchmark.ReferenceUpgradeLevelsPerArea, 0)
	return math.clamp(math.floor((anyArea(area) - 1) * perArea), 0, EconomyDefs.Runner.MaxLevel)
end

function Economy.ReferenceUpgrades(area: number): Upgrades
	local level = referenceLevel(area)
	return { troops = level, damage = level, capacity = level }
end

local function upgradeLevel(value: any): number
	return math.clamp(math.floor(num(value, 0)), 0, EconomyDefs.Runner.MaxLevel)
end

-- [PROPOSAL] Base capacity grows with the area (AreaScale) but is divided by
-- the reference player's capacity bonus, so a reference-upgraded player can
-- carry about BaseCapacity x AreaScale^(a-1) in every area: the bag stays a
-- real limit everywhere and capacity upgrades keep mattering.
local function baseCapacity(area: number): number
	local d = EconomyDefs.Runner
	local a = anyArea(area)
	return d.BaseCapacity * EconomyDefs.AreaScale ^ (a - 1) / (1 + d.CapacityPerLevel * referenceLevel(a))
end

function Economy.RunnerStats(upgrades: Upgrades, area: number): RunnerStats
	local d = EconomyDefs.Runner
	local u: any = if type(upgrades) == "table" then upgrades else {}
	local troops = upgradeLevel(u.troops)
	local damage = upgradeLevel(u.damage)
	local capacity = upgradeLevel(u.capacity)
	return {
		startTroops = d.BaseTroops + troops * d.TroopsPerLevel,
		damage = Format.RoundCents(d.BaseDamage * (1 + d.DamagePerLevel * damage)), -- a stat, not money
		capacity = round(baseCapacity(area) * (1 + d.CapacityPerLevel * capacity)), -- whole dollars
	}
end

function Economy.RunnerUpgradeEffect(kind: string, level: number): string
	if upgradeLevel(level) >= EconomyDefs.Runner.MaxLevel then
		return "MAX"
	end
	local d = EconomyDefs.Runner
	if kind == "troops" then
		return "+" .. d.TroopsPerLevel .. (if d.TroopsPerLevel == 1 then " troop" else " troops")
	elseif kind == "damage" then
		return "+" .. math.floor(d.DamagePerLevel * 100 + 0.5) .. "% damage"
	elseif kind == "capacity" then
		return "+" .. math.floor(d.CapacityPerLevel * 100 + 0.5) .. "% carry"
	end
	return ""
end

-------------------------------------------------------------------------------
-- The 10-run benchmark
-------------------------------------------------------------------------------
local function simulateArea(area: number): BenchmarkDetails
	local stats = Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
	local first = RunnerDefs.FirstLevelOfArea(area)
	local runs: { BenchmarkRun } = {}
	local total = 0
	for i = 0, num(Config.Benchmark.Runs, 10) - 1 do
		local course = RunnerCourse.Generate(area, first + i)
		local result = RunnerSim.BestCase(course, {
			startTroops = stats.startTroops,
			damage = stats.damage,
			capacity = stats.capacity,
		})
		table.insert(runs, { level = first + i, payout = result.total })
		total += result.total
	end
	return { upgradeLevel = referenceLevel(area), runs = runs, total = round(total) }
end

local function details(area: number): BenchmarkDetails
	local a = anyArea(area)
	local cached = benchCache[a]
	if cached then
		return cached
	end
	local result: BenchmarkDetails
	if a <= AREA_COUNT or AREA_COUNT < 2 then
		result = simulateArea(a)
	else
		-- [PROPOSAL] Beyond the last area (only combat levels reach here) grow
		-- by the same ratio as the last two real areas.
		local last = details(AREA_COUNT)
		local before = details(AREA_COUNT - 1)
		local ratio = math.max(1.05, last.total / math.max(0.01, before.total))
		local factor = ratio ^ (a - AREA_COUNT)
		local runs: { BenchmarkRun } = {}
		local total = 0
		for i, run in last.runs do
			local payout = round(run.payout * factor)
			table.insert(runs, { level = RunnerDefs.FirstLevelOfArea(a) + i - 1, payout = payout })
			total += payout
		end
		result = { upgradeLevel = referenceLevel(a), runs = runs, total = round(total) }
	end
	benchCache[a] = result
	return result
end

function Economy.BenchmarkDetails(area: number): BenchmarkDetails
	local d = details(area)
	local runs: { BenchmarkRun } = {}
	for _, run in d.runs do
		table.insert(runs, { level = run.level, payout = run.payout })
	end
	return { upgradeLevel = d.upgradeLevel, runs = runs, total = d.total }
end

function Economy.TenRunBenchmark(area: number): number
	return details(area).total
end

function Economy.RunBenchmark(area: number): number
	local d = details(area)
	return round(d.total / math.max(1, #d.runs))
end

-------------------------------------------------------------------------------
-- Slots, gates, sections
-------------------------------------------------------------------------------
-- The first three revenue slots of an area (in slot order), and the sum of
-- their income weights.
local function firstThree(area: number): ({ [string]: boolean }, number, { Config.SlotDef })
	local set: { [string]: boolean } = {}
	local list: { Config.SlotDef } = {}
	local weight = 0
	for _, slotDef in Config.Areas[area].slots do
		if slotDef.kind == "revenue" and #list < 3 then
			set[slotDef.id] = true
			table.insert(list, slotDef)
			weight += slotDef.incomeWeight
		end
	end
	return set, weight, list
end

-- Split a whole-dollar `total` over `weights` with largest-remainder rounding:
-- every share is whole and they sum EXACTLY to `total` (ties -> earlier index).
local function splitWhole(total: number, weights: { number }): { number }
	local sum = 0
	for _, w in weights do
		sum += math.max(0, w)
	end
	local out: { number } = {}
	local order: { { index: number, frac: number } } = {}
	local given = 0
	for index, w in weights do
		local exact = if sum > 0 then total * math.max(0, w) / sum else total / #weights
		local whole = math.floor(exact + 1e-9)
		out[index] = whole
		given += whole
		table.insert(order, { index = index, frac = exact - whole })
	end
	table.sort(order, function(a, b)
		if math.abs(a.frac - b.frac) > 1e-9 then
			return a.frac > b.frac
		end
		return a.index < b.index
	end)
	local left = math.floor(total - given + 0.5)
	for i = 1, math.max(0, left) do
		local entry = order[(i - 1) % #order + 1]
		out[entry.index] += 1
	end
	return out
end

function Economy.SlotUnlockPrice(slotId: string): number
	local info = slotLookup[slotId]
	if not info then
		return math.huge -- unknown slot: can never be bought
	end
	local b10 = Economy.TenRunBenchmark(info.area)
	if info.def.kind ~= "revenue" then
		return price(EconomyDefs.Slots.ArenaShare * b10)
	end
	local set, weightOfThree, three = firstThree(info.area)
	local w = info.def.incomeWeight
	if Config.Benchmark.Mode == "EachSection" then
		if set[slotId] then
			return price(b10)
		end
		-- 4th revenue section: B10 per average weight of the first three.
		return price(b10 * w / math.max(0.01, weightOfThree / 3))
	end
	-- "CombinedSections" (default): the first three cost B10 together
	-- (whole dollars that still add up to exactly B10).
	if set[slotId] then
		local weights: { number } = {}
		local mine = 1
		for index, slotDef in three do
			weights[index] = slotDef.incomeWeight
			if slotDef.id == slotId then
				mine = index
			end
		end
		return math.max(1, splitWhole(b10, weights)[mine])
	end
	return price(b10 * w / math.max(0.01, weightOfThree))
end

function Economy.GatePrice(area: number): number
	local a = anyArea(area)
	local cached = gateCache[a]
	if cached then
		return cached
	end
	local gate = price(EconomyDefs.Slots.GateShare * Economy.TenRunBenchmark(a))
	if a > 1 then
		-- Rounded UP: always at least GateMinStep dearer than the gate before.
		local previous = Economy.GatePrice(a - 1)
		gate = math.max(gate, math.ceil(previous * EconomyDefs.Slots.GateMinStep - 1e-9), previous + 1)
	end
	gateCache[a] = gate
	return gate
end

function Economy.AreaUnlockTotal(area: number): number
	local a = realArea(area)
	local total = 0
	for _, slotDef in Config.Areas[a].slots do
		total += Economy.SlotUnlockPrice(slotDef.id)
	end
	return total + Economy.GatePrice(a)
end

local NO_INCOME: SectionStats = { incomePerSale = 0, saleInterval = 5, stockCapacity = 0, incomePerSecond = 0 }
-- [PROPOSAL] A section always holds at least this many sales (safety floor;
-- the real defs never come close: the smallest stock is about 18).
local MIN_STOCK = 5

function Economy.SectionStats(slotId: string, level: number): SectionStats
	local info = slotLookup[slotId]
	if not info or info.def.kind ~= "revenue" then
		return table.clone(NO_INCOME)
	end
	local d = EconomyDefs.Sections
	local lvl = math.clamp(math.floor(num(level, 1)), 1, d.MaxLevel)
	local areaWeight = 0
	for _, slotDef in Config.Areas[info.area].slots do
		if slotDef.kind == "revenue" then
			areaWeight += slotDef.incomeWeight
		end
	end
	local activeRate = Economy.RunBenchmark(info.area) / Economy.RunDurationSeconds()
	local levelOneRate = d.PassiveShare * activeRate * info.def.incomeWeight / math.max(0.01, areaWeight)
	local designedInterval = d.SaleInterval[info.index] or d.SaleInterval[#d.SaleInterval]
	local baseStock = d.StockCapacity[info.index] or d.StockCapacity[#d.StockCapacity]
	-- Designed sales per full stock at the designed interval (+10% per level).
	local designedStock = baseStock * (1 + d.StockPerLevel * (lvl - 1))
	-- Whole dollars per sale; the interval stretches by (rounded / exact) so
	-- the income per second stays exactly the designed rate.
	local rate = levelOneRate * (1 + d.IncomePerLevel * (lvl - 1))
	local exact = rate * designedInterval
	local perSale = math.max(1, round(exact))
	local interval = designedInterval
	if exact > 0 then
		interval = designedInterval * perSale / exact
	end
	-- The number of sales in a full stock scales the other way, so a full
	-- stock still lasts the designed designedStock x designedInterval seconds
	-- (restock rhythm unchanged by the rounding; upgrades make it last longer).
	local stock = math.max(MIN_STOCK, round(designedStock * designedInterval / interval))
	return {
		incomePerSale = perSale,
		saleInterval = interval,
		stockCapacity = stock,
		incomePerSecond = perSale / interval,
	}
end

function Economy.SectionUpgradePrice(slotId: string, currentLevel: number): number?
	local info = slotLookup[slotId]
	if not info or info.def.kind ~= "revenue" then
		return nil
	end
	local d = EconomyDefs.Sections
	local lvl = math.max(1, math.floor(num(currentLevel, 1)))
	if lvl >= d.MaxLevel then
		return nil
	end
	return price(Economy.SlotUnlockPrice(slotId) * d.UpgradeBaseShare * d.UpgradeGrowth ^ (lvl - 1))
end

function Economy.ReferencePassiveRate(area: number): number
	local total = 0
	for a = 1, realArea(area) do
		for _, slotDef in Config.Areas[a].slots do
			if slotDef.kind == "revenue" then
				total += Economy.SectionStats(slotDef.id, 1).incomePerSecond
			end
		end
	end
	return total
end

function Economy.EmployeeCashPrice(slotId: string, playerHighestArea: number): number
	local info = slotLookup[slotId]
	local area: number
	if info and Config.Employee.PriceScalesWith ~= "PlayerArea" then
		area = info.area
	else
		area = realArea(playerHighestArea)
	end
	return price(num(Config.Employee.RunsEquivalent, 20) * Economy.RunBenchmark(area))
end

-------------------------------------------------------------------------------
-- Runner upgrade prices
-------------------------------------------------------------------------------
function Economy.RunnerUpgradePrice(kind: string, currentLevel: number): number?
	local d = EconomyDefs.Runner
	local factor = (d.KindPriceFactor :: { [string]: number })[kind]
	if factor == nil then
		return nil
	end
	local lvl = math.max(0, math.floor(num(currentLevel, 0)))
	if lvl >= d.MaxLevel then
		return nil
	end
	return price(d.BasePriceShare * Economy.RunBenchmark(1) * d.PriceGrowth ^ lvl * factor)
end

-------------------------------------------------------------------------------
-- Boosts and vehicles
-------------------------------------------------------------------------------
function Economy.BoostValue(boostId: string, playerArea: number): number
	local boost = boostLookup[boostId]
	if not boost then
		return 0
	end
	local area = realArea(playerArea)
	if boost.kind == "multiplier" then
		local seconds = num(boost.minutes, 0) * 60
		return round(seconds / Economy.RunDurationSeconds() * Economy.RunBenchmark(area))
	elseif boost.kind == "timeskip" then
		local seconds = num(boost.hours, 0) * 3600
		return round(seconds * Economy.ReferencePassiveRate(area))
	end
	return 0
end

function Economy.BoostCoinPrice(boostId: string, playerArea: number): number
	if not boostLookup[boostId] then
		return math.huge -- unknown boost: can never be bought
	end
	-- Rounded DOWN (value / price >= 1.25), at least $1.
	local share = 1 - num(Config.CoinRoute.NetGainShare, 0.2)
	return math.max(1, math.floor(share * Economy.BoostValue(boostId, playerArea) + 1e-6))
end

-- What a time skip of `seconds` pays the given sections right now
-- (docs/ARCHITECTURE.md §6.2). Per unlocked revenue section:
-- sales = floor(seconds / saleInterval); with an employee every sale is paid,
-- without one only min(sales, floor(stock)) (the stock runs out). Pure: the
-- caller (BaseService) applies the stock decrement. Not 2x-boosted.
function Economy.TimeSkipPayout(
	sections: { [string]: SectionState },
	seconds: number
): (number, { [string]: TimeSkipSlot })
	local perSlot: { [string]: TimeSkipSlot } = {}
	local s = num(seconds, 0)
	if s <= 0 or type(sections) ~= "table" then
		return 0, perSlot
	end
	local total = 0
	for slotId, section in sections do
		local info = slotLookup[slotId]
		if type(section) ~= "table" or not info or info.def.kind ~= "revenue" then
			continue
		end
		local stats = Economy.SectionStats(slotId, section.level)
		if stats.saleInterval <= 0 then
			continue
		end
		-- 1e-9: 3600 / 4.6 style divisions never lose a whole sale to float noise.
		local sales = math.floor(s / stats.saleInterval + 1e-9)
		local stockUsed = 0
		if section.employee ~= true then
			sales = math.min(sales, math.max(0, math.floor(num(section.stock, 0))))
			stockUsed = sales
		end
		if sales > 0 then
			local paid = sales * stats.incomePerSale -- whole: incomePerSale is whole
			total += paid
			perSlot[slotId] = { paid = paid, sales = sales, stockUsed = stockUsed }
		end
	end
	return total, perSlot
end

-- [PROPOSAL] Value of a time skip for pricing: what it would pay right now
-- (`preview`, from TimeSkipPayout), capped at the area reference.
function Economy.TimeSkipValue(boostId: string, playerArea: number, preview: number): number
	local boost = boostLookup[boostId]
	if not boost or boost.kind ~= "timeskip" then
		return 0
	end
	-- Whole dollars, rounded down (never more than the preview pays).
	return math.floor(math.min(Economy.BoostValue(boostId, playerArea), math.max(0, num(preview, 0))) + 1e-6)
end

-- [PROPOSAL] Cash price of a time skip = (1 - NetGainShare) x TimeSkipValue,
-- rounded DOWN to whole dollars so it never exceeds 0.8 x what it pays. 0 = it pays
-- (almost) nothing and must not be sold. Unknown / non-skip boost -> math.huge.
function Economy.TimeSkipCoinPrice(boostId: string, playerArea: number, preview: number): number
	local boost = boostLookup[boostId]
	if not boost or boost.kind ~= "timeskip" then
		return math.huge
	end
	local share = 1 - num(Config.CoinRoute.NetGainShare, 0.2)
	return math.floor(share * Economy.TimeSkipValue(boostId, playerArea, preview) + 1e-6)
end

-- [PROPOSAL] A random offer only shows a time skip when it would pay at
-- least this share of its reference value (so offers are worth buying).
Economy.TimeSkipOfferMinShare = 0.25

function Economy.TimeSkipWorthOffering(boostId: string, playerArea: number, preview: number): boolean
	local boost = boostLookup[boostId]
	if not boost or boost.kind ~= "timeskip" then
		return false
	end
	local reference = Economy.BoostValue(boostId, playerArea)
	local p = num(preview, 0)
	return p >= 1 and p + 1e-6 >= Economy.TimeSkipOfferMinShare * reference
end

function Economy.VehicleCoinPrice(vehicleId: string, playerArea: number): number
	local vehicle = vehicleLookup[vehicleId]
	if not vehicle then
		return math.huge -- unknown vehicle: can never be bought
	end
	local area = math.max(realArea(playerArea), realArea(vehicle.area))
	return price(vehicle.priceFactor * Economy.GatePrice(area))
end

-------------------------------------------------------------------------------
-- Combat and equipment
-------------------------------------------------------------------------------
function Economy.BarrierCombatLevel(area: number): number
	local c = EconomyDefs.Combat
	return math.max(1, c.BarrierLevelsPerArea * anyArea(area) + c.BarrierLevelOffset)
end

-- R on a continuous area scale: R(a) at whole numbers, geometric in between.
local function smoothBenchmark(x: number): number
	local lower = math.max(1, math.floor(x))
	local r1 = Economy.RunBenchmark(lower)
	local r2 = Economy.RunBenchmark(lower + 1)
	local ratio = math.max(1.05, r2 / math.max(0.01, r1))
	return r1 * ratio ^ (x - lower)
end

function Economy.CombatLevelReward(level: number): number
	local c = EconomyDefs.Combat
	local lvl = math.max(1, math.floor(num(level, 1)))
	-- Level L sits at "area position" x where the barrier level of area a maps to x = a.
	local x = (lvl - c.BarrierLevelOffset) / c.BarrierLevelsPerArea
	return math.max(1, round(c.RewardRuns * smoothBenchmark(x)))
end

function Economy.EquipmentUpgradePrice(rarity: number, currentLevel: number): number?
	local d = EconomyDefs.Equipment
	local r = math.clamp(math.floor(num(rarity, 1)), 1, #Config.Rarities)
	local lvl = math.max(1, math.floor(num(currentLevel, 1)))
	if lvl >= Config.Rarities[r].maxLevel then
		return nil
	end
	local factor = d.RarityFactor[r] or d.RarityFactor[#d.RarityFactor]
	return price(d.BaseShare * Economy.RunBenchmark(1) * d.Growth ^ (lvl - 1) * factor)
end

return Economy
