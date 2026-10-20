--!strict
--[[
	CombatService (server)
	The server half of the Archero-2-inspired COMBAT mode (docs/ARCHITECTURE.md §6.3).

	What it does:
	  1. At Start it builds the static arenas in workspace.Arenas: one per player
	     slot (Layout.ArenaCenter(slot)), a blue tiled floor like the base, green
	     Lego-stud hedge walls, crate / Lego-brick cover blocks (CombatDefs.COVER)
	     and tall invisible walls so nobody can leave.
	  2. It runs one fight "session" per player. Enemies, bullets, grenades and
	     cash pickups are plain DATA tables simulated here at 30 Hz (no server
	     parts). The client draws them from the "Combat" events and the 20 Hz
	     "CombatSnap" snapshot (see CombatController / CombatVisuals).

	Net actions (client -> server):
		CombatPrepare(mode, param)          mode "arena" (param = level) | "barrier" (param = area)
			-> { sessionId, mode, level, area, waveCount, reward, centerX, centerZ, ... }
		CombatStart(sessionId)              -> { stats, hp, maxHp, startsAt }
		CombatChooseAbility(sessionId, id)  -> { id, stacks }
		CombatLeave()                       -> { left = true } or { ended = true } (mid-fight = defeat)

	Push channel "Combat" (reliable). Server -> client messages:
		"Wave"            { wave, waveCount, startsAt, boss }      banner + countdown
		"AbilityChoice"   { wave, timeout, options = { { id, name, icon, description, stacks, maxStacks } } }
		"AbilityPicked"   { id, stacks, auto }
		"ProjectileBatch" { list = { { id, ownerKind, x, z, vx, vz, kind, born, life, radius } } }
		"ProjectileGone"  { ids = { id } }
		"Telegraph"       { id, kind = "grenade" | "laser", x, z, radius?, dx?, dz?, fromX?, fromZ?, start, duration }
		"EnemyDied"       { list = { { id, type, x, z, boss } } }
		"Damage"          { list = { { x, z, amount, kind, crit } } }  kind "enemy"|"player"|"dodge"|"shield"|"heal"|"boom"
		"Collected"       { list = { { id, x, z, value } } }
		"Result"          { victory, mode, level, kills, fightSeconds, collected, paid, chest = { tier, uid, item }?, ... }
		                  chest: gold/silver on a win; wood on a defeat or retreat if you killed
		                  at least 1 enemy or fought >= CombatDefs.CHEST_MIN_SECONDS (else none)
		"Ended"           { sessionId, reason }                    session is gone (back to base)
	Client -> server on "Combat":
		"Hit", { id = projectileId }   an enemy bullet touched my character (validated)
	Push channel "CombatSnap" (unreliable, 20 Hz): a buffer, see CombatDefs.EncodeSnap
		{ t, cash, enemies = { id, type, x, z, hp, maxHp, state }, pickups = { id, x, z, value } }

	Player attributes: CombatActive (true from CombatStart until the fight ends),
	CombatHP, CombatMaxHP, CombatShield.

	Public API (other services): CombatService:IsInFight(player): boolean

	All positions in messages are ARENA-LOCAL (x, z relative to the arena centre).
	Server time = workspace:GetServerTimeNow() on both sides.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local StarterPlayer = game:GetService("StarterPlayer")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local CombatDefs = require(Shared.Combat.CombatDefs)
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local EquipmentDefs = require(Shared.Combat.EquipmentDefs)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Net = require(Shared.Net)
local Rng = require(Shared.Rng)

type CombatStats = EquipmentDefs.CombatStats
type RunStats = CombatDefs.RunStats
type LevelPlan = CombatDefs.LevelPlan
type EnemyDef = CombatDefs.EnemyDef

local P = Config.Palette

local CombatService = {}
CombatService.Priority = 10

-------------------------------------------------------------------------------
-- Tunables (rules shared with the client live in CombatDefs)
-------------------------------------------------------------------------------
local STEP = 1 / CombatDefs.TICK_RATE
local SNAP_INTERVAL = 1 / CombatDefs.SNAP_RATE
local HALF_X = CombatDefs.HALF_X
local HALF_Z = CombatDefs.HALF_Z
local ENEMY_BULLET_RADIUS = 0.5
local PLAYER_BULLET_RADIUS = 0.45
local SPAWN_MIN_PLAYER_DISTANCE = 18 -- [PROPOSAL] enemies never appear right next to you
local MULTISHOT_FAN_DEG = 10 -- [PROPOSAL] degrees between extra bullets when the weapon has no spread
local MAX_HITS_PER_SECOND = 20 -- client "Hit" reports processed per second (anti-spam)
local REPORT_LOOKBACK = 0.5 -- seconds of a bullet's path checked against a reported hit
local NO_ROOT_TIMEOUT = 5 -- end the session if the character has no root this long
local ARENA_WALL_HEIGHT = 5
local BOUNDARY_HEIGHT = 100 -- above the top-down camera (~60 studs up)
local TILE = 10

-------------------------------------------------------------------------------
-- Types
-------------------------------------------------------------------------------
type Enemy = {
	id: number,
	type: string,
	def: EnemyDef,
	boss: boolean,
	x: number,
	z: number,
	hp: number,
	maxHp: number,
	damage: number,
	cash: number,
	speed: number,
	interval: number,
	windup: number,
	bulletSpeed: number,
	state: string,
	timer: number,
	cooldown: number,
	aimX: number,
	aimZ: number,
	strafe: number,
	strafeTimer: number,
	attackCount: number,
	contactCooldown: number,
	dashHit: boolean,
}

type Projectile = {
	id: number,
	owner: string, -- "enemy" | "player"
	kind: string, -- "bullet" | "sniper" | "ring" | "player"
	x: number,
	z: number,
	prevX: number,
	prevZ: number,
	vx: number,
	vz: number,
	speed: number,
	age: number,
	life: number,
	damage: number,
	radius: number,
	crit: boolean,
	pierce: number,
	ricochet: number,
	hits: { [number]: boolean },
}

type Grenade = { id: number, x: number, z: number, radius: number, damage: number, explodeAt: number }
type Pickup = { id: number, x: number, z: number, value: number }

type Outbox = {
	projectiles: { { [string]: any } },
	gone: { number },
	damage: { { [string]: any } },
	died: { { [string]: any } },
	collected: { { [string]: any } },
}

type Session = {
	id: string,
	player: Player,
	character: Model,
	humanoid: Humanoid,
	mode: string,
	level: number,
	area: number,
	plan: LevelPlan,
	slot: number,
	center: Vector3,
	state: string, -- "lobby" | "countdown" | "fighting" | "choosing" | "break" | "ended"
	stateEnds: number,
	clock: number,
	started: boolean,
	fightStartsAt: number, -- s.clock when the 3-2-1 countdown ends (chest rule)
	settled: boolean,
	base: CombatStats?,
	stats: RunStats?,
	abilityStacks: { [string]: number },
	hp: number,
	shield: number,
	wave: number,
	waveCount: number,
	enemies: { [number]: Enemy },
	projectiles: { [number]: Projectile },
	grenades: { [number]: Grenade },
	pickups: { [number]: Pickup },
	nextEnemyId: number,
	nextProjectileId: number,
	nextPickupId: number,
	nextTelegraphId: number,
	fireCooldown: number,
	cash: number,
	kills: number,
	choice: { string }?,
	rng: Random,
	abilityRng: any, -- Rng.Rng (typed as any: generic method types confuse the checker)
	px: number,
	pz: number,
	pSpeed: number,
	noRootTime: number,
	snapTimer: number,
	outbox: Outbox,
	resolvedHits: { [number]: boolean },
	hitBudget: number,
	pendingDefeat: boolean,
	lastHpSent: number,
	lastShieldSent: number,
	lastMaxSent: number,
}

local registry: { [string]: any } = {}
local sessions: { [Player]: Session } = {}
local sessionCounter = 0

-------------------------------------------------------------------------------
-- Small helpers
-------------------------------------------------------------------------------
local function isFinite(value: any): boolean
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function isInteger(value: any): boolean
	return isFinite(value) and math.floor(value) == value
end

local function getHumanoid(character: Model?): Humanoid?
	if not character then
		return nil
	end
	return character:FindFirstChildOfClass("Humanoid")
end

local function getRoot(character: Model?): BasePart?
	if not character then
		return nil
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root
	end
	return nil
end

local function serverNow(): number
	return Workspace:GetServerTimeNow()
end

local function round1(n: number): number
	return math.floor(n * 10 + 0.5) / 10
end

-- Shortest distance from point (px, pz) to the segment (ax, az) -> (bx, bz).
local function segmentDistance(ax: number, az: number, bx: number, bz: number, px: number, pz: number): number
	local dx, dz = bx - ax, bz - az
	local lengthSq = dx * dx + dz * dz
	local t = 0
	if lengthSq > 1e-9 then
		t = math.clamp(((px - ax) * dx + (pz - az) * dz) / lengthSq, 0, 1)
	end
	local cx, cz = ax + dx * t - px, az + dz * t - pz
	return math.sqrt(cx * cx + cz * cz)
end

local function rotate(x: number, z: number, degrees: number): (number, number)
	local r = math.rad(degrees)
	local c, s = math.cos(r), math.sin(r)
	return x * c - z * s, x * s + z * c
end

-- Runs fn and reports whether it worked (never errors).
local function try(fn: () -> ()): (boolean, any)
	return pcall(function(): any
		fn()
		return nil
	end)
end

local function safeFire(player: Player, channel: string, msg: string, payload: any)
	if player.Parent == nil then
		return
	end
	local ok, err = try(function()
		Net.Fire(channel, player, msg, payload)
	end)
	if not ok then
		warn("[CombatService] could not send " .. msg .. ": " .. tostring(err))
	end
end

-- Calls registry.<service>:<method>(...) if it exists; never errors.
local function callService(serviceName: string, method: string, ...: any): (boolean, ...any)
	local service = registry[serviceName]
	if type(service) ~= "table" or type(service[method]) ~= "function" then
		return false
	end
	local results = table.pack(pcall(service[method], service, ...))
	if not results[1] then
		warn(("[CombatService] %s:%s failed: %s"):format(serviceName, method, tostring(results[2])))
		return false
	end
	return true, table.unpack(results, 2, results.n)
end

local function nextAreaName(area: number): string
	local def = Config.Areas[area + 1]
	return if def then def.name else "the next area"
end

-------------------------------------------------------------------------------
-- Static arenas (workspace.Arenas)
-------------------------------------------------------------------------------
local function makePart(props: {
	name: string,
	size: Vector3,
	cframe: CFrame,
	color: Color3,
	parent: Instance,
	shape: Enum.PartType?,
	transparency: number?,
	collide: boolean?,
}): Part
	local part = Instance.new("Part")
	part.Name = props.name
	part.Anchored = true
	part.Material = Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	if props.shape then
		part.Shape = props.shape
	end
	part.Size = props.size
	part.CFrame = props.cframe
	part.Color = props.color
	part.Transparency = props.transparency or 0
	part.CanCollide = props.collide ~= false
	part.Parent = props.parent
	return part
end

-- Round Lego studs along the top of a block (size x/z along its top face).
local function addStuds(parent: Instance, topCenter: Vector3, lengthX: number, lengthZ: number, color: Color3)
	local spacing = 4
	local countX = math.max(1, math.floor(lengthX / spacing))
	local countZ = math.max(1, math.floor(lengthZ / spacing))
	for ix = 1, countX do
		for iz = 1, countZ do
			local ox = (ix - 0.5) * (lengthX / countX) - lengthX / 2
			local oz = (iz - 0.5) * (lengthZ / countZ) - lengthZ / 2
			local stud = makePart({
				name = "Stud",
				size = Vector3.new(0.7, 1.7, 1.7),
				cframe = CFrame.new(topCenter + Vector3.new(ox, 0.35, oz)) * CFrame.Angles(0, 0, math.rad(90)),
				color = color,
				parent = parent,
				shape = Enum.PartType.Cylinder,
				collide = false,
			})
			stud.CastShadow = false
		end
	end
end

local function buildArena(slot: number, folder: Instance)
	local center = Layout.ArenaCenter(slot)
	local model = Instance.new("Model")
	model.Name = "Arena" .. slot

	local floorY = Layout.FLOOR_Y
	-- Rim slab under everything (visible outside the hedges).
	makePart({
		name = "Rim",
		size = Vector3.new(Layout.ARENA_SIZE_X + 20, 2, Layout.ARENA_SIZE_Z + 20),
		cframe = CFrame.new(center + Vector3.new(0, floorY - 2, 0)),
		color = P.BasePath,
		parent = model,
	})
	-- Checkerboard floor: BaseFloor tiles with lighter BasePath tiles (image 1 colours).
	local tilesX = math.ceil(Layout.ARENA_SIZE_X / TILE)
	local tilesZ = math.ceil(Layout.ARENA_SIZE_Z / TILE)
	local sizeX = Layout.ARENA_SIZE_X / tilesX
	local sizeZ = Layout.ARENA_SIZE_Z / tilesZ
	for ix = 1, tilesX do
		for iz = 1, tilesZ do
			local x = -HALF_X + (ix - 0.5) * sizeX
			local z = -HALF_Z + (iz - 0.5) * sizeZ
			makePart({
				name = "Tile",
				size = Vector3.new(sizeX, 1, sizeZ),
				cframe = CFrame.new(center + Vector3.new(x, floorY - 0.5, z)),
				color = if (ix + iz) % 2 == 0 then P.BaseFloor else P.BasePath,
				parent = model,
			})
		end
	end
	-- White start line where the player enters (south side).
	makePart({
		name = "StartLine",
		size = Vector3.new(16, 0.1, 0.8),
		cframe = CFrame.new(center + Vector3.new(0, floorY + 0.05, CombatDefs.PLAYER_START.z + 4)),
		color = P.BaseLine,
		parent = model,
		collide = false,
	})

	-- Green Lego hedges around the arena, with studs on top.
	local wallThickness = 3
	local walls = {
		{ x = 0, z = -HALF_Z - wallThickness / 2, lx = Layout.ARENA_SIZE_X + wallThickness * 2, lz = wallThickness },
		{ x = 0, z = HALF_Z + wallThickness / 2, lx = Layout.ARENA_SIZE_X + wallThickness * 2, lz = wallThickness },
		{ x = -HALF_X - wallThickness / 2, z = 0, lx = wallThickness, lz = Layout.ARENA_SIZE_Z },
		{ x = HALF_X + wallThickness / 2, z = 0, lx = wallThickness, lz = Layout.ARENA_SIZE_Z },
	}
	for index, w in walls do
		makePart({
			name = "Hedge" .. index,
			size = Vector3.new(w.lx, ARENA_WALL_HEIGHT, w.lz),
			cframe = CFrame.new(center + Vector3.new(w.x, floorY + ARENA_WALL_HEIGHT / 2, w.z)),
			color = P.Hedge,
			parent = model,
		})
		addStuds(model, center + Vector3.new(w.x, floorY + ARENA_WALL_HEIGHT, w.z), w.lx, w.lz, P.Hedge)
		-- Tall invisible wall on the same line: nobody leaves the arena.
		makePart({
			name = "Boundary" .. index,
			size = Vector3.new(w.lx, BOUNDARY_HEIGHT, w.lz),
			cframe = CFrame.new(center + Vector3.new(w.x, floorY + BOUNDARY_HEIGHT / 2, w.z)),
			color = P.Hedge,
			parent = model,
			transparency = 1,
		})
	end
	-- Invisible ceiling (just in case something flings the character upwards).
	makePart({
		name = "Ceiling",
		size = Vector3.new(Layout.ARENA_SIZE_X + 6, 2, Layout.ARENA_SIZE_Z + 6),
		cframe = CFrame.new(center + Vector3.new(0, floorY + BOUNDARY_HEIGHT, 0)),
		color = P.Hedge,
		parent = model,
		transparency = 1,
	})

	-- Cover blocks (same rectangles the simulation uses).
	for index, c in CombatDefs.COVER do
		local color = if c.kind == "crate" then P.Wood elseif c.kind == "sandbag" then P.WallYellow else P.WallRed
		local blockCenter = center + Vector3.new(c.x, floorY + c.height / 2, c.z)
		makePart({
			name = "Cover" .. index,
			size = Vector3.new(c.sx, c.height, c.sz),
			cframe = CFrame.new(blockCenter),
			color = color,
			parent = model,
		})
		if c.kind == "crate" then
			-- darker frame strips on the crate faces
			for _, offset in { -1, 1 } do
				makePart({
					name = "Plank",
					size = Vector3.new(c.sx + 0.2, 0.6, 0.6),
					cframe = CFrame.new(blockCenter + Vector3.new(0, offset * (c.height / 2 - 0.3), c.sz / 2)),
					color = P.CoinDark,
					parent = model,
					collide = false,
				})
				makePart({
					name = "Plank",
					size = Vector3.new(c.sx + 0.2, 0.6, 0.6),
					cframe = CFrame.new(blockCenter + Vector3.new(0, offset * (c.height / 2 - 0.3), -c.sz / 2)),
					color = P.CoinDark,
					parent = model,
					collide = false,
				})
			end
		end
		addStuds(model, center + Vector3.new(c.x, floorY + c.height, c.z), c.sx, c.sz, color)
	end

	model.Parent = folder
end

local function buildArenas()
	local existing = Workspace:FindFirstChild("Arenas")
	local folder: Instance
	if existing then
		folder = existing
	else
		local created = Instance.new("Folder")
		created.Name = "Arenas"
		created.Parent = Workspace
		folder = created
	end
	for slot = 1, Config.MaxSlots do
		if not folder:FindFirstChild("Arena" .. slot) then
			local ok, err = try(function()
				buildArena(slot, folder)
			end)
			if not ok then
				warn("[CombatService] building arena " .. slot .. " failed: " .. tostring(err))
			end
		end
	end
end

-------------------------------------------------------------------------------
-- Player attributes + humanoid
-------------------------------------------------------------------------------
local function sendVitals(s: Session, force: boolean?)
	local maxHp = if s.stats then s.stats.maxHp else 0
	local hp = round1(math.max(0, s.hp))
	if force or hp ~= s.lastHpSent then
		s.lastHpSent = hp
		s.player:SetAttribute("CombatHP", hp)
	end
	local maxRounded = round1(maxHp)
	if force or maxRounded ~= s.lastMaxSent then
		s.lastMaxSent = maxRounded
		s.player:SetAttribute("CombatMaxHP", maxRounded)
	end
	if force or s.shield ~= s.lastShieldSent then
		s.lastShieldSent = s.shield
		s.player:SetAttribute("CombatShield", s.shield)
	end
end

local function clearAttributes(player: Player)
	player:SetAttribute("CombatActive", false)
	player:SetAttribute("CombatHP", nil)
	player:SetAttribute("CombatMaxHP", nil)
	player:SetAttribute("CombatShield", nil)
end

-- Back to the normal base values from StarterPlayer (what a player has on foot).
local function restoreHumanoid(s: Session)
	local humanoid = s.humanoid
	if humanoid.Parent and humanoid.Health > 0 then
		humanoid.WalkSpeed = StarterPlayer.CharacterWalkSpeed
		humanoid.JumpHeight = StarterPlayer.CharacterJumpHeight
		humanoid.JumpPower = StarterPlayer.CharacterJumpPower
	end
end

-------------------------------------------------------------------------------
-- Outbox (batched events, flushed once per tick)
-------------------------------------------------------------------------------
local function newOutbox(): Outbox
	return { projectiles = {}, gone = {}, damage = {}, died = {}, collected = {} }
end

local function pushDamage(s: Session, x: number, z: number, amount: number, kind: string, crit: boolean?)
	if #s.outbox.damage < 60 then
		table.insert(s.outbox.damage, { x = x, z = z, amount = round1(amount), kind = kind, crit = crit == true })
	end
end

local function flush(s: Session)
	local o = s.outbox
	if #o.projectiles > 0 then
		safeFire(s.player, "Combat", "ProjectileBatch", { list = o.projectiles })
	end
	if #o.gone > 0 then
		safeFire(s.player, "Combat", "ProjectileGone", { ids = o.gone })
	end
	if #o.damage > 0 then
		safeFire(s.player, "Combat", "Damage", { list = o.damage })
	end
	if #o.died > 0 then
		safeFire(s.player, "Combat", "EnemyDied", { list = o.died })
	end
	if #o.collected > 0 then
		safeFire(s.player, "Combat", "Collected", { list = o.collected })
	end
	s.outbox = newOutbox()
end

local function describeProjectile(p: Projectile): { [string]: any }
	return {
		id = p.id,
		ownerKind = p.owner,
		x = p.x,
		z = p.z,
		vx = p.vx,
		vz = p.vz,
		kind = p.kind,
		born = serverNow(),
		life = p.life - p.age,
		radius = p.radius,
	}
end

local function removeProjectile(s: Session, id: number)
	if s.projectiles[id] then
		s.projectiles[id] = nil
		table.insert(s.outbox.gone, id)
	end
end

local function clearEnemyAttacks(s: Session)
	for id, p in s.projectiles do
		if p.owner == "enemy" then
			removeProjectile(s, id)
		end
	end
	table.clear(s.grenades)
end

-------------------------------------------------------------------------------
-- Spawning
-------------------------------------------------------------------------------
local function spawnProjectile(
	s: Session,
	owner: string,
	kind: string,
	x: number,
	z: number,
	dirX: number,
	dirZ: number,
	speed: number,
	damage: number,
	radius: number,
	life: number
): Projectile
	s.nextProjectileId += 1
	local p: Projectile = {
		id = s.nextProjectileId,
		owner = owner,
		kind = kind,
		x = x,
		z = z,
		prevX = x,
		prevZ = z,
		vx = dirX * speed,
		vz = dirZ * speed,
		speed = speed,
		age = 0,
		life = life,
		damage = damage,
		radius = radius,
		crit = false,
		pierce = 0,
		ricochet = 0,
		hits = {},
	}
	s.projectiles[p.id] = p
	table.insert(s.outbox.projectiles, describeProjectile(p))
	return p
end

local function enemyBullet(s: Session, e: Enemy, dirX: number, dirZ: number, speed: number, kind: string)
	local startX = e.x + dirX * (e.def.radius + 0.6)
	local startZ = e.z + dirZ * (e.def.radius + 0.6)
	spawnProjectile(s, "enemy", kind, startX, startZ, dirX, dirZ, speed, e.damage, ENEMY_BULLET_RADIUS, 160 / speed)
end

-- A free spot in a zone that is not inside cover (and optionally far from the player).
local function freeSpot(
	s: Session,
	minX: number,
	maxX: number,
	minZ: number,
	maxZ: number,
	pad: number,
	awayFromPlayer: number
): (number, number)
	local x, z = 0, minZ
	for _ = 1, 30 do
		x = minX + s.rng:NextNumber() * (maxX - minX)
		z = minZ + s.rng:NextNumber() * (maxZ - minZ)
		local dx, dz = x - s.px, z - s.pz
		if not CombatDefs.CoverAt(x, z, pad) and (dx * dx + dz * dz) >= awayFromPlayer * awayFromPlayer then
			return x, z
		end
	end
	return x, z
end

local function spawnPickup(s: Session, x: number, z: number, value: number)
	if value <= 0 then
		return
	end
	-- never inside cover (could not be reached)
	local cover = CombatDefs.CoverAt(x, z, 1)
	if cover then
		z = cover.z + (if z >= cover.z then 1 else -1) * (cover.sz / 2 + 1.5)
	end
	s.nextPickupId += 1
	s.pickups[s.nextPickupId] = {
		id = s.nextPickupId,
		x = math.clamp(x, -HALF_X + 1.5, HALF_X - 1.5),
		z = math.clamp(z, -HALF_Z + 1.5, HALF_Z - 1.5),
		value = value,
	}
end

local function startWave(s: Session, wave: number)
	local planWave = s.plan.waves[wave]
	if not planWave then
		return
	end
	s.wave = wave
	s.state = "fighting"
	local stats = s.stats
	s.shield = if stats then stats.shieldPerWave else 0
	sendVitals(s)

	local zone = CombatDefs.ENEMY_ZONE
	for index, pe in planWave.enemies do
		local def = CombatDefs.ENEMIES[pe.type]
		if def then
			local scaled = CombatDefs.EnemyStats(pe.type, s.level)
			local x, z
			if pe.boss then
				x, z = 0, -HALF_Z + 14
			else
				x, z = freeSpot(
					s,
					-HALF_X + 4,
					HALF_X - 4,
					zone.minZ,
					zone.maxZ,
					def.radius + 1,
					SPAWN_MIN_PLAYER_DISTANCE
				)
			end
			s.nextEnemyId += 1
			local e: Enemy = {
				id = s.nextEnemyId,
				type = pe.type,
				def = def,
				boss = pe.boss,
				x = x,
				z = z,
				hp = pe.hp,
				maxHp = pe.hp,
				damage = pe.damage,
				cash = pe.cash,
				speed = scaled.speed,
				interval = scaled.interval,
				windup = scaled.windup,
				bulletSpeed = scaled.bulletSpeed,
				state = "spawn",
				timer = CombatDefs.SPAWN_TIME + (index - 1) * 0.08,
				cooldown = scaled.interval * (0.4 + 0.6 * s.rng:NextNumber()),
				aimX = 0,
				aimZ = 1,
				strafe = if s.rng:NextNumber() < 0.5 then 1 else -1,
				strafeTimer = 2 + s.rng:NextNumber() * 3,
				attackCount = 0,
				contactCooldown = 0,
				dashHit = false,
			}
			s.enemies[e.id] = e
		end
	end
	-- Cash piles lying on the ground (30% of the level reward).
	for _, value in planWave.piles do
		local x, z = freeSpot(s, -HALF_X + 4, HALF_X - 4, -HALF_Z + 5, HALF_Z - 14, 1.5, 6)
		spawnPickup(s, x, z, value)
	end
end

-------------------------------------------------------------------------------
-- Damage
-------------------------------------------------------------------------------
local finish: (s: Session, victory: boolean, reason: string) -> ()

local function killEnemy(s: Session, e: Enemy)
	if not s.enemies[e.id] then
		return
	end
	s.enemies[e.id] = nil
	s.kills += 1
	table.insert(s.outbox.died, { id = e.id, type = e.type, x = e.x, z = e.z, boss = e.boss })
	spawnPickup(s, e.x, e.z, e.cash)
	local stats = s.stats
	if stats and stats.vampire > 0 and s.hp > 0 then
		local heal = stats.maxHp * stats.vampire
		s.hp = math.min(stats.maxHp, s.hp + heal)
		pushDamage(s, s.px, s.pz, heal, "heal")
	end
end

local function damageEnemy(s: Session, e: Enemy, amount: number, crit: boolean)
	if e.hp <= 0 or not s.enemies[e.id] then
		return
	end
	e.hp -= amount
	pushDamage(s, e.x, e.z, amount, "enemy", crit)
	if e.hp <= 0 then
		killEnemy(s, e)
	end
end

-- Something hit the player. source: "bullet" | "grenade" | "melee"
local function hitPlayer(s: Session, amount: number, source: string, attacker: Enemy?)
	local stats = s.stats
	if s.state ~= "fighting" or not stats or s.hp <= 0 then
		return
	end
	if stats.dodge > 0 and s.rng:NextNumber() < stats.dodge then
		pushDamage(s, s.px, s.pz, 0, "dodge")
		return
	end
	if s.shield > 0 then
		s.shield -= 1
		pushDamage(s, s.px, s.pz, 0, "shield")
		sendVitals(s)
		return
	end
	local damage = amount * (1 - stats.damageReduction)
	s.hp = math.max(0, s.hp - damage)
	pushDamage(s, s.px, s.pz, damage, "player")
	if source == "melee" and attacker and stats.thorns > 0 then
		damageEnemy(s, attacker, amount * stats.thorns, false)
	end
	sendVitals(s)
	if s.hp <= 0 then
		s.pendingDefeat = true
	end
end

-------------------------------------------------------------------------------
-- Enemy AI
-------------------------------------------------------------------------------
-- Warns the client about an attack (red grenade circle / sniper laser). Returns its id.
local function startTelegraph(s: Session, payload: { [string]: any }): number
	s.nextTelegraphId += 1
	payload.id = s.nextTelegraphId
	payload.start = serverNow()
	safeFire(s.player, "Combat", "Telegraph", payload)
	return s.nextTelegraphId
end

local function fireAttack(s: Session, e: Enemy, dirX: number, dirZ: number)
	local def = e.def
	if def.attack == "rifle" then
		enemyBullet(s, e, dirX, dirZ, e.bulletSpeed, "bullet")
	elseif def.attack == "spread" then
		local count = s.rng:NextInteger(def.bullets, def.bulletsMax)
		for i = 1, count do
			local angle = if count > 1 then -def.spreadDeg / 2 + def.spreadDeg * (i - 1) / (count - 1) else 0
			local x, z = rotate(dirX, dirZ, angle)
			enemyBullet(s, e, x, z, e.bulletSpeed, "bullet")
		end
	elseif def.attack == "sniper" then
		-- along the laser line shown during the windup (locked direction)
		enemyBullet(s, e, e.aimX, e.aimZ, e.bulletSpeed, "sniper")
	elseif def.attack == "grenade" then
		local fuse = def.fuse or 0.9
		local radius = def.blastRadius or 5
		local offset = s.rng:NextNumber() * 1.5
		local angle = s.rng:NextNumber() * math.pi * 2
		local gx = math.clamp(s.px + math.cos(angle) * offset, -HALF_X + 1, HALF_X - 1)
		local gz = math.clamp(s.pz + math.sin(angle) * offset, -HALF_Z + 1, HALF_Z - 1)
		local id = startTelegraph(s, {
			kind = "grenade",
			x = gx,
			z = gz,
			radius = radius,
			fromX = e.x,
			fromZ = e.z,
			duration = fuse,
		})
		s.grenades[id] = { id = id, x = gx, z = gz, radius = radius, damage = e.damage, explodeAt = s.clock + fuse }
	elseif def.attack == "rings" then
		e.attackCount += 1
		if e.attackCount % 3 == 0 then
			-- aimed burst of 5
			for i = 1, 5 do
				local angle = -def.spreadDeg / 2 + def.spreadDeg * (i - 1) / 4
				local x, z = rotate(dirX, dirZ, angle)
				enemyBullet(s, e, x, z, e.bulletSpeed * 1.2, "bullet")
			end
		else
			local count = def.bullets
			local offset = e.attackCount * 13
			for i = 1, count do
				local x, z = rotate(1, 0, offset + 360 * (i - 1) / count)
				enemyBullet(s, e, x, z, e.bulletSpeed, "ring")
			end
		end
	end
end

local function updateEnemy(s: Session, e: Enemy, dt: number)
	local def = e.def
	local dx, dz = s.px - e.x, s.pz - e.z
	local dist = math.sqrt(dx * dx + dz * dz)
	local ux, uz = 0, 1
	if dist > 1e-3 then
		ux, uz = dx / dist, dz / dist
	end
	e.timer -= dt
	e.cooldown -= dt
	e.contactCooldown -= dt

	if e.state == "spawn" then
		if e.timer <= 0 then
			e.state = "move"
		end
		return
	end

	local moveX, moveZ, speed = 0, 0, e.speed
	local touching = dist <= def.radius + CombatDefs.PLAYER_RADIUS + 0.3

	if def.attack == "melee" then
		if e.state == "move" then
			moveX, moveZ = ux, uz
			if touching and e.contactCooldown <= 0 then
				e.contactCooldown = 1
				hitPlayer(s, e.damage * 0.5, "melee", e)
			end
			if dist <= (def.triggerRange or 15) and e.cooldown <= 0 then
				e.state = "charge"
				e.timer = e.windup
				e.aimX, e.aimZ = ux, uz
			end
		elseif e.state == "charge" then
			-- crouch / flash (telegraph), keep turning towards the player a little
			e.aimX, e.aimZ = ux, uz
			if e.timer <= 0 then
				e.state = "dash"
				e.timer = def.dashTime or 0.6
				e.dashHit = false
			end
		elseif e.state == "dash" then
			moveX, moveZ, speed = e.aimX, e.aimZ, def.dashSpeed or 30
			if not e.dashHit and dist <= def.radius + CombatDefs.PLAYER_RADIUS + 0.5 then
				e.dashHit = true
				hitPlayer(s, e.damage, "melee", e)
			end
			if e.timer <= 0 then
				e.state = "cool"
				e.timer = 0.8
				e.cooldown = e.interval * (0.85 + 0.3 * s.rng:NextNumber())
			end
		else -- "cool"
			if e.timer <= 0 then
				e.state = "move"
			end
		end
	else
		if e.state == "move" then
			local preferred = def.preferredRange
			if dist > preferred + 4 then
				moveX, moveZ = ux, uz
			elseif dist < preferred - 6 then
				moveX, moveZ = -ux, -uz
			else
				moveX, moveZ = -uz * e.strafe * 0.6, ux * e.strafe * 0.6
			end
			e.strafeTimer -= dt
			if e.strafeTimer <= 0 then
				e.strafe = -e.strafe
				e.strafeTimer = 2 + s.rng:NextNumber() * 3
			end
			if e.cooldown <= 0 and dist <= 70 then
				e.timer = e.windup
				e.aimX, e.aimZ = ux, uz
				if def.attack == "sniper" then
					e.state = "laser"
					startTelegraph(s, {
						kind = "laser",
						enemyId = e.id,
						x = e.x,
						z = e.z,
						dx = ux,
						dz = uz,
						duration = e.windup,
					})
				elseif def.attack == "grenade" then
					e.state = "throw"
				else
					e.state = "aim"
				end
			end
		elseif e.state == "aim" or e.state == "laser" or e.state == "throw" then
			if e.timer <= 0 then
				-- rifles / gunners / the boss aim at where you are NOW (no lead):
				-- keep moving and the bullet misses.
				fireAttack(s, e, ux, uz)
				e.state = "move"
				e.cooldown = e.interval * (0.85 + 0.3 * s.rng:NextNumber())
			end
		else
			e.state = "move"
		end
	end

	if moveX ~= 0 or moveZ ~= 0 then
		e.x += moveX * speed * dt
		e.z += moveZ * speed * dt
	end
end

-- Keep enemies apart, off the player, out of cover and inside the arena.
local function separateEnemies(s: Session)
	local list: { Enemy } = {}
	for _, e in s.enemies do
		table.insert(list, e)
	end
	for i = 1, #list do
		local a = list[i]
		for j = i + 1, #list do
			local b = list[j]
			local dx, dz = b.x - a.x, b.z - a.z
			local minDist = a.def.radius + b.def.radius
			local distSq = dx * dx + dz * dz
			if distSq < minDist * minDist then
				local dist = math.sqrt(distSq)
				if dist < 1e-3 then
					dx, dz, dist = 1, 0, 1
				end
				local push = (minDist - dist) / 2
				a.x -= dx / dist * push
				a.z -= dz / dist * push
				b.x += dx / dist * push
				b.z += dz / dist * push
			end
		end
	end
	for _, e in list do
		local r = e.def.radius
		-- not on top of the player (dashing chargers may pass through)
		if e.state ~= "dash" then
			local dx, dz = e.x - s.px, e.z - s.pz
			local minDist = r + CombatDefs.PLAYER_RADIUS
			local distSq = dx * dx + dz * dz
			if distSq < minDist * minDist and distSq > 1e-6 then
				local dist = math.sqrt(distSq)
				e.x = s.px + dx / dist * minDist
				e.z = s.pz + dz / dist * minDist
			end
		end
		-- out of cover: push along the shallowest side
		local cover = CombatDefs.CoverAt(e.x, e.z, r)
		if cover then
			local penLeft = (e.x - (cover.x - cover.sx / 2 - r))
			local penRight = ((cover.x + cover.sx / 2 + r) - e.x)
			local penTop = (e.z - (cover.z - cover.sz / 2 - r))
			local penBottom = ((cover.z + cover.sz / 2 + r) - e.z)
			local least = math.min(penLeft, penRight, penTop, penBottom)
			if least == penLeft then
				e.x -= penLeft
			elseif least == penRight then
				e.x += penRight
			elseif least == penTop then
				e.z -= penTop
			else
				e.z += penBottom
			end
			e.strafe = -e.strafe
		end
		local clampedX = math.clamp(e.x, -HALF_X + r, HALF_X - r)
		local clampedZ = math.clamp(e.z, -HALF_Z + r, HALF_Z - r)
		if clampedX ~= e.x or clampedZ ~= e.z then
			e.strafe = -e.strafe
		end
		e.x, e.z = clampedX, clampedZ
	end
end

-------------------------------------------------------------------------------
-- Player shooting + projectiles
-------------------------------------------------------------------------------
local function nearestEnemy(s: Session, x: number, z: number, maxRange: number, exclude: { [number]: boolean }?): Enemy?
	local best: Enemy? = nil
	local bestSq = maxRange * maxRange
	for id, e in s.enemies do
		if e.state ~= "spawn" and e.hp > 0 and not (exclude and exclude[id]) then
			local dx, dz = e.x - x, e.z - z
			local d = dx * dx + dz * dz
			if d <= bestSq then
				best, bestSq = e, d
			end
		end
	end
	return best
end

-- Archero rule: you only shoot while standing still.
local function playerFire(s: Session, dt: number)
	local stats = s.stats
	if not stats then
		return
	end
	s.fireCooldown = math.max(0, s.fireCooldown - dt)
	if s.pSpeed >= CombatDefs.STILL_SPEED or s.fireCooldown > 0 then
		return
	end
	local target = nearestEnemy(s, s.px, s.pz, stats.range, nil)
	if not target then
		return
	end
	s.fireCooldown = 1 / stats.fireRate
	local dx, dz = target.x - s.px, target.z - s.pz
	local dist = math.sqrt(dx * dx + dz * dz)
	if dist < 1e-3 then
		dx, dz, dist = 0, -1, 1
	end
	dx, dz = dx / dist, dz / dist
	local count = math.max(1, stats.projectiles)
	local fan = if count > 1 then math.min(60, math.max(stats.spreadDeg, MULTISHOT_FAN_DEG * (count - 1))) else 0
	for i = 1, count do
		local angle = if count > 1 then -fan / 2 + fan * (i - 1) / (count - 1) else 0
		-- a single bullet still gets a little random spread from the weapon
		if count == 1 and stats.spreadDeg > 0 then
			angle = (s.rng:NextNumber() - 0.5) * stats.spreadDeg
		end
		local bx, bz = rotate(dx, dz, angle)
		local crit = s.rng:NextNumber() < stats.critChance
		local damage = stats.damage * (if crit then stats.critMult else 1)
		s.nextProjectileId += 1
		local p: Projectile = {
			id = s.nextProjectileId,
			owner = "player",
			kind = "player",
			x = s.px + bx * 1.5,
			z = s.pz + bz * 1.5,
			prevX = s.px,
			prevZ = s.pz,
			vx = bx * stats.projectileSpeed,
			vz = bz * stats.projectileSpeed,
			speed = stats.projectileSpeed,
			age = 0,
			life = stats.range / stats.projectileSpeed + 0.1,
			damage = damage,
			radius = PLAYER_BULLET_RADIUS,
			crit = crit,
			pierce = stats.pierce,
			ricochet = stats.ricochet,
			hits = {},
		}
		s.projectiles[p.id] = p
		table.insert(s.outbox.projectiles, describeProjectile(p))
	end
end

local function playerBulletHit(s: Session, p: Projectile, e: Enemy)
	local stats = s.stats
	p.hits[e.id] = true
	local hitX, hitZ = e.x, e.z
	damageEnemy(s, e, p.damage, p.crit)
	-- Explosive: splash damage around the target.
	if stats and stats.explosive > 0 then
		pushDamage(s, hitX, hitZ, 0, "boom")
		local r2 = CombatDefs.EXPLOSIVE_RADIUS * CombatDefs.EXPLOSIVE_RADIUS
		for id, other in s.enemies do
			if id ~= e.id and other.state ~= "spawn" then
				local dx, dz = other.x - hitX, other.z - hitZ
				if dx * dx + dz * dz <= r2 then
					damageEnemy(s, other, p.damage * stats.explosive, false)
				end
			end
		end
	end
	if p.pierce > 0 then
		p.pierce -= 1
		return
	end
	if p.ricochet > 0 then
		local nextTarget = nearestEnemy(s, hitX, hitZ, CombatDefs.RICOCHET_RANGE, p.hits)
		if nextTarget then
			p.ricochet -= 1
			local dx, dz = nextTarget.x - hitX, nextTarget.z - hitZ
			local dist = math.max(1e-3, math.sqrt(dx * dx + dz * dz))
			p.x, p.z = hitX, hitZ
			p.prevX, p.prevZ = hitX, hitZ
			p.vx, p.vz = dx / dist * p.speed, dz / dist * p.speed
			p.age = 0
			p.life = CombatDefs.RICOCHET_RANGE / p.speed + 0.2
			-- same id: the client replaces its copy of this bullet
			table.insert(s.outbox.projectiles, describeProjectile(p))
			return
		end
	end
	removeProjectile(s, p.id)
end

local function updateProjectiles(s: Session, dt: number)
	for id, p in s.projectiles do
		p.prevX, p.prevZ = p.x, p.z
		p.x += p.vx * dt
		p.z += p.vz * dt
		p.age += dt
		local outside = math.abs(p.x) > HALF_X + 1 or math.abs(p.z) > HALF_Z + 1
		if p.age > p.life or outside or CombatDefs.CoverAt(p.x, p.z, 0) then
			removeProjectile(s, id)
		elseif p.owner == "enemy" then
			-- anti-cheat floor: the server applies hits it sees itself
			if segmentDistance(p.prevX, p.prevZ, p.x, p.z, s.px, s.pz) <= CombatDefs.SERVER_HIT_RADIUS then
				s.resolvedHits[id] = true
				removeProjectile(s, id)
				hitPlayer(s, p.damage, "bullet", nil)
			end
		else
			for enemyId, e in s.enemies do
				if not p.hits[enemyId] and e.state ~= "spawn" then
					local reach = e.def.radius + p.radius
					if segmentDistance(p.prevX, p.prevZ, p.x, p.z, e.x, e.z) <= reach then
						playerBulletHit(s, p, e)
						break
					end
				end
			end
		end
	end
end

local function updateGrenades(s: Session)
	for id, g in s.grenades do
		if s.clock >= g.explodeAt then
			s.grenades[id] = nil
			local dx, dz = s.px - g.x, s.pz - g.z
			if math.sqrt(dx * dx + dz * dz) <= g.radius then
				hitPlayer(s, g.damage, "grenade", nil)
			end
		end
	end
end

local function collectPickups(s: Session, everything: boolean?)
	local r2 = CombatDefs.MAGNET_RADIUS * CombatDefs.MAGNET_RADIUS
	for id, pickup in s.pickups do
		local dx, dz = pickup.x - s.px, pickup.z - s.pz
		if everything or dx * dx + dz * dz <= r2 then
			s.pickups[id] = nil
			s.cash = Format.RoundCents(s.cash + pickup.value)
			table.insert(s.outbox.collected, { id = id, x = pickup.x, z = pickup.z, value = pickup.value })
		end
	end
end

-------------------------------------------------------------------------------
-- Snapshots
-------------------------------------------------------------------------------
local function sendSnapshot(s: Session)
	local enemies = {}
	for _, e in s.enemies do
		table.insert(enemies, {
			id = e.id,
			type = e.type,
			x = e.x,
			z = e.z,
			hp = math.max(0, e.hp),
			maxHp = e.maxHp,
			state = e.state,
		})
	end
	local pickups = {}
	for _, p in s.pickups do
		table.insert(pickups, { id = p.id, x = p.x, z = p.z, value = p.value })
	end
	if #pickups > CombatDefs.SNAP_MAX_PICKUPS then
		-- send the ones nearest to the player
		table.sort(pickups, function(a, b)
			local da = (a.x - s.px) ^ 2 + (a.z - s.pz) ^ 2
			local db = (b.x - s.px) ^ 2 + (b.z - s.pz) ^ 2
			return da < db
		end)
	end
	local buf = CombatDefs.EncodeSnap({ t = serverNow(), cash = s.cash, enemies = enemies, pickups = pickups })
	if s.player.Parent then
		local ok, err = try(function()
			Net.Fire("CombatSnap", s.player, buf)
		end)
		if not ok then
			warn("[CombatService] snapshot failed: " .. tostring(err))
		end
	end
end

-------------------------------------------------------------------------------
-- Waves, abilities, results
-------------------------------------------------------------------------------
local function announceWave(s: Session, wave: number, delay: number)
	local planWave = s.plan.waves[wave]
	local boss = false
	if planWave then
		for _, e in planWave.enemies do
			boss = boss or e.boss
		end
	end
	safeFire(s.player, "Combat", "Wave", {
		wave = wave,
		waveCount = s.waveCount,
		startsAt = serverNow() + delay,
		boss = boss,
	})
end

local function abilityInfo(s: Session, id: string): { [string]: any }?
	local a = CombatDefs.AbilityById[id]
	if not a then
		return nil
	end
	return {
		id = a.id,
		name = a.name,
		icon = a.icon,
		description = a.description,
		stacks = s.abilityStacks[a.id] or 0,
		maxStacks = a.maxStacks,
	}
end

local function goToBreak(s: Session)
	s.state = "break"
	s.stateEnds = s.clock + CombatDefs.WAVE_BREAK
	announceWave(s, s.wave + 1, CombatDefs.WAVE_BREAK)
end

local function applyAbility(s: Session, id: string, auto: boolean)
	local a = CombatDefs.AbilityById[id]
	local base = s.base
	local old = s.stats
	if not a or not base or not old then
		return
	end
	s.abilityStacks[id] = (s.abilityStacks[id] or 0) + 1
	local stats = CombatDefs.DeriveStats(base, s.abilityStacks)
	s.stats = stats
	-- HP changes: Max HP+ heals the gain, Heal heals a share of max HP.
	if stats.maxHp > old.maxHp then
		s.hp += stats.maxHp - old.maxHp
	end
	if a.effect.kind == "heal" then
		s.hp += stats.maxHp * (a.effect.amount or 0)
	end
	s.hp = math.min(stats.maxHp, s.hp)
	if a.effect.kind == "perk" and a.effect.perk == "shield" then
		s.shield += 1 -- [PROPOSAL] the new shield is ready right away
	end
	if s.humanoid.Parent then
		s.humanoid.WalkSpeed = stats.moveSpeed
	end
	sendVitals(s)
	safeFire(s.player, "Combat", "AbilityPicked", { id = id, stacks = s.abilityStacks[id], auto = auto })
end

local function chooseAbility(s: Session, id: string, auto: boolean)
	s.choice = nil
	applyAbility(s, id, auto)
	goToBreak(s)
end

local function waveCleared(s: Session)
	clearEnemyAttacks(s)
	if s.wave >= s.waveCount then
		-- [PROPOSAL] a full clear vacuums up every coin still on the floor
		collectPickups(s, true)
		finish(s, true, "victory")
		return
	end
	local options = CombatDefs.RollAbilities(s.abilityStacks, s.abilityRng, 3)
	if #options == 0 then
		goToBreak(s)
		return
	end
	s.choice = options
	s.state = "choosing"
	s.stateEnds = s.clock + CombatDefs.ABILITY_TIMEOUT
	local list = {}
	for _, id in options do
		local info = abilityInfo(s, id)
		if info then
			table.insert(list, info)
		end
	end
	safeFire(s.player, "Combat", "AbilityChoice", {
		wave = s.wave,
		timeout = CombatDefs.ABILITY_TIMEOUT,
		options = list,
	})
end

-- Pays cash, awards the chest and records progress. Runs at most once per
-- session (also for forced endings like resetting or leaving the game).
local function settle(s: Session, victory: boolean): { [string]: any }
	s.settled = true
	local player = s.player
	local dataService = registry.DataService
	local data = if dataService then dataService:Get(player) else nil

	local collected = Format.RoundCents(s.cash)
	local paid = 0
	if collected > 0 and dataService then
		-- [PROPOSAL] collected cash is paid even on defeat (boostable gameplay income)
		paid = dataService:AddCash(player, collected, { boostable = true, source = "combat" })
	end

	local chest = nil
	-- seconds fought after the countdown: a defeat with 0 kills still earns a
	-- wooden chest after CombatDefs.CHEST_MIN_SECONDS ([PROPOSAL])
	local fightSeconds = if s.started then math.max(0, s.clock - s.fightStartsAt) else 0
	local tier = CombatDefs.ChestTier(s.mode, s.level, victory, s.kills, fightSeconds)
	if tier and data then
		local ok, uid, item = callService("InventoryService", "AwardChest", player, tier)
		if ok and item ~= nil then
			chest = { tier = tier, uid = uid, item = item }
		end
	end

	local newBest = false
	if data then
		data.combat.games += 1
		if victory then
			data.combat.wins += 1
		end
		data.stats.combatKills += s.kills
		if victory and s.mode == "arena" and s.level > data.combat.highestLevel then
			data.combat.highestLevel = s.level
			newBest = true
		end
		dataService:MarkDirty(player)
	end

	local barrierDefeated = false
	if victory and s.mode == "barrier" then
		local ok = callService("BaseService", "MarkBarrierDefeated", player, s.area)
		if not ok and data then
			-- ASSUMPTION: BaseService missing -> record the win directly in the
			-- documented data field so the gate can still be opened.
			data.base.barriers[tostring(s.area)] = true
			dataService:MarkDirty(player)
		end
		barrierDefeated = true
	end

	return {
		sessionId = s.id,
		victory = victory,
		mode = s.mode,
		level = s.level,
		area = s.area,
		nextAreaName = nextAreaName(s.area),
		wavesCleared = if victory then s.waveCount else math.max(0, s.wave - 1),
		waveCount = s.waveCount,
		kills = s.kills,
		fightSeconds = math.floor(fightSeconds),
		collected = collected,
		paid = paid,
		boosted = paid > collected + 0.001,
		chest = chest,
		newBest = newBest,
		barrierDefeated = barrierDefeated,
	}
end

finish = function(s: Session, victory: boolean, reason: string)
	if s.state == "ended" then
		return
	end
	s.state = "ended"
	s.stateEnds = s.clock + CombatDefs.RESULT_TIMEOUT
	s.choice = nil
	-- clear the battlefield
	for id in s.projectiles do
		removeProjectile(s, id)
	end
	table.clear(s.grenades)
	table.clear(s.enemies)
	s.player:SetAttribute("CombatActive", false)
	restoreHumanoid(s)
	s.humanoid.JumpHeight = 0 -- still no jumping until you leave the arena
	s.humanoid.JumpPower = 0
	local result: { [string]: any } = if s.settled then { sessionId = s.id, victory = victory } else settle(s, victory)
	result.reason = reason
	flush(s)
	safeFire(s.player, "Combat", "Result", result)
end

-------------------------------------------------------------------------------
-- Session lifecycle
-------------------------------------------------------------------------------
-- Ends a session for good (any reason). Safe to call more than once.
local function endSession(s: Session, reason: string)
	if sessions[s.player] ~= s then
		return
	end
	sessions[s.player] = nil
	if s.started and not s.settled and s.state ~= "ended" then
		-- forced ending mid-fight (reset, death, left the game): counts as a defeat
		local ok, result = pcall(settle, s, false)
		if ok and s.player.Parent and type(result) == "table" and (result.paid or 0) > 0 then
			Net.Notify(s.player, "Fight ended: +" .. Format.Money(result.paid), "cash")
		end
	end
	s.state = "ended"
	table.clear(s.enemies)
	table.clear(s.projectiles)
	table.clear(s.pickups)
	table.clear(s.grenades)
	try(function()
		clearAttributes(s.player)
	end)
	try(function()
		restoreHumanoid(s)
	end)
	safeFire(s.player, "Combat", "Ended", { sessionId = s.id, reason = reason })
end

local function returnToBase(s: Session, reason: string)
	endSession(s, reason)
	local modeService = registry.ModeService
	if modeService and s.player.Parent then
		modeService:ReturnToBase(s.player)
	end
end

local function updatePlayerPosition(s: Session, dt: number): boolean
	local character = s.player.Character
	local root = getRoot(character)
	if not root or character ~= s.character then
		s.noRootTime += dt
		return false
	end
	s.noRootTime = 0
	local local3 = root.Position - s.center
	s.px, s.pz = local3.X, local3.Z
	local v = root.AssemblyLinearVelocity
	s.pSpeed = math.sqrt(v.X * v.X + v.Z * v.Z)
	-- Safety net: if the avatar somehow left the arena, put it back on the start line.
	if math.abs(s.px) > HALF_X + 8 or math.abs(s.pz) > HALF_Z + 8 or local3.Y < -25 then
		local start = s.center + Vector3.new(CombatDefs.PLAYER_START.x, 0, CombatDefs.PLAYER_START.z)
		local modeService = registry.ModeService
		if modeService then
			modeService:Teleport(s.player, CFrame.lookAt(start, start + Vector3.new(0, 0, -1)))
		end
		s.px, s.pz = CombatDefs.PLAYER_START.x, CombatDefs.PLAYER_START.z
	end
	return true
end

local function tick(s: Session, dt: number)
	s.clock += dt
	s.hitBudget = math.min(MAX_HITS_PER_SECOND, s.hitBudget + MAX_HITS_PER_SECOND * dt)
	if s.state == "lobby" then
		return
	end
	if not updatePlayerPosition(s, dt) then
		if s.noRootTime > NO_ROOT_TIMEOUT then
			returnToBase(s, "no character")
		end
		return
	end
	local stats = s.stats

	if s.state == "countdown" then
		if s.clock >= s.stateEnds then
			startWave(s, 1)
		end
	elseif s.state == "fighting" then
		if stats and stats.regen > 0 and s.hp > 0 then
			s.hp = math.min(stats.maxHp, s.hp + stats.maxHp * stats.regen * dt)
		end
		for _, e in s.enemies do
			updateEnemy(s, e, dt)
		end
		separateEnemies(s)
		playerFire(s, dt)
		updateProjectiles(s, dt)
		updateGrenades(s)
		collectPickups(s)
		sendVitals(s)
		if s.pendingDefeat then
			finish(s, false, "defeated")
		elseif next(s.enemies) == nil then
			waveCleared(s)
		end
	elseif s.state == "choosing" then
		updateProjectiles(s, dt) -- your own bullets finish flying
		collectPickups(s)
		if s.clock >= s.stateEnds then
			local choice = s.choice
			if choice and choice[1] then
				chooseAbility(s, choice[1], true)
			else
				goToBreak(s)
			end
		end
	elseif s.state == "break" then
		updateProjectiles(s, dt)
		collectPickups(s)
		if s.clock >= s.stateEnds then
			startWave(s, s.wave + 1)
		end
	elseif s.state == "ended" then
		if s.clock >= s.stateEnds then
			returnToBase(s, "timeout")
			return
		end
	end

	if sessions[s.player] ~= s then
		return
	end
	flush(s)
	if s.state ~= "ended" then
		s.snapTimer -= dt
		if s.snapTimer <= 0 then
			s.snapTimer += SNAP_INTERVAL
			sendSnapshot(s)
		end
	end
end

local function lockedStats(player: Player): CombatStats
	local ok, stats = callService("InventoryService", "GetLoadoutStats", player)
	if ok and type(stats) == "table" then
		return stats
	end
	local dataService = registry.DataService
	local data = if dataService then dataService:Get(player) else nil
	if data then
		local fromDefs, defsStats = pcall(EquipmentDefs.LoadoutStats, data)
		if fromDefs and type(defsStats) == "table" then
			return defsStats
		end
	end
	return CombatDefs.DefaultStats()
end

-------------------------------------------------------------------------------
-- Net handlers
-------------------------------------------------------------------------------
local function handlePrepare(player: Player, mode: any, param: any): Net.Result
	if mode ~= "arena" and mode ~= "barrier" then
		return Net.Fail("Unknown fight")
	end
	if sessions[player] then
		return Net.Fail("You are already in a fight")
	end
	local modeService = registry.ModeService
	local dataService = registry.DataService
	if not modeService or not dataService then
		return Net.Fail("Still loading, try again")
	end
	if modeService:GetMode(player) ~= "Base" then
		return Net.Fail("Finish what you are doing first")
	end
	local data = dataService:Get(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	local character = player.Character
	local humanoid = getHumanoid(character)
	if not character or not humanoid or humanoid.Health <= 0 or not getRoot(character) then
		return Net.Fail("Wait until you have respawned")
	end

	local level: number
	local area = data.base.highestArea
	if mode == "arena" then
		if not data.base.arenaUnlocked then
			return Net.Fail("Unlock the Combat Arena first")
		end
		if not isInteger(param) or param < 1 or param > data.combat.highestLevel + 1 then
			return Net.Fail("That level is locked")
		end
		level = param
	else
		if not isInteger(param) or param ~= data.base.highestArea then
			return Net.Fail("That barrier is not in front of you")
		end
		if param >= #Config.Areas then
			return Net.Fail("There is no area after this one")
		end
		if data.base.barriers[tostring(param)] then
			return Net.Fail("You already beat this barrier")
		end
		area = param
		level = Economy.BarrierCombatLevel(area)
	end

	local plan = CombatDefs.LevelPlan(level, mode)
	local slot = modeService:GetSlot(player)
	local center = Layout.ArenaCenter(slot)

	callService("VehicleService", "Dismount", player)
	modeService:SetMode(player, "Combat")

	sessionCounter += 1
	local seed = (player.UserId % 100000) * 7 + sessionCounter * 131 + math.floor(os.clock() * 1000)
	local s: Session = {
		id = ("%d_%d"):format(player.UserId, sessionCounter),
		player = player,
		character = character,
		humanoid = humanoid,
		mode = mode,
		level = level,
		area = area,
		plan = plan,
		slot = slot,
		center = center,
		state = "lobby",
		stateEnds = 0,
		clock = 0,
		started = false,
		fightStartsAt = 0,
		settled = false,
		base = nil,
		stats = nil,
		abilityStacks = {},
		hp = 0,
		shield = 0,
		wave = 0,
		waveCount = plan.waveCount,
		enemies = {},
		projectiles = {},
		grenades = {},
		pickups = {},
		nextEnemyId = 0,
		nextProjectileId = 0,
		nextPickupId = 0,
		nextTelegraphId = 0,
		fireCooldown = 0,
		cash = 0,
		kills = 0,
		choice = nil,
		rng = Random.new(seed),
		abilityRng = Rng.new(seed),
		px = CombatDefs.PLAYER_START.x,
		pz = CombatDefs.PLAYER_START.z,
		pSpeed = 0,
		noRootTime = 0,
		snapTimer = 0,
		outbox = newOutbox(),
		resolvedHits = {},
		hitBudget = MAX_HITS_PER_SECOND,
		pendingDefeat = false,
		lastHpSent = -1,
		lastShieldSent = -1,
		lastMaxSent = -1,
	}
	sessions[player] = s

	local start = center + Vector3.new(CombatDefs.PLAYER_START.x, 0, CombatDefs.PLAYER_START.z)
	if not modeService:Teleport(player, CFrame.lookAt(start, start + Vector3.new(0, 0, -1))) then
		returnToBase(s, "teleport failed")
		return Net.Fail("Could not reach the arena")
	end
	humanoid.JumpHeight = 0 -- jumping is disabled in combat
	humanoid.JumpPower = 0
	player:SetAttribute("CombatActive", false) -- the lobby is not "active": equipping works

	return Net.Ok({
		sessionId = s.id,
		mode = mode,
		level = level,
		area = area,
		areaName = (Config.Areas[area] and Config.Areas[area].name) or "",
		nextAreaName = nextAreaName(area),
		waveCount = plan.waveCount,
		reward = plan.reward,
		boss = plan.hasBoss,
		slot = slot,
		centerX = center.X,
		centerY = center.Y,
		centerZ = center.Z,
	})
end

local function handleStart(player: Player, sessionId: any): Net.Result
	local s = sessions[player]
	if not s or type(sessionId) ~= "string" or s.id ~= sessionId then
		return Net.Fail("No fight to start")
	end
	if s.state ~= "lobby" then
		-- double click: the fight is already running
		return Net.Ok({ already = true, stats = s.stats, hp = s.hp })
	end
	local modeService = registry.ModeService
	if not modeService or modeService:GetMode(player) ~= "Combat" then
		return Net.Fail("You are not in the arena")
	end
	if not s.humanoid.Parent or s.humanoid.Health <= 0 then
		return Net.Fail("Wait until you have respawned")
	end
	local base = lockedStats(player)
	s.base = base
	local stats = CombatDefs.DeriveStats(base, s.abilityStacks)
	s.stats = stats
	s.hp = stats.maxHp
	s.shield = 0
	s.started = true
	s.state = "countdown"
	s.stateEnds = s.clock + CombatDefs.COUNTDOWN
	s.fightStartsAt = s.stateEnds
	s.humanoid.WalkSpeed = stats.moveSpeed
	player:SetAttribute("CombatActive", true)
	sendVitals(s, true)
	announceWave(s, 1, CombatDefs.COUNTDOWN)
	return Net.Ok({
		stats = stats,
		hp = s.hp,
		maxHp = stats.maxHp,
		startsAt = serverNow() + CombatDefs.COUNTDOWN,
	})
end

local function handleChoose(player: Player, sessionId: any, abilityId: any): Net.Result
	local s = sessions[player]
	if not s or type(sessionId) ~= "string" or s.id ~= sessionId then
		return Net.Fail("No fight running")
	end
	if s.state ~= "choosing" or not s.choice then
		return Net.Fail("Nothing to choose right now")
	end
	if type(abilityId) ~= "string" or not table.find(s.choice, abilityId) then
		return Net.Fail("That ability is not on offer")
	end
	chooseAbility(s, abilityId, false)
	return Net.Ok({ id = abilityId, stacks = s.abilityStacks[abilityId] or 0 })
end

local function handleLeave(player: Player): Net.Result
	local s = sessions[player]
	if not s then
		-- stuck in Combat mode without a session (should not happen): go home
		local modeService = registry.ModeService
		if modeService and modeService:GetMode(player) == "Combat" then
			modeService:ReturnToBase(player)
		end
		return Net.Ok({ left = true })
	end
	if s.state == "lobby" or s.state == "ended" then
		returnToBase(s, "left")
		return Net.Ok({ left = true })
	end
	-- leaving mid-fight = retreat = defeat (you keep the cash you collected)
	finish(s, false, "retreat")
	return Net.Ok({ ended = true })
end

-- Client says an enemy bullet touched it. Validate and apply once.
local function handleHitReport(player: Player, msg: any, payload: any)
	if msg ~= "Hit" or type(payload) ~= "table" then
		return
	end
	local s = sessions[player]
	if not s or s.state ~= "fighting" then
		return
	end
	local id = payload.id
	if not isInteger(id) then
		return
	end
	if s.hitBudget < 1 then
		return
	end
	s.hitBudget -= 1
	if s.resolvedHits[id] then
		return
	end
	local p = s.projectiles[id]
	if not p or p.owner ~= "enemy" then
		return
	end
	-- the bullet's recent path must pass close to where the server sees the player
	local backX = p.x - p.vx * math.min(REPORT_LOOKBACK, p.age)
	local backZ = p.z - p.vz * math.min(REPORT_LOOKBACK, p.age)
	if segmentDistance(backX, backZ, p.x, p.z, s.px, s.pz) > CombatDefs.REPORT_TOLERANCE then
		return
	end
	s.resolvedHits[id] = true
	removeProjectile(s, id)
	hitPlayer(s, p.damage, "bullet", nil)
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function CombatService:Init(reg: { [string]: any })
	registry = reg
	Net.Handle("CombatPrepare", handlePrepare)
	Net.Handle("CombatStart", handleStart)
	Net.Handle("CombatChooseAbility", handleChoose)
	Net.Handle("CombatLeave", handleLeave)
end

function CombatService:Start()
	buildArenas()

	Net.On("Combat", function(player: Player, msg: any, payload: any)
		local ok, err = try(function()
			handleHitReport(player, msg, payload)
		end)
		if not ok then
			warn("[CombatService] hit report failed: " .. tostring(err))
		end
	end)

	-- Any mode change away from Combat (death, respawn, other systems) ends the session.
	local modeService = registry.ModeService
	if modeService and modeService.ModeChanged then
		modeService.ModeChanged:Connect(function(player: Player, newMode: string)
			local s = sessions[player]
			if s and newMode ~= "Combat" then
				endSession(s, "mode changed")
			end
		end)
	end
	-- Pay out before the final save when someone leaves mid-fight.
	local dataService = registry.DataService
	if dataService and dataService.PlayerRemoving then
		dataService.PlayerRemoving:Connect(function(player: Player)
			local s = sessions[player]
			if s then
				endSession(s, "left the game")
			end
		end)
	end
	Players.PlayerRemoving:Connect(function(player)
		local s = sessions[player]
		if s then
			endSession(s, "left the game")
		end
	end)

	-- Fixed 30 Hz simulation on Heartbeat (at most 3 steps per frame).
	local accumulator = 0
	RunService.Heartbeat:Connect(function(dt: number)
		accumulator = math.min(accumulator + dt, STEP * 3)
		while accumulator >= STEP do
			accumulator -= STEP
			for _, s in sessions do
				local ok, err = try(function()
					tick(s, STEP)
				end)
				if not ok then
					warn("[CombatService] fight crashed, sending player home: " .. tostring(err))
					try(function()
						returnToBase(s, "error")
					end)
				end
			end
		end
	end)
end

function CombatService:IsInFight(player: Player): boolean
	return sessions[player] ~= nil
end

return CombatService
