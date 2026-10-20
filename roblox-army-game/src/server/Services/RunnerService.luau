--!strict
--[[
	RunnerService (server)
	The server half of the ARMY RUNNER game mode (docs/ARCHITECTURE.md §6.1).

	What it does:
	  1. At Start it builds the static runner tracks in workspace.RunnerTracks:
	     one long white track per player slot (Layout.RunnerTrackStart(slot)),
	     with light-blue cross stripes, low side rails, tall INVISIBLE walls so
	     nobody can fall into the sea, a start platform and a finish plaza where
	     the client draws the enemy base.
	  2. It runs one run "session" per player and is the authority on money:
	     the client only reports which lane it picked in each row, the server
	     re-simulates the whole run with RunnerSim.Resolve and pays the result.
	  3. While a run is going it watches the avatar every Heartbeat and notes
	     which lane it was really in as it crossed each row. RunnerFinish only
	     believes the client's lane when it matches what the server saw (or is
	     the next lane over and the avatar was within LANE_EDGE_TOLERANCE studs
	     of it - the server sees the avatar 0.1-0.3 s late), so a cheater can
	     not simply send the best lanes.

	Net actions (client -> server, see ARCHITECTURE §3):
		RunnerPrepare()               -> { runId, course, level, area, slot, stats, runSpeed }
		BuyRunnerUpgrade(kind)        -> { kind, level, stats }   kind = "troops" | "damage" | "capacity"
		RunnerGo(runId)               -> { stats, runSpeed, bossHp }
		RunnerFinish(runId, choices)  -> { result, paid, boosted, level }
		RunnerLeave()                 -> {}

	Push channel "Runner" (server -> client):
		"Ended", { runId }   -- the server ended the session (death, mode change)

	Public API (other services): none needed. The session table is private.

	Flow of one run:
		RunnerPrepare  - player must be in Base mode with loaded data. Generates the
		                 course, sets mode "Runner", teleports to the track start and
		                 freezes the avatar (WalkSpeed 0, no jumping).
		BuyRunnerUpgrade - permanent upgrades; allowed in Base mode, or in Runner
		                 mode before the run has started.
		RunnerGo       - locks the stats (plus the vehicle bonus) and the start time,
		                 sets WalkSpeed = RUN_SPEED, starts watching the lanes and
		                 puts the "RunnerGun" Tool in the avatar's hand (an invisible
		                 Handle the client welds the weapon model to; the standard
		                 tool-hold arm pose comes from the default Animate script).
		                 bossHp = RunnerSim.BossHp(course, stats) for the client.
		RunnerFinish   - sent ONCE by the client as soon as the squad reaches the
		                 castle, during the boss intro, and never before
		                 RunnerSim.MinDuration + a little slack on the client's
		                 clock (which starts later than ours), so the
		                 elapsed >= MinDuration x 0.9 check passes. The client then
		                 plays the boss fight from reply.result.boss, so the fight
		                 on screen is the one that is paid.
		                 anti-cheat time + position checks (fails if the avatar
		                 is missing), picks the lanes (client's if plausible, else
		                 the ones the server saw), re-simulates, pays boostable
		                 cash, raises runner.level. A repeated request gets the
		                 same reply again (never pays twice).
		RunnerLeave    - restores the avatar and returns to the base.

	Every way a session ends (RunnerLeave, death / respawn / any mode change,
	leaving the game, a failed teleport) goes through endSession, which stops
	the lane watcher, restores the avatar and destroys the RunnerGun Tool.
	Money: RunnerSim pays whole dollars; DataService:AddCash rounds as well.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local StarterPlayer = game:GetService("StarterPlayer")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Net = require(Shared.Net)
local RunnerCourse = require(Shared.Runner.RunnerCourse)
local RunnerDefs = require(Shared.Runner.RunnerDefs)
local RunnerSim = require(Shared.Runner.RunnerSim)

type Course = RunnerCourse.Course
type Row = RunnerCourse.Row
type Option = RunnerCourse.Option
type Stats = RunnerSim.Stats
type Result = RunnerSim.Result

