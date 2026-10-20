--!strict
--[[
	ShopUI (client, plain module - not a controller)
	The big pink "Shop" panel (pink header like the "Skins" card in reference
	image 2) with two tabs:

		VEHICLES  one card per Config.Vehicles entry: emoji, name, speed,
		          runner bonus. Locked ones say "Unlocks in Area N". Owned ones
		          show EQUIP / EQUIPPED, the rest BUY, which opens the two-route
		          Theme.PurchaseDialog (fixed Robux price or a cash price that
		          grows with your progress: Economy.VehicleCoinPrice).
		BOOSTS    how the random offers on the home map work, every boost with
		          its appearance chance (from Config.Boosts weights), its Robux
		          price, its current cash price, and the active 2x timer.

	Public API (used by Controllers/ShopController):
		ShopUI.Open(opts): () -> ()        -- returns close(); does nothing extra if already open
			opts = {
				Tab = "Vehicles" | "Boosts" | nil,
				OnBuyVehicle = function(vehicleId: string, route: "cash" | "robux") end,
				OnEquipVehicle = function(vehicleId: string) end,
				GetBoostSeconds = function(): number end,   -- 2x seconds left right now
				OnClose = function() end?,
			}
		ShopUI.Refresh()                   -- call after a data Sync (rebuilds only if something relevant changed)
		ShopUI.IsOpen(): boolean
		ShopUI.Close()

	All text/prices are display only: the server re-checks every purchase.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)

local P = Config.Palette

export type OpenOptions = {
	Tab: string?,
	OnBuyVehicle: (vehicleId: string, route: string) -> (),
	OnEquipVehicle: (vehicleId: string) -> (),
	GetBoostSeconds: () -> number,
	OnClose: (() -> ())?,
}

local ShopUI = {}

-- Emoji shown on each vehicle card. [PROPOSAL] (no image assets allowed)
local VEHICLE_EMOJI: { [string]: string } = {
	bike = "🏍️",
	jeep = "🚙",
	truck = "🚚",
	humvee = "🛻",
	apc = "🚛",
	tank = "🪖",
	heavytank = "💥",
	hovertank = "🛸",
}
local STYLE_EMOJI: { [string]: string } = {
	bike = "🏍️",
	jeep = "🚙",
	truck = "🚚",
	apc = "🚛",
	tank = "🪖",
	hover = "🛸",
}

-- Base walk speed shown for comparison (default.project.json CharacterWalkSpeed).
local NORMAL_WALK_SPEED = 20

local shopGui: ScreenGui? = nil
local options: OpenOptions? = nil
local closePanel: (() -> ())? = nil
local content: Frame? = nil
local currentTab = "Vehicles"
local lastSignature = ""
local timerLabel: TextLabel? = nil
local timerThread: thread? = nil
local dialogClose: (() -> ())? = nil

local function getGui(): ScreenGui
	if not shopGui then
		-- Below Theme's modal gui (50) so purchase dialogs appear on top.
		shopGui = Theme.ScreenGui("ShopUI", 40)
	end
	return shopGui :: ScreenGui
end

-- 0.02 -> "2%", 0.2667 -> "26.7%" (one decimal at most, no float noise)
local function pct(fraction: number): string
	return string.format("%g%%", math.floor(fraction * 1000 + 0.5) / 10)
end

local function totalBoostWeight(): number
	local total = 0
	for _, def in Config.Boosts do
		total += math.max(0, def.weight)
	end
	return total
end

-- A compact text describing everything the current tab depends on, so we
-- only rebuild when it changes (data syncs arrive about once per second).
local function signature(): string
	local data = State.Get()
	if not data then
		return "nodata"
	end
	local parts = { currentTab, tostring(data.base.highestArea), tostring(data.vehicles.equipped) }
	if currentTab == "Vehicles" then
		for _, def in Config.Vehicles do
			local price = Economy.VehicleCoinPrice(def.id, data.base.highestArea)
			table.insert(
				parts,
				def.id
					.. (if data.vehicles.owned[def.id] then "o" else "-")
					.. (if data.cash + 1e-6 >= price then "a" else "-")
			)
		end
	else
		for _, def in Config.Boosts do
			local price = Economy.BoostCoinPrice(def.id, data.base.highestArea)
			table.insert(parts, def.id .. (if data.cash + 1e-6 >= price then "a" else "-"))
		end
	end
	return table.concat(parts, "|")
end

local function smallLabel(
	parent: Instance,
	text: string,
	order: number,
	height: number,
	color: Color3?,
	maxSize: number?
): TextLabel
	return Theme.Label({
		Text = text,
		TextColor3 = color or P.White,
		MaxTextSize = maxSize or 20,
		Size = UDim2.new(1, 0, 0, height),
		LayoutOrder = order,
		Parent = parent,
	})
end

-------------------------------------------------------------------------------
-- Vehicles tab
-------------------------------------------------------------------------------

local function openVehicleDialog(def: Config.VehicleDef)
	local data = State.Get()
	local opts = options
	if not data or not opts or dialogClose then
		return
	end
	local price = Economy.VehicleCoinPrice(def.id, data.base.highestArea)
	local productId = Config.ProductIds["vehicle_" .. def.id] or 0
	dialogClose = Theme.PurchaseDialog({
		Title = def.name,
		Description = ("Ride it around your base at speed %d (walking is %d).\nWhile equipped, every Army Run pays +%s cash."):format(
			def.speed,
			NORMAL_WALK_SPEED,
			pct(def.runnerBonus)
		),
		RobuxPrice = def.robux,
		RobuxEnabled = productId ~= 0,
		CashPrice = price,
		CashAffordable = data.cash + 1e-6 >= price,
		Footnote = "The Robux price is fixed. The cash price grows with the areas you unlock.",
		OnClose = function()
			dialogClose = nil
		end,
		OnRobux = function()
			opts.OnBuyVehicle(def.id, "robux")
		end,
		OnCash = function()
			opts.OnBuyVehicle(def.id, "cash")
		end,
	})
end

local function buildVehicleCard(parent: Instance, def: Config.VehicleDef, index: number)
	local data = State.Get()
	if not data then
		return
	end
	local owned = data.vehicles.owned[def.id] == true
	local equipped = owned and data.vehicles.equipped == def.id
	local locked = not owned and data.base.highestArea < def.area
	local price = Economy.VehicleCoinPrice(def.id, data.base.highestArea)

	local card, body = Theme.Card({
		Name = "Vehicle_" .. def.id,
		Title = def.name,
		TitleSize = 22,
		HeaderColor = if locked then P.ButtonGrey elseif equipped then P.ButtonGreen else P.CardOrange,
		HeaderDarkColor = if locked then P.ButtonGreyDark elseif equipped then P.ButtonGreenDark else P.CardOrangeDark,
		BodyColor = if locked then P.ButtonGreyDark else P.CardBlue,
		BodyDarkColor = if locked then P.Shade else P.CardBlueDark,
		HeaderHeight = 38,
		LayoutOrder = index,
		Parent = parent,
	})
	card.Size = UDim2.fromScale(1, 1)
	Theme.List(body, "Vertical", 2)

	local emoji = VEHICLE_EMOJI[def.id] or STYLE_EMOJI[def.style] or "🚙"
	smallLabel(body, if locked then "🔒" else emoji, 1, 66, nil, 56)
	smallLabel(body, ("⚡ Speed %d"):format(def.speed), 2, 24)
	smallLabel(body, ("💰 +%s run cash"):format(pct(def.runnerBonus)), 3, 24, P.MoneyGreen)

	local status: string
	local statusColor = P.PriceGold
	if owned then
		status = if equipped then "Equipped ✓" else "Owned"
		statusColor = P.MoneyGreen
	elseif locked then
		status = ("Unlocks in Area %d"):format(def.area)
		statusColor = P.White
	else
		status = ("%s or %s"):format(Format.Robux(def.robux), Format.Money(price))
	end
	smallLabel(body, status, 4, 36, statusColor, 20)

	local buttonText: string
	local color = P.ButtonGreen
	local dark = P.ButtonGreenDark
	local enabled = true
	local onClick: () -> ()
	if equipped then
		buttonText = "EQUIPPED"
		enabled = false
		onClick = function() end
	elseif owned then
		buttonText = "EQUIP"
		color, dark = P.CardBlue, P.CardBlueDark
		onClick = function()
			local opts = options
			if opts then
				opts.OnEquipVehicle(def.id)
			end
		end
	elseif locked then
		buttonText = "LOCKED"
		enabled = false
		onClick = function() end
	else
		buttonText = "BUY"
		onClick = function()
			openVehicleDialog(def)
		end
	end
	local button = Theme.Button({
		Text = buttonText,
		Color = color,
		DarkColor = dark,
		Size = UDim2.new(1, -8, 0, 46),
		LayoutOrder = 5,
		MaxTextSize = 24,
		OnClick = onClick,
		Parent = body,
	})
	if not enabled then
		Theme.SetButtonEnabled(button, false)
	end
end

local function buildVehiclesTab(page: Frame)
	local scroll = Instance.new("ScrollingFrame")
	scroll.Name = "Vehicles"
	scroll.BackgroundTransparency = 1
	scroll.BorderSizePixel = 0
	scroll.Size = UDim2.fromScale(1, 1)
	scroll.CanvasSize = UDim2.new()
	scroll.AutomaticCanvasSize = Enum.AutomaticSize.Y
	scroll.ScrollingDirection = Enum.ScrollingDirection.Y
	scroll.ScrollBarThickness = 8
	scroll.ScrollBarImageColor3 = P.CardOutline
	scroll.Parent = page
	Theme.Padding(scroll, 6)

	local grid = Instance.new("UIGridLayout")
	grid.CellSize = UDim2.fromOffset(186, 268)
	grid.CellPadding = UDim2.fromOffset(12, 12)
	grid.SortOrder = Enum.SortOrder.LayoutOrder
	grid.HorizontalAlignment = Enum.HorizontalAlignment.Center
	grid.Parent = scroll

	for index, def in Config.Vehicles do
		buildVehicleCard(scroll, def, index)
	end
end

-------------------------------------------------------------------------------
-- Boosts tab
-------------------------------------------------------------------------------

local function timerText(): string
	local opts = options
	local seconds = if opts then opts.GetBoostSeconds() else 0
	if seconds > 0 then
		return ("⚡ 2x CASH active: %s left"):format(Format.Clock(seconds))
	end
	return "No 2x boost active right now"
end

local function boostDescription(def: Config.BoostDef): string
	if def.kind == "multiplier" then
		return ("Doubles ALL cash you earn for %d min of play time"):format(def.minutes or 0)
	end
	return ("Instantly collects %dh of your shops' revenue"):format(def.hours or 0)
end

local function buildBoostRow(parent: Instance, def: Config.BoostDef, order: number, chance: number)
	local data = State.Get()
	if not data then
		return
	end
	local price = Economy.BoostCoinPrice(def.id, data.base.highestArea)
	local value = Economy.BoostValue(def.id, data.base.highestArea)
	local affordable = data.cash + 1e-6 >= price

	local row = Instance.new("Frame")
	row.Name = "Boost_" .. def.id
	row.BackgroundColor3 = P.OfferPurple
	row.Size = UDim2.new(1, -12, 0, 66)
	row.LayoutOrder = order
	row.Parent = parent
	Theme.Corner(row, 12)
	Theme.Stroke(row, P.OfferPurpleDark, 2.5, true)

	Theme.Label({
		Text = if def.kind == "multiplier" then "⚡" else "⏩",
		MaxTextSize = 40,
		StrokeThickness = 0,
		Size = UDim2.new(0, 56, 1, 0),
		Parent = row,
	})
	Theme.Label({
		Text = def.name,
		MaxTextSize = 24,
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.42, -60, 0.5, -2),
		Position = UDim2.fromOffset(58, 4),
		Parent = row,
	})
	Theme.Label({
		Text = ("%s  •  worth ~%s"):format(boostDescription(def), Format.Money(value)),
		MaxTextSize = 16,
		TextColor3 = Color3.fromRGB(226, 214, 255),
		TextXAlignment = Enum.TextXAlignment.Left,
		Size = UDim2.new(0.42, -60, 0.5, -6),
		Position = UDim2.new(0, 58, 0.5, 0),
		Parent = row,
	})

	-- Chance pill
	local chancePill = Instance.new("Frame")
	chancePill.BackgroundColor3 = P.OfferPurpleDark
	chancePill.AnchorPoint = Vector2.new(0, 0.5)
	chancePill.Position = UDim2.new(0.42, 4, 0.5, 0)
	chancePill.Size = UDim2.new(0.2, -8, 0, 46)
	chancePill.Parent = row
	Theme.Corner(chancePill, 10)
	Theme.Label({
		Text = ("🍀 %s chance"):format(pct(chance)),
		MaxTextSize = 18,
		Size = UDim2.new(1, -8, 1, -6),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = chancePill,
	})

	-- Robux price pill
	local robuxPill = Instance.new("Frame")
	robuxPill.BackgroundColor3 = P.Robux
	robuxPill.AnchorPoint = Vector2.new(0, 0.5)
	robuxPill.Position = UDim2.new(0.62, 4, 0.5, 0)
	robuxPill.Size = UDim2.new(0.15, -8, 0, 46)
	robuxPill.Parent = row
	Theme.Corner(robuxPill, 10)
	Theme.Stroke(robuxPill, P.RobuxDark, 2, true)
	Theme.Label({
		Text = Format.Robux(def.robux),
		MaxTextSize = 20,
		Size = UDim2.new(1, -8, 1, -6),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = robuxPill,
	})

	-- Cash price pill (green when you can afford it)
	local cashPill = Instance.new("Frame")
	cashPill.BackgroundColor3 = if affordable then P.ButtonGreen else P.ButtonGreyDark
	cashPill.AnchorPoint = Vector2.new(0, 0.5)
	cashPill.Position = UDim2.new(0.77, 4, 0.5, 0)
	cashPill.Size = UDim2.new(0.23, -10, 0, 46)
	cashPill.Parent = row
	Theme.Corner(cashPill, 10)
	Theme.Stroke(cashPill, P.CardOutline, 2, true)
	Theme.CoinIcon({
		Size = UDim2.fromOffset(28, 28),
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.new(0, 6, 0.5, 0),
		Parent = cashPill,
	})
	Theme.Label({
		Text = Format.Money(price),
		TextColor3 = P.PriceGold,
		MaxTextSize = 20,
		Size = UDim2.new(1, -44, 1, -6),
		Position = UDim2.new(0, 38, 0.5, 0),
		AnchorPoint = Vector2.new(0, 0.5),
		Parent = cashPill,
	})
