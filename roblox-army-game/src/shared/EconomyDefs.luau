--!strict
--[[
	EconomyDefs
	Every balancing number of the economy in one place: area scaling, runner
	upgrades, slot/gate price shares, section incomes, combat rewards and
	equipment upgrade prices. Shared/Economy.luau turns these into prices.

	Pure data (no services, no Instances) so it loads in the Lune test runner.
	Values marked [PROPOSAL] are design assumptions: change them here, then run
	`lune run scripts/simulate` to regenerate docs/ECONOMY_TABLES.md.

	Public API (read-only constants):
		EconomyDefs.AreaScale                    cash values grow x1.6 per area
		EconomyDefs.ProgressEase                 price ease (0.8): unlock / gate / upgrade prices use B10 x this
		EconomyDefs.Runner                       permanent runner upgrade numbers
		EconomyDefs.Slots                        slot / gate price shares of Economy.PriceBenchmark
		EconomyDefs.Sections                     revenue section income, stock, upgrades
		EconomyDefs.Combat                       combat level rewards and barrier levels
		EconomyDefs.Equipment                    equipment upgrade prices
]]

local EconomyDefs = {}

-- [PROPOSAL] Every cash value on a runner course (troop value, cash stacks,
-- weapon values, enemy and boss rewards) is multiplied by AreaScale^(area - 1)
-- and rounded to whole dollars (no cents anywhere).
-- Prices are derived from the runner benchmark, so they follow automatically.
EconomyDefs.AreaScale = 1.6

-- [owner request] "So it's easier to progress": base unlocks, gates, section
-- upgrades, permanent runner upgrades and gear (equipment) upgrades cost this
-- share of what the 10-run benchmark gives (Economy.PriceBenchmark = B10 x ProgressEase). Round 4's
-- stronger guns and shoot-to-grow gates raised best-case runs ~24%; 0.8 keeps
-- those prices where they were while every run pays more. Employees keep the
-- "20 runs" rule and boosts the 80% rule (both requirements). 1 = the brief's
-- plain "10 runs fund three upgrades".
EconomyDefs.ProgressEase = 0.8

