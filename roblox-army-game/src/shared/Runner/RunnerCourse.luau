--!strict
--[[
	RunnerCourse
	Builds the army-runner course for an (area, level) pair. The course is pure
	data (rows of gates, cash, enemies and a boss at the castle); the RUNNER
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
		            (+20% damage). A gate may ALSO carry a cash stack (`cash`):
		            a red "toll" gate (lose troops, grab the money) or the
		            smaller of two blue gates (fewer troops + money vs more
		            troops) - so the money is not always behind the red gate.
		"contest"   2 lanes: "shoot to grow" gates [owner request]. A blue "+N"
		            (tone "growPositive") and a red "-N" (tone "growNegative").
		            `growHp` = damage that adds one step, `growStep` = troops
		            per step, `growMax` = the most steps. Shooting a side for the
		            engagement makes its number bigger (RunnerSim); additive only.
		"cash"      3 lanes of cash stacks (tone "cash", `cash` = value).
		"squad"     1 lane (unavoidable) or 2 lanes (pick the squad to fight).
		            `enemies` = soldiers, `hp` = hit points of EACH soldier,
		            `reward` = $ per soldier defeated. Tone "enemy".
		"barricade" 2 lanes (weak / strong). `hp` = barricade hit points,
		            `reward` = $ for destroying it. Tone "enemy".
	The course ends with the castle boss (`boss`): distance and the cash
	reward for beating it. `boss.hp` is always 0: the boss HP depends on the
	player's stats and comes from RunnerSim.BossHp(course, stats).
	Every cash value (stacks, tolls, rewards) is a whole number of dollars.

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
	label: string, -- what the gate panel shows: "+5", "x2", "-3", "÷2", "WEAPON +1", "+20% DMG", "$12"
	tone: string, -- "positive" (blue) | "negative" (red) | "weapon" (gold) | "cash" | "enemy"
	-- | "growPositive" / "growNegative" (contest: shoot-to-grow blue / red)
	cash: number?, -- cash row (or toll gate): value of the cash stack in this lane
	enemies: number?, -- squad row: number of enemy soldiers
	hp: number?, -- barricade row: hit points; squad row: hit points of each soldier
	reward: number?, -- cash for destroying the barricade / per enemy defeated
	growHp: number?, -- contest: damage that grows the gate by one step
	growStep: number?, -- contest: troops added to the gate's number per step
	growMax: number?, -- contest: the most steps the gate can grow
}

export type Row = { index: number, kind: string, distance: number, options: { Option } }

export type Course = {
	area: number,
	level: number,
	seed: number,
	length: number,
	rows: { Row },
	boss: { distance: number, hp: number, reward: number }, -- hp = 0 (see RunnerSim.BossHp)
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
	growHp: number?,
	growStep: number?,
	growMax: number?,
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

-- Round a cash amount to whole dollars (owner request: no cents), at least $1.
local function niceMoney(v: number): number
	return math.max(1, Format.RoundWhole(v))
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
			growHp = d.growHp,
			growStep = d.growStep,
			growMax = d.growMax,
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
	-- Puts a cash stack behind a gate ("-4  +$20" / "+3  +$15").
	local function withCash(d: Draft, rowIndex: number, share: number): Draft
		local cash = niceMoney(
			RunnerDefs.TOLL_CASH_BASE
				* share
				* (1 + RunnerDefs.CASH_ROW_GROWTH * rowIndex)
				* scale
				* rng:Range(0.85, 1.2)
		)
		d.cash = cash
		d.label = d.label .. "  +" .. Format.Money(cash)
		return d
	end
	local function toll(rowIndex: number): Draft
		return withCash(subN(rowIndex), rowIndex, 1)
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
		{ id = "toll", weight = 1.2 }, -- "+3" vs "-4 +$20": troops or cash?
		{ id = "bonusAdd", weight = 1.2 }, -- "+3 +$15" vs "+6": the cash rides a BLUE gate
		{ id = "addAdd", weight = 1 }, -- two blue gates of different size
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
		elseif variant.id == "bonusAdd" then
			-- [PROPOSAL] the cash gate gives fewer troops: the other gives ~60-100% more.
			local small, big = addN(rowIndex), addN(rowIndex)
			local smallN = small.amount :: number
			local bigN = math.max(big.amount :: number, smallN + math.max(2, math.floor(0.6 * smallN + 0.5)))
			big.amount = bigN
			big.label = "+" .. bigN
			drafts = { withCash(small, rowIndex, 0.75), big }
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
			-- [PROPOSAL] 30%: the troop gate also carries cash (weapon vs troops + cash).
			local troops = addN(rowIndex)
			if rng:Chance(0.3) then
				troops = withCash(troops, rowIndex, 0.75)
			end
			drafts = { weapon(), troops }
		elseif variant.id == "damageTroops" then
			drafts = { damage(), addN(rowIndex) }
		else
			drafts = { weapon(), damage() }
		end
		return addLanes("gate", drafts, rng, true)
	end

	-- Shoot-to-grow gates (see RunnerDefs "contest"): blue starts below a "+N"
	-- gate of the row, red like a "-N"; a reference squad shooting one side for
	-- the whole engagement adds CONTEST_STEPS_AT_EXPECTED steps.
	local function contestRow(rowIndex: number): { Option }
		local blueN = math.max(
			1,
			math.floor(
				(RunnerDefs.ADD_BASE + RunnerDefs.ADD_PER_ROW * rowIndex)
						* troopScale
						* RunnerDefs.CONTEST_BLUE_START
						* rng:Range(0.9, 1.1)
					+ 0.5
			)
		)
		local redN = math.max(
			1,
			math.floor(
				(RunnerDefs.SUB_BASE + RunnerDefs.SUB_PER_ROW * rowIndex)
						* troopScale
						* RunnerDefs.CONTEST_RED_START
						* rng:Range(0.9, 1.1)
					+ 0.5
			)
		)
		local power = RunnerDefs.ExpectedFirepower(rowIndex) * hpScale * armyScale
		local growHp = math.max(1, math.floor(power / RunnerDefs.CONTEST_STEPS_AT_EXPECTED + 0.5))
		local growStep = math.max(1, math.floor(troopScale + 0.5))
		local drafts: { Draft } = {
			{
				op = "add",
				amount = blueN,
				label = "+" .. blueN,
				tone = "growPositive",
				growHp = growHp,
				growStep = growStep,
				growMax = RunnerDefs.CONTEST_MAX_STEPS,
			},
			{
				op = "sub",
				amount = redN,
				label = "-" .. redN,
				tone = "growNegative",
				growHp = growHp,
				growStep = growStep,
				growMax = RunnerDefs.CONTEST_MAX_STEPS,
			},
		}
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
			hp = math.max(1, Format.RoundWhole(RunnerDefs.ENEMY_HP * hpScale)),
			reward = niceMoney(RunnerDefs.ENEMY_REWARD * scale),
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
	-- of three that are shuffled for variety (two blocks hold the
	-- RunnerDefs.CONTEST_ROWS shoot-to-grow gates); rows 17-18 are a final squad
	-- and a last upgrade/troop gate before the castle boss.
	local kinds: { string } = { "troop" }
	local blocks = {
		{ "cash", "troop", "squad" },
		{ "upgrade", "contest", "troop" },
		{ "barricade", "upgrade", "squad" },
		{ "cash", "troop", "upgrade" },
		{ "barricade", "contest", "cash" },
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
		elseif k == "contest" then
			rowKind, options = "contest", contestRow(index)
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
		hp = 0, -- not used: the boss HP comes from RunnerSim.BossHp(course, stats)
		reward = niceMoney(RunnerDefs.BOSS_REWARD * scale * armyScale ^ RunnerDefs.BOSS_REWARD_ARMY_EXP),
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
