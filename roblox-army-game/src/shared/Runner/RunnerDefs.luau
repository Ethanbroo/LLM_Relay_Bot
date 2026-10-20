--!strict
--[[
	RunnerDefs
	Every tunable number of the army runner in one place (course geometry,
	speeds, weapon tiers, gate values, enemy strength). Pure data: no services,
	no Instances, so it also loads in the Lune test runner.

	Values marked [PROPOSAL] are design assumptions (the brief does not give
	them). Change them here and run `lune run scripts/simulate` to see the new
	economy tables (docs/ECONOMY_TABLES.md).

	Public API (all read-only):
		RunnerDefs.RUN_SPEED              studs per second the avatar runs down the track
		RunnerDefs.TRACK_LENGTH           = Layout.RUNNER_TRACK_LENGTH
		RunnerDefs.TRACK_WIDTH            = Layout.RUNNER_TRACK_WIDTH
		RunnerDefs.ROW_COUNT, FIRST_ROW_DISTANCE, ROW_SPACING, BOSS_DISTANCE
		RunnerDefs.LANES                  lane geometry per row kind: { gate, cash, squad1, squad2, barricade }
		RunnerDefs.WEAPONS                weapon tiers 1..6: { name, short, damageMult, value }
		RunnerDefs.MAX_WEAPON_TIER
		RunnerDefs.TROOP_VALUE, DAMAGE_GATE_AMOUNT, DAMAGE_GATE_VALUE, ...
		RunnerDefs.BOSS_SECONDS, BARRICADE_TROOP_LOSS
		RunnerDefs.TroopScale(area)       growth of troop counts (gates, squads) by area
		RunnerDefs.EnemyHpScale(area)     growth of one enemy soldier's hit points by area
		RunnerDefs.ArmyScale(area)        growth of the reference army (squad sizes, barricade / base hp)
		RunnerDefs.ReferenceLevel(area)   permanent upgrade level of the benchmark player
		RunnerDefs.ExpectedFirepower(row) firepower of a good area-1 player at a row (enemy sizing)
		RunnerDefs.LevelFactor(area, level) gentle growth of values with the run level
		RunnerDefs.FirstLevelOfArea(area) runner level at which an area "starts" (benchmark L0)
]]

local Shared = script.Parent.Parent
local Config = require(Shared:WaitForChild("Config"))
local EconomyDefs = require(Shared:WaitForChild("EconomyDefs"))
local Layout = require(Shared:WaitForChild("Layout"))

export type LaneDef = { x: number, width: number }

export type WeaponDef = {
	name: string, -- shown in the completion summary: "Weapon: Assault Rifle"
	short: string, -- shown on gate panels: "RIFLE"
	damageMult: number, -- firepower multiplier while holding this weapon
	value: number, -- cash value (area 1 dollars) of reaching this tier in a run
}

local RunnerDefs = {}

-------------------------------------------------------------------------------
-- Speed and track
-------------------------------------------------------------------------------
RunnerDefs.RUN_SPEED = 24 -- [PROPOSAL] studs per second (Roblox default walk is 16)
RunnerDefs.TRACK_LENGTH = Layout.RUNNER_TRACK_LENGTH -- 900 studs
RunnerDefs.TRACK_WIDTH = Layout.RUNNER_TRACK_WIDTH -- 24 studs

-- Rows of gates / cash / enemies, evenly spaced down the track.
RunnerDefs.ROW_COUNT = 18 -- [PROPOSAL]
RunnerDefs.FIRST_ROW_DISTANCE = 70 -- [PROPOSAL] studs from the start line to row 1
RunnerDefs.ROW_SPACING = 45 -- [PROPOSAL] studs between rows (row 18 is at 835)
RunnerDefs.BOSS_DISTANCE = 870 -- [PROPOSAL] the enemy base stands here (track is 900 long)

-- Seconds a typical run takes on top of running: pre-run screen, countdown,
-- boss animation and the completion summary. Used by Economy.RunDurationSeconds.
RunnerDefs.RUN_OVERHEAD_SECONDS = 16 -- [PROPOSAL]

-------------------------------------------------------------------------------
-- Lanes (x = lateral centre in studs, + = right of the centre line)
-------------------------------------------------------------------------------
-- All lanes fit inside the 24-stud track (edges at -12 / +12).
RunnerDefs.LANES = {
	gate = { { x = -6, width = 11 }, { x = 6, width = 11 } }, -- 2 lanes
	cash = { { x = -8, width = 7 }, { x = 0, width = 7 }, { x = 8, width = 7 } }, -- 3 lanes
	squad1 = { { x = 0, width = 22 } }, -- 1 lane across the whole track (unavoidable fight)
	squad2 = { { x = -6, width = 11 }, { x = 6, width = 11 } }, -- 2 different squads
	barricade = { { x = -6, width = 11 }, { x = 6, width = 11 } }, -- 2 barricades
} :: { [string]: { LaneDef } }

