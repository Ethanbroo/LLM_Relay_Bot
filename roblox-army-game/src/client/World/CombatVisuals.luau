--!strict
--[[
	CombatVisuals (client)
	Draws one combat fight for the local player only: blocky enemy soldiers,
	glowing bullets, grenade warning circles, sniper lasers, spinning gold cash
	coins, death pops and floating damage numbers. Nothing here decides game
	results: the server (CombatService) simulates everything as data and this
	module just renders it.

	How the data arrives (see CombatService header):
	  * 20 Hz snapshots (enemy positions/HP/state, cash pickups) -> enemies are
	    drawn ~100 ms in the past and smoothly interpolated between snapshots.
	  * Projectile spawns { x, z, vx, vz, born } -> bullets move on the client by
	    themselves (position = start + velocity x (serverTime - born)).
	All positions are arena-local (x, z from the arena centre).

	API:
		local visuals = CombatVisuals.new(center: Vector3)
		visuals.ApplySnapshot(snap)          -- CombatDefs.Snap
		visuals.AddProjectiles(list)         -- "ProjectileBatch" entries (same id = replace)
		visuals.RemoveProjectiles(ids)
		visuals.Telegraph(payload)           -- "grenade" | "laser"
		visuals.EnemyDied(list)
		visuals.Damage(list)                 -- floating numbers ("enemy" | "player" | "dodge" | "shield" | "heal" | "boom")
		visuals.Collected(list)              -- "+$12.50" where a coin was picked up
		visuals.ClearCombat()                -- remove enemies, bullets, coins and warnings (end of the fight)
		visuals.Update(dt, rootPosition?) -> { number }
			-- call every frame; returns ids of ENEMY bullets touching the local
			-- player's HumanoidRootPart this frame (each id only once)
		visuals.Destroy()                    -- removes every part this module made
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local CombatDefs = require(Shared.Combat.CombatDefs)
local Config = require(Shared.Config)
local Format = require(Shared.Format)

local P = Config.Palette
local player = Players.LocalPlayer

type Snap = CombatDefs.Snap
type SnapEnemy = CombatDefs.SnapEnemy

local INTERP_DELAY = 0.1 -- draw enemies 100 ms in the past (smooth between snapshots)
local MAX_FLOATS_PER_BATCH = 10
local FLOATS_PER_SECOND = 18 -- floating numbers are parts + billboards: keep them affordable
local BULLET_POOL_MAX = 160
local HALF_X = CombatDefs.HALF_X
local HALF_Z = CombatDefs.HALF_Z
local FLOOR_Y = 0

export type Visuals = {
	ApplySnapshot: (snap: Snap) -> (),
	AddProjectiles: (list: { any }) -> (),
	RemoveProjectiles: (ids: { any }) -> (),
	Telegraph: (payload: { [string]: any }) -> (),
	EnemyDied: (list: { any }) -> (),
	Damage: (list: { any }) -> (),
	Collected: (list: { any }) -> (),
	ClearCombat: () -> (),
	Update: (dt: number, rootPosition: Vector3?) -> { number },
	Destroy: () -> (),
}

type PartSpec = { part: Part, offset: CFrame, color: Color3 }

type EnemyView = {
	id: number,
	type: string,
	model: Model,
	parts: { PartSpec },
	scale: number,
	hpFill: Frame,
	alert: TextLabel,
	board: BillboardGui,
	lastHp: number,
	flashUntil: number,
	flashing: boolean,
	yaw: number,
	x: number,
	z: number,
	state: string,
	spawnAlpha: number,
}

type ProjectileView = {
	id: number,
	part: Part,
	owner: string,
	x: number,
	z: number,
	vx: number,
	vz: number,
	born: number,
	life: number,
	reported: boolean,
}

type TelegraphView = {
	kind: string,
	parts: { Part },
	start: number,
	duration: number,
	x: number,
	z: number,
	radius: number,
	fromX: number,
	fromZ: number,
	disc: Part?,
	ball: Part?,
	beam: Part?,
}

type PickupView = { model: Model, rim: Part, face: Part, x: number, z: number, phase: number }

local CombatVisuals = {}

-------------------------------------------------------------------------------
-- Part helpers
-------------------------------------------------------------------------------
local function newPart(size: Vector3, color: Color3, parent: Instance?, material: Enum.Material?): Part
	local part = Instance.new("Part")
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.Material = material or Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Size = size
	part.Color = color
	part.Parent = parent
	return part
end

local function tween(instance: Instance, goals: { [string]: any }, duration: number, style: Enum.EasingStyle?)
	local t = TweenService:Create(
		instance,
		TweenInfo.new(duration, style or Enum.EasingStyle.Quad, Enum.EasingDirection.Out),
		goals
	)
	t:Play()
end

-------------------------------------------------------------------------------
-- Enemy soldier models (blocky, toy-like: red uniform, steel helmet, black gun)
-------------------------------------------------------------------------------
-- Each entry: name, size, offset from the feet centre (facing -Z), colour.
type Piece = { size: Vector3, offset: Vector3, color: Color3 }

local function soldierPieces(typeId: string): { Piece }
	local skin = P.CoinShine -- Lego-yellow skin, matches the toy look of the reference images
	local uniform = P.EnemyRed
	local dark = P.GatePost
	local gun = P.PillBlack
	local pieces: { Piece } = {
		{ size = Vector3.new(0.8, 1.6, 0.8), offset = Vector3.new(-0.45, 0.8, 0), color = dark }, -- left leg
		{ size = Vector3.new(0.8, 1.6, 0.8), offset = Vector3.new(0.45, 0.8, 0), color = dark }, -- right leg
		{ size = Vector3.new(2, 1.9, 1.1), offset = Vector3.new(0, 2.55, 0), color = uniform }, -- torso
		{ size = Vector3.new(2.05, 0.3, 1.15), offset = Vector3.new(0, 1.75, 0), color = dark }, -- belt
		{ size = Vector3.new(0.6, 1.7, 0.6), offset = Vector3.new(-1.3, 2.6, 0), color = uniform }, -- left arm
		{ size = Vector3.new(0.6, 1.7, 0.6), offset = Vector3.new(1.3, 2.6, -0.3), color = uniform }, -- right arm
		{ size = Vector3.new(1.2, 1.2, 1.2), offset = Vector3.new(0, 4.1, 0), color = skin }, -- head
	}
	if typeId == "Officer" then
		-- officer's peaked cap with a gold band
		table.insert(pieces, { size = Vector3.new(1.5, 0.45, 1.5), offset = Vector3.new(0, 4.85, 0), color = gun })
		table.insert(
			pieces,
			{ size = Vector3.new(1.55, 0.18, 1.55), offset = Vector3.new(0, 4.62, 0), color = P.Padlock }
		)
		table.insert(pieces, { size = Vector3.new(1.3, 0.12, 0.6), offset = Vector3.new(0, 4.55, -0.85), color = gun }) -- visor
		table.insert(
			pieces,
			{ size = Vector3.new(0.5, 0.3, 0.12), offset = Vector3.new(-0.5, 3.1, -0.58), color = P.Padlock }
		) -- medal
		table.insert(pieces, { size = Vector3.new(0.4, 0.4, 2.2), offset = Vector3.new(1.3, 2.3, -1.3), color = gun }) -- pistol
	else
		-- steel helmet
		table.insert(pieces, { size = Vector3.new(1.45, 0.6, 1.45), offset = Vector3.new(0, 4.75, 0), color = dark })
		if typeId == "Charger" then
			table.insert(
				pieces,
				{ size = Vector3.new(0.35, 0.35, 1.6), offset = Vector3.new(1.3, 2.0, -1.0), color = P.Wood }
			) -- baton
		elseif typeId == "Grenadier" then
			table.insert(
				pieces,
				{ size = Vector3.new(0.7, 0.7, 0.7), offset = Vector3.new(1.3, 1.8, -0.6), color = P.TroopGreenDark }
			) -- grenade
		elseif typeId == "Gunner" then
			table.insert(
				pieces,
				{ size = Vector3.new(0.7, 0.7, 2.6), offset = Vector3.new(0.9, 2.5, -1.4), color = gun }
			) -- big gun
			table.insert(
				pieces,
				{ size = Vector3.new(0.8, 0.8, 0.8), offset = Vector3.new(0.9, 2.0, -0.8), color = dark }
			) -- ammo box
		elseif typeId == "Sniper" then
			table.insert(
				pieces,
				{ size = Vector3.new(0.3, 0.3, 3.4), offset = Vector3.new(1.0, 2.8, -1.7), color = gun }
			) -- long rifle
			table.insert(
				pieces,
				{ size = Vector3.new(0.35, 0.35, 0.8), offset = Vector3.new(1.0, 3.15, -1.2), color = dark }
			) -- scope
		else
			table.insert(
				pieces,
				{ size = Vector3.new(0.4, 0.4, 2.2), offset = Vector3.new(1.0, 2.6, -1.2), color = gun }
			) -- rifle
		end
	end
	return pieces
end

-------------------------------------------------------------------------------
-- Constructor
-------------------------------------------------------------------------------
function CombatVisuals.new(center: Vector3): Visuals
	local folder = Instance.new("Folder")
	folder.Name = "CombatFx_" .. player.UserId
	folder.Parent = Workspace

	local enemies: { [number]: EnemyView } = {}
	local deadIds: { [number]: boolean } = {}
	local projectiles: { [number]: ProjectileView } = {}
	local telegraphs: { TelegraphView } = {}
	local pickups: { [number]: PickupView } = {}
	local snaps: { Snap } = {}
	local lastSnapT = -math.huge
	local bulletPool: { Part } = {}
	local destroyed = false
	local floatBudget = FLOATS_PER_SECOND

	-- true if we may show one more floating number right now
	local function takeFloat(important: boolean): boolean
		if important then
			return true
		end
		if floatBudget >= 1 then
			floatBudget -= 1
			return true
		end
		return false
	end

	local function world(x: number, z: number, y: number?): Vector3
		return Vector3.new(center.X + x, center.Y + FLOOR_Y + (y or 0), center.Z + z)
	end

	---------------------------------------------------------------------------
	-- Enemies
	---------------------------------------------------------------------------
	local function buildEnemy(id: number, typeId: string, maxHp: number): EnemyView
		local def = CombatDefs.ENEMIES[typeId]
		local scale = if def then def.scale else 1
		local model = Instance.new("Model")
		model.Name = "Enemy_" .. typeId .. "_" .. id
		local parts: { PartSpec } = {}
		local anchor: Part? = nil
		for _, piece in soldierPieces(typeId) do
			local part = newPart(piece.size * scale, piece.color, model)
			part.Name = "Piece"
			table.insert(parts, { part = part, offset = CFrame.new(piece.offset * scale), color = piece.color })
			if not anchor then
				anchor = part
			end
		end
		-- HP bar + "!" warning above the head
		local head = parts[7].part
		local board = Theme.Billboard({
			Name = "EnemyBar",
			Adornee = head,
			Parent = model,
			Size = if typeId == "Officer" then UDim2.fromOffset(150, 34) else UDim2.fromOffset(70, 26),
			StudsOffset = Vector3.new(0, 2.2 * scale, 0),
			AlwaysOnTop = true,
			MaxDistance = 220,
		})
		local back = Instance.new("Frame")
		back.Name = "Back"
		back.BackgroundColor3 = P.PillBlack
		back.AnchorPoint = Vector2.new(0.5, 1)
		back.Position = UDim2.fromScale(0.5, 1)
		back.Size = UDim2.new(1, 0, 0, if typeId == "Officer" then 14 else 9)
		back.Parent = board
		Theme.Corner(back, 0.5)
		Theme.Stroke(back, P.CardOutline, 1.5, true)
		local fill = Instance.new("Frame")
		fill.Name = "Fill"
		fill.BackgroundColor3 = P.ButtonRed
		fill.Size = UDim2.fromScale(1, 1)
		fill.Parent = back
		Theme.Corner(fill, 0.5)
		local alert = Theme.Label({
			Name = "Alert",
			Text = "!",
			TextColor3 = P.PriceGold,
			MaxTextSize = 26,
			Size = UDim2.new(1, 0, 1, if typeId == "Officer" then -16 else -10),
			Visible = false,
			Parent = board,
		})
		if typeId == "Officer" then
			alert.Text = "OFFICER"
			alert.TextColor3 = P.White
			alert.Visible = true
		end
		model.Parent = folder
		local view: EnemyView = {
			id = id,
			type = typeId,
			model = model,
			parts = parts,
			scale = scale,
			hpFill = fill,
			alert = alert,
			board = board,
			lastHp = maxHp,
			flashUntil = 0,
			flashing = false,
			yaw = 0,
			x = 0,
			z = 0,
			state = "spawn",
			spawnAlpha = 0,
		}
		return view
	end

	local function setEnemyColor(view: EnemyView, white: boolean)
		for _, spec in view.parts do
			spec.part.Color = if white then P.White else spec.color
		end
		view.flashing = white
	end

	local function setEnemyTransparency(view: EnemyView, transparency: number)
		for _, spec in view.parts do
			spec.part.Transparency = transparency
		end
	end

	local function placeEnemy(view: EnemyView, x: number, z: number, targetX: number, targetZ: number)
		local dx, dz = targetX - x, targetZ - z
		if dx * dx + dz * dz > 0.01 then
			-- Roblox "forward" is -Z: yaw so the soldier faces the target
			view.yaw = math.atan2(-dx, -dz)
		end
		local crouch = if view.state == "charge" then -0.35 * view.scale else 0
		local root = CFrame.new(world(x, z, crouch)) * CFrame.Angles(0, view.yaw, 0)
		local partList = table.create(#view.parts)
		local cframes = table.create(#view.parts)
		for i, spec in view.parts do
			partList[i] = spec.part
			cframes[i] = root * spec.offset
		end
		Workspace:BulkMoveTo(partList, cframes, Enum.BulkMoveMode.FireCFrameChanged)
		view.x, view.z = x, z
	end

	local function removeEnemy(id: number)
		local view = enemies[id]
		if view then
			enemies[id] = nil
			view.model:Destroy()
		end
	end

	-- Death pop: the soldier shrinks away and a few red blocks fly out.
	local function deathPop(view: EnemyView)
		local position = world(view.x, view.z, 2.5 * view.scale)
		for i = 1, 6 do
			local angle = (i / 6) * math.pi * 2
			local cube = newPart(Vector3.new(0.8, 0.8, 0.8), if i % 2 == 0 then P.EnemyRed else P.GatePost, folder)
			cube.CFrame = CFrame.new(position)
			local target = position + Vector3.new(math.cos(angle) * 4, 2 + (i % 3), math.sin(angle) * 4)
			tween(cube, { Position = target, Transparency = 1, Size = Vector3.new(0.2, 0.2, 0.2) }, 0.45)
			task.delay(0.5, function()
				cube:Destroy()
			end)
		end
		view.board.Enabled = false
		for _, spec in view.parts do
			tween(spec.part, { Size = spec.part.Size * 0.1, Transparency = 1 }, 0.25)
		end
		local model = view.model
		task.delay(0.3, function()
			model:Destroy()
		end)
	end

	---------------------------------------------------------------------------
	-- Pickups (spinning gold coins)
	---------------------------------------------------------------------------
	local function buildPickup(x: number, z: number, value: number): PickupView
		local model = Instance.new("Model")
		model.Name = "Cash"
		local size = if value >= 1000 then 2.4 elseif value >= 100 then 2.0 else 1.7
		local rim = newPart(Vector3.new(0.35, size, size), P.CoinDark, model)
		rim.Shape = Enum.PartType.Cylinder
		local face = newPart(Vector3.new(0.42, size * 0.75, size * 0.75), P.Coin, model)
		face.Shape = Enum.PartType.Cylinder
		model.Parent = folder
		return { model = model, rim = rim, face = face, x = x, z = z, phase = math.random() * math.pi * 2 }
	end

	---------------------------------------------------------------------------
	-- Bullets (pooled Neon balls)
	---------------------------------------------------------------------------
	local function takeBullet(): Part
		local part = table.remove(bulletPool)
		if part then
			part.Transparency = 0
			return part
		end
		local created = newPart(Vector3.new(1, 1, 1), P.CardOrange, folder, Enum.Material.Neon)
		created.Name = "Bullet"
		created.Shape = Enum.PartType.Ball
		created.CastShadow = false
		return created
	end

	local function releaseBullet(part: Part)
		if #bulletPool < BULLET_POOL_MAX then
			part.Transparency = 1
			part.CFrame = CFrame.new(center + Vector3.new(0, -50, 0))
			table.insert(bulletPool, part)
		else
			part:Destroy()
		end
	end

	local function removeProjectile(id: number)
		local view = projectiles[id]
		if view then
			projectiles[id] = nil
			releaseBullet(view.part)
		end
	end

	---------------------------------------------------------------------------
	-- Public functions
	---------------------------------------------------------------------------
	local visuals = {} :: Visuals

	function visuals.ApplySnapshot(snap: Snap)
		if destroyed or snap.t <= lastSnapT then
			return -- old / out-of-order snapshot
		end
		lastSnapT = snap.t
		table.insert(snaps, snap)
		while #snaps > 12 do
			table.remove(snaps, 1)
		end
		-- create views for new enemies, remove ones the server no longer has
		local present: { [number]: boolean } = {}
		for _, e in snap.enemies do
			present[e.id] = true
			if not enemies[e.id] and not deadIds[e.id] then
				local view = buildEnemy(e.id, e.type, e.maxHp)
				enemies[e.id] = view
				view.x, view.z = e.x, e.z
				placeEnemy(view, e.x, e.z, e.x, e.z + 1)
				setEnemyTransparency(view, 0.7)
			end
		end
		for id, view in enemies do
			if not present[id] then
				-- enemies only disappear when they die (the death event may arrive later)
				enemies[id] = nil
				deadIds[id] = true
				deathPop(view)
			end
		end
		-- pickups: drawn exactly where the latest snapshot says
		local pickupPresent: { [number]: boolean } = {}
		for _, p in snap.pickups do
			pickupPresent[p.id] = true
			if not pickups[p.id] then
				pickups[p.id] = buildPickup(p.x, p.z, p.value)
			end
		end
		for id, view in pickups do
			if not pickupPresent[id] then
				pickups[id] = nil
				view.model:Destroy()
			end
		end
	end

	function visuals.AddProjectiles(list: { any })
		if destroyed or type(list) ~= "table" then
			return
		end
		for _, entry in list do
			if type(entry) == "table" and type(entry.id) == "number" then
				local existing = projectiles[entry.id]
				local part = if existing then existing.part else takeBullet()
				local owner = if entry.ownerKind == "player" then "player" else "enemy"
				if owner == "player" then
					part.Color = P.Coin
					part.Size = Vector3.new(0.8, 0.8, 0.8)
				elseif entry.kind == "sniper" then
					part.Color = P.GateNegative
					part.Size = Vector3.new(1, 1, 1)
				else
					part.Color = P.CardOrangeDark
					part.Size = Vector3.new(1.2, 1.2, 1.2)
				end
				projectiles[entry.id] = {
					id = entry.id,
					part = part,
					owner = owner,
					x = tonumber(entry.x) or 0,
					z = tonumber(entry.z) or 0,
					vx = tonumber(entry.vx) or 0,
					vz = tonumber(entry.vz) or 0,
					born = tonumber(entry.born) or Workspace:GetServerTimeNow(),
					life = tonumber(entry.life) or 5,
					reported = if existing then existing.reported else false,
				}
			end
		end
	end

	function visuals.RemoveProjectiles(ids: { any })
		if type(ids) ~= "table" then
			return
		end
		for _, id in ids do
			if type(id) == "number" then
				removeProjectile(id)
			end
		end
	end

	function visuals.Telegraph(payload: { [string]: any })
		if destroyed then
			return
		end
		local kind = payload.kind
		local duration = math.max(0.1, tonumber(payload.duration) or 0.9)
		local start = tonumber(payload.start) or Workspace:GetServerTimeNow()
		local x = tonumber(payload.x) or 0
		local z = tonumber(payload.z) or 0
		if kind == "grenade" then
			local radius = tonumber(payload.radius) or 5
			-- faint full-size circle (where it will hit) + a growing solid disc
			local outline = newPart(Vector3.new(0.12, radius * 2, radius * 2), P.GateNegative, folder)
			outline.Shape = Enum.PartType.Cylinder
			outline.Transparency = 0.75
			outline.CFrame = CFrame.new(world(x, z, 0.08)) * CFrame.Angles(0, 0, math.rad(90))
			local disc = newPart(Vector3.new(0.14, 0.2, 0.2), P.GateNegative, folder)
			disc.Shape = Enum.PartType.Cylinder
			disc.Transparency = 0.45
			disc.CFrame = CFrame.new(world(x, z, 0.1)) * CFrame.Angles(0, 0, math.rad(90))
			local ball = newPart(Vector3.new(0.9, 0.9, 0.9), P.TroopGreenDark, folder)
			ball.Shape = Enum.PartType.Ball
			table.insert(telegraphs, {
				kind = "grenade",
				parts = { outline, disc, ball },
				start = start,
				duration = duration,
				x = x,
				z = z,
				radius = radius,
				fromX = tonumber(payload.fromX) or x,
				fromZ = tonumber(payload.fromZ) or z,
				disc = disc,
				ball = ball,
				beam = nil,
			})
		elseif kind == "laser" then
			local dx = tonumber(payload.dx) or 0
			local dz = tonumber(payload.dz) or 1
			local length = 120
			local from = world(x, z, 2.8)
			local to = from + Vector3.new(dx, 0, dz) * length
			local beam = newPart(Vector3.new(0.18, 0.18, length), P.GateNegative, folder, Enum.Material.Neon)
			beam.CastShadow = false
			beam.CFrame = CFrame.lookAt((from + to) / 2, to)
			beam.Transparency = 0.35
			table.insert(telegraphs, {
				kind = "laser",
				parts = { beam },
				start = start,
				duration = duration,
				x = x,
				z = z,
				radius = 0,
				fromX = x,
				fromZ = z,
				disc = nil,
				ball = nil,
				beam = beam,
			})
		end
	end

	local function boom(x: number, z: number, radius: number)
		local sphere = newPart(Vector3.new(1, 1, 1), P.CardOrange, folder, Enum.Material.Neon)
		sphere.Shape = Enum.PartType.Ball
		sphere.CastShadow = false
		sphere.Transparency = 0.2
		sphere.CFrame = CFrame.new(world(x, z, 1))
		tween(sphere, { Size = Vector3.new(radius * 2, radius * 2, radius * 2), Transparency = 1 }, 0.35)
		task.delay(0.4, function()
			sphere:Destroy()
		end)
	end

	function visuals.EnemyDied(list: { any })
		if destroyed or type(list) ~= "table" then
			return
		end
		for _, entry in list do
			if type(entry) == "table" and type(entry.id) == "number" then
				deadIds[entry.id] = true
				local view = enemies[entry.id]
				if view then
					enemies[entry.id] = nil
					if type(entry.x) == "number" and type(entry.z) == "number" then
						view.x, view.z = entry.x, entry.z
					end
					deathPop(view)
				end
			end
		end
	end

	function visuals.Damage(list: { any })
		if destroyed or type(list) ~= "table" then
			return
		end
		local shown = 0
		for _, entry in list do
			local important = type(entry) == "table" and entry.kind ~= "enemy" and entry.kind ~= "heal"
			if type(entry) == "table" and shown < MAX_FLOATS_PER_BATCH and takeFloat(important) then
				local x = tonumber(entry.x) or 0
				local z = tonumber(entry.z) or 0
				local amount = tonumber(entry.amount) or 0
				local kind = entry.kind
				local position = world(x + (math.random() - 0.5) * 1.5, z, 6)
				shown += 1
				if kind == "enemy" then
					local text = Format.Short(math.max(1, math.floor(amount + 0.5)))
					if entry.crit then
						Theme.FloatText(position, text .. "!", P.PriceGold)
					else
						Theme.FloatText(position, text, P.White)
					end
				elseif kind == "player" then
					Theme.FloatText(position, "-" .. Format.Short(math.max(1, math.floor(amount + 0.5))), P.ButtonRed)
				elseif kind == "dodge" then
					Theme.FloatText(position, "DODGE", P.White)
				elseif kind == "shield" then
					Theme.FloatText(position, "BLOCKED", P.CardBlue)
				elseif kind == "heal" then
					Theme.FloatText(position, "+" .. Format.Short(math.max(1, math.floor(amount + 0.5))), P.MoneyGreen)
				elseif kind == "boom" then
					shown -= 1
					boom(x, z, CombatDefs.EXPLOSIVE_RADIUS)
				end
			end
		end
	end

	function visuals.Collected(list: { any })
		if destroyed or type(list) ~= "table" then
			return
		end
		local shown = 0
		for _, entry in list do
			if type(entry) == "table" and type(entry.id) == "number" then
				local view = pickups[entry.id]
				if view then
					pickups[entry.id] = nil
					view.model:Destroy()
				end
				if shown < MAX_FLOATS_PER_BATCH and takeFloat(false) then
					shown += 1
					local value = tonumber(entry.value) or 0
					Theme.FloatText(
						world(tonumber(entry.x) or 0, tonumber(entry.z) or 0, 3),
						"+" .. Format.Money(value),
						P.MoneyGreen
					)
				end
			end
		end
	end

	function visuals.ClearCombat()
		for id in enemies do
			removeEnemy(id)
		end
		for id in projectiles do
			removeProjectile(id)
		end
		for _, t in telegraphs do
			for _, part in t.parts do
				part:Destroy()
			end
		end
		table.clear(telegraphs)
		table.clear(snaps)
		for _, view in pickups do
			view.model:Destroy()
		end
		table.clear(pickups)
	end

	-- Enemy position at render time (interpolated between two snapshots).
	local function sampleEnemies(renderT: number): { [number]: { x: number, z: number, e: SnapEnemy } }
		local out: { [number]: { x: number, z: number, e: SnapEnemy } } = {}
		local count = #snaps
		if count == 0 then
			return out
		end
		-- find snapshots a (<= renderT) and b (> renderT)
		local a: Snap = snaps[1]
		local b: Snap? = nil
		for i = 1, count do
			if snaps[i].t <= renderT then
				a = snaps[i]
				b = snaps[i + 1]
			end
		end
		if renderT < snaps[1].t then
			a, b = snaps[1], nil
		end
		local latest = snaps[count]
		local alpha = 0
		if b then
			alpha = math.clamp((renderT - a.t) / math.max(1e-3, b.t - a.t), 0, 1)
		end
		local fromA: { [number]: SnapEnemy } = {}
		for _, e in a.enemies do
			fromA[e.id] = e
		end
		local fromB: { [number]: SnapEnemy } = {}
		if b then
			for _, e in b.enemies do
				fromB[e.id] = e
			end
		end
		for _, e in latest.enemies do
			local ea = fromA[e.id]
			local eb = fromB[e.id]
			if ea and eb then
				out[e.id] = { x = ea.x + (eb.x - ea.x) * alpha, z = ea.z + (eb.z - ea.z) * alpha, e = eb }
			elseif ea then
				out[e.id] = { x = ea.x, z = ea.z, e = e }
			elseif eb then
				out[e.id] = { x = eb.x, z = eb.z, e = e }
			else
				out[e.id] = { x = e.x, z = e.z, e = e }
			end
		end
		return out
	end

	function visuals.Update(dt: number, rootPosition: Vector3?): { number }
		local hits: { number } = {}
		if destroyed then
			return hits
		end
		local now = Workspace:GetServerTimeNow()
		local clock = os.clock()
		floatBudget = math.min(FLOATS_PER_SECOND, floatBudget + dt * FLOATS_PER_SECOND)
		local localRoot: Vector3? = nil
		if rootPosition then
			localRoot = rootPosition - center
		end
		local targetX = if localRoot then localRoot.X else 0
		local targetZ = if localRoot then localRoot.Z else HALF_Z

		-- enemies
		local sampled = sampleEnemies(now - INTERP_DELAY)
		for id, view in enemies do
			local s = sampled[id]
			if s then
				local e = s.e
				view.state = e.state
				-- hit flash when HP dropped
				if e.hp < view.lastHp - 0.01 then
					view.flashUntil = clock + 0.09
				end
				view.lastHp = e.hp
				local fraction = if e.maxHp > 0 then math.clamp(e.hp / e.maxHp, 0, 1) else 0
				view.hpFill.Size = UDim2.fromScale(fraction, 1)
				-- warning "!" while aiming / charging / throwing / lasering
				if view.type ~= "Officer" then
					view.alert.Visible = e.state == "aim"
						or e.state == "charge"
						or e.state == "throw"
						or e.state == "laser"
				else
					view.alert.TextColor3 = if e.state == "aim" then P.PriceGold else P.White
				end
				-- spawn fade-in
				if e.state == "spawn" then
					view.spawnAlpha = math.min(1, view.spawnAlpha + dt * 1.5)
					setEnemyTransparency(view, 0.7 - 0.5 * view.spawnAlpha)
				elseif view.spawnAlpha < 2 then
					view.spawnAlpha = 2
					setEnemyTransparency(view, 0)
				end
				local wantWhite = clock < view.flashUntil
				if wantWhite ~= view.flashing then
					setEnemyColor(view, wantWhite)
				end
				if e.state == "dash" then
					placeEnemy(view, s.x, s.z, s.x + (s.x - view.x) * 10, s.z + (s.z - view.z) * 10)
				else
					placeEnemy(view, s.x, s.z, targetX, targetZ)
				end
			end
		end

		-- projectiles
		for id, view in projectiles do
			local age = now - view.born
			local x = view.x + view.vx * age
			local z = view.z + view.vz * age
			if
				age > view.life
				or math.abs(x) > HALF_X + 1
				or math.abs(z) > HALF_Z + 1
				or CombatDefs.CoverAt(x, z, 0)
			then
				removeProjectile(id)
			else
				view.part.CFrame = CFrame.new(world(x, z, 2.6))
				if view.owner == "enemy" and localRoot and not view.reported then
					local dx, dz = x - localRoot.X, z - localRoot.Z
					if dx * dx + dz * dz <= CombatDefs.CLIENT_HIT_RADIUS * CombatDefs.CLIENT_HIT_RADIUS then
						view.reported = true
						table.insert(hits, id)
						removeProjectile(id) -- hide it right away; the server confirms
					end
				end
			end
		end

		-- telegraphs
		for i = #telegraphs, 1, -1 do
			local t = telegraphs[i]
			local progress = (now - t.start) / t.duration
			if progress >= 1 then
				if t.kind == "grenade" then
					boom(t.x, t.z, t.radius)
				end
				for _, part in t.parts do
					part:Destroy()
				end
				table.remove(telegraphs, i)
			elseif t.kind == "grenade" then
				local alpha = math.clamp(progress, 0, 1)
				local disc = t.disc
				if disc then
					local d = math.max(0.2, t.radius * 2 * alpha)
					disc.Size = Vector3.new(0.14, d, d)
					disc.CFrame = CFrame.new(world(t.x, t.z, 0.1)) * CFrame.Angles(0, 0, math.rad(90))
				end
				local ball = t.ball
				if ball then
					-- lobbed arc from the thrower to the target
					local bx = t.fromX + (t.x - t.fromX) * alpha
					local bz = t.fromZ + (t.z - t.fromZ) * alpha
					local height = 3 + 10 * 4 * alpha * (1 - alpha)
					ball.CFrame = CFrame.new(world(bx, bz, height))
				end
			elseif t.kind == "laser" then
				local beam = t.beam
				if beam then
					-- flicker faster as the shot gets close
					local blink = math.sin(progress * progress * 40) > 0
					beam.Transparency = if blink then 0.15 else 0.6
				end
			end
		end

		-- coins spin and bob
		for _, view in pickups do
			view.phase += dt * 3
			local y = 1.6 + math.sin(view.phase) * 0.25
			local cf = CFrame.new(world(view.x, view.z, y)) * CFrame.Angles(0, view.phase, 0)
			view.rim.CFrame = cf
			view.face.CFrame = cf
		end
		return hits
	end

	function visuals.Destroy()
		if destroyed then
			return
		end
		destroyed = true
		visuals.ClearCombat()
		for _, part in bulletPool do
			part:Destroy()
		end
		table.clear(bulletPool)
		folder:Destroy()
	end

	return visuals
end

return CombatVisuals
