--!strict
--[[
	BaseCustomers (client)
	A few ambient NPC customers (bright single-colour blocky people, like the
	pink / purple / yellow walkers in reference image 1) strolling along the
	walkways of the areas the local player has unlocked. Purely decorative:
	client-only, no collisions, no Humanoids, capped at 8.

	Public API:
		BaseCustomers.Start(parent: Instance)       -- create the update loop
		BaseCustomers.SetAreas(highestArea: number) -- how many areas they may walk in
		BaseCustomers.Stop()                        -- remove every NPC and the loop

	How they walk: every NPC has a fixed sideways offset from the walkway
	centre line and walks centre -> end of one arm -> pause -> back to the
	centre -> another arm, so they always stay on the walkway.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)

local BuildingFactory = require(script.Parent.BuildingFactory)

local P = Config.Palette

local BaseCustomers = {}

local MAX_NPCS = 8 -- [PROPOSAL]
local WALK_SPEED = 7 -- studs per second [PROPOSAL]
local UPDATE_RATE = 1 / 30
local OUTFITS = { P.Pink, P.OfferPurple, P.WallYellow, P.CardBlue, P.ButtonGreen, P.CardOrange, P.CardPink }
local ARMS = { Vector3.new(0, 0, -1), Vector3.new(0, 0, 1), Vector3.new(1, 0, 0), Vector3.new(-1, 0, 0) }

type Npc = {
	model: Model,
	torso: BasePart,
	head: BasePart,
	hat: BasePart,
	legs: { BasePart },
	arms: { BasePart },
	area: number,
	lateral: number,
	from: Vector3,
	to: Vector3,
	walked: number,
	pause: number,
	atCentre: boolean,
	phase: number,
}

local rng = Random.new()
local folder: Folder? = nil
local connection: RBXScriptConnection? = nil
local npcs: { Npc } = {}
local highest = 1
local accumulator = 0

local function centrePoint(npc: Npc): Vector3
	local c = Layout.AreaCenter(npc.area)
	return Vector3.new(c.X + npc.lateral, Layout.FLOOR_Y, c.Z + npc.lateral)
end

local function armPoint(npc: Npc): Vector3
	local c = Layout.AreaCenter(npc.area)
	local dir = ARMS[rng:NextInteger(1, #ARMS)]
	local distance = rng:NextNumber(24, 58)
	if dir.X ~= 0 then
		return Vector3.new(c.X + dir.X * distance, Layout.FLOOR_Y, c.Z + npc.lateral)
	end
	return Vector3.new(c.X + npc.lateral, Layout.FLOOR_Y, c.Z + dir.Z * distance)
end

local function makeNpc(parent: Instance, index: number): Npc
	local model = Instance.new("Model")
	model.Name = "Customer" .. index
	local color = OUTFITS[(index - 1) % #OUTFITS + 1]
	local function part(name: string, size: Vector3, partColor: Color3): BasePart
		return BuildingFactory.Part(model, {
			Name = name,
			Size = size,
			CFrame = CFrame.new(),
			Color = partColor,
			CanCollide = false,
			CastShadow = true,
		})
	end
	local npc: Npc = {
		model = model,
		torso = part("Torso", Vector3.new(2, 2, 1), color),
		head = part("Head", Vector3.new(1.5, 1.5, 1.5), P.CoinShine),
		hat = part("Hat", Vector3.new(1.6, 0.4, 1.6), color),
		legs = { part("Leg", Vector3.new(0.95, 2, 1), color), part("Leg", Vector3.new(0.95, 2, 1), color) },
		arms = { part("Arm", Vector3.new(0.9, 2, 0.9), color), part("Arm", Vector3.new(0.9, 2, 0.9), color) },
		area = rng:NextInteger(1, math.max(1, highest)),
		lateral = (if rng:NextNumber() < 0.5 then -1 else 1) * rng:NextNumber(4, 9),
		from = Vector3.zero,
		to = Vector3.zero,
		walked = 0,
		pause = rng:NextNumber(0, 2),
		atCentre = true,
		phase = rng:NextNumber(0, math.pi * 2),
	}
	for _, p in model:GetChildren() do
		if p:IsA("BasePart") then
			p.CanQuery = false
		end
	end
	local start = centrePoint(npc)
	npc.from = start
	npc.to = armPoint(npc)
	model.Parent = parent
	return npc
end

-- Place every body part for one NPC (walking pose).
local function pose(npc: Npc, position: Vector3, direction: Vector3, walking: boolean)
	local flat = Vector3.new(direction.X, 0, direction.Z)
	local look = if flat.Magnitude > 1e-3 then flat.Unit else Vector3.new(0, 0, -1)
	local body = CFrame.lookAt(position, position + look)
	local t = os.clock() * 9 + npc.phase
	local swing = if walking then math.sin(t) * 0.6 else 0
	local bob = if walking then math.abs(math.sin(t)) * 0.15 else 0
	npc.torso.CFrame = body * CFrame.new(0, 3 + bob, 0)
	npc.head.CFrame = body * CFrame.new(0, 4.75 + bob, 0)
	npc.hat.CFrame = body * CFrame.new(0, 5.65 + bob, 0)
	npc.legs[1].CFrame = body * CFrame.new(-0.5, 2, 0) * CFrame.Angles(swing, 0, 0) * CFrame.new(0, -1, 0)
	npc.legs[2].CFrame = body * CFrame.new(0.5, 2, 0) * CFrame.Angles(-swing, 0, 0) * CFrame.new(0, -1, 0)
	npc.arms[1].CFrame = body * CFrame.new(-1.45, 3.9 + bob, 0) * CFrame.Angles(-swing, 0, 0) * CFrame.new(0, -0.9, 0)
	npc.arms[2].CFrame = body * CFrame.new(1.45, 3.9 + bob, 0) * CFrame.Angles(swing, 0, 0) * CFrame.new(0, -0.9, 0)
end

local function step(npc: Npc, dt: number)
	if npc.pause > 0 then
		npc.pause -= dt
		pose(npc, npc.from, npc.to - npc.from, false)
		return
	end
	local delta = npc.to - npc.from
	local length = delta.Magnitude
	npc.walked += WALK_SPEED * dt
	if length < 0.01 or npc.walked >= length then
		-- Arrived: pause, then head for the next point.
		npc.from = npc.to
		npc.walked = 0
		npc.atCentre = not npc.atCentre
		if npc.atCentre then
			npc.to = armPoint(npc)
			npc.pause = rng:NextNumber(0.2, 1)
		else
			npc.to = centrePoint(npc)
			npc.pause = rng:NextNumber(1, 3)
		end
		pose(npc, npc.from, delta, false)
		return
	end
	pose(npc, npc.from + delta.Unit * npc.walked, delta, true)
end

local function desiredCount(): number
	return math.min(MAX_NPCS, 2 + highest * 2)
end

local function refill()
	local parent = folder
	if not parent then
		return
	end
	while #npcs < desiredCount() do
		table.insert(npcs, makeNpc(parent, #npcs + 1))
	end
end

function BaseCustomers.Start(parent: Instance)
	if folder then
		return
	end
	local f = Instance.new("Folder")
	f.Name = "Customers"
	f.Parent = parent
	folder = f
	refill()
	connection = RunService.Heartbeat:Connect(function(dt: number)
		accumulator += dt
		if accumulator < UPDATE_RATE then
			return
		end
		local elapsed = math.min(accumulator, 0.25)
		accumulator = 0
		for _, npc in npcs do
			step(npc, elapsed)
		end
	end)
end

function BaseCustomers.SetAreas(highestArea: number)
	local value = math.clamp(math.floor(highestArea), 1, #Config.Areas)
	if value == highest then
		return
	end
	highest = value
	-- Spread some walkers into the newly unlocked area.
	for i, npc in npcs do
		if i % 2 == 0 then
			npc.area = rng:NextInteger(1, highest)
			npc.from = centrePoint(npc)
			npc.to = armPoint(npc)
			npc.walked = 0
			npc.atCentre = true
		end
	end
	refill()
end

function BaseCustomers.Stop()
	if connection then
		connection:Disconnect()
		connection = nil
	end
	if folder then
		folder:Destroy()
		folder = nil
	end
	table.clear(npcs)
end

return BaseCustomers
