--!strict
--[[
	BaseController (client)
	Connects the home-base visuals (World/BaseVisuals) to the server
	(Server/Services/BaseService) and to the other controllers.

	What it does:
		* builds the local player's base visuals and keeps them in sync with
		  every data update (State.Changed) - only changed parts are rebuilt
		* turns ProximityPrompt presses into server requests:
			Unlock pad      -> Net.Request("UnlockSlot", slotId)
			Restock (E)     -> Net.Request("Restock", slotId)
			Upgrade (F)     -> Net.Request("UpgradeSection", slotId)
			"Hire me" tile  -> Theme.PurchaseDialog: R$ 200 (SHOP: "HireEmployeeRobux")
			                   or cash (Net.Request("HireEmployeeCash", slotId));
			                   with an employee token: Theme.Confirm -> "AssignEmployeeToken"
			                   (both are "Base" dialogs: closed when you leave the base)
			Arena prompt    -> registry.CombatController:OpenArenaMenu()
			Gate prompt     -> barrier not beaten: registry.CombatController:StartBarrier(area)
			                   beaten: Net.Request("UnlockGate", area)
		* shows server "Base" events: floating "+$12.50" income text, employee
		  restock hops
		* errors become red toasts; successes pop the billboard / show a toast

	Public API (other controllers):
		BaseController.Priority = 20
		BaseController:Init(registry)
		BaseController:Start()
	(No other methods: everything else happens through prompts and events.)
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)
local BaseVisuals = require(Client.World.BaseVisuals)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Net = require(Shared.Net)

local P = Config.Palette

local BaseController = {}
BaseController.Priority = 20

local registry: { [string]: any } = {}

-- Requests that are still waiting for the server (stops double clicks).
local pending: { [string]: boolean } = {}
local dialogOpen = false

-- slot id -> its definition
local slotDefs: { [string]: Config.SlotDef } = {}
for _, areaDef in Config.Areas do
	for _, slotDef in areaDef.slots do
		slotDefs[slotDef.id] = slotDef
	end
end

-- Send a request once at a time per `key`. Shows the error as a toast.
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

local function inBase(): boolean
	return State.Mode() == "Base"
end

-------------------------------------------------------------------------------
-- Prompt handlers
-------------------------------------------------------------------------------
local function onUnlock(slotId: string)
	if not inBase() then
		return
	end
	local def = slotDefs[slotId]
	local result = request("unlock:" .. slotId, "UnlockSlot", slotId)
	if result and result.ok and def then
		Theme.Toast(("Unlocked %s %s!"):format(def.icon, def.name), "success")
		BaseVisuals.FloatAt(slotId, "UNLOCKED!", P.PriceGold)
	end
end

local function onRestock(slotId: string)
	if not inBase() then
		return
	end
	local result = request("restock:" .. slotId, "Restock", slotId)
	if result and result.ok then
		BaseVisuals.FloatAt(slotId, "Restocked!", P.MoneyGreen)
		BaseVisuals.Pop(slotId)
	end
end

local function onUpgrade(slotId: string)
	if not inBase() then
		return
	end
	local data = State.Get()
	local section = data and data.base.sections[slotId]
	if section and Economy.SectionUpgradePrice(slotId, section.level) == nil then
		Theme.Toast("Already at max level!", "info")
		return
	end
	local result = request("upgrade:" .. slotId, "UpgradeSection", slotId)
	if result and result.ok then
		local def = slotDefs[slotId]
		local level = tonumber(result.level) or 0
		Theme.Toast(("%s upgraded to level %d!"):format(if def then def.name else "Section", level), "success")
		BaseVisuals.FloatAt(slotId, "LEVEL UP!", P.PriceGold)
		BaseVisuals.Pop(slotId)
	end
end

local function hired(slotId: string)
	local def = slotDefs[slotId]
	Theme.Toast(("Employee hired! %s now restocks itself."):format(if def then def.name else "This section"), "success")
	BaseVisuals.Pop(slotId)
end

local function onHire(slotId: string)
	if not inBase() or dialogOpen then
		return
	end
	local data = State.Get()
	if not data then
		return
	end
	local section = data.base.sections[slotId]
	if type(section) ~= "table" or section.employee then
		return
	end
	local def = slotDefs[slotId]
	local name = if def then def.name else "this section"

	-- An employee token (bought with Robux earlier) can be used for free.
	local tokens = tonumber(data.employeeTokens) or 0
	if tokens > 0 then
		dialogOpen = true
		Theme.Confirm({
			Name = "HireTokenConfirm",
			Context = "Base", -- the HUD closes it when you leave the base (OnClose resets dialogOpen)
			Title = "Use employee token",
			Body = ("You have %d employee token%s. Use one to hire an employee for %s for free?"):format(
				tokens,
				if tokens == 1 then "" else "s",
				name
			),
			ConfirmText = "Use token",
			OnClose = function()
				dialogOpen = false
			end,
			OnConfirm = function()
				local result = request("hire:" .. slotId, "AssignEmployeeToken", slotId)
				if result and result.ok then
					hired(slotId)
				end
			end,
		})
		return
	end

	local price = Economy.EmployeeCashPrice(slotId, data.base.highestArea)
	dialogOpen = true
	Theme.PurchaseDialog({
		Context = "Base", -- the HUD closes it when you leave the base (OnClose resets dialogOpen)
		Title = "Hire employee",
		Description = ("Restocks %s automatically so it never stops selling."):format(name),
		RobuxPrice = Config.Employee.Robux,
		RobuxEnabled = Config.ProductIds.Employee ~= 0,
		CashPrice = price,
		CashAffordable = data.cash + 1e-6 >= price,
		Footnote = ("= the earnings of %d Army Runs"):format(Config.Employee.RunsEquivalent),
		OnClose = function()
			dialogOpen = false
		end,
		OnRobux = function()
			-- SHOP shows the Robux purchase window; the receipt assigns the employee.
			request("hire:" .. slotId, "HireEmployeeRobux", slotId)
		end,
		OnCash = function()
			local result = request("hire:" .. slotId, "HireEmployeeCash", slotId)
			if result and result.ok then
				hired(slotId)
			end
		end,
	})
end

local function combat(): any
	return registry.CombatController
end

local function onArena()
	if not inBase() then
		return
	end
	local controller = combat()
	if not controller or type(controller.OpenArenaMenu) ~= "function" then
		Theme.Toast("The Combat Arena is not ready yet", "error")
		return
	end
	local ok, err = pcall(controller.OpenArenaMenu, controller)
	if not ok then
		warn("[BaseController] OpenArenaMenu failed: " .. tostring(err))
		Theme.Toast("Could not open the arena", "error")
	end
end

local function onGate(area: number)
	if not inBase() then
		return
	end
	local data = State.Get()
	if not data then
		return
	end
	if area ~= data.base.highestArea then
		Theme.Toast("Open the earlier gates first", "error")
		return
	end
	if data.base.barriers[tostring(area)] ~= true then
		local controller = combat()
		if not controller or type(controller.StartBarrier) ~= "function" then
			Theme.Toast("Combat is not ready yet", "error")
			return
		end
		local ok, err = pcall(controller.StartBarrier, controller, area)
		if not ok then
			warn("[BaseController] StartBarrier failed: " .. tostring(err))
			Theme.Toast("Could not start the barrier fight", "error")
		end
		return
	end
	local price = Economy.GatePrice(area)
	if data.cash + 1e-6 < price then
		Theme.Toast("Not enough cash! You need " .. Format.Money(price), "error")
		return
	end
	request("gate:" .. area, "UnlockGate", area)
	-- The server sends the "Area unlocked" toast; the gate opens on the next sync.
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function BaseController:Init(reg: { [string]: any })
	registry = reg
end

function BaseController:Start()
	BaseVisuals.Start({
		OnUnlock = onUnlock,
		OnRestock = onRestock,
		OnUpgrade = onUpgrade,
		OnHire = onHire,
		OnArena = onArena,
		OnGate = onGate,
	})

	local data = State.Get()
	if data then
		BaseVisuals.Update(data)
	end
	State.Changed:Connect(function(snapshot)
		BaseVisuals.Update(snapshot)
	end)

	Net.On("Base", function(msg: any, payload: any)
		if type(payload) ~= "table" then
			return
		end
		if msg == "Income" then
			if not inBase() then
				return
			end
			for slotId, amount in payload do
				if type(slotId) == "string" and type(amount) == "number" then
					BaseVisuals.ShowIncome(slotId, amount)
				end
			end
		elseif msg == "Restocked" then
			if type(payload.slotId) == "string" and payload.by == "employee" then
				BaseVisuals.EmployeeHop(payload.slotId)
			end
		elseif msg == "Employee" then
			if type(payload.slotId) == "string" then
				BaseVisuals.EmployeeHop(payload.slotId)
			end
		end
	end)
end

return BaseController