end

local function buildBoostsTab(page: Frame)
	local scroll = Instance.new("ScrollingFrame")
	scroll.Name = "Boosts"
	scroll.BackgroundTransparency = 1
	scroll.BorderSizePixel = 0
	scroll.Size = UDim2.fromScale(1, 1)
	scroll.CanvasSize = UDim2.new()
	scroll.AutomaticCanvasSize = Enum.AutomaticSize.Y
	scroll.ScrollingDirection = Enum.ScrollingDirection.Y
	scroll.ScrollBarThickness = 8
	scroll.ScrollBarImageColor3 = P.CardOutline
	scroll.Parent = page
	Theme.Padding(scroll, 6)
	Theme.List(scroll, "Vertical", 8)

	-- How it works
	local info = Instance.new("Frame")
	info.Name = "HowItWorks"
	info.BackgroundColor3 = P.CardBlueDark
	info.Size = UDim2.new(1, -12, 0, 140)
	info.LayoutOrder = 1
	info.Parent = scroll
	Theme.Corner(info, 12)
	Theme.Stroke(info, P.CardOutline, 2.5, true)
	Theme.Label({
		Text = "🎁 How boost offers work",
		MaxTextSize = 26,
		Size = UDim2.new(1, -20, 0, 34),
		Position = UDim2.fromOffset(10, 4),
		Parent = info,
	})
	local minMinutes = Config.BoostOffers.MinInterval / 60
	local maxMinutes = Config.BoostOffers.MaxInterval / 60
	Theme.Label({
		Text = ("Every %s-%s minutes a purple bubble pops up on a walkway of your base (up to %d at once, each stays %ds). Walk into it to buy it with Robux or cash. It is luck: cheap boosts appear often, valuable ones rarely. The cash price is 80%% of what the boost is worth, so it still pays back +25%% if you use it fully. Time skips only appear once you own a shop."):format(
			string.format("%g", math.floor(minMinutes * 10 + 0.5) / 10),
			string.format("%g", math.floor(maxMinutes * 10 + 0.5) / 10),
			Config.BoostOffers.MaxActive,
			Config.BoostOffers.Lifetime
		),
		MaxTextSize = 18,
		TextColor3 = Color3.fromRGB(226, 238, 255),
		Size = UDim2.new(1, -20, 1, -44),
		Position = UDim2.fromOffset(10, 38),
		Parent = info,
	})

	-- Active 2x timer
	local timerRow = Instance.new("Frame")
	timerRow.Name = "Timer"
	timerRow.BackgroundColor3 = P.PillBlack
	timerRow.Size = UDim2.new(1, -12, 0, 46)
	timerRow.LayoutOrder = 2
	timerRow.Parent = scroll
	Theme.Corner(timerRow, 0.5)
	timerLabel = Theme.Label({
		Text = timerText(),
		MaxTextSize = 24,
		TextColor3 = P.MoneyGreen,
		Size = UDim2.new(1, -20, 1, -8),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = timerRow,
	})

	local total = totalBoostWeight()
	for index, def in Config.Boosts do
		local chance = if total > 0 then math.max(0, def.weight) / total else 0
		buildBoostRow(scroll, def, 10 + index, chance)
	end