local P = Config.Palette

local RunnerService = {}
RunnerService.Priority = 10

-------------------------------------------------------------------------------
-- Tunables
-------------------------------------------------------------------------------
local DEFAULT_RUN_SPEED = 24 -- used only if RunnerDefs.RUN_SPEED is missing
local MIN_DURATION_SLACK = 0.9 -- [REQUIREMENT ARCH §6.1] reject runs faster than MinDuration x 0.9
local FINISH_DISTANCE_SHARE = 0.6 -- [PROPOSAL] must have run at least 60% of the way to the boss
local TRACK_LATERAL_LIMIT = 45 -- [PROPOSAL] studs from the track centre still counted "on the track"
-- [PROPOSAL] The server sees the avatar 0.1-0.3 s late. A lane the client
-- reports is still accepted when it is next to the lane the server saw and
-- the avatar was at most this many studs outside it (sideways speed is at
-- most ~0.9 x RUN_SPEED, so 3 studs covers ~0.15 s of steering at full tilt,
-- plus the 1-stud gaps between lanes).
local LANE_EDGE_TOLERANCE = 3
local STRIPE_SPACING = 20 -- light-blue cross stripe every 20 studs (image 2)
local STRIPE_LENGTH = 9
local START_PLATFORM_LENGTH = 26
local PLAZA_LENGTH = 80
local PLAZA_WIDTH = 60
local WALL_HEIGHT = 40
local UPGRADE_KINDS = { troops = true, damage = true, capacity = true }
local GUN_NAME = "RunnerGun" -- RunnerVisuals looks for this Tool in the character

-- RUN_SPEED comes from ECON's RunnerDefs (fallback if the value is missing).
local function loadRunSpeed(): number
	local speed = (RunnerDefs :: any).RUN_SPEED
	if type(speed) == "number" and speed > 0 then
		return speed
	end
	return DEFAULT_RUN_SPEED
end
local RUN_SPEED = loadRunSpeed()

-------------------------------------------------------------------------------
-- State
-------------------------------------------------------------------------------
type Session = {
	runId: string,
	course: Course,
	level: number,
	area: number,
	slot: number,
	character: Model,
	started: boolean,
	startTime: number,
	stats: Stats?, -- locked at RunnerGo
	bossHp: number?, -- RunnerSim.BossHp(course, stats), computed at RunnerGo
	finished: boolean,
	reply: { [string]: any }?, -- cached RunnerFinish reply (safe to resend)
	sampler: RBXScriptConnection?, -- Heartbeat lane watcher (RunnerGo .. endSession / finish)
	serverChoices: { [number]: number }, -- row index -> lane the server saw the avatar in
	serverLaterals: { [number]: number }, -- row index -> lateral offset (studs) at that moment
	nextSampleRow: number, -- first row the avatar has not crossed yet (server view)
	lastTravelled: number?, -- latest sample: studs down the track
	lastLateral: number?, -- latest sample: studs right of the centre line
	originalWalkSpeed: number,
	originalJumpHeight: number,
	originalJumpPower: number,
	gun: Tool?, -- the RunnerGun in the avatar's hand (RunnerGo .. endSession)
	gunWatch: RBXScriptConnection?,
}

local registry: { [string]: any } = {}
local sessions: { [Player]: Session } = {}
local runCounter = 0

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function getHumanoid(character: Model?): Humanoid?
	if not character then
		return nil
	end
	return character:FindFirstChildOfClass("Humanoid")
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

local function isFiniteNumber(value: any): boolean
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

-- Copy the stats table so later upgrades never change a run in progress.
local function copyStats(stats: { startTroops: number, damage: number, capacity: number }, bonus: number?): Stats
	return {
		startTroops = stats.startTroops,
		damage = stats.damage,
		capacity = stats.capacity,
		payoutBonus = bonus,
	}
end

local function currentStats(data: any): { startTroops: number, damage: number, capacity: number }
	return Economy.RunnerStats(data.runner.upgrades, data.base.highestArea)
end

