--!strict
--[[
	CombatDefs
	All the rules and numbers of the Archero-2-inspired combat mode, in one
	pure module (no services, no Instances) so the server, the client and the
	Lune tests (tests/combat.spec.luau) all agree. See docs/ARCHITECTURE.md §6.3.

	Coordinates: everything in combat uses ARENA-LOCAL studs. x = 0, z = 0 is
	the arena centre (Layout.ArenaCenter(slot)); +x is east, +z is south (the
	player starts on the south side, enemies spawn in the north).

	Public API:
		-- constants (seconds / studs / Hz)
		CombatDefs.TICK_RATE, SNAP_RATE, COUNTDOWN, WAVE_BREAK, ABILITY_TIMEOUT,
		CombatDefs.RESULT_TIMEOUT, MAGNET_RADIUS, STILL_SPEED, CLIENT_HIT_RADIUS,
		CombatDefs.SERVER_HIT_RADIUS, REPORT_TOLERANCE, HALF_X, HALF_Z, PLAYER_START
		CombatDefs.COVER: { Cover }                    crates / sandbags (shared by server + client)
		CombatDefs.ENEMIES: { [string]: EnemyDef }     Rifleman, Charger, Grenadier, Gunner, Sniper, Officer
		CombatDefs.ENEMY_ORDER: { string }             type ids in a fixed order (network codes)
		CombatDefs.HpScale(level), CombatDefs.DamageScale(level): number
		CombatDefs.EnemyStats(typeId, level): EnemyStats   scaled timings/speeds for one enemy type
		CombatDefs.WaveCount(level, mode): number      mode = "arena" | "barrier"
		CombatDefs.HasBoss(level, mode): boolean
		CombatDefs.LevelPlan(level, mode): LevelPlan   waves, enemies (hp/damage/cash), cash piles
		CombatDefs.Difficulty(plan): number            sum over enemies of (hp x damage)
		CombatDefs.DifficultySkulls(level): number     1..5 skulls for the level menu
		CombatDefs.ABILITIES: { Ability }              in-run 1-of-3 picks (last ONLY for that attempt)
		CombatDefs.AbilityById: { [string]: Ability }
		CombatDefs.RollAbilities(stacks, rng, count?): { string }   random distinct ids, respects maxStacks
		CombatDefs.PERK_IDS: { string }                every perk id of ARCHITECTURE §4.5
		CombatDefs.DeriveStats(base, abilityStacks): RunStats       equipment stats + perks + abilities
		CombatDefs.ChestTier(mode, level, victory, kills): string?  "gold" | "silver" | "wood" | nil
		CombatDefs.EncodeSnap(snap): buffer / CombatDefs.DecodeSnap(buf): Snap?
			compact binary form of the 20 Hz "CombatSnap" (unreliable remotes drop
			payloads over ~1 KB, so the snapshot is packed into a buffer).
		CombatDefs.StateCode / StateName, TypeCode / TypeName   network codes
]]

local CombatFolder = script.Parent
local Shared = CombatFolder.Parent
local Economy = require(Shared.Economy)
local EquipmentDefs = require(CombatFolder.EquipmentDefs)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Rng = require(Shared.Rng)

type CombatStats = EquipmentDefs.CombatStats

local CombatDefs = {}

-------------------------------------------------------------------------------
-- Timing, arena and hit rules
-------------------------------------------------------------------------------
CombatDefs.TICK_RATE = 30 -- server simulation steps per second (ARCH §0.10: <= 30 Hz)
CombatDefs.SNAP_RATE = 20 -- snapshots per second sent to the client
CombatDefs.COUNTDOWN = 3 -- "3-2-1" before wave 1
CombatDefs.WAVE_BREAK = 1.5 -- [PROPOSAL] seconds between the ability pick and the next wave
CombatDefs.ABILITY_TIMEOUT = 20 -- seconds to pick an ability before the first one is auto-picked
CombatDefs.RESULT_TIMEOUT = 60 -- result screen stays at most this long before returning to base
CombatDefs.MAGNET_RADIUS = 5 -- [PROPOSAL] cash is collected within this many studs
CombatDefs.STILL_SPEED = 0.6 -- the player auto-fires only below this horizontal speed (studs/s)
CombatDefs.CLIENT_HIT_RADIUS = 1.6 -- client reports an enemy bullet this close to its root
CombatDefs.SERVER_HIT_RADIUS = 0.6 -- server applies hits it sees itself this close (anti-cheat floor)
CombatDefs.REPORT_TOLERANCE = 4 -- a reported hit must be this close to the server's player position
CombatDefs.PLAYER_RADIUS = 1.2 -- body radius for grenades and melee
CombatDefs.SPAWN_TIME = 0.9 -- [PROPOSAL] enemies "warp in" for this long before acting

