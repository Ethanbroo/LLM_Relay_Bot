--!strict
--[[
	VehicleModels (shared)
	Builds the chunky, toy-like vehicles out of plain Parts (no meshes, no
	asset ids). Used by the server's VehicleService when a player presses
	"Ride": the model is welded to the character's HumanoidRootPart.

	Public API:
		VehicleModels.Build(def: Config.VehicleDef): Model
			Returns a Model whose PrimaryPart is "Root": an invisible part that
			sits exactly where the HumanoidRootPart goes (the model is built
			around the origin, facing -Z like a character). Every other part is
			already welded to Root with a WeldConstraint and is Massless,
			CanCollide = false, CanQuery = false, CanTouch = false and
			unanchored, so the vehicle never changes how the character walks.
			The caller moves it with model:PivotTo(rootPart.CFrame) and welds
			Root to the HumanoidRootPart.
		VehicleModels.STYLES: { string }   -- every style Build understands
		VehicleModels.GROUND_OFFSET: number -- floor height relative to Root (-3)

	Styles (Config.Vehicles[*].style):
		"bike"  two wheels, frame, handlebars, headlight
		"jeep"  open-top tub, 4 wheels, windscreen, roll bar, spare tyre
		"truck" cab with roof around the driver, bonnet, olive canvas back
		"apc"   6 wheels, armoured box with a sloped front and a hatch
		"tank"  two tracks with road wheels, hull, turret + long barrel
		"hover" floating hull with glowing Neon thrusters (no wheels)
	Unknown styles fall back to "jeep".

	How the character fits: the HumanoidRootPart centre of a normal avatar is
	3 studs above the floor, so the floor is at y = -3 in model space. Every
	body is built so that the legs (floor .. 2 studs up) end up inside it and
	the upper body pokes out like a driver.

	This module does not use any services and creates Instances only when
	Build is called, so requiring it is always safe.
]]

local Config = require(script.Parent.Config)

local P = Config.Palette

local VehicleModels = {}

VehicleModels.STYLES = { "bike", "jeep", "truck", "apc", "tank", "hover" }

-- Floor height relative to the HumanoidRootPart centre.
local G = -3
VehicleModels.GROUND_OFFSET = G

local RAD90 = math.rad(90)
-- A Roblox cylinder's round axis is its X axis. These rotations turn it:
local AXIS_Z = CFrame.Angles(0, RAD90, 0) -- round axis along Z (barrels, spare tyres)
local AXIS_Y = CFrame.Angles(0, 0, RAD90) -- round axis along Y (thrusters)

-- Darker version of a colour (for trims).
local function darken(color: Color3, amount: number): Color3
	local h, s, v = color:ToHSV()
	return Color3.fromHSV(h, s, math.clamp(v * (1 - amount), 0, 1))
end

type Kit = {
	model: Model,
	root: Part,
	body: Color3,
	trim: Color3,
}

type PartOptions = {
	shape: Enum.PartType?,
	material: Enum.Material?,
	transparency: number?,
	wedge: boolean?,
}

-- Create one welded, massless, non-colliding part. `cf` is relative to Root.
local function add(kit: Kit, name: string, size: Vector3, cf: CFrame, color: Color3, options: PartOptions?): BasePart
	local part: BasePart
	if options and options.wedge then
		part = Instance.new("WedgePart")
	else
		local p = Instance.new("Part")
		if options and options.shape then
			p.Shape = options.shape
		end
		part = p
	end
	part.Name = name
	part.Size = size
	part.CFrame = cf
	part.Color = color
	part.Material = if options and options.material then options.material else Enum.Material.SmoothPlastic
	part.Transparency = if options and options.transparency then options.transparency else 0
	part.Anchored = false
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.Massless = true
	part.CastShadow = true
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Parent = kit.model

	local weld = Instance.new("WeldConstraint")
	weld.Name = "VehicleWeld"
	weld.Part0 = kit.root
	weld.Part1 = part
	weld.Parent = part
	return part
end

local function box(kit: Kit, name: string, size: Vector3, pos: Vector3, color: Color3): BasePart
	return add(kit, name, size, CFrame.new(pos), color)
end