-- Vehicle runner bonus (SHOP). Missing service / bad value -> 0.
local function vehicleBonus(player: Player): number
	local vehicles = registry.VehicleService
	if not vehicles or type(vehicles.GetRunnerBonus) ~= "function" then
		return 0
	end
	local ok, bonus = pcall(vehicles.GetRunnerBonus, vehicles, player)
	if ok and isFiniteNumber(bonus) then
		return math.clamp(bonus :: number, 0, 1)
	end
	return 0
end

-- Put the avatar's movement settings back the way they were before the run.
local function restoreHumanoid(session: Session)
	local humanoid = getHumanoid(session.character)
	if humanoid and humanoid.Health > 0 then
		humanoid.WalkSpeed = session.originalWalkSpeed
		humanoid.JumpHeight = session.originalJumpHeight
		humanoid.JumpPower = session.originalJumpPower
	end
end

-- The gun the avatar holds during the run: a Tool with an invisible Handle.
-- Parenting a Tool to the character equips it (RightGrip weld + the default
-- tool-hold arm pose). The client welds the part-built gun model to the Handle.
local function destroyGun(session: Session)
	local watch = session.gunWatch
	session.gunWatch = nil
	if watch then
		watch:Disconnect()
	end
	local gun = session.gun
	session.gun = nil
	if gun then
		gun:Destroy()
	end
end

local function giveGun(player: Player, session: Session)
	destroyGun(session)
	local character = session.character
	local humanoid = getHumanoid(character)
	if not humanoid or not character.Parent then
		return
	end
	-- Anything else in the hand goes back to the backpack first.
	humanoid:UnequipTools()
	local stale = character:FindFirstChild(GUN_NAME)
	if stale then
		stale:Destroy()
	end
	local tool = Instance.new("Tool")
	tool.Name = GUN_NAME
	tool.RequiresHandle = true
	tool.CanBeDropped = false
	tool.ManualActivationOnly = true
	tool.ToolTip = ""
	local handle = Instance.new("Part")
	handle.Name = "Handle"
	handle.Size = Vector3.new(0.4, 0.4, 1)
	handle.Transparency = 1
	handle.Massless = true
	handle.CanCollide = false
	handle.CanTouch = false
	handle.CanQuery = false
	handle.CastShadow = false
	handle.Parent = tool
	tool.Parent = character
	session.gun = tool
	-- If the tool ever lands in the backpack (unequipped), put it back in
	-- the hand while the run lasts. (HUD hides the hotbar, so this is rare.)
	session.gunWatch = tool.AncestryChanged:Connect(function()
		task.defer(function()
			if sessions[player] ~= session or session.gun ~= tool then
				return
			end
			if tool.Parent and tool.Parent:IsA("Backpack") and session.character.Parent then
				tool.Parent = session.character
			end
		end)
	end)
end

local function stopSampling(session: Session)
	local sampler = session.sampler
	if sampler then
		session.sampler = nil
		sampler:Disconnect()
	end
end

-- Ends a session without touching the player's mode. Safe to call twice.
local function endSession(player: Player, notifyClient: boolean)
	local session = sessions[player]
	if not session then
		return
	end
	sessions[player] = nil
	stopSampling(session)
	destroyGun(session)
	restoreHumanoid(session)
	if notifyClient and player.Parent then
		Net.Fire("Runner", player, "Ended", { runId = session.runId })
	end
end

-- Distance along the track of the enemy base, used by the "did you really
-- get there" check. (The client draws the fort on the finish plaza, at this
-- distance or a little further.)
local function bossDistance(course: Course): number
	return math.min(course.boss.distance, Layout.RUNNER_TRACK_LENGTH + 30)
end

-------------------------------------------------------------------------------
-- Lane watching (anti-cheat for the lane choices)
-------------------------------------------------------------------------------
-- Which lane (option) of a row a lateral position is in; between lanes or
-- off the edge -> the nearest lane. Same rule as RunnerController's laneAt.
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

local function optionForLane(row: Row, lane: number): Option?
	for index, option in row.options do
		if (if type(option.lane) == "number" then option.lane else index) == lane then
			return option
		end
	end
	return nil
end

