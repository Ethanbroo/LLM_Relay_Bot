--[[
	Server entry point.
	Every ModuleScript directly inside Server/Services is a service:
		Service.Priority (optional, higher = initialised first)
		Service:Init(registry)  -- create state, register Net handlers (no cross-service calls)
		Service:Start()         -- connect events, may call other services
	`registry` maps service name -> service table, e.g. registry.DataService.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Net = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Net"))
Net.Setup()

local servicesFolder = script.Parent:WaitForChild("Services")
local registry: { [string]: any } = {}
local ordered = {}

for _, module in servicesFolder:GetChildren() do
	if module:IsA("ModuleScript") then
		local ok, service = pcall(require, module)
		if ok and type(service) == "table" then
			registry[module.Name] = service
			table.insert(ordered, { name = module.Name, service = service })
		else
			warn(("[Server] failed to load %s: %s"):format(module.Name, tostring(service)))
		end
	end
end

table.sort(ordered, function(a, b)
	local pa, pb = a.service.Priority or 0, b.service.Priority or 0
	if pa ~= pb then
		return pa > pb
	end
	return a.name < b.name
end)

for _, entry in ordered do
	if type(entry.service.Init) == "function" then
		local ok, err = pcall(entry.service.Init, entry.service, registry)
		if not ok then
			warn(("[Server] %s:Init failed: %s"):format(entry.name, tostring(err)))
		end
	end
end

for _, entry in ordered do
	if type(entry.service.Start) == "function" then
		task.spawn(function()
			local ok, err = pcall(entry.service.Start, entry.service)
			if not ok then
				warn(("[Server] %s:Start failed: %s"):format(entry.name, tostring(err)))
			end
		end)
	end
end

print(("[Server] started %d services"):format(#ordered))
