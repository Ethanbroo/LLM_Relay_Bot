--!strict
--[[
	MapBuilder (server service, Priority 70)
	Builds the STATIC home base that every player shares, in the look of
	reference image 1 (docs/reference/1_base_tycoon.png):

		workspace.Map
			Area1 .. AreaN   sky-blue floor slab, a lighter cross-shaped walkway
			                 with thin white edge lines, 4 rounded plot outlines,
			                 chunky Lego walls with round studs on top (hedge
			                 green / yellow / red per area), an area name sign at
			                 the west entrance, grass strips with low-poly trees,
			                 benches, street lamps (and a red phone box in area 1)
			Corridor1 ..     short walled bridge between two neighbouring areas
			                 (the black iron gate itself is drawn by each client,
			                 see client/World/BaseVisuals.luau)
			SpawnLocation    where players appear (Layout.SpawnPosition)
			RunnerPortal     the big orange "ARMY RUN ▶" pad (Layout.RunnerPortalPosition);
			                 the RUNNER client code detects players standing on it

	It also fills the sea (Terrain water) around the base island, the runner
	tracks and the combat arenas, and sets up colour correction + bloom so the
	game keeps the bright, saturated toy look of the reference images.

	If workspace.Map already exists (for example a pre-baked map saved in the
	place file) the parts are NOT rebuilt, but terrain water and lighting are
	always applied.

	Public API:
		MapBuilder.Priority = 70
		MapBuilder:Init(registry)     -- builds the map (runs before players spawn)
		MapBuilder:Start()            -- nothing extra
		MapBuilder:GetPartCount(): number  -- how many parts were built (debug)

	Everything is positioned with Shared/Layout and coloured with Config.Palette.
	All parts are Anchored SmoothPlastic (Neon only for glowing lamp bulbs and
	the portal border). Thin "overlay" parts (walkways, lines, plot outlines)
	are raised by at least 0.02 studs above what is under them so they never
	flicker (z-fighting).
]]

local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)

local MapBuilder = {}
MapBuilder.Priority = 70

local P = Config.Palette
local PaletteByName = Config.Palette :: any

local FLOOR_Y = Layout.FLOOR_Y
local HALF = Layout.AREA_SIZE / 2 -- 80
local PATH_HALF = Layout.PATH_WIDTH / 2 -- 14
local GATE_HALF = Layout.GATE_WIDTH / 2 -- 10
local WALL_T = 3 -- wall thickness (studs)
local WALL_H = Layout.WALL_HEIGHT -- 6
local WALL_INNER = HALF - WALL_T -- 77: inside face of the perimeter wall
local STUD_SPACING = 4 -- one round Lego stud every 4 studs
local BRICK_LENGTH = 16 -- wall runs are split into bricks of about this length
local BARRIER_TOP = 18 -- invisible wall caps stop players jumping over walls
local RING = (HALF - WALL_T + Layout.PLOT_SIZE / 2 + 42) / 2 -- 73.5: centre of the free strip between plots and walls

-- Overlay heights (top surface above FLOOR_Y). Each layer is >= 0.02 higher.
local TOP_PATH_NS = 0.1
local TOP_PATH_EW = 0.12
local TOP_LINES = 0.16
local TOP_PLOTS = 0.2
local TOP_GRASS = 0.3

local partCount = 0

-------------------------------------------------------------------------------
-- Small part helpers
-------------------------------------------------------------------------------
type PartSpec = {
	Name: string?,
	Size: Vector3,
	CFrame: CFrame,
	Color: Color3,
	Shape: Enum.PartType?,
	Material: Enum.Material?,
	Transparency: number?,
	CanCollide: boolean?,
	CastShadow: boolean?,
}

local function makePart(parent: Instance, spec: PartSpec): Part
	local part = Instance.new("Part")
	part.Name = spec.Name or "Part"
	part.Anchored = true
	part.Shape = spec.Shape or Enum.PartType.Block
	part.Size = spec.Size
	part.CFrame = spec.CFrame
	part.Color = spec.Color
	part.Material = spec.Material or Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Transparency = spec.Transparency or 0
	part.CanCollide = if spec.CanCollide == nil then true else spec.CanCollide
	part.CanTouch = false
	part.CastShadow = if spec.CastShadow == nil then true else spec.CastShadow
	part.Parent = parent
	partCount += 1
	return part
