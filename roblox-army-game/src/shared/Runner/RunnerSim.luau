--!strict
--[[
	RunnerSim
	Scores an army-runner course: given the lane picked in every row it works
	out the troops, weapon, damage, cash and enemies of the run and builds the
	completion summary lines. Pure and deterministic (no services, no random),
	so the server can re-simulate a run from the client's lane choices and the
	economy can compute the best possible run.

	Public API:
		RunnerSim.Resolve(course, stats, choices): Result
			choices[i] = lane picked in row i (1..#row.options). A missing,
			non-integer or out-of-range lane counts as lane 1.
		RunnerSim.BestCase(course, stats): (Result, { number })
			EXACT best run and the lane choices that produce it.
		RunnerSim.MinDuration(course): number
			fastest legal completion time in seconds (anti-cheat floor).
		RunnerSim.Firepower(troops, damage, weaponTier, damageMult): number
		RunnerSim.LastSearch: { states: number, maxFrontier: number, capHit: boolean }
			statistics of the most recent BestCase call (tests check capHit).
		RunnerSim.STATE_CAP: number

	Rules (all numbers are in RunnerDefs):
		State during a run: troops (>= 1, the avatar always survives),
		weaponTier (1 Pistol .. 6 Rocket), damageMult (starts at 1),
		carried cash (capped by stats.capacity) and banked value.
		Firepower = troops x stats.damage x weapon damageMult x damageMult.
		Gate "+N" adds N troops, "xN" multiplies, "-N" subtracts, "÷N" divides
		(rounded down). Troops never drop below 1.
		Gold weapon gate: +1 weapon tier and banks that weapon's cash value
		(at the top tier it still banks the Rocket value).
		Gold damage gate: damageMult +0.2 and banks DAMAGE_GATE_VALUE.
		Cash stack: added to carried cash up to stats.capacity; the rest is
		lost and reported ("bag full"). [REQUIREMENT: coin-carrying capacity]
		Squad: kills floor(firepower / soldier hp) soldiers (reward each);
		every surviving soldier costs 1 troop.
		Barricade: destroyed if firepower >= hp (reward banked), otherwise
		you lose 25% of your troops (min 1 left).
		Enemy base: fraction = min(1, firepower x BOSS_SECONDS / hp),
		pays reward x fraction.
		Payout = troops x troop value + carried cash + banked upgrade values
		+ enemy rewards + enemy base reward, then x (1 + stats.payoutBonus).

	Why BestCase is exact: the final payout never goes down when any of
	(troops, weaponTier, damageMult, carried, banked) goes up, and every row
	rule keeps that order. So a partial run that is <= another one in all five
	numbers can never finish ahead of it, and dropping it (Pareto pruning) never
	loses the optimum. The search keeps only the non-dominated states per row.
]]

local Shared = script.Parent.Parent
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

export type Result = {
	troops: number,
	weaponTier: number,
	damageMult: number,
	collected: number,
	carried: number,
	outcomes: { RowOutcome },
	lines: { Line },
	total: number,
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

local function finite(n: any, fallback: number): number
	if type(n) ~= "number" or n ~= n or n == math.huge or n == -math.huge then
		return fallback
	end
	return n
end

local function cleanStats(stats: Stats): CleanStats
	return {
		startTroops = math.max(1, math.floor(finite(stats.startTroops, 1))),
		damage = math.max(0, finite(stats.damage, 0)),
		capacity = Format.RoundCents(math.max(0, finite(stats.capacity, 0))),
		payoutBonus = math.clamp(finite(stats.payoutBonus, 0), 0, 10),
	}
end

function RunnerSim.Firepower(troops: number, damage: number, weaponTier: number, damageMult: number): number
	local tier = math.clamp(math.floor(weaponTier), 1, RunnerDefs.MAX_WEAPON_TIER)
	return troops * damage * RunnerDefs.WEAPONS[tier].damageMult * damageMult
end

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
		s.upgradeValue = Format.RoundCents(RunnerDefs.WEAPONS[s.tier].value * scale)
		s.note = if tier >= RunnerDefs.MAX_WEAPON_TIER
			then "Weapon already maxed: bonus cash"
			else RunnerDefs.WEAPONS[s.tier].name .. "!"
	elseif op == "damage" then
		s.dmg = dmg + math.max(0, amount)
		s.upgradeKind = "damage"
		s.upgradeValue = Format.RoundCents(RunnerDefs.DAMAGE_GATE_VALUE * scale)
		s.note = "+" .. math.floor(amount * 100 + 0.5) .. "% damage"
	end

	-- 2) Cash stack in this lane (cash rows and red toll gates)
	local cash = finite(opt.cash, 0)
	if cash > 0 then
		local room = math.max(0, stats.capacity - s.carried)
		local take = math.min(room, cash)
		s.collected = cash
		s.overflow = Format.RoundCents(cash - take)
		s.carried = Format.RoundCents(s.carried + take)
		if s.overflow > 0 then
			s.note = "Bag full: " .. Format.Money(s.overflow) .. " lost"
		end
	end

	-- 3) Fights
	if row.kind == "squad" and opt.enemies then
		local enemies = math.max(0, math.floor(finite(opt.enemies, 0)))
		local hp = math.max(EPS, finite(opt.hp, 1))
		local fp = RunnerSim.Firepower(s.troops, stats.damage, s.tier, s.dmg)
		local kills = math.min(enemies, math.floor(fp / hp + EPS))
		local survivors = enemies - kills
		local before = s.troops
		s.troops = math.max(1, s.troops - survivors)
		s.kills = kills
		s.killReward = Format.RoundCents(kills * math.max(0, finite(opt.reward, 0)))
		s.note = "Defeated " .. kills .. " of " .. enemies .. " soldiers, lost " .. (before - s.troops) .. " troops"
	elseif row.kind == "barricade" and opt.hp then
		local hp = math.max(0, finite(opt.hp, 0))
		local fp = RunnerSim.Firepower(s.troops, stats.damage, s.tier, s.dmg)
		if fp + EPS >= hp then
			s.destroyed = true
			s.barricadeReward = Format.RoundCents(math.max(0, finite(opt.reward, 0)))
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