CombatDefs.HALF_X = Layout.ARENA_SIZE_X / 2
CombatDefs.HALF_Z = Layout.ARENA_SIZE_Z / 2
CombatDefs.PLAYER_START = { x = 0, z = Layout.ARENA_SIZE_Z / 2 - 10 }
CombatDefs.ENEMY_ZONE = { minZ = -Layout.ARENA_SIZE_Z / 2 + 6, maxZ = -4 } -- enemies spawn in the north half

-- Cover blocks (arena-local, axis-aligned). They block bullets and enemies.
-- kind: "crate" (wooden box) | "sandbag" (yellow Lego brick wall) | "brick" (red Lego brick)
export type Cover = { x: number, z: number, sx: number, sz: number, height: number, kind: string }
CombatDefs.COVER = {
	{ x = -20, z = -18, sx = 6, sz = 6, height = 5, kind = "crate" },
	{ x = 20, z = -18, sx = 6, sz = 6, height = 5, kind = "crate" },
	{ x = 0, z = 2, sx = 12, sz = 3, height = 3, kind = "sandbag" },
	{ x = -24, z = 20, sx = 4, sz = 8, height = 4, kind = "brick" },
	{ x = 24, z = 20, sx = 4, sz = 8, height = 4, kind = "brick" },
} :: { Cover }

-- Is the point (x, z) inside a cover block grown by `pad` studs?
function CombatDefs.CoverAt(x: number, z: number, pad: number?): Cover?
	local grow = pad or 0
	for _, c in CombatDefs.COVER do
		if math.abs(x - c.x) <= c.sx / 2 + grow and math.abs(z - c.z) <= c.sz / 2 + grow then
			return c
		end
	end
	return nil
end

-------------------------------------------------------------------------------
-- Enemy types  [PROPOSAL: all numbers]
-------------------------------------------------------------------------------
-- attack kinds:
--   "rifle"   keeps its distance, short aim (telegraph) then ONE aimed slow bullet
--   "melee"   runs in, crouches (telegraph), then dashes and hits on contact
--   "grenade" lobs a grenade at where you stand: red circle on the floor, boom after `fuse`
--   "spread"  a fan of 3-5 slow bullets
--   "sniper"  shows a red laser line for `windup` seconds, then a fast bullet along it
--   "rings"   (boss) rings of bullets in every direction + aimed bursts
export type EnemyDef = {
	id: string,
	name: string,
	hp: number,
	damage: number, -- per bullet / per hit
	speed: number, -- studs per second
	radius: number, -- body radius (studs)
	attack: string,
	interval: number, -- seconds between attacks
	windup: number, -- telegraph time before the attack fires
	bulletSpeed: number,
	bullets: number, -- bullets per attack (spread: minimum)
	bulletsMax: number,
	spreadDeg: number,
	preferredRange: number, -- distance it tries to keep from the player
	minLevel: number, -- first level it can appear in
	weight: number, -- base spawn weight
	weightGrowth: number, -- extra weight per level after minLevel (more dangerous types later)
	cashWeight: number, -- share of the enemy cash it drops
	scale: number, -- model size (client)
	boss: boolean,
	-- melee only
	dashSpeed: number?,
	dashTime: number?,
	triggerRange: number?,
	-- grenade only
	fuse: number?,
	blastRadius: number?,
}

local function enemy(def: { [string]: any }): EnemyDef
	local out = {
		id = def.id,
		name = def.name,
		hp = def.hp,
		damage = def.damage,
		speed = def.speed,
		radius = def.radius,
		attack = def.attack,
		interval = def.interval,
		windup = def.windup,
		bulletSpeed = def.bulletSpeed or 0,
		bullets = def.bullets or 1,
		bulletsMax = def.bulletsMax or def.bullets or 1,
		spreadDeg = def.spreadDeg or 0,
		preferredRange = def.preferredRange or 0,
		minLevel = def.minLevel or 1,
		weight = def.weight or 0,
		weightGrowth = def.weightGrowth or 0,
		cashWeight = def.cashWeight or 1,
		scale = def.scale or 1,
		boss = def.boss == true,
		dashSpeed = def.dashSpeed,
		dashTime = def.dashTime,
		triggerRange = def.triggerRange,
		fuse = def.fuse,
		blastRadius = def.blastRadius,
	}
	return out :: EnemyDef
end

