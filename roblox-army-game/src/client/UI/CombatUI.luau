--!strict
--[[
	CombatUI (client, plain module)
	Every screen of the combat mode, built with Theme (same look as the rest of
	the game: chunky outlined text, orange/blue cards, black banners).

	API:
		CombatUI.OpenArenaMenu({ highestLevel, onFight = function(level) end, onClose? }) -> close()
			Level select for the repeatable Combat Arena: levels 1..highestLevel+1,
			difficulty skulls, cash reward, chest, "FIGHT".
		CombatUI.ConfirmBarrier({ level, nextAreaName, reward, onConfirm, onClose? }) -> close()
			Both are "Base" dialogs (Theme Context = "Base"): opening one again
			replaces the old copy, and the HUD closes them when you leave the base.
		CombatUI.LobbyBar({ onInventory, onStart, onLeave }) -> { Destroy: () -> () }
			Small bar shown before round 1: [Inventory] [Start] [Leave].
		CombatUI.Hud({ level, mode, nextAreaName, waveCount, onLeave }) -> Hud
			The wave banner and the Leave button sit below Roblox's top bar
			(Theme.TopInset(), updated when the top bar changes size).
			Hud.SetWave(wave, waveCount)       black "WAVE 2/5" banner
			Hud.Countdown(startsAt, wave, boss) big 3-2-1 / "WAVE 2" / "BOSS!" in the middle
			Hud.SetCash(amount)                 lime money tag above your character
			Hud.SetVitals(hp, maxHp, shield)    HP bar above your character + bottom bar
			Hud.SetCharacter(character)         (re)attach the billboard above the avatar
			Hud.Hint(text, seconds?)            small tip above the HP bar
			Hud.Hurt()                          red screen flash
			Hud.SetFightVisible(visible)        hide the fight HUD on the result screen
			Hud.Destroy()
		CombatUI.AbilityChoice({ options, timeout, onPick = function(id) end }) -> close()
			Three Theme cards (icon, name, description, stacks, "PICK") + timer.
		CombatUI.Result({ result, onChest = function(done) end, onContinue }) -> close()
			VICTORY / DEFEAT, cash earned, then the chest, then "CONTINUE".
			A defeat without a chest explains how to earn one (CombatDefs.ChestTier).
]]