-- Studs the lateral position lies outside an option's [x - width/2, x + width/2].
local function outsideLane(option: Option, lateral: number): number
	return math.max(0, math.abs(lateral - option.x) - option.width / 2)
end

-- One sample of the avatar: records the lane of every row it has newly
-- crossed. Between two samples the position is interpolated, so the lane is
-- taken exactly where the avatar passed the row's distance.
local function sampleRows(session: Session)
	local root = getRoot(session.character)
	if not root then
		return
	end
	local start = Layout.RunnerPoint(session.slot, 0, 0)
	local position = root.CFrame.Position
	local travelled = start.Z - position.Z
	local lateral = position.X - start.X
	if not isFiniteNumber(travelled) or not isFiniteNumber(lateral) then
		return
	end
	local prevTravelled, prevLateral = session.lastTravelled, session.lastLateral
	local rows = session.course.rows
	while session.nextSampleRow <= #rows do
		local index = session.nextSampleRow
		local row = rows[index]
		if travelled < row.distance then
			break
		end
		local at = lateral
		if prevTravelled and prevLateral and prevTravelled < row.distance and travelled > prevTravelled then
			local t = (row.distance - prevTravelled) / (travelled - prevTravelled)
			at = prevLateral + (lateral - prevLateral) * t
		end
		session.serverChoices[index] = laneAt(row, at)
		session.serverLaterals[index] = at
		session.nextSampleRow = index + 1
	end
	session.lastTravelled = travelled
	session.lastLateral = lateral
end

-- The lanes that are paid. Per row: the client's lane if it is the lane the
-- server saw, or the lane next to it while the avatar was within
-- LANE_EDGE_TOLERANCE studs of it; otherwise the lane the server saw. Rows
-- the server never saw crossed -> the lane nearest to the latest sample.
local function chooseLanes(session: Session, choices: any): { number }
	local clean: { number } = {}
	local source = if type(choices) == "table" then choices else {}
	local fallbackLateral = session.lastLateral or 0
	for i, row in session.course.rows do
		local observed = session.serverChoices[i]
		if observed == nil then
			clean[i] = laneAt(row, fallbackLateral)
			continue
		end
		local lane = observed
		local claimed = rawget(source, i)
		local at = session.serverLaterals[i]
		if type(claimed) == "number" and claimed ~= observed and math.abs(claimed - observed) == 1 and at then
			local option = optionForLane(row, claimed)
			if option and outsideLane(option, at) <= LANE_EDGE_TOLERANCE then
				lane = claimed
			end
		end
		clean[i] = lane
	end
	return clean
end

-------------------------------------------------------------------------------
-- Static track building
-------------------------------------------------------------------------------
local function makePart(props: {
	name: string,
	size: Vector3,
	cframe: CFrame,
	color: Color3,
	parent: Instance,
	transparency: number?,
	canCollide: boolean?,
})
	local part = Instance.new("Part")
	part.Name = props.name
	part.Anchored = true
	part.Size = props.size
	part.CFrame = props.cframe
	part.Color = props.color
	part.Material = Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Transparency = props.transparency or 0
	part.CanCollide = if props.canCollide == nil then true else props.canCollide
	part.CastShadow = (props.transparency or 0) < 1
	if props.canCollide == false then
		part.CanQuery = false
		part.CanTouch = false
	end
	part.Parent = props.parent
	return part
end

-- Centre of a box on a track, `fromDist`..`toDist` studs from the start,
-- `lateral` studs right of the centre line, with its TOP at `topY`.
local function boxCFrame(
	slot: number,
	fromDist: number,
	toDist: number,
	lateral: number,
	topY: number,
	height: number
): CFrame
	local mid = Layout.RunnerPoint(slot, (fromDist + toDist) / 2, lateral)
	return CFrame.new(mid.X, topY - height / 2, mid.Z)
end

