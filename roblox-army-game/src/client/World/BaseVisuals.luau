--!strict
--[[
	BaseVisuals (client)
	Draws the LOCAL player's own progress on the shared home map:

		* locked slot (area unlocked)  -> white unlock pad with a thick green
		                                  border, gold price + coin (image 1),
		                                  ProximityPrompt "Unlock". Its name +
		                                  price label is AlwaysOnTop (see buildPad)
		* locked area                  -> a "LOCKED" sign in the middle of the area
		* unlocked revenue section     -> colourful building (BuildingFactory),
		                                  billboard with level, $/s, stock bar and a
		                                  flashing "OUT OF STOCK!", prompts
		                                  "Restock" (E) and "Upgrade" (F)
		* employee spot                -> "Hire me" tile, or the hired employee NPC
		* Combat Arena (a2s4) unlocked -> arena building, prompt "Enter arena"
		* gates of areas >= highestArea-> black iron gate + gold padlock and an
		                                  invisible wall in the opening; the next
		                                  gate gets a billboard + prompt

	Only what changed is rebuilt: every slot / gate remembers its current
	"state" and is rebuilt only when that state changes. Numbers on billboards
	are refreshed on every data update.

	Public API (used by Controllers/BaseController):
		BaseVisuals.Start(callbacks)       -- create the world folder + update loop
			callbacks = {
				OnUnlock(slotId), OnRestock(slotId), OnUpgrade(slotId),
				OnHire(slotId), OnArena(), OnGate(area),
			}
		BaseVisuals.Update(data)           -- apply a PlayerData snapshot (cheap, call often)
		BaseVisuals.ShowIncome(slotId, amount)  -- floating "+$12.50" over a building
		BaseVisuals.EmployeeHop(slotId)    -- the employee does a little hop (restocked)
		BaseVisuals.Pop(slotId)            -- bounce that slot's billboard (feedback)
		BaseVisuals.FloatAt(slotId, text, color?) -- floating text above a slot's front
		BaseVisuals.Destroy()              -- remove everything (no leaks)
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)
local BaseCustomers = require(script.Parent.BaseCustomers)
local BuildingFactory = require(script.Parent.BuildingFactory)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Types = require(Shared.Types)

type PlayerData = Types.PlayerData

local P = Config.Palette
local player = Players.LocalPlayer

export type Callbacks = {
	OnUnlock: (slotId: string) -> (),
	OnRestock: (slotId: string) -> (),
	OnUpgrade: (slotId: string) -> (),
	OnHire: (slotId: string) -> (),
	OnArena: () -> (),
	OnGate: (area: number) -> (),
}

type BuildingUi = {
	content: Frame,
	levelLabel: TextLabel,
	rateLabel: TextLabel,
	stockFill: Frame,
	stockText: TextLabel,
	alertLabel: TextLabel,
	restockPrompt: ProximityPrompt,
	upgradePrompt: ProximityPrompt,
}

type SlotVisual = {
	id: string,
	area: number,
	index: number,
	def: Config.SlotDef,
	cframe: CFrame,
	state: string, -- "none" | "pad" | "building" | "arena"
	folder: Folder?,
	top: BasePart?,
	content: Frame?,
	ui: BuildingUi?,
	arenaBest: TextLabel?,
	hireState: string, -- "none" | "tile" | "employee"
	hireFolder: Folder?,
	employee: Model?,
	employeeBase: CFrame?,
	hopAt: number,
	bobPhase: number,
	flashing: boolean,
}

type GateVisual = {
	area: number,
	state: string, -- "none" | "far" | "active"
	folder: Folder?,
	model: Model?,
	prompt: ProximityPrompt?,
	content: Frame?,
	title: TextLabel?,
	sub: TextLabel?,
}

local BaseVisuals = {}

local callbacks: Callbacks? = nil
local root: Folder? = nil
local heartbeat: RBXScriptConnection? = nil
local slots: { SlotVisual } = {}
local slotById: { [string]: SlotVisual } = {}
local gates: { [number]: GateVisual } = {}
local lockedSigns: { [number]: Folder } = {}
local lastData: PlayerData? = nil
local accumulator = 0

local UPDATE_RATE = 1 / 30
local PROMPT_DISTANCE = 10
local BILLBOARD_DISTANCE = 150
-- AlwaysOnTop labels show through walls, so they stop at a shorter distance
-- (pads of the next area, 184 studs away, stay hidden).
local PAD_LABEL_DISTANCE = 110
local EMPLOYEE_HOP_TIME = 0.55

-------------------------------------------------------------------------------
-- Small helpers
-------------------------------------------------------------------------------
local function anchor(parent: Instance, name: string, position: Vector3): Part
	local part = BuildingFactory.Part(parent, {
		Name = name,
		Size = Vector3.new(1, 1, 1),
		CFrame = CFrame.new(position),
		Color = P.White,
		Transparency = 1,
		CanCollide = false,
		CastShadow = false,
	})
	part.CanQuery = false
	return part
end

local function makePrompt(
	parent: Instance,
	actionText: string,
	objectText: string,
	hold: number,
	key: Enum.KeyCode?,
	gamepadKey: Enum.KeyCode?,
	distance: number?
): ProximityPrompt
	local prompt = Instance.new("ProximityPrompt")
	prompt.ActionText = actionText
	prompt.ObjectText = objectText
	prompt.HoldDuration = hold
	prompt.MaxActivationDistance = distance or PROMPT_DISTANCE
	prompt.RequiresLineOfSight = false
	prompt.KeyboardKeyCode = key or Enum.KeyCode.E
	prompt.GamepadKeyCode = gamepadKey or Enum.KeyCode.ButtonX
	prompt.Parent = parent
	return prompt
end

-- World-sized billboard (Size in studs) with a "Content" frame inside that
-- Theme.Pop can bounce. `alwaysOnTop` draws it over everything else.
local function billboard(
	adornee: BasePart,
	widthStuds: number,
	heightStuds: number,
	offsetY: number,
	maxDistance: number?,
	alwaysOnTop: boolean?
): (BillboardGui, Frame)
	local board = Theme.Billboard({
		Name = "Info",
		Parent = adornee,
		Adornee = adornee,
		Size = UDim2.fromScale(widthStuds, heightStuds),
		StudsOffset = Vector3.new(0, offsetY, 0),
		AlwaysOnTop = alwaysOnTop == true,
		MaxDistance = maxDistance or BILLBOARD_DISTANCE,
	})
	local content = Instance.new("Frame")
	content.Name = "Content"
	content.BackgroundTransparency = 1
	content.Size = UDim2.fromScale(1, 1)
	content.Parent = board
	return board, content
end

local function label(parent: Instance, text: string, y: number, height: number, color: Color3?): TextLabel
	return Theme.Label({
		Text = text,
		TextColor3 = color or P.White,
		MaxTextSize = 120,
		Position = UDim2.fromScale(0, y),
		Size = UDim2.fromScale(1, height),
		Parent = parent,
	})
end

-- "$1,562.50 (coin)" row, centred.
local function priceRow(parent: Instance, text: string, y: number, height: number): TextLabel
	local row = Instance.new("Frame")
	row.Name = "PriceRow"
	row.BackgroundTransparency = 1
	row.Position = UDim2.fromScale(0, y)
	row.Size = UDim2.fromScale(1, height)
	row.Parent = parent
	Theme.List(row, "Horizontal", 4, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)
	local widthShare = math.clamp(#text * 0.075, 0.3, 0.8)
	local priceLabel = Theme.Label({
		Text = text,
		TextColor3 = P.PriceGold,
		MaxTextSize = 120,
		StrokeThickness = 3,
		Size = UDim2.fromScale(widthShare, 1),
		LayoutOrder = 1,
		Parent = row,
	})
	Theme.CoinIcon({ Size = UDim2.fromScale(0.15, 0.8), LayoutOrder = 2, Parent = row })
	return priceLabel
end

local function slotFrame(area: number, index: number): CFrame
	local centre = Layout.SlotCenter(area, index)
	local offset = Layout.SLOT_OFFSETS[index]
	local towardsPath = Vector3.new(-math.sign(offset.X), 0, 0)
	return CFrame.lookAt(centre, centre + towardsPath)
end

local function clearSlotState(sv: SlotVisual)
	if sv.folder then
		sv.folder:Destroy()
	end
	sv.folder = nil
	sv.top = nil
	sv.content = nil
	sv.ui = nil
	sv.arenaBest = nil
	sv.flashing = false
end

local function clearHire(sv: SlotVisual)
	if sv.hireFolder then
		sv.hireFolder:Destroy()
	end
	sv.hireFolder = nil
	sv.employee = nil
	sv.employeeBase = nil
	sv.hireState = "none"
end

local function newFolder(name: string): Folder
	local folder = Instance.new("Folder")
	folder.Name = name
	folder.Parent = root
	return folder
end

local function cb(): Callbacks?
	return callbacks
end

-------------------------------------------------------------------------------
-- Slot states
-------------------------------------------------------------------------------
local function buildPad(sv: SlotVisual)
	local folder = newFolder("Pad_" .. sv.id)
	sv.folder = folder
	local isArena = sv.def.kind == "arena"
	-- Thin tile (top ~0.55 studs above the floor, above the plot outline).
	local tile =
		BuildingFactory.FlatTile(folder, sv.cframe + Vector3.new(0, 0.4, 0), 20, 11, P.PadFill, P.PadBorder, 1.2)
	tile.Name = "UnlockPad"

	local price = Economy.SlotUnlockPrice(sv.id)
	local priceText = Format.Money(price)
	-- Lifted well above the pad AND drawn on top: the pad's white/green
	-- picture is a SurfaceGui, and Roblox can draw it over a normal billboard
	-- that overlaps it on screen, which cut the name + price off (owner feedback).
	local board, content = billboard(tile, 14, 4, 4.6, PAD_LABEL_DISTANCE, true)
	board.Name = "PadInfo"
	sv.content = content
	local titleText = if isArena then "⚔️ Combat Arena" else sv.def.icon .. " " .. sv.def.name
	label(content, titleText, 0, 0.42, P.White)
	priceRow(content, priceText, 0.44, 0.56)

	local prompt = makePrompt(tile, "Unlock", sv.def.name .. " - " .. priceText, 0.4, nil, nil, 12)
	prompt.Triggered:Connect(function()
		local c = cb()
		if c then
			c.OnUnlock(sv.id)
		end
	end)
end

local function buildBuilding(sv: SlotVisual)
	local folder = newFolder("Building_" .. sv.id)
	sv.folder = folder
	local model = BuildingFactory.Build(sv.def, sv.cframe)
	model.Parent = folder
	local top = model:FindFirstChild("Top") :: BasePart?
	sv.top = top
	local adornee: BasePart = top or (model.PrimaryPart :: BasePart)

	local _, content = billboard(adornee, 10, 5.4, 3)
	sv.content = content
	local levelLabel = label(content, "", 0, 0.24, P.White)
	local rateLabel = label(content, "", 0.22, 0.26, P.MoneyGreen)

	local bar = Instance.new("Frame")
	bar.Name = "StockBar"
	bar.BackgroundColor3 = P.PillBlack
	bar.Position = UDim2.fromScale(0.08, 0.52)
	bar.Size = UDim2.fromScale(0.84, 0.2)
	bar.Parent = content
	Theme.Corner(bar, 0.5)
	Theme.Stroke(bar, P.CardOutline, 2, true)
	local fill = Instance.new("Frame")
	fill.Name = "Fill"
	fill.BackgroundColor3 = P.ButtonGreen
	fill.Size = UDim2.fromScale(1, 1)
	fill.Parent = bar
	Theme.Corner(fill, 0.5)
	local stockText = Theme.Label({
		Text = "",
		MaxTextSize = 80,
		StrokeThickness = 2,
		Size = UDim2.fromScale(1, 1),
		ZIndex = 3,
		Parent = bar,
	})
	local alertLabel = label(content, "OUT OF STOCK!", 0.74, 0.26, P.ButtonRed)
	alertLabel.Visible = false

	-- Prompts at the front of the plot (where the server checks distance).
	local front = Layout.SlotFrontPosition(sv.area, sv.index)
	local restockAnchor = anchor(folder, "RestockSpot", front + Vector3.new(0, 2.5, -2.5))
	local upgradeAnchor = anchor(folder, "UpgradeSpot", front + Vector3.new(0, 2.5, 2.5))
	local restockPrompt = makePrompt(restockAnchor, "Restock", sv.def.name, 1, Enum.KeyCode.E, Enum.KeyCode.ButtonX)
	local upgradePrompt = makePrompt(upgradeAnchor, "Upgrade", sv.def.name, 0.3, Enum.KeyCode.F, Enum.KeyCode.ButtonY)
	restockPrompt.Triggered:Connect(function()
		local c = cb()
		if c then
			c.OnRestock(sv.id)
		end
	end)
	upgradePrompt.Triggered:Connect(function()
		local c = cb()
		if c then
			c.OnUpgrade(sv.id)
		end
	end)

	sv.ui = {
		content = content,
		levelLabel = levelLabel,
		rateLabel = rateLabel,
		stockFill = fill,
		stockText = stockText,
		alertLabel = alertLabel,
		restockPrompt = restockPrompt,
		upgradePrompt = upgradePrompt,
	}
end

local function buildArena(sv: SlotVisual)
	local folder = newFolder("Arena_" .. sv.id)
	sv.folder = folder
	local model = BuildingFactory.BuildArena(sv.def, sv.cframe)
	model.Parent = folder
	local top = model:FindFirstChild("Top") :: BasePart?
	sv.top = top
	local adornee: BasePart = top or (model.PrimaryPart :: BasePart)
	local _, content = billboard(adornee, 11, 3.6, 2.5)
	sv.content = content
	label(content, "⚔️ Combat Arena", 0, 0.55, P.White)
	sv.arenaBest = label(content, "", 0.55, 0.45, P.PriceGold)

	local front = Layout.SlotFrontPosition(sv.area, sv.index)
	local spot = anchor(folder, "ArenaSpot", front + Vector3.new(0, 2.5, 0))
	local prompt = makePrompt(spot, "Enter arena", "Combat Arena", 0.2, nil, nil, 12)
	prompt.Triggered:Connect(function()
		local c = cb()
		if c then
			c.OnArena()
		end
	end)
end

local function refreshBuilding(sv: SlotVisual, section: Types.SectionState)
	local ui = sv.ui
	if not ui then
		return
	end
	local level = math.max(1, math.floor(tonumber(section.level) or 1))
	local stats = Economy.SectionStats(sv.id, level)
	local capacity = math.max(1, stats.stockCapacity)
	local stock = math.clamp(math.floor(tonumber(section.stock) or 0), 0, capacity)
	local maxed = level >= Economy.SectionMaxLevel
	local employee = section.employee == true

	ui.levelLabel.Text = ("Level %d%s%s"):format(
		level,
		if maxed then " (MAX)" else "",
		if employee then "  🧑‍💼" else ""
	)
	-- Per minute so the rate is a whole-dollar amount (no cents).
	ui.rateLabel.Text = Format.Money(stats.incomePerSecond * 60) .. "/min"
	local fraction = stock / capacity
	ui.stockFill.Size = UDim2.fromScale(math.max(fraction, 0.001), 1)
	ui.stockFill.Visible = stock > 0
	ui.stockFill.BackgroundColor3 = P.ButtonRed:Lerp(P.ButtonGreen, fraction)
	ui.stockText.Text = ("Stock %d/%d"):format(stock, capacity)

	if stock <= 0 then
		ui.alertLabel.Visible = true
		if employee then
			ui.alertLabel.Text = "Restocking..."
			ui.alertLabel.TextColor3 = P.White
			sv.flashing = false
		else
			ui.alertLabel.Text = "OUT OF STOCK!"
			ui.alertLabel.TextColor3 = P.ButtonRed
			sv.flashing = true
		end
	else
		ui.alertLabel.Visible = false
		sv.flashing = false
	end

	ui.restockPrompt.Enabled = stock < capacity
	ui.restockPrompt.ActionText = if stock <= 0 then "Restock (empty!)" else "Restock"
	local upgradePrice = Economy.SectionUpgradePrice(sv.id, level)
	if upgradePrice then
		ui.upgradePrompt.ActionText = ("Upgrade to Lv %d"):format(level + 1)
		ui.upgradePrompt.ObjectText = sv.def.name .. " - " .. Format.Money(upgradePrice)
	else
		ui.upgradePrompt.ActionText = "Max level"
		ui.upgradePrompt.ObjectText = sv.def.name .. " - MAX"
	end
end

-------------------------------------------------------------------------------
-- Employee spot
-------------------------------------------------------------------------------
local function buildHireTile(sv: SlotVisual)
	local folder = newFolder("Hire_" .. sv.id)
	sv.hireFolder = folder
	sv.hireState = "tile"
	local pos = Layout.SlotHirePosition(sv.area, sv.index)
	local tile =
		BuildingFactory.FlatTile(folder, CFrame.new(pos + Vector3.new(0, 0.42, 0)), 7, 7, P.HireTile, P.White, 0.45)
	tile.Name = "HireTile"
	-- Drawn on top for the same reason as the unlock pad label (buildPad).
	local board, content = billboard(tile, 6, 6, 4.6, PAD_LABEL_DISTANCE, true)
	board.Name = "HireInfo"
	local face = Theme.Label({
		Text = "🧑‍💼",
		MaxTextSize = 200,
		StrokeThickness = 0,
		Position = UDim2.fromScale(0.1, 0),
		Size = UDim2.fromScale(0.8, 0.62),
		Parent = content,
	})
	face.Name = "Businessman"
	label(content, "Hire me", 0.62, 0.38, P.White)
	local prompt = makePrompt(tile, "Hire employee", sv.def.name, 0, nil, nil, 9)
	prompt.Triggered:Connect(function()
		local c = cb()
		if c then
			c.OnHire(sv.id)
		end
	end)
end

local function buildEmployee(sv: SlotVisual)
	local folder = newFolder("Employee_" .. sv.id)
	sv.hireFolder = folder
	sv.hireState = "employee"
	local pos = Layout.SlotHirePosition(sv.area, sv.index)
	local offset = Layout.SLOT_OFFSETS[sv.index]
	local towardsPath = Vector3.new(-math.sign(offset.X), 0, 0)
	local cf = CFrame.lookAt(pos, pos + towardsPath)
	local model = BuildingFactory.BuildEmployee(cf)
	model.Parent = folder
	sv.employee = model
	sv.employeeBase = model:GetPivot()
	-- Small name tag.
	local headTop = model:FindFirstChild("Top") :: BasePart?
	if headTop then
		local _, content = billboard(headTop, 4.5, 1.2, 0.2, 80)
		label(content, "Employee", 0, 1, P.White)
	end
end

-------------------------------------------------------------------------------
-- Gates
-------------------------------------------------------------------------------
local function clearGate(gv: GateVisual, animate: boolean)
	local folder = gv.folder
	local model = gv.model
	gv.folder = nil
	gv.model = nil
	gv.prompt = nil
	gv.content = nil
	gv.title = nil
	gv.sub = nil
	if not folder then
		return
	end
	if not animate or not model then
		folder:Destroy()
		return
	end
	-- Open animation: the blocking wall goes at once, the gate sinks into the floor.
	local blocker = folder:FindFirstChild("GateBlocker")
	if blocker then
		blocker:Destroy()
	end
	local info = folder:FindFirstChild("GatePromptSpot")
	if info then
		info:Destroy()
	end
	local start = model:GetPivot()
	task.spawn(function()
		local began = os.clock()
		local duration = 0.8
		while os.clock() - began < duration and model.Parent do
			local alpha = (os.clock() - began) / duration
			model:PivotTo(start - Vector3.new(0, 12 * alpha * alpha, 0))
			task.wait()
		end
		folder:Destroy()
	end)
end

local function buildGate(gv: GateVisual, active: boolean)
	local folder = newFolder("Gate_" .. gv.area)
	gv.folder = folder
	local pos = Layout.GatePosition(gv.area)
	-- Face west: the side the player arrives from.
	local cf = CFrame.lookAt(pos, pos + Vector3.new(-1, 0, 0))
	local model = BuildingFactory.BuildGate(cf, Layout.GATE_WIDTH)
	model.Parent = folder
	gv.model = model

	-- Invisible wall that really blocks the opening (client parts collide
	-- with the local character).
	local blocker = BuildingFactory.Part(folder, {
		Name = "GateBlocker",
		Size = Vector3.new(1.2, 24, Layout.GATE_WIDTH + 2),
		CFrame = CFrame.new(pos + Vector3.new(0, 12, 0)),
		Color = P.GateIron,
		Transparency = 1,
		CanCollide = true,
		CastShadow = false,
	})
	blocker.CanQuery = true

	if active then
		local top = model:FindFirstChild("Top") :: BasePart?
		local adornee: BasePart = top or (model.PrimaryPart :: BasePart)
		local _, content = billboard(adornee, 18, 5, 2.5, 200)
		gv.content = content
		gv.title = label(content, "", 0, 0.55, P.White)
		gv.sub = label(content, "", 0.55, 0.45, P.PriceGold)
		local spot = anchor(folder, "GatePromptSpot", pos + Vector3.new(-3, 3, 0))
		local prompt = makePrompt(spot, "", "", 0.3, nil, nil, 12)
		local area = gv.area
		prompt.Triggered:Connect(function()
			local c = cb()
			if c then
				c.OnGate(area)
			end
		end)
		gv.prompt = prompt
	end
end

local function refreshGate(gv: GateVisual, data: PlayerData)
	local prompt, title, sub = gv.prompt, gv.title, gv.sub
	if not (prompt and title and sub) then
		return
	end
	local nextArea = Config.Areas[gv.area + 1]
	local price = Format.Money(Economy.GatePrice(gv.area))
	if data.base.barriers[tostring(gv.area)] == true then
		title.Text = "🔓 Unlock " .. nextArea.name
		sub.Text = price
		prompt.ActionText = "Unlock " .. nextArea.name .. ": " .. price
		prompt.ObjectText = "Gate"
	else
		title.Text = "⚔️ Defeat the barrier squad"
		sub.Text = "then unlock " .. nextArea.name .. " for " .. price
		prompt.ActionText = "⚔️ Defeat the barrier squad"
		prompt.ObjectText = "Gate to " .. nextArea.name
	end
end

-------------------------------------------------------------------------------
-- "LOCKED" signs in areas the player has not reached
-------------------------------------------------------------------------------
local function buildLockedSign(area: number): Folder
	local folder = newFolder("Locked_" .. area)
	local c = Layout.AreaCenter(area)
	BuildingFactory.Part(folder, {
		Name = "SignPole",
		Size = Vector3.new(0.8, 9, 0.8),
		CFrame = CFrame.new(c + Vector3.new(0, 4.5, 0)),
		Color = P.GateIron,
		CanCollide = false,
	})
	local board = BuildingFactory.Part(folder, {
		Name = "LockedBoard",
		Size = Vector3.new(0.6, 4, 12),
		CFrame = CFrame.new(c + Vector3.new(0, 8.5, 0)),
		Color = P.ButtonRed,
		CanCollide = false,
	})
	-- Left face points west (towards the player coming from earlier areas).
	BuildingFactory.FaceText(board, Enum.NormalId.Left, "🔒 LOCKED", P.ButtonRed)
	BuildingFactory.FaceText(board, Enum.NormalId.Right, "🔒 LOCKED", P.ButtonRed)
	local _, content = billboard(board, 22, 3, 4.5, 600)
	label(content, Config.Areas[area].name, 0, 1, P.White)
	return folder
end

-------------------------------------------------------------------------------
-- Update loop (30 Hz): flashing alerts and employee idle animation
-------------------------------------------------------------------------------
local function animate()
	local now = os.clock()
	local flashOn = (now % 0.8) < 0.5
	for _, sv in slots do
		local ui = sv.ui
		if ui and sv.flashing then
			ui.alertLabel.Visible = flashOn
		end
		local model, base = sv.employee, sv.employeeBase
		if model and base and model.Parent then
			local bob = math.sin(now * 2.2 + sv.bobPhase) * 0.12
			local hop = 0
			local since = now - sv.hopAt
			if since >= 0 and since < EMPLOYEE_HOP_TIME then
				local x = since / EMPLOYEE_HOP_TIME
				hop = 4 * 2.2 * x * (1 - x) -- parabola, 2.2 studs high
			end
			model:PivotTo(base + Vector3.new(0, bob + hop + 0.12, 0))
		end
	end
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function BaseVisuals.Start(cbs: Callbacks)
	if root then
		return
	end
	callbacks = cbs
	local folder = Instance.new("Folder")
	folder.Name = "BaseVisuals"
	folder.Parent = Workspace
	root = folder

	table.clear(slots)
	table.clear(slotById)
	for areaIndex, areaDef in Config.Areas do
		for slotIndex, slotDef in areaDef.slots do
			local sv: SlotVisual = {
				id = slotDef.id,
				area = areaIndex,
				index = slotIndex,
				def = slotDef,
				cframe = slotFrame(areaIndex, slotIndex),
				state = "none",
				hireState = "none",
				hopAt = -100,
				bobPhase = (areaIndex * 4 + slotIndex) * 0.9,
				flashing = false,
			}
			table.insert(slots, sv)
			slotById[slotDef.id] = sv
		end
	end
	for area = 1, #Config.Areas - 1 do
		gates[area] = { area = area, state = "none" }
	end

	BaseCustomers.Start(folder)
	heartbeat = RunService.Heartbeat:Connect(function(dt: number)
		accumulator += dt
		if accumulator < UPDATE_RATE then
			return
		end
		accumulator = 0
		animate()
	end)
end

function BaseVisuals.Update(data: PlayerData)
	if not root then
		return
	end
	lastData = data
	local base = data.base
	local highest = math.clamp(math.floor(tonumber(base.highestArea) or 1), 1, #Config.Areas)

	-- Slots -----------------------------------------------------------------
	for _, sv in slots do
		local desired: string
		local section = base.sections[sv.id]
		if sv.area > highest then
			desired = "none"
		elseif sv.def.kind == "arena" then
			desired = if base.arenaUnlocked then "arena" else "pad"
		elseif type(section) == "table" then
			desired = "building"
		else
			desired = "pad"
		end

		if desired ~= sv.state then
			clearSlotState(sv)
			sv.state = desired
			local ok, err = pcall(function()
				if desired == "pad" then
					buildPad(sv)
				elseif desired == "building" then
					buildBuilding(sv)
				elseif desired == "arena" then
					buildArena(sv)
				end
			end)
			if not ok then
				warn(("[BaseVisuals] could not build %s for %s: %s"):format(desired, sv.id, tostring(err)))
			end
			if sv.content and desired ~= "none" then
				Theme.Pop(sv.content)
			end
		end

		if desired == "building" and type(section) == "table" then
			refreshBuilding(sv, section)
		elseif desired == "arena" and sv.arenaBest then
			local best = tonumber(data.combat.highestLevel) or 0
			sv.arenaBest.Text = if best > 0 then ("Best: Level %d"):format(best) else "Fight waves for cash!"
		end

		-- Employee spot
		local desiredHire = "none"
		if desired == "building" and type(section) == "table" then
			desiredHire = if section.employee == true then "employee" else "tile"
		end
		if desiredHire ~= sv.hireState then
			clearHire(sv)
			if desiredHire == "tile" then
				buildHireTile(sv)
			elseif desiredHire == "employee" then
				buildEmployee(sv)
				sv.hopAt = os.clock() -- happy hop when hired
			end
			sv.hireState = desiredHire
		end
	end

	-- Gates -------------------------------------------------------------------
	for area, gv in gates do
		local desired = if area < highest then "none" elseif area == highest then "active" else "far"
		if desired ~= gv.state then
			local wasShown = gv.state ~= "none"
			clearGate(gv, desired == "none" and wasShown)
			gv.state = desired
			if desired ~= "none" then
				buildGate(gv, desired == "active")
			end
		end
		if desired == "active" then
			refreshGate(gv, data)
		end
	end

	-- Locked area signs ---------------------------------------------------------
	for area = 1, #Config.Areas do
		local shouldShow = area > highest
		local sign = lockedSigns[area]
		if shouldShow and not sign then
			lockedSigns[area] = buildLockedSign(area)
		elseif not shouldShow and sign then
			sign:Destroy()
			lockedSigns[area] = nil
		end
	end

	BaseCustomers.SetAreas(highest)
end

local function localRootPosition(): Vector3?
	local character = player.Character
	local hrp = character and character:FindFirstChild("HumanoidRootPart")
	if hrp and hrp:IsA("BasePart") then
		return hrp.Position
	end
	return nil
end

function BaseVisuals.ShowIncome(slotId: string, amount: number)
	local sv = slotById[slotId]
	local top = sv and sv.top
	if not top or type(amount) ~= "number" or amount <= 0 then
		return
	end
	local here = localRootPosition()
	if here and (here - top.Position).Magnitude > 140 then
		return -- too far away to see it anyway
	end
	Theme.FloatText(top.Position + Vector3.new(0, 1.5, 0), "+" .. Format.Money(amount), P.MoneyGreen)
end

function BaseVisuals.FloatAt(slotId: string, text: string, color: Color3?)
	local sv = slotById[slotId]
	if not sv then
		return
	end
	local pos = Layout.SlotFrontPosition(sv.area, sv.index) + Vector3.new(0, 5, 0)
	Theme.FloatText(pos, text, color or P.White)
end

function BaseVisuals.EmployeeHop(slotId: string)
	local sv = slotById[slotId]
	if sv then
		sv.hopAt = os.clock()
	end
end

function BaseVisuals.Pop(slotId: string)
	local sv = slotById[slotId]
	if sv and sv.content then
		Theme.Pop(sv.content)
	end
end

function BaseVisuals.GetData(): PlayerData?
	return lastData
end

function BaseVisuals.Destroy()
	if heartbeat then
		heartbeat:Disconnect()
		heartbeat = nil
	end
	BaseCustomers.Stop()
	if root then
		root:Destroy()
		root = nil
	end
	for _, sv in slots do
		sv.folder = nil
		sv.hireFolder = nil
		sv.employee = nil
		sv.ui = nil
	end
	table.clear(slots)
	table.clear(slotById)
	table.clear(gates)
	table.clear(lockedSigns)
	callbacks = nil
	lastData = nil
end

return BaseVisuals
