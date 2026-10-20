--[[
	Runs every tests/*.spec.luau file.
		cd roblox-army-game && lune run tests/run
	Each spec returns function(H) where H is the harness (H.load, H.test, H.expect, H.near).
]]

local fs = require("@lune/fs")
local process = require("@lune/process")
local H = require("./harness")

local filter = process.args[1]
local specs = {}
for _, name in fs.readDir("tests") do
	if name:match("%.spec%.luau$") and (filter == nil or name:find(filter, 1, true)) then
		table.insert(specs, name)
	end
end
table.sort(specs)

for _, name in specs do
	print("\n# " .. name)
	local spec = require("./" .. name:gsub("%.luau$", ""))
	local ok, err = pcall(spec, H)
	if not ok then
		H.test(name .. " (load)", function()
			error(err)
		end)
	end
end

if not H.summary() then
	process.exit(1)
end