end

-- Box whose TOP surface is `top` studs above the floor, centred at (x, z).
local function slab(
	parent: Instance,
	name: string,
	x: number,
	z: number,
	sizeX: number,
	sizeZ: number,
	thickness: number,
	top: number,
	color: Color3
): Part
	return makePart(parent, {
		Name = name,
		Size = Vector3.new(sizeX, thickness, sizeZ),
		CFrame = CFrame.new(x, FLOOR_Y + top - thickness / 2, z),
		Color = color,
		CastShadow = false,
	})
end

-- Upright cylinder (Roblox cylinders lie along X, so we roll them 90 degrees).
local function cylinder(
	parent: Instance,
	name: string,
	centre: Vector3,
	diameter: number,
	height: number,
	color: Color3,
	collide: boolean?
): Part
	return makePart(parent, {
		Name = name,
		Shape = Enum.PartType.Cylinder,
		Size = Vector3.new(height, diameter, diameter),
		CFrame = CFrame.new(centre) * CFrame.Angles(0, 0, math.rad(90)),
		Color = color,
		CanCollide = collide,
	})
end

local function ball(
	parent: Instance,
	name: string,
	centre: Vector3,
	diameter: number,
	color: Color3,
	collide: boolean?
): Part
	return makePart(parent, {
		Name = name,
		Shape = Enum.PartType.Ball,
		Size = Vector3.new(diameter, diameter, diameter),
		CFrame = CFrame.new(centre),
		Color = color,
		CanCollide = collide,
	})
end

local function darker(color: Color3, amount: number): Color3
	return color:Lerp(Color3.new(0, 0, 0), amount)
end

-- Text on one face of a part (signs). Text is white with a dark outline like
-- every label in the reference images.
local function faceText(part: BasePart, face: Enum.NormalId, text: string, background: Color3?)
	local gui = Instance.new("SurfaceGui")
	gui.Name = "Text" .. face.Name
	gui.Face = face
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 24
	gui.LightInfluence = 0
	gui.Parent = part

	local label = Instance.new("TextLabel")
	label.Size = UDim2.fromScale(1, 1)
	label.BackgroundColor3 = background or P.PillBlack
	label.BackgroundTransparency = if background then 0 else 1
	label.Font = Config.Font
	label.Text = text
	label.TextColor3 = P.White
	label.TextScaled = true
	label.Parent = gui

	local padding = Instance.new("UIPadding")
	padding.PaddingTop = UDim.new(0.12, 0)
	padding.PaddingBottom = UDim.new(0.12, 0)
	padding.PaddingLeft = UDim.new(0.04, 0)
	padding.PaddingRight = UDim.new(0.04, 0)
	padding.Parent = label

	local stroke = Instance.new("UIStroke")
	stroke.Color = P.TextStroke
	stroke.Thickness = 4
	stroke.LineJoinMode = Enum.LineJoinMode.Round
	stroke.Parent = label
end

