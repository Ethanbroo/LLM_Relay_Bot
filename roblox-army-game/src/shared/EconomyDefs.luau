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
		EconomyDefs.Runner                       permanent runner upgrade numbers
		EconomyDefs.Slots                        slot / gate price shares of B10
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
	CapacityPerLevel = 0.06, -- [PROPOSAL] +6% of base capacity per level
	-- Price of level 0 -> 1 as a share of R(1) (one best-case area-1 run),
	-- then x PriceGrowth for every further level.
	BasePriceShare = 0.4, -- [PROPOSAL]
	PriceGrowth = 1.17, -- [PROPOSAL]
	KindPriceFactor = { troops = 1.0, damage = 1.0, capacity = 0.9 }, -- [PROPOSAL]
}

-------------------------------------------------------------------------------
-- Slot (section) unlock prices and gate prices, as shares of B10
-------------------------------------------------------------------------------
-- B10(area) = best-case cash of the 10 benchmark runs of that area.
-- "CombinedSections" (Config.Benchmark.Mode default): the first three revenue
--     sections of an area cost B10 TOGETHER, split in proportion to their
--     income weights (so every section repays itself in the same time).
-- "EachSection": each of the first three revenue sections costs B10.
-- The 4th revenue section is priced at the same $ per income weight as the
-- first three. The Combat Arena (a2s4, no income weight) costs ArenaShare x B10.
EconomyDefs.Slots = {
	ArenaShare = 0.6, -- [PROPOSAL]
	GateShare = 1.3, -- [PROPOSAL] gate from area a into a+1 costs 1.3 x B10(a)
	GateMinStep = 1.05, -- each gate costs at least 5% more than the previous one
}

-------------------------------------------------------------------------------
-- Revenue sections
-------------------------------------------------------------------------------
EconomyDefs.Sections = {
	MaxLevel = 10, -- [PROPOSAL]
	-- [PROPOSAL] A full area of level-1 sections earns this share of the active
	-- runner rate R(area) / RunDurationSeconds(). With CombinedSections prices a
	-- level-1 section then repays its unlock price in ~40 minutes of stocked running.
	-- (Raised from 0.32 when the castle boss fight made a run ~7 s longer.)
	PassiveShare = 0.36,
	-- Seconds between sales, by slot index 1..4 (bigger shops sell less often but more per sale).
	SaleInterval = { 4, 4.6, 5.2, 5.8 }, -- [PROPOSAL]
	-- Sales before a manual restock is needed, by slot index (25-37 sales = about 2-3.5 minutes).
	StockCapacity = { 25, 29, 33, 37 }, -- [PROPOSAL]
	IncomePerLevel = 0.2, -- [PROPOSAL] +20% of level-1 income per upgrade level
	StockPerLevel = 0.1, -- [PROPOSAL] +10% of level-1 stock per upgrade level
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
	BaseShare = 0.5, -- [PROPOSAL] level 1 -> 2 of a Common item costs 0.5 x R(1)
	Growth = 1.15, -- [PROPOSAL] x1.15 per level
	RarityFactor = { 1.0, 1.5, 2.2, 3.2, 4.5 }, -- [PROPOSAL] Common .. Legendary
}

return EconomyDefs
