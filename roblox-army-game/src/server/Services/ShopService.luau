--!strict
--[[
	ShopService (server)
	The ONE MarketplaceService.ProcessReceipt for the whole game (Roblox only
	allows one). Every Robux developer product is granted here.

	Products (Config.ProductIds, 0 = not set up and ignored):
		"Employee"         -> an employee for the section the player picked with
		                      HireEmployeeRobux (BaseService:AssignEmployee). If that
		                      section can no longer take one, the player gets an
		                      employee token instead (data.employeeTokens += 1),
		                      to use later on any "Hire me" tile.
		"x2_15" ... "skip_24" (boost ids) -> BoostService:ApplyBoost
		"vehicle_<id>"     -> VehicleService:Grant

	Receipt rules (Roblox requirements):
		* Player not in this server, or data not loaded -> NotProcessedYet
		  (Roblox retries later, e.g. when they rejoin).
		* Already processed (DataService:HasReceipt) -> PurchaseGranted, but
		  only once it is saved: when saving is on (DataService:IsSaving) we
		  SaveNow again and answer NotProcessedYet if that save fails.
		* Grant -> DataService:AddReceipt -> DataService:SaveNow ->
		  PurchaseGranted. If the grant fails or errors -> NotProcessedYet.
		  If saving is on and SaveNow fails -> NotProcessedYet: the grant and
		  the receipt stay in memory, so Roblox's retry takes the HasReceipt
		  branch (saves again) and never grants twice. Without DataStore access
		  (IsSaving() == false, e.g. Studio) a granted purchase is
		  PurchaseGranted right away.

	Requests (Net actions):
		HireEmployeeRobux(slotId)
			slot must be an unlocked revenue section without an employee and the
			Employee product must be set up; remembers the slot and opens the
			Roblox purchase window.

	Push events on the "Shop" channel:
		"Purchased" { key }   a Robux product was granted (client can refresh)

	Public API:
		ShopService:GetProductKey(productId: number): string?
		ShopService:GrantProduct(player, key): boolean   -- used by the receipt
]]

local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Net = require(Shared.Net)

local ShopService = {}
ShopService.Priority = 60

local Services: { [string]: any } = {}

-- [PROPOSAL] A pending "hire for Robux" target is remembered for 10 minutes.
local PENDING_EMPLOYEE_SECONDS = 600

-- productId -> key in Config.ProductIds
local productToKey: { [number]: string } = {}
for key, productId in Config.ProductIds do
	if type(productId) == "number" and productId ~= 0 then
		if productToKey[productId] then
			warn(
				("[ShopService] product id %d is used for both %s and %s"):format(
					productId,
					productToKey[productId],
					key
				)
			)
		end
		productToKey[productId] = key
	end
end

local boostIds: { [string]: boolean } = {}
for _, def in Config.Boosts do
	boostIds[def.id] = true
end

-- slot id -> definition + area (for validation)
local slotDefs: { [string]: Config.SlotDef } = {}
for _, area in Config.Areas do
	for _, slot in area.slots do
		slotDefs[slot.id] = slot
	end
end

type Pending = { slotId: string, at: number }
local pendingEmployee: { [Player]: Pending } = {}
local processing: { [string]: boolean } = {}

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------

local function dataService(): any
	return Services.DataService
end

local function getData(player: Player): any
	local ds = dataService()
	return if ds then ds:Get(player) else nil
end

local function getMode(player: Player): string
	local modeService = Services.ModeService
	if modeService then
		return modeService:GetMode(player)
	end
	return (player:GetAttribute("Mode") :: string?) or "Base"
end

-- Can `slotId` take an employee right now?
local function canTakeEmployee(data: any, slotId: string): boolean
	local def = slotDefs[slotId]
	if not def or def.kind ~= "revenue" then
		return false
	end
	local section = data.base.sections[slotId]
	return type(section) == "table" and section.employee ~= true
end

local function grantEmployee(player: Player, data: any): boolean
	local pending = pendingEmployee[player]
	pendingEmployee[player] = nil
	local base = Services.BaseService
	if
		pending
		and os.clock() - pending.at <= PENDING_EMPLOYEE_SECONDS
		and base
		and type(base.AssignEmployee) == "function"
		and canTakeEmployee(data, pending.slotId)
	then
		if base:AssignEmployee(player, pending.slotId) then
			local def = slotDefs[pending.slotId]
			Net.Notify(
				player,
				("🧑‍💼 Employee hired for %s! It restocks by itself now."):format(
					if def then def.name else "your shop"
				),
				"success"
			)
			return true
		end
	end
	-- No valid target: keep the employee as a token for later.
	data.employeeTokens = (tonumber(data.employeeTokens) or 0) + 1
	local ds = dataService()
	if ds then
		ds:MarkDirty(player)
	end
	Net.Notify(
		player,
		'🧑‍💼 Employee token added! Walk to any "Hire me" tile and choose "Use employee token".',
		"success"
	)
	return true
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------

function ShopService:GetProductKey(productId: number): string?
	return productToKey[productId]
end