local GuiService = game:GetService("GuiService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local CombatDefs = require(Shared.Combat.CombatDefs)
local Format = require(Shared.Format)

local P = Theme.Palette

local CombatUI = {}

local MAX_MENU_LEVELS = 60 -- how many levels the arena menu lists (newest first)

local function getGui(): ScreenGui
	return Theme.ScreenGui("CombatUI", 30)
end

local CHEST_NAMES = { wood = "Wooden chest", silver = "Silver chest", gold = "Gold chest" }
local CHEST_ICONS = { wood = "📦", silver = "🎁", gold = "👑" }

-------------------------------------------------------------------------------
-- Arena level select
-------------------------------------------------------------------------------
export type ArenaMenuOptions = {
	highestLevel: number,
	onFight: (level: number) -> (),
	onClose: (() -> ())?, -- called once when the menu closes (any reason)
}

function CombatUI.OpenArenaMenu(opts: ArenaMenuOptions): () -> ()
	local _, content, close = Theme.Panel({
		Name = "ArenaMenu",
		Context = "Base",
		Title = "⚔️ Combat Arena",
		HeaderColor = P.ButtonRed,
		HeaderDarkColor = P.ButtonRedDark,
		Size = UDim2.fromOffset(620, 520),
		OnClose = opts.onClose,
	})
	Theme.List(content, "Vertical", 8)

	Theme.Label({
		Text = "Stand still to shoot • Move to dodge • Win chests with armour & weapons",
		MaxTextSize = 20,
		Size = UDim2.new(1, 0, 0, 30),
		LayoutOrder = 1,
		ZIndex = 13,
		Parent = content,
	})

	local scroll = Instance.new("ScrollingFrame")
	scroll.Name = "Levels"
	scroll.BackgroundTransparency = 1
	scroll.BorderSizePixel = 0
	scroll.Size = UDim2.new(1, 0, 1, -40)
	scroll.LayoutOrder = 2
	scroll.ZIndex = 13
	scroll.ScrollBarThickness = 8
	scroll.ScrollBarImageColor3 = P.White
	scroll.AutomaticCanvasSize = Enum.AutomaticSize.Y
	scroll.CanvasSize = UDim2.new()
	scroll.ScrollingDirection = Enum.ScrollingDirection.Y
	scroll.Parent = content
	Theme.List(scroll, "Vertical", 8)
	Theme.Padding(scroll, 4)

	local highest = math.max(0, math.floor(opts.highestLevel))
	local top = highest + 1
	local bottom = math.max(1, top - MAX_MENU_LEVELS + 1)
	local order = 0
	for level = top, bottom, -1 do
		order += 1
		local isNew = level == top
		local boss = CombatDefs.HasBoss(level, "arena")
		local row = Instance.new("Frame")
		row.Name = "Level" .. level
		row.BackgroundColor3 = if isNew then P.CardOrange else P.CardBlueDark
		row.Size = UDim2.new(1, -12, 0, 64)
		row.LayoutOrder = order
		row.ZIndex = 14
		row.Parent = scroll
		Theme.Corner(row, 12)
		Theme.Stroke(row, P.CardOutline, 2, true)

		Theme.Label({
			Text = ("Level %d%s"):format(level, if boss then " 👑" else ""),
			MaxTextSize = 26,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(0.3, 0, 0.6, 0),
			Position = UDim2.new(0, 12, 0, 4),
			ZIndex = 15,
			Parent = row,
		})
		local skulls = string.rep("💀", CombatDefs.DifficultySkulls(level))
		Theme.Label({
			Text = (if isNew then "NEW  " else "✔ ") .. skulls,
			MaxTextSize = 18,
			StrokeThickness = 0,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(0.3, 0, 0.35, 0),
			Position = UDim2.new(0, 12, 0.6, 0),
			ZIndex = 15,
			Parent = row,
		})
		local plan = CombatDefs.LevelPlan(level, "arena")
		local chest = if boss then "gold" else "silver"
		Theme.Label({
			Text = ("%s  %s  •  %d waves"):format(Format.Money(plan.reward), CHEST_ICONS[chest], plan.waveCount),
			TextColor3 = P.PriceGold,
			MaxTextSize = 24,
			Size = UDim2.new(0.42, 0, 0.7, 0),
			Position = UDim2.new(0.31, 0, 0.15, 0),
			ZIndex = 15,
			Parent = row,
		})
		local fight = Theme.Button({
			Text = "FIGHT",
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			Size = UDim2.new(0.24, 0, 0, 50),
			AnchorPoint = Vector2.new(1, 0.5),
			Position = UDim2.new(1, -8, 0.5, 0),
			ZIndex = 16,
			MaxTextSize = 24,
			OnClick = function()
				close()
				opts.onFight(level)
			end,
			Parent = row,
		})
		fight.ZIndex = 16
	end
	return close
end

-------------------------------------------------------------------------------
-- Barrier confirm
-------------------------------------------------------------------------------
export type BarrierOptions = {
	level: number,
	nextAreaName: string,
	reward: number,
	onConfirm: () -> (),
	onClose: (() -> ())?, -- called once when the dialog closes (also right before onConfirm)
}

function CombatUI.ConfirmBarrier(opts: BarrierOptions): () -> ()
	return Theme.Confirm({
		Name = "BarrierConfirm",
		Context = "Base",
		Title = "🛡️ Barrier Squad",
		Body = ("Defeat the squad guarding %s!\nLevel %d • 4 waves • Officer boss\nUp to %s + a gold chest"):format(
			opts.nextAreaName,
			opts.level,
			Format.Money(opts.reward)
		),
		ConfirmText = "FIGHT!",
		OnConfirm = opts.onConfirm,
		OnClose = opts.onClose,
	})
end

-------------------------------------------------------------------------------
-- Lobby bar (before the first round)
-------------------------------------------------------------------------------
export type LobbyOptions = { onInventory: () -> (), onStart: () -> (), onLeave: () -> () }

function CombatUI.LobbyBar(opts: LobbyOptions): { Destroy: () -> () }
	local gui = getGui()
	local bar = Instance.new("Frame")
	bar.Name = "LobbyBar"
	bar.BackgroundColor3 = P.PillBlack
	bar.BackgroundTransparency = 0.1
	bar.AnchorPoint = Vector2.new(0.5, 1)
	bar.Position = UDim2.new(0.5, 0, 1, -24)
	bar.Size = UDim2.fromOffset(620, 96)
	bar.ZIndex = 5
	bar.Parent = gui
	Theme.Corner(bar, 18)
	Theme.Stroke(bar, P.CardOutline, 3, true)
	Theme.ResponsiveScale(bar)
	Theme.Padding(bar, 12)
	Theme.List(bar, "Horizontal", 12, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)

	Theme.Button({
		Text = "🎒 Inventory",
		Color = P.CardBlue,
		DarkColor = P.CardBlueDark,
		Size = UDim2.fromOffset(190, 64),
		LayoutOrder = 1,
		ZIndex = 6,
		OnClick = opts.onInventory,
		Parent = bar,
	})
	Theme.Button({
		Text = "⚔️ Start",
		Color = P.ButtonGreen,
		DarkColor = P.ButtonGreenDark,
		Size = UDim2.fromOffset(190, 64),
		LayoutOrder = 2,
		ZIndex = 6,
		OnClick = opts.onStart,
		Parent = bar,
	})
	Theme.Button({
		Text = "Leave",
		Color = P.ButtonRed,
		DarkColor = P.ButtonRedDark,
		Size = UDim2.fromOffset(170, 64),
		LayoutOrder = 3,
		ZIndex = 6,
		OnClick = opts.onLeave,
		Parent = bar,
	})
	Theme.Pop(bar)
	return {
		Destroy = function()
			bar:Destroy()
		end,
	}
end

-------------------------------------------------------------------------------
-- In-fight HUD
-------------------------------------------------------------------------------
export type HudOptions = {
	level: number,
	mode: string,
	nextAreaName: string?,
	waveCount: number,
	onLeave: () -> (),
}

export type Hud = {
	SetWave: (wave: number, waveCount: number) -> (),
	Countdown: (startsAt: number, wave: number, boss: boolean) -> (),
	SetCash: (amount: number) -> (),
	SetVitals: (hp: number, maxHp: number, shield: number) -> (),
	SetCharacter: (character: Model?) -> (),
	Hint: (text: string, seconds: number?) -> (),
	Hurt: () -> (),
	SetFightVisible: (visible: boolean) -> (),
	Destroy: () -> (),
}

local function hpColor(fraction: number): Color3
	if fraction < 0.3 then
		return P.ButtonRed
	elseif fraction < 0.6 then
		return P.Coin
	end
	return P.MoneyGreen
end

function CombatUI.Hud(opts: HudOptions): Hud
	local gui = getGui()
	local root = Instance.new("Frame")
	root.Name = "CombatHud"
	root.BackgroundTransparency = 1
	root.Size = UDim2.fromScale(1, 1)
	root.Parent = gui

	-- top centre: black wave banner + level label (below Roblox's top bar:
	-- the CombatUI gui ignores the GUI inset)
	local top = Instance.new("Frame")
	top.Name = "Top"
	top.BackgroundTransparency = 1
	top.AnchorPoint = Vector2.new(0.5, 0)
	top.Position = UDim2.new(0.5, 0, 0, Theme.TopInset() + 8)
	top.Size = UDim2.fromOffset(320, 90)
	top.Parent = root
	Theme.ResponsiveScale(top)
	local _, setBanner = Theme.Banner({
		Text = "GET READY",
		Size = UDim2.fromOffset(290, 50),
		Position = UDim2.fromScale(0.5, 0),
		AnchorPoint = Vector2.new(0.5, 0),
		Parent = top,
	})
	local levelText = if opts.mode == "barrier"
		then ("Barrier • Level %d"):format(opts.level)
		else ("Arena • Level %d"):format(opts.level)
	Theme.Label({
		Text = levelText,
		MaxTextSize = 22,
		Size = UDim2.new(1, 0, 0, 28),
		Position = UDim2.fromOffset(0, 56),
		Parent = top,
	})

	-- top left: retreat button
	local leave = Theme.Button({
		Name = "Leave",
		Text = "🏳️ Leave",
		Color = P.ButtonRed,
		DarkColor = P.ButtonRedDark,
		Size = UDim2.fromOffset(130, 50),
		Position = UDim2.fromOffset(16, Theme.TopInset() + 8),
		MaxTextSize = 22,
		OnClick = opts.onLeave,
		Parent = root,
	})
	Theme.ResponsiveScale(leave) -- reuses the button's own "ThemeScale" UIScale

	-- keep both below the top bar if it changes size
	local insetConnection = GuiService:GetPropertyChangedSignal("TopbarInset"):Connect(function()
		local y = Theme.TopInset() + 8
		top.Position = UDim2.new(0.5, 0, 0, y)
		leave.Position = UDim2.fromOffset(16, y)
	end)

	-- bottom bar: shield + HP bar
	local bottom = Instance.new("Frame")
	bottom.Name = "Bottom"
	bottom.BackgroundColor3 = P.PillBlack
	bottom.BackgroundTransparency = 0.1
	bottom.AnchorPoint = Vector2.new(0.5, 1)
	bottom.Position = UDim2.new(0.5, 0, 1, -20)
	bottom.Size = UDim2.fromOffset(460, 46)
	bottom.Parent = root
	Theme.Corner(bottom, 0.5)
	Theme.Stroke(bottom, P.CardOutline, 2.5, true)
	Theme.ResponsiveScale(bottom)
	local barBack = Instance.new("Frame")
	barBack.Name = "BarBack"
	barBack.BackgroundColor3 = P.Shade
	barBack.AnchorPoint = Vector2.new(0, 0.5)
	barBack.Position = UDim2.new(0, 64, 0.5, 0)
	barBack.Size = UDim2.new(1, -78, 0, 26)
	barBack.Parent = bottom
	Theme.Corner(barBack, 0.5)
	local barFill = Instance.new("Frame")
	barFill.Name = "Fill"
	barFill.BackgroundColor3 = P.MoneyGreen
	barFill.Size = UDim2.fromScale(1, 1)
	barFill.Parent = barBack
	Theme.Corner(barFill, 0.5)
	local barText = Theme.Label({
		Text = "",
		MaxTextSize = 20,
		Size = UDim2.fromScale(1, 1),
		ZIndex = 3,
		Parent = barBack,
	})
	local shieldText = Theme.Label({
		Text = "❤️",
		MaxTextSize = 26,
		StrokeThickness = 0,
		Size = UDim2.new(0, 56, 1, 0),
		Position = UDim2.fromOffset(6, 0),
		Parent = bottom,
	})

	local hint = Theme.Label({
		Text = "",
		MaxTextSize = 24,
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -76),
		Size = UDim2.fromOffset(560, 34),
		Visible = false,
		Parent = root,
	})

	-- centre: countdown / wave call-outs
	local center = Theme.Label({
		Text = "",
		MaxTextSize = 96,
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.38),
		Size = UDim2.fromOffset(600, 120),
		StrokeThickness = 5,
		Visible = false,
		Parent = root,
	})

	-- red flash when hurt
	local flash = Instance.new("Frame")
	flash.Name = "Hurt"
	flash.BackgroundColor3 = P.ButtonRed
	flash.BackgroundTransparency = 1
	flash.Size = UDim2.fromScale(1, 1)
	flash.ZIndex = 0
	flash.Active = false
	flash.Parent = root

	-- billboard above the avatar: lime money tag (image 2) + HP bar
	local board: BillboardGui? = nil
	local tagSetter: ((string) -> ())? = nil
	local miniFill: Frame? = nil
	local miniShield: TextLabel? = nil
	local cashText = Format.Money(0)
	local vitals = { hp = 0, maxHp = 0, shield = 0 }

	local function applyMini()
		local fill = miniFill
		if fill then
			local fraction = if vitals.maxHp > 0 then math.clamp(vitals.hp / vitals.maxHp, 0, 1) else 0
			fill.Size = UDim2.fromScale(fraction, 1)
			fill.BackgroundColor3 = hpColor(fraction)
		end
		local shield = miniShield
		if shield then
			shield.Text = string.rep("🛡️", math.min(3, vitals.shield))
		end
	end

	local countdownToken = 0
	local hintToken = 0
	local hud = {} :: Hud

	function hud.SetWave(wave: number, waveCount: number)
		setBanner(("WAVE %d/%d"):format(math.max(1, wave), waveCount))
	end

	function hud.Countdown(startsAt: number, wave: number, boss: boolean)
		countdownToken += 1
		local token = countdownToken
		hud.SetWave(wave, opts.waveCount)
		center.Visible = true
		task.spawn(function()
			while token == countdownToken do
				local left = startsAt - Workspace:GetServerTimeNow()
				if left > 1.2 and wave == 1 then
					center.Text = tostring(math.ceil(left - 0.2))
					center.TextColor3 = P.White
				elseif left > 0 then
					center.Text = if boss then "⚠️ BOSS WAVE" else ("WAVE %d"):format(wave)
					center.TextColor3 = if boss then P.ButtonRed else P.PriceGold
				elseif left > -0.8 then
					center.Text = "FIGHT!"
					center.TextColor3 = P.MoneyGreen
				else
					center.Visible = false
					break
				end
				task.wait(0.1)
			end
		end)
	end

	function hud.SetCash(amount: number)
		cashText = Format.Money(amount)
		local setter = tagSetter
		if setter then
			setter(cashText)
		end
	end

	function hud.SetVitals(hp: number, maxHp: number, shield: number)
		vitals.hp, vitals.maxHp, vitals.shield = hp, maxHp, shield
		local fraction = if maxHp > 0 then math.clamp(hp / maxHp, 0, 1) else 0
		barFill.Size = UDim2.fromScale(fraction, 1)
		barFill.BackgroundColor3 = hpColor(fraction)
		barText.Text = ("%s / %s"):format(Format.Short(math.ceil(hp)), Format.Short(math.ceil(maxHp)))
		shieldText.Text = if shield > 0 then "🛡️" .. (if shield > 1 then tostring(shield) else "") else "❤️"
		applyMini()
	end

	function hud.SetCharacter(character: Model?)
		if board then
			board:Destroy()
			board = nil
			tagSetter = nil
			miniFill = nil
			miniShield = nil
		end
		local rootPart = character and character:FindFirstChild("HumanoidRootPart")
		if not rootPart or not rootPart:IsA("BasePart") then
			return
		end
		local created = Theme.Billboard({
			Name = "CombatPlayerBar",
			Adornee = rootPart,
			Parent = rootPart,
			Size = UDim2.fromOffset(130, 66),
			StudsOffset = Vector3.new(0, 4.6, 0),
			AlwaysOnTop = true,
			MaxDistance = 250,
		})
		local tag, setText = Theme.MoneyTag({
			Text = cashText,
			Size = UDim2.fromOffset(120, 36),
			AnchorPoint = Vector2.new(0.5, 0),
			Position = UDim2.fromScale(0.5, 0),
			MaxTextSize = 24,
			Parent = created,
		})
		tag.Name = "MoneyTag"
		local back = Instance.new("Frame")
		back.Name = "Hp"
		back.BackgroundColor3 = P.PillBlack
		back.AnchorPoint = Vector2.new(0.5, 1)
		back.Position = UDim2.new(0.5, 0, 1, -2)
		back.Size = UDim2.new(0.9, 0, 0, 14)
		back.Parent = created
		Theme.Corner(back, 0.5)
		Theme.Stroke(back, P.CardOutline, 1.5, true)
		local fill = Instance.new("Frame")
		fill.Name = "Fill"
		fill.BackgroundColor3 = P.MoneyGreen
		fill.Size = UDim2.fromScale(1, 1)
		fill.Parent = back
		Theme.Corner(fill, 0.5)
		local shieldLabel = Theme.Label({
			Text = "",
			MaxTextSize = 16,
			StrokeThickness = 0,
			AnchorPoint = Vector2.new(1, 0.5),
			Position = UDim2.new(0, -2, 0.5, 0),
			Size = UDim2.fromOffset(50, 18),
			TextXAlignment = Enum.TextXAlignment.Right,
			Parent = back,
		})
		board = created
		tagSetter = setText
		miniFill = fill
		miniShield = shieldLabel
		applyMini()
	end

	function hud.Hint(text: string, seconds: number?)
		hintToken += 1
		local token = hintToken
		hint.Text = text
		hint.Visible = true
		task.delay(seconds or 4, function()
			if token == hintToken then
				hint.Visible = false
			end
		end)
	end

	function hud.Hurt()
		flash.BackgroundTransparency = 0.6
		Theme.Tween(flash, { BackgroundTransparency = 1 }, 0.35)
	end

	function hud.SetFightVisible(visible: boolean)
		top.Visible = visible
		leave.Visible = visible
		bottom.Visible = visible
		if not visible then
			center.Visible = false
			hint.Visible = false
			countdownToken += 1
		end
	end

	function hud.Destroy()
		countdownToken += 1
		hintToken += 1
		insetConnection:Disconnect()
		if board then
			board:Destroy()
			board = nil
		end
		root:Destroy()
	end

	return hud
