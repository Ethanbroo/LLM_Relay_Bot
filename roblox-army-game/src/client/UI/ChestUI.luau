--!strict
--[[
	ChestUI (client, plain module — not a controller)
	Full-screen chest opening animation shown after every combat game:
		1. a chest drawn from Frames (wood brown / silver / gold) drops in,
		2. it shakes three times (harder each time),
		3. the lid flies off with a white flash,
		4. rotating light rays appear in the colour of the item's rarity,
		5. the item card is revealed (rarity border, icon, name, rarity,
		   key stats, perks) with a big green COLLECT button.
	Tap anywhere during steps 1-4 to skip straight to the reveal.
	If several chests are played at once they are shown one after another.
	No image assets: everything is Frames, UIStroke/UIGradient and emoji.

	API:
		ChestUI.Play(chestTier: string, item: Item, onDone: (() -> ())?)
			chestTier = "wood" | "silver" | "gold" (anything else looks like wood)
			onDone is called once, after COLLECT is pressed.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local EquipmentDefs = require(Shared.Combat.EquipmentDefs)
local Types = require(Shared.Types)

type Item = Types.Item

local P = Theme.Palette

local ChestUI = {}

type Job = { tier: string, item: any, onDone: (() -> ())? }
local queue: { Job } = {}
local playing = false

-- Colours per chest tier: main, dark (shading / bands), trim (corner metal).
local TIER_LOOK = {
	wood = { main = P.Wood, dark = Theme.Darken(P.Wood, 0.35), trim = P.ButtonGreyDark, title = "WOODEN CHEST" },
	silver = {
		main = Theme.Lighten(P.ButtonGrey, 0.35),
		dark = P.ButtonGreyDark,
		trim = P.White,
		title = "SILVER CHEST",
	},
	gold = { main = P.Coin, dark = P.CoinDark, trim = P.CoinShine, title = "GOLDEN CHEST" },
}

local function box(name: string, parent: Instance, color: Color3, size: UDim2, position: UDim2, anchor: Vector2?): Frame
	local f = Instance.new("Frame")
	f.Name = name
	f.BorderSizePixel = 0
	f.BackgroundColor3 = color
	f.Size = size
	f.Position = position
	f.AnchorPoint = anchor or Vector2.zero
	f.Parent = parent
	return f
end

-- Builds the chest. Returns (chest group, lid).
local function buildChest(parent: Instance, look: { main: Color3, dark: Color3, trim: Color3 }): (Frame, Frame)
	local chest =
		box("Chest", parent, P.White, UDim2.fromOffset(230, 190), UDim2.fromScale(0.5, -0.6), Vector2.new(0.5, 0.5))
	chest.BackgroundTransparency = 1

	-- Body (bottom part)
	local body = box("Body", chest, look.main, UDim2.new(1, 0, 0.6, 0), UDim2.fromScale(0, 0.4))
	Theme.Corner(body, 14)
	Theme.Stroke(body, P.CardOutline, 4, true)
	Theme.Gradient(body, Theme.Lighten(look.main, 0.15), look.dark)
	-- planks
	box("Plank", body, look.dark, UDim2.new(1, -24, 0, 4), UDim2.new(0, 12, 0.5, -2)).BackgroundTransparency = 0.3
	-- metal corner bands
	for _, x in { 0.08, 0.92 } do
		local band = box("Band", body, look.trim, UDim2.new(0, 22, 1, 0), UDim2.fromScale(x, 0), Vector2.new(0.5, 0))
		Theme.Stroke(band, P.CardOutline, 2, true)
	end

	-- Lid (top part, flies off)
	local lid =
		box("Lid", chest, look.main, UDim2.new(1.04, 0, 0.44, 0), UDim2.fromScale(0.5, 0.46), Vector2.new(0.5, 1))
	lid.ZIndex = 3
	Theme.Corner(lid, 22)
	Theme.Stroke(lid, P.CardOutline, 4, true)
	Theme.Gradient(lid, Theme.Lighten(look.main, 0.25), look.main)
	local rim = box("Rim", lid, look.dark, UDim2.new(1, 0, 0, 12), UDim2.new(0, 0, 1, -12))
	rim.ZIndex = 3
	for _, x in { 0.08, 0.92 } do
		local band = box("Band", lid, look.trim, UDim2.new(0, 22, 1, 0), UDim2.fromScale(x, 0), Vector2.new(0.5, 0))
		band.ZIndex = 3
		Theme.Stroke(band, P.CardOutline, 2, true)
	end

	-- Gold lock plate with a keyhole
	local lock =
		box("Lock", chest, P.Padlock, UDim2.fromOffset(44, 50), UDim2.fromScale(0.5, 0.46), Vector2.new(0.5, 0.35))
	lock.ZIndex = 4
	Theme.Corner(lock, 10)
	Theme.Stroke(lock, P.CardOutline, 3, true)
	local keyhole =
		box("Keyhole", lock, P.CardOutline, UDim2.fromOffset(10, 18), UDim2.fromScale(0.5, 0.55), Vector2.new(0.5, 0.5))
	keyhole.ZIndex = 5
	Theme.Corner(keyhole, 0.5)

	local scale = Instance.new("UIScale")
	scale.Name = "ShakeScale"
	scale.Parent = chest
	return chest, lid
end

-- Rotating light rays (+ a soft glow in the middle).
local function buildRays(parent: Instance, color: Color3): Frame
	local rays =
		box("Rays", parent, color, UDim2.fromOffset(640, 640), UDim2.fromScale(0.5, 0.45), Vector2.new(0.5, 0.5))
	rays.BackgroundTransparency = 1
	rays.Visible = false
	local count = 8 -- 8 full-width beams = 16 rays
	for i = 0, count - 1 do
		local beam = box("Beam", rays, color, UDim2.new(0, 54, 1, 0), UDim2.fromScale(0.5, 0.5), Vector2.new(0.5, 0.5))
		beam.Rotation = i * (180 / count)
		beam.BackgroundTransparency = 0
		local g = Instance.new("UIGradient")
		g.Rotation = 90
		g.Transparency = NumberSequence.new({
			NumberSequenceKeypoint.new(0, 1),
			NumberSequenceKeypoint.new(0.3, 0.8),
			NumberSequenceKeypoint.new(0.5, 0.25),
			NumberSequenceKeypoint.new(0.7, 0.8),
			NumberSequenceKeypoint.new(1, 1),
		})
		g.Parent = beam
	end
	for _, spec in { { size = 330, alpha = 0.75 }, { size = 200, alpha = 0.45 } } do
		local glow = box(
			"Glow",
			rays,
			color,
			UDim2.fromOffset(spec.size, spec.size),
			UDim2.fromScale(0.5, 0.5),
			Vector2.new(0.5, 0.5)
		)
		glow.BackgroundTransparency = spec.alpha
		Theme.Corner(glow, 0.5)
	end
	local scale = Instance.new("UIScale")
	scale.Scale = 0.2
	scale.Parent = rays
	return rays
end

-- The reveal card. Returns the card frame.
local function buildCard(parent: Instance, item: any): Frame
	local clean = EquipmentDefs.SanitizeItem(item)
	local def = if clean then EquipmentDefs.Items[clean.id] else nil
	local rarity = if clean then clean.rarity else 1
	local color = Theme.RarityColor(rarity)

	local card = box(
		"ItemCard",
		parent,
		P.CardBlueDark,
		UDim2.fromOffset(360, 440),
		UDim2.new(0.5, 0, 0, 58),
		Vector2.new(0.5, 0)
	)
	card.ZIndex = 10
	card.Visible = false
	Theme.Corner(card, 18)
	local stroke = Theme.Stroke(card, color, 6, true)
	stroke.Name = "RarityBorder"
	local fill = box("Fill", card, P.CardBlue, UDim2.fromScale(1, 1), UDim2.fromScale(0, 0))
	fill.ZIndex = 10
	Theme.Corner(fill, 18)
	Theme.Gradient(fill, Theme.Lighten(P.CardBlue, 0.1), P.CardBlueDark)

	-- Rarity header strip
	local header = box("Header", card, color, UDim2.new(1, 0, 0, 52), UDim2.fromScale(0, 0))
	header.ZIndex = 11
	Theme.Corner(header, 18)
	Theme.Gradient(header, Theme.Lighten(color, 0.2), Theme.Darken(color, 0.15))
	Theme.Label({
		Text = Theme.RarityName(rarity):upper() .. "!",
		MaxTextSize = 34,
		Size = UDim2.new(1, -16, 1, -8),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = 12,
		Parent = header,
	})

	-- Icon on a rarity-glow circle
	local iconBack =
		box("IconBack", card, color, UDim2.fromOffset(120, 120), UDim2.new(0.5, 0, 0, 62), Vector2.new(0.5, 0))
	iconBack.ZIndex = 11
	iconBack.BackgroundTransparency = 0.55
	Theme.Corner(iconBack, 0.5)
	Theme.Stroke(iconBack, color, 3, true)
	Theme.Label({
		Text = if def then def.icon else "❓",
		MaxTextSize = 84,
		StrokeThickness = 0,
		Size = UDim2.fromScale(0.86, 0.86),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = 12,
		Parent = iconBack,
	})

	Theme.Label({
		Text = if def then def.name else "Unknown item",
		MaxTextSize = 34,
		Size = UDim2.new(1, -24, 0, 38),
		Position = UDim2.new(0.5, 0, 0, 186),
		AnchorPoint = Vector2.new(0.5, 0),
		ZIndex = 12,
		Parent = card,
	})
	Theme.Label({
		Text = Theme.RarityName(rarity)
			.. (if def then (if def.slot == "weapon" then " Weapon" else " Armour") else "")
			.. "  ·  Lv "
			.. tostring(if clean then clean.level else 1),
		MaxTextSize = 22,
		TextColor3 = color,
		Size = UDim2.new(1, -24, 0, 26),
		Position = UDim2.new(0.5, 0, 0, 224),
		AnchorPoint = Vector2.new(0.5, 0),
		ZIndex = 12,
		Parent = card,
	})

	local list = box("Lines", card, P.White, UDim2.new(1, -32, 0, 176), UDim2.new(0.5, 0, 0, 254), Vector2.new(0.5, 0))
	list.BackgroundTransparency = 1
	list.ZIndex = 11
	Theme.List(list, "Vertical", 2, Enum.HorizontalAlignment.Left)
	local order = 0
	local function line(text: string, textColor: Color3?, maxSize: number)
		order += 1
		Theme.Label({
			Text = text,
			MaxTextSize = maxSize,
			TextColor3 = textColor or P.White,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, 0, 0, 22),
			LayoutOrder = order,
			ZIndex = 12,
			Parent = list,
		})
	end
	if clean then
		local stats = EquipmentDefs.ItemStatsText(clean)
		for i = 1, math.min(4, #stats) do
			line(stats[i], nil, 19)
		end
		for _, perk in EquipmentDefs.ItemPerks(clean) do
			line(
				perk.icon .. " " .. perk.name,
				if perk.source == "signature" then Theme.RarityColor(#Config.Rarities) else P.PriceGold,
				18
			)
		end
	end
	return card
end

-------------------------------------------------------------------------------
-- One chest
-------------------------------------------------------------------------------
local function playOne(job: Job)
	local look = TIER_LOOK[job.tier] or TIER_LOOK.wood
	local clean = EquipmentDefs.SanitizeItem(job.item)
	local rarityColor = Theme.RarityColor(if clean then clean.rarity else 1)
	local rarity = if clean then clean.rarity else 1

	local gui = Theme.ScreenGui("ChestReveal", 70)
	local root = Instance.new("TextButton")
	root.Name = "ChestOverlay"
	root.Text = ""
	root.AutoButtonColor = false
	root.BackgroundColor3 = P.Shade
	root.BackgroundTransparency = 1
	root.Size = UDim2.fromScale(1, 1)
	root.ZIndex = 1
	root.Parent = gui

	local stage =
		box("Stage", root, P.White, UDim2.fromOffset(560, 640), UDim2.fromScale(0.5, 0.5), Vector2.new(0.5, 0.5))
	stage.BackgroundTransparency = 1
	stage.ZIndex = 2
	Theme.ResponsiveScale(stage, 760)

	local title = Theme.Label({
		Text = look.title,
		MaxTextSize = 44,
		TextColor3 = look.main,
		StrokeThickness = 4,
		Size = UDim2.new(1, 0, 0, 50),
		Position = UDim2.fromScale(0.5, 0),
		AnchorPoint = Vector2.new(0.5, 0),
		ZIndex = 20,
		Parent = stage,
	})

	local rays = buildRays(stage, rarityColor)
	local chest, lid = buildChest(stage, look)
	rays.ZIndex = 1 -- rays behind the chest, card on top of both
	chest.ZIndex = 2
	local card = buildCard(stage, job.item)

	local skip = false
	local finished = false
	local connections: { RBXScriptConnection } = {}

	table.insert(
		connections,
		root.Activated:Connect(function()
			skip = true
		end)
	)
	table.insert(
		connections,
		RunService.RenderStepped:Connect(function(dt: number)
			if rays.Visible then
				rays.Rotation = (rays.Rotation + dt * 28) % 360
			end
		end)
	)

	-- Waits t seconds, but stops early when the player taps to skip.
	local function pause(t: number)
		local started = os.clock()
		while os.clock() - started < t and not skip and root.Parent do
			task.wait()
		end
	end

	-- 1. fade in + drop
	Theme.Tween(root, { BackgroundTransparency = 0.3 }, 0.25)
	Theme.Tween(chest, { Position = UDim2.fromScale(0.5, 0.45) }, 0.75, Enum.EasingStyle.Bounce)
	pause(0.85)

	-- 2. three shakes, each a little harder
	local shakeScale = chest:FindFirstChild("ShakeScale") :: UIScale?
	for i = 1, 3 do
		if skip then
			break
		end
		local angle = 5 + i * 4
		if shakeScale then
			shakeScale.Scale = 1 + 0.04 * i
			Theme.Tween(shakeScale, { Scale = 1 }, 0.3, Enum.EasingStyle.Back)
		end
		Theme.Tween(chest, { Rotation = angle }, 0.06)
		pause(0.06)
		Theme.Tween(chest, { Rotation = -angle }, 0.12)
		pause(0.12)
		Theme.Tween(chest, { Rotation = 0 }, 0.08)
		pause(0.38)
	end

	-- 3. lid flies off with a flash
	local flash = box("Flash", root, P.White, UDim2.fromScale(1, 1), UDim2.fromScale(0, 0))
	flash.ZIndex = 30
	flash.BackgroundTransparency = 0.15
	Theme.Tween(flash, { BackgroundTransparency = 1 }, 0.55)
	task.delay(0.6, function()
		flash:Destroy()
	end)
	chest.Rotation = 0
	Theme.Tween(lid, { Position = UDim2.new(0.5, 90, -2.5, 0), Rotation = -50 }, 0.6, Enum.EasingStyle.Quad)

	-- 4. rays
	rays.Visible = true
	local rayScale = rays:FindFirstChildOfClass("UIScale")
	if rayScale then
		Theme.Tween(rayScale, { Scale = 1 }, 0.5, Enum.EasingStyle.Back)
	end
	pause(0.7)

	-- 5. reveal
	if shakeScale then
		Theme.Tween(shakeScale, { Scale = 0 }, 0.2)
	end
	task.delay(0.22, function()
		if chest.Parent then
			chest.Visible = false
		end
	end)
	title.Visible = false
	card.Visible = true
	Theme.Pop(card)

	local collected = false
	local button = Theme.Button({
		Name = "Collect",
		Text = "COLLECT",
		Color = P.ButtonGreen,
		DarkColor = P.ButtonGreenDark,
		MaxTextSize = 34,
		Size = UDim2.fromOffset(250, 72),
		Position = UDim2.new(0.5, 0, 1, -10),
		AnchorPoint = Vector2.new(0.5, 1),
		ZIndex = 25,
		OnClick = function()
			collected = true
		end,
		Parent = stage,
	})
	button.ZIndex = 25
	Theme.Pop(button)

	-- Sparkles for Epic / Legendary drops
	local sparkleTimer = 0
	if rarity >= 4 then
		table.insert(
			connections,
			RunService.RenderStepped:Connect(function(dt: number)
				sparkleTimer -= dt
				if sparkleTimer > 0 or finished then
					return
				end
				sparkleTimer = if rarity >= 5 then 0.15 else 0.3
				local spark = Theme.Label({
					Text = "✨",
					MaxTextSize = 40,
					StrokeThickness = 0,
					Size = UDim2.fromOffset(40, 40),
					Position = UDim2.fromScale(math.random() * 0.9 + 0.05, math.random() * 0.75 + 0.05),
					AnchorPoint = Vector2.new(0.5, 0.5),
					ZIndex = 24,
					Parent = stage,
				})
				Theme.Pop(spark)
				Theme.Tween(spark, { TextTransparency = 1 }, 0.9)
				task.delay(0.95, function()
					spark:Destroy()
				end)
			end)
		)
	end

	-- 6. wait for COLLECT (or the overlay being removed)
	while not collected and root.Parent do
		task.wait(0.05)
	end

	-- 7. clean up
	finished = true
	for _, connection in connections do
		connection:Disconnect()
	end
	Theme.Tween(root, { BackgroundTransparency = 1 }, 0.15)
	task.wait(0.05)
	root:Destroy()
end

local function runQueue()
	if playing then
		return
	end
	playing = true
	while #queue > 0 do
		local job = table.remove(queue, 1) :: Job
		local ok, err = pcall(function()
			playOne(job)
		end)
		if not ok then
			warn("[ChestUI] animation failed: " .. tostring(err))
			-- remove any half-built overlay
			local gui = Theme.ScreenGui("ChestReveal", 70)
			for _, child in gui:GetChildren() do
				child:Destroy()
			end
		end
		local onDone = job.onDone
		if onDone then
			task.spawn(onDone)
		end
	end
	playing = false
end

function ChestUI.Play(chestTier: string, item: Item, onDone: (() -> ())?)
	table.insert(queue, {
		tier = if type(chestTier) == "string" then chestTier else "wood",
		item = item,
		onDone = onDone,
	})
	task.spawn(runQueue)
end

return ChestUI
