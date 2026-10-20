--!strict
--[[
	EquipmentDefs
	Every piece of combat equipment (weapons + armour), its rarity scaling,
	perks, chest odds and the "loadout -> combat stats" maths.

	This is the LASTING combat progression of the brief: armour and weapons
	come from chests at the end of every combat game, have rarities (rarer =
	better stats + more perks, and less likely to drop), and are upgraded with
	coins (Economy.EquipmentUpgradePrice). In-run abilities picked between
	waves are a different system (CombatDefs.ABILITIES) and do NOT last.

	Pure module: no services, no Instances, so it runs on the server, the
	client and in the Lune tests (tests/equipment.spec.luau).

	Public API:
		EquipmentDefs.Items: { [string]: ItemDef }     every item, by id ("w_pistol", "a_vest", ...)
		EquipmentDefs.WEAPON_IDS / ARMOUR_IDS: { string } ids per slot in display order
		EquipmentDefs.PERKS: { [string]: { name, description, icon } }  all perk ids of ARCHITECTURE §4.5
		EquipmentDefs.CHEST_TIERS: { [string]: ChestTierDef }  "wood" | "silver" | "gold" (weights per rarity)
		EquipmentDefs.MAX_INVENTORY: number            inventory cap (items)
		EquipmentDefs.LEVEL_STEP: number               +stats per upgrade level (0.06 = +6%)

		EquipmentDefs.LoadoutStats(data: PlayerData): CombatStats
			base player + equipped weapon + equipped armour + perk stacks.
			A missing / broken equipped item is treated as "nothing equipped"; never errors.
		EquipmentDefs.RollChest(chestTier: string, rng: Rng): Item
		EquipmentDefs.ChestOdds(chestTier: string): { number }      rarity 1..5 -> probability (sums to 1)

		EquipmentDefs.GetDef(item): ItemDef?             def of an item table (nil if unknown id)
		EquipmentDefs.SanitizeItem(raw): Item?           cleaned copy of an item table, nil if unusable
		EquipmentDefs.StatMult(rarity, level): number    multiplier for the scaling stats
		EquipmentDefs.ItemStats(item): StatBlock         what ONE item adds to the loadout
		EquipmentDefs.ItemPerks(item): { PerkLine }      built-in + rolled perks with names/descriptions
		EquipmentDefs.ItemStatsText(item): { string }    readable stat lines ("Damage 10.6", ...)
		EquipmentDefs.ItemPower(item): number            single number for sorting / comparing in a slot
		EquipmentDefs.UpgradeSpent(item): number         cash spent upgrading it from level 1
		EquipmentDefs.ScrapValue(item): number           cash paid for scrapping it
		EquipmentDefs.DisplayStats(stats): CombatStats   stats with perk effects applied (UI ONLY;
			COMBAT applies perks itself in CombatDefs.DeriveStats, so never feed this into combat)

	Rules (all numbers below are [PROPOSAL] unless the brief says otherwise):
	* Rarity (Config.Rarities): statMult, maxLevel, perkSlots (Rare 1, Epic 2, Legendary 3).
	* A Legendary always has the item's signature perk as one of its 3 perks.
	* Each upgrade level adds +6% to the scaling stats (weapon damage, armour HP,
	  and a gentler curve for damage reduction).
	* Fire rate, bullets, spread, range and speed are the item's "personality"
	  and do not scale; perks change them instead.
]]

local Shared = script.Parent.Parent
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Rng = require(Shared.Rng)
local Types = require(Shared.Types)

type Item = Types.Item
type PlayerData = Types.PlayerData

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

-- What one item contributes. Weapons fill the weapon fields; armour fills
-- maxHp / damageReduction / moveSpeed (as BONUSES on top of the base player).
export type StatBlock = {
	damage: number?,
	fireRate: number?,
	projectiles: number?,
	spreadDeg: number?,
	projectileSpeed: number?,
	range: number?,
	critChance: number?, -- weapon: bonus crit chance (0.1 = +10%)
	critMult: number?, -- weapon: bonus crit multiplier (0.5 = +0.5x)
	maxHp: number?, -- armour: bonus HP
	damageReduction: number?, -- armour: 0..0.6
	moveSpeed: number?, -- armour: bonus (or penalty) WalkSpeed
}

