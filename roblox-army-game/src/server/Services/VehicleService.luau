--!strict
--[[
	VehicleService (server)
	Vehicles from Config.Vehicles: buying (Robux or cash), equipping, and
	riding them around the home base.

	How riding works:
		"Ride" builds the equipped vehicle out of parts (Shared/VehicleModels),
		puts it around the character and welds it to the HumanoidRootPart with
		a WeldConstraint. All vehicle parts are Massless and do not collide, so
		the character still walks normally - only faster: Humanoid.WalkSpeed is
		set to the vehicle's speed (the old speed is remembered and restored).
		You get off automatically when you leave Base mode (runner / combat),
		die, respawn or leave the game.
		The Player attribute "Riding" (boolean) tells the client.

	Requests (Net actions, all re-validated here):
		BuyVehicle(vehicleId, route)  route = "cash" | "robux"
			needs data.base.highestArea >= def.area and not owned yet.
			"cash"  -> Economy.VehicleCoinPrice(id, highestArea)
			"robux" -> Config.ProductIds["vehicle_" .. id] -> Roblox purchase
			           window; ShopService's receipt calls Grant.
		EquipVehicle(vehicleId | nil) must own it (nil = unequip)
		ToggleRide()                  Base mode only, needs an equipped vehicle

	Public API (docs/ARCHITECTURE.md §4.4 + helpers):
		VehicleService:GetRunnerBonus(player): number   -- e.g. 0.06 = +6% runner payout
		VehicleService:Dismount(player)
		VehicleService:Grant(player, vehicleId): boolean  -- give a vehicle (paid already)
		VehicleService:IsRiding(player): boolean
		VehicleService:GetVehicleDef(vehicleId): Config.VehicleDef?

	Push events on the "Vehicle" channel:
		"Granted" { vehicleId }   a vehicle was added to the garage
]]

local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Net = require(Shared.Net)
local VehicleModels = require(Shared.VehicleModels)

local VehicleService = {}
VehicleService.Priority = 55

local Services: { [string]: any } = {}

type Ride = {
	vehicleId: string,
	model: Model,
	humanoid: Humanoid,
	originalSpeed: number,
	rideSpeed: number,
	connections: { RBXScriptConnection },
}

local rides: { [Player]: Ride } = {}
local playerConnections: { [Player]: { RBXScriptConnection } } = {}

local vehicleById: { [string]: Config.VehicleDef } = {}
for _, def in Config.Vehicles do
	vehicleById[def.id] = def
end

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------

local function isFiniteNumber(value: any): boolean
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

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

local function markDirty(player: Player)
	local ds = dataService()
	if ds then
		ds:MarkDirty(player)
	end
end

local function owns(data: any, vehicleId: string): boolean
	return type(data.vehicles.owned) == "table" and data.vehicles.owned[vehicleId] == true
end

-------------------------------------------------------------------------------
-- Riding
-------------------------------------------------------------------------------

local function dismount(player: Player)
	local ride = rides[player]
	if not ride then
		if player.Parent then
			player:SetAttribute("Riding", false)
		end
		return
	end
	rides[player] = nil
	for _, connection in ride.connections do
		connection:Disconnect()
	end
	-- Only put the old speed back if nobody else changed it meanwhile
	-- (e.g. combat sets its own move speed).
	local humanoid = ride.humanoid
	if humanoid.Parent and math.abs(humanoid.WalkSpeed - ride.rideSpeed) < 0.01 then
		humanoid.WalkSpeed = ride.originalSpeed
	end
	ride.model:Destroy()
	if player.Parent then
		player:SetAttribute("Riding", false)
	end
end

