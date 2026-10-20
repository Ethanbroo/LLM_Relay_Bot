--!strict
--[[
	ShopController (client)
	Everything the player sees of the SHOP system:

		* HUD card "Shop" (🛒, pink header) -> UI/ShopUI panel (Vehicles / Boosts)
		* HUD card "Ride" (🚙, only when a vehicle is equipped) -> Net "ToggleRide".
		  Its title switches to "Get off" while riding (Player attribute "Riding").
		* HUD status chip "2x CASH 12:30" while a 2x boost runs. It counts down
		  locally every second and is corrected by every data Sync / "Timer" event.
		* Random boost offers on the home map (World/BoostOfferVisuals). Walking
		  into one opens Theme.PurchaseDialog (Robux or cash) ->
		  Net.Request("BuyBoostOffer", offerId, route).

	Server events it listens to:
		"Boost"   "Offer" / "OfferGone" / "Timer"   (BoostService)
		"Shop"    "Purchased"                       (ShopService, Robux receipts)
	It sends Net.FireServer("Boost", "Ready") once so the server re-sends any
	offers that appeared before this script was listening.

	Public API (other controllers):
		ShopController.Priority = 10
		ShopController:Init(registry)
		ShopController:Start()
		ShopController:OpenShop(tab: string?)    -- "Vehicles" (default) | "Boosts"
		ShopController:GetBoostSeconds(): number -- 2x seconds left (local estimate)
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)
local ShopUI = require(Client.UI.ShopUI)
local BoostOfferVisuals = require(Client.World.BoostOfferVisuals)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Net = require(Shared.Net)

local P = Config.Palette
local player = Players.LocalPlayer

local ShopController = {}
ShopController.Priority = 10

local registry: { [string]: any } = {}

-- Requests still waiting for the server (stops double clicks).
local pending: { [string]: boolean } = {}

-- 2x timer: seconds left at the moment `boostSyncedAt` (os.clock()).
local boostSeconds = 0
local boostSyncedAt = os.clock()

-- The boost-offer dialog that is open right now (if any).
local offerDialog: { offerId: string, close: () -> () }? = nil

local rideButton: TextButton? = nil

local boostById: { [string]: Config.BoostDef } = {}
for _, def in Config.Boosts do
	boostById[def.id] = def
end

local function request(key: string, action: string, ...: any): Net.Result?
	if pending[key] then
		return nil
	end
	pending[key] = true
	local result = Net.Request(action, ...)
	pending[key] = nil
	if not result.ok then
		Theme.Toast(tostring(result.err or "Something went wrong"), "error")
	end
	return result
end

function ShopController:GetBoostSeconds(): number
	return math.max(0, boostSeconds - (os.clock() - boostSyncedAt))
end

local function setBoostSeconds(seconds: any)
	if type(seconds) == "number" and seconds == seconds then
		boostSeconds = math.max(0, seconds)
		boostSyncedAt = os.clock()
	end
end

-------------------------------------------------------------------------------
-- Boost offers
-------------------------------------------------------------------------------

local function closeOfferDialog(offerId: string?)
	local dialog = offerDialog
	if dialog and (offerId == nil or dialog.offerId == offerId) then
		offerDialog = nil
		dialog.close()
	end
end

local function offerDescription(def: Config.BoostDef, area: number): string
	local value = Economy.BoostValue(def.id, area)
	if def.kind == "multiplier" then
		return ("Doubles ALL cash you earn (runs, shops, combat) for %d minutes of play time. Adds to any time you already have.\nWorth about %s if you keep playing."):format(
			def.minutes or 0,
			Format.Money(value)
		)
	end
	return ("Instantly collects %d hours of your shops' revenue. Shops with an employee pay the whole time, the others until their stock runs out.\nWorth about %s."):format(
		def.hours or 0,
		Format.Money(value)
	)
end

local function openOfferDialog(offer: BoostOfferVisuals.Offer)
	if offerDialog or State.Mode() ~= "Base" or ShopUI.IsOpen() then
		return
	end
	local data = State.Get()
	local def = boostById[offer.boostId]
	if not data or not def then
		return
	end
	local area = data.base.highestArea
	local price = Economy.BoostCoinPrice(def.id, area)
	local productId = Config.ProductIds[def.id] or 0
	local offerId = offer.id
	local close = Theme.PurchaseDialog({
		Title = def.name,
		Description = offerDescription(def, area),
		RobuxPrice = def.robux,
		RobuxEnabled = productId ~= 0,
		CashPrice = price,
		CashAffordable = data.cash + 1e-6 >= price,
		Footnote = "Cash price = 80% of its value: buying with cash still pays back +25% if you use it fully",
		OnClose = function()
			if offerDialog and offerDialog.offerId == offerId then
				offerDialog = nil
			end
		end,
		OnRobux = function()
			request("offer:" .. offerId, "BuyBoostOffer", offerId, "robux")
		end,
		OnCash = function()
			local result = request("offer:" .. offerId, "BuyBoostOffer", offerId, "cash")
			if result and result.ok then
				BoostOfferVisuals.Remove(offerId)
			end
		end,
	})
	offerDialog = { offerId = offerId, close = close }
end

local function onBoostEvent(msg: any, payload: any)
	if type(payload) ~= "table" then
		return
	end
	if msg == "Offer" then
		BoostOfferVisuals.Add(payload)
	elseif msg == "OfferGone" then
		if type(payload.id) == "string" then
			BoostOfferVisuals.Remove(payload.id)
			closeOfferDialog(payload.id)
		end
	elseif msg == "Timer" then
		setBoostSeconds(payload.seconds)
	end
end

-------------------------------------------------------------------------------
-- Shop panel
-------------------------------------------------------------------------------

local function buyVehicle(vehicleId: string, route: string)
	-- The server shows the "You got the ..." toast once the vehicle is granted
	-- (right away for cash, after the Roblox window for Robux).
	request("vehicle:" .. vehicleId, "BuyVehicle", vehicleId, route)
end

local function equipVehicle(vehicleId: string)
	local result = request("equip", "EquipVehicle", vehicleId)
	if result and result.ok then
		Theme.Toast("🚙 Vehicle equipped! Use the Ride button to drive it.", "success")
	end
end

function ShopController:OpenShop(tab: string?)
	if State.Mode() ~= "Base" then
		Theme.Toast("The shop is open in your base", "error")
		return
	end
	closeOfferDialog(nil)
	ShopUI.Open({
		Tab = tab,
		OnBuyVehicle = buyVehicle,
		OnEquipVehicle = equipVehicle,
		GetBoostSeconds = function(): number
			return ShopController:GetBoostSeconds()
		end,
	})
end

-------------------------------------------------------------------------------
-- Ride button
-------------------------------------------------------------------------------

local function updateRideTitle()
	local button = rideButton
	if not button then
		return
	end
	local title = button:FindFirstChild("Title", true)
	if title and title:IsA("TextLabel") then
		title.Text = if player:GetAttribute("Riding") == true then "Get off" else "Ride"
	end
end

local function toggleRide()
	if State.Mode() ~= "Base" and player:GetAttribute("Riding") ~= true then
		Theme.Toast("You can only ride in your base", "error")
		return
	end
	request("ride", "ToggleRide")
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------

function ShopController:Init(reg: { [string]: any })
	registry = reg
end

function ShopController:Start()
	local hud = registry.HUDController

	local data = State.Get()
	if data then
		setBoostSeconds(data.boosts.multiplierSeconds)
	end

	if hud and type(hud.AddMenuButton) == "function" then
		hud:AddMenuButton({
			Id = "Shop",
			Title = "Shop",
			Icon = "🛒",
			Order = 4,
			HeaderColor = P.CardPink,
			HeaderDarkColor = P.CardPinkDark,
			OnClick = function()
				ShopController:OpenShop(nil)
			end,
		})
		rideButton = hud:AddMenuButton({
			Id = "Ride",
			Title = "Ride",
			Icon = "🚙",
			Order = 5,
			OnClick = toggleRide,
			Visible = function(snapshot: any): boolean
				local equipped = snapshot.vehicles and snapshot.vehicles.equipped
				return equipped ~= nil and snapshot.vehicles.owned[equipped] == true
			end,
		})
		updateRideTitle()
	end

	-- 2x status chip.
	local chip = if hud and type(hud.AddStatusChip) == "function"
		then hud:AddStatusChip("Boost2x", P.OfferPurple)
		else nil
	local function updateChip()
		if not chip then
			return
		end
		local seconds = ShopController:GetBoostSeconds()
		if seconds > 0 then
			chip.SetText("2x CASH " .. Format.Clock(seconds))
			chip.SetVisible(true)
		else
			chip.SetVisible(false)
		end
	end
	updateChip()
	task.spawn(function()
		while true do
			task.wait(1)
			updateChip()
		end
	end)

	State.Changed:Connect(function(snapshot)
		setBoostSeconds(snapshot.boosts.multiplierSeconds)
		updateChip()
		ShopUI.Refresh()
	end)
	State.ModeChanged:Connect(function(newMode: string)
		if newMode ~= "Base" then
			closeOfferDialog(nil)
			ShopUI.Close()
		end
	end)
	player:GetAttributeChangedSignal("Riding"):Connect(updateRideTitle)

	-- Boost offers.
	BoostOfferVisuals.Start({ OnApproach = openOfferDialog })
	Net.On("Boost", onBoostEvent)
	Net.On("Shop", function(msg: any)
		if msg == "Purchased" then
			ShopUI.Refresh()
		end
	end)
	-- Ask for offers that were sent before we were listening.
	pcall(function()
		Net.FireServer("Boost", "Ready")
	end)
end

return ShopController