export type ItemDef = {
	id: string,
	name: string,
	slot: string, -- "weapon" | "armour"
	icon: string,
	description: string,
	order: number, -- display order inside its slot
	base: StatBlock, -- stats of a Common, level 1 copy
	builtin: { string }, -- perks every copy has (any rarity), e.g. the Sniper's pierce
	perkPool: { string }, -- perks a Rare/Epic/Legendary copy can roll
	signature: string, -- perk id every Legendary copy gets
	signatureName: string, -- flavour name of the Legendary signature
}

export type PerkDef = { name: string, description: string, icon: string }

export type PerkLine = {
	id: string,
	name: string,
	description: string,
	icon: string,
	source: string, -- "builtin" | "rolled" | "signature"
}

export type ChestTierDef = {
	name: string,
	weights: { number }, -- weight per rarity, Common .. Legendary
}

local EquipmentDefs = {}

-------------------------------------------------------------------------------
-- Tunables  [PROPOSAL]
-------------------------------------------------------------------------------
EquipmentDefs.MAX_INVENTORY = 60 -- [PROPOSAL] items you can hold; extra drops become cash
EquipmentDefs.LEVEL_STEP = 0.06 -- [PROPOSAL] +6% scaling stats per upgrade level
EquipmentDefs.REDUCTION_LEVEL_STEP = 0.02 -- [PROPOSAL] damage reduction grows slower (+2% of itself per level)
EquipmentDefs.MAX_REDUCTION = 0.6 -- [ARCHITECTURE §4.5] damage reduction is capped at 60%

-- Base player (no equipment at all).
EquipmentDefs.BASE_PLAYER = {
	maxHp = 100,
	moveSpeed = 20,
	critChance = 0.05,
	critMult = 2,
}

-- Scrap: [PROPOSAL] a share of the item's rarity value (x the area-1 best-case
-- run, Economy.RunBenchmark(1)) + 30% of the cash spent upgrading it.
-- Scrap cash is NOT boostable (it is a refund, not gameplay income).
EquipmentDefs.SCRAP_RARITY_SHARE = { 0.1, 0.25, 0.6, 1.5, 4 } -- [PROPOSAL] Common .. Legendary
EquipmentDefs.SCRAP_UPGRADE_REFUND = 0.3 -- [PROPOSAL]

-------------------------------------------------------------------------------
-- Perks (ARCHITECTURE §4.5 — COMBAT implements the effects)
-------------------------------------------------------------------------------
EquipmentDefs.PERKS = {
	multishot = { name = "Multishot", icon = "🔱", description = "+1 bullet every shot" },
	pierce = { name = "Pierce", icon = "🗡️", description = "Bullets pass through 1 more enemy" },
	ricochet = { name = "Ricochet", icon = "🔁", description = "Bullets bounce to 1 more enemy" },
	crit = { name = "Critical Eye", icon = "🎯", description = "+10% critical hit chance" },
	rapid = { name = "Rapid Fire", icon = "⚡", description = "+15% fire rate" },
	explosive = { name = "Explosive", icon = "💣", description = "Bullets splash 30% damage in 6 studs" },
	regen = { name = "Regeneration", icon = "💚", description = "Heal 1% of max HP every second" },
	shield = { name = "Shield", icon = "🛡️", description = "Start each wave with a shield that blocks 1 hit" },
	dodge = { name = "Dodge", icon = "💨", description = "10% chance to ignore a hit (max 40%)" },
	thorns = { name = "Thorns", icon = "🌵", description = "Melee attackers take 50% of the damage back" },
	swift = { name = "Swift", icon = "👟", description = "+8% move speed" },
	vampire = { name = "Vampire", icon = "🧛", description = "Heal 2% of max HP for every kill" },
} :: { [string]: PerkDef }

