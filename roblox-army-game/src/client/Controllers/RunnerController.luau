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
	                The server put a "RunnerGun" Tool in the avatar's hand;
	                RunnerVisuals welds the gun model of the current weapon to it.
	                Squads / barricades / shoot-to-grow gates within
	                RunnerDefs.GUN_RANGE (1.5 rows) are shot at: we predict that
	                row with RunnerSim.Resolve (lanes + wide-shooting stances so far
	                + the lane and RunnerSim.Stance we are in now) so enemies fall,
	                HP drains and gates grow in BOTH lanes exactly as the sim says.
	                When the avatar crosses a row we record its lane and lateral
	                position (the stance) and replay the run so far.
	                Live stats sit in the BOTTOM-LEFT panel (RunnerUI HUD), clear
	                of Roblox's chat window (top-left).
	  3. BOSS     - the avatar stops before the castle. Right away we send our
	                lanes + laterals to the server (RunnerFinish, ONE request, never
	                before RunnerSim.MinDuration + FINISH_SLACK); the server
	                re-simulates and pays. Meanwhile the castle door opens and
	                the boss walks out (RunnerDefs.BOSS_INTRO_SECONDS), which
	                covers the round trip. The fight then plays the SERVER's
	                BossOutcome (reply.result.boss) exactly (its HP bar follows
	                BossHpAt, a unit dies every interval: troops first, the
	                avatar last), so what you see always matches what is paid.
	                Only if the request fails (or has no reply FINISH_REPLY_WAIT s
	                after the intro) do we play our own RunnerSim.Resolve.
	  4. SUMMARY  - "RUN COMPLETE!" panel from the reply; COLLECT returns to the
	                base (the server puts us at the spawn, away from the ARMY RUN
	                pad). After a LOST run the level stays (owner request) and the
	                panel offers TRY AGAIN: RunnerRetry starts the same level on
	                the track right away.

	Cleanup (camera, controls, jump, visuals incl. guns / boss / bullets /
	avatar pose, HUD panel, messages, input) happens on COLLECT, Back, any
	failed request, death/respawn, or the server leaving Runner mode. The
	server destroys the RunnerGun Tool when its session ends.

	Portal pad: walking onto it starts OpenPrepare, but never while a Theme
	dialog is open (Theme.IsModalOpen) - step off and back on after closing it.
	When the run starts, HUDController closes the open "Base" dialogs
	(Theme.CloseModals("Base")); the run's own screens are not "Base" dialogs
	(the pre-run screen is not a panel, the summary panel has no Context), so
	they are never closed by that.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Format = require(Shared.Format)
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
type BossOutcome = RunnerSim.BossOutcome
type Option = RunnerCourse.Option
type Fight = RunnerVisuals.Fight
type Engagement = RunnerVisuals.Engagement
type PlayerData = Types.PlayerData

local player = Players.LocalPlayer
local P = Config.Palette

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
local BOSS_CAMERA_OFFSET = Vector3.new(0, 26, 48) -- pulled back: 8 troop rows + the boss + its HP bar
local CAMERA_LOOK_AHEAD = 20
local GUN_RANGE = RunnerDefs.GUN_RANGE -- guns reach 1.5 rows: squads/barricades are shot from here
local FINISH_SLACK = 0.75 -- seconds past RunnerSim.MinDuration before we report the finish
local FINISH_REPLY_WAIT = 6 -- [PROPOSAL] seconds after the boss intro we wait for RunnerFinish before playing our own prediction
local FINISH_GIVE_UP = 15 -- [PROPOSAL] seconds after the fight with still no RunnerFinish reply: end the run
local BOSS_SAFETY = 4 -- [PROPOSAL] seconds past the planned boss fight before we finish anyway
local STUCK_TIME = 6 -- [PROPOSAL] no forward progress for this long = give up the run
local MODE_GRACE = 3 -- seconds we tolerate "not in Runner mode" before cleaning up
local RENDER_NAME = "ArmyRunnerStep"
local DEFAULT_RUN_SPEED = 24