CombatDefs.ENEMIES = {
	Rifleman = enemy({
		id = "Rifleman",
		name = "Rifleman",
		hp = 30,
		damage = 8,
		speed = 7,
		radius = 1.3,
		attack = "rifle",
		interval = 2.2,
		windup = 0.55,
		bulletSpeed = 22, -- slow and dodgeable [REQUIREMENT: dodge attacks]
		preferredRange = 22,
		minLevel = 1,
		weight = 10,
		cashWeight = 1,
	}),
	Charger = enemy({
		id = "Charger",
		name = "Charger",
		hp = 42,
		damage = 12,
		speed = 9.5,
		radius = 1.4,
		attack = "melee",
		interval = 1.8,
		windup = 0.7,
		dashSpeed = 30,
		dashTime = 0.6,
		triggerRange = 15,
		minLevel = 1,
		weight = 5,
		weightGrowth = 0.2,
		cashWeight = 1.2,
	}),
	Grenadier = enemy({
		id = "Grenadier",
		name = "Grenadier",
		hp = 34,
		damage = 15,
		speed = 6,
		radius = 1.3,
		attack = "grenade",
		interval = 3.4,
		windup = 0.4,
		fuse = 0.9, -- red circle telegraph time
		blastRadius = 5,
		preferredRange = 26,
		minLevel = 3,
		weight = 4,
		weightGrowth = 0.4,
		cashWeight = 1.3,
	}),
	Gunner = enemy({
		id = "Gunner",
		name = "Gunner",
		hp = 58,
		damage = 6,
		speed = 5.5,
		radius = 1.5,
		attack = "spread",
		interval = 2.8,
		windup = 0.6,
		bulletSpeed = 19,
		bullets = 3,
		bulletsMax = 5,
		spreadDeg = 44,
		preferredRange = 18,
		minLevel = 5,
		weight = 4,
		weightGrowth = 0.4,
		cashWeight = 1.5,
	}),
	Sniper = enemy({
		id = "Sniper",
		name = "Sniper",
		hp = 26,
		damage = 20,
		speed = 5,
		radius = 1.2,
		attack = "sniper",
		interval = 3.8,
		windup = 1.0, -- laser sight time
		bulletSpeed = 46, -- the one fast enemy bullet; the laser shows exactly where it goes
		preferredRange = 38,
		minLevel = 7,
		weight = 3,
		weightGrowth = 0.35,
		cashWeight = 1.5,
	}),
	Officer = enemy({
		id = "Officer",
		name = "Officer",
		hp = 0, -- boss HP is computed from the level's budget (see LevelPlan)
		damage = 7,
		speed = 4.5,
		radius = 2.8,
		attack = "rings",
		interval = 2.6,
		windup = 0.9,
		bulletSpeed = 17,
		bullets = 14, -- bullets per ring
		bulletsMax = 14,
		spreadDeg = 30, -- aimed burst spread
		preferredRange = 16,
		minLevel = 1,
		weight = 0, -- never random: placed on boss waves only
		cashWeight = 8,
		scale = 1.8,
		boss = true,
	}),
} :: { [string]: EnemyDef }

CombatDefs.ENEMY_ORDER = { "Rifleman", "Charger", "Grenadier", "Gunner", "Sniper", "Officer" }

CombatDefs.STATES = { "move", "aim", "charge", "dash", "throw", "laser", "spawn", "cool" }

local typeCodes: { [string]: number } = {}
for index, id in CombatDefs.ENEMY_ORDER do
	typeCodes[id] = index
end
local stateCodes: { [string]: number } = {}
for index, name in CombatDefs.STATES do
	stateCodes[name] = index
end

function CombatDefs.TypeCode(typeId: string): number
	return typeCodes[typeId] or 1
end
function CombatDefs.TypeName(code: number): string
	return CombatDefs.ENEMY_ORDER[code] or "Rifleman"
end
function CombatDefs.StateCode(state: string): number
	return stateCodes[state] or 1
end
function CombatDefs.StateName(code: number): string
	return CombatDefs.STATES[code] or "move"
end

-------------------------------------------------------------------------------
-- Level scaling  [PROPOSAL]
-------------------------------------------------------------------------------
CombatDefs.HP_GROWTH = 1.18 -- enemy HP x1.18 per level
CombatDefs.DAMAGE_GROWTH = 1.10 -- enemy damage x1.10 per level
CombatDefs.BASE_BUDGET = 3600 -- level-1 "threat budget" (sum of base hp x base damage)
CombatDefs.BUDGET_GROWTH = 1.08 -- more enemies per level
CombatDefs.MAX_ENEMIES_PER_WAVE = 14 -- keeps fights readable (and snapshots small)
CombatDefs.BOSS_SHARE = 0.35 -- share of a boss level's budget that goes into the Officer
CombatDefs.BOSS_MIN_BASE_HP = 120
CombatDefs.CASH_ENEMY_SHARE = 0.7 -- 70% of the level reward is dropped by enemies, 30% lies on the ground
CombatDefs.PILES_PER_WAVE = 3

local function sanitizeLevel(level: any): number
	if type(level) ~= "number" or level ~= level or level == math.huge or level == -math.huge then
		return 1
	end
	return math.clamp(math.floor(level), 1, 9999)
end

function CombatDefs.HpScale(level: number): number
	return CombatDefs.HP_GROWTH ^ (sanitizeLevel(level) - 1)