-------------------------------------------------------------------------------
-- Chest tiers  [PROPOSAL: weights]
-------------------------------------------------------------------------------
-- Within every tier a rarer item is always LESS likely than the next-lower
-- rarity ([REQUIREMENT] "higher-rarity equipment has a lower chance of
-- appearing"). Better chests shift the odds towards rare items.
-- ASSUMPTION: the brief for the gold chest suggested ~25/35/25/11/4, but that
-- would make Uncommon more likely than Common; it is reshaped to keep the
-- "rarer = less likely" rule while keeping Legendary at 4%.
EquipmentDefs.CHEST_TIERS = {
	wood = { name = "Wooden Chest", weights = { 62, 27, 8, 2.5, 0.5 } }, -- [PROPOSAL] defeat
	silver = { name = "Silver Chest", weights = { 45, 32, 15, 6, 2 } }, -- [PROPOSAL] arena win
	gold = { name = "Golden Chest", weights = { 36, 30, 20, 10, 4 } }, -- [PROPOSAL] barrier win / every 5th level
} :: { [string]: ChestTierDef }

-------------------------------------------------------------------------------
-- Items  [PROPOSAL: every number]
-------------------------------------------------------------------------------
local function def(spec: ItemDef): ItemDef
	return spec
end

EquipmentDefs.Items = {
	---------------------------------------------------------------- weapons
	w_pistol = def({
		id = "w_pistol",
		name = "Service Pistol",
		slot = "weapon",
		icon = "🔫",
		description = "Reliable starter sidearm. Steady, accurate shots.",
		order = 1,
		base = { damage = 10, fireRate = 2, projectiles = 1, spreadDeg = 0, projectileSpeed = 70, range = 60 },
		builtin = {},
		perkPool = { "crit", "rapid", "ricochet", "vampire", "pierce" },
		signature = "multishot",
		signatureName = "Twin Barrels",
	}),
	w_smg = def({
		id = "w_smg",
		name = "SMG",
		slot = "weapon",
		icon = "⚡",
		description = "Sprays lots of small bullets. Great against swarms.",
		order = 2,
		base = { damage = 5.5, fireRate = 5, projectiles = 1, spreadDeg = 8, projectileSpeed = 75, range = 48 },
		builtin = {},
		perkPool = { "crit", "multishot", "pierce", "vampire", "swift" },
		signature = "rapid",
		signatureName = "Hair Trigger",
	}),
	w_shotgun = def({
		id = "w_shotgun",
		name = "Shotgun",
		slot = "weapon",
		icon = "💥",
		description = "Fires 3 pellets in a wide spread. Best up close.",
		order = 3,
		base = { damage = 7, fireRate = 1.4, projectiles = 3, spreadDeg = 30, projectileSpeed = 60, range = 34 },
		builtin = {},
		perkPool = { "crit", "rapid", "pierce", "vampire", "ricochet" },
		signature = "multishot",
		signatureName = "Buckshot Storm",
	}),
	w_rifle = def({
		id = "w_rifle",
		name = "Assault Rifle",
		slot = "weapon",
		icon = "🔥",
		description = "Fast, accurate and long-ranged. A great all-rounder.",
		order = 4,
		base = { damage = 9, fireRate = 3.2, projectiles = 1, spreadDeg = 3, projectileSpeed = 85, range = 66 },
		builtin = {},
		perkPool = { "crit", "rapid", "multishot", "pierce", "vampire" },
		signature = "ricochet",
		signatureName = "Bouncing Rounds",
	}),
	w_sniper = def({
		id = "w_sniper",
		name = "Sniper Rifle",
		slot = "weapon",
		icon = "🎯",
		description = "Huge damage, slow to fire. Every bullet pierces.",
		order = 5,
		base = {
			damage = 40,
			fireRate = 0.7,
			projectiles = 1,
			spreadDeg = 0,
			projectileSpeed = 140,
			range = 110,
			critChance = 0.1,
			critMult = 0.5,
		},
		builtin = { "pierce" },
		perkPool = { "rapid", "ricochet", "vampire", "explosive", "swift" },
		signature = "crit",
		signatureName = "Dead Eye",
	}),
	w_minigun = def({
		id = "w_minigun",
		name = "Minigun",
		slot = "weapon",
		icon = "🌀",
		description = "A wall of tiny bullets. Very fast, low damage, wild spread.",
		order = 6,
		base = { damage = 3.2, fireRate = 10, projectiles = 1, spreadDeg = 14, projectileSpeed = 80, range = 50 },
		builtin = {},
		perkPool = { "crit", "rapid", "ricochet", "vampire", "swift" },
		signature = "pierce",
		signatureName = "Armour Shredder",
	}),
	w_rocket = def({
		id = "w_rocket",
		name = "Rocket Launcher",
		slot = "weapon",
		icon = "🚀",
		description = "Slow rockets that explode and hit everyone nearby.",
		order = 7,
		base = { damage = 30, fireRate = 0.8, projectiles = 1, spreadDeg = 0, projectileSpeed = 50, range = 70 },
		builtin = { "explosive" },
		perkPool = { "crit", "rapid", "multishot", "vampire", "ricochet" },
		signature = "explosive",
		signatureName = "Big Boom",
	}),

	---------------------------------------------------------------- armour
	a_vest = def({
		id = "a_vest",
		name = "Field Vest",
		slot = "armour",
		icon = "🦺",
		description = "Standard issue vest. A little extra protection.",
		order = 1,
		base = { maxHp = 20, damageReduction = 0.04, moveSpeed = 0 },
		builtin = {},
		perkPool = { "regen", "swift", "thorns", "vampire", "dodge" },
		signature = "shield",
		signatureName = "Lucky Charm",
	}),
	a_kevlar = def({
		id = "a_kevlar",
		name = "Kevlar Vest",
		slot = "armour",
		icon = "🧥",
		description = "Tough fibres stop bullets. Solid all-round defence.",
		order = 2,
		base = { maxHp = 35, damageReduction = 0.1, moveSpeed = 0 },
		builtin = {},
		perkPool = { "shield", "regen", "swift", "vampire", "dodge" },
		signature = "thorns",
		signatureName = "Spiked Plates",
	}),
	a_ghillie = def({
		id = "a_ghillie",
		name = "Ghillie Suit",
		slot = "armour",
		icon = "🌿",
		description = "Camouflage makes enemies miss. Built-in dodge.",
		order = 3,
		base = { maxHp = 15, damageReduction = 0.03, moveSpeed = 1 },
		builtin = { "dodge" },
		perkPool = { "swift", "regen", "vampire", "thorns", "shield" },
		signature = "dodge",
		signatureName = "Invisible",
	}),
	a_riot = def({
		id = "a_riot",
		name = "Riot Armour",
		slot = "armour",
		icon = "🛡️",
		description = "Heavy plates block lots of damage, but you move slower.",
		order = 4,
		base = { maxHp = 40, damageReduction = 0.2, moveSpeed = -2 },
		builtin = {},
		perkPool = { "thorns", "regen", "vampire", "dodge" },
		signature = "shield",
		signatureName = "Riot Shield",
	}),
	a_medic = def({
		id = "a_medic",
		name = "Medic Rig",
		slot = "armour",
		icon = "⛑️",
		description = "Med kits on every strap. Slowly heals you.",
		order = 5,
		base = { maxHp = 25, damageReduction = 0.06, moveSpeed = 0 },
		builtin = { "regen" },
		perkPool = { "shield", "vampire", "swift", "thorns" },
		signature = "regen",
		signatureName = "Field Surgeon",
	}),
	a_exo = def({
		id = "a_exo",
		name = "Exo Suit",
		slot = "armour",
		icon = "🦾",
		description = "Powered frame: more speed AND more HP.",
		order = 6,
		base = { maxHp = 30, damageReduction = 0.08, moveSpeed = 3 },
		builtin = {},
		perkPool = { "dodge", "regen", "shield", "thorns", "vampire" },
		signature = "swift",
		signatureName = "Overdrive",
	}),
	a_jugg = def({
		id = "a_jugg",
		name = "Juggernaut",
		slot = "armour",
		icon = "🦏",
		description = "A walking tank. Huge HP, but very slow.",
		order = 7,
		base = { maxHp = 90, damageReduction = 0.15, moveSpeed = -4 },
		builtin = {},
		perkPool = { "regen", "shield", "vampire", "dodge" },
		signature = "thorns",
		signatureName = "Unstoppable",
	}),
} :: { [string]: ItemDef }

local function idsForSlot(slot: string): { string }
	local list = {}
	for id, d in EquipmentDefs.Items do
		if d.slot == slot then
			table.insert(list, id)
		end
	end
	table.sort(list, function(a, b)
		return EquipmentDefs.Items[a].order < EquipmentDefs.Items[b].order
	end)
	return list
end

EquipmentDefs.WEAPON_IDS = idsForSlot("weapon")
EquipmentDefs.ARMOUR_IDS = idsForSlot("armour")

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local RARITY_COUNT = #Config.Rarities

local function round2(n: number): number
	return math.floor(n * 100 + 0.5) / 100
end

local function finiteNumber(value: any): number?
	if type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge then
		return value
	end
	return nil
end

local function rarityOf(value: any): number
	local n = finiteNumber(value) or 1
	return math.clamp(math.floor(n), 1, RARITY_COUNT)
end

local function levelOf(value: any, rarity: number): number
	local n = finiteNumber(value) or 1
	return math.clamp(math.floor(n), 1, Config.Rarities[rarity].maxLevel)
end

function EquipmentDefs.GetDef(item: any): ItemDef?
	if type(item) ~= "table" or type(item.id) ~= "string" then
		return nil
	end
	return EquipmentDefs.Items[item.id]
end

-- Returns a clean copy (known id, whole rarity 1..5, level 1..max, only known
-- perk ids without duplicates) or nil when the item cannot be used at all.
function EquipmentDefs.SanitizeItem(raw: any): Item?
	local d = EquipmentDefs.GetDef(raw)
	if not d then
		return nil
	end
	local rarity = rarityOf(raw.rarity)
	local item: Item = { id = d.id, rarity = rarity, level = levelOf(raw.level, rarity) }
	if type(raw.perks) == "table" then
		local perks: { string } = {}
		local seen: { [string]: boolean } = {}
		for _, perkId in raw.perks do
			if type(perkId) == "string" and EquipmentDefs.PERKS[perkId] and not seen[perkId] then
				seen[perkId] = true
				table.insert(perks, perkId)
			end
		end
		if #perks > 0 then
			item.perks = perks
		end
	end
	return item
end

function EquipmentDefs.StatMult(rarity: number, level: number): number
	local r = rarityOf(rarity)
	local l = levelOf(level, r)
	return Config.Rarities[r].statMult * (1 + EquipmentDefs.LEVEL_STEP * (l - 1))
end

-- What one item adds to the loadout (see StatBlock). Unknown item -> {}.
function EquipmentDefs.ItemStats(item: any): StatBlock
	local d = EquipmentDefs.GetDef(item)
	if not d then
		return {}
	end
	local rarity = rarityOf(item.rarity)
	local level = levelOf(item.level, rarity)
	local mult = EquipmentDefs.StatMult(rarity, level)
	local b = d.base
	if d.slot == "weapon" then
		return {
			damage = round2((b.damage or 10) * mult),
			fireRate = b.fireRate or 2,
			projectiles = b.projectiles or 1,
			spreadDeg = b.spreadDeg or 0,
			projectileSpeed = b.projectileSpeed or 70,
			range = b.range or 60,
			critChance = b.critChance or 0,
			critMult = b.critMult or 0,
		}
	end
	-- Armour. Damage reduction uses a gentler curve (sqrt of the rarity
	-- multiplier, +2% of itself per level) so it does not hit the cap at once.
	local reductionMult = math.sqrt(Config.Rarities[rarity].statMult)
		* (1 + EquipmentDefs.REDUCTION_LEVEL_STEP * (level - 1))
	return {
		maxHp = round2((b.maxHp or 0) * mult),
		damageReduction = math.min(
			EquipmentDefs.MAX_REDUCTION,
			math.floor((b.damageReduction or 0) * reductionMult * 10000 + 0.5) / 10000
		),
		moveSpeed = b.moveSpeed or 0,
	}
end

-- Built-in perks first, then rolled ones (the signature is marked).
function EquipmentDefs.ItemPerks(item: any): { PerkLine }
	local d = EquipmentDefs.GetDef(item)
	local lines: { PerkLine } = {}
	if not d then
		return lines
	end
	local function add(perkId: string, source: string)
		local perk = EquipmentDefs.PERKS[perkId]
		if perk then
			table.insert(lines, {
				id = perkId,
				name = if source == "signature" then d.signatureName .. " (" .. perk.name .. ")" else perk.name,
				description = perk.description,
				icon = perk.icon,
				source = source,
			})
		end
	end
	for _, perkId in d.builtin do
		add(perkId, "builtin")
	end
	local clean = EquipmentDefs.SanitizeItem(item)
	if clean and clean.perks then
		for _, perkId in clean.perks do
			local source = if clean.rarity == RARITY_COUNT and perkId == d.signature then "signature" else "rolled"
			add(perkId, source)
		end
	end
	return lines
end

local function formatNumber(n: number): string
	if n == math.floor(n) then
		return tostring(n)
	end
	return string.format("%.1f", n)
end

local function percent(fraction: number): string
	local p = fraction * 100
	if math.abs(p - math.floor(p + 0.5)) < 0.05 then
		return string.format("%d%%", math.floor(p + 0.5))
	end
	return string.format("%.1f%%", p)
end

function EquipmentDefs.ItemStatsText(item: any): { string }
	local d = EquipmentDefs.GetDef(item)
	local lines: { string } = {}
	if not d then
		return lines
	end
	local s = EquipmentDefs.ItemStats(item)
	if d.slot == "weapon" then
		table.insert(lines, "💥 Damage " .. formatNumber(s.damage or 0))
		table.insert(lines, "⚡ Fire rate " .. string.format("%.1f", s.fireRate or 0) .. "/s")
		if (s.projectiles or 1) > 1 then
			table.insert(lines, "🔱 Bullets x" .. tostring(s.projectiles))
		end
		table.insert(lines, "📏 Range " .. formatNumber(s.range or 0))
		if (s.critChance or 0) > 0 then
			table.insert(lines, "🎯 Crit +" .. percent(s.critChance or 0))
		end
		if (s.critMult or 0) > 0 then
			table.insert(lines, "💢 Crit damage +" .. formatNumber(s.critMult or 0) .. "x")
		end
	else
		table.insert(lines, "❤️ HP +" .. formatNumber(s.maxHp or 0))
		table.insert(lines, "🛡️ Damage taken -" .. percent(s.damageReduction or 0))
		local speed = s.moveSpeed or 0
		if speed ~= 0 then
			table.insert(lines, "👟 Speed " .. (if speed > 0 then "+" else "") .. formatNumber(speed))
		end
	end
	return lines
end

-- One number per item for sorting and quick comparison INSIDE a slot.
-- Weapons: damage per second (x bullets), armour: effective extra HP.
-- Every perk (built-in or rolled) counts as +12%.  [PROPOSAL]
function EquipmentDefs.ItemPower(item: any): number
	local d = EquipmentDefs.GetDef(item)
	if not d then
		return 0
	end
	local s = EquipmentDefs.ItemStats(item)
	local perkCount = #EquipmentDefs.ItemPerks(item)
	local value: number
	if d.slot == "weapon" then
		value = (s.damage or 0) * (s.fireRate or 0) * (s.projectiles or 1)
	else
		local hp = EquipmentDefs.BASE_PLAYER.maxHp + (s.maxHp or 0)
		local effective = hp / math.max(0.4, 1 - (s.damageReduction or 0))
		value = effective - EquipmentDefs.BASE_PLAYER.maxHp + (s.moveSpeed or 0) * 4
	end
	return math.max(1, math.floor(value * (1 + 0.12 * perkCount) + 0.5))
end

-- Cash spent upgrading an item from level 1 to its current level.
function EquipmentDefs.UpgradeSpent(item: any): number
	local clean = EquipmentDefs.SanitizeItem(item)
	if not clean then
		return 0
	end
	local total = 0
	for level = 1, clean.level - 1 do
		total += Economy.EquipmentUpgradePrice(clean.rarity, level) or 0
	end
	return round2(total)
end

function EquipmentDefs.ScrapValue(item: any): number
	local clean = EquipmentDefs.SanitizeItem(item)
	if not clean then
		return 0
	end
	local share = EquipmentDefs.SCRAP_RARITY_SHARE[clean.rarity] or EquipmentDefs.SCRAP_RARITY_SHARE[1]
	local value = share * Economy.RunBenchmark(1)
		+ EquipmentDefs.SCRAP_UPGRADE_REFUND * EquipmentDefs.UpgradeSpent(clean)
	return math.max(0.01, round2(value))
end

-------------------------------------------------------------------------------
-- Loadout -> combat stats
-------------------------------------------------------------------------------
-- The equipped item of a slot, or nil if missing / wrong slot / unknown id.
local function equippedItem(data: any, slot: string): Item?
	if type(data) ~= "table" or type(data.equipped) ~= "table" or type(data.inventory) ~= "table" then
		return nil
	end
	local uid = data.equipped[slot]
	if type(uid) ~= "string" then
		return nil
	end
	local item = EquipmentDefs.SanitizeItem(data.inventory[uid])
	if not item then
		return nil
	end
	local d = EquipmentDefs.Items[item.id]
	if d.slot ~= slot then
		return nil
	end
	return item
end

-- Bare-handed fallback = a Common level-1 Service Pistol (so a broken save can still fight).
local FALLBACK_WEAPON: Item = { id = "w_pistol", rarity = 1, level = 1 }

function EquipmentDefs.LoadoutStats(data: PlayerData): CombatStats
	local base = EquipmentDefs.BASE_PLAYER
	local weapon = equippedItem(data, "weapon")
	local armour = equippedItem(data, "armour")
	local w = EquipmentDefs.ItemStats(weapon or FALLBACK_WEAPON)
	local a: StatBlock = if armour then EquipmentDefs.ItemStats(armour) else {}

	local perks: { [string]: number } = {}
	local function addPerks(item: Item?)
		if item then
			for _, line in EquipmentDefs.ItemPerks(item) do
				perks[line.id] = (perks[line.id] or 0) + 1
			end
		end
	end
	addPerks(weapon)
	addPerks(armour)

	return {
		maxHp = round2(base.maxHp + (a.maxHp or 0)),
		damage = w.damage or 10,
		fireRate = w.fireRate or 2,
		projectiles = w.projectiles or 1,
		spreadDeg = w.spreadDeg or 0,
		projectileSpeed = w.projectileSpeed or 70,
		range = w.range or 60,
		critChance = math.clamp(base.critChance + (w.critChance or 0), 0, 1),
		critMult = base.critMult + (w.critMult or 0),
		damageReduction = math.clamp(a.damageReduction or 0, 0, EquipmentDefs.MAX_REDUCTION),
		moveSpeed = math.max(8, base.moveSpeed + (a.moveSpeed or 0)),
		perks = perks,
	}
end

-- UI ONLY: the same stats with perk effects applied (ARCHITECTURE §4.5 numbers),
-- so the Armoury can show "Fire rate 2.3/s" instead of "2.0/s + Rapid Fire".
function EquipmentDefs.DisplayStats(stats: CombatStats): CombatStats
	local function p(id: string): number
		return stats.perks[id] or 0
	end
	return {
		maxHp = stats.maxHp,
		damage = stats.damage,
		fireRate = stats.fireRate * (1 + 0.15 * p("rapid")),
		projectiles = stats.projectiles + p("multishot"),
		spreadDeg = stats.spreadDeg,
		projectileSpeed = stats.projectileSpeed,
		range = stats.range,
		critChance = math.min(1, stats.critChance + 0.1 * p("crit")),
		critMult = stats.critMult,
		damageReduction = stats.damageReduction,
		moveSpeed = stats.moveSpeed * (1 + 0.08 * p("swift")),
		perks = stats.perks,
	}
end

-------------------------------------------------------------------------------
-- Chests
-------------------------------------------------------------------------------
local RARITY_LIST: { number } = {}
for r = 1, RARITY_COUNT do
	table.insert(RARITY_LIST, r)
end

local function tierDef(chestTier: string): ChestTierDef
	return EquipmentDefs.CHEST_TIERS[chestTier] or EquipmentDefs.CHEST_TIERS.wood
end

function EquipmentDefs.ChestOdds(chestTier: string): { number }
	local weights = tierDef(chestTier).weights
	local total = 0
	for r = 1, RARITY_COUNT do
		total += weights[r] or 0
	end
	local odds = {}
	for r = 1, RARITY_COUNT do
		odds[r] = (weights[r] or 0) / total
	end
	return odds
end

-- Unknown tier -> treated as "wood".
function EquipmentDefs.RollChest(chestTier: string, rng: Rng.Rng): Item
	local weights = tierDef(chestTier).weights
	local rarity = rng:Weighted(RARITY_LIST, function(r: number): number
		return weights[r] or 0
	end)
	local ids = if rng:Chance(0.5) then EquipmentDefs.WEAPON_IDS else EquipmentDefs.ARMOUR_IDS
	local id = rng:Pick(ids)
	local d = EquipmentDefs.Items[id]

	local slots = Config.Rarities[rarity].perkSlots
	local perks: { string } = {}
	if rarity == RARITY_COUNT and slots > 0 then
		table.insert(perks, d.signature) -- Legendary always carries the signature
	end
	local pool: { string } = {}
	for _, perkId in d.perkPool do
		if perkId ~= d.signature then
			table.insert(pool, perkId)
		end
	end
	rng:Shuffle(pool)
	local index = 1
	while #perks < slots and index <= #pool do
		table.insert(perks, pool[index])
		index += 1
	end

	local item: Item = { id = id, rarity = rarity, level = 1 }
	if #perks > 0 then
		item.perks = perks
	end
	return item
end

return EquipmentDefs
