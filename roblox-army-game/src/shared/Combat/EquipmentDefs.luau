--!strict
--[[
	EquipmentDefs  (STUB written by the lead — GEAR replaces the bodies, keeps the types/signatures)
	See docs/ARCHITECTURE.md §4.5.
]]

local Shared = script.Parent.Parent
local Rng = require(Shared.Rng)
local Types = require(Shared.Types)

export type CombatStats = {
	maxHp: number,
	damage: number,
	fireRate: number,
	projectiles: number,
	spreadDeg: number,
	projectileSpeed: number,
	range: number,
	critChance: number,
	critMult: number,
	damageReduction: number,
	moveSpeed: number,
	perks: { [string]: number },
}

export type ItemDef = {
	id: string,
	name: string,
	slot: string, -- "weapon" | "armour"
	icon: string,
	description: string,
}

local EquipmentDefs = {}

EquipmentDefs.Items = {
	w_pistol = { id = "w_pistol", name = "Service Pistol", slot = "weapon", icon = "🔫", description = "" },
	a_vest = { id = "a_vest", name = "Field Vest", slot = "armour", icon = "🦺", description = "" },
} :: { [string]: ItemDef }

EquipmentDefs.PERKS = {} :: { [string]: { name: string, description: string } }

function EquipmentDefs.LoadoutStats(_data: Types.PlayerData): CombatStats
	return {
		maxHp = 100,
		damage = 10,
		fireRate = 2,
		projectiles = 1,
		spreadDeg = 0,
		projectileSpeed = 70,
		range = 60,
		critChance = 0.05,
		critMult = 2,
		damageReduction = 0,
		moveSpeed = 20,
		perks = {},
	}
end

function EquipmentDefs.RollChest(_chestTier: string, _rng: Rng.Rng): Types.Item
	return { id = "w_pistol", rarity = 1, level = 1 }
end

return EquipmentDefs