-------------------------------------------------------------------------------
-- Lego walls
-------------------------------------------------------------------------------
-- A straight wall from (x1, z1) to (x2, z2) (axis aligned), split into bricks,
-- with a row of round studs on top and an invisible cap so nobody can jump
-- over it into the sea.
local function wallRun(parent: Instance, x1: number, z1: number, x2: number, z2: number, color: Color3)
	local alongX = math.abs(x2 - x1) >= math.abs(z2 - z1)
	local length = if alongX then math.abs(x2 - x1) else math.abs(z2 - z1)
	if length < 0.5 then
		return
	end
	local startX, startZ = math.min(x1, x2), math.min(z1, z2)
	local studColor = darker(color, 0.12)

	local bricks = math.max(1, math.floor(length / BRICK_LENGTH + 0.5))
	local brickLength = length / bricks
	for i = 0, bricks - 1 do
		local offset = (i + 0.5) * brickLength
		local cx = if alongX then startX + offset else x1
		local cz = if alongX then z1 else startZ + offset
		makePart(parent, {
			Name = "WallBrick",
			-- alternate a very slightly darker shade so the bricks read as Lego blocks
			Color = if i % 2 == 0 then color else darker(color, 0.05),
			Size = if alongX
				then Vector3.new(brickLength, WALL_H, WALL_T)
				else Vector3.new(WALL_T, WALL_H, brickLength),
			CFrame = CFrame.new(cx, FLOOR_Y + WALL_H / 2, cz),
		})
	end

	local studs = math.max(1, math.floor(length / STUD_SPACING))
	local spacing = length / studs
	for i = 0, studs - 1 do
		local offset = (i + 0.5) * spacing
		local cx = if alongX then startX + offset else x1
		local cz = if alongX then z1 else startZ + offset
		cylinder(parent, "Stud", Vector3.new(cx, FLOOR_Y + WALL_H + 0.35, cz), 1.9, 0.7, studColor, false)
	end

	-- Invisible cap (players can jump ~7 studs high; walls are 6).
	local capHeight = BARRIER_TOP - WALL_H
	makePart(parent, {
		Name = "WallCap",
		Size = if alongX then Vector3.new(length, capHeight, WALL_T) else Vector3.new(WALL_T, capHeight, length),
		CFrame = CFrame.new(
			if alongX then startX + length / 2 else x1,
			FLOOR_Y + WALL_H + capHeight / 2,
			if alongX then z1 else startZ + length / 2
		),
		Color = color,
		Transparency = 1,
		CastShadow = false,
	})
end

-------------------------------------------------------------------------------
-- Decorations (all inside the free strip between the plots and the walls)
-------------------------------------------------------------------------------
local function tree(parent: Instance, x: number, z: number, scale: number)
	local base = Vector3.new(x, FLOOR_Y + TOP_GRASS, z)
	makePart(parent, {
		Name = "TreeTrunk",
		Size = Vector3.new(1.4, 5 * scale, 1.4),
		CFrame = CFrame.new(base + Vector3.new(0, 2.5 * scale, 0)),
		Color = P.Wood,
	})
	-- Chunky low-poly leaves: two blobs and one tilted block.
	ball(parent, "Leaves", base + Vector3.new(0, 6.2 * scale, 0), 5.6 * scale, P.Hedge, false)
	ball(parent, "Leaves", base + Vector3.new(1.3 * scale, 7.9 * scale, 0.5 * scale), 3.8 * scale, P.HedgeDark, false)
	makePart(parent, {
		Name = "Leaves",
		Size = Vector3.new(3.4, 3.4, 3.4) * scale,
		CFrame = CFrame.new(base + Vector3.new(-1.2 * scale, 7.3 * scale, -0.6 * scale))
			* CFrame.Angles(math.rad(35), math.rad(45), 0),
		Color = P.Grass,
		CanCollide = false,
	})
end

-- Wooden bench. `facing` is the direction a person sitting on it looks.
local function bench(parent: Instance, x: number, z: number, facing: Vector3)
	local origin = Vector3.new(x, FLOOR_Y + TOP_GRASS, z)
	local cf = CFrame.lookAt(origin, origin + facing)
	makePart(
		parent,
		{ Name = "BenchSeat", Size = Vector3.new(5, 0.4, 1.6), CFrame = cf * CFrame.new(0, 1.6, 0), Color = P.Wood }
	)
	makePart(
		parent,
		{ Name = "BenchBack", Size = Vector3.new(5, 1.3, 0.3), CFrame = cf * CFrame.new(0, 2.6, 0.75), Color = P.Wood }
	)
	for _, side in { -2, 2 } do
		makePart(parent, {
			Name = "BenchLeg",
			Size = Vector3.new(0.4, 1.4, 1.4),
			CFrame = cf * CFrame.new(side, 0.7, 0),
			Color = P.GateIron,
		})
	end
end

