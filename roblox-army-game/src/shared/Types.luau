--!strict
--[[
	Types
	Saved player data schema + defaults. All dictionary keys are strings so the
	table can be stored in a DataStore and sent over remotes unchanged.
]]

export type SectionState = {
	level: number, -- upgrade level, starts at 1
	stock: number, -- sales left before a manual restock is needed
	employee: boolean, -- true = auto-restocks
}

export type Item = {
	id: string, -- EquipmentDefs item id
	rarity: number, -- 1 Common .. 5 Legendary
	level: number, -- upgrade level, starts at 1
	perks: { string }?, -- perk ids rolled when the item dropped (Rare+), see EquipmentDefs
}

export type PlayerData = {
	version: number,
	cash: number,
	lifetimeCash: number,

	runner: {
		level: number, -- run counter shown as "Level N" (image 2)
		runs: number,
		bestPayout: number,
		-- Permanent upgrade levels bought before runs. [REQUIREMENT]
		upgrades: { troops: number, damage: number, capacity: number },
	},

	base: {
		highestArea: number, -- highest unlocked area (1..#Config.Areas)
		sections: { [string]: SectionState }, -- key = slot id ("a1s2"); present = unlocked
		barriers: { [string]: boolean }, -- key = tostring(area): barrier squad on that area's exit gate defeated
		arenaUnlocked: boolean,
	},

	combat: {
		highestLevel: number, -- highest Combat Arena level cleared
		wins: number,
		games: number,
	},

	inventory: { [string]: Item }, -- key = item uid
	equipped: { weapon: string?, armour: string? },
	nextUid: number,

	vehicles: { owned: { [string]: boolean }, equipped: string? },

	boosts: {
		multiplierSeconds: number, -- remaining play-time of the 2x cash boost
	},

	employeeTokens: number, -- Robux employees bought but not yet assigned
	receipts: { [string]: boolean }, -- processed purchase ids (idempotency)
	receiptOrder: { string },

	stats: {
		playSeconds: number,
		combatKills: number,
		sectionsUnlocked: number,
	},
}

local Types = {}

Types.DATA_VERSION = 1

-- Starter loadout (ids must exist in EquipmentDefs).
Types.STARTER_WEAPON_ID = "w_pistol"
Types.STARTER_ARMOUR_ID = "a_vest"

function Types.DefaultData(): PlayerData
	return {
		version = Types.DATA_VERSION,
		cash = 0,
		lifetimeCash = 0,
		runner = {
			level = 1,
			runs = 0,
			bestPayout = 0,
			upgrades = { troops = 0, damage = 0, capacity = 0 },
		},
		base = {
			highestArea = 1,
			sections = {},
			barriers = {},
			arenaUnlocked = false,
		},
		combat = { highestLevel = 0, wins = 0, games = 0 },
		inventory = {
			["1"] = { id = Types.STARTER_WEAPON_ID, rarity = 1, level = 1 },
			["2"] = { id = Types.STARTER_ARMOUR_ID, rarity = 1, level = 1 },
		},
		equipped = { weapon = "1", armour = "2" },
		nextUid = 3,
		vehicles = { owned = {}, equipped = nil },
		boosts = { multiplierSeconds = 0 },
		employeeTokens = 0,
		receipts = {},
		receiptOrder = {},
		stats = { playSeconds = 0, combatKills = 0, sectionsUnlocked = 0 },
	}
end

local function deepCopy(value: any): any
	if type(value) ~= "table" then
		return value
	end
	local out = {}
	for k, v in value do
		out[k] = deepCopy(v)
	end
	return out
end
Types.DeepCopy = deepCopy

-- Fill any keys missing from `data` with defaults (used after loading old saves).
function Types.Reconcile(data: any): PlayerData
	local function fill(target: any, template: any)
		for k, v in template do
			if target[k] == nil or type(target[k]) ~= type(v) then
				-- Missing, or corrupted into the wrong type: use the default.
				target[k] = deepCopy(v)
			elseif type(v) == "table" and next(v) ~= nil then
				-- Only recurse into "struct" tables, not open maps such as inventory.
				local isStruct = true
				for key in v do
					if type(key) ~= "string" or tonumber(key) ~= nil then
						isStruct = false
						break
					end
				end
				if isStruct then
					fill(target[k], v)
				end
			end
		end
	end
	if type(data) ~= "table" then
		return Types.DefaultData()
	end
	local template = Types.DefaultData()
	-- Never re-add the starter items to an existing inventory.
	template.inventory = {}
	template.equipped = {}
	fill(data, template)
	return data :: PlayerData
end

return Types