-------------------------------------------------------------------------------
-- State
-------------------------------------------------------------------------------
type Live = { troops: number, cash: number, tier: number, dmg: number }

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
	phase: string, -- "prepare" | "running" | "boss" | "summary" | "ended"
	choices: { number },
	laterals: { number }, -- row index -> studs right of the centre line where we crossed it
	stances: { number }, -- row index -> RunnerSim.Stance at that crossing
	nextRow: number,
	engageKeys: { [number]: string }, -- row index -> prediction key last sent to the visuals
	engagements: { [number]: Engagement }, -- row index -> the engagement last sent (split-fire hint)
	live: Live,
	targetLateral: number,
	camLateral: number,
	camOffset: Vector3,
	camFocus: Vector3?,
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
	closeMessage: (() -> ())?,
	goPending: boolean,
	buying: { [string]: boolean },
	levelOverrides: { [string]: number },
	dragInput: InputObject?,
	dragLastX: number,
	dragTotal: number,
	held: { [Enum.KeyCode]: boolean },
	thumbX: number,
	-- Boss phase: one RunnerFinish request, sent on arrival at the castle.
	localResult: Result?, -- our own RunnerSim.Resolve (fallback only)
	finishSent: boolean,
	finishDone: boolean, -- the request returned (ok or not)
	finishReply: { [string]: any }?, -- ok reply: { result, paid, boosted, level }
	finishError: string?,
	fightStarted: boolean,
	fightDone: boolean,
}

local RunnerController = {}

local registry: { [string]: any } = {}
local session: Session? = nil
local opening = false
local portalArmed = false
local portalCooldownUntil = 0
local controlsCache: any = nil
local startSessionRef: ((reply: { [string]: any }) -> ())? = nil -- startSession (defined further down)

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
	local ok, result = pcall(RunnerSim.Resolve, s.course, s.stats, s.choices, s.stances)
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

local function isFightRow(row: Row): boolean
	return row.kind == "squad" or row.kind == "barricade" or row.kind == "contest"
end

-- What a squad / barricade / shoot-to-grow row looks like for the visuals in
-- EVERY lane (wide shooting hits the other lane too), straight from the row's
-- RunnerSim outcome. Visual only: troops / cash / destroyed always come from
-- the outcome itself.
local function engagementFor(row: Row, lane: number, outcome: RowOutcome?): Engagement?
	if not outcome or not isFightRow(row) then
		return nil
	end
	local fights: { [number]: Fight } = {}
	for index, option in row.options do
		local l = if type(option.lane) == "number" then option.lane else index
		local own = l == lane
		local damage = numberOr(if own then outcome.damage else outcome.otherDamage, 0)
		if row.kind == "squad" then
			local enemies = math.max(0, math.floor(numberOr(option.enemies, 0)))
			local kills = math.clamp(math.floor(numberOr(if own then outcome.kills else outcome.otherKills, 0)), 0, enemies)
			fights[l] = {
				enemies = enemies,
				kills = kills,
				share = if enemies > 0 then kills / enemies else 1,
				destroyed = kills >= enemies,
			}
		elseif row.kind == "barricade" then
			local destroyed = if own then outcome.destroyed == true else outcome.otherDestroyed == true
			local hp = math.max(1e-6, numberOr(option.hp, 1))
			fights[l] = {
				enemies = 0,
				kills = 0,
				share = if destroyed then 1 else math.clamp(damage / hp, 0, 1),
				destroyed = destroyed,
			}
		else
			local steps = math.max(0, math.floor(numberOr(if own then outcome.steps else outcome.otherSteps, 0)))
			local amount = numberOr(if own then outcome.amount else outcome.otherAmount, numberOr(option.amount, 0))
			fights[l] = {
				enemies = 0,
				kills = 0,
				share = steps / math.max(1, numberOr(option.growMax, 1)),
				destroyed = false,
				steps = steps,
				amount = amount,
			}
		end
	end
	return {
		lane = lane,
		stance = math.max(0, math.floor(numberOr(outcome.stance, 0))),
		across = math.max(0, math.floor(numberOr(outcome.across, 0))),
		fights = fights,
	}
end

local function unitDps(s: Session, tier: number, dmg: number): number
	local ok, dps = pcall(RunnerSim.UnitDps, s.stats, tier, dmg)
	return if ok and type(dps) == "number" then dps else 0
end

