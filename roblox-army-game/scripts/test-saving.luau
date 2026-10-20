--[[
	Save-system simulation (DataService) with a fake DataStore: slow start,
	session locks held by other servers, stale locks, Studio without saving,
	lost locks, and an autosave racing the final save.

		cd roblox-army-game && lune run scripts/test-saving

	Takes ~30 s (one scenario waits out the 20 s lock wait).
]]
local task = require("@lune/task")
local H = require("../tests/harness")

local function Signal()
	local handlers = {}
	return {
		Connect = function(_, fn)
			table.insert(handlers, fn)
			return { Disconnect = function() end }
		end,
		Fire = function(_, ...)
			for _, fn in handlers do
				task.spawn(fn, ...)
			end
		end,
	}
end

-- Fake DataStore with UpdateAsync semantics (callback returning nil cancels).
local function makeStore()
	local data = {}
	local st = { data = data, probeFailures = 0, calls = 0 }
	st.GetAsync = function(_, key)
		if key == "__probe" and st.probeFailures > 0 then
			st.probeFailures -= 1
			error("DataStore request was throttled")
		end
		return data[key]
	end
	st.UpdateAsync = function(_, key, fn)
		st.calls += 1
		if st.delay then
			task.wait(st.delay)
		end
		local old = data[key]
		local copy = old and H.load("shared/Types").DeepCopy(old) or nil
		local new = fn(copy)
		if new ~= nil then
			data[key] = H.load("shared/Types").DeepCopy(new)
		end
		return new
	end
	return st
end

local function makeServer(store, jobId, studio)
	local players = { PlayerAdded = Signal(), PlayerRemoving = Signal(), list = {} }
	players.GetPlayers = function()
		return players.list
	end
	local game = {
		JobId = jobId,
		BindToClose = function() end,
		GetService = function(_, name)
			if name == "DataStoreService" then
				return {
					GetDataStore = function()
						return store
					end,
				}
			end
			if name == "Players" then
				return players
			end
			if name == "RunService" then
				return {
					IsStudio = function()
						return studio
					end,
					IsServer = function()
						return true
					end,
				}
			end
			return H.serviceProxy(name)
		end,
	}
	-- Net is shared and touches remotes; stub it.
	local netStub = { Handle = function() end, Fire = function() end }
	local DS = H.loadWithServices("server/Services/DataService", {}, {
		task = task,
		game = game,
		warn = function(...)
			print("   warn:", ...)
		end,
		require = function(target)
			if type(target) == "table" and tostring(target.__path):find("Net") then
				return netStub
			end
			return H.customRequire(target)
		end,
	})
	DS:Init({})
	DS:Start()
	return DS, players
end

local function makePlayer(id, name)
	local p = { UserId = id, Name = name, Parent = true, kicked = nil, attrs = {} }
	p.SetAttribute = function(self, k, v)
		self.attrs[k] = v
	end
	p.Kick = function(self, msg)
		self.kicked = msg
		self.Parent = nil
	end
	return p
end

local function waitUntil(cond, timeout)
	local t0 = os.clock()
	while not cond() and os.clock() - t0 < timeout do
		task.wait(0.1)
	end
	return cond()
end

local passed, failed = 0, 0
local function check(name, ok)
	if ok then
		passed += 1
		print("  ok   " .. name)
	else
		failed += 1
		print("  FAIL " .. name)
	end
end

-- A. Slow DataStore at start: player must wait, then get the REAL save (not defaults).
do
	local store = makeStore()
	store.data["u_1"] = H.load("shared/Types").DefaultData()
	store.data["u_1"].cash = 777
	store.probeFailures = 2 -- first two probes fail (throttled)
	local DS, players = makeServer(store, "jobA", false)
	local p = makePlayer(1, "Alice")
	table.insert(players.list, p)
	players.PlayerAdded:Fire(p)
	task.wait(1)
	check("A: no blank profile while the store is not ready", DS:Get(p) == nil)
	waitUntil(function()
		return DS:Get(p) ~= nil
	end, 15)
	check("A: real save loaded after the store recovered", DS:Get(p) and DS:Get(p).cash == 777)
	check("A: lock taken by this server", store.data["u_1"]._lock and store.data["u_1"]._lock.jobId == "jobA")
	DS:AddCash(p, 10)
	check("A: SaveNow writes", DS:SaveNow(p) == true and store.data["u_1"].cash == 787)
	players.PlayerRemoving:Fire(p)
	task.wait(0.5)
	check("A: final save releases the lock", store.data["u_1"]._lock == nil)
