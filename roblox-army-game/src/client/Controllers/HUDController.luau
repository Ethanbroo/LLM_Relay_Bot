--!strict
--[[
	HUDController
	Always-on HUD: cash pill (top-right, like both reference images), boost /
	status chips under it, the left-hand menu of card buttons (base only) and
	server toasts.

	API (other controllers, call from Start):
		HUD:AddMenuButton({
			Id = "Run", Title = "Army Run", Icon = "🏃", Order = 1,
			HeaderColor = Color3?, HeaderDarkColor = Color3?,
			OnClick = function() end,
			Visible = function(data) return true end,  -- optional, re-checked on every Sync
		}) -> TextButton
		HUD:AddStatusChip(id, color) -> { SetText(text), SetVisible(bool), Destroy() }
		HUD:Toast(text, kind?)
		HUD:GetGui() -> ScreenGui
		HUD:SetMenuVisible(visible)   -- e.g. hide while a big panel is open
]]

local GuiService = game:GetService("GuiService")
local Players = game:GetService("Players")
local StarterGui = game:GetService("StarterGui")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Shared = ReplicatedStorage:WaitForChild("Shared")
local Net = require(Shared.Net)

local P = Theme.Palette
local _player = Players.LocalPlayer

type MenuSpec = {
	Id: string,
	Title: string,
	Icon: string,
	Order: number?,
	HeaderColor: Color3?,
	HeaderDarkColor: Color3?,
	OnClick: () -> (),
	Visible: ((any) -> boolean)?,
}

type Chip = {
	SetText: (string) -> (),
	SetVisible: (boolean) -> (),
	Destroy: () -> (),
}

local HUD = {}
HUD.Priority = 80

local gui: ScreenGui
local menu: Frame
local chips: Frame
local setCash: (number) -> ()
local menuEntries: { { spec: MenuSpec, button: TextButton } } = {}
local menuForcedHidden = false

local function refreshMenu()
	local data = State.Get()
	local inBase = State.Mode() == "Base"
	menu.Visible = inBase and not menuForcedHidden
	for _, entry in menuEntries do
		local visible = true
		if entry.spec.Visible and data then
			local ok, result = pcall(entry.spec.Visible, data)
			visible = ok and result == true
		end
		entry.button.Visible = visible
	end
end

function HUD:Init(_registry: { [string]: any })
	gui = Theme.ScreenGui("HUD", 5)
	-- [PROPOSAL] Roblox's player list sits in the top-right corner, exactly where
	-- the reference images put the cash pill. The game has no leaderboard stats,
	-- so hide the list (players can still be seen in the Roblox menu).
	pcall(function()
		StarterGui:SetCoreGuiEnabled(Enum.CoreGuiType.PlayerList, false)
	end)

	local topRight = Instance.new("Frame")
	topRight.Name = "TopRight"
	topRight.BackgroundTransparency = 1
	topRight.AnchorPoint = Vector2.new(1, 0)
	-- Below Roblox's top bar (the HUD gui ignores the inset).
	topRight.Position = UDim2.new(1, -18, 0, Theme.TopInset() + 8)
	GuiService:GetPropertyChangedSignal("TopbarInset"):Connect(function()
		topRight.Position = UDim2.new(1, -18, 0, Theme.TopInset() + 8)
	end)
	topRight.Size = UDim2.fromOffset(250, 400)
	topRight.Parent = gui
	Theme.ResponsiveScale(topRight)
	local layout = Theme.List(topRight, "Vertical", 8, Enum.HorizontalAlignment.Right)
	layout.HorizontalAlignment = Enum.HorizontalAlignment.Right

	local pill, setter = Theme.CashPill({ LayoutOrder = 1, Parent = topRight })
	pill.Size = UDim2.fromOffset(240, 58)
	setCash = setter

	chips = Instance.new("Frame")
	chips.Name = "Chips"
	chips.BackgroundTransparency = 1
	chips.Size = UDim2.fromOffset(240, 300)
	chips.LayoutOrder = 2
	chips.Parent = topRight
	local chipLayout = Theme.List(chips, "Vertical", 6, Enum.HorizontalAlignment.Right)
	chipLayout.HorizontalAlignment = Enum.HorizontalAlignment.Right

	menu = Instance.new("Frame")
	menu.Name = "Menu"
	menu.BackgroundTransparency = 1
	menu.AnchorPoint = Vector2.new(0, 0.5)
	menu.Position = UDim2.new(0, 16, 0.5, 0)
	menu.Size = UDim2.fromOffset(124, 640)
	menu.Parent = gui
	Theme.ResponsiveScale(menu)
	local menuLayout = Theme.List(menu, "Vertical", 10, Enum.HorizontalAlignment.Left, Enum.VerticalAlignment.Center)
	menuLayout.HorizontalAlignment = Enum.HorizontalAlignment.Left