-- Troop value, boss fraction and boss pay at the finish line.
local function finish(
	course: Course,
	stats: CleanStats,
	scale: number,
	troops: number,
	tier: number,
	dmg: number
): (number, number, number, number)
	local troopEach = Format.RoundCents(RunnerDefs.TROOP_VALUE * scale)
	local troopsValue = Format.RoundCents(troops * troopEach)
	local fp = RunnerSim.Firepower(troops, stats.damage, tier, dmg)
	local bossHp = math.max(EPS, finite(course.boss.hp, 1))
	local fraction = math.min(1, fp * RunnerDefs.BOSS_SECONDS / bossHp)
	local bossValue = Format.RoundCents(math.max(0, finite(course.boss.reward, 0)) * fraction)
	return troopEach, troopsValue, fraction, bossValue
end

-- Total payout of a finished state (same maths as the lines in Resolve).
local function totalOf(course: Course, stats: CleanStats, scale: number, node: Node): number
	local _, troopsValue, _, bossValue = finish(course, stats, scale, node.troops, node.tier, node.dmg)
	local base = Format.RoundCents(node.bank + troopsValue + node.carried + bossValue)
	if stats.payoutBonus > 0 then
		return Format.RoundCents(base + Format.RoundCents(base * stats.payoutBonus))
	end
	return base
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

	local troopEach, troopsValue, fraction, bossValue = finish(course, st, scale, troops, tier, dmg)

	local lines: { Line } = {}
	for _, line in upgradeLines do
		table.insert(lines, line)
	end
	-- Separate the permanent starting troops (upgrades) from what this run
	-- added or lost (gates / enemies can also lower the count).
	local gained = troops - st.startTroops
	table.insert(lines, {
		kind = "troops",
		label = "Troops at finish x " .. troops,
		detail = ("%s each (started with %d from upgrades, %s%d this run)"):format(
			Format.Money(troopEach),
			st.startTroops,
			if gained >= 0 then "+" else "-",
			math.abs(gained)
		),
		value = troopsValue,
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
		value = Format.RoundCents(killValue + barricadeValue),
	})
	table.insert(lines, {
		kind = "boss",
		label = "Enemy base destroyed " .. math.floor(fraction * 100 + EPS) .. "%",
		detail = Format.Money(finite(course.boss.reward, 0)) .. " for 100%",
		value = bossValue,
	})

	local base = 0
	for _, line in lines do
		base += line.value
	end
	base = Format.RoundCents(base)
	local total = base
	if st.payoutBonus > 0 then
		local bonus = Format.RoundCents(base * st.payoutBonus)
		table.insert(lines, {
			kind = "bonus",
			label = "Bonus +" .. Format.Percent(math.floor(st.payoutBonus * 1000 + 0.5) / 1000),
			detail = "Payout bonus",
			value = bonus,
		})
		total = Format.RoundCents(base + bonus)
	end

	return {
		troops = troops,
		weaponTier = tier,
		damageMult = dmg,
		collected = Format.RoundCents(collected),
		carried = carried,
		outcomes = outcomes,
		lines = lines,
		total = total,
	}
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

-- `a` is at least as good as `b` in every dimension. Bank values are whole
-- cents, so a 0.001 tolerance only absorbs floating-point noise.
local function dominates(a: Node, b: Node): boolean
	return a.troops >= b.troops
		and a.tier >= b.tier
		and a.dmg >= b.dmg - EPS
		and a.carried >= b.carried - 0.001
		and a.bank >= b.bank - 0.001
end

local function prune(nodes: { Node }): { Node }
	table.sort(nodes, nodeBefore)
	local kept: { Node } = {}
	for _, node in nodes do
		local dominated = false
		for _, k in kept do
			if dominates(k, node) then
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

function RunnerSim.BestCase(course: Course, stats: Stats): (Result, { number })
	local st = cleanStats(stats)
	local scale = RunnerCourse.ValueScale(course.area, course.level)
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
						carried = s.carried,
						bank = node.bank + s.bankDelta,
						lane = lane,
						parent = node,
					}
					visited += 1
					local key = string.format("%d|%d|%.4f|%.2f", child.troops, child.tier, child.dmg, child.carried)
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
			nextNodes = prune(nextNodes)
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

	RunnerSim.LastSearch = { states = visited, maxFrontier = maxFrontier, capHit = capHit }
	return RunnerSim.Resolve(course, stats, choices), choices
end

function RunnerSim.MinDuration(course: Course): number
	return math.max(0, finite(course.boss.distance, 0)) / RunnerDefs.RUN_SPEED
end

return RunnerSim
