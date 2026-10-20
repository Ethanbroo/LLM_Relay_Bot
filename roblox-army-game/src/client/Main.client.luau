--[[
	Client entry point.
	Every ModuleScript directly inside Client/Controllers is a controller:
		Controller.Priority (optional, higher = initialised first)
		Controller:Init(registry)  -- build UI, local state (no cross-controller calls)
		Controller:Start()         -- connect events, may call other controllers
	`registry` maps controller name -> controller table.
]]

local Players = game:GetService("Players")

local player = Players.LocalPlayer
local clientFolder = script.Parent

-- Wait until the server has loaded our data (first Sync arrives right after).
local State = require(clientFolder:WaitForChild("State"))
State.Wait()

local controllersFolder = clientFolder:WaitForChild("Controllers")
local registry: { [string]: any } = {}
local ordered = {}

for _, module in controllersFolder:GetChildren() do
	if module:IsA("ModuleScript") then
		local ok, controller = pcall(require, module)
		if ok and type(controller) == "table" then
			registry[module.Name] = controller
			table.insert(ordered, { name = module.Name, controller = controller })
		else
			warn(("[Client] failed to load %s: %s"):format(module.Name, tostring(controller)))
		end
	end
end

table.sort(ordered, function(a, b)
	local pa, pb = a.controller.Priority or 0, b.controller.Priority or 0
	if pa ~= pb then
		return pa > pb
	end
	return a.name < b.name
end)

for _, entry in ordered do
	if type(entry.controller.Init) == "function" then
		local ok, err = pcall(entry.controller.Init, entry.controller, registry)
		if not ok then
			warn(("[Client] %s:Init failed: %s"):format(entry.name, tostring(err)))
		end
	end
end

for _, entry in ordered do
	if type(entry.controller.Start) == "function" then
		task.spawn(function()
			local ok, err = pcall(entry.controller.Start, entry.controller)
			if not ok then
				warn(("[Client] %s:Start failed: %s"):format(entry.name, tostring(err)))
			end
		end)
	end
end

print(("[Client] %s started %d controllers"):format(player.Name, #ordered))