end

function CombatDefs.DamageScale(level: number): number
	return CombatDefs.DAMAGE_GROWTH ^ (sanitizeLevel(level) - 1)
end

export type EnemyStats = {
	speed: number,
	interval: number,
	windup: number,
	bulletSpeed: number,
}

-- Speeds and timings of one enemy type at `level` (HP and damage are in the plan).
-- [PROPOSAL] enemies get up to 30% faster and attack up to 30% more often;
-- bullets get at most 20% faster so they always stay dodgeable.
function CombatDefs.EnemyStats(typeId: string, level: number): EnemyStats
	local def = CombatDefs.ENEMIES[typeId] or CombatDefs.ENEMIES.Rifleman
	local l = sanitizeLevel(level) - 1
	return {
		speed = def.speed * (1 + math.min(0.3, 0.012 * l)),
		interval = def.interval * math.max(0.7, 1 - 0.012 * l),
		windup = def.windup,
		bulletSpeed = def.bulletSpeed * (1 + math.min(0.2, 0.01 * l)),
	}
end

-- [REQUIREMENT ARCH §6.3 / task] arena: 3 + floor(level / 4) waves (max 6); barrier: 4 waves.
function CombatDefs.WaveCount(level: number, mode: string): number
	if mode == "barrier" then
		return 4
	end
	return math.min(6, 3 + math.floor(sanitizeLevel(level) / 4))
end

-- Barrier fights always end with the Officer; arena levels 5, 10, 15... too.
function CombatDefs.HasBoss(level: number, mode: string): boolean
	if mode == "barrier" then
		return true
	end
	return sanitizeLevel(level) % 5 == 0
end

-------------------------------------------------------------------------------
-- Level plans
-------------------------------------------------------------------------------
export type PlanEnemy = { type: string, hp: number, damage: number, cash: number, boss: boolean }
export type PlanWave = { enemies: { PlanEnemy }, piles: { number } }
export type LevelPlan = {
	level: number,
	mode: string,
	waveCount: number,
	waves: { PlanWave },
	hasBoss: boolean,
	reward: number, -- Economy.CombatLevelReward(level): cash of a perfect clear
	enemyCash: number,
	pileCash: number,
	enemyCount: number,
	totalHp: number,
	difficulty: number,
}

local function spawnWeight(def: EnemyDef, level: number): number
	if def.boss or level < def.minLevel then
		return 0
	end
	return def.weight + def.weightGrowth * (level - def.minLevel)
end

local function baseThreat(def: EnemyDef): number
	return def.hp * def.damage
end

-- Split `total` cents-exactly over `weights` (last entry takes the rounding remainder).
local function splitCash(total: number, weights: { number }): { number }
	local out = {}
	local sum = 0
	for _, w in weights do
		sum += w
	end
	local given = 0
	for index, w in weights do
		if index == #weights then
			out[index] = math.max(0, Format.RoundCents(total - given))
		else
			local value = if sum > 0 then Format.RoundCents(total * w / sum) else 0
			out[index] = value
			given += value
		end
	end
	return out
end

local planCache: { [string]: LevelPlan } = {}

