--!strict
--[[
	RunnerGuns (client)
	The guns of the army runner, built from plain Parts (no meshes, no asset
	ids): one chunky toy model per weapon tier of RunnerDefs.WEAPONS, plus the
	pooled bullets / hit flashes / rocket smoke that every unit fires.

	Gun coordinates: the origin is the GRIP (where the hand holds it) and the
	barrel points along -Z, i.e. along the LookVector of whatever it is welded
	to. The server's "RunnerGun" Tool Handle sits in the avatar's right hand
	with its LookVector pointing forward, so a gun welded to the Handle with
	an identity offset points where the avatar aims.

	Public API:
		RunnerGuns.Weapon(tier) -> RunnerDefs.WeaponDef   (tier clamped to 1..MAX_WEAPON_TIER)
		RunnerGuns.Pieces(tier, scale?) -> { Piece }       part list relative to the grip
			Piece = { name, size, offset: CFrame, color, material?, shape? }
		RunnerGuns.MuzzleOffset(tier, scale?) -> CFrame    muzzle relative to the grip
		RunnerGuns.Build(tier, scale?) -> Model
			unanchored, Massless parts (CanCollide / CanQuery / CanTouch off) with
			a PrimaryPart "GunRoot" at the grip. NOT joined yet: move it with
			model:PivotTo, parent it, then call RunnerGuns.WeldTo.
		RunnerGuns.WeldTo(model, part)
			WeldConstraint from `part` (Part0) to every part of the model. Call it
			only AFTER the model is in place and parented: a WeldConstraint keeps
			the offset the parts have when it switches on.
		RunnerGuns.AttachToHandle(handle: BasePart, tier) -> Model
			builds the gun, PivotTo(handle.CFrame * HANDLE_OFFSET), parents it to
			the Handle and only then welds every part to the Handle (the same
			order VehicleService uses for vehicles).
		RunnerGuns.HANDLE_OFFSET: CFrame                   grip relative to the Tool Handle
		RunnerGuns.HandleMuzzle(handle, tier) -> Vector3   world muzzle of the gun on that Handle
		RunnerGuns.POOL_SIZE: number                       default bullet pool size

		RunnerGuns.newPool(parent, maxBullets?) -> Pool    visual bullets (BulkMoveTo, no tweens)
			pool:Fire(from, to, tier, impact) -> boolean   false when every bullet is busy
				flies from `from` to `to` at the tier's bulletSpeed with its
				bulletStyle; `impact` = true plays a hit flash (rocket: explosion)
			pool:Flash(position, size, color, life)        one-off flash (boss hits, ...)
			pool:Update(dt)                                call every frame
			pool:Active() -> number                        bullets in flight
			pool:Destroy()

	Bullet styles (bulletStyle of RunnerDefs.WEAPONS). The track floor is
	white, so bullets are solid (not Neon: glowing yellows turned white) in
	strong, dark-enough colours, one per gun:
		pistol  small orange slug        rifle   long red tracer
		smg     small fast pink slug     assault blue tracer
		minigun thin purple stream       rocket  big dark-red rocket + smoke trail + explosion flash
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local RunnerDefs = require(Shared.Runner.RunnerDefs)

local P = Config.Palette

type WeaponDef = RunnerDefs.WeaponDef

export type Piece = {
	name: string,
	size: Vector3,
	offset: CFrame,
	color: Color3,
	material: Enum.Material?,
	shape: Enum.PartType?,
}

export type Pool = {
	Fire: (self: Pool, from: Vector3, to: Vector3, tier: number, impact: boolean) -> boolean,
	Flash: (self: Pool, position: Vector3, size: number, color: Color3, life: number) -> (),
	Update: (self: Pool, dt: number) -> (),
	Active: (self: Pool) -> number,
	Destroy: (self: Pool) -> (),
}

local RunnerGuns = {}

local HIDDEN = CFrame.new(0, -500, 0)
local DEFAULT_POOL = 90 -- [PROPOSAL] max visual bullets alive at once (performance cap)
local FLASH_POOL = 40 -- impact + muzzle flashes (oldest is reused when all are busy)
local SMOKE_POOL = 36

-- A Cylinder part's axis is its X axis; this turns it to lie along Z (the barrel).
local ALONG_Z = CFrame.Angles(0, math.rad(90), 0)

RunnerGuns.POOL_SIZE = DEFAULT_POOL
-- Where the gun's grip sits relative to the server's Tool Handle (identity:
-- the Handle IS the grip; its LookVector is the barrel direction).
RunnerGuns.HANDLE_OFFSET = CFrame.new()

function RunnerGuns.Weapon(tier: number): WeaponDef
	local index = math.clamp(math.floor(tier), 1, RunnerDefs.MAX_WEAPON_TIER)
	return RunnerDefs.WEAPONS[index]
end

local function styleOf(tier: number): string
	return RunnerGuns.Weapon(tier).bulletStyle
end

-------------------------------------------------------------------------------
-- Gun models (one per bullet style, so a new tier only needs a style)
-------------------------------------------------------------------------------
local function piece(name: string, size: Vector3, offset: CFrame, color: Color3, shape: Enum.PartType?): Piece
	return { name = name, size = size, offset = offset, color = color, material = nil, shape = shape }
end

local function cylinderZ(name: string, length: number, diameter: number, at: Vector3, color: Color3): Piece
	return piece(name, Vector3.new(length, diameter, diameter), CFrame.new(at) * ALONG_Z, color, Enum.PartType.Cylinder)
end

type GunDef = { pieces: { Piece }, muzzle: Vector3 }

-- [PROPOSAL] Shapes and colours are invented (toy look, Config.Palette only).
local GUNS: { [string]: GunDef } = {
	pistol = {
		pieces = {
			piece(
				"Grip",
				Vector3.new(0.26, 0.6, 0.32),
				CFrame.new(0, -0.18, 0.06) * CFrame.Angles(math.rad(-12), 0, 0),
				P.GateIron
			),
			piece("Slide", Vector3.new(0.3, 0.32, 1.05), CFrame.new(0, 0.16, -0.28), P.GatePost),
			piece("Accent", Vector3.new(0.32, 0.08, 0.6), CFrame.new(0, 0.34, -0.3), P.Coin),
			cylinderZ("Barrel", 0.25, 0.16, Vector3.new(0, 0.16, -0.86), P.ButtonGrey),
		},
		muzzle = Vector3.new(0, 0.16, -1.0),
	},
	rifle = {
		pieces = {
			piece("Grip", Vector3.new(0.24, 0.5, 0.3), CFrame.new(0, -0.18, 0.05), P.GateIron),
			piece("Body", Vector3.new(0.3, 0.34, 2.2), CFrame.new(0, 0.12, -0.55), P.Wood),
			piece("Stock", Vector3.new(0.28, 0.46, 0.85), CFrame.new(0, 0.02, 0.9), P.Wood),
			cylinderZ("Barrel", 1.3, 0.16, Vector3.new(0, 0.2, -2.25), P.GatePost),
			cylinderZ("Scope", 0.75, 0.22, Vector3.new(0, 0.44, -0.55), P.GateIron),
		},
		muzzle = Vector3.new(0, 0.2, -2.95),
	},
	smg = {
		pieces = {
			piece("Grip", Vector3.new(0.26, 0.5, 0.3), CFrame.new(0, -0.18, 0.05), P.GateIron),
			piece("Body", Vector3.new(0.32, 0.42, 1.2), CFrame.new(0, 0.13, -0.35), P.GatePost),
			piece("Mag", Vector3.new(0.2, 0.7, 0.26), CFrame.new(0, -0.38, -0.45), P.CardOrange),
			piece("Accent", Vector3.new(0.34, 0.1, 0.8), CFrame.new(0, 0.36, -0.35), P.CardOrange),
			cylinderZ("Barrel", 0.5, 0.15, Vector3.new(0, 0.15, -1.2), P.ButtonGrey),
		},
		muzzle = Vector3.new(0, 0.15, -1.48),
	},
	assault = {
		pieces = {
			piece("Grip", Vector3.new(0.26, 0.52, 0.3), CFrame.new(0, -0.2, 0.05), P.GateIron),
			piece("Body", Vector3.new(0.32, 0.42, 2.0), CFrame.new(0, 0.13, -0.55), P.GatePost),
			piece(
				"Mag",
				Vector3.new(0.22, 0.72, 0.3),
				CFrame.new(0, -0.4, -0.65) * CFrame.Angles(math.rad(14), 0, 0),
				P.CardOrange
			),
			piece("Stock", Vector3.new(0.28, 0.42, 0.75), CFrame.new(0, 0.04, 0.75), P.GateIron),
			piece("Sight", Vector3.new(0.12, 0.2, 0.5), CFrame.new(0, 0.44, -0.4), P.CardOrange),
			cylinderZ("Barrel", 0.9, 0.15, Vector3.new(0, 0.17, -2.0), P.ButtonGrey),
		},
		muzzle = Vector3.new(0, 0.17, -2.5),
	},
	minigun = {
		pieces = {
			piece("Grip", Vector3.new(0.26, 0.5, 0.3), CFrame.new(0, -0.2, 0.05), P.GateIron),
			piece("Motor", Vector3.new(0.8, 0.8, 1.0), CFrame.new(0, 0.2, -0.2), P.ButtonGreyDark),
			piece("Ammo", Vector3.new(0.55, 0.6, 0.6), CFrame.new(0.65, 0.05, -0.1), P.Coin),
			piece("Handle", Vector3.new(0.16, 0.16, 0.8), CFrame.new(0, 0.72, -0.2), P.GatePost),
			cylinderZ("Ring", 0.2, 0.9, Vector3.new(0, 0.2, -1.0), P.GatePost),
			cylinderZ("BarrelA", 2.1, 0.17, Vector3.new(0, 0.42, -2.05), P.ButtonGrey),
			cylinderZ("BarrelB", 2.1, 0.17, Vector3.new(0.21, 0.08, -2.05), P.ButtonGrey),
			cylinderZ("BarrelC", 2.1, 0.17, Vector3.new(-0.21, 0.08, -2.05), P.ButtonGrey),
			cylinderZ("Tip", 0.2, 0.75, Vector3.new(0, 0.2, -3.0), P.GatePost),
		},
		muzzle = Vector3.new(0, 0.2, -3.15),
	},
	rocket = {
		pieces = {
			piece("Grip", Vector3.new(0.26, 0.55, 0.3), CFrame.new(0, -0.2, 0.05), P.GateIron),
			cylinderZ("Tube", 3.2, 0.62, Vector3.new(0, 0.38, -0.45), P.TroopGreenDark),
			cylinderZ("BackRing", 0.3, 0.74, Vector3.new(0, 0.38, 1.05), P.GatePost),
			cylinderZ("FrontRing", 0.3, 0.74, Vector3.new(0, 0.38, -1.95), P.ButtonRed),
			piece("Warhead", Vector3.new(0.5, 0.5, 0.5), CFrame.new(0, 0.38, -2.2), P.ButtonRed, Enum.PartType.Ball),
			piece("Sight", Vector3.new(0.14, 0.3, 0.3), CFrame.new(-0.36, 0.72, -0.6), P.Coin),
		},
		muzzle = Vector3.new(0, 0.38, -2.4),
	},
}

local function gunFor(tier: number): GunDef
	return GUNS[styleOf(tier)] or GUNS.pistol
end

local function scaled(offset: CFrame, scale: number): CFrame
	return offset - offset.Position + offset.Position * scale
end

function RunnerGuns.Pieces(tier: number, scale: number?): { Piece }
	local s = scale or 1
	local list: { Piece } = {}
	for _, p in gunFor(tier).pieces do
		table.insert(list, {
			name = p.name,
			size = p.size * s,
			offset = scaled(p.offset, s),
			color = p.color,
			material = p.material,
			shape = p.shape,
		})
	end
	return list
end

function RunnerGuns.MuzzleOffset(tier: number, scale: number?): CFrame
	return CFrame.new(gunFor(tier).muzzle * (scale or 1))
end

local function makeGunPart(p: Piece, cframe: CFrame, parent: Instance): Part
	local part = Instance.new("Part")
	part.Name = p.name
	part.Anchored = false
	part.Massless = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.CastShadow = false
	part.Material = p.material or Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	if p.shape then
		part.Shape = p.shape
	end
	part.Size = p.size
	part.CFrame = cframe
	part.Color = p.color
	part.Parent = parent
	return part
end

function RunnerGuns.Build(tier: number, scale: number?): Model
	local model = Instance.new("Model")
	model.Name = "RunnerGunModel"
	model:SetAttribute("Tier", tier)
	local origin = CFrame.new(0, 2000, 0) -- built away from everything; PivotTo moves every part
	local root = makeGunPart(piece("GunRoot", Vector3.new(0.2, 0.2, 0.2), CFrame.new(), P.GatePost), origin, model)
	root.Transparency = 1
	for _, p in RunnerGuns.Pieces(tier, scale) do
		makeGunPart(p, origin * p.offset, model)
	end
	model.PrimaryPart = root
	return model
end

function RunnerGuns.WeldTo(model: Model, part: BasePart)
	for _, item in model:GetDescendants() do
		if item:IsA("BasePart") and item ~= part then
			local weld = Instance.new("WeldConstraint")
			weld.Name = "GunWeld"
			weld.Part0 = part
			weld.Part1 = item
			weld.Parent = item
		end
	end
end

function RunnerGuns.AttachToHandle(handle: BasePart, tier: number): Model
	local model = RunnerGuns.Build(tier)
	-- 1) move EVERY part (PivotTo works outside Workspace; setting only the
	--    root's CFrame would leave the pieces 2000 studs up), 2) parent,
	-- 3) weld: each WeldConstraint keeps the offset the parts have right now.
	model:PivotTo(handle.CFrame * RunnerGuns.HANDLE_OFFSET)
	model.Parent = handle
	RunnerGuns.WeldTo(model, handle)
	return model
end

function RunnerGuns.HandleMuzzle(handle: BasePart, tier: number): Vector3
	return (handle.CFrame * RunnerGuns.HANDLE_OFFSET * RunnerGuns.MuzzleOffset(tier)).Position
end

-------------------------------------------------------------------------------
-- Bullet styles
-------------------------------------------------------------------------------
type Style = {
	size: Vector3, -- the bullet part (long side along its flight)
	color: Color3,
	shape: Enum.PartType,
	hitSize: number, -- impact flash diameter
	hitColor: Color3,
	smoke: boolean, -- rocket smoke trail
}

-- [PROPOSAL] Bullet colours: saturated and dark enough to stand out on the
-- white track floor (owner feedback: the old yellow / pale Neon bullets were
-- invisible on it). Hit flashes use the same colour.
local BULLET_ORANGE = Color3.fromRGB(235, 90, 0)
local BULLET_RED = Color3.fromRGB(215, 20, 35)
local BULLET_PINK = Color3.fromRGB(220, 30, 150)
local BULLET_BLUE = Color3.fromRGB(25, 85, 235)
local BULLET_PURPLE = Color3.fromRGB(125, 35, 220)
local ROCKET_RED = Color3.fromRGB(170, 20, 20)

local STYLES: { [string]: Style } = {
	pistol = {
		size = Vector3.new(0.38, 0.38, 0.8),
		color = BULLET_ORANGE,
		shape = Enum.PartType.Block,
		hitSize = 1.1,
		hitColor = BULLET_ORANGE,
		smoke = false,
	},
	rifle = {
		size = Vector3.new(0.26, 0.26, 2.8),
		color = BULLET_RED,
		shape = Enum.PartType.Block,
		hitSize = 1.3,
		hitColor = BULLET_RED,
		smoke = false,
	},
	smg = {
		size = Vector3.new(0.3, 0.3, 0.85),
		color = BULLET_PINK,
		shape = Enum.PartType.Block,
		hitSize = 0.9,
		hitColor = BULLET_PINK,
		smoke = false,
	},
	assault = {
		size = Vector3.new(0.28, 0.28, 1.9),
		color = BULLET_BLUE,
		shape = Enum.PartType.Block,
		hitSize = 1.2,
		hitColor = BULLET_BLUE,
		smoke = false,
	},
	minigun = {
		size = Vector3.new(0.22, 0.22, 3.6),
		color = BULLET_PURPLE,
		shape = Enum.PartType.Block,
		hitSize = 0.9,
		hitColor = BULLET_PURPLE,
		smoke = false,
	},
	rocket = {
		size = Vector3.new(0.6, 0.6, 1.7),
		color = ROCKET_RED,
		shape = Enum.PartType.Block,
		hitSize = 7,
		hitColor = P.CardOrange,
		smoke = true,
	},
}

local function styleFor(tier: number): Style
	return STYLES[styleOf(tier)] or STYLES.pistol
end

-------------------------------------------------------------------------------
-- Pool
-------------------------------------------------------------------------------
type Bullet = {
	part: Part,
	from: Vector3,
	to: Vector3,
	t: number,
	duration: number,
	active: boolean,
	impact: boolean,
	style: Style,
	smokeClock: number,
}

type Effect = {
	part: Part,
	position: Vector3,
	t: number,
	life: number,
	size0: number,
	size1: number,
	transparency0: number,
	rise: number,
	active: boolean,
}

local function effectPart(name: string, parent: Instance, material: Enum.Material): Part
	local part = Instance.new("Part")
	part.Name = name
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.CastShadow = false
	part.Material = material
	part.Shape = Enum.PartType.Ball
	part.Size = Vector3.one
	part.CFrame = HIDDEN
	part.Parent = parent
	return part
end

function RunnerGuns.newPool(parent: Instance, maxBullets: number?): Pool
	local folder = Instance.new("Folder")
	folder.Name = "Bullets"
	folder.Parent = parent
	local destroyed = false

	local bullets: { Bullet } = {}
	for i = 1, maxBullets or DEFAULT_POOL do
		local part = Instance.new("Part")
		part.Name = "Bullet"
		part.Anchored = true
		part.CanCollide = false
		part.CanQuery = false
		part.CanTouch = false
		part.CastShadow = false
		part.Material = Enum.Material.SmoothPlastic -- solid colour: Neon glows towards white on the white floor
		-- Start in the pistol style for real (Fire only restyles when the style
		-- changes, so without this the first pistol bullets stayed default grey).
		part.Size = STYLES.pistol.size
		part.Color = STYLES.pistol.color
		part.Shape = STYLES.pistol.shape
		part.CFrame = HIDDEN
		part.Parent = folder
		bullets[i] = {
			part = part,
			from = Vector3.zero,
			to = Vector3.zero,
			t = 0,
			duration = 0,
			active = false,
			impact = false,
			style = STYLES.pistol,
			smokeClock = 0,
		}
	end
	local free: { number } = {}
	for i = #bullets, 1, -1 do
		table.insert(free, i)
	end
	local activeCount = 0

	local function makeEffects(count: number, name: string, material: Enum.Material): { Effect }
		local list: { Effect } = {}
		for i = 1, count do
			list[i] = {
				part = effectPart(name, folder, material),
				position = Vector3.zero,
				t = 0,
				life = 0,
				size0 = 1,
				size1 = 1,
				transparency0 = 0,
				rise = 0,
				active = false,
			}
		end
		return list
	end
	local flashes = makeEffects(FLASH_POOL, "Flash", Enum.Material.Neon)
	local smokes = makeEffects(SMOKE_POOL, "Smoke", Enum.Material.SmoothPlastic)
	local nextFlash, nextSmoke = 1, 1

	local function startEffect(
		e: Effect,
		position: Vector3,
		size0: number,
		size1: number,
		color: Color3,
		life: number,
		transparency0: number,
		rise: number
	)
		e.position = position
		e.t = 0
		e.life = math.max(0.05, life)
		e.size0 = size0
		e.size1 = size1
		e.transparency0 = transparency0
		e.rise = rise
		e.active = true
		e.part.Color = color
		e.part.Transparency = transparency0
		e.part.Size = Vector3.one * size0
	end

	local self = {} :: Pool

	function self.Flash(_self: Pool, position: Vector3, size: number, color: Color3, life: number)
		if destroyed then
			return
		end
		local e = flashes[nextFlash]
		nextFlash = nextFlash % #flashes + 1
		startEffect(e, position, size * 0.35, size, color, life, 0.05, 0)
	end

	local function smoke(position: Vector3)
		local e = smokes[nextSmoke]
		nextSmoke = nextSmoke % #smokes + 1
		startEffect(e, position, 0.6, 1.8, P.ButtonGrey, 0.55, 0.25, 1.2)
	end

	function self.Fire(_self: Pool, from: Vector3, to: Vector3, tier: number, impact: boolean): boolean
		if destroyed then
			return false
		end
		local index = table.remove(free)
		if not index then
			return false -- every bullet is busy: skip this shot (visual only)
		end
		local bullet = bullets[index]
		local style = styleFor(tier)
		local speed = math.max(10, RunnerGuns.Weapon(tier).bulletSpeed)
		bullet.from = from
		bullet.to = to
		bullet.t = 0
		bullet.duration = math.max(0.04, (to - from).Magnitude / speed)
		bullet.active = true
		bullet.impact = impact
		bullet.smokeClock = 0
		if bullet.style ~= style then
			bullet.style = style
			bullet.part.Size = style.size
			bullet.part.Color = style.color
			bullet.part.Shape = style.shape
		end
		activeCount += 1
		return true
	end

	function self.Active(_self: Pool): number
		return activeCount
	end

	local parts: { BasePart } = {}
	local cframes: { CFrame } = {}

	local function updateEffect(e: Effect, dt: number)
		e.t += dt
		local a = e.t / e.life
		table.insert(parts, e.part)
		if a >= 1 then
			e.active = false
			table.insert(cframes, HIDDEN)
			return
		end
		local size = e.size0 + (e.size1 - e.size0) * a
		e.part.Size = Vector3.one * size
		e.part.Transparency = e.transparency0 + (1 - e.transparency0) * a
		table.insert(cframes, CFrame.new(e.position + Vector3.new(0, e.rise * a, 0)))
	end

	function self.Update(_self: Pool, dt: number)
		if destroyed then
			return
		end
		table.clear(parts)
		table.clear(cframes)
		for index, bullet in bullets do
			if not bullet.active then
				continue
			end
			bullet.t += dt
			local a = bullet.t / bullet.duration
			table.insert(parts, bullet.part)
			if a >= 1 then
				bullet.active = false
				activeCount -= 1
				table.insert(free, index)
				table.insert(cframes, HIDDEN)
				if bullet.impact then
					local style = bullet.style
					self:Flash(bullet.to, style.hitSize, style.hitColor, if style.smoke then 0.45 else 0.14)
					if style.smoke then
						self:Flash(bullet.to + Vector3.new(0, 1, 0), style.hitSize * 0.55, P.Coin, 0.3)
						smoke(bullet.to + Vector3.new(0, 1.5, 0))
					end
				end
			else
				local pos = bullet.from:Lerp(bullet.to, a)
				local look = if (bullet.to - bullet.from).Magnitude > 0.01
					then bullet.to
					else pos + Vector3.new(0, 0, -1)
				table.insert(cframes, CFrame.lookAt(pos, look))
				if bullet.style.smoke then
					bullet.smokeClock += dt
					if bullet.smokeClock >= 0.05 then
						bullet.smokeClock = 0
						smoke(pos)
					end
				end
			end
		end
		for _, e in flashes do
			if e.active then
				updateEffect(e, dt)
			end
		end
		for _, e in smokes do
			if e.active then
				updateEffect(e, dt)
			end
		end
		if #parts > 0 then
			Workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
		end
	end

	function self.Destroy(_self: Pool)
		if destroyed then
			return
		end
		destroyed = true
		folder:Destroy()
	end

	return self
end

return RunnerGuns