-- Bottom-left HUD panel: troops (true count), weapon, squad DPS, carried cash.
local function updateHud(s: Session, troopsOverride: number?)
	local hud = s.hud
	if not hud then
		return
	end
	local troops = troopsOverride or s.live.troops
	local tier = math.clamp(math.floor(s.live.tier), 1, RunnerDefs.MAX_WEAPON_TIER)
	hud.SetStats({
		troops = troops,
		weaponName = RunnerDefs.WEAPONS[tier].name,
		weaponTier = tier,
		maxTier = RunnerDefs.MAX_WEAPON_TIER,
		dps = troops * unitDps(s, tier, s.live.dmg),
		cash = s.live.cash,
		capacity = s.stats.capacity,
	})
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
		if s.closeMessage then
			s.closeMessage()
			s.closeMessage = nil
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
local function crossRow(s: Session, index: number, lane: number, lateral: number)
	local crossed = s.course.rows[index]
	s.choices[index] = lane
	s.engagements[index] = nil
	s.laterals[index] = lateral
	s.stances[index] = if crossed then RunnerSim.Stance(crossed, lane, lateral) else 0
	s.nextRow = index + 1
	s.engageKeys[index] = nil
	local result = safeResolve(s)
	local outcome = if result then findOutcome(result, index) else nil
	local row = s.course.rows[index]
	local visuals = s.visuals
	local cashBefore = s.live.cash
	if visuals then
		visuals:RowCrossed(index, lane, outcome, s.stats.capacity, engagementFor(row, lane, outcome))
	end
	if outcome then
		s.live = {
			troops = outcome.troopsAfter,
			cash = outcome.cashAfter,
			tier = outcome.weaponTier,
			dmg = outcome.damageMult,
		}
	end
	if visuals then
		visuals:SetSquad(s.live.troops, s.live.tier)
	end
	updateHud(s)
	if s.hud and s.live.cash > cashBefore + 0.5 then
		s.hud.PopCash()
	end
end

-- Squads / barricades / shoot-to-grow gates within gun range: tell the
-- visuals what the fight will look like (in both lanes) if we stay where we
-- are now. Re-predicted only when a relevant lane or stance (or the last
-- crossed row) changes.
local function updateEngagements(s: Session, distance: number, lateral: number)
	local visuals = s.visuals
	if not visuals then
		return
	end
	local rows = s.course.rows
	local lanes: { number } = {}
	local stances: { number } = {}
	local key = tostring(s.nextRow)
	for index = s.nextRow, #rows do
		local row = rows[index]
		if distance < row.distance - GUN_RANGE then
			break
		end
		local lane = laneAt(row, lateral)
		local stance = RunnerSim.Stance(row, lane, lateral)
		lanes[index] = lane
		stances[index] = stance
		key ..= ":" .. lane .. "/" .. stance
		if isFightRow(row) and s.engageKeys[index] ~= key then
			s.engageKeys[index] = key
			local predicted = table.clone(s.choices)
			local predictedStances = table.clone(s.stances)
			for j = s.nextRow, index do
				predicted[j] = lanes[j]
				predictedStances[j] = stances[j]
			end
			local ok, result = pcall(RunnerSim.Resolve, s.course, s.stats, predicted, predictedStances)
			local outcome = if ok and type(result) == "table" then findOutcome(result, index) else nil
			local engagement = engagementFor(row, lane, outcome)
			if engagement then
				s.engagements[index] = engagement
				visuals:Engage(index, engagement)
			end
		end
	end
	-- Wide shooting is a trade-off (owner request: the middle hits both sides,
	-- a side gets more damage), so say when fire is being split.
	local nearest: Engagement? = nil
	for index = s.nextRow, #rows do
		local engagement = s.engagements[index]
		if engagement then
			nearest = engagement
			break
		end
		if distance < rows[index].distance - GUN_RANGE then
			break
		end
	end
	if s.hud then
		s.hud.SetNote(if nearest and nearest.across > 0
			then ("🎯 SPLIT FIRE: %d shoot the other side"):format(nearest.across)
			else nil)
	end
end

local function bossReward(result: Result): number
	for _, line in result.lines do
		if line.kind == "boss" then
			return line.value
		end
	end
	return 0
end

-- The summary appears once BOTH the boss fight has played (fightDone) and the
-- RunnerFinish reply is in (finishDone). It always shows the server's result.
-- TRY AGAIN after a lost run: the server ends this run and starts the same
-- level on the track (RunnerRetry); we then build the new run like a fresh one.
local function retry(s: Session)
	if session ~= s or opening then
		return
	end
	opening = true
	task.spawn(function()
		local reply = Net.Request("RunnerRetry", s.runId)
		opening = false
		if session ~= s then
			return
		end
		if not reply.ok then
			toast(reply.err or "Could not start the run", "error")
			leave(s)
			return
		end
		cleanup() -- the old run's visuals, HUD and input
		local start = startSessionRef
		if start and not session then
			start(reply)
		end
	end)
end

