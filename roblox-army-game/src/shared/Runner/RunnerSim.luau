--!strict
--[[
	RunnerSim
	Scores an army-runner course: given the lane picked in every row it works
	out the troops, weapon, damage, cash, enemies and the boss fight of the run
	and builds the completion summary lines. Pure and deterministic (no
	services, no random), so the server can re-simulate a run from the client's
	lane choices, the client can animate exactly what the server will pay, and
	the economy can compute the best possible run.

	Public API:
		RunnerSim.Resolve(course, stats, choices): Result
			choices[i] = lane picked in row i (1..#row.options). A missing,
			non-integer or out-of-range lane counts as lane 1.
		RunnerSim.BestCase(course, stats): (Result, { number })
			EXACT best run and the lane choices that produce it.
		RunnerSim.MinDuration(course): number
			fastest legal completion time in seconds (running only, anti-cheat floor).
		RunnerSim.UnitDps(stats, weaponTier, damageMult): number
			damage per second of ONE unit (the avatar or a troop: all fire the same gun)
			= WEAPONS[tier].damage x WEAPONS[tier].fireRate
			  x (stats.damage / EconomyDefs.Runner.BaseDamage) x damageMult
		RunnerSim.BossHp(course, stats): number
			boss max HP = BOSS_HP_SHARE x the most damage ANY lane path could deal
			to a boss that never dies (memoized per course table + stats)
		RunnerSim.BossFight(units, unitDps, bossHp): BossOutcome   exact closed form
		RunnerSim.BossHpAt(outcome, t): number     boss HP t seconds into the fight
		RunnerSim.AliveAt(outcome, t): number      units alive t seconds into the fight
		RunnerSim.BossDamagePotential(units, unitDps): number
			damage a squad deals if the boss never dies: unitDps x interval x N(N+1)/2
		RunnerSim.LastSearch: { states: number, maxFrontier: number, capHit: boolean }
			statistics of the most recent BestCase call (tests check capHit).
		RunnerSim.STATE_CAP: number

	Rules (all numbers are in RunnerDefs):
		State during a run: troops (>= 1, the avatar always survives the rows),
		weaponTier (1 Pistol .. 6 Rocket), damageMult (in-run damage gates,
		starts at 1), carried cash (capped by stats.capacity) and banked value.
		Gate "+N" adds N troops, "xN" multiplies, "-N" subtracts, "÷N" divides
		(rounded down). Troops never drop below 1 on the track.
		Gold weapon gate: +1 weapon tier and banks that weapon's cash value
		(at the top tier it still banks the Rocket value).
		Gold damage gate: damageMult +0.2 and banks DAMAGE_GATE_VALUE.
		Cash stack: added to carried cash up to stats.capacity; the rest is
		lost and reported ("bag full"). [REQUIREMENT: coin-carrying capacity]
		Squad: the squad shoots it for ENGAGE_SECONDS (guns reach 1.5 rows):
		damage = troops x UnitDps x ENGAGE_SECONDS, kills floor(damage / soldier
		hp) soldiers (reward each); every surviving soldier costs 1 troop.
		Barricade: destroyed if troops x UnitDps x ENGAGE_SECONDS >= hp (reward
		banked), otherwise you lose 25% of your troops (min 1 left).
		Boss at the castle: every BossKillInterval(N) seconds it kills one unit
		(troops first, the avatar last) while all living units shoot it. Its HP
		is BossHp(course, stats), so only (near) best lane paths beat it.
		Victory: the boss reward is paid and the units still alive count as
		troops at the finish. Defeat: no boss reward and 0 troops at the finish.
		Payout = survivors x troop value + carried cash + banked upgrade values
		+ enemy rewards + boss reward, then + payoutBonus. Whole dollars.

	Boss fight closed form: with N units, unit DPS u and interval I, during
	kill interval k (time (k-1)I .. kI) N-(k-1) units are alive, so the damage
	by the end of interval k is D(k) = u I (kN - k(k-1)/2). The boss dies in
	the first interval k with D(k) >= hp; if even D(N) = u I N(N+1)/2 < hp,
	every unit dies and the boss survives.

	Why BestCase is exact: the final payout never goes down when any of
	(troops, weaponTier, damageMult, carried, banked) goes up (the boss damage
	potential, victory and the survivors all rise with troops and unit DPS),
	and every row rule keeps that order. So a partial run that is <= another
	one in all five numbers can never finish ahead of it, and dropping it
	(Pareto pruning) never loses the optimum. The same argument with only
	(troops, weaponTier, damageMult) makes BossHp's search exact.
]]

local Shared = script.Parent.Parent
local EconomyDefs = require(Shared:WaitForChild("EconomyDefs"))
local Format = require(Shared:WaitForChild("Format"))
local RunnerCourse = require(script.Parent:WaitForChild("RunnerCourse"))
local RunnerDefs = require(script.Parent:WaitForChild("RunnerDefs"))

export type Stats = { startTroops: number, damage: number, capacity: number, payoutBonus: number? }

export type RowOutcome = {
	row: number,
	lane: number,
	kind: string,
	label: string,
	troopsBefore: number,
	troopsAfter: number,
	cashBefore: number,
	cashAfter: number,
	weaponTier: number,
	damageMult: number,
	destroyed: boolean?,
	note: string?,
}

export type Line = { kind: string, label: string, detail: string, value: number }

export type BossOutcome = {
	hp: number, -- boss max HP for this course + stats
	victory: boolean,
	units: number, -- units that reached the castle (troops incl. the avatar)
	unitDps: number,
	interval: number, -- seconds between boss kills
	killed: number, -- units the boss killed (troops first, the avatar last; = units on defeat)
	survivors: number, -- units alive at the end (0 on defeat)
	duration: number, -- seconds the fight lasts (boss death time on victory, units x interval on defeat)
	damageDealt: number, -- damage the squad dealt to the boss
	hpLeft: number, -- boss HP left (0 on victory)
}

export type Result = {
	troops: number, -- units alive AFTER the boss fight (0 on defeat)
	weaponTier: number,
	damageMult: number,
	collected: number,
	carried: number,
	outcomes: { RowOutcome },
	lines: { Line },
	total: number,
	boss: BossOutcome,
	unitDps: number, -- DPS of one unit at the castle
}

type Course = RunnerCourse.Course
type Row = RunnerCourse.Row
type Option = RunnerCourse.Option

-- Clean copy of the stats with safe values.
type CleanStats = { startTroops: number, damage: number, capacity: number, payoutBonus: number }

-- Result of walking through one option of one row.
type Step = {
	troops: number,
	tier: number,
	dmg: number,
	carried: number,
	bankDelta: number,
	collected: number, -- cash touched (before the capacity cap)
	overflow: number, -- cash lost because the bag was full
	kills: number,
	killReward: number,
	destroyed: boolean?,
	barricadeReward: number,
	upgradeKind: string?, -- "weapon" | "damage"
	upgradeValue: number,
	note: string?,
}

-- Search node for BestCase.
type Node = {
	troops: number,
	tier: number,
	dmg: number,
	carried: number,
	bank: number,
	lane: number,
	parent: Node?,
}

local RunnerSim = {}

-- [PROPOSAL] Upper bound of partial runs kept per row by BestCase. Real
-- courses stay far below this (tests assert the cap is never hit).
RunnerSim.STATE_CAP = 200000
RunnerSim.LastSearch = { states = 0, maxFrontier = 0, capHit = false }

local EPS = 1e-9
local round = Format.RoundWhole

local function finite(n: any, fallback: number): number
	if type(n) ~= "number" or n ~= n or n == math.huge or n == -math.huge then
		return fallback
	end
	return n
end

-- 12345 -> "12,345" (whole numbers for HP texts).
local function commas(n: number): string
	local s = tostring(math.floor(math.abs(n) + 0.5))
	local out = s:reverse():gsub("(%d%d%d)", "%1,"):reverse()
	if out:sub(1, 1) == "," then
		out = out:sub(2)
	end
	return out
end

local function cleanStats(stats: Stats): CleanStats
	local s: any = if type(stats) == "table" then stats else {}
	return {
		startTroops = math.max(1, math.floor(finite(s.startTroops, 1))),
		damage = math.max(0, finite(s.damage, 0)),
		-- Rounded DOWN so carried cash never exceeds the real capacity.
		capacity = math.floor(math.max(0, finite(s.capacity, 0)) + EPS),
		payoutBonus = math.clamp(finite(s.payoutBonus, 0), 0, 10),
	}
end

local function clampTier(weaponTier: number): number
	return math.clamp(math.floor(finite(weaponTier, 1)), 1, RunnerDefs.MAX_WEAPON_TIER)
end

-- UnitDps on already-clean numbers.
local function unitDps(damage: number, tier: number, dmg: number): number
	local w = RunnerDefs.WEAPONS[clampTier(tier)]
	return w.damage * w.fireRate * (damage / EconomyDefs.Runner.BaseDamage) * dmg
end

function RunnerSim.UnitDps(stats: Stats, weaponTier: number, damageMult: number): number
	local st = cleanStats(stats)
	return unitDps(st.damage, weaponTier, math.max(0, finite(damageMult, 1)))
end

-------------------------------------------------------------------------------
-- The boss fight (exact closed form)
-------------------------------------------------------------------------------
-- Damage dealt by the end of kill interval k: u I (kN - k(k-1)/2).
local function damageBy(units: number, u: number, interval: number, k: number): number
	return u * interval * (k * units - k * (k - 1) / 2)
end

function RunnerSim.BossDamagePotential(units: number, dps: number): number
	local n = math.max(0, math.floor(finite(units, 0)))
	local u = math.max(0, finite(dps, 0))
	return damageBy(n, u, RunnerDefs.BossKillInterval(n), n)
end

function RunnerSim.BossFight(units: number, dps: number, bossHp: number): BossOutcome
	local n = math.max(0, math.floor(finite(units, 0)))
	local u = math.max(0, finite(dps, 0))
	local hp = math.max(0, finite(bossHp, 0))
	local interval = RunnerDefs.BossKillInterval(n)
	local potential = damageBy(n, u, interval, n)

	if hp <= 0 then
		-- Nothing to beat (only hand-made courses): instant victory.
		return {
			hp = hp,
			victory = true,
			units = n,
			unitDps = u,
			interval = interval,
			killed = 0,
			survivors = n,
			duration = 0,
			damageDealt = 0,
			hpLeft = 0,
		}
	end
	if n <= 0 or u <= 0 or potential < hp then
		return {
			hp = hp,
			victory = false,
			units = n,
			unitDps = u,
			interval = interval,
			killed = n,
			survivors = 0,
			duration = n * interval,
			damageDealt = potential,
			hpLeft = hp - potential,
		}
	end

	-- First interval k with damageBy(k) >= hp: solve k^2 - (2N+1)k + 2h <= 0
	-- (h = hp / (u I)), then fix float noise by stepping.
	local h = hp / (u * interval)
	local b = 2 * n + 1
	local disc = math.max(0, b * b - 8 * h)
	local k = math.clamp(math.ceil((b - math.sqrt(disc)) / 2), 1, n)
	while k > 1 and damageBy(n, u, interval, k - 1) >= hp do
		k -= 1
	end
	while k < n and damageBy(n, u, interval, k) < hp do
		k += 1
	end
	local before = damageBy(n, u, interval, k - 1)
	local alive = n - (k - 1)
	local t = (k - 1) * interval + math.clamp((hp - before) / (u * alive), 0, interval)
	return {
		hp = hp,
		victory = true,
		units = n,
		unitDps = u,
		interval = interval,
		killed = k - 1,
		survivors = alive,
		duration = t,
		damageDealt = hp,
		hpLeft = 0,
	}
end

-- Boss HP `t` seconds into the fight described by `outcome`.
function RunnerSim.BossHpAt(outcome: BossOutcome, t: number): number
	local time = finite(t, 0)
	if time >= outcome.duration then
		return outcome.hpLeft
	elseif time <= 0 then
		return outcome.hp
	end
	local n, u, interval = outcome.units, outcome.unitDps, outcome.interval
	local k = math.clamp(math.floor(time / interval) + 1, 1, math.max(1, n))
	local dealt = damageBy(n, u, interval, k - 1) + u * (n - (k - 1)) * (time - (k - 1) * interval)
	return math.max(outcome.hpLeft, outcome.hp - dealt)
end

-- Units alive `t` seconds into the fight (a kill lands at the END of each interval).
function RunnerSim.AliveAt(outcome: BossOutcome, t: number): number
	local time = finite(t, 0)
	if time >= outcome.duration then
		return outcome.survivors
	elseif time <= 0 then
		return outcome.units
	end
	local killed = math.min(outcome.killed, math.floor(time / outcome.interval + EPS))
	return outcome.units - killed
end

-------------------------------------------------------------------------------
-- Rows
-------------------------------------------------------------------------------
-- Lane actually used in a row (invalid / missing -> 1).
local function laneFor(row: Row, choice: any): number
	if type(choice) == "number" and choice == math.floor(choice) and choice >= 1 and choice <= #row.options then
		return choice
	end
	return 1
end

-- Walk through one option. Pure: returns the new state numbers + what happened.
local function step(
	row: Row,
	opt: Option,
	stats: CleanStats,
	scale: number,
	troops: number,
	tier: number,
	dmg: number,
	carried: number
): Step
	local s: Step = {
		troops = troops,
		tier = tier,
		dmg = dmg,
		carried = carried,
		bankDelta = 0,
		collected = 0,
		overflow = 0,
		kills = 0,
		killReward = 0,
		destroyed = nil,
		barricadeReward = 0,
		upgradeKind = nil,
		upgradeValue = 0,
		note = nil,
	}
	local amount = finite(opt.amount, 0)

	-- 1) Troop maths / gold gates
	local op = opt.op
	if op == "add" then
		s.troops = troops + math.max(0, math.floor(amount))
	elseif op == "mul" then
		s.troops = troops * math.max(1, math.floor(amount))
	elseif op == "sub" then
		s.troops = math.max(1, troops - math.max(0, math.floor(amount)))
	elseif op == "div" then
		s.troops = math.max(1, math.floor(troops / math.max(1, amount)))
	elseif op == "weapon" then
		s.tier = math.min(RunnerDefs.MAX_WEAPON_TIER, tier + math.max(1, math.floor(amount)))
		s.upgradeKind = "weapon"
		s.upgradeValue = round(RunnerDefs.WEAPONS[s.tier].value * scale)
		s.note = if tier >= RunnerDefs.MAX_WEAPON_TIER
			then "Weapon already maxed: bonus cash"
			else RunnerDefs.WEAPONS[s.tier].name .. "!"
	elseif op == "damage" then
		s.dmg = dmg + math.max(0, amount)
		s.upgradeKind = "damage"
		s.upgradeValue = round(RunnerDefs.DAMAGE_GATE_VALUE * scale)
		s.note = "+" .. math.floor(amount * 100 + 0.5) .. "% damage"
	end

	-- 2) Cash stack in this lane (cash rows and red toll gates), whole dollars
	local cash = math.max(0, round(finite(opt.cash, 0)))
	if cash > 0 then
		local room = math.max(0, stats.capacity - s.carried)
		local take = math.min(room, cash)
		s.collected = cash
		s.overflow = cash - take
		s.carried = s.carried + take
		if s.overflow > 0 then
			s.note = "Bag full: " .. Format.Money(s.overflow) .. " lost"
		end
	end

	-- 3) Fights: the squad shoots for ENGAGE_SECONDS before it arrives
	if row.kind == "squad" and opt.enemies then
		local enemies = math.max(0, math.floor(finite(opt.enemies, 0)))
		local hp = math.max(EPS, finite(opt.hp, 1))
		local damage = s.troops * unitDps(stats.damage, s.tier, s.dmg) * RunnerDefs.ENGAGE_SECONDS
		local kills = math.min(enemies, math.floor(damage / hp + EPS))
		local survivors = enemies - kills
		local before = s.troops
		s.troops = math.max(1, s.troops - survivors)
		s.kills = kills
		s.killReward = kills * math.max(0, round(finite(opt.reward, 0)))
		s.note = "Defeated " .. kills .. " of " .. enemies .. " soldiers, lost " .. (before - s.troops) .. " troops"
	elseif row.kind == "barricade" and opt.hp then
		local hp = math.max(0, finite(opt.hp, 0))
		local damage = s.troops * unitDps(stats.damage, s.tier, s.dmg) * RunnerDefs.ENGAGE_SECONDS
		if damage + EPS >= hp then
			s.destroyed = true
			s.barricadeReward = math.max(0, round(finite(opt.reward, 0)))
			s.note = "Barricade destroyed! +" .. Format.Money(s.barricadeReward)
		else
			local before = s.troops
			s.destroyed = false
			s.troops = math.max(1, math.floor(s.troops * (1 - RunnerDefs.BARRICADE_TROOP_LOSS)))
			s.note = "Barricade too strong: lost " .. (before - s.troops) .. " troops"
		end
	end

	s.bankDelta = s.upgradeValue + s.killReward + s.barricadeReward
	return s