-------------------------------------------------------------------------------
-- Weapons collected during a run (gold gates, +1 tier each)
-------------------------------------------------------------------------------
-- [PROPOSAL] Tier 1 is what every run starts with. `value` is the cash the
-- completion summary credits for reaching that tier (scaled by area like all
-- cash values). A weapon gate collected at the top tier still pays the Rocket
-- value ("MAX"), so a gold gate is never a wasted pick.
RunnerDefs.WEAPONS = {
	{ name = "Pistol", short = "PISTOL", damageMult = 1.0, value = 0 },
	{ name = "Rifle", short = "RIFLE", damageMult = 1.35, value = 8 },
	{ name = "SMG", short = "SMG", damageMult = 1.75, value = 12 },
	{ name = "Assault Rifle", short = "ASSAULT", damageMult = 2.25, value = 18 },
	{ name = "Minigun", short = "MINIGUN", damageMult = 2.9, value = 25 },
	{ name = "Rocket", short = "ROCKET", damageMult = 3.7, value = 35 },
} :: { WeaponDef }
RunnerDefs.MAX_WEAPON_TIER = #RunnerDefs.WEAPONS

-------------------------------------------------------------------------------
-- Payout values (area 1 dollars; multiplied by the area scale and level factor)
-------------------------------------------------------------------------------
RunnerDefs.TROOP_VALUE = 1.5 -- [PROPOSAL] $ per troop alive at the finish ("$1.50 each")
RunnerDefs.DAMAGE_GATE_AMOUNT = 0.2 -- [PROPOSAL] +20% damage per damage gate (additive)
RunnerDefs.DAMAGE_GATE_VALUE = 9 -- [PROPOSAL] $ credited per damage gate collected
RunnerDefs.CASH_ROW_BASE = 9 -- [PROPOSAL] middle cash lane of the first cash row
RunnerDefs.CASH_ROW_GROWTH = 0.06 -- [PROPOSAL] +6% cash per row further down the track
RunnerDefs.CASH_LANE_SPREAD = { 0.55, 1.0, 1.6 } -- [PROPOSAL] low / mid / high cash lanes
RunnerDefs.TOLL_CASH_BASE = 16 -- [PROPOSAL] cash behind a red "toll" gate (trade troops for cash)
RunnerDefs.ENEMY_REWARD = 0.5 -- [PROPOSAL] $ per enemy soldier defeated
RunnerDefs.BARRICADE_REWARD = 7 -- [PROPOSAL] $ for destroying a weak barricade (strong pays ~2.2x)
RunnerDefs.BOSS_REWARD = 60 -- [PROPOSAL] $ for destroying 100% of the enemy base

-------------------------------------------------------------------------------
-- Troop maths gates (the original + x - ÷ modifiers, applied to TROOP COUNT)
-------------------------------------------------------------------------------
RunnerDefs.ADD_BASE = 2 -- [PROPOSAL] "+N" at row 1 (grows with row and area)
RunnerDefs.ADD_PER_ROW = 0.25 -- [PROPOSAL] extra +N per row index
RunnerDefs.MUL_ROWS = 2 -- [PROPOSAL] troop gates per course that offer "x2"
RunnerDefs.SUB_BASE = 2 -- [PROPOSAL] "-N" at row 1
RunnerDefs.SUB_PER_ROW = 0.2 -- [PROPOSAL]

