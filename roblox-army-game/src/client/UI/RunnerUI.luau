--!strict
--[[
	RunnerUI (client, plain module - not a controller)
	All screens of the army runner, styled like reference image 2 with Theme.

	Public API:
		RunnerUI.ShowHud(level) -> Hud
			Hud.SetLevel(level)          -- black "Level N" banner (top centre)
			Hud.SetProgress(fraction)    -- thin progress bar under the banner
			Hud.Destroy()

		RunnerUI.ShowPrepare({
			Data = PlayerData, Stats = { startTroops, damage, capacity },
			OnBuy = function(kind) end,  -- kind = "troops" | "damage" | "capacity"
			OnBack = function() end,
		}) -> Prepare
			Prepare.Update(data, stats, levelOverrides?)  -- refresh cards
			Prepare.SetBusy(kind, busy)  -- grey out a price button while buying
			Prepare.Destroy()
		  Layout (image 2): LEFT column "PERMANENT UPGRADES" with three cards
		  (orange header, blue body): Starting Troops, Damage, Carry Capacity.
		  RIGHT pink "Bonuses" card (vehicle bonus, 2x cash). BOTTOM centre the
		  tutorial hand with a <-> arrow "Drag or press A / D to start".
		  Bottom-right a small "Back" button.

		RunnerUI.ShowSummary({
			Level = number, Result = RunnerSim.Result, Paid = number,
			Boosted = boolean, Upgrades = { troops, damage, capacity },
			OnCollect = function() end,
		}) -> close()
		  "RUN COMPLETE!" panel. A sub-heading "COLLECTED THIS RUN" lists every
		  result line (what you picked up during this run and its cash value),
		  then the TOTAL actually paid. A second sub-heading "PERMANENT UPGRADES"
		  shows the bought starting stats, kept clearly apart (brief requirement).
		  The panel deliberately has no Context, so HUD's Theme.CloseModals("Base")
		  (run when the mode changes to Runner) never closes it.

	Screen-size scaling uses Theme.ResponsiveScale on plain holder frames only
	(one UIScale per object, shared with Theme.Pop / button press animations).
	The HUD banner sits below Roblox's top bar (Theme.TopInset()).
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Types = require(Shared.Types)
local RunnerSim = require(Shared.Runner.RunnerSim)

local Theme = require(script.Parent.Theme)

type PlayerData = Types.PlayerData
type Result = RunnerSim.Result
type RunnerStats = { startTroops: number, damage: number, capacity: number }

local P = Config.Palette
local _player = Players.LocalPlayer

local RunnerUI = {}

-------------------------------------------------------------------------------
-- Shared bits
-------------------------------------------------------------------------------
type UpgradeInfo = {
	kind: string,
	title: string,
	icon: string,
	value: (RunnerStats) -> string,
}

local UPGRADES: { UpgradeInfo } = {
	{
		kind = "troops",
		title = "Starting Troops",
		icon = "🪖",
		value = function(stats: RunnerStats): string
			return tostring(math.floor(stats.startTroops)) .. " troops"
		end,
	},
	{
		kind = "damage",
		title = "Damage",
		icon = "💥",
		value = function(stats: RunnerStats): string
			return "DMG " .. Format.Short(math.floor(stats.damage * 10 + 0.5) / 10)
		end,
	},
	{
		kind = "capacity",
		title = "Carry Capacity",
		icon = "💰",
		value = function(stats: RunnerStats): string
			return Format.Money(stats.capacity)
		end,
	},
}

local function getGui(): ScreenGui
	return Theme.ScreenGui("RunnerUI", 8)
end

-- Screen-size scaling goes through Theme.ResponsiveScale, which reuses the
-- object's single "ThemeScale" UIScale (Roblox applies only one UIScale per
-- object) and stops listening when the screen is destroyed. It is only put
-- on plain holder frames, never on a button or anything Theme.Pop animates.
local function responsive(gui: GuiObject)
	Theme.ResponsiveScale(gui)
end

local function upgradeLevel(data: PlayerData, kind: string, overrides: { [string]: number }?): number
	local levels = data.runner.upgrades :: { [string]: number }
	local level = levels[kind] or 0
	if overrides and overrides[kind] then
		level = math.max(level, overrides[kind])
	end
	return level
end

local function vehicleBonusFor(data: PlayerData): (string?, number)
	local equipped = data.vehicles and data.vehicles.equipped
	if not equipped then
		return nil, 0
	end
	for _, def in Config.Vehicles do
		if def.id == equipped then
			return def.name, def.runnerBonus
		end
	end
	return nil, 0
end

local function safeEffect(kind: string, level: number): string
	local ok, text = pcall(Economy.RunnerUpgradeEffect, kind, level)
	if ok and type(text) == "string" and text ~= "" then
		return text
	end
	return "Small boost"
end

-------------------------------------------------------------------------------
-- HUD: "Level N" banner + progress bar
-------------------------------------------------------------------------------
export type Hud = {
	SetLevel: (level: number) -> (),
	SetProgress: (fraction: number) -> (),
	Destroy: () -> (),
}

function RunnerUI.ShowHud(level: number): Hud
	local root = Instance.new("Frame")
	root.Name = "RunnerHud"
	root.BackgroundTransparency = 1
	root.AnchorPoint = Vector2.new(0.5, 0)
	-- RunnerUI ignores the GUI inset: start below Roblox's top bar.
	root.Position = UDim2.new(0.5, 0, 0, Theme.TopInset() + 6)
	root.Size = UDim2.fromOffset(300, 80)
	root.Parent = getGui()
	responsive(root)

	local _, setBanner = Theme.Banner({
		Text = "Level " .. level,
		Size = UDim2.fromOffset(290, 48),
		Position = UDim2.fromScale(0.5, 0),
		AnchorPoint = Vector2.new(0.5, 0),
		Parent = root,
	})

	local bar = Instance.new("Frame")
	bar.Name = "Progress"
	bar.BackgroundColor3 = P.PillBlack
	bar.BackgroundTransparency = 0.2
	bar.AnchorPoint = Vector2.new(0.5, 0)
	bar.Position = UDim2.new(0.5, 0, 0, 56)
	bar.Size = UDim2.fromOffset(240, 14)
	bar.Parent = root
	Theme.Corner(bar, 0.5)
	Theme.Stroke(bar, P.CardOutline, 2, true)
	local fill = Instance.new("Frame")
	fill.BackgroundColor3 = P.MoneyGreen
	fill.Size = UDim2.fromScale(0, 1)
	fill.Parent = bar
	Theme.Corner(fill, 0.5)

	return {
		SetLevel = function(newLevel: number)
			setBanner("Level " .. newLevel)
		end,
		SetProgress = function(fraction: number)
			fill.Size = UDim2.fromScale(math.clamp(fraction, 0, 1), 1)
		end,
		Destroy = function()
			root:Destroy()
		end,
	}
end

-------------------------------------------------------------------------------
-- Pre-run screen
-------------------------------------------------------------------------------
export type Prepare = {
	Update: (data: PlayerData, stats: RunnerStats, overrides: { [string]: number }?) -> (),
	SetBusy: (kind: string, busy: boolean) -> (),
	Destroy: () -> (),
}

type CardRefs = {
	info: UpgradeInfo,
	value: TextLabel,
	level: TextLabel,
	nextText: TextLabel,
	button: TextButton,
	busy: boolean,
}

function RunnerUI.ShowPrepare(props: {
	Data: PlayerData,
	Stats: RunnerStats,
	OnBuy: (kind: string) -> (),
	OnBack: () -> (),
}): Prepare
	local root = Instance.new("Frame")
	root.Name = "RunnerPrepare"
	root.BackgroundTransparency = 1
	root.Size = UDim2.fromScale(1, 1)
	root.Parent = getGui()

	---------------------------------------------------------------------------
	-- LEFT: permanent upgrade cards
	---------------------------------------------------------------------------
	local left = Instance.new("Frame")
	left.Name = "Upgrades"
	left.BackgroundTransparency = 1
	left.AnchorPoint = Vector2.new(0, 0.5)
	left.Position = UDim2.new(0, 16, 0.5, 10)
	left.Size = UDim2.fromOffset(214, 660)
	left.Parent = root
	responsive(left)
	Theme.List(left, "Vertical", 8, Enum.HorizontalAlignment.Left, Enum.VerticalAlignment.Center)

	local heading = Instance.new("Frame")
	heading.Name = "Heading"
	heading.BackgroundColor3 = P.PillBlack
	heading.BackgroundTransparency = 0.1
	heading.Size = UDim2.fromOffset(214, 58)
	heading.LayoutOrder = 0
	heading.Parent = left
	Theme.Corner(heading, 12)
	Theme.Stroke(heading, P.CardOutline, 2, true)
	Theme.Label({
		Text = "PERMANENT UPGRADES",
		TextColor3 = P.PriceGold,
		MaxTextSize = 22,
		Size = UDim2.new(1, -12, 0, 30),
		Position = UDim2.fromOffset(6, 4),
		Parent = heading,
	})
	Theme.Label({
		Text = "Kept forever · small boost per level",
		MaxTextSize = 14,
		StrokeThickness = 1.5,
		Size = UDim2.new(1, -12, 0, 18),
		Position = UDim2.fromOffset(6, 34),
		Parent = heading,
	})

	local cards: { CardRefs } = {}
	for index, info in UPGRADES do
		local card, body = Theme.Card({
			Name = info.kind,
			Title = info.title,
			HeaderHeight = 34,
			TitleSize = 22,
			Size = UDim2.fromOffset(206, 182),
			LayoutOrder = index,
			Parent = left,
		})
		card.Active = true
		Theme.List(body, "Vertical", 3)

		local top = Instance.new("Frame")
		top.BackgroundTransparency = 1
		top.Size = UDim2.new(1, 0, 0, 42)
		top.LayoutOrder = 1
		top.Parent = body
		Theme.Label({
			Text = info.icon,
			StrokeThickness = 0,
			MaxTextSize = 36,
			Size = UDim2.new(0, 42, 1, 0),
			Parent = top,
		})
		local value = Theme.Label({
			Text = "",
			MaxTextSize = 24,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, -48, 1, 0),
			Position = UDim2.fromOffset(48, 0),
			Parent = top,
		})
		local level = Theme.Label({
			Text = "LEVEL 1 ⬆",
			MaxTextSize = 20,
			Size = UDim2.new(1, 0, 0, 22),
			LayoutOrder = 2,
			Parent = body,
		})
		local nextText = Theme.Label({
			Text = "",
			MaxTextSize = 15,
			StrokeThickness = 1.5,
			TextColor3 = Color3.fromRGB(220, 238, 255),
			Size = UDim2.new(1, 0, 0, 18),
			LayoutOrder = 3,
			Parent = body,
		})
		local refs: CardRefs
		local button = Theme.Button({
			Name = "Buy",
			Text = "",
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			Size = UDim2.new(1, 0, 0, 40),
			MaxTextSize = 22,
			LayoutOrder = 4,
			OnClick = function()
				if refs and not refs.busy then
					props.OnBuy(info.kind)
				end
			end,
			Parent = body,
		})
		Theme.CoinIcon({
			Size = UDim2.fromOffset(26, 26),
			AnchorPoint = Vector2.new(0, 0.5),
			Position = UDim2.new(0, 8, 0.5, -2),
			Parent = button,
		})
		refs = { info = info, value = value, level = level, nextText = nextText, button = button, busy = false }
		table.insert(cards, refs)
	end

	---------------------------------------------------------------------------
	-- RIGHT: bonuses card (pink header like "Skins" in image 2)
	---------------------------------------------------------------------------
	local rightHolder = Instance.new("Frame")
	rightHolder.Name = "BonusHolder"
	rightHolder.BackgroundTransparency = 1
	rightHolder.AnchorPoint = Vector2.new(1, 0.5)
	rightHolder.Position = UDim2.new(1, -16, 0.5, 60)
	rightHolder.Size = UDim2.fromOffset(206, 230)
	rightHolder.Parent = root
	responsive(rightHolder)
	local _, bonusBody = Theme.Card({
		Name = "Bonuses",
		Title = "Bonuses",
		HeaderColor = P.CardPink,
		HeaderDarkColor = P.CardPinkDark,
		HeaderHeight = 38,
		TitleSize = 26,
		Size = UDim2.fromScale(1, 1),
		Parent = rightHolder,
	})
	Theme.List(bonusBody, "Vertical", 4)
	local function bonusRow(order: number, icon: string, title: string): TextLabel
		local row = Instance.new("Frame")
		row.BackgroundTransparency = 1
		row.Size = UDim2.new(1, 0, 0, 82)
		row.LayoutOrder = order
		row.Parent = bonusBody
		Theme.Label({ Text = icon, StrokeThickness = 0, MaxTextSize = 34, Size = UDim2.new(0, 40, 0, 40), Parent = row })
		Theme.Label({
			Text = title,
			MaxTextSize = 20,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, -44, 0, 40),
			Position = UDim2.fromOffset(44, 0),
			Parent = row,
		})
		return Theme.Label({
			Text = "",
			MaxTextSize = 20,
			TextColor3 = P.MoneyGreen,
			Size = UDim2.new(1, 0, 0, 36),
			Position = UDim2.fromOffset(0, 42),
			Parent = row,
		})
	end
	local vehicleValue = bonusRow(1, "🚙", "Vehicle")
	local boostValue = bonusRow(2, "⚡", "2x Cash")

	---------------------------------------------------------------------------
	-- BOTTOM: tutorial hand + <-> arrow
	---------------------------------------------------------------------------
	local hint = Instance.new("Frame")
	hint.Name = "Hint"
	hint.BackgroundTransparency = 1
	hint.AnchorPoint = Vector2.new(0.5, 1)
	hint.Position = UDim2.new(0.5, 0, 1, -24)
	hint.Size = UDim2.fromOffset(420, 190)
	hint.Parent = root
	responsive(hint)

	local arrowBar = Instance.new("Frame")
	arrowBar.Name = "Arrow"
	arrowBar.BackgroundColor3 = P.White
	arrowBar.AnchorPoint = Vector2.new(0.5, 0.5)
	arrowBar.Position = UDim2.new(0.5, 0, 0, 52)
	arrowBar.Size = UDim2.fromOffset(250, 14)
	arrowBar.Parent = hint
	Theme.Corner(arrowBar, 0.5)
	Theme.Stroke(arrowBar, P.TextStroke, 3, true)
	for _, side in { -1, 1 } do
		Theme.Label({
			Text = if side < 0 then "◀" else "▶",
			MaxTextSize = 64,
			StrokeThickness = 3,
			Size = UDim2.fromOffset(64, 64),
			AnchorPoint = Vector2.new(0.5, 0.5),
			Position = UDim2.new(0.5, side * 140, 0, 50),
			Parent = hint,
		})
	end
	local hand = Theme.Label({
		Text = "👆",
		StrokeThickness = 0,
		MaxTextSize = 72,
		Size = UDim2.fromOffset(84, 84),
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.new(0.5, -60, 0, 50),
		Parent = hint,
	})
	-- Slide the hand left <-> right forever (stops when destroyed).
	local slide = TweenService:Create(
		hand,
		TweenInfo.new(0.9, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut, -1, true),
		{ Position = UDim2.new(0.5, 60, 0, 50) }
	)
	slide:Play()
	Theme.Label({
		Text = "Drag or press A / D to start",
		MaxTextSize = 30,
		Size = UDim2.new(1, 0, 0, 40),
		Position = UDim2.new(0, 0, 1, -40),
		Parent = hint,
	})

	---------------------------------------------------------------------------
	-- Back button (bottom-right, clear of the upgrade column)
	---------------------------------------------------------------------------
	local backHolder = Instance.new("Frame")
	backHolder.BackgroundTransparency = 1
	backHolder.AnchorPoint = Vector2.new(1, 1)
	backHolder.Position = UDim2.new(1, -16, 1, -16)
	backHolder.Size = UDim2.fromOffset(130, 56)
	backHolder.Parent = root
	responsive(backHolder)
	Theme.Button({
		Name = "Back",
		Text = "Back",
		Color = P.ButtonRed,
		DarkColor = P.ButtonRedDark,
		Size = UDim2.fromScale(1, 1),
		MaxTextSize = 26,
		OnClick = function()
			props.OnBack()
		end,
		Parent = backHolder,
	})

	---------------------------------------------------------------------------
	-- Refresh
	---------------------------------------------------------------------------
	local lastData = props.Data
	local lastStats = props.Stats
	local lastOverrides: { [string]: number }? = nil

	local function refresh()
		local data = lastData
		for _, refs in cards do
			local kind = refs.info.kind
			local level = upgradeLevel(data, kind, lastOverrides)
			refs.value.Text = refs.info.value(lastStats)
			-- Upgrade levels are stored from 0; shown from LEVEL 1. [PROPOSAL]
			refs.level.Text = "LEVEL " .. (level + 1) .. " ⬆"
			local price: number? = nil
			if level < Economy.RunnerUpgradeMaxLevel then
				local ok, value = pcall(Economy.RunnerUpgradePrice, kind, level)
				if ok and type(value) == "number" then
					price = value
				end
			end
			if price == nil then
				refs.nextText.Text = "Fully upgraded!"
				Theme.SetButtonText(refs.button, "MAX")
				Theme.SetButtonEnabled(refs.button, false)
			else
				refs.nextText.Text = "Next: " .. safeEffect(kind, level)
				Theme.SetButtonText(refs.button, "     " .. Format.Money(price))
				local affordable = data.cash + 1e-6 >= price
				Theme.SetButtonEnabled(refs.button, affordable and not refs.busy)
			end
		end

		local vehicleName, bonus = vehicleBonusFor(data)
		if vehicleName and bonus > 0 then
			vehicleValue.Text = "+" .. Format.Percent(bonus) .. " cash"
			vehicleValue.TextColor3 = P.MoneyGreen
		else
			vehicleValue.Text = "None (Shop)"
			vehicleValue.TextColor3 = P.White
		end
		local boostSeconds = data.boosts and data.boosts.multiplierSeconds or 0
		if boostSeconds > 0 then
			boostValue.Text = "ON " .. Format.Clock(boostSeconds)
			boostValue.TextColor3 = P.MoneyGreen
		else
			boostValue.Text = "Off"
			boostValue.TextColor3 = P.White
		end
	end
	refresh()

	return {
		Update = function(data: PlayerData, stats: RunnerStats, overrides: { [string]: number }?)
			lastData = data
			lastStats = stats
			lastOverrides = overrides
			if root.Parent then
				refresh()
			end
		end,
		SetBusy = function(kind: string, busy: boolean)
			for _, refs in cards do
				if refs.info.kind == kind then
					refs.busy = busy
					if not busy then
						Theme.Pop(refs.button)
					end
				end
			end
			if root.Parent then
				refresh()
			end
		end,
		Destroy = function()
			slide:Cancel()
			root:Destroy()
		end,
	}