local function lamp(parent: Instance, x: number, z: number)
	local base = Vector3.new(x, FLOOR_Y, z)
	cylinder(parent, "LampPole", base + Vector3.new(0, 4.5, 0), 0.6, 9, P.GateIron, true)
	makePart(parent, {
		Name = "LampCap",
		Size = Vector3.new(1.8, 0.5, 1.8),
		CFrame = CFrame.new(base + Vector3.new(0, 10.5, 0)),
		Color = P.GateIron,
	})
	ball(parent, "LampBulb", base + Vector3.new(0, 9.7, 0), 1.6, P.CoinShine, false).Material = Enum.Material.Neon
end

local function bush(parent: Instance, x: number, z: number)
	ball(parent, "Bush", Vector3.new(x, FLOOR_Y + 1.6, z), 4.4, P.Hedge, true)
	ball(parent, "Bush", Vector3.new(x + 1.2, FLOOR_Y + 2.4, z - 0.6), 3, P.HedgeDark, false)
end

-- Red phone box (image 1, top-left). Area 1 only.
local function phoneBox(parent: Instance, x: number, z: number)
	local base = Vector3.new(x, FLOOR_Y, z)
	makePart(parent, {
		Name = "PhoneBox",
		Size = Vector3.new(4, 8.4, 4),
		CFrame = CFrame.new(base + Vector3.new(0, 4.2, 0)),
		Color = P.WallRed,
	})
	makePart(parent, {
		Name = "PhoneRoof",
		Size = Vector3.new(4.6, 0.8, 4.6),
		CFrame = CFrame.new(base + Vector3.new(0, 8.8, 0)),
		Color = P.WallRed,
	})
	makePart(parent, {
		Name = "PhoneCrown",
		Size = Vector3.new(2.2, 0.7, 2.2),
		CFrame = CFrame.new(base + Vector3.new(0, 9.55, 0)),
		Color = P.WallRed,
	})
	-- Glass panes on all four sides.
	local glass = P.PanelBlue:Lerp(P.White, 0.45)
	for _, dir in { Vector3.new(1, 0, 0), Vector3.new(-1, 0, 0), Vector3.new(0, 0, 1), Vector3.new(0, 0, -1) } do
		local centre = base + Vector3.new(0, 4.4, 0) + dir * 2.03
		local size = if dir.X ~= 0 then Vector3.new(0.1, 5.4, 2.8) else Vector3.new(2.8, 5.4, 0.1)
		makePart(parent, {
			Name = "PhoneGlass",
			Size = size,
			CFrame = CFrame.new(centre),
			Color = glass,
			CanCollide = false,
			CastShadow = false,
		})
	end
	-- White "TELEPHONE" strip above the door (facing +Z, towards the walkway).
	local strip = makePart(parent, {
		Name = "PhoneSign",
		Size = Vector3.new(3.2, 0.7, 0.1),
		CFrame = CFrame.new(base + Vector3.new(0, 7.75, 2.06)),
		Color = P.PillBlack,
		CanCollide = false,
	})
	faceText(strip, Enum.NormalId.Back, "TELEPHONE")
end