-------------------------------------------------------------------------------
-- Enemies (squads, barricades, the enemy base)
-------------------------------------------------------------------------------
-- [PROPOSAL] Enemies are sized against the army a good area-1 player has
-- when reaching row r: about EXPECTED_TROOPS_BASE + EXPECTED_TROOPS_PER_ROW x r
-- troops, firing EXPECTED_POWER_PER_ROW better weapons/damage per row.
-- (Other areas multiply by EnemyHpScale x ArmyScale.)
RunnerDefs.EXPECTED_TROOPS_BASE = 3
RunnerDefs.EXPECTED_TROOPS_PER_ROW = 2.2
RunnerDefs.EXPECTED_POWER_PER_ROW = 0.04
RunnerDefs.ENEMY_HP = 30 -- [PROPOSAL] hit points of one enemy soldier in area 1
RunnerDefs.SQUAD_SHARE = 0.35 -- [PROPOSAL] soldiers per expected troop (normal squad)
RunnerDefs.SQUAD_SMALL = 0.6 -- [PROPOSAL] size of the small squad in a 2-squad row
RunnerDefs.SQUAD_BIG = 1.45 -- [PROPOSAL] size of the big squad (pays more, costs more)
RunnerDefs.SQUAD_FINAL = 1.25 -- [PROPOSAL] size of the last squad before the enemy base
RunnerDefs.BARRICADE_WEAK = 0.75 -- [PROPOSAL] weak barricade hp = 75% of expected firepower
RunnerDefs.BARRICADE_STRONG = 1.7 -- [PROPOSAL] strong barricade hp = 170% of expected firepower
RunnerDefs.BARRICADE_TROOP_LOSS = 0.25 -- failed barricade costs 25% of troops (min 1 left)
RunnerDefs.BOSS_HP = 5000 -- [PROPOSAL] enemy base hit points in area 1
RunnerDefs.BOSS_SECONDS = 5 -- [PROPOSAL] seconds the squad shoots at the enemy base

-------------------------------------------------------------------------------
-- Scaling helpers
-------------------------------------------------------------------------------
-- [PROPOSAL] Troop counts on gates and in squads grow slowly with the area
-- (+12% per area) so numbers stay readable while upgraded players still face
-- meaningful choices.
function RunnerDefs.TroopScale(area: number): number
	return 1 + 0.12 * (math.max(1, area) - 1)
end

-- Permanent-upgrade level of the reference player in `area` (same rule as
-- Economy.ReferenceUpgrades: Config.Benchmark.ReferenceUpgradeLevelsPerArea per area).
function RunnerDefs.ReferenceLevel(area: number): number
	local perArea = Config.Benchmark.ReferenceUpgradeLevelsPerArea
	return math.clamp((math.max(1, area) - 1) * perArea, 0, EconomyDefs.Runner.MaxLevel)
end

-- [PROPOSAL] Hit points of ONE enemy soldier grow exactly like the damage of
-- the reference player (4 damage levels per area x 4% = +16% per area), so a
-- reference-upgraded player wins most fights in every area while an
-- un-upgraded player falls behind.
function RunnerDefs.EnemyHpScale(area: number): number
	return 1 + EconomyDefs.Runner.DamagePerLevel * RunnerDefs.ReferenceLevel(area)
end

-- [PROPOSAL] How much bigger the reference player's army is than in area 1.
-- Starting troops (base + troop upgrades) count ARMY_START_WEIGHT times
-- (they get doubled by the x2 gates), plus ARMY_GATE_TROOPS troops picked up
-- from gates (which grow with TroopScale). Squad sizes and barricade / enemy
-- base hit points grow with this so fights stay fights in every area.
RunnerDefs.ARMY_START_WEIGHT = 3
RunnerDefs.ARMY_GATE_TROOPS = 30
function RunnerDefs.ArmyScale(area: number): number
	local r = EconomyDefs.Runner
	local start = r.BaseTroops + r.TroopsPerLevel * RunnerDefs.ReferenceLevel(area)
	local w, g = RunnerDefs.ARMY_START_WEIGHT, RunnerDefs.ARMY_GATE_TROOPS
	return (w * start + g * RunnerDefs.TroopScale(area)) / (w * r.BaseTroops + g)
end

-- Firepower a good area-1 player has when reaching row `rowIndex`.
function RunnerDefs.ExpectedFirepower(rowIndex: number): number
	local troops = RunnerDefs.EXPECTED_TROOPS_BASE + RunnerDefs.EXPECTED_TROOPS_PER_ROW * rowIndex
	return troops * EconomyDefs.Runner.BaseDamage * (1 + RunnerDefs.EXPECTED_POWER_PER_ROW * rowIndex)
end

-- Runner level at which an area "starts": the 10 benchmark runs of `area` are
-- levels FirstLevelOfArea(area) .. FirstLevelOfArea(area) + 9.
function RunnerDefs.FirstLevelOfArea(area: number): number
	return 1 + 10 * (math.max(1, area) - 1)
end

-- [PROPOSAL] Values grow gently (+1% per run level) after the area's first
-- level, capped at +20%, so grinding one area forever cannot inflate rewards.
RunnerDefs.LEVEL_STEP = 0.01
RunnerDefs.LEVEL_CAP = 20
function RunnerDefs.LevelFactor(area: number, level: number): number
	local inArea = math.clamp(level - RunnerDefs.FirstLevelOfArea(area), 0, RunnerDefs.LEVEL_CAP)
	return 1 + RunnerDefs.LEVEL_STEP * inArea
end

return RunnerDefs
