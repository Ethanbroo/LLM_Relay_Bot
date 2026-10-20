--!strict
--[[
	RunnerController (client)
	Runs the ARMY RUNNER game mode on the player's screen (ARCHITECTURE §6.1).

	Public API (§4.7):
		RunnerController:OpenPrepare()
			Same as stepping on the ARMY RUN portal pad: asks the server for a
			run (RunnerPrepare), which teleports you to your track, then shows
			the pre-run screen. Safe to call repeatedly (double clicks ignored).

	How a run goes:
	  1. PREPARE  - the avatar stands at the track start, camera high behind it
	                (like reference image 2). Left: permanent upgrade cards.
	                Right: bonuses. Bottom: tutorial hand. The first left/right
	                input (A/D, arrow keys, mouse or touch drag, gamepad stick or
	                d-pad) asks the server to start (RunnerGo).
	  2. RUNNING  - default controls are disabled; every frame we call
	                humanoid:Move() forward (-Z) and steer towards a target lane.
	                When the avatar crosses a row we record the lane it is in and
	                replay the run so far with RunnerSim.Resolve to show what
	                happened (troops added/removed, cash, weapons, enemies).
	  3. BOSS     - the avatar stops at the enemy base, troops fire for a few
	                seconds while its HP bar drains, then we send our lane choices
	                to the server (RunnerFinish). The server re-simulates and pays.
	  4. SUMMARY  - "RUN COMPLETE!" panel; COLLECT returns to the base.

	Cleanup (camera, controls, jump, visuals, UI, input) happens on COLLECT,
	Back, any failed request, death/respawn, or the server leaving Runner mode.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Layout = require(Shared.Layout)
local Net = require(Shared.Net)
local RunnerCourse = require(Shared.Runner.RunnerCourse)
local RunnerDefs = require(Shared.Runner.RunnerDefs)
local RunnerSim = require(Shared.Runner.RunnerSim)
local Types = require(Shared.Types)

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)
local RunnerUI = require(Client.UI.RunnerUI)
local RunnerVisuals = require(Client.World.RunnerVisuals)

type Course = RunnerCourse.Course
type Row = RunnerCourse.Row
type Stats = RunnerSim.Stats
type Result = RunnerSim.Result
type RowOutcome = RunnerSim.RowOutcome
type PlayerData = Types.PlayerData

local player = Players.LocalPlayer

-------------------------------------------------------------------------------
-- Tunables
-------------------------------------------------------------------------------
local PORTAL_RADIUS = 7 -- studs from Layout.RunnerPortalPosition()
local PORTAL_COOLDOWN = 4 -- [PROPOSAL] seconds between portal triggers
local KEY_STEER_SPEED = 26 -- [PROPOSAL] how fast keys / stick move the target lane (studs/s)
local STEER_GAIN = 7 -- [PROPOSAL] how hard the avatar chases the target lane
local LATERAL_LIMIT = Layout.RUNNER_TRACK_WIDTH / 2 - 2
local DRAG_START_PIXELS = 14 -- [PROPOSAL] drag this far to start the run
local CAMERA_OFFSET = Vector3.new(0, 17, 22) -- high behind the runner (image 2)
local BOSS_CAMERA_OFFSET = Vector3.new(0, 20, 30)
local CAMERA_LOOK_AHEAD = 20
local ENGAGE_RANGE = 18 -- [PROPOSAL] troops start shooting this far before a squad/barricade (visual only)
-- Troops fire at the enemy base for at least RunnerDefs.BOSS_SECONDS (the
-- sim also uses it: fraction destroyed = firepower x BOSS_SECONDS / hp).
local BOSS_MIN_TIME = if type((RunnerDefs :: any).BOSS_SECONDS) == "number" then math.max(3, (RunnerDefs :: any).BOSS_SECONDS) else 3
local BOSS_MAX_TIME = 30 -- [PROPOSAL]
local STUCK_TIME = 6 -- [PROPOSAL] no forward progress for this long = give up the run
local MODE_GRACE = 3 -- seconds we tolerate "not in Runner mode" before cleaning up
local RENDER_NAME = "ArmyRunnerStep"
local DEFAULT_RUN_SPEED = 24

-------------------------------------------------------------------------------
-- State
-------------------------------------------------------------------------------
type Live = { troops: number, cash: number, tier: number }

type Session = {
	runId: string,
	course: Course,
	level: number,
	slot: number,
	centreX: number,
	startZ: number,
	stats: Stats,
	previewStats: { startTroops: number, damage: number, capacity: number },
	runSpeed: number,
	phase: string, -- "prepare" | "running" | "boss" | "finishing" | "summary" | "ended"
	choices: { number },
	nextRow: number,
	engaged: { [number]: boolean },
	live: Live,
	targetLateral: number,
	camLateral: number,
	camOffset: Vector3,
	startClock: number,
	lastDistance: number,
	lastProgressClock: number,
	notRunnerSince: number?,
	character: Model,
	humanoid: Humanoid,
	connections: { any }, -- RBXScriptConnections and Signal connections (both have :Disconnect())
	visuals: RunnerVisuals.Visuals?,
	hud: RunnerUI.Hud?,
	prepare: RunnerUI.Prepare?,
	closeSummary: (() -> ())?,
	goPending: boolean,
	buying: { [string]: boolean },
	levelOverrides: { [string]: number },
	dragInput: InputObject?,
	dragLastX: number,
	dragTotal: number,
	held: { [Enum.KeyCode]: boolean },
	thumbX: number,
}

local RunnerController = {}

local registry: { [string]: any } = {}
local session: Session? = nil
local opening = false
local portalArmed = false
local portalCooldownUntil = 0
local controlsCache: any = nil

-------------------------------------------------------------------------------
-- Small helpers
-------------------------------------------------------------------------------
local function toast(text: string, kind: string?)
	Theme.Toast(text, kind)
end

local function getRoot(character: Model?): BasePart?
	if not character then
		return nil
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root
	end
	return nil
end

-- Roblox's default PlayerModule -> ControlModule (keyboard/touch/gamepad movement).
local function getControls(): any
	if controlsCache then
		return controlsCache
	end
	local playerScripts = player:FindFirstChild("PlayerScripts") or player:WaitForChild("PlayerScripts", 5)
	local module = playerScripts and playerScripts:WaitForChild("PlayerModule", 5)
	if module and module:IsA("ModuleScript") then
		local ok, playerModule = pcall(require, module)
		if ok and type(playerModule) == "table" and type((playerModule :: any).GetControls) == "function" then
			local gotOk, controls = pcall((playerModule :: any).GetControls, playerModule)
			if gotOk and controls then
				controlsCache = controls
			end
		end
	end
	return controlsCache
end

local function setControlsEnabled(enabled: boolean)
	local controls = getControls()
	if not controls then
		return
	end
	if enabled then
		pcall(function()
			controls:Enable()
		end)
	else
		pcall(function()
			controls:Disable()
		end)
	end
end

local function numberOr(value: any, fallback: number): number
	if type(value) == "number" and value == value then
		return value
	end
	return fallback
end

local function cleanStats(raw: any, fallback: { startTroops: number, damage: number, capacity: number }): Stats
	local source = if type(raw) == "table" then raw else {}
	return {
		startTroops = numberOr(source.startTroops, fallback.startTroops),
		damage = numberOr(source.damage, fallback.damage),
		capacity = numberOr(source.capacity, fallback.capacity),
		payoutBonus = if type(source.payoutBonus) == "number" then source.payoutBonus else nil,
	}
end

local function safeResolve(s: Session): Result?
	local ok, result = pcall(RunnerSim.Resolve, s.course, s.stats, s.choices)
	if ok and type(result) == "table" then
		return result
	end
	warn("[RunnerController] RunnerSim.Resolve failed: " .. tostring(result))
	return nil
end

local function findOutcome(result: Result, rowIndex: number): RowOutcome?
	if type(result.outcomes) ~= "table" then
		return nil
	end
	for _, outcome in result.outcomes do
		if outcome.row == rowIndex then
			return outcome
		end
	end
	return nil
end

-- Which lane (option) of a row the avatar is in, by its lateral position.
local function laneAt(row: Row, lateral: number): number
	local best, bestDistance = 1, math.huge
	for index, option in row.options do
		local lane = if type(option.lane) == "number" then option.lane else index
		local half = option.width / 2
		if lateral >= option.x - half and lateral <= option.x + half then
			return lane
		end
		local d = math.abs(lateral - option.x)
		if d < bestDistance then
			best, bestDistance = lane, d
		end
	end
	return best
end

-- Fraction of the enemy base destroyed (drives the HP bar at the end).
local function bossFraction(course: Course, result: Result?): number
	if not result then
		return 0
	end
	for _, line in result.lines do
		if line.kind == "boss" then
			if course.boss.reward > 0 then
				return math.clamp(line.value / course.boss.reward, 0, 1)
			end
			return 1
		end
	end
	return if result.troops > 0 then 1 else 0
end

-------------------------------------------------------------------------------
-- Cleanup
-------------------------------------------------------------------------------
local function restoreCamera()
	local camera = Workspace.CurrentCamera
	if not camera then
		return
	end
	camera.CameraType = Enum.CameraType.Custom
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	if humanoid then
		camera.CameraSubject = humanoid
	end
end

-- Ends the local session. Safe to call many times.
local function cleanup()
	local s = session
	if not s then
		return
	end
	session = nil
	s.phase = "ended"
	RunService:UnbindFromRenderStep(RENDER_NAME)
	for _, connection in s.connections do
		connection:Disconnect()
	end
	table.clear(s.connections)
	local function try(fn: () -> ())
		local ok, err = pcall(function(): any
			fn()
			return nil
		end)
		if not ok then
			warn("[RunnerController] cleanup step failed: " .. tostring(err))
		end
	end
	try(function()
		if s.closeSummary then
			s.closeSummary()
			s.closeSummary = nil
		end
	end)
	try(function()
		if s.prepare then
			s.prepare.Destroy()
			s.prepare = nil
		end
	end)
	try(function()
		if s.hud then
			s.hud.Destroy()
			s.hud = nil
		end
	end)
	try(function()
		if s.visuals then
			s.visuals:Destroy()
			s.visuals = nil
		end
	end)
	try(function()
		if s.humanoid.Parent then
			s.humanoid:Move(Vector3.zero, false)
			s.humanoid:SetStateEnabled(Enum.HumanoidStateType.Jumping, true)
		end
	end)
	try(restoreCamera)
	try(function()
		setControlsEnabled(true)
	end)
	-- The return teleport may drop us on the portal pad: wait until we step off.
	portalArmed = false
	portalCooldownUntil = os.clock() + PORTAL_COOLDOWN
end

-- Leave the run: local cleanup now, then tell the server (teleports to base).
local function leave(s: Session)
	if session ~= s then
		return
	end
	cleanup()
	task.spawn(function()
		local reply = Net.Request("RunnerLeave")
		if not reply.ok then
			warn("[RunnerController] RunnerLeave failed: " .. tostring(reply.err))
		end
	end)
end

local function fail(s: Session, message: string?)
	if session ~= s then
		return
	end
	toast(message or "Something went wrong with the run", "error")
	leave(s)
end

-------------------------------------------------------------------------------
-- Run flow
-------------------------------------------------------------------------------
local function crossRow(s: Session, index: number, lane: number)
	s.choices[index] = lane
	s.nextRow = index + 1
	local result = safeResolve(s)
	local outcome = if result then findOutcome(result, index) else nil
	local visuals = s.visuals
	if visuals then
		visuals:RowCrossed(index, lane, outcome, s.stats.capacity)
	end
	if outcome then
		s.live = { troops = outcome.troopsAfter, cash = outcome.cashAfter, tier = outcome.weaponTier }
	end
	if visuals then
		visuals:SetSquad(s.live.troops, s.live.cash, s.stats.capacity, s.live.tier)
	end
end

local function finish(s: Session)
	if session ~= s or s.phase ~= "boss" then
		return
	end
	s.phase = "finishing"
	local reply = Net.Request("RunnerFinish", s.runId, s.choices)
	if session ~= s then
		return
	end
	if not reply.ok then
		fail(s, reply.err or "Could not finish the run")
		return
	end
	s.phase = "summary"
	local result = reply.result :: Result
	local paid = numberOr(reply.paid, 0)
	local data = State.Get()
	local upgrades = if data then data.runner.upgrades else { troops = 0, damage = 0, capacity = 0 }
	s.closeSummary = RunnerUI.ShowSummary({
		Level = s.level,
		Result = result,
		Paid = paid,
		Boosted = reply.boosted == true,
		Upgrades = upgrades,
		OnCollect = function()
			s.closeSummary = nil
			leave(s)
		end,
	})
end

local function beginBoss(s: Session, lateral: number)
	s.phase = "boss"
	-- Any row we somehow skipped counts with the lane we are in now.
	local rows = s.course.rows
	for index = s.nextRow, #rows do
		crossRow(s, index, laneAt(rows[index], lateral))
	end
	s.humanoid:Move(Vector3.zero, false)
	local result = safeResolve(s)
	local fraction = bossFraction(s.course, result)
	local okMin, minDuration = pcall(RunnerSim.MinDuration, s.course)
	local elapsed = os.clock() - s.startClock
	-- Fight at least BOSS_MIN_TIME, and long enough that the server's
	-- anti-cheat minimum run time is surely over before we report.
	local wanted = if okMin and type(minDuration) == "number" then minDuration - elapsed + 0.75 else 0
	local duration = math.clamp(wanted, BOSS_MIN_TIME, BOSS_MAX_TIME)
	if s.visuals then
		s.visuals:StartBossFight(duration, fraction)
	end
	task.delay(duration + 0.4, function()
		finish(s)
	end)
end

local function requestGo(s: Session)
	if s.phase ~= "prepare" or s.goPending or next(s.buying) ~= nil then
		return
	end
	s.goPending = true
	task.spawn(function()
		local reply = Net.Request("RunnerGo", s.runId)
		s.goPending = false
		if session ~= s or s.phase ~= "prepare" then
			return
		end
		if not reply.ok then
			fail(s, reply.err or "Could not start the run")
			return
		end
		s.stats = cleanStats(reply.stats, s.previewStats)
		s.runSpeed = numberOr(reply.runSpeed, s.runSpeed)
		s.phase = "running"
		s.startClock = os.clock()
		s.lastProgressClock = os.clock()
		s.live = { troops = s.stats.startTroops, cash = 0, tier = 1 }
		local root = getRoot(s.character)
		if root then
			s.targetLateral = math.clamp(root.Position.X - s.centreX, -LATERAL_LIMIT, LATERAL_LIMIT)
			s.lastDistance = s.startZ - root.Position.Z
		end
		if s.prepare then
			s.prepare.Destroy()
			s.prepare = nil
		end
		if s.visuals then
			s.visuals:SetSquad(s.live.troops, 0, s.stats.capacity, 1)
		end
	end)
end

local function buyUpgrade(s: Session, kind: string)
	if s.phase ~= "prepare" or s.buying[kind] or s.goPending then
		return
	end
	s.buying[kind] = true
	if s.prepare then
		s.prepare.SetBusy(kind, true)
	end
	task.spawn(function()
		local reply = Net.Request("BuyRunnerUpgrade", kind)
		s.buying[kind] = nil
		if session ~= s then
			return
		end
		if reply.ok then
			if type(reply.level) == "number" then
				s.levelOverrides[kind] = reply.level
			end
			if type(reply.stats) == "table" then
				local stats = cleanStats(reply.stats, s.previewStats)
				s.previewStats = { startTroops = stats.startTroops, damage = stats.damage, capacity = stats.capacity }
				s.stats = stats
			end
			toast("Permanent upgrade bought!", "success")
			if s.visuals and s.phase == "prepare" then
				s.visuals:SetSquad(s.previewStats.startTroops, 0, s.previewStats.capacity, 1)
			end
		else
			toast(reply.err or "Could not buy the upgrade", "error")
		end
		local prepare = s.prepare
		if prepare then
			prepare.SetBusy(kind, false)
			local data = State.Get()
			if data then
				prepare.Update(data, s.previewStats, s.levelOverrides)
			end
		end
	end)
end

-------------------------------------------------------------------------------
-- Per-frame update
-------------------------------------------------------------------------------
local function updateCamera(s: Session, root: BasePart, dt: number)
	local camera = Workspace.CurrentCamera
	if not camera then
		return
	end
	if camera.CameraType ~= Enum.CameraType.Scriptable then
		camera.CameraType = Enum.CameraType.Scriptable
	end
	local alpha = 1 - math.exp(-dt * 6)
	local lateral = root.Position.X - s.centreX
	s.camLateral += (lateral * 0.55 - s.camLateral) * alpha
	local goalOffset = if s.phase == "prepare" or s.phase == "running" then CAMERA_OFFSET else BOSS_CAMERA_OFFSET
	s.camOffset = s.camOffset:Lerp(goalOffset, alpha * 0.5)
	local focus = Vector3.new(s.centreX + s.camLateral, Layout.FLOOR_Y + 2, root.Position.Z)
	camera.CFrame = CFrame.lookAt(focus + s.camOffset, focus + Vector3.new(0, 0, -CAMERA_LOOK_AHEAD))
end

local function step(dt: number)
	local s = session
	if not s then
		return
	end
	dt = math.min(dt, 0.1)
	if player.Character ~= s.character then
		cleanup() -- respawned
		return
	end
	local root = getRoot(s.character)
	if not root or s.humanoid.Health <= 0 then
		return -- dying: the server switches us back to Base, which cleans up
	end

	-- Watchdog: the server ended our run but we missed the message.
	if State.Mode() ~= "Runner" then
		local since = s.notRunnerSince or os.clock()
		s.notRunnerSince = since
		if os.clock() - since > MODE_GRACE then
			cleanup()
			return
		end
	else
		s.notRunnerSince = nil
	end

	local distance = s.startZ - root.Position.Z
	local lateral = root.Position.X - s.centreX
	local visuals = s.visuals

	if s.phase == "running" then
		-- Steering: keys / stick move the target lane, drags move it directly.
		local input = s.thumbX
		if s.held[Enum.KeyCode.A] or s.held[Enum.KeyCode.Left] or s.held[Enum.KeyCode.DPadLeft] then
			input -= 1
		end
		if s.held[Enum.KeyCode.D] or s.held[Enum.KeyCode.Right] or s.held[Enum.KeyCode.DPadRight] then
			input += 1
		end
		s.targetLateral = math.clamp(s.targetLateral + math.clamp(input, -1, 1) * KEY_STEER_SPEED * dt, -LATERAL_LIMIT, LATERAL_LIMIT)
		local speed = math.max(1, s.runSpeed)
		local sideways = math.clamp((s.targetLateral - lateral) * STEER_GAIN, -speed * 0.9, speed * 0.9)
		s.humanoid:Move(Vector3.new(sideways, 0, -speed).Unit, false)

		-- Rows: shoot at squads/barricades a bit early, record the lane on crossing.
		local rows = s.course.rows
		while s.nextRow <= #rows do
			local index = s.nextRow
			local row = rows[index]
			if (row.kind == "squad" or row.kind == "barricade") and not s.engaged[index] and distance >= row.distance - ENGAGE_RANGE then
				s.engaged[index] = true
				if visuals then
					visuals:Engage(index, laneAt(row, lateral))
				end
			end
			if distance < row.distance then
				break
			end
			crossRow(s, index, laneAt(row, lateral))
		end

		-- Progress bar + stuck check.
		local stopDistance = if visuals then visuals.stopDistance else Layout.RUNNER_TRACK_LENGTH
		if s.hud then
			s.hud.SetProgress(distance / math.max(1, stopDistance))
		end
		if distance > s.lastDistance + 0.5 then
			s.lastDistance = distance
			s.lastProgressClock = os.clock()
		elseif os.clock() - s.lastProgressClock > STUCK_TIME then
			fail(s, "Your run got stuck - please try again")
			return
		end
		if distance >= stopDistance then
			beginBoss(s, lateral)
		end
	else
		s.humanoid:Move(Vector3.zero, false)
	end

	if visuals then
		visuals:Update(dt, root, s.phase == "running")
	end
	updateCamera(s, root, dt)
end

-------------------------------------------------------------------------------
-- Input
-------------------------------------------------------------------------------
local STEER_KEYS: { [Enum.KeyCode]: boolean } = {
	[Enum.KeyCode.A] = true,
	[Enum.KeyCode.D] = true,
	[Enum.KeyCode.Left] = true,
	[Enum.KeyCode.Right] = true,
	[Enum.KeyCode.DPadLeft] = true,
	[Enum.KeyCode.DPadRight] = true,
}

local function connectInput(s: Session)
	local function viewportWidth(): number
		local camera = Workspace.CurrentCamera
		return if camera then math.max(200, camera.ViewportSize.X) else 1000
	end

	table.insert(
		s.connections,
		UserInputService.InputBegan:Connect(function(input: InputObject, processed: boolean)
			if session ~= s then
				return
			end
			if STEER_KEYS[input.KeyCode] then
				if processed and input.UserInputType == Enum.UserInputType.Keyboard then
					return -- typing in chat
				end
				s.held[input.KeyCode] = true
				requestGo(s)
			elseif
				not processed
				and (input.UserInputType == Enum.UserInputType.MouseButton1 or input.UserInputType == Enum.UserInputType.Touch)
				and s.dragInput == nil
			then
				s.dragInput = input
				s.dragLastX = input.Position.X
				s.dragTotal = 0
			end
		end)
	)

	table.insert(
		s.connections,
		UserInputService.InputChanged:Connect(function(input: InputObject)
			if session ~= s then
				return
			end
			if input.KeyCode == Enum.KeyCode.Thumbstick1 then
				local x = input.Position.X
				s.thumbX = if math.abs(x) > 0.2 then x else 0
				if math.abs(x) > 0.5 then
					requestGo(s)
				end
				return
			end
			local drag = s.dragInput
			if not drag then
				return
			end
			local isMouseDrag = drag.UserInputType == Enum.UserInputType.MouseButton1
				and input.UserInputType == Enum.UserInputType.MouseMovement
			if input == drag or isMouseDrag then
				local dx = input.Position.X - s.dragLastX
				s.dragLastX = input.Position.X
				s.dragTotal += math.abs(dx)
				if s.phase == "prepare" then
					if s.dragTotal >= DRAG_START_PIXELS then
						requestGo(s)
					end
				elseif s.phase == "running" then
					-- Dragging across ~60% of the screen sweeps the whole track.
					local studsPerPixel = Layout.RUNNER_TRACK_WIDTH / (viewportWidth() * 0.6)
					s.targetLateral = math.clamp(s.targetLateral + dx * studsPerPixel, -LATERAL_LIMIT, LATERAL_LIMIT)
				end
			end
		end)
	)

	table.insert(
		s.connections,
		UserInputService.InputEnded:Connect(function(input: InputObject)
			if session ~= s then
				return
			end
			if STEER_KEYS[input.KeyCode] then
				s.held[input.KeyCode] = nil
			end
			local drag = s.dragInput
			if drag and (input == drag or (input.UserInputType == Enum.UserInputType.MouseButton1 and drag.UserInputType == Enum.UserInputType.MouseButton1)) then
				s.dragInput = nil
			end
		end)
	)
end

-------------------------------------------------------------------------------
-- Session start
-------------------------------------------------------------------------------
local function startSession(reply: { [string]: any })
	local course = reply.course
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	local valid = type(reply.runId) == "string"
		and type(course) == "table"
		and type(course.rows) == "table"
		and type(course.boss) == "table"
		and type(reply.slot) == "number"
	if not valid or not character or not humanoid then
		toast("Could not start the run, please try again", "error")
		task.spawn(Net.Request, "RunnerLeave")
		return
	end

	local slot = reply.slot :: number
	local fallbackStats = { startTroops = 1, damage = 1, capacity = 0 }
	local stats = cleanStats(reply.stats, fallbackStats)
	local s: Session = {
		runId = reply.runId :: string,
		course = course :: Course,
		level = numberOr(reply.level, 1),
		slot = slot,
		centreX = Layout.RunnerPoint(slot, 0, 0).X,
		startZ = Layout.RunnerPoint(slot, 0, 0).Z,
		stats = stats,
		previewStats = { startTroops = stats.startTroops, damage = stats.damage, capacity = stats.capacity },
		runSpeed = numberOr(reply.runSpeed, DEFAULT_RUN_SPEED),
		phase = "prepare",
		choices = {},
		nextRow = 1,
		engaged = {},
		live = { troops = stats.startTroops, cash = 0, tier = 1 },
		targetLateral = 0,
		camLateral = 0,
		camOffset = CAMERA_OFFSET,
		startClock = os.clock(),
		lastDistance = 0,
		lastProgressClock = os.clock(),
		notRunnerSince = nil,
		character = character,
		humanoid = humanoid,
		connections = {},
		visuals = nil,
		hud = nil,
		prepare = nil,
		closeSummary = nil,
		goPending = false,
		buying = {},
		levelOverrides = {},
		dragInput = nil,
		dragLastX = 0,
		dragTotal = 0,
		held = {},
		thumbX = 0,
	}
	session = s

	local ok, err = pcall(function()
		local visuals = RunnerVisuals.new(s.course, slot)
		s.visuals = visuals
		visuals:SetSquad(stats.startTroops, 0, stats.capacity, 1)
		s.hud = RunnerUI.ShowHud(s.level)
		local data = State.Get()
		if data then
			s.prepare = RunnerUI.ShowPrepare({
				Data = data,
				Stats = s.previewStats,
				OnBuy = function(kind: string)
					buyUpgrade(s, kind)
				end,
				OnBack = function()
					leave(s)
				end,
			})
		end
	end)
	if not ok then
		warn("[RunnerController] building the run failed: " .. tostring(err))
		fail(s, "Could not build the run, please try again")
		return
	end

	-- Take over the avatar and camera.
	setControlsEnabled(false)
	humanoid:SetStateEnabled(Enum.HumanoidStateType.Jumping, false)
	local camera = Workspace.CurrentCamera
	if camera then
		camera.CameraType = Enum.CameraType.Scriptable
	end
	RunService:BindToRenderStep(RENDER_NAME, Enum.RenderPriority.Camera.Value + 1, step)
	connectInput(s)

	table.insert(
		s.connections,
		State.Changed:Connect(function(data: PlayerData)
			if session == s and s.prepare then
				s.prepare.Update(data, s.previewStats, s.levelOverrides)
			end
		end)
	)
	table.insert(
		s.connections,
		humanoid.Died:Connect(function()
			if session == s then
				cleanup()
			end
		end)
	)
	-- If the player was already gone by the time controls were disabled.
	if session ~= s then
		cleanup()
	end
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function RunnerController:OpenPrepare()
	if session or opening then
		return
	end
	if State.Mode() ~= "Base" then
		toast("Finish what you are doing first!", "error")
		return
	end
	if not State.Get() then
		toast("Still loading your data...", "info")
		return
	end
	opening = true
	getControls() -- make sure the control module is ready before we need it
	local reply = Net.Request("RunnerPrepare")
	opening = false
	if not reply.ok then
		toast(reply.err or "Could not start a run", "error")
		return
	end
	if session then
		return
	end
	startSession(reply)
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function RunnerController:Init(reg: { [string]: any })
	registry = reg
end

function RunnerController:Start()
	task.spawn(getControls)

	local hud = registry.HUDController
	if hud and hud.AddMenuButton then
		hud:AddMenuButton({
			Id = "Run",
			Title = "Army Run",
			Icon = "🪖",
			Order = 1,
			OnClick = function()
				task.spawn(function()
					RunnerController:OpenPrepare()
				end)
			end,
		})
	end

	-- Any mode other than Runner ends the local session (death, leaving...).
	State.ModeChanged:Connect(function(newMode: string)
		if session and newMode ~= "Runner" then
			cleanup()
		end
	end)
	Net.On("Runner", function(msg: string, payload: any)
		local s = session
		if msg == "Ended" and s and type(payload) == "table" and payload.runId == s.runId then
			cleanup()
		end
	end)
	player.CharacterAdded:Connect(function()
		if session then
			cleanup()
		end
	end)

	-- ARMY RUN portal pad in area 1 (BASE builds the pad, we detect the player).
	local portal = Layout.RunnerPortalPosition()
	local accumulator = 0
	RunService.Heartbeat:Connect(function(dt: number)
		accumulator += dt
		if accumulator < 0.2 then
			return
		end
		accumulator = 0
		if session or opening or State.Mode() ~= "Base" then
			portalArmed = false
			return
		end
		local root = getRoot(player.Character)
		if not root then
			return
		end
		local offset = root.Position - portal
		local flat = Vector2.new(offset.X, offset.Z).Magnitude
		if flat > PORTAL_RADIUS or math.abs(offset.Y) > 12 then
			portalArmed = true -- must step off and back on to trigger again
		elseif portalArmed and os.clock() >= portalCooldownUntil then
			portalArmed = false
			portalCooldownUntil = os.clock() + PORTAL_COOLDOWN
			task.spawn(function()
				RunnerController:OpenPrepare()
			end)
		end
	end)
end

return RunnerController