-------------------------------------------------------------------------------
-- One area
-------------------------------------------------------------------------------
local function buildArea(map: Instance, areaIndex: number)
	local areaDef = Config.Areas[areaIndex]
	local areaCount = #Config.Areas
	local c = Layout.AreaCenter(areaIndex)
	local cx, cz = c.X, c.Z

	local folder = Instance.new("Folder")
	folder.Name = "Area" .. areaIndex
	folder.Parent = map

	local wallColor: Color3 = PaletteByName[areaDef.wall] or P.Hedge

	-- Floor slab (top surface at FLOOR_Y).
	slab(folder, "Floor", cx, cz, Layout.AREA_SIZE, Layout.AREA_SIZE, 4, 0, P.BaseFloor)

	-- Cross-shaped walkway (two arms, the east-west one 0.02 higher).
	local armLength = WALL_INNER * 2
	slab(folder, "PathNS", cx, cz, Layout.PATH_WIDTH, armLength, 0.1, TOP_PATH_NS, P.BasePath)
	slab(folder, "PathEW", cx, cz, armLength, Layout.PATH_WIDTH, 0.12, TOP_PATH_EW, P.BasePath)

	-- Thin white edge lines along both arms (not across the crossing).
	local lineW = 0.5
	local segLength = WALL_INNER - PATH_HALF
	local segMid = (WALL_INNER + PATH_HALF) / 2
	for _, sideSign in { -1, 1 } do
		for _, endSign in { -1, 1 } do
			local edge = sideSign * (PATH_HALF - lineW / 2)
			-- lines of the north-south arm
			slab(folder, "PathLine", cx + edge, cz + endSign * segMid, lineW, segLength, 0.04, TOP_LINES, P.BaseLine)
			-- lines of the east-west arm
			slab(folder, "PathLine", cx + endSign * segMid, cz + edge, segLength, lineW, 0.04, TOP_LINES, P.BaseLine)
		end
	end

	-- Plot outlines: a rounded light band around a floor-coloured inside,
	-- drawn with a SurfaceGui on an invisible thin part (crisp rounded corners).
	for slotIndex = 1, Config.SlotsPerArea do
		local plotCentre = Layout.SlotCenter(areaIndex, slotIndex)
		local plot = slab(
			folder,
			"PlotOutline",
			plotCentre.X,
			plotCentre.Z,
			Layout.PLOT_SIZE,
			Layout.PLOT_SIZE,
			0.05,
			TOP_PLOTS,
			P.PlotOutline
		)
		plot.Transparency = 1
		plot.CanCollide = false
		plot.CanQuery = false
		local gui = Instance.new("SurfaceGui")
		gui.Name = "Outline"
		gui.Face = Enum.NormalId.Top
		gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
		gui.PixelsPerStud = 10
		gui.LightInfluence = 1
		gui.Parent = plot
		local band = 3 -- studs
		local frame = Instance.new("Frame")
		frame.BackgroundTransparency = 1
		frame.AnchorPoint = Vector2.new(0.5, 0.5)
		frame.Position = UDim2.fromScale(0.5, 0.5)
		frame.Size = UDim2.new(1, -band * 2 * 10, 1, -band * 2 * 10)
		frame.Parent = gui
		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 60)
		corner.Parent = frame
		local stroke = Instance.new("UIStroke")
		stroke.ApplyStrokeMode = Enum.ApplyStrokeMode.Border
		stroke.Color = P.PlotOutline
		stroke.Thickness = band * 10
		stroke.Parent = frame
	end

	-- Perimeter walls (openings of GATE_WIDTH at z = 0 towards the neighbours).
	local north, south = cz - HALF + WALL_T / 2, cz + HALF - WALL_T / 2
	local west, east = cx - HALF + WALL_T / 2, cx + HALF - WALL_T / 2
	wallRun(folder, cx - HALF, north, cx + HALF, north, wallColor)
	wallRun(folder, cx - HALF, south, cx + HALF, south, wallColor)
	local function sideWall(x: number, hasOpening: boolean)
		local zMin, zMax = cz - HALF + WALL_T, cz + HALF - WALL_T
		if hasOpening then
			wallRun(folder, x, zMin, x, cz - GATE_HALF, wallColor)
			wallRun(folder, x, cz + GATE_HALF, x, zMax, wallColor)
		else
			wallRun(folder, x, zMin, x, zMax, wallColor)
		end
	end
	sideWall(west, areaIndex > 1)
	sideWall(east, areaIndex < areaCount)

	-- Area name sign: an arch over the west entrance (a wall sign in area 1).
	local signX = west
	for _, side in { -1, 1 } do
		makePart(folder, {
			Name = "SignPillar",
			Size = Vector3.new(WALL_T + 0.6, 15, 3.4),
			CFrame = CFrame.new(signX, FLOOR_Y + 7.5, cz + side * (GATE_HALF + 1.7)),
			Color = darker(wallColor, 0.1),
		})
	end
	local board = makePart(folder, {
		Name = "AreaSign",
		Size = Vector3.new(1.6, 4, GATE_HALF * 2 + 6.8),
		CFrame = CFrame.new(signX, FLOOR_Y + 13, cz),
		Color = P.CardBlue,
	})
	local title = ("AREA %d: %s"):format(areaIndex, string.upper(areaDef.name))
	faceText(board, Enum.NormalId.Right, title, P.CardBlue) -- readable from inside the area
	if areaIndex > 1 then
		faceText(board, Enum.NormalId.Left, title, P.CardBlue) -- readable when arriving from the west
	end

	-- Decorations in the free strip between the plots and the walls.
	local decor = Instance.new("Folder")
	decor.Name = "Decor"
	decor.Parent = folder
	local mirror = if areaIndex % 2 == 0 then -1 else 1 -- vary the layout between areas
	local stripLength = Layout.PLOT_SIZE - 4
	-- North strip (west half) and south strip (east half): grass + trees + bench.
	for _, strip in { { x = -42 * mirror, z = -RING, face = 1 }, { x = 42 * mirror, z = RING, face = -1 } } do
		slab(decor, "Grass", cx + strip.x, cz + strip.z, stripLength, 6, 0.3, TOP_GRASS, P.Grass)
		tree(decor, cx + strip.x - 17, cz + strip.z, 1)
		tree(decor, cx + strip.x + 17, cz + strip.z, 0.85)
		bench(decor, cx + strip.x, cz + strip.z, Vector3.new(0, 0, strip.face))
	end
	-- West strip (south half) and east strip (north half): grass + one tree.
	for _, strip in { { x = -RING, z = 42 * mirror }, { x = RING, z = -42 * mirror } } do
		slab(decor, "Grass", cx + strip.x, cz + strip.z, 6, stripLength, 0.3, TOP_GRASS, P.Grass)
		tree(decor, cx + strip.x, cz + strip.z + 12, 0.9)
		bush(decor, cx + strip.x, cz + strip.z - 12)
	end
	-- Street lamps where the walkway meets the strips.
	for _, pos in { { -1, -1 }, { 1, -1 }, { -1, 1 }, { 1, 1 } } do
		lamp(decor, cx + pos[1] * (PATH_HALF + 3), cz + pos[2] * RING)
	end
	-- Corner bushes / phone box.
	if areaIndex == 1 then
		phoneBox(decor, cx - RING, cz - RING)
	else
		bush(decor, cx - RING, cz - RING)
	end
	bush(decor, cx + RING, cz + RING)