-------------------------------------------------------------------------------
-- Permanent runner upgrades (bought before a run) [REQUIREMENT: small steps]
-------------------------------------------------------------------------------
EconomyDefs.Runner = {
	MaxLevel = 50, -- [PROPOSAL]
	BaseTroops = 3, -- [PROPOSAL] troops at level 0 (the avatar + 2)
	TroopsPerLevel = 1, -- [PROPOSAL] "+1 troop" per level
	BaseDamage = 10, -- [PROPOSAL] damage of one troop at level 0
	DamagePerLevel = 0.04, -- [PROPOSAL] +4% of base damage per level
	BaseCapacity = 60, -- [PROPOSAL] $ you can carry on the track in area 1 (x AreaScale per area, whole $)
	-- [owner request] +20% of base capacity per level (was +6%: "increase the
	-- upgrade amount of coins so it's easier to progress"). The cash rows offer
	-- about 2.5x the reference bag (RunnerDefs.CASH_ROW_BASE), so the bag is the
	-- limit and each level adds real money per run: in area 1 about 7-8 levels
	-- above the reference level pay (+$12 each per best-case run), later areas
	-- have room for more levels (and pay more per level: about +$15 in area 4,
	-- +$49 in area 8). Gate cash bags go straight to the bank, not the bag.
	CapacityPerLevel = 0.2,
	-- Price of level 0 -> 1 as a share of R(1) x ProgressEase (one best-case
	-- area-1 run, eased), then x PriceGrowth for every further level.
	BasePriceShare = 0.4, -- [PROPOSAL]
	PriceGrowth = 1.17, -- [PROPOSAL]
	KindPriceFactor = { troops = 1.0, damage = 1.0, capacity = 0.9 }, -- [PROPOSAL]
}

-------------------------------------------------------------------------------
-- Slot (section) unlock prices and gate prices, as shares of PriceBenchmark
-------------------------------------------------------------------------------
-- B10(area) = best-case cash of the 10 benchmark runs of that area;
-- PriceBenchmark(area) = B10(area) x ProgressEase (Economy.PriceBenchmark).
-- "CombinedSections" (Config.Benchmark.Mode default): the first three revenue
--     sections of an area cost PriceBenchmark TOGETHER (= ProgressEase x B10),
--     split in proportion to their income weights (so every section repays
--     itself in the same time).
-- "EachSection": each of the first three revenue sections costs PriceBenchmark.
-- The 4th revenue section is priced at the same $ per income weight as the
-- first three. The Combat Arena (a2s4, no income weight) costs ArenaShare x
-- PriceBenchmark.
EconomyDefs.Slots = {
	ArenaShare = 0.6, -- [PROPOSAL]
	GateShare = 1.3, -- [PROPOSAL] gate from area a into a+1 costs 1.3 x PriceBenchmark(a)
	GateMinStep = 1.05, -- each gate costs at least 5% more than the previous one
}

-------------------------------------------------------------------------------
-- Revenue sections
-------------------------------------------------------------------------------
EconomyDefs.Sections = {
	MaxLevel = 10, -- [PROPOSAL]
	-- [PROPOSAL] A full area of level-1 sections earns this share of the active
	-- runner rate R(area) / RunDurationSeconds(). With CombinedSections prices a
	-- level-1 section then repays its unlock price in ~40 minutes of stocked running
	-- (~33 minutes with the eased prices, EconomyDefs.ProgressEase).
	-- (0.32 -> 0.36 when the castle boss fight made a run ~7 s longer, -> 0.39 when
	-- the boss intro / outro (~5.4 s) were counted in RunDurationSeconds: the share
	-- grows with the run length so the section $/s and the payback stay the same.)
	PassiveShare = 0.39,
	-- Designed seconds between sales, by slot index 1..4 (bigger shops sell less
	-- often but more per sale). Economy.SectionStats stretches or shrinks this so
	-- every sale is whole dollars (income per second stays exact).
	SaleInterval = { 4, 4.6, 5.2, 5.8 }, -- [PROPOSAL]
	-- Designed sales per full stock at the designed SaleInterval, by slot index:
	-- a full level-1 stock lasts StockCapacity x SaleInterval = 100 / 133 / 172 / 215 s
	-- (about 1.7-3.6 minutes) before a manual restock. SectionStats rescales the
	-- number of sales with the real interval so a full stock still lasts that long.
	StockCapacity = { 25, 29, 33, 37 }, -- [PROPOSAL]
	IncomePerLevel = 0.2, -- [PROPOSAL] +20% of level-1 income per upgrade level
	StockPerLevel = 0.1, -- [PROPOSAL] +10% of level-1 stock duration per upgrade level
	UpgradeBaseShare = 0.35, -- [PROPOSAL] level 1 -> 2 costs 35% of the unlock price
	UpgradeGrowth = 1.5, -- [PROPOSAL] each further level costs x1.5
}

-------------------------------------------------------------------------------
-- Combat
-------------------------------------------------------------------------------
EconomyDefs.Combat = {
	-- [PROPOSAL] barrier squad on area a's exit gate = combat level 3a - 1
	-- (area 1 -> level 2, area 2 -> level 5, area 3 -> level 8, ...).
	BarrierLevelsPerArea = 3,
	BarrierLevelOffset = -1,
	-- [PROPOSAL] a perfect clear of a combat level pays about this many best-case
	-- runner runs of the area the level belongs to (it takes a few minutes).
	RewardRuns = 3,
}

-------------------------------------------------------------------------------
-- Equipment upgrades (coins spent on armour / weapons from chests)
-------------------------------------------------------------------------------
EconomyDefs.Equipment = {
	BaseShare = 0.5, -- [PROPOSAL] level 1 -> 2 of a Common item costs 0.5 x R(1) x ProgressEase
	Growth = 1.15, -- [PROPOSAL] x1.15 per level
	RarityFactor = { 1.0, 1.5, 2.2, 3.2, 4.5 }, -- [PROPOSAL] Common .. Legendary
}

return EconomyDefs
