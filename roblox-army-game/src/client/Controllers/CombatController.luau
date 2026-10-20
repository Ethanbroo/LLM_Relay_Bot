--!strict
--[[
	CombatController (client)
	The client half of the Archero-2-inspired combat mode (docs/ARCHITECTURE.md §6.3).
	It talks to CombatService, moves the top-down camera, draws the fight with
	CombatVisuals and shows every screen with CombatUI.

	Public API (ARCHITECTURE §4.6, call from other controllers' Start or later):
		CombatController:OpenArenaMenu()            level select for the repeatable Combat Arena
		CombatController:StartBarrier(area: number) fight the barrier squad on `area`'s exit gate
		CombatController:IsActive(): boolean        true while a fight (or its lobby) is open
		OpenArenaMenu / StartBarrier do nothing while a fight (or its lobby) is open
		or a CombatPrepare request is on its way. Their dialogs are "Base" dialogs:
		opening one again replaces the old copy, and the HUD closes them when you
		leave the base. The lobby inventory has no Context, so it stays open when
		the mode changes to "Combat".

	Flow of one fight:
		1. OpenArenaMenu / StartBarrier -> CombatPrepare (server teleports you to your arena)
		2. LOBBY before round 1: the inventory opens ("Prepare for battle") so you
		   can review and equip armour and weapons [REQUIREMENT]. Closing it shows
		   a small bar: [Inventory] [Start] [Leave].
		3. START BATTLE -> CombatStart -> 3-2-1 -> waves. Move with WASD / thumbstick,
		   stand still to shoot automatically, dodge the slow bullets and red circles.
		4. After each wave (except the last): pick 1 of 3 abilities (this attempt only).
		5. Result screen -> chest -> CONTINUE -> CombatLeave -> back to base.
		Camera, controls (jump) and every visual are restored / removed when the
		fight ends for ANY reason (death, respawn, leaving, server timeout).
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local ChestUI = require(Client.UI.ChestUI)
local CombatUI = require(Client.UI.CombatUI)
local CombatVisuals = require(Client.World.CombatVisuals)
local InventoryUI = require(Client.UI.InventoryUI)
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local CombatDefs = require(Shared.Combat.CombatDefs)
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Net = require(Shared.Net)

local player = Players.LocalPlayer

local CombatController = {}

local RENDER_NAME = "CombatCamera"
local CAMERA_HEIGHT = 58 -- studs above the player
local CAMERA_BACK = 32 -- studs to the south -> about 61 degrees looking down
local CAMERA_SMOOTHING = 8
local CAMERA_CLAMP_X = CombatDefs.HALF_X - 16 -- keep the arena nicely framed
local CAMERA_CLAMP_Z = CombatDefs.HALF_Z - 14

type Session = {
	id: string,
	mode: string,
	level: number,
	area: number,
	waveCount: number,
	reward: number,
	nextAreaName: string,
	center: Vector3,
	phase: string, -- "lobby" | "fighting" | "result"
	visuals: CombatVisuals.Visuals,
	hud: CombatUI.Hud,
	lobbyBar: { Destroy: () -> () }?,
	closeInventory: (() -> ())?,
	inventoryOpen: boolean,
	closeChoice: (() -> ())?,
	closeResult: (() -> ())?,
	closeConfirm: (() -> ())?,
	starting: boolean,
	leaving: boolean,
	connections: { RBXScriptConnection },
	humanoid: Humanoid?,
	camPos: Vector3?,
	lastHp: number,
}

local registry: { [string]: any } = {}
local session: Session? = nil
local busy = false -- a CombatPrepare request is on its way

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function getRoot(): BasePart?
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root
	end
	return nil
end

local function getHumanoid(): Humanoid?
	local character = player.Character
	return character and character:FindFirstChildOfClass("Humanoid")
end

local function try(label: string, fn: () -> ())
	local ok, err = pcall(function(): any
		fn()
		return nil
	end)
	if not ok then
		warn("[CombatController] " .. label .. " failed: " .. tostring(err))
	end
end

local function setMenuVisible(visible: boolean)
	local hud = registry.HUDController
	if hud and hud.SetMenuVisible then
		try("SetMenuVisible", function()
			hud:SetMenuVisible(visible)
		end)
	end
end

local function restoreCamera()
	local camera = Workspace.CurrentCamera
	if not camera then
		return
	end
	camera.CameraType = Enum.CameraType.Custom
	local humanoid = getHumanoid()
	if humanoid then
		camera.CameraSubject = humanoid
	end
end

-------------------------------------------------------------------------------
-- Cleanup (safe to call many times)
-------------------------------------------------------------------------------
local function cleanup()
	local s = session
	if not s then
		return
	end
	session = nil
	RunService:UnbindFromRenderStep(RENDER_NAME)
	for _, connection in s.connections do
		connection:Disconnect()
	end
	table.clear(s.connections)
	try("close confirm", function()
		if s.closeConfirm then
			s.closeConfirm()
		end
	end)
	try("close inventory", function()
		if s.closeInventory then
			s.closeInventory()
		end
	end)
	try("close lobby", function()
		if s.lobbyBar then
			s.lobbyBar.Destroy()
		end
	end)
	try("close choice", function()
		if s.closeChoice then
			s.closeChoice()
		end
	end)
	try("close result", function()
		if s.closeResult then
			s.closeResult()
		end
	end)
	try("hud", s.hud.Destroy)
	try("visuals", s.visuals.Destroy)
	try("humanoid", function()
		local humanoid = s.humanoid
		if humanoid and humanoid.Parent then
			humanoid:SetStateEnabled(Enum.HumanoidStateType.Jumping, true)
		end
	end)
	try("camera", restoreCamera)
	setMenuVisible(true)
end

-------------------------------------------------------------------------------
-- Leaving
-------------------------------------------------------------------------------
local function requestLeave(s: Session)
	if s.leaving then
		return
	end
	s.leaving = true
	task.spawn(function()
		local result = Net.Request("CombatLeave")
		if session ~= s then
			return
		end
		s.leaving = false
		if not result.ok then
			Theme.Toast(result.err or "Could not leave", "error")
		elseif result.left then
			cleanup() -- the server is sending us home
		end
		-- result.ended: mid-fight retreat -> the "Result" event shows the result screen
	end)
end

-------------------------------------------------------------------------------
-- Lobby (before the first round)
-------------------------------------------------------------------------------
local showLobbyBar: (s: Session) -> ()
local openInventory: (s: Session) -> ()

local function startBattle(s: Session)
	if s.starting or s.phase ~= "lobby" or session ~= s then
		return
	end
	s.starting = true
	if s.lobbyBar then
		s.lobbyBar.Destroy()
		s.lobbyBar = nil
	end
	local closeInventory = s.closeInventory
	s.closeInventory = nil
	s.inventoryOpen = false
	if closeInventory then
		try("close inventory", closeInventory)
	end
	task.spawn(function()
		local result = Net.Request("CombatStart", s.id)
		if session ~= s then
			return
		end
		s.starting = false
		if not result.ok then
			Theme.Toast(result.err or "Could not start the fight", "error")
			showLobbyBar(s)
			return
		end
		s.phase = "fighting"
		s.hud.SetFightVisible(true)
		s.hud.Hint("Stand still to shoot! Move to dodge bullets and red circles.", 6)
	end)
end

showLobbyBar = function(s: Session)
	if session ~= s or s.phase ~= "lobby" or s.lobbyBar or s.starting then
		return
	end
	s.lobbyBar = CombatUI.LobbyBar({
		onInventory = function()
			openInventory(s)
		end,
		onStart = function()
			startBattle(s)
		end,
		onLeave = function()
			requestLeave(s)
		end,
	})
end

openInventory = function(s: Session)
	if session ~= s or s.phase ~= "lobby" or s.inventoryOpen or s.starting then
		return
	end
	if s.lobbyBar then
		s.lobbyBar.Destroy()
		s.lobbyBar = nil
	end
	s.inventoryOpen = true
	local startPressed = false
	local ok, closeOrErr = pcall(InventoryUI.Open, {
		Title = "Prepare for battle",
		ActionText = "START BATTLE",
		OnAction = function()
			startPressed = true
			-- deferred: InventoryUI.Open may call this before it has returned
			task.defer(function()
				startBattle(s)
			end)
		end,
		OnClose = function()
			s.inventoryOpen = false
			s.closeInventory = nil
			if not startPressed then
				task.defer(function()
					showLobbyBar(s)
				end)
			end
		end,
	})
	if ok and type(closeOrErr) == "function" then
		if s.inventoryOpen and not startPressed then
			s.closeInventory = closeOrErr
		elseif startPressed then
			try("close inventory", closeOrErr)
		end
	else
		warn("[CombatController] InventoryUI.Open failed: " .. tostring(closeOrErr))
		s.inventoryOpen = false
		showLobbyBar(s)
	end
end

-------------------------------------------------------------------------------
-- Per-frame: camera + visuals + reporting hits on myself
-------------------------------------------------------------------------------
local function step(s: Session, dt: number)
	local root = getRoot()
	local camera = Workspace.CurrentCamera
	if camera and root then
		if camera.CameraType ~= Enum.CameraType.Scriptable then
			camera.CameraType = Enum.CameraType.Scriptable
		end
		local localPos = root.Position - s.center
		local focus = s.center
			+ Vector3.new(
				math.clamp(localPos.X, -CAMERA_CLAMP_X, CAMERA_CLAMP_X),
				localPos.Y,
				math.clamp(localPos.Z, -CAMERA_CLAMP_Z, CAMERA_CLAMP_Z)
			)
		local offset = Vector3.new(0, CAMERA_HEIGHT, CAMERA_BACK)
		local desired = focus + offset
		local current = s.camPos or desired
		current = current:Lerp(desired, 1 - math.exp(-CAMERA_SMOOTHING * dt))
		s.camPos = current
		camera.CFrame = CFrame.lookAt(current, current - offset)
	end

	local hits = s.visuals.Update(dt, if root then root.Position else nil)
	if s.phase == "fighting" and player:GetAttribute("CombatActive") == true then
		for _, id in hits do
			Net.FireServer("Combat", "Hit", { id = id })
		end
	end

	local hp = player:GetAttribute("CombatHP")
	local maxHp = player:GetAttribute("CombatMaxHP")
	local shield = player:GetAttribute("CombatShield")
	if type(hp) == "number" and type(maxHp) == "number" then
		s.hud.SetVitals(hp, maxHp, if type(shield) == "number" then shield else 0)
		if hp < s.lastHp - 0.05 then
			s.hud.Hurt()
		end
		s.lastHp = hp
	end
end

-------------------------------------------------------------------------------
-- Server events
-------------------------------------------------------------------------------
local function onResult(s: Session, result: { [string]: any })
	s.phase = "result"
	try("close choice", function()
		if s.closeChoice then
			s.closeChoice()
			s.closeChoice = nil
		end
	end)
	try("close confirm", function()
		if s.closeConfirm then
			s.closeConfirm()
			s.closeConfirm = nil
		end
	end)
	s.visuals.ClearCombat()
	s.hud.SetFightVisible(false)
	if s.closeResult then
		return
	end
	s.closeResult = CombatUI.Result({
		result = result,
		onChest = function(done: () -> ())
			local chest = result.chest
			if type(chest) == "table" and type(chest.tier) == "string" and type(chest.item) == "table" then
				local finished = false
				local function finishOnce()
					if not finished then
						finished = true
						done()
					end
				end
				local ok, err = pcall(function(): any
					ChestUI.Play(chest.tier, chest.item, finishOnce)
					return nil
				end)
				if not ok then
					warn("[CombatController] ChestUI.Play failed: " .. tostring(err))
					finishOnce()
				end
			else
				done()
			end
		end,
		onContinue = function()
			s.closeResult = nil
			requestLeave(s)
		end,
	})
end

local function onCombatEvent(msg: any, payload: any)
	local s = session
	if not s or type(msg) ~= "string" or type(payload) ~= "table" then
		return
	end
	if msg == "Ended" then
		if payload.sessionId == s.id then
			cleanup()
		end
	elseif msg == "Wave" then
		local wave = tonumber(payload.wave) or 1
		local startsAt = tonumber(payload.startsAt) or Workspace:GetServerTimeNow()
		s.hud.Countdown(startsAt, wave, payload.boss == true)
	elseif msg == "AbilityChoice" then
		if s.closeChoice then
			s.closeChoice()
		end
		local options = if type(payload.options) == "table" then payload.options else {}
		s.closeChoice = CombatUI.AbilityChoice({
			options = options,
			timeout = tonumber(payload.timeout) or CombatDefs.ABILITY_TIMEOUT,
			onPick = function(id: string)
				s.closeChoice = nil
				task.spawn(function()
					-- a failure only means the server auto-picked first ("AbilityPicked" tells us what)
					local result = Net.Request("CombatChooseAbility", s.id, id)
					if not result.ok then
						warn("[CombatController] ability pick refused: " .. tostring(result.err))
					end
				end)
			end,
		})
	elseif msg == "AbilityPicked" then
		if s.closeChoice then
			s.closeChoice()
			s.closeChoice = nil
		end
		local ability = CombatDefs.AbilityById[tostring(payload.id)]
		if ability then
			s.hud.Hint(
				("%s %s%s"):format(ability.icon, ability.name, if payload.auto then " (auto-picked)" else "!"),
				3
			)
		end
	elseif msg == "ProjectileBatch" then
		s.visuals.AddProjectiles(payload.list)
	elseif msg == "ProjectileGone" then
		s.visuals.RemoveProjectiles(payload.ids)
	elseif msg == "Telegraph" then
		s.visuals.Telegraph(payload)
	elseif msg == "EnemyDied" then
		s.visuals.EnemyDied(payload.list)
	elseif msg == "Damage" then
		s.visuals.Damage(payload.list)
	elseif msg == "Collected" then
		s.visuals.Collected(payload.list)
	elseif msg == "Result" then
		onResult(s, payload)
	end
end

local function onSnapshot(buf: any)
	local s = session
	if not s or s.phase == "result" then
		return
	end
	local snap = CombatDefs.DecodeSnap(buf)
	if snap then
		s.visuals.ApplySnapshot(snap)
		s.hud.SetCash(snap.cash)
	end
end

-------------------------------------------------------------------------------
-- Entering a fight
-------------------------------------------------------------------------------
local function enter(reply: { [string]: any })
	cleanup() -- never two sessions at once
	local center = Vector3.new(tonumber(reply.centerX) or 0, tonumber(reply.centerY) or 0, tonumber(reply.centerZ) or 0)
	local mode = if reply.mode == "barrier" then "barrier" else "arena"
	local level = tonumber(reply.level) or 1
	local waveCount = tonumber(reply.waveCount) or 3
	local s: Session
	local hud = CombatUI.Hud({
		level = level,
		mode = mode,
		nextAreaName = tostring(reply.nextAreaName or ""),
		waveCount = waveCount,
		onLeave = function()
			local current = session
			if not current or current.phase == "result" then
				return
			end
			if current.phase == "lobby" then
				requestLeave(current)
				return
			end
			if current.closeConfirm then
				return
			end
			current.closeConfirm = Theme.Confirm({
				Name = "RetreatConfirm",
				Title = "Retreat?",
				Body = "Leaving now counts as a defeat. You keep the cash you collected.",
				ConfirmText = "Retreat",
				ConfirmColor = Theme.Palette.ButtonRed,
				OnConfirm = function()
					current.closeConfirm = nil
					requestLeave(current)
				end,
				OnClose = function()
					current.closeConfirm = nil
				end,
			})
		end,
	})
	s = {
		id = tostring(reply.sessionId),
		mode = mode,
		level = level,
		area = tonumber(reply.area) or 1,
		waveCount = waveCount,
		reward = tonumber(reply.reward) or 0,
		nextAreaName = tostring(reply.nextAreaName or ""),
		center = center,
		phase = "lobby",
		visuals = CombatVisuals.new(center),
		hud = hud,
		lobbyBar = nil,
		closeInventory = nil,
		inventoryOpen = false,
		closeChoice = nil,
		closeResult = nil,
		closeConfirm = nil,
		starting = false,
		leaving = false,
		connections = {},
		humanoid = getHumanoid(),
		camPos = nil,
		lastHp = math.huge,
	}
	session = s
	hud.SetWave(0, waveCount)
	hud.SetFightVisible(true)
	hud.SetCharacter(player.Character)
	hud.SetCash(0)
	setMenuVisible(false)

	local humanoid = s.humanoid
	if humanoid then
		humanoid:SetStateEnabled(Enum.HumanoidStateType.Jumping, false)
	end
	RunService:BindToRenderStep(RENDER_NAME, Enum.RenderPriority.Camera.Value + 1, function(dt: number)
		if session == s then
			step(s, dt)
		end
	end)
	-- The lobby: review and equip armour and weapons before round 1.
	openInventory(s)
end

local function prepare(mode: string, param: number)
	if busy or session then
		return -- already fighting (or about to)
	end
	if State.Mode() ~= "Base" then
		Theme.Toast("Finish what you are doing first", "error")
		return
	end
	busy = true
	task.spawn(function()
		local result = Net.Request("CombatPrepare", mode, param)
		busy = false
		if not result.ok then
			Theme.Toast(result.err or "Could not start the fight", "error")
			return
		end
		enter(result)
	end)
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function CombatController:OpenArenaMenu()
	local data = State.Get()
	if not data or busy or session then
		return -- ignored while a fight (or its lobby) is open
	end
	if not data.base.arenaUnlocked then
		Theme.Toast(
			"Unlock the Combat Arena in " .. (Config.Areas[Config.ArenaArea].name or "area 2") .. " first",
			"info"
		)
		return
	end
	if State.Mode() ~= "Base" then
		return
	end
	CombatUI.OpenArenaMenu({
		highestLevel = data.combat.highestLevel,
		onFight = function(level: number)
			prepare("arena", level)
		end,
	})
end

function CombatController:StartBarrier(area: number)
	local data = State.Get()
	if not data or busy or session or type(area) ~= "number" then
		return -- ignored while a fight (or its lobby) is open
	end
	if State.Mode() ~= "Base" then
		return
	end
	if data.base.barriers[tostring(area)] then
		Theme.Toast("You already beat this barrier squad", "info")
		return
	end
	if area >= #Config.Areas then
		return
	end
	local level = Economy.BarrierCombatLevel(area)
	local nextArea = Config.Areas[area + 1]
	CombatUI.ConfirmBarrier({
		level = level,
		nextAreaName = if nextArea then nextArea.name else "the next area",
		reward = Economy.CombatLevelReward(level),
		onConfirm = function()
			prepare("barrier", area)
		end,
	})
end

function CombatController:IsActive(): boolean
	return session ~= nil
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function CombatController:Init(reg: { [string]: any })
	registry = reg
end

function CombatController:Start()
	local hud = registry.HUDController
	if hud and hud.AddMenuButton then
		hud:AddMenuButton({
			Id = "Battle",
			Title = "Battle",
			Icon = "⚔️",
			Order = 2,
			HeaderColor = Theme.Palette.ButtonRed,
			HeaderDarkColor = Theme.Palette.ButtonRedDark,
			OnClick = function()
				CombatController:OpenArenaMenu()
			end,
			Visible = function(data: any): boolean
				return type(data) == "table" and type(data.base) == "table" and data.base.arenaUnlocked == true
			end,
		})
	end

	Net.On("Combat", function(msg: any, payload: any)
		try("combat event " .. tostring(msg), function()
			onCombatEvent(msg, payload)
		end)
	end)
	Net.On("CombatSnap", function(buf: any)
		try("snapshot", function()
			onSnapshot(buf)
		end)
	end)

	-- Leaving Combat mode for any reason ends the local session.
	State.ModeChanged:Connect(function(newMode: string)
		if session and newMode ~= "Combat" then
			cleanup()
		end
	end)
	-- Respawned: the old character is gone, so is the fight.
	player.CharacterAdded:Connect(function()
		if session then
			cleanup()
		end
	end)
end

return CombatController