end

-------------------------------------------------------------------------------
-- Panel
-------------------------------------------------------------------------------

local rebuild: () -> ()

local function buildTabs(parent: Frame)
	local row = Instance.new("Frame")
	row.Name = "Tabs"
	row.BackgroundTransparency = 1
	row.Size = UDim2.new(1, 0, 0, 56)
	row.Parent = parent
	Theme.List(row, "Horizontal", 12, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)

	local tabs = { { id = "Vehicles", text = "🚙 VEHICLES" }, { id = "Boosts", text = "⚡ BOOSTS" } }
	for index, tab in tabs do
		local active = currentTab == tab.id
		Theme.Button({
			Name = tab.id,
			Text = tab.text,
			Color = if active then P.CardPink else P.ButtonGrey,
			DarkColor = if active then P.CardPinkDark else P.ButtonGreyDark,
			Size = UDim2.fromOffset(220, 52),
			LayoutOrder = index,
			MaxTextSize = 26,
			OnClick = function()
				if currentTab ~= tab.id then
					currentTab = tab.id
					rebuild()
				end
			end,
			Parent = row,
		})
	end
end

rebuild = function()
	local frame = content
	if not frame then
		return
	end
	timerLabel = nil
	for _, child in frame:GetChildren() do
		if child:IsA("GuiObject") then
			child:Destroy()
		end
	end
	buildTabs(frame)
	local page = Instance.new("Frame")
	page.Name = "Page"
	page.BackgroundTransparency = 1
	page.Position = UDim2.fromOffset(0, 64)
	page.Size = UDim2.new(1, 0, 1, -64)
	page.Parent = frame
	if currentTab == "Boosts" then
		buildBoostsTab(page)
	else
		buildVehiclesTab(page)
	end
	lastSignature = signature()