-- The full content of a level. Deterministic: the same (level, mode) always
-- gives the same plan (positions are chosen by the server at run time).
--   * "threat budget" (sum of base hp x base damage) grows 8% per level, then
--     every enemy's HP x1.18^(L-1) and damage x1.10^(L-1) -> later levels are
--     strictly harder (tests/combat.spec.luau checks this).
--   * More dangerous types unlock and get more common as levels rise.
--   * Boss levels: the Officer gets BOSS_SHARE of the budget as HP, on the last wave.
--   * Cash: 70% of Economy.CombatLevelReward(level) dropped by enemies
--     (by cashWeight), 30% as ground piles (PILES_PER_WAVE per wave).
function CombatDefs.LevelPlan(level: number, mode: string): LevelPlan
	local lvl = sanitizeLevel(level)
	local m = if mode == "barrier" then "barrier" else "arena"
	local key = m .. ":" .. lvl
	local cached = planCache[key]
	if cached then
		return cached
	end

	local rng = Rng.new(lvl * 7919 + (if m == "barrier" then 104729 else 0))
	local waveCount = CombatDefs.WaveCount(lvl, m)
	local hasBoss = CombatDefs.HasBoss(lvl, m)
	local budget = CombatDefs.BASE_BUDGET * CombatDefs.BUDGET_GROWTH ^ (lvl - 1)
	local regularBudget = if hasBoss then budget * (1 - CombatDefs.BOSS_SHARE) else budget
	local maxRegular = waveCount * CombatDefs.MAX_ENEMIES_PER_WAVE

	-- 1) pick regular enemy types until the budget is used up
	local pool: { EnemyDef } = {}
	for _, id in CombatDefs.ENEMY_ORDER do
		local def = CombatDefs.ENEMIES[id]
		if spawnWeight(def, lvl) > 0 then
			table.insert(pool, def)
		end
	end
	local picked: { EnemyDef } = {}
	local placed = 0
	while placed < regularBudget and #picked < maxRegular do
		local def = rng:Weighted(pool, function(d: EnemyDef): number
			return spawnWeight(d, lvl)
		end)
		table.insert(picked, def)
		placed += baseThreat(def)
	end
	-- If the per-wave cap stopped us early, the enemies we have become tougher
	-- ("elite") so difficulty still keeps rising. [PROPOSAL]
	local elite = math.max(1, regularBudget / math.max(1, placed))

	-- 2) deal them into waves: later waves get more enemies (weights 2, 3, 4, ...)
	local n = #picked
	local weightSum = 0
	for w = 1, waveCount do
		weightSum += w + 1
	end
	local counts: { number } = {}
	local cumulative = 0
	local dealt = 0
	for w = 1, waveCount do
		cumulative += w + 1
		local upTo = if w == waveCount then n else math.floor(n * cumulative / weightSum + 0.5)
		counts[w] = math.max(0, upTo - dealt)
		dealt = upTo
	end
	-- make sure every wave has a few enemies (2 when there are enough) and none is over the cap
	local minimum = math.max(1, math.min(2, math.floor(n / waveCount)))
	for w = 1, waveCount do
		while counts[w] < minimum do
			local donor, most = 0, minimum
			for other = 1, waveCount do
				if counts[other] > most then
					donor, most = other, counts[other]
				end
			end
			if donor == 0 then
				break
			end
			counts[donor] -= 1
			counts[w] += 1
		end
	end
	for w = waveCount, 1, -1 do
		while counts[w] > CombatDefs.MAX_ENEMIES_PER_WAVE do
			local moved = false
			for other = 1, waveCount do
				if counts[other] < CombatDefs.MAX_ENEMIES_PER_WAVE then
					counts[other] += 1
					counts[w] -= 1
					moved = true
					break
				end
			end
			if not moved then
				break
			end
		end
	end

	local hpScale = CombatDefs.HpScale(lvl)
	local damageScale = CombatDefs.DamageScale(lvl)
	local waves: { PlanWave } = {}
	local all: { PlanEnemy } = {}
	local index = 0
	for w = 1, waveCount do
		local list: { PlanEnemy } = {}
		for _ = 1, counts[w] do
			index += 1
			local def = picked[index]
			if def then
				local e: PlanEnemy = {
					type = def.id,
					hp = math.max(1, math.floor(def.hp * elite * hpScale + 0.5)),
					damage = math.max(1, math.floor(def.damage * damageScale * 10 + 0.5) / 10),
					cash = 0,
					boss = false,
				}
				table.insert(list, e)
				table.insert(all, e)
			end
		end
		waves[w] = { enemies = list, piles = {} }
	end

	-- 3) the Officer boss on the last wave
	if hasBoss then
		local officer = CombatDefs.ENEMIES.Officer
		local bossBaseHp = math.max(CombatDefs.BOSS_MIN_BASE_HP, budget * CombatDefs.BOSS_SHARE / officer.damage)
		local boss: PlanEnemy = {
			type = officer.id,
			hp = math.max(1, math.floor(bossBaseHp * hpScale + 0.5)),
			damage = math.max(1, math.floor(officer.damage * damageScale * 10 + 0.5) / 10),
			cash = 0,
			boss = true,
		}
		table.insert(waves[waveCount].enemies, boss)
		table.insert(all, boss)
	end

	-- 4) cash: 70% dropped by enemies, 30% in ground piles
	local reward = Economy.CombatLevelReward(lvl)
	local enemyCash = Format.RoundCents(reward * CombatDefs.CASH_ENEMY_SHARE)
	local pileCash = Format.RoundCents(reward - enemyCash)
	local weights = {}
	for i, e in all do
		weights[i] = CombatDefs.ENEMIES[e.type].cashWeight
	end
	local enemyValues = splitCash(enemyCash, weights)
	for i, e in all do
		e.cash = enemyValues[i]
	end
	local pileWeights = {}
	for i = 1, waveCount * CombatDefs.PILES_PER_WAVE do
		pileWeights[i] = 1
	end
	local pileValues = splitCash(pileCash, pileWeights)
	for i, value in pileValues do
		local w = math.floor((i - 1) / CombatDefs.PILES_PER_WAVE) + 1
		table.insert(waves[w].piles, value)
	end

	local totalHp = 0
	for _, e in all do
		totalHp += e.hp
	end
	local plan: LevelPlan = {
		level = lvl,
		mode = m,
		waveCount = waveCount,
		waves = waves,
		hasBoss = hasBoss,
		reward = reward,
		enemyCash = enemyCash,
		pileCash = pileCash,
		enemyCount = #all,
		totalHp = totalHp,
		difficulty = 0,
	}
	plan.difficulty = CombatDefs.Difficulty(plan)
	planCache[key] = plan
	return plan