end

-- Walled bridge between area `areaIndex` and the next one.
local function buildCorridor(map: Instance, areaIndex: number)
	local areaDef = Config.Areas[areaIndex]
	local wallColor: Color3 = PaletteByName[areaDef.wall] or P.Hedge
	local c = Layout.AreaCenter(areaIndex)
	local x0 = c.X + HALF
	local x1 = Layout.AreaCenter(areaIndex + 1).X - HALF
	local folder = Instance.new("Folder")
	folder.Name = "Corridor" .. areaIndex
	folder.Parent = map
	local width = Layout.GATE_WIDTH + WALL_T * 2
	slab(folder, "CorridorFloor", (x0 + x1) / 2, c.Z, x1 - x0, width, 4 + TOP_PATH_EW, TOP_PATH_EW, P.BasePath)
	for _, side in { -1, 1 } do
		local z = c.Z + side * (GATE_HALF + WALL_T / 2)
		wallRun(folder, x0, z, x1, z, wallColor)
	end
end

-------------------------------------------------------------------------------
-- Spawn + ARMY RUN portal
-------------------------------------------------------------------------------
local function buildSpawn(map: Instance)
	local spawnPos = Layout.SpawnPosition()
	local spawn = Instance.new("SpawnLocation")
	spawn.Name = "SpawnLocation"
	spawn.Anchored = true
	spawn.Size = Vector3.new(10, 1, 10)
	-- Top surface 0.04 above the walkway so it does not flicker.
	spawn.CFrame = CFrame.new(spawnPos.X, FLOOR_Y + TOP_PATH_NS + 0.06 - 0.5, spawnPos.Z)
	spawn.Color = P.PadBorder
	spawn.Material = Enum.Material.SmoothPlastic
	spawn.TopSurface = Enum.SurfaceType.Smooth
	spawn.BottomSurface = Enum.SurfaceType.Smooth
	spawn.Neutral = true
	spawn.Duration = 0 -- no force field bubble
	spawn.AllowTeamChangeOnTouch = false
	spawn.Parent = map
	partCount += 1