-- A wheel standing on the floor: tyre + lighter hub. Axle runs along X.
local function wheel(kit: Kit, x: number, z: number, radius: number, width: number)
	local centre = CFrame.new(x, G + radius, z)
	add(
		kit,
		"Tyre",
		Vector3.new(width, radius * 2, radius * 2),
		centre,
		P.PillBlack,
		{ shape = Enum.PartType.Cylinder }
	)
	add(
		kit,
		"Hub",
		Vector3.new(width + 0.1, radius * 1.0, radius * 1.0),
		centre,
		P.ButtonGrey,
		{ shape = Enum.PartType.Cylinder }
	)
end

-- Small glowing light (headlights, tail lights).
local function light(kit: Kit, pos: Vector3, size: number, color: Color3)
	add(kit, "Light", Vector3.new(size, size, 0.3), CFrame.new(pos), color, { material = Enum.Material.Neon })
end

-- Thin dark bar between two points (frames, roll bars, pillars).
local function bar(kit: Kit, a: Vector3, b: Vector3, thickness: number, color: Color3)
	local length = (b - a).Magnitude
	if length < 0.01 then
		return
	end
	local cf = CFrame.lookAt((a + b) / 2, b)
	add(kit, "Bar", Vector3.new(thickness, thickness, length), cf, color)
end

local function glass(kit: Kit, size: Vector3, pos: Vector3)
	add(kit, "Glass", size, CFrame.new(pos), P.TrackRail, { transparency = 0.45 })
end

-------------------------------------------------------------------------------
-- Styles. Front of the vehicle = -Z (the way the character looks).
-------------------------------------------------------------------------------

local function buildBike(kit: Kit)
	-- Wheels front and back.
	wheel(kit, 0, -2.5, 1.15, 0.7)
	wheel(kit, 0, 2.5, 1.15, 0.7)
	-- Fairing that hides the legs.
	box(kit, "Body", Vector3.new(2.4, 2.4, 3.2), Vector3.new(0, G + 1.7, 0), kit.body)
	box(kit, "StripeL", Vector3.new(0.1, 0.5, 3.0), Vector3.new(-1.25, G + 2.1, 0), kit.trim)
	box(kit, "StripeR", Vector3.new(0.1, 0.5, 3.0), Vector3.new(1.25, G + 2.1, 0), kit.trim)
	-- Seat on top, tank in front of the rider.
	box(kit, "Seat", Vector3.new(2.0, 0.4, 1.8), Vector3.new(0, G + 3.05, 0.6), P.GatePost)
	box(kit, "FuelTank", Vector3.new(2.0, 0.9, 1.2), Vector3.new(0, G + 3.0, -1.0), kit.trim)
	-- Front fork + handlebars + headlight.
	bar(kit, Vector3.new(-0.45, G + 1.15, -2.5), Vector3.new(-0.45, G + 3.6, -1.9), 0.25, P.GatePost)
	bar(kit, Vector3.new(0.45, G + 1.15, -2.5), Vector3.new(0.45, G + 3.6, -1.9), 0.25, P.GatePost)
	add(
		kit,
		"Handlebar",
		Vector3.new(3.0, 0.25, 0.25),
		CFrame.new(0, G + 3.7, -1.85),
		P.GatePost,
		{ shape = Enum.PartType.Cylinder }
	)
	box(kit, "GripL", Vector3.new(0.5, 0.35, 0.35), Vector3.new(-1.45, G + 3.7, -1.85), P.PillBlack)
	box(kit, "GripR", Vector3.new(0.5, 0.35, 0.35), Vector3.new(1.45, G + 3.7, -1.85), P.PillBlack)
	box(kit, "Lamp", Vector3.new(0.9, 0.9, 0.5), Vector3.new(0, G + 3.0, -2.1), kit.trim)
	light(kit, Vector3.new(0, G + 3.0, -2.4), 0.6, P.CoinShine)
	light(kit, Vector3.new(0, G + 2.4, 1.65), 0.4, P.ButtonRed)
	-- Mudguards.
	box(kit, "GuardF", Vector3.new(0.9, 0.2, 1.6), Vector3.new(0, G + 2.45, -2.5), kit.body)
	box(kit, "GuardB", Vector3.new(0.9, 0.2, 1.6), Vector3.new(0, G + 2.45, 2.6), kit.body)
end