end

-- Difficulty of a plan = sum over every enemy of (hp x damage).
function CombatDefs.Difficulty(plan: LevelPlan): number
	local total = 0
	for _, wave in plan.waves do
		for _, e in wave.enemies do
			total += e.hp * e.damage
		end
	end
	return total
end

-- 1..5 skulls shown in the level menu. [PROPOSAL]
function CombatDefs.DifficultySkulls(level: number): number
	return math.clamp(1 + math.floor((sanitizeLevel(level) - 1) / 4), 1, 5)
end

-------------------------------------------------------------------------------
-- In-run abilities (Archero-style pick 1 of 3 after each cleared wave)
-- [REQUIREMENT] they last ONLY for the current attempt. Lasting progression is
-- equipment only (GEAR). [PROPOSAL] the list and numbers.
-------------------------------------------------------------------------------
export type AbilityEffect = { kind: string, perk: string?, amount: number? }
-- kind: "perk" (adds 1 stack of a §4.5 perk) | "damage" (+amount x damage)
--       | "maxhp" (+amount x max HP, heals the gain) | "heal" (heals amount x max HP)
export type Ability = {
	id: string,
	name: string,
	icon: string,
	description: string,
	maxStacks: number,
	weight: number,
	effect: AbilityEffect,
}

local function ability(
	id: string,
	name: string,
	icon: string,
	description: string,
	maxStacks: number,
	weight: number,
	effect: AbilityEffect
): Ability
	return {
		id = id,
		name = name,
		icon = icon,
		description = description,
		maxStacks = maxStacks,
		weight = weight,
		effect = effect,
	}
end

CombatDefs.ABILITIES = {
	ability("multishot", "Multishot", "🔱", "+1 bullet every shot", 3, 8, { kind = "perk", perk = "multishot" }),
	ability(
		"pierce",
		"Pierce",
		"🗡️",
		"Bullets pass through 1 more enemy",
		3,
		7,
		{ kind = "perk", perk = "pierce" }
	),
	ability(
		"ricochet",
		"Ricochet",
		"🔁",
		"Bullets bounce to 1 more enemy",
		3,
		7,
		{ kind = "perk", perk = "ricochet" }
	),
	ability("rapid", "Attack Speed+", "⚡", "+15% fire rate", 5, 10, { kind = "perk", perk = "rapid" }),
	ability("attack", "Attack+", "💥", "+20% damage", 5, 10, { kind = "damage", amount = 0.2 }),
	ability("crit", "Crit+", "🎯", "+10% critical hit chance", 4, 8, { kind = "perk", perk = "crit" }),
	ability("maxhp", "Max HP+", "❤️", "+20% max HP (and heals that much)", 5, 8, { kind = "maxhp", amount = 0.2 }),
	ability("heal", "Heal", "🩹", "Restore 40% of your max HP", 99, 6, { kind = "heal", amount = 0.4 }),
	ability(
		"shield",
		"Shield",
		"🛡️",
		"Start every wave with a shield that blocks 1 hit",
		2,
		6,
		{ kind = "perk", perk = "shield" }
	),
	ability("dodge", "Dodge", "💨", "+10% chance to dodge a hit (max 40%)", 4, 7, { kind = "perk", perk = "dodge" }),
	ability("swift", "Swift", "👟", "+8% move speed", 3, 6, { kind = "perk", perk = "swift" }),
	ability("vampire", "Vampire", "🧛", "Heal 2% max HP for every kill", 3, 6, { kind = "perk", perk = "vampire" }),
	ability(
		"explosive",
		"Explosive",
		"💣",
		"Bullets splash 30% damage around the target",
		2,
		6,
		{ kind = "perk", perk = "explosive" }
	),
	ability("regen", "Regen", "🌿", "Heal 1% max HP every second", 3, 6, { kind = "perk", perk = "regen" }),
	ability(
		"thorns",
		"Thorns",
		"🌵",
		"Melee attackers take 50% of their damage back",
		2,
		5,
		{ kind = "perk", perk = "thorns" }
	),
} :: { Ability }

CombatDefs.AbilityById = {} :: { [string]: Ability }
for _, a in CombatDefs.ABILITIES do
	CombatDefs.AbilityById[a.id] = a
end

-- Pick `count` (default 3) different abilities that are not yet at maxStacks.
function CombatDefs.RollAbilities(stacks: { [string]: number }, rng: Rng.Rng, count: number?): { string }
	local want = count or 3
	local pool: { Ability } = {}
	for _, a in CombatDefs.ABILITIES do
		if (stacks[a.id] or 0) < a.maxStacks then
			table.insert(pool, a)
		end
	end
	local out: { string } = {}
	while #out < want and #pool > 0 do
		local chosen, index = rng:Weighted(pool, function(a: Ability): number
			return a.weight
		end)
		table.insert(out, chosen.id)
		table.remove(pool, index)
	end
	return out
