--[[
	equipment.spec.luau — tests for Shared/Combat/EquipmentDefs (items, rarity,
	chests, loadout stats) and its use of Economy.EquipmentUpgradePrice.
	Run: lune run tests/run equipment
]]

return function(H)
	local EquipmentDefs = H.load("shared/Combat/EquipmentDefs")
	local Config = H.load("shared/Config")
	local Economy = H.load("shared/Economy")
	local Rng = H.load("shared/Rng")
	local Types = H.load("shared/Types")

	local RARITIES = #Config.Rarities
	local PERK_IDS = {
		"multishot",
		"pierce",
		"ricochet",
		"crit",
		"rapid",
		"explosive",
		"regen",
		"shield",
		"dodge",
		"thorns",
		"swift",
		"vampire",
	}

	-- The "main" scaling number of an item: weapon damage or armour HP.
	local function primary(item)
		local s = EquipmentDefs.ItemStats(item)
		local d = EquipmentDefs.Items[item.id]
		if d.slot == "weapon" then
			return s.damage
		end
		return s.maxHp
	end

	H.test("equipment: starter items exist and match Types", function()
		H.expect(EquipmentDefs.Items[Types.STARTER_WEAPON_ID] ~= nil, "starter weapon")
		H.expect(EquipmentDefs.Items[Types.STARTER_ARMOUR_ID] ~= nil, "starter armour")
		H.expect(EquipmentDefs.Items.w_pistol.slot == "weapon", "pistol is a weapon")
		H.expect(EquipmentDefs.Items.a_vest.slot == "armour", "vest is armour")
		H.expect(#EquipmentDefs.WEAPON_IDS == 7, "7 weapons")
		H.expect(#EquipmentDefs.ARMOUR_IDS == 7, "7 armours")
	end)

	H.test("equipment: every item is complete and its perks are known", function()
		for id, d in EquipmentDefs.Items do
			H.expect(d.id == id, "id mismatch " .. id)
			H.expect(type(d.name) == "string" and #d.name > 0, "name " .. id)
			H.expect(type(d.icon) == "string" and #d.icon > 0, "icon " .. id)
			H.expect(type(d.description) == "string" and #d.description > 0, "description " .. id)
			H.expect(EquipmentDefs.PERKS[d.signature] ~= nil, "signature perk " .. id)
			for _, p in d.builtin do
				H.expect(EquipmentDefs.PERKS[p] ~= nil, "builtin perk " .. p)
			end
			local poolWithoutSignature = 0
			for _, p in d.perkPool do
				H.expect(EquipmentDefs.PERKS[p] ~= nil, "pool perk " .. p)
				if p ~= d.signature then
					poolWithoutSignature += 1
				end
			end
			H.expect(poolWithoutSignature >= Config.Rarities[RARITIES].perkSlots - 1, "pool big enough " .. id)
		end
		for _, p in PERK_IDS do
			local perk = EquipmentDefs.PERKS[p]
			H.expect(perk ~= nil and #perk.name > 0 and #perk.description > 0, "perk text " .. p)
		end
		H.expect(EquipmentDefs.Items.w_shotgun.base.projectiles == 3, "shotgun fires 3 pellets")
		H.expect(table.find(EquipmentDefs.Items.w_sniper.builtin, "pierce") ~= nil, "sniper pierces")
		H.expect(table.find(EquipmentDefs.Items.w_rocket.builtin, "explosive") ~= nil, "rocket explodes")
		H.expect(table.find(EquipmentDefs.Items.a_ghillie.builtin, "dodge") ~= nil, "ghillie dodges")
		H.expect(table.find(EquipmentDefs.Items.a_medic.builtin, "regen") ~= nil, "medic regenerates")
	end)

	H.test("equipment: higher rarity is strictly better at the same level", function()
		for id in EquipmentDefs.Items do
			for _, level in { 1, 5, 10 } do
				for r = 2, RARITIES do
					local low = { id = id, rarity = r - 1, level = level }
					local high = { id = id, rarity = r, level = level }
					H.expect(
						primary(high) > primary(low),
						("%s rarity %d > %d at level %d"):format(id, r, r - 1, level)
					)
					H.expect(EquipmentDefs.ItemPower(high) > EquipmentDefs.ItemPower(low), "power " .. id)
					local d = EquipmentDefs.Items[id]
					if d.slot == "armour" then
						local lowS = EquipmentDefs.ItemStats(low)
						local highS = EquipmentDefs.ItemStats(high)
						H.expect(highS.damageReduction >= lowS.damageReduction, "reduction " .. id)
					end
				end
			end
		end
		for r = 2, RARITIES do
			H.expect(Config.Rarities[r].perkSlots >= Config.Rarities[r - 1].perkSlots, "perk slots grow")
			H.expect(Config.Rarities[r].maxLevel > Config.Rarities[r - 1].maxLevel, "max level grows")
		end
	end)

	H.test("equipment: upgrading increases stats (~+6% per level)", function()
		for id in EquipmentDefs.Items do
			for r = 1, RARITIES do
				local maxLevel = Config.Rarities[r].maxLevel
				for level = 1, maxLevel - 1 do
					local a = { id = id, rarity = r, level = level }
					local b = { id = id, rarity = r, level = level + 1 }
					H.expect(primary(b) > primary(a), ("%s r%d level %d -> %d"):format(id, r, level, level + 1))
				end
			end
		end
		local l1 = primary({ id = "w_pistol", rarity = 1, level = 1 })
		local l2 = primary({ id = "w_pistol", rarity = 1, level = 2 })
		H.near(l2 / l1, 1.06, 0.005, "+6% per level")
	end)

	H.test("equipment: chest odds strictly decrease with rarity; gold > silver > wood legendary", function()
		for _, tier in { "wood", "silver", "gold" } do
			local odds = EquipmentDefs.ChestOdds(tier)
			local sum = 0
			for r = 1, RARITIES do
				sum += odds[r]
				if r > 1 then
					H.expect(odds[r] < odds[r - 1], ("%s: rarity %d less likely than %d"):format(tier, r, r - 1))
				end
			end
			H.near(sum, 1, 1e-9, tier .. " odds sum to 1")
		end
		local wood = EquipmentDefs.ChestOdds("wood")
		local silver = EquipmentDefs.ChestOdds("silver")
		local gold = EquipmentDefs.ChestOdds("gold")
		H.expect(gold[RARITIES] > silver[RARITIES], "gold legendary > silver")
		H.expect(silver[RARITIES] > wood[RARITIES], "silver legendary > wood")
		H.expect(gold[1] < silver[1] and silver[1] < wood[1], "better chests have fewer commons")
	end)

	H.test("equipment: 10k chest rolls match the weights; perks follow the rules", function()
		for _, tier in { "wood", "silver", "gold" } do
			local rng = Rng.new(1234 + #tier)
			local counts = { 0, 0, 0, 0, 0 }
			local weapons = 0
			local N = 10000
			for _ = 1, N do
				local item = EquipmentDefs.RollChest(tier, rng)
				local d = EquipmentDefs.Items[item.id]
				H.expect(d ~= nil, "rolled a known item")
				H.expect(item.level == 1, "new items are level 1")
				counts[item.rarity] += 1
				if d.slot == "weapon" then
					weapons += 1
				end
				local perks = item.perks or {}
				H.expect(#perks == Config.Rarities[item.rarity].perkSlots, "perk count = perk slots")
				local seen = {}
				for _, p in perks do
					H.expect(not seen[p], "no duplicate perks")
					seen[p] = true
					H.expect(p == d.signature or table.find(d.perkPool, p) ~= nil, "perk from the item's pool")
				end
				if item.rarity == RARITIES then
					H.expect(seen[d.signature] == true, "legendary has its signature")
				else
					H.expect(not seen[d.signature], "signature is Legendary-only")
				end
			end
			local odds = EquipmentDefs.ChestOdds(tier)
			for r = 1, RARITIES do
				local expected = odds[r]
				local tolerance = 4 * math.sqrt(expected * (1 - expected) / N) + 0.002
				H.near(counts[r] / N, expected, tolerance, tier .. " rarity " .. r)
			end
			H.near(weapons / N, 0.5, 0.03, tier .. " weapon/armour 50/50")
		end
		-- unknown tier behaves like wood, never errors
		local item = EquipmentDefs.RollChest("platinum", Rng.new(5))
		H.expect(EquipmentDefs.Items[item.id] ~= nil, "unknown tier still rolls")
	end)

	H.test("equipment: LoadoutStats never errors on empty / invalid equipment", function()
		local default = EquipmentDefs.LoadoutStats(Types.DefaultData())
		H.expect(default.maxHp > 100, "vest adds HP")
		H.near(default.damage, 10, 1e-9, "pistol damage")
		H.near(default.critChance, 0.05, 1e-9, "base crit")
		H.near(default.critMult, 2, 1e-9, "base crit mult")
		H.near(default.moveSpeed, 20, 1e-9, "base move speed")

		local cases = {
			{},
			{ inventory = {}, equipped = {} },
			{ inventory = {}, equipped = { weapon = "99", armour = "98" } },
			{ inventory = { ["1"] = { id = "nope", rarity = 1, level = 1 } }, equipped = { weapon = "1" } },
			{ inventory = { ["1"] = { id = "a_vest", rarity = 1, level = 1 } }, equipped = { weapon = "1" } },
			{
				inventory = { ["1"] = { id = "w_smg", rarity = 99, level = -4, perks = { "bogus", 5 } } },
				equipped = { weapon = "1", armour = 7 },
			},
			{ inventory = "broken", equipped = "broken" },
		}
		for i, data in cases do
			local ok, stats = pcall(EquipmentDefs.LoadoutStats, data)
			H.expect(ok, "case " .. i .. " errored: " .. tostring(stats))
			H.expect(stats.maxHp >= 100 and stats.damage > 0 and stats.fireRate > 0, "case " .. i .. " sane")
			H.expect(type(stats.perks) == "table", "perks table")
		end
		local ok = pcall(EquipmentDefs.LoadoutStats, nil)
		H.expect(ok, "nil data does not error")
	end)

	H.test("equipment: damage reduction is capped at 60% and perks stack", function()
		local data = Types.DefaultData()
		data.inventory["9"] = { id = "a_riot", rarity = 5, level = 50, perks = { "shield", "thorns", "dodge" } }
		data.inventory["10"] = { id = "w_rocket", rarity = 5, level = 1, perks = { "explosive", "crit", "rapid" } }
		data.equipped.armour = "9"
		data.equipped.weapon = "10"
		local s = EquipmentDefs.LoadoutStats(data)
		H.expect(s.damageReduction <= 0.6 + 1e-9, "reduction capped")
		H.expect(s.perks.explosive == 2, "built-in + signature explosive stack")
		H.expect(s.perks.shield == 1 and s.perks.crit == 1, "perks counted")
		H.expect(s.moveSpeed < 20, "riot armour is slower")
		local shown = EquipmentDefs.DisplayStats(s)
		H.expect(shown.critChance > s.critChance and shown.fireRate > s.fireRate, "display applies perks")
	end)

	H.test("equipment: Economy.EquipmentUpgradePrice is nil at max level, scrap pays more after upgrades", function()
		for r = 1, RARITIES do
			local maxLevel = Config.Rarities[r].maxLevel
			H.expect(Economy.EquipmentUpgradePrice(r, maxLevel) == nil, "nil at max for rarity " .. r)
			local price = Economy.EquipmentUpgradePrice(r, maxLevel - 1)
			H.expect(type(price) == "number" and price > 0, "price below max for rarity " .. r)
		end
		local fresh = { id = "w_rifle", rarity = 3, level = 1 }
		local upgraded = { id = "w_rifle", rarity = 3, level = 6 }
		H.expect(EquipmentDefs.UpgradeSpent(fresh) == 0, "nothing spent on a fresh item")
		H.expect(EquipmentDefs.ScrapValue(upgraded) > EquipmentDefs.ScrapValue(fresh), "upgrades raise scrap value")
		local spent = EquipmentDefs.UpgradeSpent(upgraded)
		H.expect(
			EquipmentDefs.ScrapValue(upgraded) - EquipmentDefs.ScrapValue(fresh) < spent,
			"scrap refunds only part"
		)
		for r = 2, RARITIES do
			H.expect(
				EquipmentDefs.ScrapValue({ id = "a_vest", rarity = r, level = 1 })
					> EquipmentDefs.ScrapValue({ id = "a_vest", rarity = r - 1, level = 1 }),
				"rarer scraps for more"
			)
		end
	end)

	H.test("equipment: text helpers", function()
		local item = { id = "w_shotgun", rarity = 4, level = 3, perks = { "crit", "rapid" } }
		local lines = EquipmentDefs.ItemStatsText(item)
		H.expect(#lines >= 3, "stat lines")
		local perks = EquipmentDefs.ItemPerks(item)
		H.expect(#perks == 2, "two perks")
		local legendary = { id = "a_ghillie", rarity = 5, level = 1, perks = { "dodge", "swift", "regen" } }
		local lp = EquipmentDefs.ItemPerks(legendary)
		H.expect(lp[1].source == "builtin" and lp[2].source == "signature", "builtin then signature")
		H.expect(#EquipmentDefs.ItemStatsText({ id = "unknown" }) == 0, "unknown item -> no lines")
		H.expect(EquipmentDefs.SanitizeItem({ id = "nope" }) == nil, "unknown item sanitizes to nil")
	end)
end
