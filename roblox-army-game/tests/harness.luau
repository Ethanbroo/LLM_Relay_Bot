--[[
	Lune test harness: loads Roblox-style ModuleScripts from src/ outside Roblox.

	Supports the require styles used by shared (pure-logic) modules:
		require(script.Parent.Config)
		require(script.Parent.Sub.Module)
		local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
		require(Shared.Config)

	Roblox datatypes (Color3, Vector3, CFrame, UDim2, Enum, ...) come from
	@lune/roblox. Services other than ReplicatedStorage are not available, so
	only pure modules (no Instances / services at require time) can be tested.

	Usage (from the roblox-army-game folder):
		lune run tests/run
]]

local fs = require("@lune/fs")
local luau = require("@lune/luau")
local roblox = require("@lune/roblox")

local Harness = {}

local SRC = "src"
local ROOTS = {
	ReplicatedStorage = { Shared = SRC .. "/shared" },
}

local moduleCache: { [string]: any } = {}
local loading: { [string]: boolean } = {}

-- Index every file and folder under src/ ONCE, up front. Proxy lookups run
-- inside metamethods, where Lune's (yielding) fs calls are not allowed.
local knownFiles: { [string]: boolean } = {}
local knownDirs: { [string]: boolean } = {}
local function indexTree(dir: string)
	knownDirs[dir] = true
	for _, name in fs.readDir(dir) do
		local full = dir .. "/" .. name
		if fs.isDir(full) then
			indexTree(full)
		else
			knownFiles[full] = true
		end
	end
end
indexTree(SRC)

local makeProxy

local function resolveFile(path: string): string?
	for _, candidate in { path .. ".luau", path .. ".lua", path .. "/init.luau", path .. "/init.lua" } do
		if knownFiles[candidate] then
			return candidate
		end
	end
	return nil
end

local function childPath(path: string, name: string): string?
	local base = path:gsub("/init%.luau$", ""):gsub("/init%.lua$", ""):gsub("%.luau$", ""):gsub("%.lua$", "")
	local candidate = base .. "/" .. name
	if resolveFile(candidate) or knownDirs[candidate] then
		return candidate
	end
	return nil
end

local function parentPath(path: string): string
	local trimmed = path:gsub("/init%.luau$", ""):gsub("/init%.lua$", "")
	return (trimmed:gsub("/[^/]+$", ""))
end

local customRequire

makeProxy = function(path: string)
	local proxy = {}
	local meta = {}
	meta.__index = function(_, key)
		if key == "Parent" then
			return makeProxy(parentPath(path))
		elseif key == "Name" then
			return (path:gsub("%.luau$", ""):match("([^/]+)$"))
		elseif key == "__path" then
			return path
		elseif key == "WaitForChild" or key == "FindFirstChild" then
			return function(_self, name)
				local child = childPath(path, name)
				if not child then
					if key == "WaitForChild" then
						error("harness: no child " .. name .. " under " .. path)
					end
					return nil
				end
				return makeProxy(child)
			end
		elseif key == "IsA" then
			return function()
				return true
			end
		end
		local child = childPath(path, key)
		if not child then
			error(("harness: %s is not a valid member of %s"):format(tostring(key), path))
		end
		return makeProxy(child)
	end
	meta.__tostring = function()
		return "Proxy(" .. path .. ")"
	end
	return setmetatable(proxy, meta)
end

local function serviceProxy(name: string)
	local children = ROOTS[name]
	if not children then
		error("harness: service " .. name .. " is not available in tests")
	end
	local service = {}
	local function child(_self, childName)
		local p = children[childName]
		if not p then
			error("harness: no " .. childName .. " in " .. name)
		end
		return makeProxy(p)
	end
	service.WaitForChild = child
	service.FindFirstChild = child
	return setmetatable(service, {
		__index = function(_, key)
			return child(nil, key)
		end,
	})
end

local fakeGame = {
	GetService = function(_self, name)
		return serviceProxy(name)
	end,
}

local function loadModule(path: string): any
	local file = resolveFile(path)
	if not file then
		error("harness: cannot find module at " .. path)
	end
	if moduleCache[file] ~= nil then
		return moduleCache[file]
	end
	if loading[file] then
		error("harness: circular require of " .. file)
	end
	loading[file] = true
	local source = fs.readFile(file)
	local env = {
		script = makeProxy(file),
		require = customRequire,
		game = fakeGame,
		Color3 = roblox.Color3,
		Vector3 = roblox.Vector3,
		Vector2 = roblox.Vector2,
		CFrame = roblox.CFrame,
		UDim = roblox.UDim,
		UDim2 = roblox.UDim2,
		Enum = roblox.Enum,
		BrickColor = roblox.BrickColor,
		ColorSequence = roblox.ColorSequence,
		ColorSequenceKeypoint = roblox.ColorSequenceKeypoint,
		NumberSequence = roblox.NumberSequence,
		NumberSequenceKeypoint = roblox.NumberSequenceKeypoint,
		NumberRange = roblox.NumberRange,
		task = task,
	}
	local fn = luau.load(source, { debugName = file, environment = env })
	local result = fn()
	moduleCache[file] = result
	loading[file] = nil
	return result
end

customRequire = function(target: any)
	if type(target) == "table" and target.__path then
		return loadModule(target.__path)
	end
	if type(target) == "string" then
		return require(target)
	end
	error("harness: unsupported require target " .. tostring(target))
end

-- Load a module by its path under src/, e.g. Harness.load("shared/Economy")
function Harness.load(relativePath: string): any
	return loadModule(SRC .. "/" .. relativePath)
end

-- Minimal assertion helpers --------------------------------------------------
local passed, failed = 0, 0
local failures = {}

function Harness.test(name: string, fn: () -> ())
	local ok, err = pcall(fn)
	if ok then
		passed += 1
		print("  ok   " .. name)
	else
		failed += 1
		table.insert(failures, name .. ": " .. tostring(err))
		print("  FAIL " .. name .. "\n       " .. tostring(err))
	end
end

function Harness.expect(condition: any, message: string?)
	if not condition then
		error(message or "expectation failed", 2)
	end
end

function Harness.near(a: number, b: number, tolerance: number, message: string?)
	if math.abs(a - b) > tolerance then
		error(
			("%s: expected %s ~= %s (+/- %s)"):format(message or "near", tostring(a), tostring(b), tostring(tolerance)),
			2
		)
	end
end

function Harness.summary(): boolean
	print(("\n%d passed, %d failed"):format(passed, failed))
	for _, f in failures do
		print("  - " .. f)
	end
	return failed == 0
end

return Harness