local function buildJeep(kit: Kit)
	-- Wheels.
	for _, x in { -2.7, 2.7 } do
		for _, z in { -3.2, 3.2 } do
			wheel(kit, x, z, 1.2, 1.0)
		end
	end
	-- Chassis + running boards (hide the lower legs from the side).
	box(kit, "Chassis", Vector3.new(4.6, 0.7, 8.6), Vector3.new(0, G + 0.75, 0.2), P.GatePost)
	box(kit, "BoardL", Vector3.new(0.5, 0.8, 3.6), Vector3.new(-2.55, G + 1.0, 0), kit.trim)
	box(kit, "BoardR", Vector3.new(0.5, 0.8, 3.6), Vector3.new(2.55, G + 1.0, 0), kit.trim)
	-- Open-top tub.
	box(kit, "Tub", Vector3.new(5.0, 1.9, 7.4), Vector3.new(0, G + 2.05, 0.9), kit.body)
	box(kit, "TubRim", Vector3.new(5.2, 0.3, 7.6), Vector3.new(0, G + 3.1, 0.9), kit.trim)
	-- Bonnet in front.
	box(kit, "Bonnet", Vector3.new(5.0, 1.6, 2.4), Vector3.new(0, G + 2.0, -4.0), kit.body)
	box(kit, "Grille", Vector3.new(4.0, 1.0, 0.2), Vector3.new(0, G + 1.9, -5.25), P.GatePost)
	light(kit, Vector3.new(-1.7, G + 2.2, -5.3), 0.6, P.CoinShine)
	light(kit, Vector3.new(1.7, G + 2.2, -5.3), 0.6, P.CoinShine)
	-- Wheel arches.
	for _, x in { -2.7, 2.7 } do
		for _, z in { -3.2, 3.2 } do
			box(kit, "Arch", Vector3.new(1.3, 0.3, 2.8), Vector3.new(x, G + 2.65, z), kit.trim)
		end
	end
	-- Windscreen frame + glass.
	bar(kit, Vector3.new(-2.35, G + 3.1, -2.8), Vector3.new(-2.35, G + 4.7, -2.6), 0.25, P.GatePost)
	bar(kit, Vector3.new(2.35, G + 3.1, -2.8), Vector3.new(2.35, G + 4.7, -2.6), 0.25, P.GatePost)
	bar(kit, Vector3.new(-2.35, G + 4.7, -2.6), Vector3.new(2.35, G + 4.7, -2.6), 0.25, P.GatePost)
	glass(kit, Vector3.new(4.5, 1.4, 0.12), Vector3.new(0, G + 3.9, -2.7))
	-- Roll bar behind the driver.
	bar(kit, Vector3.new(-2.3, G + 3.1, 2.2), Vector3.new(-2.3, G + 5.0, 2.2), 0.3, P.GatePost)
	bar(kit, Vector3.new(2.3, G + 3.1, 2.2), Vector3.new(2.3, G + 5.0, 2.2), 0.3, P.GatePost)
	bar(kit, Vector3.new(-2.3, G + 5.0, 2.2), Vector3.new(2.3, G + 5.0, 2.2), 0.3, P.GatePost)
	-- Steering wheel.
	add(
		kit,
		"Steering",
		Vector3.new(0.2, 1.1, 1.1),
		CFrame.new(-0.0, G + 3.5, -1.5) * CFrame.Angles(0, RAD90, 0) * CFrame.Angles(0, 0, math.rad(-30)),
		P.PillBlack,
		{ shape = Enum.PartType.Cylinder }
	)
	-- Spare tyre on the back.
	add(
		kit,
		"SpareTyre",
		Vector3.new(0.7, 2.0, 2.0),
		CFrame.new(0, G + 2.4, 4.95) * AXIS_Z,
		P.PillBlack,
		{ shape = Enum.PartType.Cylinder }
	)
	add(
		kit,
		"SpareHub",
		Vector3.new(0.8, 1.0, 1.0),
		CFrame.new(0, G + 2.4, 4.95) * AXIS_Z,
		P.ButtonGrey,
		{ shape = Enum.PartType.Cylinder }
	)
	light(kit, Vector3.new(-2.1, G + 2.4, 4.65), 0.45, P.ButtonRed)
	light(kit, Vector3.new(2.1, G + 2.4, 4.65), 0.45, P.ButtonRed)
end

