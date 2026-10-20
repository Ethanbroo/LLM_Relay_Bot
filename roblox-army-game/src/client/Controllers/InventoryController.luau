--!strict
--[[
	InventoryController (client)
	Adds the "Gear" card to the HUD menu (base only) that opens the Armoury
	(InventoryUI), and listens to the server's "Inventory" channel.

	API (other controllers, from Start or later):
		InventoryController:Open()    -- open the Armoury (only in Base mode)
		InventoryController:Close()   -- close it if this controller opened it

	Behaviour:
		* The Armoury opened from the HUD closes itself when you leave Base mode
		  (the Combat lobby opens its own copy through CombatController).
		* "Granted"   -> the new item gets a "NEW" badge in the Armoury.
		* "Converted" -> toast: your inventory was full, the drop became cash.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Client = script.Parent.Parent
local InventoryUI = require(Client.UI.InventoryUI)
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local EquipmentDefs = require(Shared.Combat.EquipmentDefs)
local Format = require(Shared.Format)
local Net = require(Shared.Net)

local InventoryController = {}

local registry: { [string]: any } = {}
local closeArmoury: (() -> ())? = nil

function InventoryController:Init(reg: { [string]: any })
	registry = reg
end

function InventoryController:Open()
	if State.Mode() ~= "Base" then
		return
	end
	if closeArmoury then
		return -- already open (double click)
	end
	closeArmoury = InventoryUI.Open({
		Title = "Armoury",
		OnClose = function()
			closeArmoury = nil
		end,
	})
end

function InventoryController:Close()
	local close = closeArmoury
	if close then
		closeArmoury = nil
		close()
	end
end

function InventoryController:Start()
	local hud = registry.HUDController
	if hud and hud.AddMenuButton then
		hud:AddMenuButton({
			Id = "Gear",
			Title = "Gear",
			Icon = "🎒",
			Order = 3,
			OnClick = function()
				self:Open()
			end,
		})
	else
		warn("[InventoryController] HUDController missing: no Gear button")
	end

	State.ModeChanged:Connect(function(newMode: string)
		if newMode ~= "Base" then
			self:Close()
		end
	end)

	Net.On("Inventory", function(msg: any, payload: any)
		if type(payload) ~= "table" then
			return
		end
		if msg == "Granted" then
			if type(payload.uid) == "string" then
				InventoryUI.MarkNew(payload.uid)
			end
		elseif msg == "Converted" then
			local def = EquipmentDefs.GetDef(payload.item)
			local name = if def then def.name else "item"
			local value = tonumber(payload.value) or 0
			Theme.Toast(("Inventory full! %s scrapped for +%s"):format(name, Format.Money(value)), "cash")
		end
	end)
end

return InventoryController