end

-------------------------------------------------------------------------------
-- Ability pick (1 of 3)
-------------------------------------------------------------------------------
export type AbilityOption = {
	id: string,
	name: string,
	icon: string,
	description: string,
	stacks: number,
	maxStacks: number,
}
export type AbilityChoiceOptions = { options: { AbilityOption }, timeout: number, onPick: (id: string) -> () }

function CombatUI.AbilityChoice(opts: AbilityChoiceOptions): () -> ()
	local gui = Theme.ScreenGui("CombatChoice", 45)
	local shade = Instance.new("Frame")
	shade.Name = "AbilityChoice"
	shade.BackgroundColor3 = P.Shade
	shade.BackgroundTransparency = 0.4
	shade.Size = UDim2.fromScale(1, 1)
	shade.Active = true
	shade.Parent = gui

	local holder = Instance.new("Frame")
	holder.Name = "Holder"
	holder.BackgroundTransparency = 1
	holder.AnchorPoint = Vector2.new(0.5, 0.5)
	holder.Position = UDim2.fromScale(0.5, 0.5)
	holder.Size = UDim2.fromOffset(720, 420)
	holder.Parent = shade
	Theme.ResponsiveScale(holder)

	Theme.Label({
		Text = "Choose an ability!",
		MaxTextSize = 44,
		StrokeThickness = 4,
		Size = UDim2.new(1, 0, 0, 56),
		Parent = holder,
	})
	local timer = Theme.Label({
		Text = "",
		MaxTextSize = 22,
		Size = UDim2.new(1, 0, 0, 26),
		Position = UDim2.fromOffset(0, 56),
		Parent = holder,
	})

	local row = Instance.new("Frame")
	row.Name = "Cards"
	row.BackgroundTransparency = 1
	row.Position = UDim2.fromOffset(0, 92)
	row.Size = UDim2.new(1, 0, 0, 320)
	row.Parent = holder
	Theme.List(row, "Horizontal", 18, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)

	local closed = false
	local picked = false
	local function close()
		if closed then
			return
		end
		closed = true
		shade:Destroy()
	end

	for index, option in opts.options do
		local card, body = Theme.Card({
			Title = option.name,
			Size = UDim2.fromOffset(220, 310),
			LayoutOrder = index,
			TitleSize = 24,
			Parent = row,
		})
		Theme.List(body, "Vertical", 6)
		Theme.Label({
			Text = option.icon,
			MaxTextSize = 64,
			StrokeThickness = 0,
			Size = UDim2.new(1, 0, 0, 72),
			LayoutOrder = 1,
			ZIndex = card.ZIndex + 3,
			Parent = body,
		})
		Theme.Label({
			Text = option.description,
			MaxTextSize = 20,
			Size = UDim2.new(1, 0, 0, 70),
			LayoutOrder = 2,
			ZIndex = card.ZIndex + 3,
			Parent = body,
		})
		local stars = ""
		if option.maxStacks <= 5 then
			stars = string.rep("★", option.stacks) .. string.rep("☆", option.maxStacks - option.stacks)
		elseif option.stacks > 0 then
			stars = ("x%d"):format(option.stacks)
		end
		Theme.Label({
			Text = stars,
			TextColor3 = P.PriceGold,
			MaxTextSize = 20,
			Size = UDim2.new(1, 0, 0, 24),
			LayoutOrder = 3,
			ZIndex = card.ZIndex + 3,
			Parent = body,
		})
		Theme.Button({
			Text = "PICK",
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			Size = UDim2.new(1, -8, 0, 54),
			LayoutOrder = 4,
			ZIndex = card.ZIndex + 4,
			OnClick = function()
				if picked or closed then
					return
				end
				picked = true
				close()
				opts.onPick(option.id)
			end,
			Parent = body,
		})
		Theme.Pop(card)
	end

	-- timer text (the server auto-picks the first card when it runs out)
	local deadline = os.clock() + opts.timeout
	task.spawn(function()
		while not closed do
			local left = math.max(0, math.ceil(deadline - os.clock()))
			timer.Text = ("Auto-pick in %ds"):format(left)
			task.wait(0.25)
		end
	end)
	return close