end

-------------------------------------------------------------------------------
-- Perks (ARCHITECTURE §4.5) and the stats used during one attempt
-------------------------------------------------------------------------------
CombatDefs.PERK_IDS = {
	"multishot",
	"pierce",
	"ricochet",
	"crit",
	"rapid",
	"explosive",
	"regen",
	"shield",
	"dodge",
	"thorns",
	"swift",
	"vampire",
}

CombatDefs.EXPLOSIVE_RADIUS = 6
CombatDefs.RICOCHET_RANGE = 26 -- [PROPOSAL] how far a bullet can bounce

export type RunStats = {
	maxHp: number,
	damage: number,
	fireRate: number,
	projectiles: number,
	spreadDeg: number,
	projectileSpeed: number,
	range: number,
	critChance: number,
	critMult: number,
	damageReduction: number,
	moveSpeed: number,
	pierce: number, -- extra enemies a bullet passes through
	ricochet: number, -- extra bounces
	explosive: number, -- splash damage fraction (0.3 per stack)
	regen: number, -- fraction of max HP healed per second
	shieldPerWave: number, -- shield hits at the start of every wave
	dodge: number, -- chance to ignore a hit (max 0.4)
	thorns: number, -- fraction of melee damage reflected
	vampire: number, -- fraction of max HP healed per kill
	perks: { [string]: number }, -- combined perk stacks (equipment + abilities)
}

local function finite(value: any, fallback: number, min: number, max: number): number
	if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
		return fallback
	end
	return math.clamp(value, min, max)
end

