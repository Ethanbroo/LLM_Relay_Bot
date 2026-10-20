--!strict
--[[
	Net
	All client <-> server traffic goes through here.

	Requests (client -> server, with a reply):
		-- server
		Net.Handle("BuyRunnerUpgrade", function(player, kind) ... return Net.Ok({ level = 3 }) end)
		-- client
		local result = Net.Request("BuyRunnerUpgrade", "troops")
		if result.ok then ... else warn(result.err) end

	Handlers return a table (Net.Ok / Net.Fail) or nil (= ok). A handler that
	errors returns { ok = false, err = "Something went wrong" } to the client.

	Push events (server -> client, or client -> server):
		Net.Fire("Notify", player, "Hello", "success")   -- server -> one client
		Net.FireAll("Notify", "Server message")          -- server -> all clients
		Net.FireServer("Name", ...)                      -- client -> server
		Net.On("Notify", function(...) end)              -- listen on either side
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

export type Result = { ok: boolean, err: string?, [string]: any }

local Net = {}

-- Every RemoteEvent the game uses. Add new names here (server creates them).
Net.EVENTS = {
	"Sync", -- server -> client: full PlayerData snapshot
	"Notify", -- server -> client: (text, kind) toast
	"Fx", -- server -> client: (kind, payload) world effects (floating text...)
	"Runner", -- server -> client: (msg, payload)
	"Combat", -- both ways: (msg, payload) reliable combat events
	"Base", -- server -> client: (msg, payload)
	"Boost", -- server -> client: (msg, payload) random offers / timers
	"Shop", -- server -> client: (msg, payload)
	"Inventory", -- server -> client: (msg, payload)
	"Vehicle", -- server -> client: (msg, payload)
}

-- High-frequency, droppable events.
Net.UNRELIABLE = {
	"CombatSnap", -- server -> client: combat entity snapshot (~20 Hz)
}

local FOLDER_NAME = "Remotes"
local ACTION_NAME = "Action"
local IS_SERVER = RunService:IsServer()

local folder: Folder? = nil
local handlers: { [string]: (Player, ...any) -> any } = {}

-- Per-player request budget (token bucket) to stop remote spam.
local RATE_PER_SECOND = 25
local BURST = 40
local buckets: { [Player]: { tokens: number, last: number } } = {}

local function getFolder(): Folder
	local cached = folder
	if cached then
		return cached
	end
	local result: Folder
	if IS_SERVER then
		local existing = ReplicatedStorage:FindFirstChild(FOLDER_NAME)
		if existing and existing:IsA("Folder") then
			result = existing
		else
			result = Instance.new("Folder")
			result.Name = FOLDER_NAME
			result.Parent = ReplicatedStorage
		end
	else
		result = ReplicatedStorage:WaitForChild(FOLDER_NAME) :: Folder
	end
	folder = result
	return result
end

local function getRemote(name: string): Instance
	local f = getFolder()
	if IS_SERVER then
		local remote = f:FindFirstChild(name)
		if not remote then
			error(("Net: unknown remote '%s' (add it to Net.EVENTS)"):format(name))
		end
		return remote
	end
	return f:WaitForChild(name)
end

function Net.Ok(payload: { [string]: any }?): Result
	local result = (payload or {}) :: Result
	result.ok = true
	return result
end

function Net.Fail(message: string): Result
	return { ok = false, err = message }
end

-- Server: create every remote. Called once by Server/Main before services load.
function Net.Setup()
	assert(IS_SERVER, "Net.Setup is server-only")
	local f = getFolder()
	for _, name in Net.EVENTS do
		if not f:FindFirstChild(name) then
			local remote = Instance.new("RemoteEvent")
			remote.Name = name
			remote.Parent = f
		end
	end
	for _, name in Net.UNRELIABLE do
		if not f:FindFirstChild(name) then
			local remote = Instance.new("UnreliableRemoteEvent")
			remote.Name = name
			remote.Parent = f
		end
	end
	local action = f:FindFirstChild(ACTION_NAME) :: RemoteFunction?
	if not action then
		local created = Instance.new("RemoteFunction")
		created.Name = ACTION_NAME
		created.Parent = f
		action = created
	end
	(action :: RemoteFunction).OnServerInvoke = function(player: Player, actionName: any, ...: any): Result
		if type(actionName) ~= "string" then
			return Net.Fail("Bad request")
		end
		-- rate limit
		local now = os.clock()
		local bucket = buckets[player]
		if not bucket then
			bucket = { tokens = BURST, last = now }
			buckets[player] = bucket
		end
		bucket.tokens = math.min(BURST, bucket.tokens + (now - bucket.last) * RATE_PER_SECOND)
		bucket.last = now
		if bucket.tokens < 1 then
			return Net.Fail("Slow down!")
		end
		bucket.tokens -= 1

		local handler = handlers[actionName]
		if not handler then
			return Net.Fail("Unknown action " .. actionName)
		end
		local ok, result = pcall(handler, player, ...)
		if not ok then
			warn(("[Net] action %s failed for %s: %s"):format(actionName, player.Name, tostring(result)))
			return Net.Fail("Something went wrong")
		end
		if result == nil then
			return Net.Ok()
		end
		if type(result) ~= "table" then
			return Net.Ok({ value = result })
		end
		if result.ok == nil then
			result.ok = true
		end
		return result
	end
	Players.PlayerRemoving:Connect(function(player)
		buckets[player] = nil
	end)
end

-- Server: register a request handler.
function Net.Handle(actionName: string, handler: (Player, ...any) -> any)
	assert(IS_SERVER, "Net.Handle is server-only")
	if handlers[actionName] then
		warn("[Net] handler replaced: " .. actionName)
	end
	handlers[actionName] = handler
end

-- Client: send a request and wait for the reply. Never errors.
function Net.Request(actionName: string, ...: any): Result
	assert(not IS_SERVER, "Net.Request is client-only")
	local remote = getRemote(ACTION_NAME) :: RemoteFunction
	local args = table.pack(...)
	local ok, result = pcall(function()
		return remote:InvokeServer(actionName, table.unpack(args, 1, args.n))
	end)
	if not ok then
		return Net.Fail("Connection problem")
	end
	if type(result) ~= "table" then
		return Net.Fail("Bad reply")
	end
	return result :: Result
end

-- Server: fire one client.
function Net.Fire(eventName: string, player: Player, ...: any)
	local remote = getRemote(eventName) :: any
	remote:FireClient(player, ...)
end

-- Server: fire every client.
function Net.FireAll(eventName: string, ...: any)
	local remote = getRemote(eventName) :: any
	remote:FireAllClients(...)
end

-- Client: fire the server.
function Net.FireServer(eventName: string, ...: any)
	local remote = getRemote(eventName) :: any
	remote:FireServer(...)
end

-- Listen to an event. Server callbacks receive (player, ...).
function Net.On(eventName: string, callback: (...any) -> ()): RBXScriptConnection
	local remote = getRemote(eventName) :: any
	if IS_SERVER then
		return remote.OnServerEvent:Connect(callback)
	end
	return remote.OnClientEvent:Connect(callback)
end

-- Server convenience: toast message for one player.
-- kind: "info" | "success" | "error" | "cash"
function Net.Notify(player: Player, text: string, kind: string?)
	Net.Fire("Notify", player, text, kind or "info")
end

return Net
