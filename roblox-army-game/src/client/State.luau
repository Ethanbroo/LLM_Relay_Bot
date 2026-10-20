--!strict
--[[
	State (client)
	Latest PlayerData snapshot pushed by the server's DataService.

		local State = require(Client.State)
		State.Get()                -- PlayerData? (read-only, never mutate)
		State.Wait()               -- yields until the first snapshot
		State.Changed:Connect(function(data) ... end)
		State.Mode()               -- "Base" | "Runner" | "Combat"
		State.ModeChanged:Connect(function(newMode, oldMode) ... end)
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Net = require(Shared.Net)
local Signal = require(Shared.Signal)
local Types = require(Shared.Types)

local player = Players.LocalPlayer

local State = {}
State.Changed = Signal.new()
State.ModeChanged = Signal.new()

local data: Types.PlayerData? = nil
local currentMode: string = (player:GetAttribute("Mode") :: string?) or "Base"

Net.On("Sync", function(snapshot: Types.PlayerData)
	data = snapshot
	State.Changed:Fire(snapshot)
end)

player:GetAttributeChangedSignal("Mode"):Connect(function()
	local old = currentMode
	currentMode = (player:GetAttribute("Mode") :: string?) or "Base"
	if old ~= currentMode then
		State.ModeChanged:Fire(currentMode, old)
	end
end)

function State.Get(): Types.PlayerData?
	return data
end

function State.Wait(): Types.PlayerData
	if not data then
		task.spawn(function()
			-- Ask again in case the first push happened before we listened.
			while not data do
				Net.Request("RequestSync")
				task.wait(2)
			end
		end)
	end
	while not data do
		State.Changed:Wait()
	end
	return data :: Types.PlayerData
end

function State.Mode(): string
	return currentMode
end

return State