end

-------------------------------------------------------------------------------
-- Completion summary
-------------------------------------------------------------------------------
local LINE_ICONS: { [string]: string } = {
	troops = "🪖",
	weapon = "🔫",
	damage = "💥",
	cash = "💰",
	enemies = "🎯",
	boss = "🏰",
	bonus = "🚙",
}

local function subHeading(parent: Instance, order: number, text: string, color: Color3)
	local pill = Instance.new("Frame")
	pill.Name = "SubHeading"
	pill.BackgroundColor3 = P.PillBlack
	pill.BackgroundTransparency = 0.15
	pill.Size = UDim2.new(1, 0, 0, 32)
	pill.LayoutOrder = order
	pill.Parent = parent
	Theme.Corner(pill, 0.5)
	Theme.Label({
		Text = text,
		TextColor3 = color,
		MaxTextSize = 22,
		Size = UDim2.new(1, -16, 1, -4),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = pill,
	})
end

local function moneyText(value: number): string
	if value < 0 then
		return Format.Money(value)
	end
	return "+" .. Format.Money(value)
end

local function summaryRow(parent: Instance, order: number, icon: string, label: string, detail: string, value: number?)
	local row = Instance.new("Frame")
	row.Name = "Line"
	row.BackgroundColor3 = P.CardBlueDark
	row.BackgroundTransparency = 0.35
	row.Size = UDim2.new(1, -8, 0, 46)
	row.LayoutOrder = order
	row.Parent = parent
	Theme.Corner(row, 10)
	Theme.Label({ Text = icon, StrokeThickness = 0, MaxTextSize = 28, Size = UDim2.fromOffset(40, 46), Parent = row })
	Theme.Label({
		Text = label,
		MaxTextSize = 20,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.62, -44, 0, 24),
		Position = UDim2.fromOffset(42, 2),
		Parent = row,
	})
	Theme.Label({
		Text = detail,
		MaxTextSize = 15,
		StrokeThickness = 1.5,
		TextColor3 = Color3.fromRGB(220, 238, 255),
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.62, -44, 0, 18),
		Position = UDim2.fromOffset(42, 26),
		Parent = row,
	})
	if value then
		Theme.Label({
			Text = moneyText(value),
			TextColor3 = if value >= 0 then P.MoneyGreen else P.ButtonRed,
			MaxTextSize = 24,
			TextXAlignment = Enum.TextXAlignment.Right,
			Size = UDim2.new(0.38, -46, 1, -8),
			Position = UDim2.new(0.62, 0, 0, 4),
			Parent = row,
		})
		Theme.CoinIcon({
			Size = UDim2.fromOffset(28, 28),
			AnchorPoint = Vector2.new(1, 0.5),
			Position = UDim2.new(1, -8, 0.5, 0),
			Parent = row,
		})
	end
