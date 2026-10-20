--!strict
--[[
	RunnerVisuals (client)
	Draws one army-runner course for the local player only (ARCHITECTURE §5:
	per-player visuals are built on the client). The server already built the
	white track floor; this module adds everything ON the track:

	  * gate rows   - translucent blue / red / gold panels between dark posts,
	                  with huge white outlined labels ("+5", "x2", "-3", "÷2", "WEAPON +1").
	                  When the row before a gold weapon gate is crossed, that gate is
	                  relabelled with the gun you would get ("🔫 SMG"; at the top tier
	                  "🔫 MAX +$X", the cash it pays instead); rows further ahead keep
	                  "WEAPON +1" (the gun depends on the gates you take before them)
	  * cash rows   - stacks of gold coins + green cash bricks. Their VALUE labels
	                  (and the cash label of a red toll gate) stay hidden until the
	                  previous row has been crossed (owner request: the far-away
	                  values cluttered the view); row 1's are visible from the start
	  * enemy squads- red-uniform soldiers with rifles and a count label
	  * barricades  - sandbag walls with an HP bar
	  * the castle  - at the end of the track, with a 13-stud gateway; the BOSS
	                  (RunnerBoss) waits hidden behind its door and walks out of it
	  * your squad  - up to 40 green toy soldiers standing NEXT TO the avatar in
	                  rows of RunnerDefs.FORMATION_WIDTH (front row: 2 troops, the
	                  avatar, 2 troops; more rows behind), each holding the same gun.
	                  Near a track edge the WHOLE formation shifts sideways by whole
	                  columns (the avatar's own spot stays free), so troops never
	                  overlap; the avatar itself stays where the player steers
	  * guns        - the avatar holds the server's "RunnerGun" Tool; we weld the
	                  part-built gun of the current weapon tier (RunnerGuns) to its
	                  Handle. The avatar and the front row fire at the gun's TRUE
	                  fireRate (up to FRONT_MAX_RATE = 12/s); the back rows share what
	                  is left of the bullet budget (BULLET_BUDGET of the pool alive),
	                  so the pistol, SMG, assault rifle and minigun clearly differ in
	                  rate, bullet style and speed. Range: RunnerDefs.GUN_RANGE
	  * effects     - pop text, gate flashes, hit flashes, falling enemies, confetti

	The OUTCOME of every fight comes from RunnerSim (RunnerController passes it
	in); this module only shows it: enemies fall / barricade HP drains while the
	squad closes the last GUN_RANGE studs (= RunnerDefs.ENGAGE_SECONDS), and the
	boss fight follows the BossOutcome exactly (RunnerSim.BossHpAt / AliveAt).
	RunnerController passes the SERVER's BossOutcome (RunnerFinish reply) so the
	fight on screen always matches what is paid.

	Public API:
		local visuals = RunnerVisuals.new(course, slot, character)
		visuals.fortDistance: number      -- distance (studs from start) of the castle's front
		visuals.stopDistance: number      -- where the avatar stops to fight the boss
		visuals:SetSquad(troops, weaponTier)   -- troops to draw (true count; drawn capped at 40) + gun
		visuals:Update(dt, root: BasePart?, moving: boolean)   -- call every frame
		visuals:Engage(rowIndex, lane, fight: Fight?)   -- shoot that row's option (lane may change)
		visuals:RowCrossed(rowIndex, lane, outcome?, capacity, fight: Fight?)  -- play the row's result
		visuals:OpenCastle(maxHp) -> number   -- intro: the door sinks, then the boss
			appears and walks out; returns its length (RunnerDefs.BOSS_INTRO_SECONDS).
			Call it on arrival; the fight outcome can follow later. Calling again is a no-op.
		visuals:StartBossFight(outcome: BossOutcome, callbacks) -> number (seconds from now until onDone)
			opens the castle first if needed; the fight starts when the intro is
			over (right away if it already is), then RunnerDefs.BOSS_OUTRO_SECONDS
			of victory / defeat before onDone.
			callbacks = { onProgress(alive, hp), onResolved(victory), onDone() }
		visuals:BossFocus() -> Vector3?   -- boss chest (camera framing)
		visuals:Destroy()                 -- removes every instance it created, restores the avatar pose

		Fight = { enemies: number, kills: number, share: number, destroyed: boolean }
			squads: kills of enemies; barricades: share = fraction of its HP taken off

	This module never talks to the server; RunnerController feeds it.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local RunnerCourse = require(Shared.Runner.RunnerCourse)
local RunnerDefs = require(Shared.Runner.RunnerDefs)
local RunnerSim = require(Shared.Runner.RunnerSim)

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)
local RunnerBoss = require(script.Parent.RunnerBoss)
local RunnerGuns = require(script.Parent.RunnerGuns)
local RunnerModels = require(script.Parent.RunnerModels)

type Course = RunnerCourse.Course
type Option = RunnerCourse.Option
type Row = RunnerCourse.Row
type RowOutcome = RunnerSim.RowOutcome
type BossOutcome = RunnerSim.BossOutcome
type Rig = RunnerModels.Rig
type Boss = RunnerBoss.Boss

local P = Config.Palette

-------------------------------------------------------------------------------
-- Tunables (all visual only)
-------------------------------------------------------------------------------
local MAX_TROOP_MODELS = 40 -- [REQUIREMENT ARCH §0.10] cap rendered troops
local MAX_ENEMY_MODELS = 12 -- per squad lane [PROPOSAL]
local FRONT_MAX_RATE = 12 -- [PROPOSAL] avatar + front row fire at the gun's true rate, up to this (shots/s)
local MIN_BACK_RATE = 0.3 -- [PROPOSAL] back-row troops never fire slower than this (shots/s)
local BULLET_BUDGET = math.floor(RunnerGuns.POOL_SIZE * 0.8) -- [PROPOSAL] bullets aimed to be alive (rest: boss shots)
local GATE_HEIGHT = 7
local POST_HEIGHT = 8.4
local FORT_DEPTH = 14

-- Boss timing is shared with the economy (RunnerDefs; fallback = same values).
local function defNumber(name: string, fallback: number): number
	local value = (RunnerDefs :: any)[name]
	return if type(value) == "number" and value >= 0 then value else fallback
end
local BOSS_INTRO_SECONDS = defNumber("BOSS_INTRO_SECONDS", 2.62) -- door opens + boss walks out
local BOSS_OUTRO_SECONDS = defNumber("BOSS_OUTRO_SECONDS", 2.8) -- victory / defeat before the summary
local BOSS_DOOR_TIME = math.min(0.7, BOSS_INTRO_SECONDS * 0.4) -- [PROPOSAL] door sinks (part of the intro)
local BOSS_WAIT_DEPTH = RunnerModels.FORT_GATE_DEPTH / 2 -- boss waits this deep in the gateway
local BOSS_STAND = 9 -- boss stops this many studs in front of the castle
local SQUAD_GAP = 30 -- the avatar stops this many studs in front of the castle
local DEATH_TIME = 0.45
local HIDDEN = CFrame.new(0, -500, 0)

local GUN_RANGE = RunnerDefs.GUN_RANGE
local FORMATION_WIDTH = math.max(1, math.floor(RunnerDefs.FORMATION_WIDTH))
local SPACING_X = RunnerDefs.FORMATION_SPACING_X
local SPACING_Z = RunnerDefs.FORMATION_SPACING_Z

local function toneColor(option: Option): Color3
	local tone = option.tone
	if tone == "negative" or tone == "enemy" then
		return P.GateNegative
	elseif tone == "weapon" then
		return P.GateWeapon
	elseif tone == "cash" then
		return P.MoneyGreen
	end
	if option.op == "sub" or option.op == "div" then
		return P.GateNegative
	elseif option.op == "weapon" or option.op == "damage" then
		return P.GateWeapon
	end
	return P.GatePositive
end

-- Formation slot of troop i (1-based; the avatar is the centre of row 0).
-- Row 0 holds FORMATION_WIDTH - 1 troops (closest to the avatar first), every
-- later row FORMATION_WIDTH (centre first). Returns (column, row): column in
-- units of SPACING_X (+ = right), row in units of SPACING_Z behind the avatar.
local function slotOf(i: number): (number, number)
	local half = math.floor(FORMATION_WIDTH / 2)
	local front = FORMATION_WIDTH - 1
	if i <= front then
		local k = math.ceil(i / 2)
		return if i % 2 == 1 then -k else k, 0
	end
	local j = i - front - 1 -- 0-based in the back rows
	local row = 1 + math.floor(j / FORMATION_WIDTH)
	local index = j % FORMATION_WIDTH -- 0 = centre, then left/right pairs
	if index == 0 then
		return 0, row
	end
	local k = math.min(half, math.ceil(index / 2))
	return if index % 2 == 1 then -k else k, row
end

-------------------------------------------------------------------------------
-- Small UI helpers (billboards over world objects)
-------------------------------------------------------------------------------
type HpBar = { set: (fraction: number, duration: number?) -> (), gui: BillboardGui }

local function makeHpBar(adornee: BasePart, title: string, width: number, offsetY: number): HpBar
	local gui = Theme.Billboard({
		Name = "HpBar",
		Parent = adornee,
		Adornee = adornee,
		Size = UDim2.fromOffset(width, 54),
		StudsOffset = Vector3.new(0, offsetY, 0),
		AlwaysOnTop = false,
		MaxDistance = 160,
	})
	Theme.Label({
		Text = title,
		MaxTextSize = 22,
		Size = UDim2.new(1, 0, 0, 24),
		Parent = gui,
	})
	local back = Instance.new("Frame")
	back.BackgroundColor3 = P.PillBlack
	back.Position = UDim2.fromOffset(0, 28)
	back.Size = UDim2.new(1, 0, 0, 20)
	back.Parent = gui
	Theme.Corner(back, 0.5)
	Theme.Stroke(back, P.CardOutline, 2, true)
	local fill = Instance.new("Frame")
	fill.BackgroundColor3 = P.ButtonRed
	fill.Position = UDim2.fromOffset(2, 2)
	fill.Size = UDim2.new(1, -4, 1, -4)
	fill.Parent = back
	Theme.Corner(fill, 0.5)
	return {
		gui = gui,
		set = function(fraction: number, duration: number?)
			local f = math.clamp(fraction, 0, 1)
			local goal = UDim2.new(f, -math.floor(4 * f + 0.5), 1, -4) -- offsets must be whole pixels
			if duration and duration > 0 then
				Theme.Tween(fill, { Size = goal }, duration, Enum.EasingStyle.Linear)
			else
				fill.Size = goal
			end
		end,
	}
end

local function makeTextBillboard(
	adornee: BasePart,
	text: string,
	color: Color3,
	offsetY: number,
	pill: Color3?
): BillboardGui
	local gui = Theme.Billboard({
		Name = "Value",
		Parent = adornee,
		Adornee = adornee,
		Size = UDim2.fromOffset(150, 46),
		StudsOffset = Vector3.new(0, offsetY, 0),
		AlwaysOnTop = false,
		MaxDistance = 220,
	})
	local holder: Instance = gui
	if pill then
		local frame = Instance.new("Frame")
		frame.Name = "Pill"
		frame.BackgroundColor3 = pill
		frame.Size = UDim2.fromScale(0.8, 1)
		frame.Position = UDim2.fromScale(0.5, 0)
		frame.AnchorPoint = Vector2.new(0.5, 0)
		frame.Parent = gui
		Theme.Corner(frame, 0.4)
		Theme.Stroke(frame, P.CardOutline, 2, true)
		holder = frame
	end
	Theme.Label({
		Name = "Text",
		Text = text,
		TextColor3 = color,
		MaxTextSize = 34,
		Size = UDim2.new(1, -8, 1, -4),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = holder,
	})
	return gui
end

-- Invisible anchor part used to hang billboards in the world.
local function anchorPart(parent: Instance, position: Vector3): Part
	return RunnerModels.Part({
		Name = "Anchor",
		Size = Vector3.new(0.2, 0.2, 0.2),
		CFrame = CFrame.new(position),
		Color = P.White,
		Transparency = 1,
		Parent = parent,
	})
end

-- An enemy soldier falls (from wherever it is now) and fades out.
local function killRig(rig: Rig, duration: number)
	local down = rig.pivot * CFrame.Angles(math.rad(85), 0, 0)
	for i, part in rig.parts do
		Theme.Tween(
			part,
			{ CFrame = down * rig.offsets[i], Transparency = 1 },
			duration,
			Enum.EasingStyle.Quad,
			Enum.EasingDirection.In
		)
	end
	task.delay(duration + 0.1, function()
		rig.model:Destroy()
	end)
end

local function crumble(model: Model, duration: number)
	for _, item in model:GetDescendants() do
		if item:IsA("BasePart") then
			local push = Vector3.new(math.random(-30, 30) / 10, -2.5, -math.random(10, 40) / 10)
			local spin = CFrame.Angles(math.random() * 2, math.random() * 2, math.random() * 2)
			Theme.Tween(
				item,
				{ CFrame = (item.CFrame + push) * spin, Transparency = 1 },
				duration,
				Enum.EasingStyle.Quad,
				Enum.EasingDirection.In
			)
		end
	end
	task.delay(duration + 0.1, function()
		model:Destroy()
	end)
end

local function flyAway(model: Model, offset: Vector3, duration: number)
	for _, item in model:GetDescendants() do
		if item:IsA("BasePart") then
			Theme.Tween(item, { CFrame = item.CFrame + offset, Transparency = 1 }, duration)
		end
	end
	task.delay(duration + 0.1, function()
		model:Destroy()
	end)
end

-- Text of a gate panel without the toll cash ("-4  +$20" -> "-4"); the cash
-- part gets its own label that is revealed later.
local function gateText(option: Option): string
	local text = option.label
	if (option.cash or 0) > 0 then
		local stripped = text:gsub("%s*%+%$.*$", "")
		if stripped ~= "" then
			text = stripped
		end
	end
	if option.tone == "weapon" and option.op == "weapon" then
		text = "🔫 " .. text
	end
	return text
end

-- Soldiers shown as killed (of the `shown` models) for `kills` of `enemies`.
local function shownKills(fight: Fight?, shown: number): number
	if not fight or shown <= 0 or fight.enemies <= 0 or fight.kills <= 0 then
		return 0
	end
	if fight.kills >= fight.enemies then
		return shown
	end
	local k = math.clamp(math.floor(fight.kills / fight.enemies * shown + 0.5), 1, shown)
	if k >= shown then
		k = shown - 1 -- some survived: keep at least one standing
	end
	return k
end

-------------------------------------------------------------------------------
-- Types
-------------------------------------------------------------------------------
export type Fight = { enemies: number, kills: number, share: number, destroyed: boolean }

export type BossCallbacks = {
	onProgress: (alive: number, hp: number) -> (),
	onResolved: (victory: boolean) -> (),
	onDone: () -> (),
}

type EnemyRig = { rig: Rig, fall: number, target: number }

type OptionVisual = {
	option: Option,
	centre: Vector3,
	panel: Part?,
	label: TextLabel?,
	model: Model?,
	enemies: { EnemyRig },
	targets: { Vector3 },
	hp: HpBar?,
	anchor: Part?,
	cashLabel: BillboardGui?, -- hidden until the previous row is crossed
}

type RowVisual = { row: Row, options: { OptionVisual }, revealed: boolean, crossed: boolean }

type Engagement = { lane: number, fight: Fight? }

type BossShot = { time: number, killIndex: number }

type BossFight = {
	outcome: BossOutcome,
	callbacks: BossCallbacks,
	clock: number, -- seconds since StartBossFight
	fightStart: number, -- clock value when the fight itself starts (t = 0)
	shots: { BossShot },
	nextShot: number,
	resolved: boolean,
	done: boolean,
}

export type Visuals = {
	fortDistance: number,
	stopDistance: number,
	SetSquad: (self: Visuals, troops: number, weaponTier: number) -> (),
	Update: (self: Visuals, dt: number, root: BasePart?, moving: boolean) -> (),
	Engage: (self: Visuals, rowIndex: number, lane: number, fight: Fight?) -> (),
	RowCrossed: (
		self: Visuals,
		rowIndex: number,
		lane: number,
		outcome: RowOutcome?,
		capacity: number,
		fight: Fight?
	) -> (),
	OpenCastle: (self: Visuals, maxHp: number) -> number,
	StartBossFight: (self: Visuals, outcome: BossOutcome, callbacks: BossCallbacks) -> number,
	BossFocus: (self: Visuals) -> Vector3?,
	Destroy: (self: Visuals) -> (),
}

local RunnerVisuals = {}

function RunnerVisuals.new(course: Course, slot: number, character: Model): Visuals
	local folder = Instance.new("Folder")
	folder.Name = "ArmyRunnerVisuals"

	local floorY = Layout.FLOOR_Y
	local halfTrack = Layout.RUNNER_TRACK_WIDTH / 2
	local centreX = Layout.RunnerPoint(slot, 0, 0).X
	local startZ = Layout.RunnerPoint(slot, 0, 0).Z
	local destroyed = false

	local function worldAt(distance: number, lateral: number, height: number?): Vector3
		return Layout.RunnerPoint(slot, distance, lateral) + Vector3.new(0, height or 0, 0)
	end

	---------------------------------------------------------------------------
	-- Rows
	---------------------------------------------------------------------------
	local rowVisuals: { RowVisual } = {}

	local function buildGateRow(row: Row, rowFolder: Folder): { OptionVisual }
		local list: { OptionVisual } = {}
		local edges: { number } = {}
		local function addEdge(x: number)
			for _, e in edges do
				if math.abs(e - x) < 0.6 then
					return
				end
			end
			table.insert(edges, x)
		end
		for lane, option in row.options do
			local width = math.max(2, option.width)
			local centre = worldAt(row.distance, option.x, GATE_HEIGHT / 2 + 0.2)
			local panel = RunnerModels.Part({
				Name = "GatePanel" .. lane,
				Size = Vector3.new(width - 0.5, GATE_HEIGHT, 0.3),
				CFrame = CFrame.new(centre),
				Color = toneColor(option),
				Transparency = 0.45,
				Parent = rowFolder,
			})
			panel.CastShadow = false
			-- The runner travels -Z, so the face it sees is the +Z face = Back.
			local label = RunnerModels.SurfaceLabel(panel, Enum.NormalId.Back, gateText(option), 160)
			addEdge(option.x - width / 2)
			addEdge(option.x + width / 2)
			-- A red "toll" gate also carries cash: a small stack behind it whose
			-- value label stays hidden until the previous row is crossed.
			local tollCash: Model? = nil
			local cashLabel: BillboardGui? = nil
			local cash = option.cash or 0
			if cash > 0 then
				local base = worldAt(row.distance + 2.5, option.x, 0)
				local stack = RunnerModels.CoinStack(rowFolder, base, 4)
				tollCash = stack
				local anchor = anchorPart(stack, base + Vector3.new(0, 3.2, 0))
				local gui = makeTextBillboard(anchor, "+" .. Format.Money(cash), P.MoneyGreen, 0.6, P.PillBlack)
				gui.Enabled = false
				cashLabel = gui
			end
			table.insert(list, {
				option = option,
				centre = centre,
				panel = panel,
				label = label,
				model = tollCash,
				enemies = {},
				targets = {},
				hp = nil,
				anchor = nil,
				cashLabel = cashLabel,
			})
		end
		for _, x in edges do
			RunnerModels.Part({
				Name = "GatePost",
				Size = Vector3.new(0.7, POST_HEIGHT, 0.7),
				CFrame = CFrame.new(worldAt(row.distance, x, POST_HEIGHT / 2)),
				Color = P.GatePost,
				Parent = rowFolder,
			})
		end
		return list
	end

	local function buildCashRow(row: Row, rowFolder: Folder): { OptionVisual }
		local list: { OptionVisual } = {}
		local biggest = 0
		for _, option in row.options do
			biggest = math.max(biggest, option.cash or 0)
		end
		for _, option in row.options do
			local cash = option.cash or 0
			local coins = if biggest > 0 then math.ceil(cash / biggest * 7) else 1
			local base = worldAt(row.distance, option.x, 0)
			local model = RunnerModels.CoinStack(rowFolder, base, coins)
			local anchor = anchorPart(model, base + Vector3.new(0, 3.6, 0))
			local gui = makeTextBillboard(anchor, Format.Money(cash), P.PriceGold, 1.2, nil)
			gui.Enabled = false -- revealed when the previous row is crossed
			table.insert(list, {
				option = option,
				centre = base,
				panel = nil,
				label = nil,
				model = model,
				enemies = {},
				targets = {},
				hp = nil,
				anchor = anchor,
				cashLabel = gui,
			})
		end
		return list
	end

	local function buildSquadRow(row: Row, rowFolder: Folder): { OptionVisual }
		local list: { OptionVisual } = {}
		for _, option in row.options do
			local count = math.max(0, math.floor(option.enemies or 0))
			local shown = math.min(count, MAX_ENEMY_MODELS)
			local width = math.max(4, option.width - 1)
			local perRow = math.max(1, math.min(shown, math.floor(width / 1.7)))
			local rigs: { EnemyRig } = {}
			local model = Instance.new("Model")
			model.Name = "EnemySquad"
			model.Parent = rowFolder
			for i = 1, shown do
				local col = (i - 1) % perRow
				local line = math.floor((i - 1) / perRow)
				local x = option.x + (col - (perRow - 1) / 2) * 1.7
				local pos = worldAt(row.distance - 1 - line * 1.6, x, 0)
				-- Enemies face the runner (+Z): rotate 180 degrees.
				local pivot = CFrame.new(pos) * CFrame.Angles(0, math.pi, 0)
				local rig = RunnerModels.Soldier("enemy", model, pivot)
				table.insert(rigs, { rig = rig, fall = 0, target = 0 })
			end
			local anchor = anchorPart(model, worldAt(row.distance, option.x, 4.5))
			makeTextBillboard(
				anchor,
				if option.label ~= "" then option.label else tostring(count),
				P.White,
				0.8,
				P.EnemyRed
			)
			table.insert(list, {
				option = option,
				centre = worldAt(row.distance, option.x, 0),
				panel = nil,
				label = nil,
				model = model,
				enemies = rigs,
				targets = {},
				hp = nil,
				anchor = anchor,
				cashLabel = nil,
			})
		end
		return list
	end

	local function buildBarricadeRow(row: Row, rowFolder: Folder): { OptionVisual }
		local list: { OptionVisual } = {}
		for _, option in row.options do
			local centre = worldAt(row.distance, option.x, 0)
			local width = math.max(4, option.width - 0.4)
			local model = RunnerModels.Sandbags(rowFolder, centre, width)
			local anchor = anchorPart(model, centre + Vector3.new(0, 4.4, 0))
			local hp = makeHpBar(anchor, "HP " .. Format.Short(math.floor((option.hp or 0) + 0.5)), 150, 1.4)
			hp.set(1)
			local targets = {
				centre + Vector3.new(-width / 4, 1.5, 0.7),
				centre + Vector3.new(0, 2.2, 0.7),
				centre + Vector3.new(width / 4, 1.2, 0.7),
			}
			table.insert(list, {
				option = option,
				centre = centre,
				panel = nil,
				label = nil,
				model = model,
				enemies = {},
				targets = targets,
				hp = hp,
				anchor = anchor,
				cashLabel = nil,
			})
		end
		return list
	end

	for index, row in course.rows do
		local rowFolder = Instance.new("Folder")
		rowFolder.Name = ("Row%02d_%s"):format(index, row.kind)
		rowFolder.Parent = folder
		local options: { OptionVisual }
		if row.kind == "cash" then
			options = buildCashRow(row, rowFolder)
		elseif row.kind == "squad" then
			options = buildSquadRow(row, rowFolder)
		elseif row.kind == "barricade" then
			options = buildBarricadeRow(row, rowFolder)
		else
			options = buildGateRow(row, rowFolder)
		end
		rowVisuals[index] = { row = row, options = options, revealed = false, crossed = false }
	end

	-- Cash a weapon gate pays at the top tier ("Weapon already maxed: bonus
	-- cash" in RunnerSim: the top weapon's value x the course's value scale).
	local maxWeaponCash = 0
	do
		local ok, scale = pcall(RunnerCourse.ValueScale, course.area, course.level)
		local top = RunnerDefs.WEAPONS[RunnerDefs.MAX_WEAPON_TIER]
		if ok and type(scale) == "number" and top then
			maxWeaponCash = Format.RoundWhole(top.value * scale)
		end
	end

	-- What a gold weapon gate gives when crossed with weapon tier `tierNow`.
	local function weaponGateText(option: Option, tierNow: number): string
		local maxTier = RunnerDefs.MAX_WEAPON_TIER
		if tierNow >= maxTier then
			return "🔫 MAX +" .. Format.Money(maxWeaponCash)
		end
		local step = math.max(1, math.floor(option.amount or 1))
		local def = RunnerDefs.WEAPONS[math.min(maxTier, tierNow + step)]
		return "🔫 " .. (if def then def.short else option.label)
	end

	-- Show a row's cash values (owner request: only once the row before it is
	-- crossed) and name the gun its weapon gates give with the weapon tier the
	-- squad has now (`tierNow`).
	local function reveal(index: number, tierNow: number)
		local rowVisual = rowVisuals[index]
		if not rowVisual or rowVisual.revealed then
			return
		end
		rowVisual.revealed = true
		for _, ov in rowVisual.options do
			local label = ov.label
			if label and ov.option.op == "weapon" then
				label.Text = weaponGateText(ov.option, tierNow)
				Theme.Pop(label)
			end
			local gui = ov.cashLabel
			if gui then
				gui.Enabled = true
				local holder = gui:FindFirstChildWhichIsA("GuiObject")
				if holder then
					Theme.Pop(holder)
				end
			end
		end
	end
	reveal(1, 1) -- every run starts with the pistol (tier 1)

	---------------------------------------------------------------------------
	-- The castle. It stands on the server's finish plaza (which starts at the
	-- end of the track), never closer than the course's boss distance.
	---------------------------------------------------------------------------
	local trackEnd = Layout.RUNNER_TRACK_LENGTH
	local fortDistance = math.clamp(course.boss.distance, trackEnd + 6, trackEnd + 30)
	local fortCentre = worldAt(fortDistance + FORT_DEPTH / 2, 0, 0)
	local fortModel, _fortTargets, fortDoor = RunnerModels.Fort(folder, fortCentre)
	local stopDistance = math.max(course.boss.distance + 2, fortDistance - SQUAD_GAP)

	---------------------------------------------------------------------------
	-- Your squad (pooled toy soldiers in formation)
	---------------------------------------------------------------------------
	local squadFolder = Instance.new("Folder")
	squadFolder.Name = "Squad"
	squadFolder.Parent = folder
	local rigs: { Rig } = {}
	local positions: { Vector3 } = {}
	local active: { boolean } = {}
	local dying: { number? } = {} -- seconds since the troop died (death animation)
	for i = 1, MAX_TROOP_MODELS do
		rigs[i] = RunnerModels.Soldier("troop", squadFolder, HIDDEN, 1)
		positions[i] = HIDDEN.Position
		active[i] = false
		dying[i] = nil
	end

	local troops = 0 -- soldiers to draw next to the avatar (the run's troops - 1)
	local tier = 1
	local bobClock = 0
	local lastRoot: BasePart? = nil

	---------------------------------------------------------------------------
	-- Guns and bullets
	---------------------------------------------------------------------------
	local pool = RunnerGuns.newPool(folder)
	local avatarGun: Model? = nil
	local avatarGunTier = 0
	local avatarGunHandle: BasePart? = nil
	local fireClocks: { number } = {}
	for i = 0, MAX_TROOP_MODELS do
		fireClocks[i + 1] = math.random() -- random phase so units do not fire in sync
	end
	local shotCounter = 0

	local function currentHandle(): BasePart?
		local tool = character:FindFirstChild("RunnerGun")
		local handle = tool and tool:FindFirstChild("Handle")
		if handle and handle:IsA("BasePart") then
			return handle
		end
		return nil
	end

	local function refreshAvatarGun()
		local handle = currentHandle()
		if not handle then
			return
		end
		local gun = avatarGun
		if gun and gun.Parent == handle and avatarGunTier == tier then
			return
		end
		if gun then
			gun:Destroy()
		end
		avatarGun = RunnerGuns.AttachToHandle(handle, tier)
		avatarGunTier = tier
		avatarGunHandle = handle
	end

	local function avatarMuzzle(root: BasePart): Vector3
		local handle = avatarGunHandle
		if handle and handle.Parent and avatarGun and avatarGun.Parent == handle then
			return RunnerGuns.HandleMuzzle(handle, tier)
		end
		return (root.CFrame * CFrame.new(1, 0.4, -1.8)).Position
	end

	---------------------------------------------------------------------------
	-- Engagements (rows being shot at) and the boss fight
	---------------------------------------------------------------------------
	local engaged: { [number]: Engagement } = {}
	local boss: Boss? = nil
	local fight: BossFight? = nil
	local castleOpen = false
	local castleClock = 0 -- seconds since OpenCastle (the intro is over at BOSS_INTRO_SECONDS)
	local knockMotor: Motor6D? = nil
	local knockOriginal: CFrame? = nil
	local knockT = 0
	local knockTarget: CFrame? = nil

	local function aliveTroops(): number
		-- Units drawn next to the avatar (the avatar itself is unit 1 in the boss fight).
		local f = fight
		if f and f.clock >= f.fightStart then
			local t = f.clock - f.fightStart
			return math.max(0, RunnerSim.AliveAt(f.outcome, t) - 1)
		end
		return troops
	end

	-- Avatar knocked down by the boss: VISUAL ONLY. The body is rotated on the
	-- client through the root joint's C0 (never Humanoid.Health, no physics).
	local function knockDownAvatar()
		if knockMotor then
			return
		end
		local root = character:FindFirstChild("HumanoidRootPart")
		if not root or not root:IsA("BasePart") then
			return
		end
		for _, item in character:GetDescendants() do
			if item:IsA("Motor6D") and item.Part0 == root then
				knockMotor = item
				knockOriginal = item.C0
				local drop = math.max(0, root.Position.Y - floorY - 0.8)
				knockTarget = CFrame.new(0, -drop, 0) * CFrame.Angles(math.rad(88), 0, 0) * item.C0
				knockT = 0
				break
			end
		end
	end

	local function restoreAvatar()
		local motor = knockMotor
		local original = knockOriginal
		knockMotor = nil
		knockOriginal = nil
		knockTarget = nil
		if motor and original and motor.Parent then
			motor.C0 = original
		end
	end

	---------------------------------------------------------------------------
	-- Methods
	---------------------------------------------------------------------------
	local self = {} :: Visuals
	self.fortDistance = fortDistance
	self.stopDistance = stopDistance

	function self.SetSquad(_self: Visuals, newTroops: number, newTier: number)
		-- `newTroops` is the run's troop count INCLUDING the avatar (RunnerSim
		-- counts the avatar as 1 of the troops), so we draw troops - 1 soldiers.
		troops = math.max(0, math.floor(newTroops) - 1)
		local clean = math.clamp(math.floor(newTier), 1, RunnerDefs.MAX_WEAPON_TIER)
		if clean ~= tier then
			tier = clean
			for _, rig in rigs do
				RunnerModels.SetRigGun(rig, tier)
			end
		end
	end

	-- Points to shoot at right now (nearest engaged row, or the boss).
	local function currentTargets(): ({ Vector3 }, boolean)
		local b = boss
		local f = fight
		if b and f and f.clock >= f.fightStart and not f.resolved then
			return b:AimPoints(), true
		end
		local best: number? = nil
		for index in engaged do
			if best == nil or index < best then
				best = index
			end
		end
		if best == nil then
			return {}, false
		end
		local e = engaged[best]
		local rowVisual = rowVisuals[best]
		local ov = rowVisual and rowVisual.options[e.lane]
		if not ov then
			return {}, false
		end
		if #ov.enemies > 0 then
			local list: { Vector3 } = {}
			for _, enemy in ov.enemies do
				if enemy.target < 0.5 then
					table.insert(list, enemy.rig.pivot.Position + Vector3.new(0, 1.6, 0))
				end
			end
			if #list == 0 then
				for _, enemy in ov.enemies do
					table.insert(list, enemy.rig.pivot.Position + Vector3.new(0, 0.6, 0))
				end
			end
			return list, false
		end
		return ov.targets, false
	end

	local function shoot(dt: number, root: BasePart, shown: number)
		local targets, atBoss = currentTargets()
		if #targets == 0 then
			return
		end
		local weapon = RunnerGuns.Weapon(tier)
		-- The avatar (unit 0) and the front-row troops (units 1 .. WIDTH-1) fire
		-- at the gun's true rate. Bullets alive ~ shots/s x flight time (at most
		-- GUN_RANGE / bulletSpeed), so the back rows share what the budget has left.
		local trueRate = math.clamp(weapon.fireRate, 0.1, FRONT_MAX_RATE)
		local flight = GUN_RANGE / math.max(10, weapon.bulletSpeed)
		local shotsBudget = BULLET_BUDGET / math.max(0.05, flight)
		local front = math.min(shown + 1, FORMATION_WIDTH)
		local back = shown + 1 - front
		local backRate = trueRate
		if back > 0 then
			backRate = math.clamp((shotsBudget - front * trueRate) / back, math.min(MIN_BACK_RATE, trueRate), trueRate)
		end
		local avatarDown = knockMotor ~= nil
		for unit = 0, shown do
			local index = unit + 1
			local rate = if unit < FORMATION_WIDTH then trueRate else backRate
			local c = fireClocks[index] + dt * rate
			if c < 1 then
				fireClocks[index] = c
				continue
			end
			fireClocks[index] = math.min(c - 1, 0.99)
			if unit == 0 and avatarDown then
				continue
			end
			local from: Vector3
			if unit == 0 then
				from = avatarMuzzle(root)
				pool:Flash(from, 0.9, P.CoinShine, 0.06) -- muzzle flash: shows the gun's rate
			else
				from = RunnerModels.RigMuzzle(rigs[unit], CFrame.new(positions[unit]))
			end
			shotCounter += 1
			local to = targets[(shotCounter % #targets) + 1]
				+ Vector3.new(math.random(-8, 8) / 10, math.random(-6, 6) / 10, 0)
			local offset = to - from
			local impact = true
			if offset.Magnitude > GUN_RANGE then
				to = from + offset.Unit * GUN_RANGE -- out of range: the bullet fades out
				impact = false
			end
			if pool:Fire(from, to, tier, impact) and impact and atBoss then
				local b = boss
				if b then
					b:Hit()
				end
			end
		end
	end

	-- Enemies of engaged squads fall over while the squad closes in; the
	-- barricade HP bar drains. Progress = share of the last GUN_RANGE studs run.
	local function updateEngagements(dt: number, distance: number, parts: { BasePart }, cframes: { CFrame })
		for index, e in engaged do
			local rowVisual = rowVisuals[index]
			if not rowVisual then
				continue
			end
			local row = rowVisual.row
			local progress = math.clamp((distance - (row.distance - GUN_RANGE)) / GUN_RANGE, 0, 1)
			for lane, ov in rowVisual.options do
				local targeted = lane == e.lane
				if #ov.enemies > 0 then
					local down = if targeted then math.floor(shownKills(e.fight, #ov.enemies) * progress + 1e-6) else 0
					for i, enemy in ov.enemies do
						enemy.target = if i <= down then 1 else 0
						if math.abs(enemy.fall - enemy.target) > 0.001 then
							local speed = if enemy.target > enemy.fall then 5 else 3
							local step = math.clamp(enemy.target - enemy.fall, -speed * dt, speed * dt)
							enemy.fall += step
							RunnerModels.RigCFrames(
								enemy.rig,
								enemy.rig.pivot * CFrame.Angles(math.rad(85) * enemy.fall, 0, 0),
								parts,
								cframes
							)
						end
					end
				end
				local hp = ov.hp
				if hp then
					local share = if targeted and e.fight then e.fight.share else 0
					hp.set(1 - math.clamp(share, 0, 1) * (if targeted then progress else 0))
				end
			end
		end
	end

	-- Boss fight timeline (see StartBossFight).
	local function updateBoss(dt: number, root: BasePart?)
		local f = fight
		local b = boss
		if not b then
			return
		end
		castleClock += dt
		if not f then
			b:Update(dt) -- intro: walking out, waiting for the fight's outcome
			return
		end
		f.clock += dt
		local outcome = f.outcome
		local t = f.clock - f.fightStart
		if t >= 0 then
			local tc = math.min(t, outcome.duration)
			b:SetHp(RunnerSim.BossHpAt(outcome, tc))
			if not f.resolved then
				f.callbacks.onProgress(RunnerSim.AliveAt(outcome, tc), RunnerSim.BossHpAt(outcome, tc))
			end
			-- Boss shots: one per kill, timed to land when the unit dies.
			while f.nextShot <= #f.shots and t >= f.shots[f.nextShot].time do
				local shot = f.shots[f.nextShot]
				f.nextShot += 1
				local muzzle = b:Attack()
				local unitsBefore = outcome.units - (shot.killIndex - 1)
				local troopIndex = unitsBefore - 1 -- 0 = the avatar
				local target: Vector3
				if troopIndex <= 0 or not root then
					target = if root then root.Position else b:Chest()
				elseif troopIndex <= MAX_TROOP_MODELS then
					target = positions[troopIndex] + Vector3.new(0, 1.4, 0)
				else
					-- A troop beyond the drawn 40: aim at the back of the formation.
					target = positions[math.min(MAX_TROOP_MODELS, math.max(1, aliveTroops()))] + Vector3.new(0, 1.4, 2)
				end
				pool:Fire(muzzle, target, RunnerDefs.MAX_WEAPON_TIER, true)
				pool:Flash(muzzle, 3, P.EnemyRed, 0.2)
			end
			if t >= outcome.duration and not f.resolved then
				f.resolved = true
				local victory = outcome.victory
				if victory then
					b:Die()
					local chest = b:Chest()
					for i = 0, 5 do
						task.delay(i * 0.18, function()
							if not destroyed then
								pool:Flash(
									chest + Vector3.new(math.random(-30, 30) / 10, math.random(-30, 30) / 10, 0),
									6 + i,
									if i % 2 == 0 then P.CardOrange else P.Coin,
									0.4
								)
							end
						end)
					end
					-- Confetti + coins burst.
					local colors = { P.Coin, P.MoneyGreen, P.CardPink, P.CardBlue, P.CardOrange, P.White }
					for i = 1, 36 do
						local piece = RunnerModels.Part({
							Name = "Confetti",
							Size = Vector3.new(0.5, 0.08, 0.35),
							CFrame = CFrame.new(chest + Vector3.new(0, 3, 0)),
							Color = colors[(i % #colors) + 1],
							Parent = folder,
						})
						local goal = chest
							+ Vector3.new(
								math.random(-140, 140) / 10,
								math.random(-40, 60) / 10,
								math.random(-60, 160) / 10
							)
						Theme.Tween(piece, {
							CFrame = CFrame.new(goal) * CFrame.Angles(math.random() * 6, math.random() * 6, 0),
							Transparency = 1,
						}, 1.6 + math.random() * 0.6)
						task.delay(2.4, function()
							piece:Destroy()
						end)
					end
					for i = 1, 12 do
						local coin = RunnerModels.Part({
							Name = "CoinBurst",
							Shape = Enum.PartType.Cylinder,
							Size = Vector3.new(0.3, 1.6, 1.6),
							CFrame = CFrame.new(chest) * CFrame.Angles(0, 0, math.rad(90)),
							Color = P.Coin,
							Parent = folder,
						})
						local goal = (if root then root.Position else chest)
							+ Vector3.new(math.random(-60, 60) / 10, 4 + math.random() * 3, math.random(-20, 20) / 10)
						task.delay(i * 0.05, function()
							if coin.Parent then
								Theme.Tween(coin, { CFrame = CFrame.new(goal), Transparency = 0.2 }, 0.7)
							end
						end)
						task.delay(1.4, function()
							if coin.Parent then
								Theme.Tween(coin, { Transparency = 1 }, 0.3)
							end
						end)
						task.delay(1.8, function()
							coin:Destroy()
						end)
					end
					Theme.FloatText(chest + Vector3.new(0, 6, 4), "BOSS DEFEATED!", P.Coin)
				else
					knockDownAvatar()
					b:Roar()
				end
				f.callbacks.onResolved(victory)
			end
		end
		if f.resolved and not f.done and t >= outcome.duration + BOSS_OUTRO_SECONDS then
			f.done = true
			f.callbacks.onDone()
		end
		b:Update(dt)
	end

	function self.Update(_self: Visuals, dt: number, root: BasePart?, moving: boolean)
		if destroyed then
			return
		end
		bobClock += dt
		local parts: { BasePart } = {}
		local cframes: { CFrame } = {}
		local shown = math.min(aliveTroops(), MAX_TROOP_MODELS)
		local alpha = 1 - math.exp(-dt * 10)
		if root then
			lastRoot = root
			refreshAvatarGun()
			local rootPosition = root.CFrame.Position
			local base = Vector3.new(rootPosition.X, floorY, rootPosition.Z)
			-- Near a track edge the whole formation moves over by whole columns
			-- (the same shift for every troop), so no two troops share a spot and
			-- the avatar's own spot in the front row stays free.
			local half = math.floor(FORMATION_WIDTH / 2)
			local minX = centreX - halfTrack + 0.8
			local maxX = centreX + halfTrack - 0.8
			local lo = math.ceil((minX - (base.X - half * SPACING_X)) / SPACING_X - 1e-6)
			local hi = math.floor((maxX - (base.X + half * SPACING_X)) / SPACING_X + 1e-6)
			local shift = if lo <= hi then math.clamp(0, lo, hi) else 0
			shift = math.clamp(shift, -half, half)
			for i = 1, MAX_TROOP_MODELS do
				local rig = rigs[i]
				if i <= shown then
					if not active[i] or dying[i] then
						if not active[i] then
							positions[i] = base -- new troops pop out of the avatar
						end
						active[i] = true
						dying[i] = nil
					end
					local col, line = slotOf(i)
					col += shift
					if line == 0 and col == 0 then
						col = shift -- the avatar stands here: take the slot it left free
					end
					local target = Vector3.new(base.X + col * SPACING_X, floorY, base.Z + line * SPACING_Z)
					positions[i] = positions[i]:Lerp(target, alpha)
					local bob = if moving then math.abs(math.sin(bobClock * 13 + i * 0.7)) * 0.35 else 0
					RunnerModels.RigCFrames(rig, CFrame.new(positions[i] + Vector3.new(0, bob, 0)), parts, cframes)
				elseif active[i] then
					-- Died: fall backwards and sink, then hide.
					local d = (dying[i] or 0) + dt
					if d >= DEATH_TIME then
						active[i] = false
						dying[i] = nil
						RunnerModels.RigCFrames(rig, HIDDEN, parts, cframes)
					else
						dying[i] = d
						local a = d / DEATH_TIME
						local pivot = CFrame.new(positions[i] - Vector3.new(0, a * 1.2, 0))
							* CFrame.Angles(math.rad(85) * math.min(1, a * 1.6), 0, 0)
						RunnerModels.RigCFrames(rig, pivot, parts, cframes)
					end
				end
			end
			local distance = startZ - rootPosition.Z
			updateEngagements(dt, distance, parts, cframes)
			shoot(dt, root, shown)
		end

		if #parts > 0 then
			Workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
		end
		pool:Update(dt)
		updateBoss(dt, root)

		-- Avatar knock-down pose (defeat).
		local motor = knockMotor
		local original = knockOriginal
		local goal = knockTarget
		if motor and original and goal and knockT < 1 then
			knockT = math.min(1, knockT + dt / 0.5)
			local eased = 1 - (1 - knockT) * (1 - knockT)
			motor.C0 = original:Lerp(goal, eased)
		end
	end

	function self.Engage(_self: Visuals, rowIndex: number, lane: number, newFight: Fight?)
		local rowVisual = rowVisuals[rowIndex]
		if not rowVisual or rowVisual.crossed or not rowVisual.options[lane] then
			return
		end
		engaged[rowIndex] = { lane = lane, fight = newFight }
	end

	function self.RowCrossed(
		_self: Visuals,
		rowIndex: number,
		lane: number,
		outcome: RowOutcome?,
		capacityNow: number,
		rowFight: Fight?
	)
		local rowVisual = rowVisuals[rowIndex]
		if not rowVisual or destroyed then
			return
		end
		rowVisual.crossed = true
		engaged[rowIndex] = nil
		-- The next row's cash values appear now; its weapon gates name the gun
		-- for the tier we have after THIS row (SetSquad updates `tier` later).
		reveal(rowIndex + 1, if outcome then outcome.weaponTier else tier)
		local optionVisual = rowVisual.options[lane]
		if not optionVisual then
			return
		end
		local kind = rowVisual.row.kind
		local popAt = optionVisual.centre + Vector3.new(0, 7, -2)
		local troopDelta = if outcome then outcome.troopsAfter - outcome.troopsBefore else 0
		local cashDelta = if outcome then outcome.cashAfter - outcome.cashBefore else 0
		local tierBefore = tier

		if kind == "gate" then
			local panel = optionVisual.panel
			if panel then
				-- Flash bright, then fade away like the Count-Masters gates.
				panel.Transparency = 0.1
				Theme.Tween(panel, { Transparency = 1 }, 0.45)
				local label = optionVisual.label
				if label then
					Theme.Tween(label, { TextTransparency = 1 }, 0.45)
					local stroke = label:FindFirstChildOfClass("UIStroke")
					if stroke then
						Theme.Tween(stroke, { Transparency = 1 }, 0.45)
					end
				end
			end
			local tollCash = optionVisual.model
			if tollCash then
				flyAway(tollCash, Vector3.new(0, 6, 4), 0.4)
				optionVisual.model = nil
				if cashDelta > 0.001 then
					Theme.FloatText(popAt + Vector3.new(0, 2.5, 0), "+" .. Format.Money(cashDelta), P.MoneyGreen)
				end
			end
			-- Weapon gates get their own "🔫 Rifle!" pop below instead.
			if optionVisual.option.op ~= "weapon" then
				local good = toneColor(optionVisual.option) ~= P.GateNegative
				Theme.FloatText(popAt, gateText(optionVisual.option), if good then P.MoneyGreen else P.ButtonRed)
			end
		elseif kind == "cash" then
			local model = optionVisual.model
			if model then
				flyAway(model, Vector3.new(0, 6, 4), 0.4)
				optionVisual.model = nil
			end
			if cashDelta > 0.001 then
				Theme.FloatText(popAt, "+" .. Format.Money(cashDelta), P.MoneyGreen)
			end
			local offered = optionVisual.option.cash or 0
			if outcome and offered > cashDelta + 0.5 and capacityNow > 0 and outcome.cashAfter >= capacityNow - 0.5 then
				Theme.FloatText(popAt + Vector3.new(0, 2.5, 0), "CAPACITY FULL!", P.Coin)
			end
		elseif kind == "squad" then
			for laneIndex, ov in rowVisual.options do
				if laneIndex ~= lane then
					-- Not fought: stand back up.
					for _, enemy in ov.enemies do
						enemy.fall, enemy.target = 0, 0
						local parts: { BasePart } = {}
						local cframes: { CFrame } = {}
						RunnerModels.RigCFrames(enemy.rig, enemy.rig.pivot, parts, cframes)
						Workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
					end
				end
			end
			local killed = shownKills(rowFight, #optionVisual.enemies)
			local root = lastRoot
			for i, enemy in optionVisual.enemies do
				local rig = enemy.rig
				if i <= killed then
					task.delay(i * 0.02, function()
						if rig.model.Parent then
							killRig(rig, 0.35)
						end
					end)
				else
					-- Survivors charge into your squad (each costs one troop).
					local towards = if root then root.Position - rig.pivot.Position else Vector3.new(0, 0, 6)
					flyAway(rig.model, Vector3.new(towards.X, 0, towards.Z) * 0.8, 0.35)
				end
			end
			optionVisual.enemies = {}
			if optionVisual.anchor then
				optionVisual.anchor:Destroy()
				optionVisual.anchor = nil
			end
			if rowFight and rowFight.kills > 0 then
				Theme.FloatText(popAt + Vector3.new(0, 2, 0), "💥 " .. rowFight.kills .. " down!", P.Coin)
			end
		elseif kind == "barricade" then
			local broken = if outcome then outcome.destroyed == true else (rowFight ~= nil and rowFight.destroyed)
			local hp = optionVisual.hp
			if broken then
				if hp then
					hp.set(0, 0.15)
				end
				local model = optionVisual.model
				if model then
					task.delay(0.1, function()
						if model.Parent then
							crumble(model, 0.6)
						end
					end)
					optionVisual.model = nil
				end
				pool:Flash(optionVisual.centre + Vector3.new(0, 2, 0), 6, P.CardOrange, 0.35)
				Theme.FloatText(popAt, "SMASHED!", P.Coin)
			else
				if hp then
					local share = if rowFight then rowFight.share else 0
					hp.set(1 - math.clamp(share, 0, 1), 0.15)
				end
				Theme.FloatText(popAt, "TOO STRONG!", P.ButtonRed)
			end
		end

		if troopDelta ~= 0 and kind ~= "gate" then
			local text = if troopDelta > 0 then "+" .. troopDelta .. " 🪖" else tostring(troopDelta) .. " 🪖"
			Theme.FloatText(popAt + Vector3.new(0, -2, 0), text, if troopDelta > 0 then P.MoneyGreen else P.ButtonRed)
		end
		if outcome and outcome.weaponTier > tierBefore then
			Theme.FloatText(
				popAt + Vector3.new(0, 3, 0),
				"🔫 " .. RunnerGuns.Weapon(outcome.weaponTier).name .. "!",
				P.Coin
			)
		end
		-- A weapon gate at the top tier pays cash instead ("Weapon already maxed").
		local maxedWeapon = kind == "gate"
			and optionVisual.option.op == "weapon"
			and outcome ~= nil
			and outcome.weaponTier <= tierBefore
		if maxedWeapon and outcome and outcome.note and outcome.note ~= "" then
			Theme.FloatText(popAt + Vector3.new(0, 5, 0), outcome.note, P.Coin)
		end
	end

	local function bossPivot(distance: number): CFrame
		local p = worldAt(distance, 0, 0)
		return CFrame.lookAt(p, p + Vector3.new(0, 0, 1)) -- faces the runner (+Z)
	end

	-- Boss intro (RunnerDefs.BOSS_INTRO_SECONDS in total):
	--   0 .. BOSS_DOOR_TIME      the castle door sinks into the ground; the boss
	--                            waits hidden inside the gateway (no HP bar yet)
	--   then .. intro end        it appears, its HP bar switches on, and it walks
	--                            out to BOSS_STAND studs in front of the castle
	function self.OpenCastle(_self: Visuals, maxHp: number): number
		if destroyed then
			return 0
		end
		if castleOpen then
			return math.max(0, BOSS_INTRO_SECONDS - castleClock)
		end
		castleOpen = true
		castleClock = 0
		engaged = {}
		local sink = fortDoor:GetExtentsSize().Y + 0.2
		for _, item in fortDoor:GetDescendants() do
			if item:IsA("BasePart") then
				Theme.Tween(
					item,
					{ CFrame = item.CFrame - Vector3.new(0, sink, 0) },
					BOSS_DOOR_TIME,
					Enum.EasingStyle.Quad
				)
			end
		end
		local b = RunnerBoss.new(folder, bossPivot(fortDistance + BOSS_WAIT_DEPTH), maxHp)
		boss = b
		task.delay(BOSS_DOOR_TIME, function()
			if boss == b and not destroyed then
				b:Show()
				b:WalkTo(bossPivot(fortDistance - BOSS_STAND), math.max(0.3, BOSS_INTRO_SECONDS - BOSS_DOOR_TIME))
			end
		end)
		Theme.FloatText(worldAt(fortDistance - 2, 0, 21), "👹 THE BOSS!", P.ButtonRed)
		return BOSS_INTRO_SECONDS
	end

	-- Plays the boss fight exactly as the BossOutcome says (RunnerController
	-- passes the server's):
	--   intro (OpenCastle)       if it is still playing, the fight waits for it
	--   fight t = 0 .. duration  everyone shoots it, its bar follows BossHpAt(t);
	--                            each kill k lands at k x interval (troops from the
	--                            back rows first, the avatar last)
	--   then BOSS_OUTRO_SECONDS  victory (boss falls, confetti, coins) or defeat
	--                            (avatar knocked down, boss roars), then onDone()
	function self.StartBossFight(_self: Visuals, outcome: BossOutcome, callbacks: BossCallbacks): number
		if destroyed or fight then
			return 0
		end
		self.OpenCastle(self, outcome.hp) -- no-op when the intro already started
		local b = boss
		if b then
			b:SetMaxHp(outcome.hp)
			b:SetHp(outcome.hp)
		end

		-- One boss shot per kill, fired so it lands when the unit dies.
		local shots: { BossShot } = {}
		local speed = RunnerGuns.Weapon(RunnerDefs.MAX_WEAPON_TIER).bulletSpeed
		local travel = math.clamp((SQUAD_GAP - BOSS_STAND) / math.max(10, speed), 0.1, 0.6)
		local lastTime = -math.huge
		for k = 1, outcome.killed do
			local landing = k * outcome.interval
			local fire = math.max(0, landing - travel)
			-- Huge armies kill very fast: draw at most ~6 shots per second.
			if fire - lastTime >= 0.16 or k == outcome.killed then
				table.insert(shots, { time = fire, killIndex = k })
				lastTime = fire
			end
		end
		local wait = math.max(0, BOSS_INTRO_SECONDS - castleClock)
		fight = {
			outcome = outcome,
			callbacks = callbacks,
			clock = 0,
			fightStart = wait,
			shots = shots,
			nextShot = 1,
			resolved = false,
			done = false,
		}
		return wait + outcome.duration + BOSS_OUTRO_SECONDS
	end

	function self.BossFocus(_self: Visuals): Vector3?
		local b = boss
		return if b then b:Chest() else nil
	end

	function self.Destroy(_self: Visuals)
		if destroyed then
			return
		end
		destroyed = true
		restoreAvatar()
		local gun = avatarGun
		avatarGun = nil
		if gun then
			gun:Destroy()
		end
		local b = boss
		boss = nil
		fight = nil
		if b then
			b:Destroy()
		end
		pool:Destroy()
		fortModel:Destroy()
		folder:Destroy()
	end

	folder.Parent = Workspace
	return self
end

return RunnerVisuals