local function tryShowSummary(s: Session)
	if session ~= s or s.phase ~= "boss" or not s.fightDone or not s.finishDone then
		return
	end
	local reply = s.finishReply
	if not reply then
		fail(s, s.finishError or "Could not finish the run")
		return
	end
	s.phase = "summary"
	if s.closeMessage then
		s.closeMessage()
		s.closeMessage = nil
	end
	if s.hud then
		s.hud.SetStatsVisible(false) -- the summary lists everything; keep the screen clear
	end
	local result = reply.result :: Result
	local paid = numberOr(reply.paid, 0)
	local lost = type(result.boss) ~= "table" or result.boss.victory ~= true
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
			toast("💡 Spend your cash on base unlocks - or press 🪖 Army Run to play again", "info")
		end,
		-- A lost level is played again until it is won (owner request).
		OnRetry = if lost
			then function()
				s.closeSummary = nil
				retry(s)
			end
			else nil,
	})
end

local function markFightDone(s: Session)
	if session ~= s or s.fightDone then
		return
	end
	s.fightDone = true
	tryShowSummary(s)
	-- The reply never came: give up instead of waiting forever.
	task.delay(FINISH_GIVE_UP, function()
		if session == s and s.phase == "boss" and not s.finishDone then
			fail(s, "Could not finish the run")
		end
	end)
end

-- Plays the boss fight with the SERVER's outcome (RunnerFinish reply), so the
-- screen always matches the payout. Only when the request failed (or is far
-- too slow) does it fall back to our own RunnerSim.Resolve prediction.
local function startFight(s: Session)
	if session ~= s or s.phase ~= "boss" or s.fightStarted then
		return
	end
	s.fightStarted = true
	local reply = s.finishReply
	local result: Result? = if reply then reply.result :: Result else s.localResult
	local outcome: BossOutcome? = if result and type(result.boss) == "table" then result.boss else nil
	local visuals = s.visuals
	if not result or not outcome or not visuals then
		markFightDone(s)
		return
	end
	local fightResult: Result = result
	local fightOutcome: BossOutcome = outcome
	local tier = s.live.tier
	local dmg = s.live.dmg
	local total = visuals:StartBossFight(fightOutcome, {
		onProgress = function(alive: number, _hp: number)
			if session == s then
				updateHud(s, alive)
			end
		end,
		onResolved = function(victory: boolean)
			if session ~= s then
				return
			end
			s.live = { troops = fightOutcome.survivors, cash = s.live.cash, tier = tier, dmg = dmg }
			updateHud(s)
			if s.closeMessage then
				s.closeMessage()
			end
			if victory then
				s.closeMessage = RunnerUI.ShowMessage(
					"BOSS DEFEATED!",
					"+" .. Format.Money(bossReward(fightResult)) .. " boss reward",
					P.Coin,
					3
				)
			else
				s.closeMessage =
					RunnerUI.ShowMessage("DEFEATED BY THE BOSS", "Pick the best lanes to beat it", P.ButtonRed, 3.2)
			end
		end,
		onDone = function()
			markFightDone(s)
		end,
	})
	-- Safety net: finish even if the visuals stopped updating.
	task.delay(total + BOSS_SAFETY, function()
		markFightDone(s)
	end)
end

-- Sends RunnerFinish exactly ONCE, as soon as the squad reaches the castle
-- (the boss intro covers the round trip). The server rejects runs faster
-- than RunnerSim.MinDuration x 0.9; we never report before MinDuration +
-- FINISH_SLACK (arriving at the castle normally takes longer anyway).
local function sendFinish(s: Session)
	if session ~= s or s.finishSent then
		return
	end
	s.finishSent = true
	local okMin, minDuration = pcall(RunnerSim.MinDuration, s.course)
	if okMin and type(minDuration) == "number" then
		local wait = minDuration + FINISH_SLACK - (os.clock() - s.startClock)
		if wait > 0 then
			task.wait(wait)
			if session ~= s then
				return
			end
		end
	end
	local reply = Net.Request("RunnerFinish", s.runId, s.choices, s.laterals)
	if session ~= s then
		return
	end
	if reply.ok and type(reply.result) == "table" then
		s.finishReply = reply
	else
		s.finishError = if reply.ok then "Could not finish the run" else reply.err
	end
	s.finishDone = true
	if s.fightStarted then
		tryShowSummary(s) -- fight already playing (slow reply) or done
	else
		startFight(s)
	end
end

