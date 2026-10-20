--!strict
--[[
	RunnerCourse
	Builds the army-runner course for an (area, level) pair. The course is pure
	data (rows of gates, cash, enemies and a final enemy base); the RUNNER
	system turns it into parts on the client and RunnerSim scores it on the
	server. The same (area, level) always produces exactly the same course
	(seeded Shared/Rng, never math.random), so server, client and the economy
	simulation agree.

	Public API:
		RunnerCourse.Generate(area: number, level: number): Course
		RunnerCourse.SeedFor(area: number, level: number): number
		RunnerCourse.ValueScale(area: number, level: number): number
			-- multiplier for every cash value of that course (area x level growth)

	Row kinds and their options (see RunnerDefs for every number):
		"gate"      2 lanes. Troop maths gates (the original modifiers, applied to
		            the TROOP COUNT): "+N" and "xN" are blue (tone "positive"),
		            "-N" and "÷N" are red (tone "negative"). Gold gates
		            (tone "weapon"): op "weapon" (+1 weapon tier) or op "damage"
		            (+20% damage). A red "toll" gate (op "sub") may ALSO carry a
		            cash stack (`cash`): you lose troops but grab the money.
		"cash"      3 lanes of cash stacks (tone "cash", `cash` = value).
		"squad"     1 lane (unavoidable) or 2 lanes (pick the squad to fight).
		            `enemies` = soldiers, `hp` = hit points of EACH soldier,
		            `reward` = $ per soldier defeated. Tone "enemy".
		"barricade" 2 lanes (weak / strong). `hp` = barricade hit points,
		            `reward` = $ for destroying it. Tone "enemy".
	The course ends with the enemy base (`boss`): hp and reward for 100%.

	Every row has at least one sensible option (a blue/gold gate, any cash
	lane, the smaller squad, the weaker barricade).
]]

local Shared = script.Parent.Parent
local EconomyDefs = require(Shared:WaitForChild("EconomyDefs"))
local Format = require(Shared:WaitForChild("Format"))
local Rng = require(Shared:WaitForChild("Rng"))
local RunnerDefs = require(script.Parent:WaitForChild("RunnerDefs"))

export type Option = {
	lane: number, -- 1..#row.options, left -> right
	x: number, -- lateral centre (studs, + = right of centre line)
	width: number, -- studs
	op: string?, -- gate: "add" | "mul" | "sub" | "div" | "weapon" | "damage"
	amount: number?, -- +N troops, xN, -N, /N, +tiers, +damage fraction (0.2 = +20%)
	label: string, -- what the gate panel shows: "+5", "x2", "-3", "÷2", "WEAPON +1", "+20% DMG", "$12.50"
	tone: string, -- "positive" (blue) | "negative" (red) | "weapon" (gold) | "cash" | "enemy"
	cash: number?, -- cash row (or toll gate): value of the cash stack in this lane
	enemies: number?, -- squad row: number of enemy soldiers
	hp: number?, -- barricade row: hit points; squad row: hit points of each soldier
	reward: number?, -- cash for destroying the barricade / per enemy defeated
}

export type Row = { index: number, kind: string, distance: number, options: { Option } }

export type Course = {
	area: number,
	level: number,
	seed: number,
	length: number,
	rows: { Row },
	boss: { distance: number, hp: number, reward: number },
}

-- An option before it is given a lane (lane/x/width are filled in later).
type Draft = {
	op: string?,
	amount: number?,
	label: string,
	tone: string,
	cash: number?,
	enemies: number?,
	hp: number?,
	reward: number?,
}

local RunnerCourse = {}

-- Deterministic seed from (area, level). Different for every pair in practice.
function RunnerCourse.SeedFor(area: number, level: number): number
	local a = math.max(1, math.floor(area))
	local l = math.max(1, math.floor(level))
	return (a * 1000003 + l * 7919 + 12345) % 2147483646 + 1
end

-- Multiplier for every cash value: AreaScale^(area-1) x the gentle level factor.
function RunnerCourse.ValueScale(area: number, level: number): number
	local a = math.max(1, math.floor(area))
	return EconomyDefs.AreaScale ^ (a - 1) * RunnerDefs.LevelFactor(a, level)