end

function ShopUI.IsOpen(): boolean
	return closePanel ~= nil
end

function ShopUI.Close()
	local close = closePanel
	if close then
		close()
	end
end

function ShopUI.Open(opts: OpenOptions): () -> ()
	if closePanel then
		return closePanel :: () -> ()
	end
	options = opts
	currentTab = if opts.Tab == "Boosts" then "Boosts" else "Vehicles"

	local _, body, close = Theme.Panel({
		Name = "Shop",
		Title = "🛒 Shop",
		HeaderColor = P.CardPink,
		HeaderDarkColor = P.CardPinkDark,
		Size = UDim2.fromOffset(880, 590),
		Gui = getGui(),
		OnClose = function()
			closePanel = nil
			content = nil
			timerLabel = nil
			options = nil
			local thread = timerThread
			timerThread = nil
			if thread and coroutine.status(thread) == "suspended" then
				pcall(task.cancel, thread)
			end
			local dialog = dialogClose
			dialogClose = nil
			if dialog then
				dialog()
			end
			if opts.OnClose then
				opts.OnClose()
			end
		end,
	})
	content = body
	closePanel = close
	rebuild()

	-- Keep the 2x timer text ticking while the panel is open.
	timerThread = task.spawn(function()
		while closePanel == close do
			task.wait(1)
			local label = timerLabel
			if label and label.Parent then
				label.Text = timerText()
			end
		end
	end)
	return close
end

function ShopUI.Refresh()
	if not closePanel then
		return
	end
	if signature() ~= lastSignature then
		rebuild()
	end
end

return ShopUI