-- Returns (true) or (false, reason).
local function mount(player: Player, def: Config.VehicleDef): (boolean, string?)
	dismount(player)
	local character = player.Character
	if not character then
		return false, "Your character is not ready"
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	if not root or not root:IsA("BasePart") or not humanoid or humanoid.Health <= 0 then
		return false, "Your character is not ready"
	end
	if humanoid.Sit then
		humanoid.Sit = false
	end

	local model = VehicleModels.Build(def)
	local vehicleRoot = model.PrimaryPart
	if not vehicleRoot then
		model:Destroy()
		return false, "Could not build the vehicle"
	end
	model:PivotTo(root.CFrame)
	local weld = Instance.new("WeldConstraint")
	weld.Name = "VehicleMount"
	weld.Part0 = vehicleRoot
	weld.Part1 = root
	weld.Parent = vehicleRoot
	model.Parent = character

	local ride: Ride = {
		vehicleId = def.id,
		model = model,
		humanoid = humanoid,
		originalSpeed = humanoid.WalkSpeed,
		rideSpeed = def.speed,
		connections = {},
	}
	humanoid.WalkSpeed = def.speed
	rides[player] = ride
	player:SetAttribute("Riding", true)

	-- Get off on death, or if the model / character disappears.
	table.insert(
		ride.connections,
		humanoid.Died:Connect(function()
			if rides[player] == ride then
				dismount(player)
			end
		end)
	)
	table.insert(
		ride.connections,
		model.AncestryChanged:Connect(function()
			if rides[player] == ride and not model:IsDescendantOf(workspace) then
				dismount(player)
			end
		end)
	)
	return true, nil
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------

function VehicleService:GetVehicleDef(vehicleId: string): Config.VehicleDef?
	return vehicleById[vehicleId]
end

function VehicleService:GetRunnerBonus(player: Player): number
	local data = getData(player)
	if not data then
		return 0
	end
	local equipped = data.vehicles.equipped
	if type(equipped) ~= "string" or not owns(data, equipped) then
		return 0
	end
	local def = vehicleById[equipped]
	if not def or not isFiniteNumber(def.runnerBonus) then
		return 0
	end
	return math.max(0, def.runnerBonus)
end

function VehicleService:Dismount(player: Player)
	dismount(player)
end

function VehicleService:IsRiding(player: Player): boolean
	return rides[player] ~= nil
end

-- Give a vehicle that has been paid for. Newly bought vehicles are equipped
-- straight away. [PROPOSAL] (newer vehicles are always better)
function VehicleService:Grant(player: Player, vehicleId: string): boolean
	local def = vehicleById[vehicleId]
	local data = getData(player)
	if not def or not data then
		return false
	end
	if type(data.vehicles.owned) ~= "table" then
		data.vehicles.owned = {}
	end
	data.vehicles.owned[vehicleId] = true
	data.vehicles.equipped = vehicleId
	markDirty(player)
	-- Riding something else? Swap to the new vehicle.
	local ride = rides[player]
	if ride and ride.vehicleId ~= vehicleId and getMode(player) == "Base" then
		mount(player, def)
	end
	if player.Parent then
		Net.Fire("Vehicle", player, "Granted", { vehicleId = vehicleId })
		Net.Notify(player, ("🚙 You got the %s! Press Ride to drive it."):format(def.name), "success")
	end
	return true
end

-------------------------------------------------------------------------------
-- Requests
-------------------------------------------------------------------------------