-- Apply the reward for one product key. Returns true only when it was applied.
function ShopService:GrantProduct(player: Player, key: string): boolean
	local data = getData(player)
	if not data then
		return false
	end
	if key == "Employee" then
		return grantEmployee(player, data)
	end
	if boostIds[key] then
		local boosts = Services.BoostService
		if not boosts or type(boosts.ApplyBoost) ~= "function" then
			return false
		end
		if not boosts:ApplyBoost(player, key) then
			return false
		end
		if type(boosts.RemoveReservedOffer) == "function" then
			boosts:RemoveReservedOffer(player, key)
		end
		return true
	end
	local vehicleId = string.match(key, "^vehicle_(.+)$")
	if vehicleId then
		local vehicles = Services.VehicleService
		if not vehicles or type(vehicles.Grant) ~= "function" then
			return false
		end
		return vehicles:Grant(player, vehicleId) == true
	end
	warn("[ShopService] no grant rule for product key " .. key)
	return false
end

-------------------------------------------------------------------------------
-- Receipt
-------------------------------------------------------------------------------

local function processReceipt(receiptInfo: { [string]: any }): Enum.ProductPurchaseDecision
	local playerId = receiptInfo.PlayerId
	local productId = receiptInfo.ProductId
	local purchaseId = tostring(receiptInfo.PurchaseId)
	if type(playerId) ~= "number" or type(productId) ~= "number" then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	local player = Players:GetPlayerByUserId(playerId)
	if not player then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	local ds = dataService()
	if not ds then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	-- Roblox re-delivers unfinished purchases right as the player joins, while
	-- their save may still be loading (up to ~20 s if another server is still
	-- finishing its save). Wait for it instead of failing straight away.
	if not ds:Get(player) and not ds:WaitFor(player, 45) then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	if ds:HasReceipt(player, purchaseId) then
		-- Granted earlier; tell Roblox only once that is actually saved.
		if ds:IsSaving() then
			local ok, saved = pcall(function()
				return ds:SaveNow(player)
			end)
			if not ok or saved ~= true then
				warn("[ShopService] save of an earlier purchase failed, will retry: " .. tostring(saved))
				return Enum.ProductPurchaseDecision.NotProcessedYet
			end
		end
		return Enum.ProductPurchaseDecision.PurchaseGranted
	end
	if processing[purchaseId] then
		-- The same receipt is being granted right now (Roblox retried early).
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	local key = productToKey[productId]
	if not key then
		warn(("[ShopService] unknown product id %d (check Config.ProductIds)"):format(productId))
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	processing[purchaseId] = true
	local ok, granted = pcall(function()
		return ShopService:GrantProduct(player, key)
	end)
	if not ok or granted ~= true then
		processing[purchaseId] = nil
		if not ok then
			warn("[ShopService] grant failed: " .. tostring(granted))
		end
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	ds:AddReceipt(player, purchaseId)
	processing[purchaseId] = nil
	local saveOk, saved = pcall(function()
		return ds:SaveNow(player)
	end)
	if player.Parent then
		Net.Fire("Shop", player, "Purchased", { key = key })
	end
	if ds:IsSaving() and (not saveOk or saved ~= true) then
		-- The grant + receipt stay in memory: the retry takes the HasReceipt
		-- branch above, saves again and never grants twice.
		warn("[ShopService] save after purchase failed, Roblox will retry: " .. tostring(saved))
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	return Enum.ProductPurchaseDecision.PurchaseGranted
end

-------------------------------------------------------------------------------
-- Requests
-------------------------------------------------------------------------------

local function hireEmployeeRobux(player: Player, slotId: any): Net.Result
	if type(slotId) ~= "string" or not slotDefs[slotId] then
		return Net.Fail("Unknown section")
	end
	local data = getData(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	if getMode(player) ~= "Base" then
		return Net.Fail("Go back to your base first")
	end
	local def = slotDefs[slotId]
	if def.kind ~= "revenue" then
		return Net.Fail("Only shops can have employees")
	end
	local section = data.base.sections[slotId]
	if type(section) ~= "table" then
		return Net.Fail("Unlock this section first")
	end
	if section.employee == true then
		return Net.Fail("This section already has an employee")
	end
	local productId = Config.ProductIds.Employee or 0
	if productId == 0 then
		return Net.Fail("Robux purchases are not set up yet")
	end
	pendingEmployee[player] = { slotId = slotId, at = os.clock() }
	local ok, err = pcall(function()
		MarketplaceService:PromptProductPurchase(player, productId)
	end)
	if not ok then
		warn("[ShopService] PromptProductPurchase failed: " .. tostring(err))
		return Net.Fail("Could not open the Robux purchase window")
	end
	return Net.Ok({ prompted = true })
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------

function ShopService:Init(registry: { [string]: any })
	Services = registry
	Net.Handle("HireEmployeeRobux", hireEmployeeRobux)

	MarketplaceService.ProcessReceipt = function(receiptInfo)
		local ok, decision = pcall(processReceipt, receiptInfo :: any)
		if ok then
			return decision
		end
		warn("[ShopService] ProcessReceipt error: " .. tostring(decision))
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
end

function ShopService:Start()
	Players.PlayerRemoving:Connect(function(player)
		pendingEmployee[player] = nil
	end)
end

return ShopService