end

-------------------------------------------------------------------------------
-- Result screen
-------------------------------------------------------------------------------
export type ResultOptions = {
	result: { [string]: any },
	onChest: (done: () -> ()) -> (),
	onContinue: () -> (),
}

function CombatUI.Result(opts: ResultOptions): () -> ()
	local result = opts.result
	local victory = result.victory == true
	local panel, content, close = Theme.Panel({
		Name = "CombatResult",
		Title = if victory then "VICTORY!" else "DEFEAT",
		HeaderColor = if victory then P.ButtonGreen else P.ButtonRed,
		HeaderDarkColor = if victory then P.ButtonGreenDark else P.ButtonRedDark,
		Size = UDim2.fromOffset(520, 440),
		Closable = false,
	})
	Theme.List(content, "Vertical", 8)

	local lines = {}
	local level = tonumber(result.level) or 0
	if result.mode == "barrier" then
		table.insert(lines, ("Barrier squad • Level %d"):format(level))
	else
		table.insert(lines, ("Combat Arena • Level %d"):format(level))
	end
	table.insert(
		lines,
		("Waves cleared: %d/%d    Enemies: %d"):format(
			tonumber(result.wavesCleared) or 0,
			tonumber(result.waveCount) or 0,
			tonumber(result.kills) or 0
		)
	)
	for index, text in lines do
		Theme.Label({
			Text = text,
			MaxTextSize = 24,
			Size = UDim2.new(1, 0, 0, 32),
			LayoutOrder = index,
			ZIndex = 13,
			Parent = content,
		})
	end

	local paid = tonumber(result.paid) or 0
	local cashRow = Instance.new("Frame")
	cashRow.Name = "Cash"
	cashRow.BackgroundTransparency = 1
	cashRow.Size = UDim2.new(1, 0, 0, 56)
	cashRow.LayoutOrder = 10
	cashRow.ZIndex = 13
	cashRow.Parent = content
	Theme.MoneyTag({
		Text = "+" .. Format.Money(paid) .. (if result.boosted then "  (2x)" else ""),
		Size = UDim2.fromOffset(280, 52),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.5),
		MaxTextSize = 30,
		ZIndex = 14,
		Parent = cashRow,
	})

	local chest = result.chest
	local chestTier = if type(chest) == "table" and type(chest.tier) == "string" then chest.tier else nil

	local note = ""
	if result.barrierDefeated then
		note = ("The way to %s is open — unlock its gate!"):format(tostring(result.nextAreaName or "the next area"))
	elseif result.newBest then
		note = "New best level! The next level is unlocked."
	elseif not victory and chestTier then
		note = "Cash you collected is yours, and you earned a chest. Upgrade your gear and try again!"
	elseif not victory then
		-- matches CombatDefs.ChestTier: wood needs 1 kill or CHEST_MIN_SECONDS of fighting
		note = ("No chest this time: defeat an enemy or fight for %d seconds to earn one."):format(
			CombatDefs.CHEST_MIN_SECONDS
		)
	end
	Theme.Label({
		Text = note,
		TextColor3 = P.PriceGold,
		MaxTextSize = 20,
		Size = UDim2.new(1, 0, 0, 48),
		LayoutOrder = 11,
		ZIndex = 13,
		Parent = content,
	})

	local button: TextButton
	local continueMode = chestTier == nil
	local opened = false
	button = Theme.Button({
		Text = if continueMode
			then "CONTINUE"
			else ("%s OPEN %s"):format(
				CHEST_ICONS[chestTier] or "🎁",
				string.upper(CHEST_NAMES[chestTier] or "chest")
			),
		Color = if continueMode then P.ButtonGreen else P.CardOrange,
		DarkColor = if continueMode then P.ButtonGreenDark else P.CardOrangeDark,
		Size = UDim2.fromOffset(320, 66),
		LayoutOrder = 12,
		ZIndex = 14,
		OnClick = function()
			if continueMode then
				close()
				opts.onContinue()
				return
			end
			if opened then
				return
			end
			opened = true
			-- hide the whole panel (and its dim background) while the chest plays
			local shade = panel.Parent
			local function setShown(shown: boolean)
				if shade and shade:IsA("GuiObject") then
					shade.Visible = shown
				end
			end
			setShown(false)
			opts.onChest(function()
				continueMode = true
				setShown(true)
				Theme.SetButtonText(button, "CONTINUE")
				Theme.Pop(button)
			end)
		end,
		Parent = content,
	})
	return close
end

return CombatUI