end

-- `a` is at least as good as `b` in every dimension. Bank / carried are whole
-- dollars, so a 0.001 tolerance only absorbs floating-point noise.
local function dominates(a: Node, b: Node, troopsOnly: boolean): boolean
	return a.troops >= b.troops
		and a.tier >= b.tier
		and a.dmg >= b.dmg - EPS
		and (troopsOnly or (a.carried >= b.carried - 0.001 and a.bank >= b.bank - 0.001))
end

-- Sort order for pruning: biggest troops first, then the other dimensions.
local function nodeBefore(a: Node, b: Node): boolean
	if a.troops ~= b.troops then
		return a.troops > b.troops
	elseif a.tier ~= b.tier then
		return a.tier > b.tier
	elseif math.abs(a.dmg - b.dmg) > EPS then
		return a.dmg > b.dmg
	elseif math.abs(a.carried - b.carried) > EPS then
		return a.carried > b.carried
	end
	return a.bank > b.bank
end

local function prune(nodes: { Node }, troopsOnly: boolean): { Node }
	table.sort(nodes, nodeBefore)
	local kept: { Node } = {}
	for _, node in nodes do
		local dominated = false
		for _, k in kept do
			if dominates(k, node, troopsOnly) then
				dominated = true
				break
			end
		end
		if not dominated then
			table.insert(kept, node)
		end
	end
	return kept