end

function RunnerUI.ShowSummary(props: {
	Level: number,
	Result: Result,
	Paid: number,
	Boosted: boolean,
	Upgrades: { troops: number, damage: number, capacity: number },
	OnCollect: () -> (),
}): () -> ()
	local collected = false
	local _, content, close = Theme.Panel({
		Name = "RunnerSummary",
		Title = "RUN COMPLETE!",
		Closable = false,
		Size = UDim2.fromOffset(600, 640),
	})
	Theme.List(content, "Vertical", 8)

	Theme.Label({
		Text = "Level " .. props.Level,
		TextColor3 = P.PriceGold,
		MaxTextSize = 28,
		Size = UDim2.new(1, 0, 0, 30),
		LayoutOrder = 1,
		Parent = content,
	})

	subHeading(content, 2, "COLLECTED THIS RUN (this run only)", P.MoneyGreen)

	local list = Instance.new("ScrollingFrame")
	list.Name = "Lines"
	list.BackgroundTransparency = 1
	list.BorderSizePixel = 0
	list.Size = UDim2.new(1, 0, 0, 232)
	list.CanvasSize = UDim2.new()
	list.AutomaticCanvasSize = Enum.AutomaticSize.Y
	list.ScrollingDirection = Enum.ScrollingDirection.Y
	list.ScrollBarThickness = 6
	list.LayoutOrder = 3
	list.Parent = content
	Theme.List(list, "Vertical", 5)

	local order = 0
	local result = props.Result
	local lines: { RunnerSim.Line } = if type(result) == "table" and type(result.lines) == "table"
		then result.lines
		else {}
	for _, line in lines do
		order += 1
		local value = if type(line.value) == "number" then line.value else 0
		local label, detail = tostring(line.label), tostring(line.detail or "")
		if line.kind == "bonus" then
			-- The only payout bonus is the equipped vehicle's runner bonus.
			label, detail = "Vehicle bonus", label .. " payout"
		end
		summaryRow(list, order, LINE_ICONS[line.kind] or "⭐", label, detail, value)
	end
	local total = if type(result) == "table" and type(result.total) == "number" then result.total else 0
	if props.Boosted then
		order += 1
		summaryRow(list, order, "⚡", "2x CASH", "Boost active - payout doubled", math.max(0, props.Paid - total))
	end
	if order == 0 then
		summaryRow(list, 1, "🪖", "No pickups this time", "Try the blue gates!", nil)
	end

	-- TOTAL (what was actually added to your cash).
	local totalRow = Instance.new("Frame")
	totalRow.Name = "Total"
	totalRow.BackgroundColor3 = P.PillBlack
	totalRow.BackgroundTransparency = 0.1
	totalRow.Size = UDim2.new(1, 0, 0, 60)
	totalRow.LayoutOrder = 4
	totalRow.Parent = content
	Theme.Corner(totalRow, 14)
	Theme.Stroke(totalRow, P.CardOutline, 2, true)
	Theme.Label({
		Text = "TOTAL",
		MaxTextSize = 32,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.4, -16, 1, -10),
		Position = UDim2.fromOffset(16, 5),
		Parent = totalRow,
	})
	Theme.Label({
		Text = Format.Money(props.Paid),
		TextColor3 = P.MoneyGreen,
		MaxTextSize = 40,
		TextXAlignment = Enum.TextXAlignment.Right,
		Size = UDim2.new(0.6, -66, 1, -10),
		Position = UDim2.new(0.4, 0, 0, 5),
		Parent = totalRow,
	})
	Theme.CoinIcon({
		Size = UDim2.fromOffset(40, 40),
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, -12, 0.5, 0),
		Parent = totalRow,
	})

	-- PERMANENT upgrades, clearly separated from what was collected this run.
	subHeading(content, 5, "PERMANENT UPGRADES (kept forever)", P.PriceGold)
	local upgrades = props.Upgrades
	Theme.Label({
		Text = ("🪖 Troops Lv %d   ·   💥 Damage Lv %d   ·   💰 Carry Lv %d"):format(
			(upgrades.troops or 0) + 1,
			(upgrades.damage or 0) + 1,
			(upgrades.capacity or 0) + 1
		),
		MaxTextSize = 20,
		Size = UDim2.new(1, 0, 0, 28),
		LayoutOrder = 6,
		Parent = content,
	})

	local buttonRow = Instance.new("Frame")
	buttonRow.BackgroundTransparency = 1
	buttonRow.Size = UDim2.new(1, 0, 0, 66)
	buttonRow.LayoutOrder = 7
	buttonRow.Parent = content
	Theme.Button({
		Name = "Collect",
		Text = "COLLECT",
		Color = P.ButtonGreen,
		DarkColor = P.ButtonGreenDark,
		Size = UDim2.fromOffset(240, 64),
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.fromScale(0.5, 0),
		MaxTextSize = 32,
		OnClick = function()
			if collected then
				return
			end
			collected = true
			close()
			props.OnCollect()
		end,
		Parent = buttonRow,
	})

	return close
end

return RunnerUI
