--[[
	Bakes the static home-base map into the built place file, so the island,
	walls, plots, signs and portal are visible in Roblox Studio's editor before
	pressing Play (otherwise they only appear once the game runs).

	It runs the real server MapBuilder (src/server/Services/MapBuilder.luau)
	against the place's Workspace/Lighting inside Lune. At runtime MapBuilder
	sees workspace.Map already exists and keeps it; terrain water is still
	filled when the game starts (Lune cannot create terrain).

		rojo build default.project.json -o build/ArmyRunTycoon.rbxlx
		lune run scripts/bake build/ArmyRunTycoon.rbxlx
]]

local fs = require("@lune/fs")
local process = require("@lune/process")
local roblox = require("@lune/roblox")
local H = require("../tests/harness")

local path = process.args[1] or "build/ArmyRunTycoon.rbxlx"
local game = roblox.deserializePlace(fs.readFile(path))
local workspace = game:GetService("Workspace")
local lighting = game:GetService("Lighting")

if workspace:FindFirstChild("Map") then
	print("[bake] workspace.Map already present, nothing to do")
	process.exit(0)
end

-- Terrain is filled at runtime; skip background work during the bake.
local bakeTask = {
	spawn = function() end,
	defer = function() end,
	delay = function() end,
	wait = function()
		return 0
	end,
}

local MapBuilder = H.loadWithServices("server/Services/MapBuilder", {
	Workspace = workspace,
	Lighting = lighting,
}, { task = bakeTask, warn = print })

MapBuilder:Init({})

local map = workspace:FindFirstChild("Map")
if not map then
	print("[bake] MapBuilder did not create workspace.Map")
	process.exit(1)
end

local parts = 0
for _, d in map:GetDescendants() do
	if d:IsA("BasePart") then
		parts += 1
	end
end
fs.writeFile(path, roblox.serializePlace(game, true))
print(("[bake] baked workspace.Map (%d parts) into %s"):format(parts, path))
