--!strict
--[[
	ModeService
	Tracks what each player is doing and moves them between the three systems.

	Modes: "Base" (home map, default) | "Runner" | "Combat"
	The current mode is mirrored to the client as the Player attribute "Mode".
	Each player also gets a stable slot number (1..Config.MaxSlots) that picks
	their personal runner track and combat arena (see Layout).

	API (server):
		ModeService:GetMode(player): string
		ModeService:SetMode(player, mode)
		ModeService:GetSlot(player): number
		ModeService:Teleport(player, cframe): boolean
		ModeService:ReturnToBase(player, target: CFrame?)
			back to Base mode at `target`, or where the player stood before leaving the base
		ModeService:GetBaseCFrame(player): CFrame?
			where the player stood when they last left the base (nil if never)
		ModeService.ModeChanged: Signal(player, newMode, oldMode)
			Services that run sessions (runner, combat) listen to this and clean
			up when a player leaves their mode for any reason (death, leaving...).
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)
local Signal = require(Shared.Signal)

local ModeService = {}
ModeService.Priority = 90
ModeService.ModeChanged = Signal.new()

local modes: { [Player]: string } = {}
local slots: { [Player]: number } = {}
local usedSlots: { [number]: Player } = {}
local lastBaseCFrame: { [Player]: CFrame } = {}

local function allocateSlot(player: Player): number
	for i = 1, Config.MaxSlots do
		if not usedSlots[i] then
			usedSlots[i] = player
			return i
		end
	end
	-- More players than slots: share the last slot (tracks/arenas are per-client visuals anyway).
	return Config.MaxSlots
end

function ModeService:Init(_registry: { [string]: any }) end

function ModeService:Start()
	local function onPlayer(player: Player)
		slots[player] = allocateSlot(player)
		modes[player] = "Base"
		player:SetAttribute("Mode", "Base")
		player:SetAttribute("Slot", slots[player])
		player.CharacterAdded:Connect(function(character)
			-- A respawn always lands you back at the base.
			if modes[player] ~= "Base" then
				self:SetMode(player, "Base")
			end
			local humanoid = character:WaitForChild("Humanoid", 10) :: Humanoid?
			if humanoid then
				humanoid.Died:Connect(function()
					if modes[player] ~= "Base" then
						self:SetMode(player, "Base")
					end
				end)
			end
		end)
	end
	Players.PlayerAdded:Connect(onPlayer)
	for _, player in Players:GetPlayers() do
		onPlayer(player)
	end
	Players.PlayerRemoving:Connect(function(player)
		local old = modes[player]
		if old and old ~= "Base" then
			self.ModeChanged:Fire(player, "Base", old)
		end
		local slot = slots[player]
		if slot and usedSlots[slot] == player then
			usedSlots[slot] = nil
		end
		modes[player] = nil
		slots[player] = nil
		lastBaseCFrame[player] = nil
	end)
end

function ModeService:GetMode(player: Player): string
	return modes[player] or "Base"
end

function ModeService:GetSlot(player: Player): number
	return slots[player] or 1
end

function ModeService:SetMode(player: Player, mode: string)
	local old = modes[player] or "Base"
	if old == mode then
		return
	end
	if old == "Base" then
		-- Remember where the player was so we can bring them back there: the
		-- point under their feet, facing the way they faced (Teleport adds the
		-- standing height back; review: storing the root CFrame landed them
		-- 3.5 studs too high, e.g. inside a stall's roof).
		local character = player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart") :: BasePart?
		if root then
			local humanoid = character and character:FindFirstChildOfClass("Humanoid")
			local drop = (if humanoid then humanoid.HipHeight else 2) + root.Size.Y / 2
			local feet = root.Position - Vector3.new(0, drop, 0)
			local look = root.CFrame.LookVector
			local flat = Vector3.new(look.X, 0, look.Z)
			lastBaseCFrame[player] = if flat.Magnitude > 0.05
				then CFrame.lookAt(feet, feet + flat.Unit)
				else CFrame.new(feet)
		end
	end
	modes[player] = mode
	player:SetAttribute("Mode", mode)
	self.ModeChanged:Fire(player, mode, old)
end

function ModeService:Teleport(player: Player, cframe: CFrame): boolean
	local character = player.Character
	if not character then
		return false
	end
	local root = character:FindFirstChild("HumanoidRootPart") :: BasePart?
	if not root then
		return false
	end
	root.AssemblyLinearVelocity = Vector3.zero
	character:PivotTo(cframe + Vector3.new(0, 3.5, 0))
	return true
end

function ModeService:GetBaseCFrame(player: Player): CFrame?
	return lastBaseCFrame[player]
end

function ModeService:ReturnToBase(player: Player, place: CFrame?)
	local target = place or lastBaseCFrame[player]
	if not target then
		target = CFrame.new(Layout.SpawnPosition())
	end
	self:SetMode(player, "Base")
	self:Teleport(player, target)
end

return ModeService