local function buildTrack(slot: number, parent: Instance)
	local folder = Instance.new("Folder")
	folder.Name = "Track_" .. slot
	folder:SetAttribute("Slot", slot)

	local width = Layout.RUNNER_TRACK_WIDTH
	local length = Layout.RUNNER_TRACK_LENGTH
	local floorY = Layout.FLOOR_Y
	local floorThickness = 4
	local halfW = width / 2

	-- Start platform (behind the start line) + main track floor.
	makePart({
		name = "StartPlatform",
		size = Vector3.new(width, floorThickness, START_PLATFORM_LENGTH),
		cframe = boxCFrame(slot, -START_PLATFORM_LENGTH, 0, 0, floorY, floorThickness),
		color = P.TrackWhite,
		parent = folder,
	})
	makePart({
		name = "Floor",
		size = Vector3.new(width, floorThickness, length),
		cframe = boxCFrame(slot, 0, length, 0, floorY, floorThickness),
		color = P.TrackWhite,
		parent = folder,
	})

	-- Very light blue cross stripes (decoration only, no collision).
	local stripes = Instance.new("Folder")
	stripes.Name = "Stripes"
	stripes.Parent = folder
	local d = STRIPE_SPACING / 2
	while d + STRIPE_LENGTH < length do
		makePart({
			name = "Stripe",
			size = Vector3.new(width - 0.2, 0.1, STRIPE_LENGTH),
			cframe = boxCFrame(slot, d, d + STRIPE_LENGTH, 0, floorY + 0.06, 0.1),
			color = P.TrackStripe,
			parent = stripes,
			canCollide = false,
		})
		d += STRIPE_SPACING
	end

	-- Low side rails along both edges (from the start platform to the plaza).
	local railFrom = -START_PLATFORM_LENGTH
	for _, side in { -1, 1 } do
		makePart({
			name = "Rail",
			size = Vector3.new(1, 1.2 + floorThickness, length - railFrom),
			cframe = boxCFrame(slot, railFrom, length, side * (halfW + 0.5), floorY + 1.2, 1.2 + floorThickness),
			color = P.TrackRail,
			parent = folder,
		})
		-- Tall invisible wall so the player can never fall into the sea.
		makePart({
			name = "InvisibleWall",
			size = Vector3.new(1, WALL_HEIGHT, length - railFrom),
			cframe = boxCFrame(slot, railFrom, length, side * (halfW + 1.5), floorY + WALL_HEIGHT, WALL_HEIGHT),
			color = P.TrackRail,
			parent = folder,
			transparency = 1,
		})
	end
	-- Rail + invisible wall behind the start platform.
	makePart({
		name = "BackRail",
		size = Vector3.new(width + 2, 1.2 + floorThickness, 1),
		cframe = boxCFrame(slot, railFrom - 1, railFrom, 0, floorY + 1.2, 1.2 + floorThickness),
		color = P.TrackRail,
		parent = folder,
	})
	makePart({
		name = "InvisibleBackWall",
		size = Vector3.new(width + 4, WALL_HEIGHT, 1),
		cframe = boxCFrame(slot, railFrom - 2, railFrom - 1, 0, floorY + WALL_HEIGHT, WALL_HEIGHT),
		color = P.TrackRail,
		parent = folder,
		transparency = 1,
	})

	-- Finish plaza (the client builds the enemy base on top of it).
	local plazaFrom = length
	local plazaTo = length + PLAZA_LENGTH
	local halfP = PLAZA_WIDTH / 2
	makePart({
		name = "FinishPlaza",
		size = Vector3.new(PLAZA_WIDTH, floorThickness, PLAZA_LENGTH),
		cframe = boxCFrame(slot, plazaFrom, plazaTo, 0, floorY, floorThickness),
		color = P.TrackWhite,
		parent = folder,
	})
	-- Plaza rails + invisible walls: left, right, far end and the two short
	-- front pieces either side of where the track joins.
	local frontPiece = halfP - (halfW + 2)
	local walls = {
		{ size = Vector3.new(1, 0, PLAZA_LENGTH), from = plazaFrom, to = plazaTo, lateral = -(halfP + 0.5) },
		{ size = Vector3.new(1, 0, PLAZA_LENGTH), from = plazaFrom, to = plazaTo, lateral = halfP + 0.5 },
		{ size = Vector3.new(PLAZA_WIDTH + 2, 0, 1), from = plazaTo, to = plazaTo + 1, lateral = 0 },
		{
			size = Vector3.new(frontPiece, 0, 1),
			from = plazaFrom - 1,
			to = plazaFrom,
			lateral = -(halfW + 2 + frontPiece / 2),
		},
		{
			size = Vector3.new(frontPiece, 0, 1),
			from = plazaFrom - 1,
			to = plazaFrom,
			lateral = halfW + 2 + frontPiece / 2,
		},
	}
	for _, w in walls do
		makePart({
			name = "PlazaRail",
			size = Vector3.new(w.size.X, 1.2 + floorThickness, w.size.Z),
			cframe = boxCFrame(slot, w.from, w.to, w.lateral, floorY + 1.2, 1.2 + floorThickness),
			color = P.TrackRail,
			parent = folder,
		})
		makePart({
			name = "InvisibleWall",
			size = Vector3.new(w.size.X, WALL_HEIGHT, w.size.Z),
			cframe = boxCFrame(slot, w.from, w.to, w.lateral, floorY + WALL_HEIGHT, WALL_HEIGHT),
			color = P.TrackRail,
			parent = folder,
			transparency = 1,
		})
	end

	folder.Parent = parent