end

local function buildPortal(map: Instance)
	local pos = Layout.RunnerPortalPosition()
	local folder = Instance.new("Folder")
	folder.Name = "RunnerPortal"
	folder.Parent = map

	local size = 15
	local base = slab(folder, "PortalPad", pos.X, pos.Z, size, size, 0.3, 0.3, P.CardOrange)
	-- Glowing neon border.
	local border = 1
	local borderTop = 0.34
	for _, side in { -1, 1 } do
		slab(
			folder,
			"PortalGlow",
			pos.X + side * (size / 2 - border / 2),
			pos.Z,
			border,
			size,
			0.1,
			borderTop,
			P.CardOrange
		).Material =
			Enum.Material.Neon
		slab(
			folder,
			"PortalGlow",
			pos.X,
			pos.Z + side * (size / 2 - border / 2),
			size - border * 2,
			border,
			0.1,
			borderTop,
			P.CardOrange
		).Material =
			Enum.Material.Neon
	end

	-- "ARMY RUN ▶" painted on top. Written twice (one copy upside down, like a
	-- helipad marking) so it reads correctly from both ends of the walkway.
	local gui = Instance.new("SurfaceGui")
	gui.Name = "PortalText"
	gui.Face = Enum.NormalId.Top
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 24
	gui.LightInfluence = 0
	gui.Parent = base
	local inner = Instance.new("Frame")
	inner.AnchorPoint = Vector2.new(0.5, 0.5)
	inner.Position = UDim2.fromScale(0.5, 0.5)
	inner.Size = UDim2.fromScale(0.82, 0.82)
	inner.BackgroundColor3 = P.White
	inner.Parent = gui
	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0.12, 0)
	corner.Parent = inner
	for i, rotation in { 0, 180 } do
		local label = Instance.new("TextLabel")
		label.BackgroundTransparency = 1
		label.AnchorPoint = Vector2.new(0.5, 0.5)
		label.Position = UDim2.fromScale(0.5, if i == 1 then 0.72 else 0.28)
		label.Size = UDim2.fromScale(0.92, 0.36)
		label.Rotation = rotation
		label.Font = Config.Font
		label.Text = "ARMY RUN ▶"
		label.TextScaled = true
		label.TextColor3 = P.CardOrange
		label.Parent = inner
		local stroke = Instance.new("UIStroke")
		stroke.Color = P.TextStroke
		stroke.Thickness = 5
		stroke.Parent = label
	end

	-- Standing arch behind the pad, readable while walking up from the spawn.
	local archZ = pos.Z - size / 2 - 1.5
	for _, side in { -1, 1 } do
		makePart(folder, {
			Name = "ArchPost",
			Size = Vector3.new(1.6, 13, 1.6),
			CFrame = CFrame.new(pos.X + side * 9, FLOOR_Y + 6.5, archZ),
			Color = P.CardOrangeDark,
		})
	end
	local beam = makePart(folder, {
		Name = "ArchSign",
		Size = Vector3.new(20, 3.6, 1.2),
		CFrame = CFrame.new(pos.X, FLOOR_Y + 12, archZ),
		Color = P.CardOrange,
	})
	faceText(beam, Enum.NormalId.Back, "🏃 ARMY RUN ▶", P.CardOrange) -- Back face points +Z (towards the spawn)
end