local function beginBoss(s: Session, lateral: number)
	s.phase = "boss"
	-- Any row we somehow skipped counts with the lane we are in now.
	local rows = s.course.rows
	for index = s.nextRow, #rows do
		crossRow(s, index, laneAt(rows[index], lateral), lateral)
	end
	s.humanoid:Move(Vector3.zero, false)
	if s.hud then
		s.hud.SetProgress(1)
		s.hud.SetNote(nil)
	end
	-- Our own prediction: the boss's max HP for the intro (the same number the
	-- server uses: same course + stats) and the fallback if RunnerFinish fails.
	local predicted = safeResolve(s)
	s.localResult = predicted
	local intro = 0
	local visuals = s.visuals
	if visuals then
		local hp = if predicted and type(predicted.boss) == "table" then numberOr(predicted.boss.hp, 0) else 0
		intro = visuals:OpenCastle(hp)
	end
	-- We are inside the RenderStep callback and sendFinish yields: spawn it.
	task.spawn(sendFinish, s)
	-- Reply far too slow: play our prediction rather than a frozen boss.
	task.delay(intro + FINISH_REPLY_WAIT, function()
		startFight(s)
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
		-- Boss HP is an exact search over the whole course (memoized per
		-- course + stats): compute it now so the first row crossing has no hitch.
		pcall(RunnerSim.BossHp, s.course, s.stats)
		s.phase = "running"
		s.startClock = os.clock()
		s.lastProgressClock = os.clock()
		s.live = { troops = s.stats.startTroops, cash = 0, tier = 1, dmg = 1 }
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
			s.visuals:SetSquad(s.live.troops, 1)
		end
		if s.hud then
			s.hud.SetStatsVisible(true)
		end
		updateHud(s)
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
				s.visuals:SetSquad(s.previewStats.startTroops, 1)
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
	local running = s.phase == "prepare" or s.phase == "running"
	-- Boss fight: frame the squad and the boss (focus between them, centred).
	local bossAt = if running or not s.visuals then nil else s.visuals:BossFocus()
	s.camLateral += ((if bossAt then 0 else lateral * 0.55) - s.camLateral) * alpha
	s.camOffset = s.camOffset:Lerp(if running then CAMERA_OFFSET else BOSS_CAMERA_OFFSET, alpha * 0.5)
	local focusZ = if bossAt then root.Position.Z + (bossAt.Z - root.Position.Z) * 0.4 else root.Position.Z
	local goal = Vector3.new(s.centreX + s.camLateral, Layout.FLOOR_Y + 2, focusZ)
	local focus = if s.camFocus then s.camFocus:Lerp(goal, alpha) else goal
	s.camFocus = focus
	local lookAt = if bossAt then focus + Vector3.new(0, 1, -1) else focus + Vector3.new(0, 0, -CAMERA_LOOK_AHEAD)
	camera.CFrame = CFrame.lookAt(focus + s.camOffset, lookAt)
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
		s.targetLateral =
			math.clamp(s.targetLateral + math.clamp(input, -1, 1) * KEY_STEER_SPEED * dt, -LATERAL_LIMIT, LATERAL_LIMIT)
		local speed = math.max(1, s.runSpeed)
		local sideways = math.clamp((s.targetLateral - lateral) * STEER_GAIN, -speed * 0.9, speed * 0.9)
		s.humanoid:Move(Vector3.new(sideways, 0, -speed).Unit, false)

		-- Rows: record the lane on crossing, then shoot at what is in range.
		local rows = s.course.rows
		while s.nextRow <= #rows and distance >= rows[s.nextRow].distance do
			local index = s.nextRow
			crossRow(s, index, laneAt(rows[index], lateral), lateral)
		end
		updateEngagements(s, distance, lateral)

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
			if
				drag
				and (
					input == drag
					or (
						input.UserInputType == Enum.UserInputType.MouseButton1
						and drag.UserInputType == Enum.UserInputType.MouseButton1
					)
				)
			then
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
		laterals = {},
		stances = {},
		nextRow = 1,
		engageKeys = {},
		engagements = {},
		live = { troops = stats.startTroops, cash = 0, tier = 1, dmg = 1 },
		targetLateral = 0,
		camLateral = 0,
		camOffset = CAMERA_OFFSET,
		camFocus = nil,
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
		closeMessage = nil,
		goPending = false,
		buying = {},
		levelOverrides = {},
		dragInput = nil,
		dragLastX = 0,
		dragTotal = 0,
		held = {},
		thumbX = 0,
		localResult = nil,
		finishSent = false,
		finishDone = false,
		finishReply = nil,
		finishError = nil,
		fightStarted = false,
		fightDone = false,
	}
	session = s

	local ok, err = pcall(function()
		local visuals = RunnerVisuals.new(s.course, slot, character)
		s.visuals = visuals
		visuals:SetSquad(stats.startTroops, 1)
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

startSessionRef = startSession

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
		elseif Theme.IsModalOpen() then
			-- Looking at a dialog (shop, purchase, ...): never yank the player
			-- into a run. Step off and back on once the dialog is closed.
			portalArmed = false
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
