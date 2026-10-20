--!strict
--[[
	RunnerVisuals (client)
	Draws one army-runner course for the local player only (ARCHITECTURE §5:
	per-player visuals are built on the client). The server already built the
	white track floor; this module adds everything ON the track:

	  * gate rows   - translucent blue / red / gold panels between dark posts,
	                  with huge white outlined labels ("+5", "x2", "-3", "÷2", "RIFLE")
	  * cash rows   - stacks of gold coins + green cash bricks with value labels
	  * enemy squads- red-uniform blocky soldiers with a count label
	  * barricades  - sandbag walls with an HP bar
	  * boss        - the enemy base (fort) with an HP bar
	  * your squad  - up to 40 green toy soldiers following the avatar in a
	                  sunflower "blob" formation, a lime-green money tag showing the
	                  carried cash and a troop-count pill (reference image 2)
	  * effects     - pop text, gate flashes, little yellow bullets, falling enemies

	Public API:
		local visuals = RunnerVisuals.new(course, slot)
		visuals.fortDistance: number      -- distance (studs from start) of the fort's front
		visuals.stopDistance: number      -- where the avatar stops to fight the fort
		visuals:SetSquad(troops, cash, capacity, weaponTier)
		visuals:Update(dt, root: BasePart?, moving: boolean)   -- call every frame
		visuals:Engage(rowIndex, lane)    -- troops start shooting at that row's targets
		visuals:RowCrossed(rowIndex, lane, outcome?, capacity)  -- play the row's effect
		visuals:StartBossFight(duration, fraction)  -- shoot the fort, drain its HP bar
		visuals:Destroy()                 -- removes every instance it created

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
local RunnerModels = require(script.Parent.RunnerModels)

type Course = RunnerCourse.Course
type Option = RunnerCourse.Option
type Row = RunnerCourse.Row
type RowOutcome = RunnerSim.RowOutcome
type Rig = RunnerModels.Rig

local P = Config.Palette

-------------------------------------------------------------------------------
-- Tunables (all visual only)
-------------------------------------------------------------------------------
local MAX_TROOP_MODELS = 40 -- [REQUIREMENT ARCH §0.10] cap rendered troops
local MAX_ENEMY_MODELS = 12 -- per squad lane [PROPOSAL]
local GATE_HEIGHT = 7
local POST_HEIGHT = 8.4
local BULLET_SPEED = 140
local BULLET_POOL = 36
local FIRE_INTERVAL = 0.07
local ENGAGE_TIME = 1.1
local FORT_DEPTH = 14
local HIDDEN = CFrame.new(0, -500, 0)

-- Weapon names for the troop pill (from ECON's RunnerDefs.WEAPONS).
local FALLBACK_WEAPONS = { "Pistol", "Rifle", "SMG", "Assault Rifle", "Minigun", "Rocket" }
local function loadWeaponNames(): { string }
	local names: { string } = {}
	local list = (RunnerDefs :: any).WEAPONS
	if type(list) == "table" then
		for i, entry in list :: { any } do
			if type(entry) == "string" then
				names[i] = entry
			elseif type(entry) == "table" and type(entry.name) == "string" then
				names[i] = entry.name
			end
		end
	end
	return if #names > 0 then names else FALLBACK_WEAPONS
end
local WEAPON_NAMES = loadWeaponNames()

local function weaponName(tier: number): string
	local index = math.clamp(math.floor(tier), 1, #WEAPON_NAMES)
	return WEAPON_NAMES[index] or "Pistol"
end

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

-------------------------------------------------------------------------------
-- Small UI helpers (billboards over world objects)
-------------------------------------------------------------------------------
type HpBar = { set: (fraction: number, duration: number?) -> (), gui: BillboardGui }

local function makeHpBar(adornee: BasePart, title: string, width: number, offsetY: number, alwaysOnTop: boolean): HpBar
	local gui = Theme.Billboard({
		Name = "HpBar",
		Parent = adornee,
		Adornee = adornee,
		Size = UDim2.fromOffset(width, 54),
		StudsOffset = Vector3.new(0, offsetY, 0),
		AlwaysOnTop = alwaysOnTop,
		MaxDistance = if alwaysOnTop then 400 else 160,
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

-- Tip a model over and fade it out (enemy falls, barricade crumbles).
local function knockDown(model: Model, pivot: CFrame, duration: number)
	local tilt = pivot * CFrame.Angles(-math.rad(85), 0, 0)
	for _, item in model:GetDescendants() do
		if item:IsA("BasePart") then
			local relative = pivot:ToObjectSpace(item.CFrame)
			Theme.Tween(
				item,
				{ CFrame = tilt * relative, Transparency = 1 },
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

local function sunflowerOffset(i: number): Vector3
	-- Golden-angle spiral: each new troop lands in the next gap of the blob,
	-- biased a little behind the avatar (+Z is behind when running -Z).
	local angle = i * 2.39996
	local radius = 2.1 + 1.0 * math.sqrt(i)
	return Vector3.new(math.cos(angle) * radius, 0, math.sin(angle) * radius * 0.8 + 1.2)
end

-------------------------------------------------------------------------------
-- The visuals object
-------------------------------------------------------------------------------
type OptionVisual = {
	option: Option,
	centre: Vector3,
	panel: Part?,
	label: TextLabel?,
	model: Model?,
	enemies: { Rig },
	targets: { Vector3 },
	hp: HpBar?,
	anchor: Part?,
}

type RowVisual = { row: Row, options: { OptionVisual } }

type Bullet = { part: Part, from: Vector3, to: Vector3, t: number, duration: number, active: boolean }

export type Visuals = {
	fortDistance: number,
	stopDistance: number,
	SetSquad: (self: Visuals, troops: number, cash: number, capacity: number, weaponTier: number) -> (),
	Update: (self: Visuals, dt: number, root: BasePart?, moving: boolean) -> (),
	Engage: (self: Visuals, rowIndex: number, lane: number) -> (),
	RowCrossed: (self: Visuals, rowIndex: number, lane: number, outcome: RowOutcome?, capacity: number) -> (),
	StartBossFight: (self: Visuals, duration: number, fraction: number) -> (),
	Destroy: (self: Visuals) -> (),
}

local RunnerVisuals = {}

function RunnerVisuals.new(course: Course, slot: number): Visuals
	local folder = Instance.new("Folder")
	folder.Name = "ArmyRunnerVisuals"

	local floorY = Layout.FLOOR_Y
	local halfTrack = Layout.RUNNER_TRACK_WIDTH / 2
	local centreX = Layout.RunnerPoint(slot, 0, 0).X
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
			local text = option.label
			if option.tone == "weapon" and option.op == "weapon" then
				text = "🔫 " .. text
			end
			-- The runner travels -Z, so the face it sees is the +Z face = Back.
			local label = RunnerModels.SurfaceLabel(panel, Enum.NormalId.Back, text, 160)
			addEdge(option.x - width / 2)
			addEdge(option.x + width / 2)
			-- A red "toll" gate also carries cash: show a small stack behind it.
			local tollCash: Model? = nil
			if (option.cash or 0) > 0 then
				tollCash = RunnerModels.CoinStack(rowFolder, worldAt(row.distance + 2.5, option.x, 0), 4)
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
			makeTextBillboard(anchor, option.label ~= "" and option.label or Format.Money(cash), P.PriceGold, 1.2, nil)
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
			local rigs: { Rig } = {}
			local targets: { Vector3 } = {}
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
				table.insert(rigs, rig)
				table.insert(targets, pos + Vector3.new(0, 1.5, 0))
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
				targets = targets,
				hp = nil,
				anchor = anchor,
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
			local hp = makeHpBar(anchor, "HP " .. Format.Short(option.hp or 0), 150, 1.4, false)
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
			})
		end
		return list
	end

	for index, row in course.rows do
		local rowFolder = Instance.new("Folder")
		rowFolder.Name = ("Row%02d_%s"):format(index, row.kind)
		rowFolder.Parent = folder
		local options: { OptionVisual }
		if row.kind == "gate" then
			options = buildGateRow(row, rowFolder)
		elseif row.kind == "cash" then
			options = buildCashRow(row, rowFolder)
		elseif row.kind == "squad" then
			options = buildSquadRow(row, rowFolder)
		elseif row.kind == "barricade" then
			options = buildBarricadeRow(row, rowFolder)
		else
			options = buildGateRow(row, rowFolder)
		end
		rowVisuals[index] = { row = row, options = options }
	end

	---------------------------------------------------------------------------
	-- Boss fort. It stands on the server's finish plaza (which starts at the
	-- end of the track), never closer than the course's boss distance, so the
	-- run always lasts at least RunnerSim.MinDuration. [PROPOSAL]
	---------------------------------------------------------------------------
	local trackEnd = Layout.RUNNER_TRACK_LENGTH
	local fortDistance = math.clamp(course.boss.distance, trackEnd + 6, trackEnd + 30)
	local fortCentre = worldAt(fortDistance + FORT_DEPTH / 2, 0, 0)
	local fortModel, fortTargets = RunnerModels.Fort(folder, fortCentre)
	local fortAnchor = anchorPart(fortModel, fortCentre + Vector3.new(0, 19, 0))
	local fortHp = makeHpBar(fortAnchor, "ENEMY BASE  HP " .. Format.Short(course.boss.hp), 260, 0, true)
	fortHp.set(1)

	---------------------------------------------------------------------------
	-- Your squad (pooled toy soldiers + money tag)
	---------------------------------------------------------------------------
	local squadFolder = Instance.new("Folder")
	squadFolder.Name = "Squad"
	squadFolder.Parent = folder
	local rigs: { Rig } = {}
	local positions: { Vector3 } = {}
	local active: { boolean } = {}
	for i = 1, MAX_TROOP_MODELS do
		rigs[i] = RunnerModels.Soldier("troop", squadFolder, HIDDEN)
		positions[i] = HIDDEN.Position
		active[i] = false
	end

	local tagGui = Instance.new("BillboardGui")
	tagGui.Name = "SquadTag"
	tagGui.Size = UDim2.fromOffset(170, 96)
	tagGui.StudsOffset = Vector3.new(0, 6.2, 0)
	tagGui.AlwaysOnTop = true
	tagGui.LightInfluence = 0
	tagGui.MaxDistance = 400
	tagGui.ResetOnSpawn = false
	tagGui.Parent = folder
	local moneyTag, setMoney = Theme.MoneyTag({
		Text = Format.Money(0),
		Size = UDim2.fromOffset(150, 46),
		Position = UDim2.fromScale(0.5, 0),
		AnchorPoint = Vector2.new(0.5, 0),
		Parent = tagGui,
	})
	local countPill = Instance.new("Frame")
	countPill.Name = "Count"
	countPill.BackgroundColor3 = P.PillBlack
	countPill.BackgroundTransparency = 0.15
	countPill.Size = UDim2.fromOffset(170, 34)
	countPill.Position = UDim2.new(0.5, 0, 0, 52)
	countPill.AnchorPoint = Vector2.new(0.5, 0)
	countPill.Parent = tagGui
	Theme.Corner(countPill, 0.5)
	local countLabel = Theme.Label({
		Text = "🪖 0",
		MaxTextSize = 22,
		Size = UDim2.new(1, -10, 1, -4),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = countPill,
	})

	local troops = 0
	local cash = 0
	local capacity = 0
	local tier = 1
	local bobClock = 0

	---------------------------------------------------------------------------
	-- Bullets
	---------------------------------------------------------------------------
	local bullets: { Bullet } = {}
	for i = 1, BULLET_POOL do
		local part = RunnerModels.Part({
			Name = "Bullet",
			Size = Vector3.new(0.22, 0.22, 0.9),
			CFrame = HIDDEN,
			Color = P.Coin,
			Material = Enum.Material.Neon,
			Parent = folder,
		})
		part.CastShadow = false
		bullets[i] = { part = part, from = Vector3.zero, to = Vector3.zero, t = 0, duration = 0, active = false }
	end
	local nextBullet = 1

	local function fireBullet(from: Vector3, to: Vector3)
		local bullet = bullets[nextBullet]
		nextBullet = nextBullet % BULLET_POOL + 1
		bullet.from = from
		bullet.to = to
		bullet.t = 0
		bullet.duration = math.max(0.05, (to - from).Magnitude / BULLET_SPEED)
		bullet.active = true
	end

	local engageTargets: { Vector3 } = {}
	local engageUntil = 0
	local fireClock = 0

	---------------------------------------------------------------------------
	-- Methods
	---------------------------------------------------------------------------
	local self = {} :: Visuals
	self.fortDistance = fortDistance
	self.stopDistance = fortDistance - 14

	local function refreshTag()
		setMoney(Format.Money(cash))
		local full = capacity > 0 and cash >= capacity - 0.001
		local text = ("🪖 %d  ·  %s"):format(troops, weaponName(tier))
		if full then
			text = "🪖 " .. troops .. "  ·  BAG FULL!"
		end
		countLabel.Text = text
		countLabel.TextColor3 = if full then P.Coin else P.White
	end

	function self.SetSquad(_self: Visuals, newTroops: number, newCash: number, newCapacity: number, newTier: number)
		troops = math.max(0, math.floor(newTroops))
		cash = math.max(0, newCash)
		capacity = newCapacity
		tier = newTier
		refreshTag()
	end

	function self.Update(_self: Visuals, dt: number, root: BasePart?, moving: boolean)
		if destroyed then
			return
		end
		bobClock += dt
		local parts: { BasePart } = {}
		local cframes: { CFrame } = {}
		local shown = math.min(troops, MAX_TROOP_MODELS)
		local alpha = 1 - math.exp(-dt * 12)
		if root then
			if tagGui.Adornee ~= root then
				tagGui.Adornee = root
			end
			local rootPosition = root.CFrame.Position
			local base = Vector3.new(rootPosition.X, floorY, rootPosition.Z)
			for i = 1, MAX_TROOP_MODELS do
				local rig = rigs[i]
				if i <= shown then
					if not active[i] then
						active[i] = true
						positions[i] = base -- new troops pop out of the avatar
					end
					local target = base + sunflowerOffset(i)
					local x = math.clamp(target.X, centreX - halfTrack + 0.8, centreX + halfTrack - 0.8)
					target = Vector3.new(x, floorY, target.Z)
					positions[i] = positions[i]:Lerp(target, alpha)
					local bob = if moving then math.abs(math.sin(bobClock * 13 + i * 0.7)) * 0.35 else 0
					RunnerModels.RigCFrames(rig, CFrame.new(positions[i] + Vector3.new(0, bob, 0)), parts, cframes)
				elseif active[i] then
					active[i] = false
					RunnerModels.RigCFrames(rig, HIDDEN, parts, cframes)
				end
			end
		end

		-- Shooting at the current targets.
		if os.clock() < engageUntil and #engageTargets > 0 and shown > 0 then
			fireClock += dt
			while fireClock >= FIRE_INTERVAL do
				fireClock -= FIRE_INTERVAL
				local shooter = math.random(1, shown)
				local from = positions[shooter] + Vector3.new(0.66, 1.5, -1.2)
				local to = engageTargets[math.random(1, #engageTargets)]
				fireBullet(from, to + Vector3.new(math.random(-10, 10) / 10, math.random(-5, 5) / 10, 0))
			end
		else
			fireClock = 0
		end

		for _, bullet in bullets do
			if bullet.active then
				bullet.t += dt
				local a = bullet.t / bullet.duration
				table.insert(parts, bullet.part)
				if a >= 1 then
					bullet.active = false
					table.insert(cframes, HIDDEN)
				else
					local pos = bullet.from:Lerp(bullet.to, a)
					table.insert(cframes, CFrame.lookAt(pos, bullet.to))
				end
			end
		end

		if #parts > 0 then
			Workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
		end
	end

	function self.Engage(_self: Visuals, rowIndex: number, lane: number)
		local rowVisual = rowVisuals[rowIndex]
		local optionVisual = rowVisual and rowVisual.options[lane]
		if not optionVisual or #optionVisual.targets == 0 then
			return
		end
		engageTargets = optionVisual.targets
		engageUntil = os.clock() + ENGAGE_TIME
	end

	function self.RowCrossed(_self: Visuals, rowIndex: number, lane: number, outcome: RowOutcome?, capacityNow: number)
		local rowVisual = rowVisuals[rowIndex]
		if not rowVisual or destroyed then
			return
		end
		local optionVisual = rowVisual.options[lane]
		if not optionVisual then
			return
		end
		local kind = rowVisual.row.kind
		local popAt = optionVisual.centre + Vector3.new(0, 7, -2)
		local troopDelta = if outcome then outcome.troopsAfter - outcome.troopsBefore else 0
		local cashDelta = if outcome then outcome.cashAfter - outcome.cashBefore else 0

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
				for _, item in tollCash:GetDescendants() do
					if item:IsA("BasePart") then
						Theme.Tween(item, { CFrame = item.CFrame + Vector3.new(0, 6, 4), Transparency = 1 }, 0.4)
					end
				end
				task.delay(0.5, function()
					tollCash:Destroy()
				end)
				optionVisual.model = nil
				if cashDelta > 0.001 then
					Theme.FloatText(popAt + Vector3.new(0, 2.5, 0), "+" .. Format.Money(cashDelta), P.MoneyGreen)
				end
			end
			-- Weapon gates get their own "🔫 Rifle!" pop below instead.
			if optionVisual.option.op ~= "weapon" then
				local good = toneColor(optionVisual.option) ~= P.GateNegative
				Theme.FloatText(popAt, optionVisual.option.label, if good then P.MoneyGreen else P.ButtonRed)
			end
		elseif kind == "cash" then
			local model = optionVisual.model
			if model then
				for _, item in model:GetDescendants() do
					if item:IsA("BasePart") then
						Theme.Tween(item, { CFrame = item.CFrame + Vector3.new(0, 6, 4), Transparency = 1 }, 0.4)
					end
				end
				task.delay(0.5, function()
					model:Destroy()
				end)
				optionVisual.model = nil
			end
			if cashDelta > 0.001 then
				Theme.FloatText(popAt, "+" .. Format.Money(cashDelta), P.MoneyGreen)
			end
			local offered = optionVisual.option.cash or 0
			if
				outcome
				and offered > cashDelta + 0.01
				and capacityNow > 0
				and outcome.cashAfter >= capacityNow - 0.01
			then
				Theme.FloatText(popAt + Vector3.new(0, 2.5, 0), "CAPACITY FULL!", P.Coin)
			end
			Theme.Pop(moneyTag)
		elseif kind == "squad" then
			local wiped = outcome ~= nil and outcome.troopsAfter <= 0
			if not wiped then
				for i, rig in optionVisual.enemies do
					task.delay(i * 0.03, function()
						if rig.model.Parent then
							knockDown(rig.model, rig.pivot, 0.45)
						end
					end)
				end
				optionVisual.enemies = {}
				if optionVisual.anchor then
					optionVisual.anchor:Destroy()
					optionVisual.anchor = nil
				end
			end
		elseif kind == "barricade" then
			local broken = outcome == nil or outcome.destroyed == true
			local hp = optionVisual.hp
			if broken then
				if hp then
					hp.set(0, 0.25)
				end
				local model = optionVisual.model
				if model then
					task.delay(0.2, function()
						if model.Parent then
							crumble(model, 0.6)
						end
					end)
					optionVisual.model = nil
				end
				Theme.FloatText(popAt, "SMASHED!", P.Coin)
			else
				if hp then
					hp.set(0.35, 0.3)
				end
				Theme.FloatText(popAt, "TOO STRONG!", P.ButtonRed)
			end
		end

		if troopDelta ~= 0 and kind ~= "gate" then
			local text = if troopDelta > 0 then "+" .. troopDelta .. " 🪖" else tostring(troopDelta) .. " 🪖"
			Theme.FloatText(popAt + Vector3.new(0, -2, 0), text, if troopDelta > 0 then P.MoneyGreen else P.ButtonRed)
		end
		if outcome and outcome.weaponTier > tier then
			Theme.FloatText(popAt + Vector3.new(0, 3, 0), "🔫 " .. weaponName(outcome.weaponTier) .. "!", P.Coin)
		end
		-- A weapon gate at the top tier pays cash instead ("Weapon already maxed").
		local maxedWeapon = kind == "gate"
			and optionVisual.option.op == "weapon"
			and outcome ~= nil
			and outcome.weaponTier <= tier
		if maxedWeapon and outcome and outcome.note and outcome.note ~= "" then
			Theme.FloatText(popAt + Vector3.new(0, 5, 0), outcome.note, P.Coin)
		end
	end

	function self.StartBossFight(_self: Visuals, duration: number, fraction: number)
		if destroyed then
			return
		end
		engageTargets = fortTargets
		engageUntil = os.clock() + duration
		local f = math.clamp(fraction, 0, 1)
		fortHp.set(1 - f, duration)
		task.delay(duration, function()
			if destroyed then
				return
			end
			if f >= 0.999 then
				Theme.FloatText(fortCentre + Vector3.new(0, 14, 10), "DESTROYED!", P.Coin)
				crumble(fortModel, 1.0)
			else
				Theme.FloatText(
					fortCentre + Vector3.new(0, 14, 10),
					Format.Percent(math.floor(f * 100) / 100) .. " DESTROYED",
					P.White
				)
			end
		end)
	end

	function self.Destroy(_self: Visuals)
		if destroyed then
			return
		end
		destroyed = true
		folder:Destroy()
	end

	refreshTag()
	folder.Parent = Workspace
	return self
end

return RunnerVisuals