end

-- Exact Pareto search over the whole course. troopsOnly = true ignores cash
-- and bank (enough for the boss damage maximum). Returns the final frontier.
local function search(
	course: Course,
	st: CleanStats,
	scale: number,
	troopsOnly: boolean
): ({ Node }, number, number, boolean)
	local frontier: { Node } = {
		{ troops = st.startTroops, tier = 1, dmg = 1, carried = 0, bank = 0, lane = 0, parent = nil },
	}
	local visited, maxFrontier, capHit = 1, 1, false

	for _, row in course.rows do
		local nextNodes: { Node } = {}
		if #row.options == 0 then
			-- Keep the same states; the lane is recorded as 1 (what Resolve uses).
			for _, node in frontier do
				table.insert(nextNodes, {
					troops = node.troops,
					tier = node.tier,
					dmg = node.dmg,
					carried = node.carried,
					bank = node.bank,
					lane = 1,
					parent = node,
				})
			end
		else
			-- Merge exact duplicates (same troops/tier/damage/cash): keep the richer bank.
			local byKey: { [string]: Node } = {}
			for _, node in frontier do
				for lane, opt in row.options do
					local s = step(row, opt, st, scale, node.troops, node.tier, node.dmg, node.carried)
					local child: Node = {
						troops = s.troops,
						tier = s.tier,
						dmg = s.dmg,
						carried = if troopsOnly then 0 else s.carried,
						bank = if troopsOnly then 0 else node.bank + s.bankDelta,
						lane = lane,
						parent = node,
					}
					visited += 1
					local key = string.format("%d|%d|%.4f|%d", child.troops, child.tier, child.dmg, child.carried)
					local existing = byKey[key]
					if existing == nil then
						byKey[key] = child
						table.insert(nextNodes, child)
					elseif child.bank > existing.bank + 0.001 then
						-- Replace in place so the array order stays deterministic.
						existing.bank = child.bank
						existing.lane = child.lane
						existing.parent = child.parent
					end
				end
			end
			nextNodes = prune(nextNodes, troopsOnly)
		end
		if #nextNodes > RunnerSim.STATE_CAP then
			capHit = true
			local trimmed: { Node } = table.create(RunnerSim.STATE_CAP)
			for i = 1, RunnerSim.STATE_CAP do
				trimmed[i] = nextNodes[i]
			end
			nextNodes = trimmed
		end
		maxFrontier = math.max(maxFrontier, #nextNodes)
		frontier = nextNodes
	end
	return frontier, visited, maxFrontier, capHit
end

-------------------------------------------------------------------------------
-- Boss HP (memoized per course table + the stats that matter)
-------------------------------------------------------------------------------
local bossHpMemo: { [any]: { [string]: number } } = setmetatable({}, { __mode = "k" }) :: any
local lastBossSearch = { states = 0, maxFrontier = 0, capHit = false }

local function bossHpClean(course: Course, st: CleanStats): number
	-- Capacity and the payout bonus never change troops or damage.
	local key = string.format("%d|%.6f", st.startTroops, st.damage)
	local perCourse = bossHpMemo[course]
	if perCourse == nil then
		perCourse = {}
		bossHpMemo[course] = perCourse
	end
	local cached = perCourse[key]
	if cached then
		return cached
	end
	local scale = RunnerCourse.ValueScale(course.area, course.level)
	local frontier, visited, maxFrontier, capHit = search(course, st, scale, true)
	local best = 0
	for _, node in frontier do
		best = math.max(best, RunnerSim.BossDamagePotential(node.troops, unitDps(st.damage, node.tier, node.dmg)))
	end
	lastBossSearch = { states = visited, maxFrontier = maxFrontier, capHit = capHit }
	-- Whole HP for the health bar; floor keeps it <= SHARE x best, so the best
	-- path always wins. With no damage at all the boss cannot be beaten.
	local hp = math.max(1, math.floor(RunnerDefs.BOSS_HP_SHARE * best))
	perCourse[key] = hp
	return hp
end

function RunnerSim.BossHp(course: Course, stats: Stats): number
	return bossHpClean(course, cleanStats(stats))
end

-------------------------------------------------------------------------------
-- Finish line
-------------------------------------------------------------------------------
type Finish = { boss: BossOutcome, dps: number, troopsValue: number, bossValue: number }

local function finish(course: Course, st: CleanStats, scale: number, troops: number, tier: number, dmg: number): Finish
	local dps = unitDps(st.damage, tier, dmg)
	local boss = RunnerSim.BossFight(troops, dps, bossHpClean(course, st))
	local troopsValue = if boss.victory then round(boss.survivors * RunnerDefs.TROOP_VALUE * scale) else 0
	local bossValue = if boss.victory then math.max(0, round(finite(course.boss.reward, 0))) else 0
	return { boss = boss, dps = dps, troopsValue = troopsValue, bossValue = bossValue }
end

local function withBonus(base: number, st: CleanStats): (number, number)
	local bonus = if st.payoutBonus > 0 then round(base * st.payoutBonus) else 0
	return base + bonus, bonus
end

-- Total payout of a finished state (same maths as the lines in Resolve).
local function totalOf(course: Course, st: CleanStats, scale: number, node: Node): number
	local f = finish(course, st, scale, node.troops, node.tier, node.dmg)
	local total = withBonus(node.bank + node.carried + f.troopsValue + f.bossValue, st)
	return total
end

function RunnerSim.Resolve(course: Course, stats: Stats, choices: { number }): Result
	local st = cleanStats(stats)
	local scale = RunnerCourse.ValueScale(course.area, course.level)
	local picks: { any } = if type(choices) == "table" then choices else {}

	local troops, tier, dmg, carried = st.startTroops, 1, 1, 0
	local collected = 0
	local overflowTotal = 0
	local kills, killValue = 0, 0
	local barricades, barricadeValue = 0, 0
	local outcomes: { RowOutcome } = {}
	local upgradeLines: { Line } = {}

	for index, row in course.rows do
		if #row.options == 0 then
			continue
		end
		local lane = laneFor(row, picks[index])
		local opt = row.options[lane]
		local s = step(row, opt, st, scale, troops, tier, dmg, carried)

		table.insert(outcomes, {
			row = index,
			lane = lane,
			kind = row.kind,
			label = opt.label,
			troopsBefore = troops,
			troopsAfter = s.troops,
			cashBefore = carried,
			cashAfter = s.carried,
			weaponTier = s.tier,
			damageMult = s.dmg,
			destroyed = s.destroyed,
			note = s.note,
		})

		if s.upgradeKind == "weapon" then
			local name = RunnerDefs.WEAPONS[s.tier].name
			table.insert(upgradeLines, {
				kind = "weapon",
				label = "Weapon: " .. name .. (if tier >= RunnerDefs.MAX_WEAPON_TIER then " (MAX)" else ""),
				detail = "Gold gate, row " .. index,
				value = s.upgradeValue,
			})
		elseif s.upgradeKind == "damage" then
			table.insert(upgradeLines, {
				kind = "damage",
				label = "Damage +" .. math.floor((s.dmg - dmg) * 100 + 0.5) .. "%",
				detail = "Gold gate, row " .. index .. " (now +" .. math.floor((s.dmg - 1) * 100 + 0.5) .. "%)",
				value = s.upgradeValue,
			})
		end

		collected += s.collected
		overflowTotal += s.overflow
		kills += s.kills
		killValue += s.killReward
		if s.destroyed then
			barricades += 1
			barricadeValue += s.barricadeReward
		end
		troops, tier, dmg, carried = s.troops, s.tier, s.dmg, s.carried
	end

	local f = finish(course, st, scale, troops, tier, dmg)
	local boss = f.boss

	local lines: { Line } = {}
	for _, line in upgradeLines do
		table.insert(lines, line)
	end
	-- Separate the permanent starting troops (upgrades) from what this run
	-- added or lost (gates / enemies can also lower the count) and what the
	-- boss took.
	local gained = troops - st.startTroops
	table.insert(lines, {
		kind = "troops",
		label = "Troops at finish x " .. boss.survivors,
		detail = ("started with %d from upgrades, %s%d this run, %d lost to the boss"):format(
			st.startTroops,
			if gained >= 0 then "+" else "-",
			math.abs(gained),
			boss.killed
		),
		value = f.troopsValue,
	})
	local cashDetail = Format.Money(carried) .. " of " .. Format.Money(st.capacity) .. " capacity"
	if overflowTotal > 0 then
		cashDetail ..= ", " .. Format.Money(overflowTotal) .. " lost (bag full)"
	end
	table.insert(lines, { kind = "cash", label = "Cash carried", detail = cashDetail, value = carried })
	local enemyDetail = kills .. " soldiers"
	if barricades > 0 then
		enemyDetail ..= " + " .. barricades .. (if barricades == 1 then " barricade" else " barricades")
	end
	table.insert(lines, {
		kind = "enemies",
		label = "Enemies defeated x " .. kills,
		detail = enemyDetail,
		value = killValue + barricadeValue,
	})
	if boss.victory then
		table.insert(lines, {
			kind = "boss",
			label = "Boss defeated!",
			detail = ("Boss HP %s beaten in %.1f s"):format(commas(boss.hp), boss.duration),
			value = f.bossValue,
		})
	else
		table.insert(lines, {
			kind = "boss",
			label = "Defeated by the boss",
			detail = ("Boss had %s HP left - only the best lanes beat it"):format(commas(boss.hpLeft)),
			value = 0,
		})
	end

	local base = 0
	for _, line in lines do
		base += line.value
	end
	local total, bonus = withBonus(base, st)
	if st.payoutBonus > 0 then
		table.insert(lines, {
			kind = "bonus",
			label = "Bonus +" .. Format.Percent(math.floor(st.payoutBonus * 1000 + 0.5) / 1000),
			detail = "Payout bonus",
			value = bonus,
		})
	end

	return {
		troops = boss.survivors,
		weaponTier = tier,
		damageMult = dmg,
		collected = collected,
		carried = carried,
		outcomes = outcomes,
		lines = lines,
		total = total,
		boss = boss,
		unitDps = f.dps,
	}
end

function RunnerSim.BestCase(course: Course, stats: Stats): (Result, { number })
	local st = cleanStats(stats)
	local scale = RunnerCourse.ValueScale(course.area, course.level)
	bossHpClean(course, st) -- memoized; its search is cheaper (3 dimensions)
	local bossSearch = lastBossSearch
	local frontier, visited, maxFrontier, capHit = search(course, st, scale, false)

	-- Pick the finished state with the biggest payout (first one wins ties).
	local best: Node = frontier[1]
	local bestTotal = -math.huge
	for _, node in frontier do
		local t = totalOf(course, st, scale, node)
		if t > bestTotal + 0.001 then
			best, bestTotal = node, t
		end
	end

	-- Walk back up the parents to recover the lane of every row.
	local reversed: { number } = {}
	local cursor: Node? = best
	while cursor and cursor.parent do
		table.insert(reversed, cursor.lane)
		cursor = cursor.parent
	end
	local choices: { number } = {}
	for i = #reversed, 1, -1 do
		table.insert(choices, reversed[i])
	end

	RunnerSim.LastSearch = {
		states = visited,
		maxFrontier = maxFrontier,
		capHit = capHit or bossSearch.capHit,
	}
	return RunnerSim.Resolve(course, stats, choices), choices
end

function RunnerSim.MinDuration(course: Course): number
	return math.max(0, finite(course.boss.distance, 0)) / RunnerDefs.RUN_SPEED
end

return RunnerSim
