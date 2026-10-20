--!strict
--[[
	Theme (client UI kit)
	Builds every piece of UI in the look of the reference images: chunky
	FredokaOne text with dark outlines, black currency pill with a gold coin,
	orange-header / blue-body cards, green money tags, rounded glossy buttons.
	No image assets are needed (coins and icons are drawn with frames/emoji).

	Most functions take a props table. Common props:
		Name, Parent, Size, Position, AnchorPoint, LayoutOrder, ZIndex, Visible

	Quick reference:
		Theme.ScreenGui(name, displayOrder?) -> ScreenGui
		Theme.Label({ Text, TextColor3?, MaxTextSize?, StrokeThickness?, ... }) -> TextLabel
		Theme.Button({ Text, Color?, DarkColor?, OnClick, MaxTextSize?, ... }) -> TextButton
		Theme.SetButtonEnabled(button, enabled)
		Theme.Card({ Title, HeaderColor?, HeaderDarkColor?, BodyColor?, BodyDarkColor?, ... }) -> (card, body)
		Theme.Panel({ Title, HeaderColor?, OnClose?, Size? }) -> (panel, content, close)
		Theme.CoinIcon({ ... }) -> Frame
		Theme.CashPill({ ... }) -> (Frame, setAmount(number))
		Theme.MoneyTag({ Text, ... }) -> (Frame, setText(string))
		Theme.Banner({ Text, ... }) -> (Frame, setText(string))
		Theme.PurchaseDialog({ Title, Description, RobuxPrice?, RobuxEnabled?, CashPrice?, OnRobux?, OnCash? })
		Theme.Confirm({ Title, Body, ConfirmText?, OnConfirm })
		Theme.Toast(text, kind?)
		Theme.FloatText(worldPosition, text, color?)
		Theme.Billboard({ Adornee?, Parent, Size, StudsOffset?, AlwaysOnTop?, MaxDistance? }) -> BillboardGui
		Theme.RarityColor(rarity) -> Color3
		Theme.Tween(instance, goals, duration?, style?, direction?) -> Tween
		Theme.Pop(guiObject)
		Theme.Corner/Stroke/Gradient/Padding/List/AspectRatio helpers
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Format = require(Shared.Format)

local P = Config.Palette
local player = Players.LocalPlayer

local Theme = {}
Theme.Palette = P
Theme.Font = Config.Font

type Props = { [string]: any }

local GENERIC_KEYS = {
	"Name",
	"Size",
	"Position",
	"AnchorPoint",
	"LayoutOrder",
	"ZIndex",
	"Visible",
	"BackgroundColor3",
	"BackgroundTransparency",
	"ClipsDescendants",
	"AutomaticSize",
	"Rotation",
}

local function applyGeneric(instance: Instance, props: Props)
	local target = instance :: any
	for _, key in GENERIC_KEYS do
		if props[key] ~= nil then
			target[key] = props[key]
		end
	end
end

-------------------------------------------------------------------------------
-- Low-level helpers
-------------------------------------------------------------------------------

-- Generic creator: Theme.New("Frame", { Size = ..., Parent = ... }, { children })
function Theme.New(className: string, props: Props?, children: { Instance }?): Instance
	local instance = Instance.new(className) :: any
	local parent = nil
	if props then
		for key, value in props do
			if key == "Parent" then
				parent = value
			else
				instance[key] = value
			end
		end
	end
	if children then
		for _, child in children do
			child.Parent = instance
		end
	end
	if parent then
		instance.Parent = parent
	end
	return instance
end

function Theme.Corner(parent: Instance, radius: number?): UICorner
	local c = Instance.new("UICorner")
	c.CornerRadius = if radius and radius < 1 and radius > 0 then UDim.new(radius, 0) else UDim.new(0, radius or 12)
	c.Parent = parent
	return c
end

-- isBorder = true draws an outline around a frame; otherwise it outlines text.
function Theme.Stroke(parent: Instance, color: Color3?, thickness: number?, isBorder: boolean?): UIStroke
	local s = Instance.new("UIStroke")
	s.Color = color or P.TextStroke
	s.Thickness = thickness or 2.5
	s.LineJoinMode = Enum.LineJoinMode.Round
	s.ApplyStrokeMode = if isBorder then Enum.ApplyStrokeMode.Border else Enum.ApplyStrokeMode.Contextual
	s.Parent = parent
	return s
end

function Theme.Gradient(parent: Instance, top: Color3, bottom: Color3, rotation: number?): UIGradient
	local g = Instance.new("UIGradient")
	g.Color = ColorSequence.new(top, bottom)
	g.Rotation = rotation or 90
	g.Parent = parent
	return g
end

function Theme.Padding(parent: Instance, px: number, py: number?): UIPadding
	local p = Instance.new("UIPadding")
	local vertical = py or px
	p.PaddingLeft = UDim.new(0, px)
	p.PaddingRight = UDim.new(0, px)
	p.PaddingTop = UDim.new(0, vertical)
	p.PaddingBottom = UDim.new(0, vertical)
	p.Parent = parent
	return p
end

-- direction: "Vertical" | "Horizontal"
function Theme.List(
	parent: Instance,
	direction: string?,
	padding: number?,
	hAlign: Enum.HorizontalAlignment?,
	vAlign: Enum.VerticalAlignment?
): UIListLayout
	local l = Instance.new("UIListLayout")
	l.FillDirection = if direction == "Horizontal" then Enum.FillDirection.Horizontal else Enum.FillDirection.Vertical
	l.Padding = UDim.new(0, padding or 8)
	l.SortOrder = Enum.SortOrder.LayoutOrder
	l.HorizontalAlignment = hAlign or Enum.HorizontalAlignment.Center
	l.VerticalAlignment = vAlign or Enum.VerticalAlignment.Top
	l.Parent = parent
	return l
end

function Theme.AspectRatio(parent: Instance, ratio: number): UIAspectRatioConstraint
	local a = Instance.new("UIAspectRatioConstraint")
	a.AspectRatio = ratio
	a.Parent = parent
	return a
end

function Theme.Tween(
	instance: Instance,
	goals: { [string]: any },
	duration: number?,
	style: Enum.EasingStyle?,
	direction: Enum.EasingDirection?
): Tween
	local tween = TweenService:Create(
		instance,
		TweenInfo.new(duration or 0.25, style or Enum.EasingStyle.Quad, direction or Enum.EasingDirection.Out),
		goals
	)
	tween:Play()
	return tween
end

local function getScale(gui: GuiObject): UIScale
	local scale = gui:FindFirstChild("ThemePopScale") :: UIScale?
	if not scale then
		local created = Instance.new("UIScale")
		created.Name = "ThemePopScale"
		created.Parent = gui
		scale = created
	end
	return scale :: UIScale
end

-- Quick "bounce" used when something is bought / appears.
function Theme.Pop(gui: GuiObject)
	local scale = getScale(gui)
	scale.Scale = 0.85
	Theme.Tween(scale, { Scale = 1 }, 0.35, Enum.EasingStyle.Back)
end

function Theme.RarityColor(rarity: number): Color3
	return P.Rarity[math.clamp(rarity, 1, #P.Rarity)]
end

function Theme.RarityName(rarity: number): string
	local def = Config.Rarities[math.clamp(rarity, 1, #Config.Rarities)]
	return def.name
end

-- Shrinks UI on small (phone) screens, grows it a little on big screens.
function Theme.ResponsiveScale(gui: GuiObject, baseHeight: number?): UIScale
	local scale = Instance.new("UIScale")
	scale.Name = "ResponsiveScale"
	scale.Parent = gui
	local function update()
		local camera = Workspace.CurrentCamera
		local height = if camera then camera.ViewportSize.Y else 800
		scale.Scale = math.clamp(height / (baseHeight or 820), 0.55, 1.25)
	end
	update()
	local camera = Workspace.CurrentCamera
	if camera then
		local connection = camera:GetPropertyChangedSignal("ViewportSize"):Connect(update)
		-- Stop listening once the UI is destroyed (panels are created and destroyed often).
		scale.Destroying:Connect(function()
			connection:Disconnect()
		end)
	end
	return scale
end

-------------------------------------------------------------------------------
-- Building blocks
-------------------------------------------------------------------------------

function Theme.ScreenGui(name: string, displayOrder: number?): ScreenGui
	local playerGui = player:WaitForChild("PlayerGui")
	local existing = playerGui:FindFirstChild(name)
	if existing and existing:IsA("ScreenGui") then
		return existing
	end
	local gui = Instance.new("ScreenGui")
	gui.Name = name
	gui.ResetOnSpawn = false
	gui.IgnoreGuiInset = true
	gui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	gui.DisplayOrder = displayOrder or 0
	gui.Parent = playerGui
	return gui
end

-- Bold outlined label. Text scales to fit, up to MaxTextSize.
function Theme.Label(props: Props): TextLabel
	local label = Instance.new("TextLabel")
	label.BackgroundTransparency = 1
	label.Font = Config.Font
	label.Text = props.Text or ""
	label.TextColor3 = props.TextColor3 or P.White
	label.TextScaled = true
	label.TextWrapped = props.TextWrapped ~= false
	label.RichText = props.RichText == true
	label.TextXAlignment = props.TextXAlignment or Enum.TextXAlignment.Center
	label.TextYAlignment = props.TextYAlignment or Enum.TextYAlignment.Center
	label.Size = UDim2.fromScale(1, 1)
	applyGeneric(label, props)
	local limit = Instance.new("UITextSizeConstraint")
	limit.MaxTextSize = props.MaxTextSize or 32
	limit.MinTextSize = props.MinTextSize or 8
	limit.Parent = label
	if props.StrokeThickness ~= 0 then
		Theme.Stroke(label, props.StrokeColor, props.StrokeThickness or 2.5)
	end
	if props.Parent then
		label.Parent = props.Parent
	end
	return label
end

local function darken(color: Color3, amount: number): Color3
	local h, s, v = color:ToHSV()
	return Color3.fromHSV(h, s, math.clamp(v * (1 - amount), 0, 1))
end
Theme.Darken = darken

local function lighten(color: Color3, amount: number): Color3
	return color:Lerp(Color3.new(1, 1, 1), amount)
end
Theme.Lighten = lighten

-- Glossy rounded button with a darker "lip" underneath (pressable look).
function Theme.Button(props: Props): TextButton
	local color: Color3 = props.Color or P.ButtonGreen
	local dark: Color3 = props.DarkColor or darken(color, 0.3)

	local button = Instance.new("TextButton")
	button.AutoButtonColor = false
	button.Text = ""
	button.BackgroundColor3 = dark
	button.Size = UDim2.fromOffset(200, 60)
	applyGeneric(button, props)
	button.BackgroundColor3 = dark
	Theme.Corner(button, props.CornerRadius or 14)
	Theme.Stroke(button, P.CardOutline, 2.5, true)

	local face = Instance.new("Frame")
	face.Name = "Face"
	face.BackgroundColor3 = color
	face.Size = UDim2.new(1, 0, 1, -5)
	face.ZIndex = button.ZIndex
	face.Parent = button
	Theme.Corner(face, props.CornerRadius or 14)
	Theme.Gradient(face, lighten(color, 0.18), color)

	local label = Theme.Label({
		Name = "Label",
		Text = props.Text or "",
		MaxTextSize = props.MaxTextSize or 28,
		Size = UDim2.new(1, -16, 1, -8),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = button.ZIndex + 1,
		RichText = props.RichText,
		Parent = face,
	})
	label.Name = "Label"

	local scale = getScale(button)
	button.MouseButton1Down:Connect(function()
		Theme.Tween(scale, { Scale = 0.94 }, 0.08)
	end)
	button.MouseButton1Up:Connect(function()
		Theme.Tween(scale, { Scale = 1 }, 0.12, Enum.EasingStyle.Back)
	end)
	button.MouseLeave:Connect(function()
		Theme.Tween(scale, { Scale = 1 }, 0.12)
	end)
	if props.OnClick then
		button.Activated:Connect(function()
			if button:GetAttribute("Disabled") then
				return
			end
			props.OnClick()
		end)
	end
	button:SetAttribute("BaseColor", color)
	button:SetAttribute("BaseDarkColor", dark)
	if props.Parent then
		button.Parent = props.Parent
	end
	return button
end

function Theme.SetButtonText(button: TextButton, text: string)
	local face = button:FindFirstChild("Face")
	local label = face and face:FindFirstChild("Label") :: TextLabel?
	if label then
		label.Text = text
	end
end

-- Greys a Theme.Button out (and ignores clicks) or restores its colour.
function Theme.SetButtonEnabled(button: TextButton, enabled: boolean)
	button:SetAttribute("Disabled", not enabled)
	local face = button:FindFirstChild("Face") :: Frame?
	local base = button:GetAttribute("BaseColor") :: Color3?
	local baseDark = button:GetAttribute("BaseDarkColor") :: Color3?
	if face and base and baseDark then
		local color = if enabled then base else P.ButtonGrey
		face.BackgroundColor3 = color
		button.BackgroundColor3 = if enabled then baseDark else P.ButtonGreyDark
		local gradient = face:FindFirstChildOfClass("UIGradient")
		if gradient then
			gradient.Color = ColorSequence.new(lighten(color, 0.18), color)
		end
	end
end

-- Gold coin drawn from frames (matches the coin in both reference images).
function Theme.CoinIcon(props: Props): Frame
	local coin = Instance.new("Frame")
	coin.Name = "Coin"
	coin.BackgroundColor3 = P.CoinDark
	coin.Size = UDim2.fromOffset(40, 40)
	applyGeneric(coin, props)
	Theme.Corner(coin, 0.5)
	Theme.Stroke(coin, P.TextStroke, 2, true)
	Theme.AspectRatio(coin, 1)

	local inner = Instance.new("Frame")
	inner.Name = "Inner"
	inner.AnchorPoint = Vector2.new(0.5, 0.5)
	inner.Position = UDim2.fromScale(0.5, 0.5)
	inner.Size = UDim2.fromScale(0.74, 0.74)
	inner.BackgroundColor3 = P.Coin
	inner.ZIndex = coin.ZIndex
	inner.Parent = coin
	Theme.Corner(inner, 0.5)
	Theme.Gradient(inner, P.CoinShine, P.Coin, 45)
	if props.Parent then
		coin.Parent = props.Parent
	end
	return coin
end

-- Black rounded currency pill: [coin] $1,562.50 (top-right of both images).
function Theme.CashPill(props: Props): (Frame, (number) -> ())
	local pill = Instance.new("Frame")
	pill.Name = props.Name or "CashPill"
	pill.BackgroundColor3 = P.PillBlack
	pill.BackgroundTransparency = 0.05
	pill.Size = UDim2.fromOffset(240, 58)
	applyGeneric(pill, props)
	Theme.Corner(pill, 0.5)

	local coin = Theme.CoinIcon({
		Size = UDim2.new(0, 0, 1, 8),
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.new(0, -6, 0.5, 0),
		ZIndex = pill.ZIndex + 1,
		Parent = pill,
	})
	coin.Size = UDim2.fromScale(0, 1.15)
	coin.AutomaticSize = Enum.AutomaticSize.None
	local aspect = coin:FindFirstChildOfClass("UIAspectRatioConstraint")
	if aspect then
		aspect.DominantAxis = Enum.DominantAxis.Height
	end

	local label = Theme.Label({
		Name = "Amount",
		Text = Format.Money(0),
		MaxTextSize = 34,
		Size = UDim2.new(1, -70, 0.78, 0),
		Position = UDim2.new(0, 58, 0.5, 0),
		AnchorPoint = Vector2.new(0, 0.5),
		TextXAlignment = Enum.TextXAlignment.Center,
		ZIndex = pill.ZIndex + 1,
		Parent = pill,
	})

	local shown = 0
	local target = 0
	local animating = false
	local function setAmount(amount: number)
		target = amount
		if animating then
			return
		end
		animating = true
		task.spawn(function()
			local from = shown
			local started = os.clock()
			local duration = if math.abs(target - from) > 0 then 0.45 else 0
			while os.clock() - started < duration do
				local alpha = (os.clock() - started) / duration
				alpha = 1 - (1 - alpha) ^ 3
				shown = from + (target - from) * alpha
				label.Text = Format.Money(shown)
				task.wait()
				if target ~= amount then
					-- New target arrived mid-animation: restart from where we are.
					from = shown
					amount = target
					started = os.clock()
				end
			end
			shown = target
			label.Text = Format.Money(shown)
			animating = false
		end)
		if target > shown + 0.001 then
			Theme.Pop(pill)
		end
	end
	if props.Parent then
		pill.Parent = props.Parent
	end
	return pill, setAmount
end

-- Green money tag floating above the runner squad ("$122.50" in image 2).
function Theme.MoneyTag(props: Props): (Frame, (string) -> ())
	local tag = Instance.new("Frame")
	tag.Name = props.Name or "MoneyTag"
	tag.BackgroundColor3 = P.MoneyGreenDark
	tag.Size = UDim2.fromOffset(130, 44)
	applyGeneric(tag, props)
	Theme.Corner(tag, 10)
	Theme.Stroke(tag, P.CardOutline, 2, true)
	local face = Instance.new("Frame")
	face.Name = "Face"
	face.BackgroundColor3 = P.MoneyGreen
	face.Size = UDim2.new(1, 0, 1, -4)
	face.ZIndex = tag.ZIndex
	face.Parent = tag
	Theme.Corner(face, 10)
	local label = Theme.Label({
		Text = props.Text or "",
		MaxTextSize = props.MaxTextSize or 30,
		Size = UDim2.new(1, -10, 1, -4),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = tag.ZIndex + 1,
		Parent = face,
	})
	if props.Parent then
		tag.Parent = props.Parent
	end
	return tag, function(text: string)
		label.Text = text
	end
end

-- Black banner with white text ("Level 39" in image 2).
function Theme.Banner(props: Props): (Frame, (string) -> ())
	local banner = Instance.new("Frame")
	banner.Name = props.Name or "Banner"
	banner.BackgroundColor3 = P.PillBlack
	banner.Size = UDim2.fromOffset(290, 48)
	applyGeneric(banner, props)
	Theme.Corner(banner, 8)
	Theme.Stroke(banner, Color3.fromRGB(60, 60, 70), 2, true)
	local label = Theme.Label({
		Text = props.Text or "",
		MaxTextSize = props.MaxTextSize or 32,
		Size = UDim2.new(1, -16, 1, -8),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		StrokeThickness = 0,
		ZIndex = banner.ZIndex + 1,
		Parent = banner,
	})
	if props.Parent then
		banner.Parent = props.Parent
	end
	return banner, function(text: string)
		label.Text = text
	end
end

-- Card with coloured header and blue body ("Coin Magnet" card in image 2).
-- Returns the card and its body frame (put content in the body).
function Theme.Card(props: Props): (Frame, Frame)
	local headerColor: Color3 = props.HeaderColor or P.CardOrange
	local headerDark: Color3 = props.HeaderDarkColor or P.CardOrangeDark
	local bodyColor: Color3 = props.BodyColor or P.CardBlue
	local bodyDark: Color3 = props.BodyDarkColor or P.CardBlueDark
	local headerHeight: number = props.HeaderHeight or 40

	local card = Instance.new("Frame")
	card.Name = props.Name or "Card"
	card.BackgroundColor3 = bodyDark
	card.Size = UDim2.fromOffset(180, 220)
	applyGeneric(card, props)
	card.ClipsDescendants = false
	Theme.Corner(card, 14)
	Theme.Stroke(card, P.CardOutline, 3, true)

	local bodyFill = Instance.new("Frame")
	bodyFill.Name = "BodyFill"
	bodyFill.BackgroundColor3 = bodyColor
	bodyFill.Size = UDim2.fromScale(1, 1)
	bodyFill.ZIndex = card.ZIndex
	bodyFill.Parent = card
	Theme.Corner(bodyFill, 14)
	Theme.Gradient(bodyFill, lighten(bodyColor, 0.1), bodyDark)

	local header = Instance.new("Frame")
	header.Name = "Header"
	header.BackgroundColor3 = headerColor
	header.Size = UDim2.new(1, 0, 0, headerHeight)
	header.ZIndex = card.ZIndex + 1
	header.Parent = card
	Theme.Corner(header, 14)
	Theme.Gradient(header, lighten(headerColor, 0.15), headerDark)
	-- square off the header's bottom corners
	local headerBottom = Instance.new("Frame")
	headerBottom.BorderSizePixel = 0
	headerBottom.BackgroundColor3 = headerDark
	headerBottom.Size = UDim2.new(1, 0, 0, 12)
	headerBottom.Position = UDim2.new(0, 0, 1, -12)
	headerBottom.ZIndex = card.ZIndex + 1
	headerBottom.Parent = header

	Theme.Label({
		Name = "Title",
		Text = props.Title or "",
		MaxTextSize = props.TitleSize or 26,
		Size = UDim2.new(1, -14, 1, -6),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = card.ZIndex + 2,
		Parent = header,
	})

	local body = Instance.new("Frame")
	body.Name = "Body"
	body.BackgroundTransparency = 1
	body.Position = UDim2.fromOffset(0, headerHeight)
	body.Size = UDim2.new(1, 0, 1, -headerHeight)
	body.ZIndex = card.ZIndex + 1
	body.Parent = card
	Theme.Padding(body, 8)

	if props.Parent then
		card.Parent = props.Parent
	end
	return card, body
end

local modalGui: ScreenGui? = nil
local function getModalGui(): ScreenGui
	if not modalGui then
		modalGui = Theme.ScreenGui("ThemeModals", 50)
	end
	return modalGui :: ScreenGui
end

-- Centered modal panel with a dimmed background and a red close button.
-- Returns (panel, content, close()).
function Theme.Panel(props: Props): (Frame, Frame, () -> ())
	local gui = props.Gui or getModalGui()
	local shade = Instance.new("TextButton")
	shade.Name = (props.Name or "Panel") .. "Shade"
	shade.AutoButtonColor = false
	shade.Text = ""
	shade.BackgroundColor3 = P.Shade
	shade.BackgroundTransparency = 0.45
	shade.Size = UDim2.fromScale(1, 1)
	shade.ZIndex = 10
	shade.Parent = gui

	local card, body = Theme.Card({
		Name = props.Name or "Panel",
		Title = props.Title or "",
		HeaderColor = props.HeaderColor,
		HeaderDarkColor = props.HeaderDarkColor,
		BodyColor = props.BodyColor or P.PanelBlue,
		BodyDarkColor = props.BodyDarkColor or P.PanelBlueDark,
		HeaderHeight = 54,
		TitleSize = 34,
		Size = props.Size or UDim2.fromOffset(560, 440),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		ZIndex = 11,
		Parent = shade,
	})
	Theme.ResponsiveScale(card)

	local closed = false
	local function close()
		if closed then
			return
		end
		closed = true
		shade:Destroy()
		if props.OnClose then
			props.OnClose()
		end
	end

	if props.Closable ~= false then
		local closeButton = Theme.Button({
			Name = "Close",
			Text = "X",
			Color = P.ButtonRed,
			Size = UDim2.fromOffset(46, 46),
			AnchorPoint = Vector2.new(0.5, 0.5),
			Position = UDim2.new(1, -6, 0, 6),
			ZIndex = 20,
			MaxTextSize = 26,
			OnClick = close,
			Parent = card,
		})
		closeButton.ZIndex = 20
	end

	Theme.Pop(card)
	return card, body, close
end

-- Two-route purchase dialog used for employees, boosts and vehicles:
-- [ R$ 200 ]  or  [ (coin) $12,345 ]
function Theme.PurchaseDialog(props: Props): () -> ()
	local _, content, close = Theme.Panel({
		Name = "PurchaseDialog",
		Title = props.Title or "Buy",
		HeaderColor = props.HeaderColor or P.OfferPurple,
		HeaderDarkColor = props.HeaderDarkColor or P.OfferPurpleDark,
		Size = UDim2.fromOffset(520, 360),
		OnClose = props.OnClose,
	})
	Theme.List(content, "Vertical", 12)

	Theme.Label({
		Name = "Description",
		Text = props.Description or "",
		MaxTextSize = 24,
		Size = UDim2.new(1, 0, 0, 110),
		LayoutOrder = 1,
		ZIndex = 13,
		Parent = content,
	})

	local row = Instance.new("Frame")
	row.Name = "Buttons"
	row.BackgroundTransparency = 1
	row.Size = UDim2.new(1, 0, 0, 74)
	row.LayoutOrder = 2
	row.ZIndex = 13
	row.Parent = content
	Theme.List(row, "Horizontal", 14, Enum.HorizontalAlignment.Center, Enum.VerticalAlignment.Center)

	if props.RobuxPrice then
		local robuxButton = Theme.Button({
			Name = "Robux",
			Text = if props.RobuxEnabled == false then "Robux not set up" else Format.Robux(props.RobuxPrice),
			Color = P.Robux,
			DarkColor = P.RobuxDark,
			Size = UDim2.fromOffset(210, 70),
			ZIndex = 14,
			LayoutOrder = 1,
			OnClick = function()
				close()
				if props.OnRobux then
					props.OnRobux()
				end
			end,
			Parent = row,
		})
		if props.RobuxEnabled == false then
			Theme.SetButtonEnabled(robuxButton, false)
		end
	end

	if props.CashPrice then
		local cashButton = Theme.Button({
			Name = "Cash",
			Text = "      " .. Format.Money(props.CashPrice),
			Color = P.ButtonGreen,
			DarkColor = P.ButtonGreenDark,
			Size = UDim2.fromOffset(230, 70),
			ZIndex = 14,
			LayoutOrder = 2,
			OnClick = function()
				close()
				if props.OnCash then
					props.OnCash()
				end
			end,
			Parent = row,
		})
		Theme.CoinIcon({
			Size = UDim2.fromOffset(34, 34),
			AnchorPoint = Vector2.new(0, 0.5),
			Position = UDim2.new(0, 12, 0.5, -2),
			ZIndex = 16,
			Parent = cashButton,
		})
		if props.CashAffordable == false then
			Theme.SetButtonEnabled(cashButton, false)
		end
	end

	if props.Footnote then
		Theme.Label({
			Name = "Footnote",
			Text = props.Footnote,
			MaxTextSize = 18,
			TextColor3 = Color3.fromRGB(220, 235, 255),
			Size = UDim2.new(1, 0, 0, 40),
			LayoutOrder = 3,
			ZIndex = 13,
			Parent = content,
		})
	end
	return close
end

function Theme.Confirm(props: Props): () -> ()
	local _, content, close = Theme.Panel({
		Name = "Confirm",
		Title = props.Title or "Are you sure?",
		Size = UDim2.fromOffset(480, 300),
		OnClose = props.OnClose,
	})
	Theme.List(content, "Vertical", 12)
	Theme.Label({
		Text = props.Body or "",
		MaxTextSize = 24,
		Size = UDim2.new(1, 0, 0, 110),
		LayoutOrder = 1,
		ZIndex = 13,
		Parent = content,
	})
	Theme.Button({
		Text = props.ConfirmText or "Yes",
		Color = props.ConfirmColor or P.ButtonGreen,
		Size = UDim2.fromOffset(220, 64),
		LayoutOrder = 2,
		ZIndex = 14,
		OnClick = function()
			close()
			props.OnConfirm()
		end,
		Parent = content,
	})
	return close
end

-------------------------------------------------------------------------------
-- Toasts and floating world text
-------------------------------------------------------------------------------
local toastGui: ScreenGui? = nil
local toastList: Frame? = nil

local TOAST_COLORS = {
	info = P.CardBlue,
	success = P.ButtonGreen,
	error = P.ButtonRed,
	cash = P.MoneyGreen,
}

-- kind: "info" | "success" | "error" | "cash"
function Theme.Toast(text: string, kind: string?)
	if not toastGui then
		toastGui = Theme.ScreenGui("ThemeToasts", 60)
		local list = Instance.new("Frame")
		list.Name = "List"
		list.BackgroundTransparency = 1
		list.AnchorPoint = Vector2.new(0.5, 0)
		list.Position = UDim2.new(0.5, 0, 0, 86)
		list.Size = UDim2.fromOffset(520, 300)
		list.Parent = toastGui
		Theme.List(list, "Vertical", 6)
		toastList = list
	end
	local color = TOAST_COLORS[kind or "info"] or P.CardBlue
	local toast = Instance.new("Frame")
	toast.BackgroundColor3 = color
	toast.Size = UDim2.fromOffset(460, 46)
	toast.LayoutOrder = math.floor(os.clock() * 100)
	toast.Parent = toastList
	Theme.Corner(toast, 12)
	Theme.Stroke(toast, P.CardOutline, 2.5, true)
	Theme.Label({ Text = text, MaxTextSize = 24, Size = UDim2.new(1, -16, 1, -6), Position = UDim2.fromScale(0.5, 0.5), AnchorPoint = Vector2.new(0.5, 0.5), Parent = toast })
	Theme.Pop(toast)
	task.delay(2.6, function()
		Theme.Tween(toast, { BackgroundTransparency = 1 }, 0.3)
		task.wait(0.3)
		toast:Destroy()
	end)
end

function Theme.Billboard(props: Props): BillboardGui
	local board = Instance.new("BillboardGui")
	board.Name = props.Name or "Billboard"
	board.Size = props.Size or UDim2.fromOffset(200, 60)
	board.StudsOffset = props.StudsOffset or Vector3.new(0, 3, 0)
	board.AlwaysOnTop = props.AlwaysOnTop == true
	board.MaxDistance = props.MaxDistance or 160
	board.LightInfluence = 0
	board.ResetOnSpawn = false
	if props.Adornee then
		board.Adornee = props.Adornee
	end
	board.Parent = props.Parent
	return board
end

-- Rising, fading text in the world ("+$12.50" over a shop).
function Theme.FloatText(position: Vector3, text: string, color: Color3?)
	local anchor = Instance.new("Part")
	anchor.Name = "FloatText"
	anchor.Anchored = true
	anchor.CanCollide = false
	anchor.CanQuery = false
	anchor.CanTouch = false
	anchor.Transparency = 1
	anchor.Size = Vector3.new(0.2, 0.2, 0.2)
	anchor.Position = position
	anchor.Parent = Workspace

	local board = Theme.Billboard({ Parent = anchor, Adornee = anchor, Size = UDim2.fromOffset(180, 50), StudsOffset = Vector3.zero, AlwaysOnTop = true, MaxDistance = 120 })
	local label = Theme.Label({ Text = text, TextColor3 = color or P.MoneyGreen, MaxTextSize = 34, Parent = board })
	Theme.Tween(anchor, { Position = position + Vector3.new(0, 6, 0) }, 1.2)
	task.delay(0.6, function()
		Theme.Tween(label, { TextTransparency = 1 }, 0.6)
		local stroke = label:FindFirstChildOfClass("UIStroke")
		if stroke then
			Theme.Tween(stroke, { Transparency = 1 }, 0.6)
		end
	end)
	task.delay(1.3, function()
		anchor:Destroy()
	end)
end

return Theme
