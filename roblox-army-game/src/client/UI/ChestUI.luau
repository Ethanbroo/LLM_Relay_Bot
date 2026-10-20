--!strict
--[[
	ChestUI  (STUB written by the lead — GEAR replaces the body, keeps the signature)
	See docs/ARCHITECTURE.md §4.5.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Types = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Types"))

local ChestUI = {}

function ChestUI.Play(_chestTier: string, _item: Types.Item, onDone: (() -> ())?)
	if onDone then
		onDone()
	end
end

return ChestUI