local function buildTruck(kit: Kit)
	local canvas = P.TroopGreenDark
	-- Wheels: one front axle, two rear axles.
	for _, x in { -2.8, 2.8 } do
		wheel(kit, x, -3.0, 1.3, 1.1)
		wheel(kit, x, 4.2, 1.3, 1.1)
		wheel(kit, x, 6.9, 1.3, 1.1)
	end
	box(kit, "Chassis", Vector3.new(4.8, 0.7, 12.6), Vector3.new(0, G + 1.0, 2.0), P.GatePost)
	box(kit, "StepL", Vector3.new(0.5, 1.0, 3.4), Vector3.new(-2.6, G + 0.9, 0.1), kit.trim)
	box(kit, "StepR", Vector3.new(0.5, 1.0, 3.4), Vector3.new(2.6, G + 0.9, 0.1), kit.trim)
	-- Cab around the driver: lower body, pillars, roof, windscreen.
	box(kit, "CabLower", Vector3.new(5.4, 2.0, 4.0), Vector3.new(0, G + 2.3, 0), kit.body)
	for _, x in { -2.5, 2.5 } do
		for _, z in { -1.8, 1.8 } do
			box(kit, "Pillar", Vector3.new(0.4, 2.7, 0.4), Vector3.new(x, G + 4.65, z), kit.body)
		end
	end
	box(kit, "Roof", Vector3.new(5.6, 0.5, 4.4), Vector3.new(0, G + 6.25, 0), kit.body)
	box(kit, "RoofTrim", Vector3.new(5.7, 0.15, 4.5), Vector3.new(0, G + 6.0, 0), kit.trim)
	glass(kit, Vector3.new(4.6, 2.4, 0.12), Vector3.new(0, G + 4.6, -1.85))
	-- Bonnet + grille + headlights.
	box(kit, "Bonnet", Vector3.new(5.0, 2.0, 2.6), Vector3.new(0, G + 2.4, -3.3), kit.body)
	box(kit, "Grille", Vector3.new(3.8, 1.3, 0.2), Vector3.new(0, G + 2.3, -4.65), P.GatePost)
	light(kit, Vector3.new(-1.9, G + 2.6, -4.7), 0.7, P.CoinShine)
	light(kit, Vector3.new(1.9, G + 2.6, -4.7), 0.7, P.CoinShine)
	box(kit, "Bumper", Vector3.new(5.4, 0.5, 0.5), Vector3.new(0, G + 1.4, -4.8), P.GatePost)
	-- Canvas back: cargo bed + canvas cover with dark ribs.
	box(kit, "Bed", Vector3.new(5.6, 1.0, 7.0), Vector3.new(0, G + 1.85, 5.6), kit.trim)
	box(kit, "Canvas", Vector3.new(5.4, 3.6, 6.8), Vector3.new(0, G + 4.15, 5.6), canvas)
	for _, z in { 2.6, 4.6, 6.6, 8.6 } do
		box(kit, "Rib", Vector3.new(5.6, 0.25, 0.3), Vector3.new(0, G + 6.0, z), darken(canvas, 0.35))
		box(kit, "RibL", Vector3.new(0.12, 3.6, 0.3), Vector3.new(-2.75, G + 4.15, z), darken(canvas, 0.35))
		box(kit, "RibR", Vector3.new(0.12, 3.6, 0.3), Vector3.new(2.75, G + 4.15, z), darken(canvas, 0.35))
	end
	light(kit, Vector3.new(-2.2, G + 1.85, 9.15), 0.5, P.ButtonRed)
	light(kit, Vector3.new(2.2, G + 1.85, 9.15), 0.5, P.ButtonRed)
end

