--!strict
--[[
	InventoryUI (client, plain module — not a controller)
	The "Armoury": review your armour and weapons, equip them, upgrade them
	with coins and scrap the ones you do not need.

	Layout (one big Theme.Panel, about 780 x 540, shrinks on small screens):
		LEFT   two big equipped-slot tiles (Weapon, Armour) + a summary of your
		       combined combat stats (HP, damage, fire rate, bullets, crit,
		       damage reduction, speed, perks).
		RIGHT  scrolling grid of every item you own (rarity-coloured border,
		       emoji icon, "Lv n", green "E" badge when equipped, pink "NEW"
		       for fresh chest drops). Sorted weapons first, then rarity, then level.
		       Clicking an item opens its detail card with EQUIP / UPGRADE / SCRAP.
		BOTTOM (optional) a big green action button, e.g. "START BATTLE".
	The panel refreshes live whenever the server sends new data (State.Changed).

	API:
		InventoryUI.Open(opts: {
			Title: string?,          -- panel title (default "Armoury")
			ActionText: string?,     -- show a big green action button with this text
			OnAction: (() -> ())?,   -- called when the action button is pressed (then the panel closes)
			OnClose: (() -> ())?,    -- called once when the panel closes (any reason)
		}): () -> ()                 -- returns close(); safe to call more than once
		InventoryUI.MarkNew(uid: string)  -- show the "NEW" badge on an item until it is viewed
		InventoryUI.IsOpen(): boolean

	Server requests used: "EquipItem", "UpgradeItem", "ScrapItem" (InventoryService).
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local EquipmentDefs = require(Shared.Combat.EquipmentDefs)
local Format = require(Shared.Format)
local Net = require(Shared.Net)
local Types = require(Shared.Types)

type Item = Types.Item
type PlayerData = Types.PlayerData

export type OpenOptions = {
	Title: string?,
	ActionText: string?,
	OnAction: (() -> ())?,
	OnClose: (() -> ())?,
}

type Entry = { uid: string, item: Item, def: EquipmentDefs.ItemDef, equipped: boolean }

local P = Theme.Palette
local Z = 13 -- ZIndex of panel content (Theme panels use 11-12)

local InventoryUI = {}

local newUids: { [string]: boolean } = {}
local activeClose: (() -> ())? = nil

function InventoryUI.MarkNew(uid: string)
	if type(uid) == "string" then
		newUids[uid] = true
	end
end

function InventoryUI.IsOpen(): boolean
	return activeClose ~= nil
end

-------------------------------------------------------------------------------
-- Small helpers
-------------------------------------------------------------------------------
local function fmt(n: number): string
	if math.abs(n - math.floor(n + 0.5)) < 0.05 then
		return tostring(math.floor(n + 0.5))
	end
	return string.format("%.1f", n)
end

local function pct(fraction: number): string
	return fmt(fraction * 100) .. "%"
end

local function newFrame(name: string, parent: Instance): Frame
	local f = Instance.new("Frame")
	f.Name = name
	f.BackgroundTransparency = 1
	f.BorderSizePixel = 0
	f.ZIndex = Z
	f.Parent = parent
	return f
end

local function newScroller(name: string, parent: Instance): ScrollingFrame
	local s = Instance.new("ScrollingFrame")
	s.Name = name
	s.BackgroundTransparency = 1
	s.BorderSizePixel = 0
	s.ZIndex = Z
	s.ScrollBarThickness = 8
	s.ScrollBarImageColor3 = P.White
	s.ScrollingDirection = Enum.ScrollingDirection.Y
	s.CanvasSize = UDim2.new(0, 0, 0, 0)
	s.AutomaticCanvasSize = Enum.AutomaticSize.Y
	s.Parent = parent
	return s
end

-- "Inset" background used for the stats box, the grid and the detail card.
local function inset(frame: GuiObject, transparency: number?)
	frame.BackgroundColor3 = P.CardBlueDark
	frame.BackgroundTransparency = transparency or 0.25
	Theme.Corner(frame, 12)
	Theme.Stroke(frame, P.CardOutline, 2, true)
end

-- A "label ...... value" line for the stats summary.
local function statRow(parent: Instance, order: number, left: string, right: string, rightColor: Color3?)
	local row = newFrame("Row", parent)
	row.Size = UDim2.new(1, 0, 0, 24)
	row.LayoutOrder = order
	Theme.Label({
		Text = left,
		MaxTextSize = 19,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.62, 0, 1, 0),
		ZIndex = Z + 1,
		Parent = row,
	})
	Theme.Label({
		Text = right,
		MaxTextSize = 19,
		TextColor3 = rightColor or P.White,
		TextXAlignment = Enum.TextXAlignment.Right,
		Size = UDim2.new(0.38, 0, 1, 0),
		Position = UDim2.fromScale(0.62, 0),
		ZIndex = Z + 1,
		Parent = row,
	})
end

-- One wrapped text line in a vertical list.
local function textLine(parent: Instance, order: number, text: string, height: number, color: Color3?, maxSize: number?)
	return Theme.Label({
		Text = text,
		MaxTextSize = maxSize or 19,
		TextColor3 = color or P.White,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(1, -4, 0, height),
		LayoutOrder = order,
		ZIndex = Z + 1,
		Parent = parent,
	})
end

-- Sorted list of the items you own (unknown item ids are skipped).
local function sortedEntries(data: PlayerData): { Entry }
	local list: { Entry } = {}
	if type(data.inventory) ~= "table" then
		return list
	end
	local equipped = if type(data.equipped) == "table" then data.equipped else {} :: any
	for uid, raw in data.inventory do
		local item = EquipmentDefs.SanitizeItem(raw)
		local def = item and EquipmentDefs.Items[item.id]
		if item and def then
			table.insert(list, {
				uid = uid,
				item = item,
				def = def,
				equipped = equipped.weapon == uid or equipped.armour == uid,
			})
		end
	end
	table.sort(list, function(a: Entry, b: Entry): boolean
		if a.def.slot ~= b.def.slot then
			return a.def.slot == "weapon"
		end
		if a.item.rarity ~= b.item.rarity then
			return a.item.rarity > b.item.rarity
		end
		if a.item.level ~= b.item.level then
			return a.item.level > b.item.level
		end
		if a.def.order ~= b.def.order then
			return a.def.order < b.def.order
		end
		return (tonumber(a.uid) or 0) < (tonumber(b.uid) or 0)
	end)
	return list
end

-- Square-ish item badge: rarity border + glow, emoji icon.
local function drawItemFace(parent: GuiObject, item: Item, def: EquipmentDefs.ItemDef, iconSize: number)
	local color = Theme.RarityColor(item.rarity)
	parent.BackgroundColor3 = P.CardBlueDark
	parent.BackgroundTransparency = 0
	Theme.Corner(parent, 12)
	local stroke = Theme.Stroke(parent, color, 3, true)
	stroke.Name = "RarityStroke"

	local glow = newFrame("Glow", parent)
	glow.Size = UDim2.fromScale(1, 1)
	glow.BackgroundTransparency = 0
	glow.BackgroundColor3 = color
	Theme.Corner(glow, 12)
	local gradient = Instance.new("UIGradient")
	gradient.Rotation = 90
	gradient.Transparency = NumberSequence.new({
		NumberSequenceKeypoint.new(0, 0.92),
		NumberSequenceKeypoint.new(0.55, 0.7),
		NumberSequenceKeypoint.new(1, 0.25),
	})
	gradient.Parent = glow

	Theme.Label({
		Name = "Icon",
		Text = def.icon,
		MaxTextSize = iconSize,
		StrokeThickness = 0,
		Size = UDim2.new(1, -8, 0.62, 0),
		Position = UDim2.new(0.5, 0, 0, 4),
		AnchorPoint = Vector2.new(0.5, 0),
		ZIndex = Z + 2,
		Parent = parent,
	})
	return stroke
end

local function badge(parent: Instance, text: string, color: Color3, position: UDim2, anchor: Vector2, width: number)
	local b = newFrame("Badge" .. text, parent)
	b.BackgroundTransparency = 0
	b.BackgroundColor3 = color
	b.Size = UDim2.fromOffset(width, 22)
	b.Position = position
	b.AnchorPoint = anchor
	b.ZIndex = Z + 4
	Theme.Corner(b, 0.5)
	Theme.Stroke(b, P.CardOutline, 2, true)
	Theme.Label({
		Text = text,
		MaxTextSize = 16,
		StrokeThickness = 1.5,
		Size = UDim2.new(1, -4, 1, -2),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = Z + 5,
		Parent = b,
	})
	return b
end

-------------------------------------------------------------------------------
-- Open
-------------------------------------------------------------------------------
function InventoryUI.Open(opts: OpenOptions): () -> ()
	opts = if type(opts) == "table" then opts else {}
	-- Only one Armoury at a time.
	local previous = activeClose
	if previous then
		previous()
	end

	local hasAction = type(opts.ActionText) == "string" and opts.ActionText ~= ""

	-- Fit the panel on the screen (Theme.Panel scales by screen HEIGHT; we
	-- also make sure the WIDTH fits narrow phones).
	local camera = Workspace.CurrentCamera
	local viewport = if camera then camera.ViewportSize else Vector2.new(1280, 800)
	local uiScale = math.clamp(viewport.Y / 820, 0.55, 1.25)
	local width = math.clamp((viewport.X - 24) / uiScale, 520, 780)
	local height = math.clamp((viewport.Y - 16) / uiScale, 420, if hasAction then 610 else 540)

	local closed = false
	local stateConn: { Disconnect: (any) -> () }? = nil
	local closeConfirm: (() -> ())? = nil
	local closeSelf: () -> () = function() end

	local function cleanup()
		if closed then
			return
		end
		closed = true
		if stateConn then
			stateConn:Disconnect()
			stateConn = nil
		end
		if closeConfirm then
			local c = closeConfirm
			closeConfirm = nil
			pcall(c)
		end
		if activeClose == closeSelf then
			activeClose = nil
		end
	end

	local _, content, panelClose = Theme.Panel({
		Name = "Armoury",
		Title = opts.Title or "Armoury",
		HeaderColor = P.CardOrange,
		HeaderDarkColor = P.CardOrangeDark,
		Size = UDim2.fromOffset(width, height),
		OnClose = function()
			cleanup()
			local onClose = opts.OnClose
			if onClose then
				local ok, err = pcall(function()
					onClose()
				end)
				if not ok then
					warn("[InventoryUI] OnClose failed: " .. tostring(err))
				end
			end
		end,
	})
	closeSelf = function()
		panelClose()
	end
	activeClose = closeSelf

	local bottomSpace = if hasAction then 76 else 0

	---------------------------------------------------------------- columns
	local left = newFrame("Left", content)
	left.Size = UDim2.new(0.37, -6, 1, -bottomSpace)

	local right = newFrame("Right", content)
	right.Position = UDim2.new(0.37, 6, 0, 0)
	right.Size = UDim2.new(0.63, -6, 1, -bottomSpace)

	-- Equipped slot tiles
	local slotRow = newFrame("Slots", left)
	slotRow.Size = UDim2.new(1, 0, 0, 150)

	-- Stats summary
	local statsBox = newFrame("Stats", left)
	statsBox.Position = UDim2.new(0, 0, 0, 158)
	statsBox.Size = UDim2.new(1, 0, 1, -158)
	inset(statsBox)
	Theme.Label({
		Text = "YOUR COMBAT STATS",
		MaxTextSize = 20,
		TextColor3 = P.PriceGold,
		Size = UDim2.new(1, -12, 0, 26),
		Position = UDim2.new(0.5, 0, 0, 6),
		AnchorPoint = Vector2.new(0.5, 0),
		ZIndex = Z + 1,
		Parent = statsBox,
	})
	local statsList = newScroller("List", statsBox)
	statsList.Position = UDim2.new(0, 10, 0, 34)
	statsList.Size = UDim2.new(1, -16, 1, -40)
	Theme.List(statsList, "Vertical", 2, Enum.HorizontalAlignment.Left)

	-- Item grid
	local countLabel = Theme.Label({
		Text = "ITEMS",
		MaxTextSize = 22,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(1, -60, 0, 28),
		Position = UDim2.new(0, 4, 0, 0),
		ZIndex = Z,
		Parent = right,
	})
	local gridBox = newFrame("GridBox", right)
	gridBox.Position = UDim2.new(0, 0, 0, 32)
	gridBox.Size = UDim2.new(1, 0, 1, -32)
	inset(gridBox)
	local grid = newScroller("Grid", gridBox)
	grid.Position = UDim2.fromOffset(4, 4)
	grid.Size = UDim2.new(1, -8, 1, -8)
	local gridLayout = Instance.new("UIGridLayout")
	gridLayout.CellSize = UDim2.fromOffset(86, 98)
	gridLayout.CellPadding = UDim2.fromOffset(9, 9)
	gridLayout.SortOrder = Enum.SortOrder.LayoutOrder
	gridLayout.HorizontalAlignment = Enum.HorizontalAlignment.Center
	gridLayout.Parent = grid
	Theme.Padding(grid, 8, 10)

	-- Detail card (covers the grid while open)
	local detail = newFrame("Detail", right)
	detail.ZIndex = Z + 10
	detail.Size = UDim2.fromScale(1, 1)
	detail.Visible = false
	inset(detail, 0)
	detail.BackgroundColor3 = P.CardBlue
	Theme.Gradient(detail, Theme.Lighten(P.CardBlue, 0.08), P.CardBlueDark)

	---------------------------------------------------------------- action button
	if hasAction then
		local acted = false
		Theme.Button({
			Name = "Action",
			Text = opts.ActionText,
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			MaxTextSize = 36,
			Size = UDim2.new(0.5, 0, 0, 66),
			Position = UDim2.new(0.5, 0, 1, -2),
			AnchorPoint = Vector2.new(0.5, 1),
			ZIndex = Z + 2,
			OnClick = function()
				if acted or closed then
					return
				end
				acted = true
				local onAction = opts.OnAction
				if onAction then
					local ok, err = pcall(function()
						onAction()
					end)
					if not ok then
						warn("[InventoryUI] OnAction failed: " .. tostring(err))
					end
				end
				closeSelf()
			end,
			Parent = content,
		})
	end

	---------------------------------------------------------------- state
	local selectedUid: string? = nil
	local busy = false
	local inventoryKey = ""
	local detailKey = ""
	local detailButtons: {
		equip: TextButton?,
		upgrade: TextButton?,
		scrap: TextButton?,
		price: number?,
		equipped: boolean,
	} =
		{ equipped = false }
	local tileStrokes: { [string]: UIStroke } = {}

	local refresh: () -> ()
	local showDetail: (uid: string?) -> ()

	local function setSelectedStroke()
		for uid, stroke in tileStrokes do
			local tile = stroke.Parent
			if uid == selectedUid then
				stroke.Color = P.White
				stroke.Thickness = 4
			elseif tile then
				stroke.Color = (tile :: Instance):GetAttribute("RarityColor") :: Color3? or P.White
				stroke.Thickness = 3
			end
		end
	end

	-- Sends one of the three requests (ignores double clicks while waiting).
	local function request(action: string, uid: string, onOk: (result: { [string]: any }) -> ())
		if busy or closed then
			return
		end
		busy = true
		refresh()
		local result = Net.Request(action, uid)
		busy = false
		if result.ok then
			onOk(result)
		else
			Theme.Toast(result.err or "Something went wrong", "error")
		end
		if not closed then
			refresh()
		end
	end

	---------------------------------------------------------------- left column
	local function drawSlots(data: PlayerData)
		for _, child in slotRow:GetChildren() do
			child:Destroy()
		end
		local equipped = if type(data.equipped) == "table" then data.equipped else {} :: any
		for index, slot in { "weapon", "armour" } do
			local uid = equipped[slot]
			local raw = if type(uid) == "string" and type(data.inventory) == "table" then data.inventory[uid] else nil
			local item = EquipmentDefs.SanitizeItem(raw)
			local def = item and EquipmentDefs.Items[item.id]
			if def and def.slot ~= slot then
				item, def = nil, nil
			end

			local tile = Instance.new("TextButton")
			tile.Name = slot
			tile.Text = ""
			tile.AutoButtonColor = false
			tile.ZIndex = Z
			tile.Size = UDim2.new(0.5, -5, 1, 0)
			tile.Position = if index == 1 then UDim2.new(0, 0, 0, 0) else UDim2.new(0.5, 5, 0, 0)
			tile.Parent = slotRow
			if item and def then
				drawItemFace(tile, item, def, 56)
				Theme.Label({
					Text = def.name,
					MaxTextSize = 17,
					Size = UDim2.new(1, -8, 0, 20),
					Position = UDim2.new(0.5, 0, 1, -24),
					AnchorPoint = Vector2.new(0.5, 1),
					ZIndex = Z + 2,
					Parent = tile,
				})
				Theme.Label({
					Text = "Lv " .. item.level,
					MaxTextSize = 17,
					TextColor3 = P.PriceGold,
					Size = UDim2.new(1, -8, 0, 20),
					Position = UDim2.new(0.5, 0, 1, -4),
					AnchorPoint = Vector2.new(0.5, 1),
					ZIndex = Z + 2,
					Parent = tile,
				})
				local equippedUid = uid :: string
				tile.Activated:Connect(function()
					showDetail(equippedUid)
				end)
			else
				inset(tile)
				Theme.Label({
					Text = "Empty",
					MaxTextSize = 20,
					TextColor3 = P.TrackStripe,
					Size = UDim2.new(1, -8, 0.5, 0),
					Position = UDim2.fromScale(0.5, 0.55),
					AnchorPoint = Vector2.new(0.5, 0.5),
					ZIndex = Z + 1,
					Parent = tile,
				})
			end
			badge(
				tile,
				if slot == "weapon" then "WEAPON" else "ARMOUR",
				P.CardOrange,
				UDim2.new(0.5, 0, 0, -8),
				Vector2.new(0.5, 0),
				86
			)
		end
	end

	local function drawStats(data: PlayerData)
		for _, child in statsList:GetChildren() do
			if child:IsA("GuiObject") then
				child:Destroy()
			end
		end
		local ok, raw = pcall(function(): EquipmentDefs.CombatStats
			return EquipmentDefs.LoadoutStats(data)
		end)
		if not ok or type(raw) ~= "table" then
			textLine(statsList, 1, "Stats unavailable", 24)
			return
		end
		local s = EquipmentDefs.DisplayStats(raw)
		statRow(statsList, 1, "❤️ HP", fmt(s.maxHp), P.MoneyGreen)
		statRow(statsList, 2, "💥 Damage", fmt(s.damage))
		statRow(statsList, 3, "⚡ Fire rate", string.format("%.1f/s", s.fireRate))
		statRow(statsList, 4, "🔱 Bullets", tostring(s.projectiles))
		statRow(statsList, 5, "🎯 Crit", pct(s.critChance) .. " x" .. fmt(s.critMult))
		statRow(statsList, 6, "🛡️ Dmg reduction", pct(s.damageReduction))
		statRow(statsList, 7, "👟 Speed", fmt(s.moveSpeed))

		local perkIds = {}
		for id, stacks in s.perks do
			if stacks > 0 and EquipmentDefs.PERKS[id] then
				table.insert(perkIds, id)
			end
		end
		table.sort(perkIds)
		if #perkIds == 0 then
			textLine(statsList, 10, "✨ Perks: none yet (Rare+ items have perks)", 40, P.TrackStripe, 16)
		else
			textLine(statsList, 10, "✨ Perks", 24, P.PriceGold)
			for i, id in perkIds do
				local perk = EquipmentDefs.PERKS[id]
				local stacks = s.perks[id]
				textLine(
					statsList,
					10 + i,
					perk.icon .. " " .. perk.name .. (if stacks > 1 then " x" .. stacks else ""),
					22,
					nil,
					17
				)
			end
		end
	end

	---------------------------------------------------------------- grid
	local function drawGrid(data: PlayerData, entries: { Entry })
		for _, child in grid:GetChildren() do
			if child:IsA("GuiObject") then
				child:Destroy()
			end
		end
		table.clear(tileStrokes)
		countLabel.Text = ("ITEMS  %d / %d"):format(#entries, EquipmentDefs.MAX_INVENTORY)
		if #entries == 0 then
			local empty = textLine(grid, 1, "No items yet. Win combat games to earn chests!", 60)
			empty.Size = UDim2.fromOffset(240, 60)
			return
		end
		for index, entry in entries do
			local tile = Instance.new("TextButton")
			tile.Name = "Item" .. entry.uid
			tile.Text = ""
			tile.AutoButtonColor = false
			tile.LayoutOrder = index
			tile.ZIndex = Z
			tile.Parent = grid
			tile:SetAttribute("RarityColor", Theme.RarityColor(entry.item.rarity))
			local stroke = drawItemFace(tile, entry.item, entry.def, 40)
			tileStrokes[entry.uid] = stroke
			Theme.Label({
				Text = "Lv " .. entry.item.level,
				MaxTextSize = 18,
				Size = UDim2.new(1, -8, 0, 22),
				Position = UDim2.new(0.5, 0, 1, -4),
				AnchorPoint = Vector2.new(0.5, 1),
				ZIndex = Z + 2,
				Parent = tile,
			})
			if entry.equipped then
				local e = badge(tile, "E", P.ButtonGreen, UDim2.fromOffset(-6, -6), Vector2.zero, 24)
				e.Size = UDim2.fromOffset(24, 24)
			end
			if newUids[entry.uid] then
				badge(tile, "NEW", P.CardPink, UDim2.new(1, 6, 0, -6), Vector2.new(1, 0), 44)
			end
			local uid = entry.uid
			tile.Activated:Connect(function()
				showDetail(uid)
			end)
		end
		setSelectedStroke()
	end

	---------------------------------------------------------------- detail card
	local function updateDetailButtons()
		local data = State.Get()
		local cash = if data then data.cash else 0
		local b = detailButtons
		if b.equip then
			Theme.SetButtonEnabled(b.equip, not busy and not b.equipped)
		end
		if b.upgrade then
			local affordable = b.price ~= nil and cash + 1e-6 >= (b.price :: number)
			Theme.SetButtonEnabled(b.upgrade, not busy and affordable)
		end
		if b.scrap then
			Theme.SetButtonEnabled(b.scrap, not busy and not b.equipped)
		end
	end

	local function buildDetail(entry: Entry)
		for _, child in detail:GetChildren() do
			if child:IsA("GuiObject") then
				child:Destroy()
			end
		end
		detailButtons = { equipped = entry.equipped }
		local item, def = entry.item, entry.def
		local rarityColor = Theme.RarityColor(item.rarity)
		local maxLevel = Config.Rarities[item.rarity].maxLevel

		-- Back arrow
		Theme.Button({
			Name = "Back",
			Text = "◀",
			Color = P.CardOrange,
			Size = UDim2.fromOffset(44, 40),
			Position = UDim2.fromOffset(8, 8),
			ZIndex = Z + 12,
			MaxTextSize = 22,
			OnClick = function()
				showDetail(nil)
			end,
			Parent = detail,
		})

		-- Icon box
		local iconBox = newFrame("IconBox", detail)
		iconBox.ZIndex = Z + 11
		iconBox.Size = UDim2.fromOffset(96, 96)
		iconBox.Position = UDim2.fromOffset(60, 8)
		drawItemFace(iconBox, item, def, 60)

		-- Name / rarity / level
		local info = newFrame("Info", detail)
		info.ZIndex = Z + 11
		info.Position = UDim2.fromOffset(166, 6)
		info.Size = UDim2.new(1, -174, 0, 100)
		Theme.Label({
			Text = def.name,
			MaxTextSize = 30,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, 0, 0, 34),
			ZIndex = Z + 12,
			Parent = info,
		})
		Theme.Label({
			Text = Theme.RarityName(item.rarity):upper() .. (if def.slot == "weapon" then "  WEAPON" else "  ARMOUR"),
			MaxTextSize = 22,
			TextColor3 = rarityColor,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, 0, 0, 26),
			Position = UDim2.fromOffset(0, 34),
			ZIndex = Z + 12,
			Parent = info,
		})
		Theme.Label({
			Text = ("Level %d / %d   ·   Power %d"):format(item.level, maxLevel, EquipmentDefs.ItemPower(item)),
			MaxTextSize = 18,
			TextColor3 = P.PriceGold,
			TextXAlignment = Enum.TextXAlignment.Left,
			Size = UDim2.new(1, 0, 0, 22),
			Position = UDim2.fromOffset(0, 62),
			ZIndex = Z + 12,
			Parent = info,
		})

		-- Description, stats and perks (scrolls if long)
		local body = newScroller("Body", detail)
		body.ZIndex = Z + 11
		body.Position = UDim2.fromOffset(12, 112)
		body.Size = UDim2.new(1, -20, 1, -112 - 80)
		Theme.List(body, "Vertical", 3, Enum.HorizontalAlignment.Left)
		local order = 0
		local function line(text: string, height: number, color: Color3?, maxSize: number?)
			order += 1
			local l = textLine(body, order, text, height, color, maxSize)
			l.ZIndex = Z + 12
		end
		line(def.description, 38, P.TrackStripe, 17)
		for _, text in EquipmentDefs.ItemStatsText(item) do
			line(text, 24, nil, 20)
		end
		local perks = EquipmentDefs.ItemPerks(item)
		if #perks > 0 then
			line("✨ Perks", 24, P.PriceGold, 20)
			for _, perk in perks do
				local tag = if perk.source == "builtin"
					then " (built-in)"
					elseif perk.source == "signature" then " ★"
					else ""
				line(
					perk.icon .. " " .. perk.name .. tag,
					22,
					if perk.source == "signature" then rarityColor else nil,
					18
				)
				line("     " .. perk.description, 20, P.TrackStripe, 15)
			end
		elseif item.rarity < 3 then
			line("Rare, Epic and Legendary items roll extra perks.", 22, P.TrackStripe, 15)
		end
		if item.rarity < #Config.Rarities then
			line("Legendary signature: " .. def.signatureName, 22, Theme.RarityColor(#Config.Rarities), 15)
		end

		-- Buttons
		local row = newFrame("Buttons", detail)
		row.ZIndex = Z + 11
		row.Position = UDim2.new(0, 8, 1, -74)
		row.Size = UDim2.new(1, -16, 0, 66)
		Theme.List(row, "Horizontal", 8, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)
		local buttonSize = UDim2.new(1 / 3, -6, 0, 62)

		local uid = entry.uid
		detailButtons.equip = Theme.Button({
			Name = "Equip",
			Text = if entry.equipped then "EQUIPPED" else "EQUIP",
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			Size = buttonSize,
			LayoutOrder = 1,
			ZIndex = Z + 12,
			MaxTextSize = 24,
			OnClick = function()
				request("EquipItem", uid, function()
					Theme.Toast("Equipped " .. def.name .. "!", "success")
				end)
			end,
			Parent = row,
		})

		local price = Economy.EquipmentUpgradePrice(item.rarity, item.level)
		detailButtons.price = price
		local upgrade = Theme.Button({
			Name = "Upgrade",
			Text = if price then "UPGRADE\n" .. Format.Money(price) else "MAX LEVEL",
			Color = P.CardOrange,
			DarkColor = P.CardOrangeDark,
			Size = buttonSize,
			LayoutOrder = 2,
			ZIndex = Z + 12,
			MaxTextSize = 22,
			OnClick = function()
				request("UpgradeItem", uid, function(result)
					Theme.Toast(("%s upgraded to Lv %s!"):format(def.name, tostring(result.level or "?")), "success")
				end)
			end,
			Parent = row,
		})
		detailButtons.upgrade = upgrade
		if price then
			-- gold coin on the left of the price (like the reference HUD)
			local face = upgrade:FindFirstChild("Face")
			local label = face and face:FindFirstChild("Label") :: TextLabel?
			if label then
				label.Size = UDim2.new(1, -40, 1, -8)
				label.Position = UDim2.new(0.5, 14, 0.5, 0)
			end
			Theme.CoinIcon({
				Size = UDim2.fromOffset(24, 24),
				AnchorPoint = Vector2.new(0, 0.5),
				Position = UDim2.new(0, 8, 0.5, -2),
				ZIndex = Z + 14,
				Parent = upgrade,
			})
		end

		local scrapValue = EquipmentDefs.ScrapValue(item)
		detailButtons.scrap = Theme.Button({
			Name = "Scrap",
			Text = "SCRAP\n+" .. Format.Money(scrapValue),
			Color = P.ButtonRed,
			DarkColor = P.ButtonRedDark,
			Size = buttonSize,
			LayoutOrder = 3,
			ZIndex = Z + 12,
			MaxTextSize = 22,
			OnClick = function()
				if busy or closed or detailButtons.equipped then
					return
				end
				if closeConfirm then
					pcall(closeConfirm)
				end
				closeConfirm = Theme.Confirm({
					Title = "Scrap item?",
					Body = ("Scrap your %s %s (Lv %d) for %s? This cannot be undone."):format(
						Theme.RarityName(item.rarity),
						def.name,
						item.level,
						Format.Money(scrapValue)
					),
					ConfirmText = "SCRAP",
					ConfirmColor = P.ButtonRed,
					OnClose = function()
						closeConfirm = nil
					end,
					OnConfirm = function()
						if closed then
							return
						end
						request("ScrapItem", uid, function(result)
							local value = tonumber(result.value) or scrapValue
							Theme.Toast("Scrapped for +" .. Format.Money(value), "cash")
							if selectedUid == uid then
								showDetail(nil)
							end
						end)
					end,
				})
			end,
			Parent = row,
		})
		updateDetailButtons()
	end

	showDetail = function(uid: string?)
		if closed then
			return
		end
		selectedUid = uid
		detailKey = ""
		if uid and newUids[uid] then
			newUids[uid] = nil
			inventoryKey = "" -- redraw the grid to drop the NEW badge
		end
		refresh()
	end

	---------------------------------------------------------------- refresh
	refresh = function()
		if closed then
			return
		end
		local data = State.Get()
		if not data then
			countLabel.Text = "Loading..."
			return
		end
		local entries = sortedEntries(data)

		-- Rebuild the grid + left column only when the items actually changed
		-- (cash changes every few seconds and must not flicker the grid).
		local parts = {}
		for _, e in entries do
			table.insert(
				parts,
				("%s:%s:%d:%d:%s:%s"):format(
					e.uid,
					e.item.id,
					e.item.rarity,
					e.item.level,
					if e.equipped then "E" else "",
					if newUids[e.uid] then "N" else ""
				)
			)
		end
		local key = table.concat(parts, "|")
		if key ~= inventoryKey then
			inventoryKey = key
			drawSlots(data)
			drawStats(data)
			drawGrid(data, entries)
		end

		-- Detail card
		local entry: Entry? = nil
		if selectedUid then
			for _, e in entries do
				if e.uid == selectedUid then
					entry = e
					break
				end
			end
			if not entry then
				selectedUid = nil -- scrapped / gone
			end
		end
		if entry then
			local dKey = ("%s:%d:%d:%s"):format(
				entry.uid,
				entry.item.rarity,
				entry.item.level,
				tostring(entry.equipped)
			)
			if dKey ~= detailKey then
				detailKey = dKey
				buildDetail(entry)
			end
			detail.Visible = true
			updateDetailButtons()
		else
			detailKey = ""
			detail.Visible = false
		end
		setSelectedStroke()
	end

	stateConn = State.Changed:Connect(function()
		refresh()
	end)
	refresh()

	return closeSelf
end

return InventoryUI
