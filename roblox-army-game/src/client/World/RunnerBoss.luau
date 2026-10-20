--!strict
--[[
	RunnerBoss (client)
	The giant boss that walks out of the castle at the end of an army run
	(owner request: "a boss at final stage of run that comes out of castle
	and does damage to units ... health bar on top of its head").
	Built from plain Parts (no meshes / asset ids): a red brute about 3.5x a
	soldier, horned helmet, angry face and a huge cannon on its right arm.
	All parts are Anchored and moved with workspace:BulkMoveTo every frame.
	It is visual only: RunnerVisuals drives it from RunnerSim's BossOutcome.

	Public API:
		local boss = RunnerBoss.new(parent, pivot: CFrame, maxHp)
			pivot = its feet, LookVector = where it faces
		boss:WalkTo(pivot, duration)   walk (swinging legs) to a new pivot
		boss:Attack() -> Vector3       cannon recoil; returns the muzzle position
		boss:Hit()                     short white flash (throttled)
		boss:SetHp(hp)                 health bar: fill green -> red + "1,234 / 5,000"
		boss:Die()                     topples backwards and fades (victory)
		boss:Roar()                    raises its arms (it won)
		boss:Update(dt)                call every frame
		boss:AimPoints() -> { Vector3 } points on its body for bullets to hit
		boss:Chest() -> Vector3
		boss:Destroy()
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Format = require(Shared.Format)

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)
local RunnerModels = require(script.Parent.RunnerModels)

local P = Config.Palette

export type Boss = {
	WalkTo: (self: Boss, pivot: CFrame, duration: number) -> (),
	Attack: (self: Boss) -> Vector3,
	Hit: (self: Boss) -> (),
	SetHp: (self: Boss, hp: number) -> (),
	Die: (self: Boss) -> (),
	Roar: (self: Boss) -> (),
	Update: (self: Boss, dt: number) -> (),
	AimPoints: (self: Boss) -> { Vector3 },
	Chest: (self: Boss) -> Vector3,
	Destroy: (self: Boss) -> (),
}

-- Joints in boss space (feet at the origin, facing -Z). [PROPOSAL] sizes.
local JOINTS: { [string]: CFrame } = {
	body = CFrame.new(),
	head = CFrame.new(0, 7.7, 0),
	leftArm = CFrame.new(-3.0, 7.3, 0),
	rightArm = CFrame.new(3.0, 7.3, 0),
	leftLeg = CFrame.new(-1.15, 3.3, 0),
	rightLeg = CFrame.new(1.15, 3.3, 0),
}
local AIM_ANGLE = math.rad(78) -- the cannon arm is held forward
local MUZZLE = CFrame.new(0, -7.1, 0) -- in the right-arm joint frame

type PieceDef = {
	group: string,
	name: string,
	size: Vector3,
	offset: CFrame,
	color: Color3,
	shape: Enum.PartType?,
	material: Enum.Material?,
}

local function def(
	group: string,
	name: string,
	size: Vector3,
	offset: CFrame,
	color: Color3,
	shape: Enum.PartType?
): PieceDef
	return { group = group, name = name, size = size, offset = offset, color = color, shape = shape, material = nil }
end

local ALONG_ARM = CFrame.Angles(0, 0, math.rad(90)) -- cylinder axis X -> along the arm (Y)

local PIECES: { PieceDef } = {
	-- Legs (hip joints)
	def("leftLeg", "Leg", Vector3.new(1.7, 2.6, 1.8), CFrame.new(0, -1.3, 0), P.GatePost),
	def("leftLeg", "Boot", Vector3.new(1.9, 0.8, 2.3), CFrame.new(0, -2.9, -0.2), P.GateIron),
	def("rightLeg", "Leg", Vector3.new(1.7, 2.6, 1.8), CFrame.new(0, -1.3, 0), P.GatePost),
	def("rightLeg", "Boot", Vector3.new(1.9, 0.8, 2.3), CFrame.new(0, -2.9, -0.2), P.GateIron),
	-- Body
	def("body", "Belt", Vector3.new(4.6, 0.7, 2.6), CFrame.new(0, 3.55, 0), P.GatePost),
	def("body", "Buckle", Vector3.new(1.0, 0.6, 0.2), CFrame.new(0, 3.55, -1.35), P.Coin),
	def("body", "Torso", Vector3.new(5.0, 3.8, 2.8), CFrame.new(0, 5.8, 0), P.EnemyRed),
	def("body", "Chest", Vector3.new(3.6, 2.4, 0.3), CFrame.new(0, 6.1, -1.5), P.ButtonRedDark),
	def("body", "Badge", Vector3.new(0.9, 0.9, 0.12), CFrame.new(0, 6.2, -1.7), P.Coin),
	def("body", "PadL", Vector3.new(1.9, 0.9, 2.1), CFrame.new(-3.0, 7.75, 0), P.GatePost),
	def("body", "PadR", Vector3.new(1.9, 0.9, 2.1), CFrame.new(3.0, 7.75, 0), P.GatePost),
	-- Head
	def("head", "Head", Vector3.new(2.2, 2.0, 2.0), CFrame.new(0, 1.15, 0), P.CoinShine),
	def("head", "Helmet", Vector3.new(2.6, 1.0, 2.5), CFrame.new(0, 2.35, 0.05), P.ButtonRedDark),
	def(
		"head",
		"HornL",
		Vector3.new(0.45, 1.2, 0.45),
		CFrame.new(-1.1, 3.0, 0) * CFrame.Angles(0, 0, math.rad(25)),
		P.Coin
	),
	def(
		"head",
		"HornR",
		Vector3.new(0.45, 1.2, 0.45),
		CFrame.new(1.1, 3.0, 0) * CFrame.Angles(0, 0, math.rad(-25)),
		P.Coin
	),
	def("head", "EyeL", Vector3.new(0.45, 0.35, 0.1), CFrame.new(-0.5, 1.35, -1.02), P.Shade),
	def("head", "EyeR", Vector3.new(0.45, 0.35, 0.1), CFrame.new(0.5, 1.35, -1.02), P.Shade),
	def("head", "Brow", Vector3.new(1.8, 0.28, 0.12), CFrame.new(0, 1.75, -1.03) * CFrame.Angles(0, 0, 0), P.Shade),
	def("head", "Mouth", Vector3.new(1.0, 0.2, 0.1), CFrame.new(0, 0.62, -1.02), P.Shade),
	-- Left arm (big fist)
	def("leftArm", "Arm", Vector3.new(1.3, 3.4, 1.3), CFrame.new(0, -1.7, 0), P.EnemyRed),
	def("leftArm", "Fist", Vector3.new(1.6, 1.4, 1.6), CFrame.new(0, -3.8, 0), P.CoinShine),
	-- Right arm with the giant cannon (held forward, see AIM_ANGLE)
	def("rightArm", "Arm", Vector3.new(1.3, 3.0, 1.3), CFrame.new(0, -1.5, 0), P.EnemyRed),
	def("rightArm", "Hand", Vector3.new(1.5, 1.2, 1.5), CFrame.new(0, -3.2, 0), P.CoinShine),
	def(
		"rightArm",
		"Cannon",
		Vector3.new(4.4, 1.9, 1.9),
		CFrame.new(0, -4.6, 0) * ALONG_ARM,
		P.GatePost,
		Enum.PartType.Cylinder
	),
	def("rightArm", "Drum", Vector3.new(1.3, 1.3, 1.3), CFrame.new(1.05, -4.0, 0), P.Coin),
	def(
		"rightArm",
		"Ring",
		Vector3.new(0.5, 2.3, 2.3),
		CFrame.new(0, -6.6, 0) * ALONG_ARM,
		P.ButtonRed,
		Enum.PartType.Cylinder
	),
}

local RunnerBoss = {}

local function hpText(n: number): string
	return (Format.Money(math.max(0, math.floor(n + 0.5))):gsub("^%$", ""))
end

function RunnerBoss.new(parent: Instance, pivot: CFrame, maxHp: number): Boss
	local model = Instance.new("Model")
	model.Name = "Boss"
	local parts: { BasePart } = {}
	local groups: { string } = {}
	local offsets: { CFrame } = {}
	local helmet: BasePart? = nil
	for _, piece in PIECES do
		local part = RunnerModels.Part({
			Name = piece.name,
			Size = piece.size,
			CFrame = pivot * JOINTS[piece.group] * piece.offset,
			Color = piece.color,
			Shape = piece.shape,
			Material = piece.material,
			Parent = model,
		})
		table.insert(parts, part)
		table.insert(groups, piece.group)
		table.insert(offsets, piece.offset)
		if piece.name == "Helmet" then
			helmet = part
		end
	end
	-- Glowing cannon mouth.
	local glow = RunnerModels.Part({
		Name = "Glow",
		Size = Vector3.new(0.2, 1.3, 1.3),
		CFrame = pivot,
		Color = P.EnemyRed,
		Material = Enum.Material.Neon,
		Shape = Enum.PartType.Cylinder,
		Parent = model,
	})
	table.insert(parts, glow)
	table.insert(groups, "rightArm")
	table.insert(offsets, CFrame.new(0, -6.85, 0) * ALONG_ARM)
	for _, part in parts do
		part.CastShadow = true
	end

	local highlight = Instance.new("Highlight")
	highlight.FillColor = P.White
	highlight.OutlineTransparency = 1
	highlight.FillTransparency = 1
	highlight.DepthMode = Enum.HighlightDepthMode.Occluded
	highlight.Adornee = model
	highlight.Parent = model
	model.Parent = parent

	---------------------------------------------------------------------------
	-- Health bar on top of its head
	---------------------------------------------------------------------------
	local gui = Theme.Billboard({
		Name = "BossHp",
		Parent = model,
		Adornee = helmet,
		Size = UDim2.fromOffset(300, 70),
		StudsOffset = Vector3.new(0, 3.4, 0),
		AlwaysOnTop = true,
		MaxDistance = 500,
	})
	Theme.Label({
		Text = "👹 BOSS",
		TextColor3 = P.White,
		MaxTextSize = 26,
		Size = UDim2.new(1, 0, 0, 28),
		Parent = gui,
	})
	local back = Instance.new("Frame")
	back.Name = "Bar"
	back.BackgroundColor3 = P.PillBlack
	back.Position = UDim2.fromOffset(0, 30)
	back.Size = UDim2.new(1, 0, 0, 34)
	back.Parent = gui
	Theme.Corner(back, 0.5)
	Theme.Stroke(back, P.CardOutline, 3, true)
	local fill = Instance.new("Frame")
	fill.Name = "Fill"
	fill.BackgroundColor3 = P.MoneyGreen
	fill.Position = UDim2.fromOffset(3, 3)
	fill.Size = UDim2.new(1, -6, 1, -6)
	fill.Parent = back
	Theme.Corner(fill, 0.5)
	local hpLabel = Theme.Label({
		Text = "",
		MaxTextSize = 22,
		Size = UDim2.new(1, -12, 1, -4),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = 3,
		Parent = back,
	})

	---------------------------------------------------------------------------
	-- Animation state
	---------------------------------------------------------------------------
	local destroyed = false
	local current = pivot
	local walkFrom, walkTo = pivot, pivot
	local walkT, walkDuration = 1, 0
	local walkPhase = 0
	local clock = 0
	local recoil = 0 -- 1 right after a shot, decays to 0
	local flash = 0
	local lastFlash = 0
	local dying: number? = nil -- seconds since Die()
	local roaring = false

	local self = {} :: Boss

	function self.SetHp(_self: Boss, hp: number)
		local f = if maxHp > 0 then math.clamp(hp / maxHp, 0, 1) else 0
		fill.Size = UDim2.new(f, -math.floor(6 * f + 0.5), 1, -6)
		fill.Visible = f > 0
		fill.BackgroundColor3 = P.ButtonRed:Lerp(P.MoneyGreen, f)
		hpLabel.Text = hpText(hp) .. " / " .. hpText(maxHp)
	end

	function self.WalkTo(_self: Boss, target: CFrame, duration: number)
		walkFrom = current
		walkTo = target
		walkT = 0
		walkDuration = math.max(0.05, duration)
	end

	local function jointCFrames(root: CFrame): { [string]: CFrame }
		local walking = walkT < 1
		local swing = if walking then math.sin(walkPhase) * math.rad(28) else 0
		local breathe = math.sin(clock * 2.2) * 0.08
		local armUp = 0
		if roaring then
			armUp = math.rad(150) + math.sin(clock * 9) * math.rad(10)
		end
		local aim = AIM_ANGLE + recoil * math.rad(28) - (if roaring then math.rad(40) else 0)
		local lean = CFrame.Angles(recoil * math.rad(5), 0, 0)
		local bob = if walking then math.abs(math.sin(walkPhase)) * 0.35 else breathe
		local body = root * CFrame.new(0, bob, 0) * lean
		return {
			body = body,
			head = body * JOINTS.head * CFrame.Angles(if roaring then math.rad(-15) else 0, 0, 0),
			leftArm = body * JOINTS.leftArm * CFrame.Angles(-swing * 0.8 + math.rad(12) + armUp, 0, 0),
			rightArm = body * JOINTS.rightArm * CFrame.Angles(aim, 0, 0),
			leftLeg = root * JOINTS.leftLeg * CFrame.Angles(swing, 0, 0),
			rightLeg = root * JOINTS.rightLeg * CFrame.Angles(-swing, 0, 0),
		}
	end

	function self.Attack(_self: Boss): Vector3
		recoil = 1
		local joints = jointCFrames(current)
		return (joints.rightArm * MUZZLE).Position
	end

	function self.Hit(_self: Boss)
		if clock - lastFlash > 0.12 then
			lastFlash = clock
			flash = 1
		end
	end

	function self.Die(_self: Boss)
		if dying == nil then
			dying = 0
			roaring = false
			gui.Enabled = false
		end
	end

	function self.Roar(_self: Boss)
		roaring = true
	end

	function self.Chest(_self: Boss): Vector3
		return (current * CFrame.new(0, 6, -1.2)).Position
	end

	function self.AimPoints(_self: Boss): { Vector3 }
		return {
			(current * CFrame.new(0, 6.2, -1.6)).Position,
			(current * CFrame.new(-1.6, 5.2, -1.5)).Position,
			(current * CFrame.new(1.6, 5.4, -1.5)).Position,
			(current * CFrame.new(0, 8.9, -1.1)).Position,
			(current * CFrame.new(0, 4.0, -1.4)).Position,
		}
	end

	local outParts: { BasePart } = {}
	local outCFrames: { CFrame } = {}

	function self.Update(_self: Boss, dt: number)
		if destroyed then
			return
		end
		clock += dt
		if walkT < 1 then
			walkT = math.min(1, walkT + dt / walkDuration)
			walkPhase += dt * 7
			current = walkFrom:Lerp(walkTo, walkT)
		end
		recoil = math.max(0, recoil - dt * 3.5)

		local root = current
		if dying then
			local d = (dying :: number) + dt
			dying = d
			local fall = math.clamp(d / 1.1, 0, 1)
			root = current * CFrame.Angles(math.rad(82) * fall * fall, 0, 0) * CFrame.new(0, -fall * 0.6, 0)
			if d > 1.0 then
				local fade = math.clamp((d - 1.0) / 0.8, 0, 1)
				for _, part in parts do
					part.Transparency = fade
				end
			end
		end

		local joints = jointCFrames(root)
		table.clear(outParts)
		table.clear(outCFrames)
		for i, part in parts do
			table.insert(outParts, part)
			table.insert(outCFrames, joints[groups[i]] * offsets[i])
		end
		Workspace:BulkMoveTo(outParts, outCFrames, Enum.BulkMoveMode.FireCFrameChanged)

		if flash > 0 then
			flash = math.max(0, flash - dt * 7)
			highlight.FillTransparency = 1 - 0.65 * flash
		end
	end

	function self.Destroy(_self: Boss)
		if destroyed then
			return
		end
		destroyed = true
		model:Destroy()
	end

	self:SetHp(maxHp)
	self:Update(0)
	return self
end

return RunnerBoss