local function buildApc(kit: Kit)
	for _, x in { -3.2, 3.2 } do
		for _, z in { -3.6, 0.4, 4.4 } do
			wheel(kit, x, z, 1.25, 1.0)
		end
	end
	-- Armoured box.
	box(kit, "Hull", Vector3.new(6.0, 2.8, 10.4), Vector3.new(0, G + 2.1, 0.6), kit.body)
	box(kit, "Skirt", Vector3.new(5.2, 0.8, 10.0), Vector3.new(0, G + 0.8, 0.6), P.GatePost)
	-- Sloped front (WedgePart: tall side at the back, slope down towards -Z).
	add(kit, "Glacis", Vector3.new(6.0, 2.0, 2.6), CFrame.new(0, G + 2.5, -5.9), kit.body, { wedge = true })
	box(kit, "Nose", Vector3.new(6.0, 0.8, 2.6), Vector3.new(0, G + 1.1, -5.9), kit.trim)
	-- Side armour bands and fenders.
	box(kit, "BandL", Vector3.new(0.2, 0.5, 10.2), Vector3.new(-3.05, G + 2.7, 0.6), kit.trim)
	box(kit, "BandR", Vector3.new(0.2, 0.5, 10.2), Vector3.new(3.05, G + 2.7, 0.6), kit.trim)
	-- Hatch rim around the driver (torso pokes out of the roof).
	local top = G + 3.5
	box(kit, "HatchF", Vector3.new(2.8, 0.5, 0.4), Vector3.new(0, top + 0.25, -1.2), P.GatePost)
	box(kit, "HatchB", Vector3.new(2.8, 0.5, 0.4), Vector3.new(0, top + 0.25, 1.2), P.GatePost)
	box(kit, "HatchL", Vector3.new(0.4, 0.5, 2.8), Vector3.new(-1.2, top + 0.25, 0), P.GatePost)
	box(kit, "HatchR", Vector3.new(0.4, 0.5, 2.8), Vector3.new(1.2, top + 0.25, 0), P.GatePost)
	box(kit, "HatchLid", Vector3.new(2.4, 0.25, 2.2), Vector3.new(0, top + 1.2, 2.2), kit.trim)
	-- Roof machine gun.
	box(kit, "GunMount", Vector3.new(0.8, 0.8, 0.8), Vector3.new(1.8, top + 0.4, -2.0), P.GatePost)
	add(
		kit,
		"GunBarrel",
		Vector3.new(2.8, 0.3, 0.3),
		CFrame.new(1.8, top + 0.6, -3.4) * AXIS_Z,
		P.GatePost,
		{ shape = Enum.PartType.Cylinder }
	)
	light(kit, Vector3.new(-2.2, G + 2.0, -7.25), 0.6, P.CoinShine)
	light(kit, Vector3.new(2.2, G + 2.0, -7.25), 0.6, P.CoinShine)
	light(kit, Vector3.new(-2.4, G + 2.4, 5.85), 0.45, P.ButtonRed)
	light(kit, Vector3.new(2.4, G + 2.4, 5.85), 0.45, P.ButtonRed)
end

local function buildTank(kit: Kit)
	-- Tracks with road wheels on both sides.
	for _, x in { -3.3, 3.3 } do
		box(kit, "Track", Vector3.new(1.8, 2.0, 11.4), Vector3.new(x, G + 1.0, 0.4), P.PillBlack)
		box(kit, "TrackGuard", Vector3.new(2.0, 0.3, 11.8), Vector3.new(x, G + 2.15, 0.4), kit.trim)
		local outer = x + math.sign(x) * 0.95
		for _, z in { -4.0, -2.0, 0.0, 2.0, 4.0 } do
			add(
				kit,
				"RoadWheel",
				Vector3.new(0.2, 1.4, 1.4),
				CFrame.new(outer, G + 0.85, z + 0.4),
				P.ButtonGreyDark,
				{ shape = Enum.PartType.Cylinder }
			)
		end
	end
	-- Hull (lower part hides the legs, upper part is the deck).
	box(kit, "HullLower", Vector3.new(4.8, 1.5, 10.6), Vector3.new(0, G + 1.15, 0.4), P.GatePost)
	box(kit, "Hull", Vector3.new(5.4, 1.5, 10.4), Vector3.new(0, G + 2.65, 0.6), kit.body)
	add(kit, "Glacis", Vector3.new(5.4, 1.5, 2.0), CFrame.new(0, G + 2.65, -5.6), kit.body, { wedge = true })
	-- Turret around the driver: the head pokes out of the top like a commander.
	box(kit, "Turret", Vector3.new(4.2, 1.4, 4.6), Vector3.new(0, G + 4.1, 0.4), kit.body)
	box(kit, "TurretTrim", Vector3.new(4.3, 0.25, 4.7), Vector3.new(0, G + 3.45, 0.4), kit.trim)
	box(kit, "Mantlet", Vector3.new(1.6, 1.0, 0.8), Vector3.new(0, G + 4.1, -2.2), kit.trim)
	-- Long barrel pointing forward.
	add(
		kit,
		"Barrel",
		Vector3.new(6.4, 0.6, 0.6),
		CFrame.new(0, G + 4.15, -5.6) * AXIS_Z,
		kit.trim,
		{ shape = Enum.PartType.Cylinder }
	)
	add(
		kit,
		"Muzzle",
		Vector3.new(0.9, 0.85, 0.85),
		CFrame.new(0, G + 4.15, -8.8) * AXIS_Z,
		P.GatePost,
		{ shape = Enum.PartType.Cylinder }
	)
	-- Storage box on the back of the turret.
	box(kit, "Stowage", Vector3.new(3.6, 0.9, 0.9), Vector3.new(0, G + 4.0, 3.15), kit.trim)
	light(kit, Vector3.new(-2.0, G + 2.9, -6.65), 0.5, P.CoinShine)
	light(kit, Vector3.new(2.0, G + 2.9, -6.65), 0.5, P.CoinShine)
