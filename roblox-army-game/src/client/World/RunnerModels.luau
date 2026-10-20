--!strict
--[[
	RunnerModels (client)
	Builds the little toy models used by the army runner, from plain Parts only
	(no meshes, no asset ids). Everything is Anchored, SmoothPlastic and
	non-colliding, so nothing can ever block the running avatar.

	Public API:
		RunnerModels.Part(props) -> Part
			props: { Name?, Size, CFrame, Color, Parent, Transparency?, Material?, Shape? }
		RunnerModels.Soldier(kind, parent, pivot, gunTier?) -> Rig
			kind = "troop" (green helmet + body + arms holding a gun) | "enemy" (red uniform)
			Rig = { model, parts, offsets, pivot, gunParts, gunTier }
			Move a rig fast with RunnerModels.RigCFrames(rig, pivot, out) + workspace:BulkMoveTo.
		RunnerModels.SetRigGun(rig, tier)     -- swap the gun the rig holds (RunnerGuns model, troop scale)
		RunnerModels.RigMuzzle(rig, pivot) -> Vector3  -- world position of the rig's muzzle at `pivot`
		RunnerModels.TROOP_GRIP: CFrame       -- where a soldier's right hand holds the gun (rig space)
		RunnerModels.GUN_SCALE: number        -- troop guns are this x the avatar's gun
		RunnerModels.RigCFrames(rig, pivot, outParts, outCFrames)  -- appends part/CFrame pairs
		RunnerModels.CoinStack(parent, position, coins) -> Model     -- flat gold coins + green cash bricks
		RunnerModels.Sandbags(parent, centre, width) -> Model         -- barricade wall
		RunnerModels.Fort(parent, centre) -> (Model, { Vector3 }, Model)
			castle at the end of the track + points to shoot at + its gate door.
			The gateway is a real opening (FORT_GATE_WIDTH x FORT_GATE_HEIGHT,
			FORT_GATE_DEPTH deep) so the ~11.7-stud boss fits; the boss waits in
			it, hidden behind the door, and walks out once RunnerVisuals has sunk
			the door into the ground.
		RunnerModels.FORT_GATE_WIDTH / FORT_GATE_HEIGHT / FORT_GATE_DEPTH: number
		RunnerModels.SurfaceLabel(part, face, text, maxTextSize?) -> TextLabel
			big white text with a dark outline drawn on one face of a part.

	Coordinates: every builder places its model with the "front" facing +Z,
	i.e. towards the runner, who travels towards -Z. Soldier rigs are built
	facing -Z (the run direction); enemies are rotated 180 degrees.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)

local RunnerGuns = require(script.Parent.RunnerGuns)

local P = Config.Palette

local RunnerModels = {}

export type Rig = {
	model: Model,
	parts: { BasePart }, -- body parts first, then the gun parts
	offsets: { CFrame },
	pivot: CFrame, -- where the rig was built (feet, facing its front)
	gunParts: number, -- how many entries at the end of `parts` are the gun
	gunTier: number, -- 0 = no gun
}

type PartProps = {
	Name: string?,
	Size: Vector3,
	CFrame: CFrame,
	Color: Color3,
	Parent: Instance?,
	Transparency: number?,
	Material: Enum.Material?,
	Shape: Enum.PartType?,
}

function RunnerModels.Part(props: PartProps): Part
	local part = Instance.new("Part")
	part.Name = props.Name or "Part"
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.CastShadow = true
	part.Material = props.Material or Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	if props.Shape then
		part.Shape = props.Shape
	end
	part.Size = props.Size
	part.CFrame = props.CFrame
	part.Color = props.Color
	part.Transparency = props.Transparency or 0
	part.Parent = props.Parent
	return part
end

-------------------------------------------------------------------------------
-- Soldiers
-------------------------------------------------------------------------------
-- Offsets are relative to the soldier's feet, facing -Z (the run direction).
-- Each entry: name, size, offset, colour key.
type PieceDef = { name: string, size: Vector3, offset: CFrame, color: Color3 }

-- [PROPOSAL] Right hand position of a soldier holding its gun forward.
RunnerModels.TROOP_GRIP = CFrame.new(0.62, 1.55, -0.95)
RunnerModels.GUN_SCALE = 0.62 -- soldiers are ~3 studs tall, the avatar ~5

local function soldierPieces(kind: string): { PieceDef }
	local isEnemy = kind == "enemy"
	local body = if isEnemy then P.EnemyRed else P.TroopGreen
	local dark = if isEnemy then P.ButtonRedDark else P.TroopGreenDark
	local legs = if isEnemy then P.GatePost else P.TroopGreenDark
	-- [PROPOSAL] Lego-style yellow head (toy look of the reference images).
	local head = P.CoinShine
	return {
		{ name = "Legs", size = Vector3.new(1.0, 0.9, 0.6), offset = CFrame.new(0, 0.45, 0), color = legs },
		{ name = "Body", size = Vector3.new(1.15, 1.1, 0.7), offset = CFrame.new(0, 1.45, 0), color = body },
		{ name = "Head", size = Vector3.new(0.8, 0.75, 0.75), offset = CFrame.new(0, 2.38, 0), color = head },
		{ name = "Helmet", size = Vector3.new(1.0, 0.42, 1.0), offset = CFrame.new(0, 2.85, 0.02), color = dark },
		-- Both arms reach forward to hold the gun (the right one at TROOP_GRIP).
		{
			name = "RightArm",
			size = Vector3.new(0.3, 0.3, 0.95),
			offset = CFrame.new(0.66, 1.68, -0.5),
			color = body,
		},
		{
			name = "LeftArm",
			size = Vector3.new(0.3, 0.3, 1.05),
			offset = CFrame.new(0.15, 1.62, -0.62) * CFrame.Angles(0, math.rad(-28), 0),
			color = body,
		},
	}
end

function RunnerModels.Soldier(kind: string, parent: Instance, pivot: CFrame, gunTier: number?): Rig
	local model = Instance.new("Model")
	model.Name = if kind == "enemy" then "Enemy" else "Troop"
	local parts: { BasePart } = {}
	local offsets: { CFrame } = {}
	for _, piece in soldierPieces(kind) do
		local part = RunnerModels.Part({
			Name = piece.name,
			Size = piece.size,
			CFrame = pivot * piece.offset,
			Color = piece.color,
			Parent = model,
		})
		part.CastShadow = false -- 40 little shadows cost more than they add
		table.insert(parts, part)
		table.insert(offsets, piece.offset)
	end
	model.WorldPivot = pivot
	model.Parent = parent
	local rig: Rig = { model = model, parts = parts, offsets = offsets, pivot = pivot, gunParts = 0, gunTier = 0 }
	-- Enemies always carry a rifle; troops the weapon tier of the run.
	RunnerModels.SetRigGun(rig, gunTier or (if kind == "enemy" then 2 else 1))
	return rig
end

-- Replaces the gun the rig holds. The new parts are placed at the rig's
-- build pivot; the next RigCFrames call moves them with the rest.
function RunnerModels.SetRigGun(rig: Rig, tier: number)
	if rig.gunTier == tier and rig.gunParts > 0 then
		return
	end
	for _ = 1, rig.gunParts do
		local part = table.remove(rig.parts)
		table.remove(rig.offsets)
		if part then
			part:Destroy()
		end
	end
	rig.gunParts = 0
	rig.gunTier = tier
	local current = rig.model.WorldPivot
	for _, piece in RunnerGuns.Pieces(tier, RunnerModels.GUN_SCALE) do
		local offset = RunnerModels.TROOP_GRIP * piece.offset
		local part = RunnerModels.Part({
			Name = "Gun" .. piece.name,
			Size = piece.size,
			CFrame = current * offset,
			Color = piece.color,
			Material = piece.material,
			Shape = piece.shape,
			Parent = rig.model,
		})
		-- Anchored like the rest of the rig (moved with BulkMoveTo, no welds);
		-- never collides, is never hit by raycasts and never fires Touched.
		part.Massless = true
		part.CastShadow = false
		table.insert(rig.parts, part)
		table.insert(rig.offsets, offset)
		rig.gunParts += 1
	end
end

function RunnerModels.RigMuzzle(rig: Rig, pivot: CFrame): Vector3
	local tier = math.max(1, rig.gunTier)
	return (pivot * RunnerModels.TROOP_GRIP * RunnerGuns.MuzzleOffset(tier, RunnerModels.GUN_SCALE)).Position
end

-- Appends the rig's parts and their target CFrames to the two lists, ready
-- for workspace:BulkMoveTo(outParts, outCFrames).
function RunnerModels.RigCFrames(rig: Rig, pivot: CFrame, outParts: { BasePart }, outCFrames: { CFrame })
	for i, part in rig.parts do
		table.insert(outParts, part)
		table.insert(outCFrames, pivot * rig.offsets[i])
	end
end

-------------------------------------------------------------------------------
-- Text on a part face
-------------------------------------------------------------------------------
function RunnerModels.SurfaceLabel(part: BasePart, face: Enum.NormalId, text: string, maxTextSize: number?): TextLabel
	local gui = Instance.new("SurfaceGui")
	gui.Name = "Label"
	gui.Face = face
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 40
	gui.LightInfluence = 0
	gui.AlwaysOnTop = false
	gui.Adornee = part
	gui.Parent = part

	local label = Instance.new("TextLabel")
	label.BackgroundTransparency = 1
	label.Size = UDim2.fromScale(0.94, 0.8)
	label.Position = UDim2.fromScale(0.5, 0.5)
	label.AnchorPoint = Vector2.new(0.5, 0.5)
	label.Font = Config.Font
	label.Text = text
	label.TextColor3 = P.White
	label.TextScaled = true
	label.TextWrapped = true
	label.Parent = gui
	local limit = Instance.new("UITextSizeConstraint")
	limit.MaxTextSize = maxTextSize or 120
	limit.Parent = label
	local stroke = Instance.new("UIStroke")
	stroke.Color = P.TextStroke
	stroke.Thickness = 6
	stroke.LineJoinMode = Enum.LineJoinMode.Round
	stroke.Parent = label
	return label
end

-------------------------------------------------------------------------------
-- Cash stack (cash rows)
-------------------------------------------------------------------------------
function RunnerModels.CoinStack(parent: Instance, position: Vector3, coins: number): Model
	local model = Instance.new("Model")
	model.Name = "CashStack"
	local count = math.clamp(math.floor(coins), 1, 8)
	-- A Cylinder part's round faces point along its X axis; rotate so it lies flat.
	local flat = CFrame.Angles(0, 0, math.rad(90))
	for i = 1, count do
		local offset = Vector3.new(((i % 2) - 0.5) * 0.25, 0.22 + (i - 1) * 0.42, ((i % 3) - 1) * 0.15)
		RunnerModels.Part({
			Name = "Coin",
			Shape = Enum.PartType.Cylinder,
			Size = Vector3.new(0.4, 2.6, 2.6),
			CFrame = CFrame.new(position + Vector3.new(-1.2, 0, 0) + offset) * flat,
			Color = if i % 2 == 0 then P.CoinDark else P.Coin,
			Parent = model,
		})
	end
	-- Green cash bricks next to the coins.
	local bricks = math.clamp(math.ceil(count / 2), 1, 4)
	for i = 1, bricks do
		RunnerModels.Part({
			Name = "Cash",
			Size = Vector3.new(1.6, 0.45, 0.9),
			CFrame = CFrame.new(position + Vector3.new(1.3, 0.23 + (i - 1) * 0.47, 0))
				* CFrame.Angles(0, math.rad((i % 2) * 12), 0),
			Color = if i % 2 == 0 then P.MoneyGreenDark else P.MoneyGreen,
			Parent = model,
		})
	end
	model.Parent = parent
	return model
end

-------------------------------------------------------------------------------
-- Barricade (sandbag wall)
-------------------------------------------------------------------------------
function RunnerModels.Sandbags(parent: Instance, centre: Vector3, width: number): Model
	local model = Instance.new("Model")
	model.Name = "Barricade"
	local bagW = 2.2
	local layers = 3
	for layer = 1, layers do
		local shift = if layer % 2 == 0 then bagW / 2 else 0
		local usable = width - 0.6
		local count = math.max(1, math.floor(usable / bagW))
		local startX = -((count - 1) * bagW) / 2 + shift * 0.5
		for i = 1, count do
			local x = startX + (i - 1) * bagW
			if math.abs(x) <= usable / 2 then
				RunnerModels.Part({
					Name = "Sandbag",
					Size = Vector3.new(bagW - 0.15, 1.0, 1.4),
					CFrame = CFrame.new(centre + Vector3.new(x, 0.5 + (layer - 1) * 0.95, 0)),
					Color = if (i + layer) % 2 == 0 then P.Wood else P.WallYellow,
					Parent = model,
				})
			end
		end
	end
	-- Dark posts at both ends with a rail (barbed wire look).
	for _, side in { -1, 1 } do
		RunnerModels.Part({
			Name = "Post",
			Size = Vector3.new(0.5, 4, 0.5),
			CFrame = CFrame.new(centre + Vector3.new(side * (width / 2 - 0.4), 2, 0)),
			Color = P.GatePost,
			Parent = model,
		})
	end
	RunnerModels.Part({
		Name = "Wire",
		Size = Vector3.new(width - 0.6, 0.18, 0.18),
		CFrame = CFrame.new(centre + Vector3.new(0, 3.7, 0)),
		Color = P.GatePost,
		Parent = model,
	})
	model.Parent = parent
	return model
end

-------------------------------------------------------------------------------
-- Enemy base (boss)
-------------------------------------------------------------------------------
-- [PROPOSAL] Gateway sized for the boss (RunnerBoss: ~11.7 studs with its
-- horns mid-stride, ~8 wide with the shoulder pads and cannon drum).
RunnerModels.FORT_GATE_WIDTH = 11
RunnerModels.FORT_GATE_HEIGHT = 13
RunnerModels.FORT_GATE_DEPTH = 7

-- Returns the model, a few points on its front to aim bullets at and the door.
function RunnerModels.Fort(parent: Instance, centre: Vector3): (Model, { Vector3 }, Model)
	local model = Instance.new("Model")
	model.Name = "EnemyBase"
	local width, height, depth = 30, 15, 14 -- [PROPOSAL] walls 2 studs above the gateway
	local gateW = RunnerModels.FORT_GATE_WIDTH
	local gateH = RunnerModels.FORT_GATE_HEIGHT
	local gateD = RunnerModels.FORT_GATE_DEPTH
	local front = depth / 2 -- the +Z face looks at the runner

	local function block(name: string, size: Vector3, offset: Vector3, color: Color3): Part
		return RunnerModels.Part({
			Name = name,
			Size = size,
			CFrame = CFrame.new(centre + offset),
			Color = color,
			Parent = model,
		})
	end

	-- Main walls (red Lego bricks) built AROUND the gateway opening: left and
	-- right blocks, a lintel above the gate and a back block behind the recess.
	local sideW = (width - gateW) / 2
	for _, side in { -1, 1 } do
		block(
			"Wall",
			Vector3.new(sideW, height, depth),
			Vector3.new(side * (gateW / 2 + sideW / 2), height / 2, 0),
			P.WallRed
		)
	end
	block(
		"Lintel",
		Vector3.new(gateW, height - gateH, depth),
		Vector3.new(0, gateH + (height - gateH) / 2, 0),
		P.WallRed
	)
	block(
		"WallBack",
		Vector3.new(gateW, gateH, depth - gateD),
		Vector3.new(0, gateH / 2, front - gateD - (depth - gateD) / 2),
		P.WallRed
	)
	-- Dark back of the gateway recess (the boss waits in front of it).
	block("Gateway", Vector3.new(gateW, gateH, 0.2), Vector3.new(0, gateH / 2, front - gateD + 0.1), P.Shade)
	local crenels = 7
	for i = 1, crenels do
		local x = -width / 2 + (i - 0.5) * (width / crenels)
		block("Crenel", Vector3.new(2.4, 1.6, 2.4), Vector3.new(x, height + 0.8, front - 1.2), P.WallRed)
	end
	-- Two dark towers with yellow tops.
	for _, side in { -1, 1 } do
		local x = side * (width / 2 + 2)
		block("Tower", Vector3.new(6, height + 6, 6), Vector3.new(x, (height + 6) / 2, 2), P.GatePost)
		block("TowerTop", Vector3.new(7, 1.4, 7), Vector3.new(x, height + 6.7, 2), P.WallYellow)
		block("Window", Vector3.new(2, 2, 0.3), Vector3.new(x, height + 2, 5.1), P.Shade)
	end
	-- Iron door with bars over the gateway. Its own model so it can sink
	-- into the ground (RunnerVisuals sinks it by its own height).
	local door = Instance.new("Model")
	door.Name = "Door"
	block(
		"Door",
		Vector3.new(gateW + 0.4, gateH + 0.2, 0.4),
		Vector3.new(0, (gateH + 0.2) / 2, front + 0.3),
		P.GateIron
	).Parent =
		door
	for i = -2, 2 do
		block("DoorBar", Vector3.new(0.35, gateH, 0.2), Vector3.new(i * 2, gateH / 2, front + 0.55), P.GatePost).Parent =
			door
	end
	door.Parent = model
	-- Flag on the roof.
	block("Pole", Vector3.new(0.4, 9, 0.4), Vector3.new(0, height + 4.5, 0), P.GatePost)
	block("Flag", Vector3.new(4.5, 2.6, 0.2), Vector3.new(2.45, height + 7.4, 0), P.EnemyRed)

	-- Big "ENEMY BASE" sign above the door, facing the runner (+Z = Back face).
	local sign = block("Sign", Vector3.new(16, 3, 0.4), Vector3.new(0, height + 3.4, front + 0.3), P.PillBlack)
	RunnerModels.SurfaceLabel(sign, Enum.NormalId.Back, "ENEMY BASE", 100)

	model.Parent = parent
	local frontZ = centre.Z + front + 0.5
	local y = centre.Y
	local targets = {
		Vector3.new(centre.X - 8, y + 4, frontZ),
		Vector3.new(centre.X + 8, y + 4, frontZ),
		Vector3.new(centre.X, y + 6, frontZ),
		Vector3.new(centre.X - width / 2 - 2, y + 9, frontZ - 3),
		Vector3.new(centre.X + width / 2 + 2, y + 9, frontZ - 3),
		Vector3.new(centre.X, y + 2.5, frontZ),
	}
	return model, targets, door
end

return RunnerModels