end

function HUD:Start()
	local data = State.Get()
	if data then
		setCash(data.cash)
	end
	State.Changed:Connect(function(snapshot)
		setCash(snapshot.cash)
		refreshMenu()
	end)
	State.ModeChanged:Connect(function(newMode: string)
		-- Dialogs opened in the base (hire, shop, arena menu, ...) must not stay
		-- over the run / fight screens.
		if newMode ~= "Base" then
			Theme.CloseModals("Base")
		end
		refreshMenu()
	end)
	Net.On("Notify", function(text: string, kind: string?)
		Theme.Toast(text, kind)
	end)
	refreshMenu()
end

function HUD:GetGui(): ScreenGui
	return gui
end

function HUD:Toast(text: string, kind: string?)
	Theme.Toast(text, kind)
end

function HUD:SetMenuVisible(visible: boolean)
	menuForcedHidden = not visible
	refreshMenu()
end

-- Small card button: coloured header with the title, blue body with an icon.
function HUD:AddMenuButton(spec: MenuSpec): TextButton
	local button = Instance.new("TextButton")
	button.Name = spec.Id
	button.Text = ""
	button.AutoButtonColor = false
	button.BackgroundTransparency = 1
	button.Size = UDim2.fromOffset(118, 104)
	button.LayoutOrder = spec.Order or 10
	button.Parent = menu

	local card, body = Theme.Card({
		Title = spec.Title,
		HeaderColor = spec.HeaderColor or P.CardOrange,
		HeaderDarkColor = spec.HeaderDarkColor or P.CardOrangeDark,
		HeaderHeight = 30,
		TitleSize = 20,
		Size = UDim2.fromScale(1, 1),
		Parent = button,
	})
	card.Active = false
	Theme.Label({
		Text = spec.Icon,
		MaxTextSize = 44,
		StrokeThickness = 0,
		Size = UDim2.fromScale(1, 1),
		ZIndex = card.ZIndex + 3,
		Parent = body,
	})

	button.Activated:Connect(function()
		Theme.Pop(button)
		spec.OnClick()
	end)
	table.insert(menuEntries, { spec = spec, button = button })
	refreshMenu()
	return button
end

function HUD:AddStatusChip(id: string, color: Color3?): Chip
	local chip = Instance.new("Frame")
	chip.Name = id
	chip.BackgroundColor3 = color or P.OfferPurple
	chip.Size = UDim2.fromOffset(200, 38)
	chip.Visible = false
	chip.Parent = chips
	Theme.Corner(chip, 0.5)
	Theme.Stroke(chip, P.CardOutline, 2, true)
	local label = Theme.Label({
		Text = "",
		MaxTextSize = 22,
		Size = UDim2.new(1, -16, 1, -6),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = chip,
	})
	return {
		SetText = function(text: string)
			label.Text = text
		end,
		SetVisible = function(visible: boolean)
			chip.Visible = visible
		end,
		Destroy = function()
			chip:Destroy()
		end,
	}
end

return HUD
