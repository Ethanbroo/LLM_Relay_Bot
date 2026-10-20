--!strict
--[[
	InventoryService (server)
	Owns the player's combat equipment: inventory, equipped items and chest
	rewards. All item maths lives in Shared/Combat/EquipmentDefs.

	Requests (client -> server, via Net.Request):
		"EquipItem"(uid)    put an item you own in its slot (weapon / armour)
		"UpgradeItem"(uid)  pay Economy.EquipmentUpgradePrice(rarity, level), level + 1
		"ScrapItem"(uid)    destroy an item that is NOT equipped for EquipmentDefs.ScrapValue cash
		All three are refused while the player attribute CombatActive is true
		(the loadout is locked mid-fight; the lobby before round 1 is fine).

	Push channel "Inventory" (server -> client):
		"Granted",   { uid: string, item: Item, tier: string }   a chest item was added
		"Converted", { item: Item, value: number, tier: string } inventory was full: item paid out as cash

	Server API (other services, from Start or later):
		InventoryService:GetLoadoutStats(player): CombatStats
		InventoryService:AwardChest(player, chestTier): (string?, Item?)
			chestTier = "wood" | "silver" | "gold". Returns the new uid + item.
			[PROPOSAL] If the inventory is full (EquipmentDefs.MAX_INVENTORY) the
			item is converted to its scrap value in cash; then the uid is nil but
			the item is still returned so the chest animation can show it.
			Returns (nil, nil) if the player's data is not loaded or the tier is bad.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Economy = require(Shared.Economy)
local EquipmentDefs = require(Shared.Combat.EquipmentDefs)
local Net = require(Shared.Net)
local Rng = require(Shared.Rng)
local Types = require(Shared.Types)

type Item = Types.Item
type PlayerData = Types.PlayerData
type CombatStats = EquipmentDefs.CombatStats

local InventoryService = {}
InventoryService.Priority = 40

local registry: { [string]: any } = {}
local rollCounter = 0

local VALID_TIERS = { wood = true, silver = true, gold = true }
local MAX_UID_LENGTH = 16

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function getData(player: Player): PlayerData?
	local dataService = registry.DataService
	if not dataService then
		return nil
	end
	return dataService:Get(player)
end

local function markDirty(player: Player)
	local dataService = registry.DataService
	if dataService then
		dataService:MarkDirty(player)
	end
end

local function countItems(data: PlayerData): number
	local n = 0
	for _ in data.inventory do
		n += 1
	end
	return n
end

-- Shared checks for the three requests. Returns the data + item, or an error text.
local function validate(player: Player, uid: any): (PlayerData?, Item?, string?)
	if player:GetAttribute("CombatActive") == true then
		return nil, nil, "You can't change gear during a fight!"
	end
	if type(uid) ~= "string" or #uid == 0 or #uid > MAX_UID_LENGTH then
		return nil, nil, "Bad item"
	end
	local data = getData(player)
	if not data then
		return nil, nil, "Your data is still loading"
	end
	local item = data.inventory[uid]
	if type(item) ~= "table" or not EquipmentDefs.GetDef(item) then
		return nil, nil, "You don't have that item"
	end
	return data, item, nil
end

-- A brand-new uid that is not in use (normally just tostring(nextUid)).
local function takeUid(data: PlayerData): string
	local nextUid = math.max(1, math.floor(tonumber(data.nextUid) or 1))
	while data.inventory[tostring(nextUid)] ~= nil do
		nextUid += 1
	end
	data.nextUid = nextUid + 1
	return tostring(nextUid)
end

-- The best owned item of a slot (by power), or nil.
local function bestOfSlot(data: PlayerData, slot: string): string?
	local bestUid: string? = nil
	local bestPower = -1
	for uid, item in data.inventory do
		local d = EquipmentDefs.GetDef(item)
		if d and d.slot == slot then
			local power = EquipmentDefs.ItemPower(item)
			if power > bestPower then
				bestUid, bestPower = uid, power
			end
		end
	end
	return bestUid
end

-- Repairs a save after loading: nextUid above every numeric uid, equipped
-- slots pointing at real items of the right slot, and a starter item if a
-- slot has nothing at all (so nobody is ever stuck without gear).
local function repairData(player: Player, data: PlayerData)
	if type(data.inventory) ~= "table" then
		data.inventory = {}
	end
	if type(data.equipped) ~= "table" then
		data.equipped = {}
	end
	local highest = 0
	for uid, item in data.inventory do
		local n = tonumber(uid)
		if n and n > highest then
			highest = math.floor(n)
		end
		-- clean numbers in place (keeps unknown ids untouched for safety)
		local clean = EquipmentDefs.SanitizeItem(item)
		if clean then
			data.inventory[uid] = clean
		end
	end
	if type(data.nextUid) ~= "number" or data.nextUid <= highest then
		data.nextUid = highest + 1
	end

	for _, entry in
		{
			{ slot = "weapon", starter = Types.STARTER_WEAPON_ID },
			{ slot = "armour", starter = Types.STARTER_ARMOUR_ID },
		}
	do
		local slot = entry.slot
		local uid = (data.equipped :: any)[slot]
		local item = if type(uid) == "string" then data.inventory[uid] else nil
		local d = EquipmentDefs.GetDef(item)
		if not d or d.slot ~= slot then
			local replacement = bestOfSlot(data, slot)
			if replacement then
				(data.equipped :: any)[slot] = replacement
			else
				local newUid = takeUid(data)
				data.inventory[newUid] = { id = entry.starter, rarity = 1, level = 1 }
				(data.equipped :: any)[slot] = newUid
			end
		end
	end
	markDirty(player)
end

-------------------------------------------------------------------------------
-- Requests
-------------------------------------------------------------------------------
local function onEquip(player: Player, uid: any)
	local data, item, err = validate(player, uid)
	if not data or not item then
		return Net.Fail(err or "Bad item")
	end
	local d = EquipmentDefs.GetDef(item)
	if not d then
		return Net.Fail("Unknown item")
	end
	local key = uid :: string
	if d.slot == "weapon" then
		data.equipped.weapon = key
	else
		data.equipped.armour = key
	end
	markDirty(player)
	return Net.Ok({ slot = d.slot, uid = key })
end

local function onUpgrade(player: Player, uid: any)
	local data, item, err = validate(player, uid)
	if not data or not item then
		return Net.Fail(err or "Bad item")
	end
	local clean = EquipmentDefs.SanitizeItem(item)
	if not clean then
		return Net.Fail("Unknown item")
	end
	local price = Economy.EquipmentUpgradePrice(clean.rarity, clean.level)
	if price == nil then
		return Net.Fail("Max level")
	end
	local dataService = registry.DataService
	if not dataService or not dataService:SpendCash(player, price) then
		return Net.Fail("Not enough cash")
	end
	item.rarity = clean.rarity
	item.level = clean.level + 1
	markDirty(player)
	return Net.Ok({ uid = uid, level = item.level, price = price })
end

local function onScrap(player: Player, uid: any)
	local data, item, err = validate(player, uid)
	if not data or not item then
		return Net.Fail(err or "Bad item")
	end
	if data.equipped.weapon == uid or data.equipped.armour == uid then
		return Net.Fail("Equip something else first")
	end
	local value = EquipmentDefs.ScrapValue(item)
	data.inventory[uid :: string] = nil
	local paid = 0
	local dataService = registry.DataService
	if dataService then
		-- A refund, not gameplay income: never boosted by 2x.
		paid = dataService:AddCash(player, value, { boostable = false, source = "scrap" })
	end
	markDirty(player)
	return Net.Ok({ uid = uid, value = paid })
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------
function InventoryService:Init(reg: { [string]: any })
	registry = reg
	Net.Handle("EquipItem", onEquip)
	Net.Handle("UpgradeItem", onUpgrade)
	Net.Handle("ScrapItem", onScrap)
end

function InventoryService:Start()
	local dataService = registry.DataService
	if not dataService then
		warn("[InventoryService] DataService missing")
		return
	end
	dataService.PlayerLoaded:Connect(function(player: Player, data: PlayerData)
		local ok, err = pcall(function()
			repairData(player, data)
		end)
		if not ok then
			warn("[InventoryService] repair failed for " .. player.Name .. ": " .. tostring(err))
		end
	end)
	-- Players that finished loading before we connected.
	for _, player in dataService:GetLoadedPlayers() do
		local data = dataService:Get(player)
		if data then
			pcall(function()
				repairData(player, data)
			end)
		end
	end
end

-------------------------------------------------------------------------------
-- Server API
-------------------------------------------------------------------------------
function InventoryService:GetLoadoutStats(player: Player): CombatStats
	local data = getData(player)
	local ok, result = pcall(function(): CombatStats
		return EquipmentDefs.LoadoutStats(data :: any)
	end)
	if ok and type(result) == "table" then
		return result
	end
	warn("[InventoryService] LoadoutStats failed: " .. tostring(result))
	return EquipmentDefs.LoadoutStats({} :: any)
end

function InventoryService:AwardChest(player: Player, chestTier: string): (string?, Item?)
	if type(chestTier) ~= "string" or not VALID_TIERS[chestTier] then
		warn("[InventoryService] bad chest tier " .. tostring(chestTier))
		return nil, nil
	end
	local data = getData(player)
	if not data then
		return nil, nil
	end
	-- Fresh, unpredictable seed per roll: clock + time + UserId + a counter.
	rollCounter += 1
	local seed = math.floor(os.clock() * 1e6) + os.time() * 7 + player.UserId * 7919 + rollCounter * 104729
	local item = EquipmentDefs.RollChest(chestTier, Rng.new(seed))

	if countItems(data) >= EquipmentDefs.MAX_INVENTORY then
		-- [PROPOSAL] full inventory: the drop is paid out as its scrap value.
		local value = EquipmentDefs.ScrapValue(item)
		local paid = 0
		local dataService = registry.DataService
		if dataService then
			paid = dataService:AddCash(player, value, { boostable = false, source = "scrap" })
		end
		Net.Fire("Inventory", player, "Converted", { item = item, value = paid, tier = chestTier })
		return nil, item
	end

	local uid = takeUid(data)
	data.inventory[uid] = item
	markDirty(player)
	Net.Fire("Inventory", player, "Granted", { uid = uid, item = item, tier = chestTier })
	return uid, item
end

return InventoryService
