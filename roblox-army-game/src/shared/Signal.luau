--!strict
--[[
	Signal
	Tiny pure-Luau signal (no BindableEvents, safe to fire with tables).

		local s = Signal.new()
		local conn = s:Connect(function(a, b) ... end)
		s:Fire(1, 2)
		conn:Disconnect()
]]

export type Connection = {
	Connected: boolean,
	Disconnect: (self: Connection) -> (),
}

export type Signal = {
	Connect: (self: Signal, fn: (...any) -> ()) -> Connection,
	Once: (self: Signal, fn: (...any) -> ()) -> Connection,
	Fire: (self: Signal, ...any) -> (),
	Wait: (self: Signal) -> ...any,
	DisconnectAll: (self: Signal) -> (),
}

local Signal = {}
Signal.__index = Signal

function Signal.new(): Signal
	local self = setmetatable({ _handlers = {} :: { [any]: (...any) -> () } }, Signal)
	return (self :: any) :: Signal
end

function Signal:Connect(fn: (...any) -> ()): Connection
	local handlers = self._handlers
	local connection = {} :: any
	connection.Connected = true
	function connection.Disconnect(c)
		c.Connected = false
		handlers[c] = nil
	end
	handlers[connection] = fn
	return connection :: Connection
end

function Signal:Once(fn: (...any) -> ()): Connection
	local connection: Connection
	connection = self:Connect(function(...)
		connection:Disconnect()
		fn(...)
	end)
	return connection
end

function Signal:Fire(...: any)
	-- Copy first so handlers may disconnect while we iterate.
	local list = {}
	for _, fn in self._handlers do
		table.insert(list, fn)
	end
	for _, fn in list do
		task.spawn(fn, ...)
	end
end

function Signal:Wait(): ...any
	local thread = coroutine.running()
	self:Once(function(...)
		task.spawn(thread, ...)
	end)
	return coroutine.yield()
end

function Signal:DisconnectAll()
	for connection in self._handlers do
		connection.Connected = false
	end
	table.clear(self._handlers)
end

return Signal