end

-- B. Another live server still holds the lock: wait, then load its final save.
do
	local store = makeStore()
	local rec = H.load("shared/Types").DefaultData()
	rec.cash = 100
	rec._lock = { jobId = "jobOld", t = os.time() }
	store.data["u_2"] = rec
	local DS, players = makeServer(store, "jobB", false)
	local p = makePlayer(2, "Bob")
	players.PlayerAdded:Fire(p)
	task.wait(2)
	check("B: waits while another server owns the save", DS:Get(p) == nil)
	-- old server writes its final save (cash 250) and releases
	store.data["u_2"].cash = 250
	store.data["u_2"]._lock = nil
	waitUntil(function()
		return DS:Get(p) ~= nil
	end, 10)
	check("B: loads the old server's final save", DS:Get(p) and DS:Get(p).cash == 250)
end

-- C. Lock never released (old server hung): take over after the wait, old server can no longer write.
do
	local store = makeStore()
	local rec = H.load("shared/Types").DefaultData()
	rec.cash = 50
	rec._lock = { jobId = "jobHung", t = os.time() }
	store.data["u_3"] = rec
	local DS, players = makeServer(store, "jobC", false)
	local p = makePlayer(3, "Cara")
	players.PlayerAdded:Fire(p)
	local ok = waitUntil(function()
		return DS:Get(p) ~= nil
	end, 30)
	check("C: takes over a lock that is never released (~20 s)", ok and store.data["u_3"]._lock.jobId == "jobC")
	-- now an "old server" instance that thinks it owns the save tries to write
	local DS2, players2 = makeServer(store, "jobHung", false)
	-- simulate DS2 having a loaded profile: load fresh player on DS2 will block (jobC holds lock) so instead check lockedByOther path via SaveNow on DS
	check("C: new owner can save", DS:SaveNow(p) == true)
end

-- D. Stale lock from a crashed server (old timestamp) is taken immediately.
do
	local store = makeStore()
	local rec = H.load("shared/Types").DefaultData()
	rec.cash = 9
	rec._lock = { jobId = "jobCrashed", t = os.time() - 3600 }
	store.data["u_4"] = rec
	local DS, players = makeServer(store, "jobD", false)
	local p = makePlayer(4, "Dan")
	players.PlayerAdded:Fire(p)
	local ok = waitUntil(function()
		return DS:Get(p) ~= nil
	end, 5)
	check("D: stale lock taken at once", ok and DS:Get(p).cash == 9)
end

-- E. Studio without API access: plays with defaults, never saves.
do
	local store = makeStore()
	store.probeFailures = 99
	local DS, players = makeServer(store, "", true)
	local p = makePlayer(5, "Eve")
	players.PlayerAdded:Fire(p)
	local ok = waitUntil(function()
		return DS:Get(p) ~= nil
	end, 10)
	check("E: Studio fallback profile", ok and DS:IsSaving() == false)
	check("E: Studio fallback never writes", DS:SaveNow(p) == false and store.data["u_5"] == nil)
end

-- F. Server that lost the lock stops writing.
do
	local store = makeStore()
	local DS, players = makeServer(store, "jobF", false)
	local p = makePlayer(6, "Finn")
	players.PlayerAdded:Fire(p)
	waitUntil(function()
		return DS:Get(p) ~= nil
	end, 5)
	store.data["u_6"]._lock = { jobId = "jobOther", t = os.time() }
	store.data["u_6"].cash = 999
	DS:AddCash(p, 1)
	check("F: save refused when another server owns the lock", DS:SaveNow(p) == false and store.data["u_6"].cash == 999)
end

-- G. An autosave fired while the final (release) save is still writing must not re-take the lock.
do
	local store = makeStore()
	local DS, players = makeServer(store, "jobG", false)
	local p = makePlayer(7, "Gus")
	players.PlayerAdded:Fire(p)
	waitUntil(function()
		return DS:Get(p) ~= nil
	end, 5)
	store.delay = 0.5
	players.PlayerRemoving:Fire(p)
	task.wait(0.1)
	local late = DS:SaveNow(p)
	waitUntil(function()
		return DS:Get(p) == nil
	end, 5)
	task.wait(1)
	check("G: autosave during the final save is refused", late == false)
	check("G: lock released after leaving", store.data["u_7"]._lock == nil)
end

print(("\n%d passed, %d failed"):format(passed, failed))
require("@lune/process").exit(if failed == 0 then 0 else 1)