local function buyVehicle(player: Player, vehicleId: any, route: any): Net.Result
	if type(vehicleId) ~= "string" then
		return Net.Fail("Bad vehicle")
	end
	local def = vehicleById[vehicleId]
	if not def then
		return Net.Fail("Unknown vehicle")
	end
	if route ~= "cash" and route ~= "robux" then
		return Net.Fail("Bad payment option")
	end
	local data = getData(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	if getMode(player) ~= "Base" then
		return Net.Fail("Go back to your base first")
	end
	if owns(data, vehicleId) then
		return Net.Fail("You already own this vehicle")
	end
	local highest = tonumber(data.base.highestArea) or 1
	if highest < def.area then
		return Net.Fail(("Unlocks in Area %d"):format(def.area))
	end

	if route == "cash" then
		local price = Economy.VehicleCoinPrice(vehicleId, highest)
		if not isFiniteNumber(price) or price <= 0 then
			return Net.Fail("This vehicle cannot be bought with cash")
		end
		local ds = dataService()
		if not ds or not ds:SpendCash(player, price) then
			return Net.Fail(("Not enough cash (%s)"):format(Format.Money(price)))
		end
		if not VehicleService:Grant(player, vehicleId) then
			ds:AddCash(player, price, { source = "refund" })
			return Net.Fail("Something went wrong, your cash was refunded")
		end
		return Net.Ok({ vehicleId = vehicleId, price = price })
	end

	local productId = Config.ProductIds["vehicle_" .. vehicleId] or 0
	if productId == 0 then
		return Net.Fail("Robux purchases are not set up yet")
	end
	local ok, err = pcall(function()
		MarketplaceService:PromptProductPurchase(player, productId)
	end)
	if not ok then
		warn("[VehicleService] PromptProductPurchase failed: " .. tostring(err))
		return Net.Fail("Could not open the Robux purchase window")
	end
	return Net.Ok({ prompted = true })
end

local function equipVehicle(player: Player, vehicleId: any): Net.Result
	if vehicleId ~= nil and type(vehicleId) ~= "string" then
		return Net.Fail("Bad vehicle")
	end
	local data = getData(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	if getMode(player) ~= "Base" then
		return Net.Fail("Go back to your base first")
	end
	if vehicleId == nil then
		data.vehicles.equipped = nil
		markDirty(player)
		dismount(player)
		return Net.Ok({ equipped = nil })
	end
	local def = vehicleById[vehicleId]
	if not def then
		return Net.Fail("Unknown vehicle")
	end
	if not owns(data, vehicleId) then
		return Net.Fail("You do not own this vehicle")
	end
	data.vehicles.equipped = vehicleId
	markDirty(player)
	local ride = rides[player]
	if ride and ride.vehicleId ~= vehicleId then
		mount(player, def)
	end
	return Net.Ok({ equipped = vehicleId })
end

local function toggleRide(player: Player): Net.Result
	local data = getData(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	if rides[player] then
		dismount(player)
		return Net.Ok({ riding = false })
	end
	if getMode(player) ~= "Base" then
		return Net.Fail("You can only ride vehicles in your base")
	end
	local equipped = data.vehicles.equipped
	if type(equipped) ~= "string" or not owns(data, equipped) then
		return Net.Fail("Equip a vehicle in the Shop first")
	end
	local def = vehicleById[equipped]
	if not def then
		return Net.Fail("Equip a vehicle in the Shop first")
	end
	local ok, reason = mount(player, def)
	if not ok then
		return Net.Fail(reason or "Could not get on the vehicle")
	end
	return Net.Ok({ riding = true })
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------

function VehicleService:Init(registry: { [string]: any })
	Services = registry
	Net.Handle("BuyVehicle", buyVehicle)
	Net.Handle("EquipVehicle", equipVehicle)
	Net.Handle("ToggleRide", toggleRide)
end

function VehicleService:Start()
	local modeService = Services.ModeService
	if modeService and modeService.ModeChanged then
		modeService.ModeChanged:Connect(function(player: Player, newMode: string)
			if newMode ~= "Base" then
				dismount(player)
			end
		end)
	end

	local function onPlayer(player: Player)
		if playerConnections[player] then
			return
		end
		player:SetAttribute("Riding", false)
		playerConnections[player] = {
			player.CharacterRemoving:Connect(function()
				dismount(player)
			end),
		}
	end
	Players.PlayerAdded:Connect(onPlayer)
	for _, player in Players:GetPlayers() do
		onPlayer(player)
	end
	Players.PlayerRemoving:Connect(function(player)
		dismount(player)
		local list = playerConnections[player]
		playerConnections[player] = nil
		if list then
			for _, connection in list do
				connection:Disconnect()
			end
		end
	end)
end

return VehicleService
