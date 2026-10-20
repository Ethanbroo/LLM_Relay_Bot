--!strict
--[[
	BuildingFactory (client)
	Builds the chunky, colourful toy models of the home base out of plain
	Parts (no meshes, no image assets), in the style of reference image 1.

	Public API:
		BuildingFactory.Build(slotDef, cframe): Model
			A revenue building in the style `slotDef.style`
			("stand" | "shop" | "diner" | "hut" | "tent" | "station" | "garage" |
			 "tower" | "hangar"; "arena" builds the Combat Arena) coloured with
			`slotDef.color`. Every building has a striped awning, a counter,
			crates and a sign with the name + icon emoji.
			`cframe` = centre of the plot on the floor, LookVector = the side
			that faces the walkway (the front of the building).
			The model contains an invisible part named "Top" above the roof
			(use it for billboards / floating text).
		BuildingFactory.BuildArena(slotDef, cframe): Model    -- ring wall, flags, "COMBAT ARENA" sign
		BuildingFactory.BuildEmployee(cframe): Model          -- blocky NPC: black suit, white shirt, red tie
		BuildingFactory.BuildGate(cframe, width): Model       -- black iron gate with spikes + gold padlock
			(`cframe` LookVector = the side the player arrives from; the gate spans `width` studs across)
		BuildingFactory.FlatTile(parent, cframe, sizeX, sizeZ, fill, border, borderStuds): Part
			A thin floor tile drawn with a SurfaceGui: rounded rectangle `fill`
			with a `border` band (unlock pads, the "Hire me" tile).
		BuildingFactory.Part(parent, spec): Part              -- anchored SmoothPlastic part helper
		BuildingFactory.FaceText(part, face, text, background?, textColor?): SurfaceGui

	All parts are Anchored, SmoothPlastic, CanTouch = false. Decorative parts
	do not collide (so nobody gets stuck); building bodies do collide.
	"Forward" below means towards the walkway (the CFrame's LookVector).
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)

local P = Config.Palette

local BuildingFactory = {}

export type PartSpec = {
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

local function darker(color: Color3, amount: number): Color3
	local h, s, v = color:ToHSV()
	return Color3.fromHSV(h, s, math.clamp(v * (1 - amount), 0, 1))
end

local function lighter(color: Color3, amount: number): Color3
	return color:Lerp(Color3.new(1, 1, 1), amount)
end

-- Pick a contrasting stripe colour for awnings (white on dark colours,
-- a darker shade on very light colours such as white tents).
local function stripeColor(color: Color3): Color3
	local _, s, v = color:ToHSV()
	if v > 0.9 and s < 0.15 then
		return P.ButtonRed
	end
	return P.White
end

function BuildingFactory.Part(parent: Instance, spec: PartSpec): Part
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
	return part
end
local Part = BuildingFactory.Part

-- Builder for one model: helpers take positions in the building's local
-- space: x = right, y = up from the floor, f = forward (towards the walkway).
type Builder = {
	model: Model,
	cf: CFrame,
	at: (x: number, y: number, f: number) -> CFrame,
	box: (name: string, x: number, y: number, f: number, size: Vector3, color: Color3, collide: boolean?) -> Part,
	cyl: (
		name: string,
		x: number,
		y: number,
		f: number,
		diameter: number,
		height: number,
		color: Color3,
		collide: boolean?
	) -> Part,
	ball: (name: string, x: number, y: number, f: number, diameter: number, color: Color3) -> Part,
}

local function newBuilder(name: string, cf: CFrame): Builder
	local model = Instance.new("Model")
	model.Name = name
	local function at(x: number, y: number, f: number): CFrame
		return cf * CFrame.new(x, y, -f)
	end
	local b: Builder = {
		model = model,
		cf = cf,
		at = at,
		box = function(
			partName: string,
			x: number,
			y: number,
			f: number,
			size: Vector3,
			color: Color3,
			collide: boolean?
		): Part
			return Part(
				model,
				{ Name = partName, Size = size, CFrame = at(x, y, f), Color = color, CanCollide = collide }
			)
		end,
		cyl = function(
			partName: string,
			x: number,
			y: number,
			f: number,
			diameter: number,
			height: number,
			color: Color3,
			collide: boolean?
		): Part
			return Part(model, {
				Name = partName,
				Shape = Enum.PartType.Cylinder,
				Size = Vector3.new(height, diameter, diameter),
				CFrame = at(x, y, f) * CFrame.Angles(0, 0, math.rad(90)),
				Color = color,
				CanCollide = collide,
			})
		end,
		ball = function(partName: string, x: number, y: number, f: number, diameter: number, color: Color3): Part
			return Part(model, {
				Name = partName,
				Shape = Enum.PartType.Ball,
				Size = Vector3.new(diameter, diameter, diameter),
				CFrame = at(x, y, f),
				Color = color,
				CanCollide = false,
			})
		end,
	}
	return b
end

-- White outlined text on one face of a part.
function BuildingFactory.FaceText(
	part: BasePart,
	face: Enum.NormalId,
	text: string,
	background: Color3?,
	textColor: Color3?
): SurfaceGui
	local gui = Instance.new("SurfaceGui")
	gui.Name = "Text" .. face.Name
	gui.Face = face
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 28
	gui.LightInfluence = 0
	gui.Parent = part

	local label = Instance.new("TextLabel")
	label.Name = "Label"
	label.Size = UDim2.fromScale(1, 1)
	label.BackgroundColor3 = background or P.PillBlack
	label.BackgroundTransparency = if background then 0 else 1
	label.Font = Config.Font
	label.Text = text
	label.TextColor3 = textColor or P.White
	label.TextScaled = true
	label.Parent = gui
	local padding = Instance.new("UIPadding")
	padding.PaddingTop = UDim.new(0.1, 0)
	padding.PaddingBottom = UDim.new(0.1, 0)
	padding.PaddingLeft = UDim.new(0.05, 0)
	padding.PaddingRight = UDim.new(0.05, 0)
	padding.Parent = label
	local stroke = Instance.new("UIStroke")
	stroke.Color = P.TextStroke
	stroke.Thickness = 4
	stroke.LineJoinMode = Enum.LineJoinMode.Round
	stroke.Parent = label
	return gui
end

-- Thin floor tile with a rounded-rectangle picture on top. The picture is
-- symmetric, so it looks the same from every side.
function BuildingFactory.FlatTile(
	parent: Instance,
	cframe: CFrame,
	sizeX: number,
	sizeZ: number,
	fill: Color3,
	border: Color3,
	borderStuds: number
): Part
	local tile = Part(parent, {
		Name = "Tile",
		Size = Vector3.new(sizeX, 0.3, sizeZ),
		CFrame = cframe,
		Color = fill,
		Transparency = 1,
		CanCollide = false,
		CastShadow = false,
	})
	tile.CanQuery = false
	local pixelsPerStud = 20
	local gui = Instance.new("SurfaceGui")
	gui.Name = "TileGui"
	gui.Face = Enum.NormalId.Top
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = pixelsPerStud
	gui.LightInfluence = 0.4
	gui.Parent = tile
	local outer = Instance.new("Frame")
	outer.Name = "Border"
	outer.Size = UDim2.fromScale(1, 1)
	outer.BackgroundColor3 = border
	outer.Parent = gui
	local outerCorner = Instance.new("UICorner")
	outerCorner.CornerRadius = UDim.new(0, math.floor(math.min(sizeX, sizeZ) * pixelsPerStud * 0.22))
	outerCorner.Parent = outer
	local inset = math.floor(borderStuds * pixelsPerStud)
	local inner = Instance.new("Frame")
	inner.Name = "Fill"
	inner.AnchorPoint = Vector2.new(0.5, 0.5)
	inner.Position = UDim2.fromScale(0.5, 0.5)
	inner.Size = UDim2.new(1, -inset * 2, 1, -inset * 2)
	inner.BackgroundColor3 = fill
	inner.Parent = outer
	local innerCorner = Instance.new("UICorner")
	innerCorner.CornerRadius = UDim.new(0, math.max(4, outerCorner.CornerRadius.Offset - inset))
	innerCorner.Parent = inner
	return tile
end

-------------------------------------------------------------------------------
-- Shared building pieces
-------------------------------------------------------------------------------

-- Striped awning sloping down towards the front.
local function awning(b: Builder, width: number, f: number, y: number, color: Color3)
	local stripes = math.max(4, math.floor(width / 2))
	local stripeW = width / stripes
	local other = stripeColor(color)
	for i = 0, stripes - 1 do
		local x = -width / 2 + (i + 0.5) * stripeW
		local c = if i % 2 == 0 then color else other
		local part = Part(b.model, {
			Name = "Awning",
			Size = Vector3.new(stripeW, 0.3, 4),
			CFrame = b.at(x, y, f + 1.8) * CFrame.Angles(math.rad(-22), 0, 0),
			Color = c,
			CanCollide = false,
		})
		-- little hanging flap at the front edge
		Part(b.model, {
			Name = "AwningFlap",
			Size = Vector3.new(stripeW, 0.7, 0.2),
			CFrame = part.CFrame * CFrame.new(0, -0.35, -2),
			Color = c,
			CanCollide = false,
		})
	end
end

-- Shop counter: coloured front with a white top.
local function counter(b: Builder, width: number, f: number, color: Color3)
	b.box("Counter", 0, 1.5, f, Vector3.new(width, 3, 2), darker(color, 0.15), true)
	b.box("CounterTop", 0, 3.15, f, Vector3.new(width + 0.4, 0.3, 2.4), P.White, false)
end

-- Wooden crates beside the building.
local function crates(b: Builder, x: number, f: number)
	b.box("Crate", x, 1, f, Vector3.new(2, 2, 2), P.Wood, true)
	b.box("Crate", x + 0.3, 2.9, f + 0.2, Vector3.new(1.8, 1.8, 1.8), darker(P.Wood, 0.12), false)
	b.box(
		"Crate",
		x + (if x > 0 then -2.3 else 2.3),
		0.8,
		f + 0.4,
		Vector3.new(1.6, 1.6, 1.6),
		lighter(P.Wood, 0.12),
		false
	)
end

-- Name sign with the icon emoji, facing forward.
local function sign(b: Builder, def: Config.SlotDef, width: number, y: number, f: number, color: Color3)
	local board = b.box("Sign", 0, y, f, Vector3.new(width, 3, 0.5), darker(color, 0.35), false)
	BuildingFactory.FaceText(board, Enum.NormalId.Front, def.icon .. " " .. def.name, darker(color, 0.35))
end

-- Window: light-blue glass pane.
local function window(b: Builder, x: number, y: number, f: number, w: number, h: number)
	b.box("Window", x, y, f, Vector3.new(w, h, 0.2), lighter(P.PanelBlue, 0.45), false)
end

local function finish(b: Builder, topHeight: number): Model
	local top = b.box("Top", 0, topHeight, 0, Vector3.new(1, 1, 1), P.White, false)
	top.Transparency = 1
	top.CanQuery = false
	top.CastShadow = false
	local root = b.box("Root", 0, 0.5, 0, Vector3.new(1, 1, 1), P.White, false)
	root.Transparency = 1
	root.CanQuery = false
	root.CastShadow = false
	b.model.PrimaryPart = root
	b.model:SetAttribute("TopHeight", topHeight)
	return b.model
end

-------------------------------------------------------------------------------
-- Building styles (each about 25-45 parts, footprint within ±13 studs)
-------------------------------------------------------------------------------
local Styles: { [string]: (b: Builder, def: Config.SlotDef, color: Color3) -> number } = {}

-- Open market stall: back wall, four posts, roof, awning, counter.
Styles.stand = function(b, def, color)
	b.box("BackWall", 0, 3.5, -5, Vector3.new(16, 7, 1), color, true)
	for _, x in { -7.5, 7.5 } do
		for _, f in { -4.5, 3.5 } do
			b.box("Post", x, 3.5, f, Vector3.new(0.8, 7, 0.8), P.White, true)
		end
	end
	b.box("Roof", 0, 7.3, -0.5, Vector3.new(17, 0.6, 10.5), lighter(color, 0.15), true)
	b.box("Floor", 0, 0.15, -0.5, Vector3.new(16, 0.3, 10), lighter(color, 0.55), false)
	awning(b, 16, 4.5, 6.8, color)
	counter(b, 13, 3.4, color)
	b.box("Goods", -3, 3.8, 3.4, Vector3.new(2.2, 1, 1.4), P.CardOrange, false)
	b.box("Goods", 0.5, 3.8, 3.4, Vector3.new(2.2, 1, 1.4), P.ButtonGreen, false)
	b.box("Goods", 4, 3.8, 3.4, Vector3.new(2.2, 1, 1.4), P.Coin, false)
	crates(b, -10.5, 2)
	crates(b, 10.5, -2)
	sign(b, def, 15, 9.4, -0.5, color)
	return 12
end

-- Shop: a box with a big front window, door, flat roof with a white rim.
Styles.shop = function(b, def, color)
	b.box("Body", 0, 5.5, -3, Vector3.new(22, 11, 16), color, true)
	b.box("Roof", 0, 11.4, -3, Vector3.new(23, 0.8, 17), P.White, true)
	b.box("RoofRim", 0, 12.1, 4.9, Vector3.new(23, 0.6, 1.2), darker(color, 0.25), false)
	window(b, -3, 4.6, 5.05, 12, 5.2)
	b.box("WindowFrame", -3, 7.4, 5.1, Vector3.new(12.6, 0.4, 0.3), P.White, false)
	b.box("Door", 6.5, 3.5, 5.05, Vector3.new(4, 7, 0.2), darker(color, 0.45), false)
	b.box("DoorKnob", 5.2, 3.5, 5.2, Vector3.new(0.4, 0.4, 0.2), P.Coin, false)
	awning(b, 21, 5, 7.6, color)
	counter(b, 9, 7.5, color)
	crates(b, -10.5, 7.5)
	sign(b, def, 18, 9.9, 5.3, color)
	return 14
end

-- Diner: wide body with rounded ends and a doughnut sign on the roof.
Styles.diner = function(b, def, color)
	b.box("Body", 0, 4.5, -2, Vector3.new(16, 9, 14), color, true)
	b.cyl("RoundEnd", -8, 4.5, -2, 14, 9, color, true)
	b.cyl("RoundEnd", 8, 4.5, -2, 14, 9, color, true)
	b.box("Roof", 0, 9.3, -2, Vector3.new(16, 0.6, 14.4), P.White, true)
	b.cyl("RoofEnd", -8, 9.3, -2, 14.4, 0.6, P.White, true)
	b.cyl("RoofEnd", 8, 9.3, -2, 14.4, 0.6, P.White, true)
	-- stripe band around the middle
	b.box("Stripe", 0, 2.2, 5.05, Vector3.new(16, 1, 0.2), P.White, false)
	window(b, 0, 5.3, 5.05, 14, 3.6)
	-- giant doughnut sign (image 1): a pink disc with a white hole
	local donut = b.cyl("Donut", 0, 14, -2, 7, 1.4, P.Pink, false)
	donut.CFrame = b.at(0, 14, -2) * CFrame.Angles(0, math.rad(90), 0)
	local hole = b.cyl("DonutHole", 0, 14, -2, 2.6, 1.5, P.White, false)
	hole.CFrame = b.at(0, 14, -2) * CFrame.Angles(0, math.rad(90), 0)
	for i, sprinkle in { P.CardBlue, P.Coin, P.ButtonGreen, P.White } do
		local angle = math.rad(i * 90 + 20)
		b.box(
			"Sprinkle",
			math.cos(angle) * 2.4,
			14 + math.sin(angle) * 2.4,
			-1.25,
			Vector3.new(0.9, 0.3, 0.1),
			sprinkle,
			false
		)
	end
	b.box("DonutPost", 0, 11, -2, Vector3.new(0.6, 3, 0.6), P.GateIron, false)
	awning(b, 14, 5.2, 7.2, color)
	counter(b, 8, 7.6, color)
	crates(b, 10.5, 7)
	sign(b, def, 14, 11, 4.6, color)
	return 18
end

-- Round hut with a stepped cone roof and a barber pole.
Styles.hut = function(b, def, color)
	b.cyl("Body", 0, 4, -2, 15, 8, color, true)
	b.cyl("Roof1", 0, 8.6, -2, 17, 1.2, P.White, true)
	b.cyl("Roof2", 0, 9.8, -2, 12.5, 1.2, color, false)
	b.cyl("Roof3", 0, 11, -2, 8, 1.2, P.White, false)
	b.cyl("Roof4", 0, 12.2, -2, 3.6, 1.2, color, false)
	b.ball("RoofBall", 0, 13.3, -2, 1.6, P.Coin)
	b.box("Door", 0, 3, 5.4, Vector3.new(4, 6, 0.6), darker(color, 0.45), false)
	-- barber pole: red / white / blue segments
	for i, c in { P.ButtonRed, P.White, P.CardBlue, P.White, P.ButtonRed } do
		b.cyl("Pole", 6.5, 0.55 + (i - 1) * 1.1, 6, 1.2, 1.1, c, false)
	end
	b.ball("PoleTop", 6.5, 5.8, 6, 1.4, P.White)
	awning(b, 10, 5.8, 6.6, color)
	counter(b, 6, 8.2, color)
	crates(b, -9.5, 6)
	sign(b, def, 12, 9.9, 7, color)
	return 15
end

-- Army tent: low walls with a triangular prism roof (a diamond block, half
-- of it hidden inside the walls).
Styles.tent = function(b, def, color)
	local depth = 14
	b.box("Walls", 0, 1.8, -2, Vector3.new(20, 3.6, depth), color, true)
	local side = depth / math.sqrt(2)
	local roof = b.box("Roof", 0, 3.6, -2, Vector3.new(20.4, side, side), color, true)
	roof.CFrame = b.at(0, 3.6, -2) * CFrame.Angles(math.rad(45), 0, 0)
	b.box("Ridge", 0, 3.6 + depth / 2, -2, Vector3.new(20.8, 0.6, 0.6), darker(color, 0.3), false)
	b.box("DoorFlap", 0, 2.6, 5.05, Vector3.new(5, 5.2, 0.2), darker(color, 0.4), false)
	-- emblem disc on the roof front (e.g. a med tent cross colour)
	local emblem = b.cyl("Emblem", 0, 7, 1.55, 3.6, 0.3, P.ButtonRed, false)
	emblem.CFrame = b.at(0, 7.2, 1.55) * CFrame.Angles(math.rad(45), math.rad(90), 0)
	for _, x in { -10.6, 10.6 } do
		b.box("Peg", x, 0.8, 5.5, Vector3.new(0.4, 1.6, 0.4), P.Wood, false)
	end
	awning(b, 12, 3.6, 4.2, color)
	counter(b, 10, 7.6, color)
	crates(b, 11, 7)
	sign(b, def, 12, 12.6, -2, color)
	return 14
end

-- Station: canopy on pillars, a kiosk at the back and two pumps.
Styles.station = function(b, def, color)
	b.box("Kiosk", 0, 3.5, -6, Vector3.new(12, 7, 8), P.White, true)
	window(b, -2, 3.8, -1.95, 6, 3.5)
	b.box("KioskDoor", 4, 3, -1.95, Vector3.new(2.6, 6, 0.2), darker(color, 0.4), false)
	b.box("KioskStripe", 0, 6.4, -1.95, Vector3.new(12, 0.8, 0.2), color, false)
	for _, x in { -9, 9 } do
		for _, f in { -1, 6 } do
			b.box("Pillar", x, 4.5, f, Vector3.new(1, 9, 1), P.White, true)
		end
	end
	b.box("Canopy", 0, 9.5, 2.5, Vector3.new(21, 1.2, 12), P.White, true)
	b.box("CanopyEdge", 0, 9.5, 8.55, Vector3.new(21, 1.2, 0.2), color, false)
	for _, x in { -4, 4 } do
		b.box("Pump", x, 1.8, 4.5, Vector3.new(1.8, 3.6, 1.4), color, true)
		b.box("PumpScreen", x, 2.8, 5.25, Vector3.new(1.2, 0.8, 0.1), P.PillBlack, false)
		b.box("PumpTop", x, 3.8, 4.5, Vector3.new(2, 0.4, 1.6), P.White, false)
	end
	awning(b, 10, -1.9, 6.2, color)
	counter(b, 6, 9, color)
	crates(b, -11, 8)
	sign(b, def, 16, 11.6, 8.4, color)
	return 13
end

-- Garage: box with a big striped roll-up door and a roof sign.
Styles.garage = function(b, def, color)
	b.box("Body", 0, 5, -3, Vector3.new(22, 10, 16), color, true)
	b.box("Roof", 0, 10.4, -3, Vector3.new(23, 0.8, 17), P.White, true)
	for i = 0, 4 do
		b.box(
			"DoorSlat",
			-2,
			1 + i * 1.5,
			5.05,
			Vector3.new(12, 1.3, 0.2),
			if i % 2 == 0 then P.ButtonGrey else P.White,
			false
		)
	end
	b.box("DoorFrame", -2, 8.1, 5.1, Vector3.new(12.6, 0.6, 0.3), darker(color, 0.3), false)
	window(b, 7.5, 5, 5.05, 4, 3)
	b.box("Tyre", 9.5, 0.9, 7.5, Vector3.new(1.8, 1.8, 0.8), P.PillBlack, false)
	b.box("Tyre", 9.5, 2.6, 7.5, Vector3.new(1.8, 1.6, 0.8), P.PillBlack, false)
	awning(b, 8, 5, 7.6, color)
	counter(b, 6, 9, color)
	crates(b, -10.5, 7.5)
	sign(b, def, 16, 12.2, -3, color)
	return 14
end

-- Tower: shop at the base, tall column, platform and a dish on top.
Styles.tower = function(b, def, color)
	b.box("Base", 0, 3.5, 0, Vector3.new(14, 7, 10), color, true)
	b.box("BaseRoof", 0, 7.3, 0, Vector3.new(15, 0.6, 11), P.White, true)
	window(b, -2.5, 3.8, 5.05, 6, 3.4)
	b.box("Door", 4, 3, 5.05, Vector3.new(2.8, 6, 0.2), darker(color, 0.4), false)
	b.cyl("Column", 0, 14, -2, 4.5, 14, P.White, true)
	for i = 0, 2 do
		b.cyl("ColumnBand", 0, 10 + i * 4, -2, 4.8, 1, color, false)
	end
	b.box("Platform", 0, 21.4, -2, Vector3.new(9, 0.8, 9), color, false)
	b.box("Railing", 0, 22.4, 2.3, Vector3.new(9, 1.2, 0.3), P.White, false)
	local dish = b.ball("Dish", 0, 24, -2, 4.4, P.White)
	dish.Size = Vector3.new(4.4, 4.4, 4.4)
	b.box("Antenna", 0, 26.5, -2, Vector3.new(0.3, 4, 0.3), P.GateIron, false)
	local tip = b.ball("AntennaTip", 0, 28.6, -2, 0.8, P.ButtonRed)
	tip.Material = Enum.Material.Neon
	awning(b, 13, 5, 6.6, color)
	counter(b, 7, 7.8, color)
	crates(b, -9.5, 6)
	sign(b, def, 12, 9.2, 3, color)
	return 25
end

-- Hangar: a big arched roof (a cylinder half sunk into the floor).
Styles.hangar = function(b, def, color)
	local arch = b.cyl("Arch", 0, 0, -3, 22, 15, color, true)
	arch.CFrame = b.at(0, 0, -3) * CFrame.Angles(0, math.rad(90), 0)
	-- white ribs over the arch
	for _, f in { -9, -3, 3 } do
		local rib = b.cyl("Rib", 0, 0, f, 22.4, 0.6, P.White, false)
		rib.CFrame = b.at(0, 0, f) * CFrame.Angles(0, math.rad(90), 0)
	end
	b.box("Doorway", 0, 3.6, 4.55, Vector3.new(12, 7.2, 0.2), darker(color, 0.5), false)
	b.box("DoorStripe", 0, 7.4, 4.6, Vector3.new(12.4, 0.5, 0.25), P.Coin, false)
	-- 4.6: in front of the arch's front face (z 4.5), so they do not z-fight with it
	window(b, -7.8, 4, 4.6, 2.5, 2.5)
	window(b, 7.8, 4, 4.6, 2.5, 2.5)
	awning(b, 10, 5.5, 7, color)
	counter(b, 7, 8.4, color)
	crates(b, 10.5, 7.5)
	sign(b, def, 14, 12.6, 0.8, color)
	return 14
end

-- Combat Arena: octagon ring wall with an open front, flags and a big sign.
local function arenaStyle(b: Builder, def: Config.SlotDef, color: Color3): number
	local radius = 12.5
	b.cyl("ArenaFloor", 0, 0.12, 0, radius * 2, 0.22, lighter(P.Wood, 0.5), false)
	b.cyl("ArenaRing", 0, 0.25, 0, 8, 0.1, P.White, false)
	for i = 0, 7 do
		local angle = math.rad(i * 45)
		-- leave the front segment (pointing at the walkway) open as the entrance
		if i ~= 0 then
			local x = math.sin(angle) * radius
			local f = math.cos(angle) * radius
			local wall =
				b.box("RingWall", x, 2.5, f, Vector3.new(10.2, 5, 1.6), if i % 2 == 0 then color else P.White, true)
			wall.CFrame = b.at(x, 2.5, f) * CFrame.Angles(0, -angle, 0)
			b.box("RingTop", x, 5.2, f, Vector3.new(10.4, 0.4, 2), darker(color, 0.25), false).CFrame = b.at(x, 5.2, f)
				* CFrame.Angles(0, -angle, 0)
		end
	end
	-- Flags on poles around the ring.
	for i, angle in { 45, 135, 225, 315 } do
		local a = math.rad(angle)
		local x, f = math.sin(a) * (radius + 1.5), math.cos(a) * (radius + 1.5)
		b.box("FlagPole", x, 6, f, Vector3.new(0.4, 12, 0.4), P.GateIron, false)
		b.box("Flag", x + 1.6, 10.8, f, Vector3.new(3, 2, 0.15), if i % 2 == 0 then P.White else P.ButtonRed, false)
	end
	-- Entrance arch with the sign.
	local archF = radius + 0.5
	for _, x in { -5.2, 5.2 } do
		b.box("ArchPost", x, 5, archF, Vector3.new(1.4, 10, 1.4), darker(color, 0.2), true)
	end
	local board = b.box("ArenaSign", 0, 10.2, archF, Vector3.new(13, 3, 0.6), P.PillBlack, false)
	BuildingFactory.FaceText(board, Enum.NormalId.Front, def.icon .. " COMBAT ARENA", P.PillBlack)
	-- Two practice targets inside.
	for _, x in { -4, 4 } do
		local target = b.cyl("Target", x, 2.4, -5, 3, 0.4, P.White, false)
		target.CFrame = b.at(x, 2.4, -5) * CFrame.Angles(0, math.rad(90), 0)
		local bull = b.cyl("Bullseye", x, 2.4, -4.75, 1.4, 0.45, P.ButtonRed, false)
		bull.CFrame = b.at(x, 2.4, -4.75) * CFrame.Angles(0, math.rad(90), 0)
		b.box("TargetLeg", x, 0.6, -5, Vector3.new(0.3, 1.2, 0.3), P.Wood, false)
	end
	crates(b, 10.5, 9)
	return 13
end

function BuildingFactory.Build(slotDef: Config.SlotDef, cframe: CFrame): Model
	if slotDef.kind == "arena" or slotDef.style == "arena" then
		return BuildingFactory.BuildArena(slotDef, cframe)
	end
	local b = newBuilder("Building_" .. slotDef.id, cframe)
	local builder = Styles[slotDef.style] or Styles.shop
	local topHeight = builder(b, slotDef, slotDef.color)
	return finish(b, topHeight)
end

function BuildingFactory.BuildArena(slotDef: Config.SlotDef, cframe: CFrame): Model
	local b = newBuilder("Arena_" .. slotDef.id, cframe)
	local topHeight = arenaStyle(b, slotDef, slotDef.color)
	return finish(b, topHeight)
end

-------------------------------------------------------------------------------
-- Employee NPC (black suit, white shirt, red tie) - like the "Hire me" man
-------------------------------------------------------------------------------
function BuildingFactory.BuildEmployee(cframe: CFrame): Model
	local b = newBuilder("Employee", cframe)
	local suit = P.PillBlack
	local skin = P.CoinShine -- classic blocky toy skin tone from the palette
	for _, x in { -0.5, 0.5 } do
		b.box("Leg", x, 1, 0, Vector3.new(0.95, 2, 1), suit, false)
		b.box("Shoe", x, 0.15, 0.15, Vector3.new(1, 0.3, 1.3), P.TextStroke, false)
	end
	b.box("Torso", 0, 3, 0, Vector3.new(2, 2, 1), suit, false)
	b.box("Shirt", 0, 3.25, 0.52, Vector3.new(0.8, 1.5, 0.05), P.White, false)
	b.box("Tie", 0, 3.15, 0.56, Vector3.new(0.3, 1.2, 0.05), P.ButtonRed, false)
	for _, x in { -1.5, 1.5 } do
		b.box("Arm", x, 3, 0, Vector3.new(1, 2, 1), suit, false)
		b.box("Hand", x, 1.8, 0, Vector3.new(0.8, 0.5, 0.8), skin, false)
	end
	b.box("Briefcase", 1.5, 1.1, 0.1, Vector3.new(0.5, 1.1, 1.5), P.Wood, false)
	b.box("Head", 0, 4.75, 0, Vector3.new(1.5, 1.5, 1.5), skin, false)
	b.box("Hair", 0, 5.6, -0.1, Vector3.new(1.6, 0.35, 1.6), P.TextStroke, false)
	for _, x in { -0.35, 0.35 } do
		b.box("Eye", x, 4.95, 0.76, Vector3.new(0.2, 0.3, 0.05), P.TextStroke, false)
	end
	b.box("Smile", 0, 4.4, 0.76, Vector3.new(0.6, 0.12, 0.05), P.TextStroke, false)
	return finish(b, 6.5)
end

-------------------------------------------------------------------------------
-- Iron gate with spikes and a big gold padlock (image 1)
-------------------------------------------------------------------------------
function BuildingFactory.BuildGate(cframe: CFrame, width: number): Model
	local b = newBuilder("Gate", cframe)
	local iron = P.GateIron
	local height = 10
	-- posts with gold ball finials
	for _, x in { -(width / 2 + 0.8), width / 2 + 0.8 } do
		b.box("GatePost", x, height / 2 + 0.5, 0, Vector3.new(1.6, height + 1, 1.6), iron, false)
		b.ball("Finial", x, height + 1.5, 0, 1.4, P.Padlock)
	end
	-- vertical bars with diamond spikes on top
	local bars = math.floor(width / 1.25)
	local spacing = width / bars
	for i = 0, bars - 1 do
		local x = -width / 2 + (i + 0.5) * spacing
		b.box("Bar", x, height / 2, 0, Vector3.new(0.3, height, 0.3), iron, false)
		local spike = b.box("Spike", x, height + 0.3, 0, Vector3.new(0.55, 0.55, 0.3), iron, false)
		spike.CFrame = b.at(x, height + 0.3, 0) * CFrame.Angles(0, 0, math.rad(45))
	end
	-- horizontal rails and a decorative middle bar
	for _, y in { 1.2, height - 1.4 } do
		b.box("Rail", 0, y, 0, Vector3.new(width, 0.45, 0.5), iron, false)
	end
	b.box("CentreBar", 0, height / 2, 0.05, Vector3.new(0.6, height, 0.6), iron, false)
	-- big gold padlock hanging on the front (the side the player comes from)
	local lockY = height / 2 - 0.3
	b.box("Padlock", 0, lockY, 0.75, Vector3.new(2.8, 2.4, 1), P.Padlock, false)
	b.box("PadlockShine", -0.7, lockY + 0.5, 1.27, Vector3.new(0.6, 0.9, 0.05), P.CoinShine, false)
	b.box("Keyhole", 0, lockY - 0.2, 1.27, Vector3.new(0.35, 0.8, 0.05), P.TextStroke, false)
	for _, x in { -0.9, 0.9 } do
		b.box("Shackle", x, lockY + 1.75, 0.75, Vector3.new(0.4, 1.3, 0.4), P.CoinShine, false)
	end
	b.box("ShackleTop", 0, lockY + 2.5, 0.75, Vector3.new(2.2, 0.4, 0.4), P.CoinShine, false)
	return finish(b, height + 3)
end

return BuildingFactory