-- Equipment stats (GEAR's CombatStats, already including item perks) +
-- in-run ability stacks -> the numbers the simulation uses.
-- Any missing / broken value from equipment falls back to a safe default.
function CombatDefs.DeriveStats(base: CombatStats, abilityStacks: { [string]: number }): RunStats
	local perks: { [string]: number } = {}
	if type(base.perks) == "table" then
		for id, stacks in base.perks do
			if type(id) == "string" and type(stacks) == "number" and stacks > 0 then
				perks[id] = (perks[id] or 0) + math.floor(stacks)
			end
		end
	end
	local damageBonus = 0
	local hpBonus = 0
	for id, stacks in abilityStacks do
		local a = CombatDefs.AbilityById[id]
		if a and stacks > 0 then
			local effect = a.effect
			if effect.kind == "perk" and effect.perk then
				perks[effect.perk] = (perks[effect.perk] or 0) + stacks
			elseif effect.kind == "damage" then
				damageBonus += (effect.amount or 0) * stacks
			elseif effect.kind == "maxhp" then
				hpBonus += (effect.amount or 0) * stacks
			end
		end
	end
	local function p(id: string): number
		return perks[id] or 0
	end
	return {
		maxHp = finite(base.maxHp, 100, 1, 1e12) * (1 + hpBonus),
		damage = finite(base.damage, 10, 0.1, 1e12) * (1 + damageBonus),
		fireRate = finite(base.fireRate, 2, 0.2, 20) * (1 + 0.15 * p("rapid")),
		projectiles = math.floor(finite(base.projectiles, 1, 1, 9)) + p("multishot"),
		spreadDeg = finite(base.spreadDeg, 0, 0, 90),
		projectileSpeed = finite(base.projectileSpeed, 70, 20, 200),
		range = finite(base.range, 60, 10, 140),
		critChance = math.min(1, finite(base.critChance, 0.05, 0, 1) + 0.1 * p("crit")),
		critMult = finite(base.critMult, 2, 1, 10),
		damageReduction = finite(base.damageReduction, 0, 0, 0.6),
		moveSpeed = finite(base.moveSpeed, 20, 8, 40) * (1 + 0.08 * p("swift")),
		pierce = p("pierce"),
		ricochet = p("ricochet"),
		explosive = 0.3 * p("explosive"),
		regen = 0.01 * p("regen"),
		shieldPerWave = p("shield"),
		dodge = math.min(0.4, 0.1 * p("dodge")),
		thorns = 0.5 * p("thorns"),
		vampire = 0.02 * p("vampire"),
		perks = perks,
	}
end

-- Safe default equipment stats (used if GEAR's InventoryService is missing).
function CombatDefs.DefaultStats(): CombatStats
	return {
		maxHp = 100,
		damage = 10,
		fireRate = 2,
		projectiles = 1,
		spreadDeg = 0,
		projectileSpeed = 70,
		range = 60,
		critChance = 0.05,
		critMult = 2,
		damageReduction = 0,
		moveSpeed = 20,
		perks = {},
	}
end

-------------------------------------------------------------------------------
-- Chests  [REQUIREMENT: a chest at the end of every combat game]
-------------------------------------------------------------------------------
-- gold: barrier wins and every 5th arena level won; silver: other wins;
-- wood: defeats, but only if at least one enemy was killed ([PROPOSAL]: stops
-- free chests from starting and instantly leaving).
function CombatDefs.ChestTier(mode: string, level: number, victory: boolean, kills: number): string?
	if victory then
		if mode == "barrier" or sanitizeLevel(level) % 5 == 0 then
			return "gold"
		end
		return "silver"
	end
	if kills > 0 then
		return "wood"
	end
	return nil
end

-------------------------------------------------------------------------------
-- Snapshot packing (CombatSnap, 20 Hz, unreliable)
-------------------------------------------------------------------------------
export type SnapEnemy = { id: number, type: string, x: number, z: number, hp: number, maxHp: number, state: string }
export type SnapPickup = { id: number, x: number, z: number, value: number }
export type Snap = { t: number, cash: number, enemies: { SnapEnemy }, pickups: { SnapPickup } }

CombatDefs.SNAP_MAX_ENEMIES = 24
CombatDefs.SNAP_MAX_PICKUPS = 32
local POS_SCALE = 50 -- positions are sent in 1/50 stud steps
local HEADER = 18
local ENEMY_BYTES = 16
local PICKUP_BYTES = 10

local function packPos(v: number): number
	return math.clamp(math.floor(v * POS_SCALE + 0.5), -32768, 32767)
end

-- Turns a snapshot into a small buffer (at most ~720 bytes).
-- Extra enemies / pickups beyond the caps are left out.
function CombatDefs.EncodeSnap(snap: Snap): buffer
	local ne = math.min(#snap.enemies, CombatDefs.SNAP_MAX_ENEMIES)
	local np = math.min(#snap.pickups, CombatDefs.SNAP_MAX_PICKUPS)
	local b = buffer.create(HEADER + ne * ENEMY_BYTES + np * PICKUP_BYTES)
	buffer.writef64(b, 0, snap.t)
	buffer.writef64(b, 8, snap.cash)
	buffer.writeu8(b, 16, ne)
	buffer.writeu8(b, 17, np)
	local o = HEADER
	for i = 1, ne do
		local e = snap.enemies[i]
		buffer.writeu16(b, o, e.id % 65536)
		buffer.writeu8(b, o + 2, CombatDefs.TypeCode(e.type))
		buffer.writeu8(b, o + 3, CombatDefs.StateCode(e.state))
		buffer.writei16(b, o + 4, packPos(e.x))
		buffer.writei16(b, o + 6, packPos(e.z))
		buffer.writef32(b, o + 8, e.hp)
		buffer.writef32(b, o + 12, e.maxHp)
		o += ENEMY_BYTES
	end
	for i = 1, np do
		local p = snap.pickups[i]
		buffer.writeu16(b, o, p.id % 65536)
		buffer.writei16(b, o + 2, packPos(p.x))
		buffer.writei16(b, o + 4, packPos(p.z))
		buffer.writef32(b, o + 6, p.value)
		o += PICKUP_BYTES
	end
	return b
end

-- Reverse of EncodeSnap. Returns nil for anything that is not a valid snapshot.
function CombatDefs.DecodeSnap(b: any): Snap?
	if type(b) ~= "buffer" then
		return nil
	end
	local len = buffer.len(b)
	if len < HEADER then
		return nil
	end
	local ne = buffer.readu8(b, 16)
	local np = buffer.readu8(b, 17)
	if len < HEADER + ne * ENEMY_BYTES + np * PICKUP_BYTES then
		return nil
	end
	local snap: Snap = { t = buffer.readf64(b, 0), cash = buffer.readf64(b, 8), enemies = {}, pickups = {} }
	local o = HEADER
	for _ = 1, ne do
		table.insert(snap.enemies, {
			id = buffer.readu16(b, o),
			type = CombatDefs.TypeName(buffer.readu8(b, o + 2)),
			state = CombatDefs.StateName(buffer.readu8(b, o + 3)),
			x = buffer.readi16(b, o + 4) / POS_SCALE,
			z = buffer.readi16(b, o + 6) / POS_SCALE,
			hp = buffer.readf32(b, o + 8),
			maxHp = buffer.readf32(b, o + 12),
		})
		o += ENEMY_BYTES
	end
	for _ = 1, np do
		table.insert(snap.pickups, {
			id = buffer.readu16(b, o),
			x = buffer.readi16(b, o + 2) / POS_SCALE,
			z = buffer.readi16(b, o + 4) / POS_SCALE,
			value = buffer.readf32(b, o + 6),
		})
		o += PICKUP_BYTES
	end
	return snap
end

return CombatDefs