-------------------------------------------------------------------------------
-- Terrain water + lighting (always applied)
-------------------------------------------------------------------------------
local function fillWater()
	local terrain = Workspace.Terrain
	terrain.WaterColor = P.Sea
	terrain.WaterTransparency = 0.3
	terrain.WaterReflectance = 0.3
	terrain.WaterWaveSize = 0.12
	terrain.WaterWaveSpeed = 8

	-- One big sea covering the base island, the runner-track zone
	-- (x 0..(MaxSlots+1)*RUNNER_SLOT_SPACING, z ~600..1700) and the arena zone
	-- (x 0..(MaxSlots+1)*ARENA_SLOT_SPACING, z ~-800..-600), with a margin.
	local lastArea = Layout.AreaCenter(#Config.Areas)
	local minX = -HALF - 240
	local maxX = math.max(
		lastArea.X + HALF + 240,
		(Config.MaxSlots + 1) * Layout.RUNNER_SLOT_SPACING + 200,
		(Config.MaxSlots + 1) * Layout.ARENA_SLOT_SPACING + 200
	)
	local minZ = Layout.ARENA_Z - 100 - 200
	local maxZ = Layout.RUNNER_START_Z + 100 + 200

	local surface = FLOOR_Y - 1.5 -- just below the floor
	local depth = 16
	local chunk = 512 -- fill in chunks so no single call is huge
	local x = minX
	while x < maxX do
		local sizeX = math.min(chunk, maxX - x)
		local z = minZ
		while z < maxZ do
			local sizeZ = math.min(chunk, maxZ - z)
			local ok, err = pcall(function()
				terrain:FillBlock(
					CFrame.new(x + sizeX / 2, surface - depth / 2, z + sizeZ / 2),
					Vector3.new(sizeX, depth, sizeZ),
					Enum.Material.Water
				)
			end)
			if not ok then
				warn("[MapBuilder] water fill failed: " .. tostring(err))
				return
			end
			z += sizeZ
		end
		x += sizeX
	end
end

local function applyLighting()
	local cc = Lighting:FindFirstChild("BaseColorCorrection")
	if not (cc and cc:IsA("ColorCorrectionEffect")) then
		local created = Instance.new("ColorCorrectionEffect")
		created.Name = "BaseColorCorrection"
		created.Parent = Lighting
		cc = created
	end
	local colorCorrection = cc :: ColorCorrectionEffect
	colorCorrection.Saturation = 0.25
	colorCorrection.Contrast = 0.08
	colorCorrection.Brightness = 0.02
	colorCorrection.TintColor = Color3.new(1, 1, 1)
	colorCorrection.Enabled = true

	local bl = Lighting:FindFirstChild("BaseBloom")
	if not (bl and bl:IsA("BloomEffect")) then
		local created = Instance.new("BloomEffect")
		created.Name = "BaseBloom"
		created.Parent = Lighting
		bl = created
	end
	local bloom = bl :: BloomEffect
	bloom.Intensity = 0.35 -- subtle: only neon parts glow
	bloom.Size = 20
	bloom.Threshold = 1.6
	bloom.Enabled = true

	-- Keep the air clear and bright (no heavy fog), like both reference images.
	Lighting.FogEnd = 100000
end

-------------------------------------------------------------------------------
-- Service
-------------------------------------------------------------------------------
function MapBuilder:Init(_registry: { [string]: any })
	local existing = Workspace:FindFirstChild("Map")
	if existing then
		print("[MapBuilder] workspace.Map already exists, keeping it (terrain + lighting still applied)")
	else
		local map = Instance.new("Folder")
		map.Name = "Map"
		for areaIndex = 1, #Config.Areas do
			buildArea(map, areaIndex)
			if areaIndex < #Config.Areas then
				buildCorridor(map, areaIndex)
			end
		end
		buildSpawn(map)
		buildPortal(map)
		-- Parent last: one replication burst instead of thousands of small ones.
		map.Parent = Workspace
		print(("[MapBuilder] built the base map (%d parts)"):format(partCount))
	end

	applyLighting()
	-- Terrain filling can take a moment; do it without delaying other services.
	task.spawn(fillWater)
end

function MapBuilder:Start() end

function MapBuilder:GetPartCount(): number
	return partCount
end

return MapBuilder
