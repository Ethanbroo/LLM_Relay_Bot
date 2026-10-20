--!strict
--[[
	CameraController
	Frames the home base like reference image 1: a high camera looking down at
	the island at roughly 45-50 degrees, instead of Roblox's close
	over-the-shoulder default. Applied when the character spawns and every time
	the player comes back to the base from a run or a fight. The player can
	still zoom and rotate freely afterwards (normal Roblox camera).

	No public API.
]]

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local StarterPlayer = game:GetService("StarterPlayer")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local State = require(Client.State)

local player = Players.LocalPlayer

local CameraController = {}

local BASE_OFFSET = Vector3.new(0, 36, 32) -- [PROPOSAL] ~48 degrees down, ~48 studs away

local framing = false

local function frameBase()
	if framing then
		return
	end
	framing = true
	task.spawn(function()
		-- Let runner/combat code restore the normal camera first.
		task.wait(0.25)
		local character = player.Character
		local root = character and character:WaitForChild("HumanoidRootPart", 5) :: BasePart?
		local camera = Workspace.CurrentCamera
		if root and camera and State.Mode() == "Base" and camera.CameraType == Enum.CameraType.Custom then
			-- Forcing the minimum zoom for one frame makes the camera scripts
			-- adopt that distance; then the normal range is restored.
			local normalMin = StarterPlayer.CameraMinZoomDistance
			player.CameraMinZoomDistance = BASE_OFFSET.Magnitude
			RunService.RenderStepped:Wait()
			camera.CFrame = CFrame.lookAt(root.Position + BASE_OFFSET, root.Position)
			RunService.RenderStepped:Wait()
			player.CameraMinZoomDistance = normalMin
		end
		framing = false
	end)
end

function CameraController:Init(_registry: { [string]: any }) end

function CameraController:Start()
	player.CharacterAdded:Connect(frameBase)
	if player.Character then
		frameBase()
	end
	State.ModeChanged:Connect(function(newMode: string)
		if newMode == "Base" then
			frameBase()
		end
	end)
end

return CameraController