end

-- Round a cash amount to a "nice" value: quarters below $20, halves below
-- $200, whole dollars above. Always a whole number of cents.
local function niceMoney(v: number): number
	if v < 20 then
		return Format.RoundCents(math.max(0.25, math.floor(v * 4 + 0.5) / 4))
	elseif v < 200 then
		return Format.RoundCents(math.floor(v * 2 + 0.5) / 2)
	end
	return math.floor(v + 0.5)
end

local function addLanes(laneKey: string, drafts: { Draft }, rng: Rng.Rng, shuffle: boolean): { Option }
	if shuffle then
		rng:Shuffle(drafts)
	end
	local lanes = RunnerDefs.LANES[laneKey]
	local options: { Option } = {}
	for index, d in drafts do
		local lane = lanes[index]
		table.insert(options, {
			lane = index,
			x = lane.x,
			width = lane.width,
			op = d.op,
			amount = d.amount,
			label = d.label,
			tone = d.tone,
			cash = d.cash,
			enemies = d.enemies,
			hp = d.hp,
			reward = d.reward,
		})
	end
	return options
end

function RunnerCourse.Generate(area: number, level: number): Course
	area = math.max(1, math.floor(area))
	level = math.max(1, math.floor(level))
	local seed = RunnerCourse.SeedFor(area, level)
	local rng = Rng.new(seed)

	local scale = RunnerCourse.ValueScale(area, level)
	local troopScale = RunnerDefs.TroopScale(area)
	local levelFactor = RunnerDefs.LevelFactor(area, level)
	local hpScale = RunnerDefs.EnemyHpScale(area) * levelFactor
	local armyScale = RunnerDefs.ArmyScale(area)

	-- Option builders ---------------------------------------------------------
	-- [PROPOSAL] The small random spreads below (about +-10..25%), the 15% chance
	-- of a late "÷3", the 40% chance of a single-lane squad and the 2.2x reward of
	-- a strong barricade only add variety; the big numbers live in RunnerDefs.
	local function addN(rowIndex: number): Draft
		local n = math.max(
			1,
			math.floor(
				(RunnerDefs.ADD_BASE + RunnerDefs.ADD_PER_ROW * rowIndex) * troopScale * rng:Range(0.8, 1.25) + 0.5
			)
		)
		return { op = "add", amount = n, label = "+" .. n, tone = "positive" }
	end
	local function mulN(): Draft
		return { op = "mul", amount = 2, label = "x2", tone = "positive" }
	end
	local function subN(rowIndex: number): Draft
		local n = math.max(
			1,
			math.floor(
				(RunnerDefs.SUB_BASE + RunnerDefs.SUB_PER_ROW * rowIndex) * troopScale * rng:Range(0.8, 1.2) + 0.5
			)
		)
		return { op = "sub", amount = n, label = "-" .. n, tone = "negative" }
	end
	local function divN(rowIndex: number): Draft
		local d = if rowIndex >= 10 and rng:Chance(0.15) then 3 else 2
		return { op = "div", amount = d, label = "÷" .. d, tone = "negative" }
	end
	local function toll(rowIndex: number): Draft
		local d = subN(rowIndex)
		local cash = niceMoney(
			RunnerDefs.TOLL_CASH_BASE * (1 + RunnerDefs.CASH_ROW_GROWTH * rowIndex) * scale * rng:Range(0.85, 1.2)
		)
		d.cash = cash
		d.label = d.label .. "  +" .. Format.Money(cash)
		return d
	end
	local function weapon(): Draft
		return { op = "weapon", amount = 1, label = "WEAPON +1", tone = "weapon" }
	end
	local function damage(): Draft
		local pct = math.floor(RunnerDefs.DAMAGE_GATE_AMOUNT * 100 + 0.5)
		return { op = "damage", amount = RunnerDefs.DAMAGE_GATE_AMOUNT, label = "+" .. pct .. "% DMG", tone = "weapon" }
	end

	-- Row builders --------------------------------------------------------------
	-- [PROPOSAL] Exactly MUL_ROWS troop gates per course offer a "x2" (so troop
	-- counts stay readable and every course is worth about the same); the other
	-- troop gates mix + - and toll gates. Weighted variants keep every troop
	-- gate a real decision.
	local MUL_VARIANTS = {
		{ id = "addMul", weight = 3 }, -- "+8" vs "x2": depends on how many troops you have
		{ id = "mulDiv", weight = 1 }, -- "x2" vs "÷2"
	}
	local PLAIN_VARIANTS = {
		{ id = "addSub", weight = 2 }, -- blue vs red (classic)
		{ id = "toll", weight = 2 }, -- "+3" vs "-4 +$20": troops or cash?
		{ id = "addAdd", weight = 1.5 }, -- two blue gates of different size
	}
	local UPGRADE_VARIANTS = {
		{ id = "weaponTroops", weight = 3 }, -- gold weapon gate vs more troops
		{ id = "damageTroops", weight = 2 }, -- +20% DMG vs more troops
		{ id = "weaponDamage", weight = 2 }, -- weapon vs damage
	}
	local function weightOf(v: { id: string, weight: number }): number
		return v.weight
	end

	local function troopGate(rowIndex: number, withMul: boolean): { Option }
		local variant = rng:Weighted(if withMul then MUL_VARIANTS else PLAIN_VARIANTS, weightOf)
		local drafts: { Draft }
		if variant.id == "addMul" then
			drafts = { addN(rowIndex), mulN() }
		elseif variant.id == "mulDiv" then
			drafts = { mulN(), divN(rowIndex) }
		elseif variant.id == "addSub" then
			drafts = { addN(rowIndex), subN(rowIndex) }
		elseif variant.id == "toll" then
			drafts = { addN(rowIndex), toll(rowIndex) }
		else
			local a, b = addN(rowIndex), addN(rowIndex)
			if a.amount == b.amount then
				local bigger = (b.amount :: number) + math.max(1, math.floor(0.5 * (b.amount :: number)))
				b.amount = bigger
				b.label = "+" .. bigger
			end
			drafts = { a, b }
		end
		return addLanes("gate", drafts, rng, true)
	end

	local function upgradeGate(rowIndex: number): { Option }
		local variant = rng:Weighted(UPGRADE_VARIANTS, weightOf)
		local drafts: { Draft }
		if variant.id == "weaponTroops" then
			drafts = { weapon(), addN(rowIndex) }
		elseif variant.id == "damageTroops" then
			drafts = { damage(), addN(rowIndex) }
		else
			drafts = { weapon(), damage() }
		end
		return addLanes("gate", drafts, rng, true)
	end

	local function cashRow(rowIndex: number): { Option }
		local base = RunnerDefs.CASH_ROW_BASE * (1 + RunnerDefs.CASH_ROW_GROWTH * rowIndex) * scale
		local drafts: { Draft } = {}
		for _, spread in RunnerDefs.CASH_LANE_SPREAD do
			local value = niceMoney(base * spread * rng:Range(0.9, 1.1))
			table.insert(drafts, { label = Format.Money(value), tone = "cash", cash = value })
		end
		return addLanes("cash", drafts, rng, true)
	end

	local function squadDraft(rowIndex: number, sizeMult: number): Draft
		local expectedTroops = RunnerDefs.EXPECTED_TROOPS_BASE + RunnerDefs.EXPECTED_TROOPS_PER_ROW * rowIndex
		local enemies = math.max(
			1,
			math.floor(expectedTroops * RunnerDefs.SQUAD_SHARE * armyScale * sizeMult * rng:Range(0.85, 1.15) + 0.5)
		)
		return {
			label = enemies .. " SOLDIERS",
			tone = "enemy",
			enemies = enemies,
			hp = Format.RoundCents(RunnerDefs.ENEMY_HP * hpScale),
			reward = Format.RoundCents(RunnerDefs.ENEMY_REWARD * scale),
		}
	end

	local function squadRow(rowIndex: number, final: boolean): { Option }
		if final then
			return addLanes("squad1", { squadDraft(rowIndex, RunnerDefs.SQUAD_FINAL) }, rng, false)
		elseif rng:Chance(0.4) then
			return addLanes("squad1", { squadDraft(rowIndex, 1) }, rng, false)
		end
		-- [PROPOSAL] two squads: a small one and a big one that pays more.
		return addLanes(
			"squad2",
			{ squadDraft(rowIndex, RunnerDefs.SQUAD_SMALL), squadDraft(rowIndex, RunnerDefs.SQUAD_BIG) },
			rng,
			true
		)
	end

	local function barricadeRow(rowIndex: number): { Option }
		local power = RunnerDefs.ExpectedFirepower(rowIndex) * hpScale * armyScale
		local weakHp = math.floor(power * RunnerDefs.BARRICADE_WEAK * rng:Range(0.9, 1.1) + 0.5)
		local strongHp = math.floor(power * RunnerDefs.BARRICADE_STRONG * rng:Range(0.9, 1.1) + 0.5)
		local weakReward = niceMoney(RunnerDefs.BARRICADE_REWARD * (1 + RunnerDefs.CASH_ROW_GROWTH * rowIndex) * scale)
		local strongReward = niceMoney(weakReward * 2.2)
		local drafts: { Draft } = {
			{ label = "HP " .. weakHp, tone = "enemy", hp = weakHp, reward = weakReward },
			{ label = "HP " .. strongHp, tone = "enemy", hp = strongHp, reward = strongReward },
		}
		return addLanes("barricade", drafts, rng, true)
	end

	-- Course blueprint ------------------------------------------------------------
	-- [PROPOSAL] 18 rows. Row 1 is always a troop gate; rows 2-16 come in blocks
	-- of three that are shuffled for variety; rows 17-18 are a final squad and a
	-- last upgrade/troop gate before the enemy base.
	local kinds: { string } = { "troop" }
	local blocks = {
		{ "cash", "troop", "squad" },
		{ "upgrade", "cash", "troop" },
		{ "barricade", "upgrade", "squad" },
		{ "cash", "troop", "upgrade" },
		{ "barricade", "troop", "cash" },
	}
	for _, block in blocks do
		local copy = table.clone(block)
		rng:Shuffle(copy)
		for _, k in copy do
			table.insert(kinds, k)
		end
	end
	table.insert(kinds, "squadFinal")
	table.insert(kinds, if rng:Chance(0.5) then "upgrade" else "troop")

	-- Choose which troop gates (never row 1) offer the x2.
	local troopRows: { number } = {}
	for index, k in kinds do
		if k == "troop" and index > 1 then
			table.insert(troopRows, index)
		end
	end
	rng:Shuffle(troopRows)
	local mulRows: { [number]: boolean } = {}
	for i = 1, math.min(RunnerDefs.MUL_ROWS, #troopRows) do
		mulRows[troopRows[i]] = true
	end

	local rows: { Row } = {}
	for index, k in kinds do
		local options: { Option }
		local rowKind: string
		if k == "troop" then
			rowKind, options = "gate", troopGate(index, mulRows[index] == true)
		elseif k == "upgrade" then
			rowKind, options = "gate", upgradeGate(index)
		elseif k == "cash" then
			rowKind, options = "cash", cashRow(index)
		elseif k == "barricade" then
			rowKind, options = "barricade", barricadeRow(index)
		else
			rowKind, options = "squad", squadRow(index, k == "squadFinal")
		end
		table.insert(rows, {
			index = index,
			kind = rowKind,
			distance = RunnerDefs.FIRST_ROW_DISTANCE + (index - 1) * RunnerDefs.ROW_SPACING,
			options = options,
		})
	end

	local boss = {
		distance = RunnerDefs.BOSS_DISTANCE,
		hp = math.floor(RunnerDefs.BOSS_HP * hpScale * armyScale + 0.5),
		reward = niceMoney(RunnerDefs.BOSS_REWARD * scale),
	}

	return {
		area = area,
		level = level,
		seed = seed,
		length = RunnerDefs.TRACK_LENGTH,
		rows = rows,
		boss = boss,
	}
end

return RunnerCourse