end

local function buildTracks()
	if Workspace:FindFirstChild("RunnerTracks") then
		return -- pre-baked into the place file already
	end
	local root = Instance.new("Folder")
	root.Name = "RunnerTracks"
	for slot = 1, Config.MaxSlots do
		buildTrack(slot, root)
	end
	root.Parent = Workspace
end

-------------------------------------------------------------------------------
-- Request handlers
-------------------------------------------------------------------------------
local function modeService(): any
	return registry.ModeService
end

local function dataService(): any
	return registry.DataService
end

local function onPrepare(player: Player)
	local Mode = modeService()
	local Data = dataService()
	if not Mode or not Data then
		return Net.Fail("The game is still starting, try again")
	end
	if sessions[player] then
		return Net.Fail("You are already on a run")
	end
	if Mode:GetMode(player) ~= "Base" then
		return Net.Fail("Finish what you are doing first")
	end
	local data = Data:Get(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	local character = player.Character
	local humanoid = getHumanoid(character)
	local root = getRoot(character)
	if not character or not humanoid or not root or humanoid.Health <= 0 then
		return Net.Fail("Your character is not ready")
	end

	local area = math.clamp(math.floor(data.base.highestArea), 1, #Config.Areas)
	local level = math.max(1, math.floor(data.runner.level))
	local course = RunnerCourse.Generate(area, level)
	local slot = Mode:GetSlot(player)

	-- Get off any vehicle before teleporting (SHOP also does this on mode change).
	local vehicles = registry.VehicleService
	if vehicles and type(vehicles.Dismount) == "function" then
		pcall(vehicles.Dismount, vehicles, player)
	end

	runCounter += 1
	local session: Session = {
		runId = ("%d-%d-%d"):format(player.UserId, runCounter, math.floor(os.clock() * 1000) % 100000),
		course = course,
		level = level,
		area = area,
		slot = slot,
		character = character,
		started = false,
		startTime = 0,
		stats = nil,
		bossHp = nil,
		finished = false,
		reply = nil,
		sampler = nil,
		serverChoices = {},
		serverLaterals = {},
		nextSampleRow = 1,
		lastTravelled = nil,
		lastLateral = nil,
		originalWalkSpeed = if humanoid.WalkSpeed > 0 then humanoid.WalkSpeed else StarterPlayer.CharacterWalkSpeed,
		originalJumpHeight = if humanoid.JumpHeight > 0 then humanoid.JumpHeight else StarterPlayer.CharacterJumpHeight,
		originalJumpPower = if humanoid.JumpPower > 0 then humanoid.JumpPower else StarterPlayer.CharacterJumpPower,
		gun = nil,
		gunWatch = nil,
	}

	-- Mode first (ModeService remembers where you stood in the base), then teleport.
	Mode:SetMode(player, "Runner")
	sessions[player] = session
	local startCFrame = Layout.RunnerTrackStart(slot) * CFrame.new(0, 0, 4)
	if not Mode:Teleport(player, startCFrame) then
		endSession(player, false)
		Mode:SetMode(player, "Base")
		return Net.Fail("Could not reach the track, try again")
	end
	humanoid.WalkSpeed = 0
	humanoid.JumpHeight = 0
	humanoid.JumpPower = 0

	return Net.Ok({
		runId = session.runId,
		course = course,
		level = level,
		area = area,
		slot = slot,
		stats = currentStats(data),
		runSpeed = RUN_SPEED,
	})
end

local function onBuyUpgrade(player: Player, kind: any)
	local Mode = modeService()
	local Data = dataService()
	if not Mode or not Data then
		return Net.Fail("The game is still starting, try again")
	end
	if type(kind) ~= "string" or not UPGRADE_KINDS[kind] then
		return Net.Fail("Unknown upgrade")
	end
	local mode = Mode:GetMode(player)
	local session = sessions[player]
	local allowed = mode == "Base" or (mode == "Runner" and session ~= nil and not session.started)
	if not allowed then
		return Net.Fail("Upgrades can only be bought before a run starts")
	end
	local data = Data:Get(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	local upgrades = data.runner.upgrades :: { [string]: number }
	local level = upgrades[kind] or 0
	if level >= Economy.RunnerUpgradeMaxLevel then
		return Net.Fail("Already at max level")
	end
	local price = Economy.RunnerUpgradePrice(kind, level)
	if price == nil or not isFiniteNumber(price) or price < 0 then
		return Net.Fail("Already at max level")
	end
	if not Data:SpendCash(player, price) then
		return Net.Fail("Not enough cash")
	end
	upgrades[kind] = level + 1
	Data:MarkDirty(player)
	return Net.Ok({ kind = kind, level = level + 1, stats = currentStats(data) })
end

local function onGo(player: Player, runId: any)
	local Mode = modeService()
	local Data = dataService()
	local session = sessions[player]
	if not session or type(runId) ~= "string" or runId ~= session.runId then
		return Net.Fail("This run has ended")
	end
	if session.started then
		-- Repeated request (double input): answer the same thing again.
		return Net.Ok({ stats = session.stats, runSpeed = RUN_SPEED, bossHp = session.bossHp })
	end
	if not Mode or Mode:GetMode(player) ~= "Runner" then
		return Net.Fail("This run has ended")
	end
	local data = Data and Data:Get(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	local humanoid = getHumanoid(session.character)
	if not humanoid or humanoid.Health <= 0 or player.Character ~= session.character then
		return Net.Fail("Your character is not ready")
	end

	local bonus = vehicleBonus(player)
	local stats = copyStats(currentStats(data), if bonus > 0 then bonus else nil)
	session.stats = stats
	session.started = true
	session.startTime = os.clock()
	humanoid.WalkSpeed = RUN_SPEED
	-- Boss max HP for this course + stats (memoized; RunnerFinish's Resolve reuses it).
	local okHp, bossHp = pcall(RunnerSim.BossHp, session.course, stats)
	session.bossHp = if okHp and isFiniteNumber(bossHp) then bossHp else nil
	giveGun(player, session)

	-- Watch which lane the avatar is really in as it crosses each row.
	-- ASSUMPTION: this runs every Heartbeat (faster than the 30 Hz loop rule)
	-- on purpose - it is one position read per running player, and a finer
	-- sample keeps honest players' lanes exact.
	sampleRows(session)
	stopSampling(session)
	session.sampler = RunService.Heartbeat:Connect(function()
		if sessions[player] ~= session then
			stopSampling(session)
			return
		end
		sampleRows(session)
	end)
	return Net.Ok({ stats = session.stats, runSpeed = RUN_SPEED, bossHp = session.bossHp })
end

-- Make sure the vehicle bonus shows up exactly once in the result.
-- RunnerSim is expected to apply Stats.payoutBonus itself (a "bonus" line).
-- If it did not, apply it here so the player still gets it.
local function ensureBonus(result: Result, bonus: number?)
	if not bonus or bonus <= 0 then
		return
	end
	for _, line in result.lines do
		if line.kind == "bonus" then
			return
		end
	end
	local extra = Format.RoundWhole(result.total * bonus)
	if extra <= 0 then
		return
	end
	table.insert(result.lines, {
		kind = "bonus",
		label = "Vehicle bonus",
		detail = "+" .. Format.Percent(bonus) .. " payout",
		value = extra,
	})
	result.total = Format.RoundWhole(result.total + extra)
end

local function onFinish(player: Player, runId: any, choices: any)
	local Data = dataService()
	local session = sessions[player]
	if not session or type(runId) ~= "string" or runId ~= session.runId then
		return Net.Fail("This run has ended")
	end
	if session.finished and session.reply then
		return Net.Ok(session.reply) -- the reply got lost, send it again (no double pay)
	end
	if not session.started or not session.stats then
		return Net.Fail("The run has not started")
	end
	local data = Data and Data:Get(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end

	-- Anti-cheat: time and position.
	local elapsed = os.clock() - session.startTime
	if elapsed < RunnerSim.MinDuration(session.course) * MIN_DURATION_SLACK then
		return Net.Fail("That was too fast!")
	end
	local root = getRoot(session.character)
	if not root then
		return Net.Fail("Your character is not ready")
	end
	local start = Layout.RunnerPoint(session.slot, 0, 0)
	local position = root.CFrame.Position
	local travelled = start.Z - position.Z
	local lateral = math.abs(position.X - start.X)
	if travelled < bossDistance(session.course) * FINISH_DISTANCE_SHARE or lateral > TRACK_LATERAL_LIMIT then
		return Net.Fail("You did not reach the enemy base")
	end
	-- Last sample: rows still ahead of the avatar count with the lane it stands in now.
	sampleRows(session)

	local stats = session.stats :: Stats
	local clean = chooseLanes(session, choices)
	local result = RunnerSim.Resolve(session.course, stats, clean)
	ensureBonus(result, stats.payoutBonus)

	local total = if isFiniteNumber(result.total) then math.max(0, Format.RoundWhole(result.total)) else 0
	local paid = if total > 0 then Data:AddCash(player, total, { boostable = true, source = "runner" }) else 0

	local runner = data.runner
	runner.level = math.max(1, math.floor(runner.level)) + 1
	runner.runs += 1
	runner.bestPayout = math.max(runner.bestPayout, paid)
	Data:MarkDirty(player)

	local reply = {
		result = result,
		paid = paid,
		boosted = paid > total + 0.001,
		level = runner.level,
	}
	session.finished = true
	session.reply = reply
	stopSampling(session) -- the lanes are final now
	return Net.Ok(reply)
end

local function onLeave(player: Player)
	local Mode = modeService()
	endSession(player, false)
	if Mode and Mode:GetMode(player) == "Runner" then
		Mode:ReturnToBase(player)
	end
	return Net.Ok()
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function RunnerService:Init(reg: { [string]: any })
	registry = reg
	Net.Handle("RunnerPrepare", onPrepare)
	Net.Handle("BuyRunnerUpgrade", onBuyUpgrade)
	Net.Handle("RunnerGo", onGo)
	Net.Handle("RunnerFinish", onFinish)
	Net.Handle("RunnerLeave", onLeave)
end

function RunnerService:Start()
	local ok, err = pcall(function(): any
		buildTracks()
		return nil
	end)
	if not ok then
		warn("[RunnerService] building the runner tracks failed: " .. tostring(err))
	end

	local Mode = modeService()
	if Mode and Mode.ModeChanged then
		-- Death, respawn, leaving the game or any other mode change ends the run.
		Mode.ModeChanged:Connect(function(player: Player, newMode: string, oldMode: string)
			if oldMode == "Runner" and newMode ~= "Runner" then
				endSession(player, true)
			end
		end)
	end
	Players.PlayerRemoving:Connect(function(player)
		endSession(player, false) -- also stops the lane watcher
	end)
end

return RunnerService
