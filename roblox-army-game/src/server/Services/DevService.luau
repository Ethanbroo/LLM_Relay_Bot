--!strict
--[[
	DevService (Studio only)
	Test tools so the owner can try every system quickly in Roblox Studio
	without grinding: give cash, beat the current barrier, open a chest,
	start a boost, reset progress.

	SAFETY: does nothing at all in a live (published, non-Studio) server —
	the request handlers are only registered when RunService:IsStudio().

	Actions (client DevController):
		DevGiveCash(amount)      -- amount from a fixed list
		DevBeatBarrier()         -- marks the barrier on your current area's exit gate as defeated
		DevChest(tier)           -- "wood" | "silver" | "gold"
		DevBoost(boostId)        -- applies a Config.Boosts entry for free
		DevReset()               -- wipes your progress back to a new player
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Net = require(Shared.Net)
local Types = require(Shared.Types)

local DevService = {}

local Services: { [string]: any } = {}

local CASH_AMOUNTS = { [1000] = true, [100000] = true, [10000000] = true }
local CHEST_TIERS = { wood = true, silver = true, gold = true }

function DevService:Init(registry: { [string]: any })
	Services = registry
	if not RunService:IsStudio() then
		return
	end

	Net.Handle("DevGiveCash", function(player, amount)
		if type(amount) ~= "number" or not CASH_AMOUNTS[amount] then
			return Net.Fail("Bad amount")
		end
		Services.DataService:AddCash(player, amount, { source = "dev" })
		return Net.Ok()
	end)

	Net.Handle("DevBeatBarrier", function(player)
		local data = Services.DataService:Get(player)
		local base = Services.BaseService
		if not data or not base or not base.MarkBarrierDefeated then
			return Net.Fail("Base service not ready")
		end
		local area = data.base.highestArea
		if area >= #Config.Areas then
			return Net.Fail("Already at the last area")
		end
		base:MarkBarrierDefeated(player, area)
		return Net.Ok({ area = area })
	end)

	Net.Handle("DevChest", function(player, tier)
		local inventory = Services.InventoryService
		if type(tier) ~= "string" or not CHEST_TIERS[tier] then
			return Net.Fail("Bad chest tier")
		end
		if not inventory or not inventory.AwardChest then
			return Net.Fail("Inventory service not ready")
		end
		if player:GetAttribute("CombatActive") then
			return Net.Fail("Not during a fight")
		end
		local uid, item = inventory:AwardChest(player, tier)
		return Net.Ok({ uid = uid, item = item, tier = tier })
	end)

	Net.Handle("DevBoost", function(player, boostId)
		local boosts = Services.BoostService
		if type(boostId) ~= "string" then
			return Net.Fail("Bad boost")
		end
		local known = false
		for _, def in Config.Boosts do
			if def.id == boostId then
				known = true
			end
		end
		if not known then
			return Net.Fail("Unknown boost")
		end
		if not boosts or not boosts.ApplyBoost then
			return Net.Fail("Boost service not ready")
		end
		boosts:ApplyBoost(player, boostId)
		return Net.Ok()
	end)

	Net.Handle("DevReset", function(player)
		local data = Services.DataService:Get(player)
		if not data then
			return Net.Fail("Data not loaded")
		end
		if Services.ModeService:GetMode(player) ~= "Base" then
			return Net.Fail("Go back to the base first")
		end
		local fresh = Types.DefaultData() :: any
		local live = data :: any
		for key in live do
			live[key] = nil
		end
		for key, value in fresh do
			live[key] = value
		end
		Services.DataService:MarkDirty(player)
		return Net.Ok()
	end)
end

return DevService