end

local function buildHover(kit: Kit)
	local glow = P.Sky
	-- Main hull floating above the floor.
	box(kit, "Skirt", Vector3.new(5.4, 1.0, 8.6), Vector3.new(0, G + 1.1, 0.4), P.GatePost)
	box(kit, "Hull", Vector3.new(6.0, 1.6, 9.0), Vector3.new(0, G + 2.4, 0.4), kit.body)
	add(kit, "Nose", Vector3.new(6.0, 1.6, 2.6), CFrame.new(0, G + 2.4, -5.4), kit.body, { wedge = true })
	box(kit, "Stripe", Vector3.new(6.1, 0.3, 9.1), Vector3.new(0, G + 2.0, 0.4), kit.trim)
	-- Low cockpit wall around the driver.
	box(kit, "CockpitL", Vector3.new(0.4, 0.9, 3.0), Vector3.new(-1.4, G + 3.65, 0), kit.trim)
	box(kit, "CockpitR", Vector3.new(0.4, 0.9, 3.0), Vector3.new(1.4, G + 3.65, 0), kit.trim)
	glass(kit, Vector3.new(3.2, 1.2, 0.15), Vector3.new(0, G + 3.8, -1.6))
	-- Side fins.
	add(
		kit,
		"FinL",
		Vector3.new(0.3, 1.6, 2.6),
		CFrame.new(-3.1, G + 3.6, 3.4) * CFrame.Angles(0, math.pi, 0),
		kit.trim,
		{ wedge = true }
	)
	add(
		kit,
		"FinR",
		Vector3.new(0.3, 1.6, 2.6),
		CFrame.new(3.1, G + 3.6, 3.4) * CFrame.Angles(0, math.pi, 0),
		kit.trim,
		{ wedge = true }
	)
	-- Glowing thrusters underneath (round axis pointing down) and at the back.
	for _, x in { -2.0, 2.0 } do
		for _, z in { -2.6, 3.4 } do
			add(
				kit,
				"Thruster",
				Vector3.new(0.35, 1.6, 1.6),
				CFrame.new(x, G + 0.45, z) * AXIS_Y,
				glow,
				{ shape = Enum.PartType.Cylinder, material = Enum.Material.Neon }
			)
		end
	end
	for _, x in { -1.6, 1.6 } do
		add(
			kit,
			"Engine",
			Vector3.new(0.6, 1.3, 1.3),
			CFrame.new(x, G + 2.5, 4.95) * AXIS_Z,
			P.GatePost,
			{ shape = Enum.PartType.Cylinder }
		)
		add(
			kit,
			"EngineGlow",
			Vector3.new(0.2, 1.0, 1.0),
			CFrame.new(x, G + 2.5, 5.3) * AXIS_Z,
			glow,
			{ shape = Enum.PartType.Cylinder, material = Enum.Material.Neon }
		)
	end
	-- A soft glow light under the hull.
	local pointLight = Instance.new("PointLight")
	pointLight.Color = glow
	pointLight.Range = 10
	pointLight.Brightness = 1.5
	pointLight.Parent = kit.root
end

local BUILDERS: { [string]: (Kit) -> () } = {
	bike = buildBike,
	jeep = buildJeep,
	truck = buildTruck,
	apc = buildApc,
	tank = buildTank,
	hover = buildHover,
}

-- Build a vehicle model for a Config.Vehicles entry (see header).
function VehicleModels.Build(def: Config.VehicleDef): Model
	local model = Instance.new("Model")
	model.Name = "Vehicle_" .. tostring(def.id)

	local root = Instance.new("Part")
	root.Name = "Root"
	root.Size = Vector3.new(2, 2, 1)
	root.CFrame = CFrame.new()
	root.Transparency = 1
	root.Anchored = false
	root.CanCollide = false
	root.CanQuery = false
	root.CanTouch = false
	root.Massless = true
	root.CastShadow = false
	root.Parent = model
	model.PrimaryPart = root

	local bodyColor = if typeof(def.color) == "Color3" then def.color else P.TroopGreen
	local kit: Kit = {
		model = model,
		root = root,
		body = bodyColor,
		trim = darken(bodyColor, 0.4),
	}
	local builder = BUILDERS[def.style] or buildJeep
	builder(kit)
	return model
end

return VehicleModels
