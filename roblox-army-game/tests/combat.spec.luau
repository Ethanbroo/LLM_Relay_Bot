--[[
	combat.spec.luau — tests for Shared/Combat/CombatDefs (pure combat rules).
	Run: lune run tests/run combat
]]

return function(H)
	local CombatDefs = H.load("shared/Combat/CombatDefs")
	local Economy = H.load("shared/Economy")
	local Rng = H.load("shared/Rng")

	local MAX_LEVEL = 60

	H.test("combat: every enemy type of the brief exists", function()
		for _, id in { "Rifleman", "Charger", "Grenadier", "Gunner", "Sniper", "Officer" } do
			local def = CombatDefs.ENEMIES[id]
			H.expect(def ~= nil, "missing enemy " .. id)
			H.expect(def.id == id, "id mismatch " .. id)
		end
		H.expect(CombatDefs.ENEMIES.Officer.boss == true, "Officer is the boss")
		H.expect(#CombatDefs.ENEMY_ORDER == 6, "6 enemy types")
	end)

	H.test("combat: enemy bullets are slow and dodgeable, telegraphs visible", function()
		for _, id in { "Rifleman", "Gunner", "Officer" } do
			for _, level in { 1, 20, 60 } do
				local s = CombatDefs.EnemyStats(id, level)
				H.expect(s.bulletSpeed >= 15 and s.bulletSpeed <= 28, id .. " bullet speed " .. s.bulletSpeed)
			end
		end
		H.near(CombatDefs.ENEMIES.Grenadier.fuse, 0.9, 0.11, "grenade telegraph ~0.9 s")
		H.expect(CombatDefs.ENEMIES.Sniper.windup >= 0.8, "sniper laser shows long enough")
	end)

	H.test("combat: wave counts follow the spec", function()
		H.expect(CombatDefs.WaveCount(1, "arena") == 3, "level 1 arena = 3 waves")
		H.expect(CombatDefs.WaveCount(4, "arena") == 4, "level 4 arena = 4 waves")
		H.expect(CombatDefs.WaveCount(12, "arena") == 6, "level 12 arena = 6 waves")
		H.expect(CombatDefs.WaveCount(40, "arena") == 6, "max 6 waves")
		for level = 1, 30 do
			H.expect(CombatDefs.WaveCount(level, "barrier") == 4, "barrier = 4 waves")
		end
	end)

	H.test("combat: bosses on barrier fights and every 5th arena level (last wave)", function()
		for level = 1, 30 do
			local plan = CombatDefs.LevelPlan(level, "arena")
			local last = plan.waves[plan.waveCount]
			local hasBoss = false
			for _, wave in plan.waves do
				for _, e in wave.enemies do
					if e.boss then
						hasBoss = true
						H.expect(wave == last, "boss must be on the last wave")
					end
				end
			end
			H.expect(hasBoss == (level % 5 == 0), "arena boss rule at level " .. level)
			local barrier = CombatDefs.LevelPlan(level, "barrier")
			local found = false
			for _, e in barrier.waves[4].enemies do
				found = found or (e.type == "Officer")
			end
			H.expect(found, "barrier last wave has the Officer at level " .. level)
		end
	end)

	H.test("combat: difficulty (HP x damage) strictly increases with level", function()
		for _, mode in { "arena", "barrier" } do
			local previous = 0
			for level = 1, MAX_LEVEL do
				local plan = CombatDefs.LevelPlan(level, mode)
				local d = CombatDefs.Difficulty(plan)
				H.expect(
					d > previous,
					("%s difficulty level %d (%.0f) <= level %d (%.0f)"):format(mode, level, d, level - 1, previous)
				)
				previous = d
				for _, wave in plan.waves do
					H.expect(#wave.enemies >= 1, "every wave has enemies")
					H.expect(#wave.enemies <= CombatDefs.MAX_ENEMIES_PER_WAVE + 1, "wave cap")
				end
			end
		end
	end)

	H.test("combat: more enemies and more dangerous types later", function()
		local early = CombatDefs.LevelPlan(1, "arena")
		local late = CombatDefs.LevelPlan(25, "arena")
		H.expect(late.enemyCount > early.enemyCount, "more enemies at level 25")
		for _, wave in early.waves do
			for _, e in wave.enemies do
				H.expect(e.type == "Rifleman" or e.type == "Charger", "level 1 only has basic types, got " .. e.type)
			end
		end
		local seen = {}
		for _, wave in late.waves do
			for _, e in wave.enemies do
				seen[e.type] = true
			end
		end
		H.expect(seen.Sniper or seen.Gunner or seen.Grenadier, "level 25 has dangerous types")
	end)

	H.test("combat: cash split 70/30 sums to the level reward", function()
		for _, mode in { "arena", "barrier" } do
			for level = 1, 30 do
				local plan = CombatDefs.LevelPlan(level, mode)
				H.near(plan.reward, Economy.CombatLevelReward(level), 0.001, "reward from Economy")
				local enemyCash, pileCash = 0, 0
				for _, wave in plan.waves do
					for _, e in wave.enemies do
						H.expect(e.cash >= 0, "no negative drops")
						enemyCash += e.cash
					end
					H.expect(#wave.piles == CombatDefs.PILES_PER_WAVE, "piles per wave")
					for _, v in wave.piles do
						H.expect(v >= 0, "no negative piles")
						pileCash += v
					end
				end
				H.near(enemyCash + pileCash, plan.reward, 0.011, "perfect clear = reward")
				H.near(enemyCash / plan.reward, 0.7, 0.01, "70% from enemies")
			end
		end
	end)

	H.test("combat: rewards increase with level", function()
		local previous = 0
		for level = 1, MAX_LEVEL do
			local reward = CombatDefs.LevelPlan(level, "arena").reward
			H.expect(reward > previous, "reward rises at level " .. level)
			previous = reward
		end
	end)

	H.test("combat: barrier level comes from Economy and is deterministic", function()
		for area = 1, 7 do
			local level = Economy.BarrierCombatLevel(area)
			local a = CombatDefs.LevelPlan(level, "barrier")
			local b = CombatDefs.LevelPlan(level, "barrier")
			H.expect(a == b, "plans are cached / deterministic")
			H.expect(a.waveCount == 4, "barrier waves")
		end
	end)

	H.test("combat: ability ids unique and complete", function()
		local seen = {}
		for _, a in CombatDefs.ABILITIES do
			H.expect(not seen[a.id], "duplicate ability id " .. a.id)
			seen[a.id] = true
			H.expect(type(a.name) == "string" and #a.name > 0, "name")
			H.expect(type(a.icon) == "string" and #a.icon > 0, "icon")
			H.expect(type(a.description) == "string" and #a.description > 0, "description")
			H.expect(a.maxStacks >= 1, "maxStacks")
			H.expect(CombatDefs.AbilityById[a.id] == a, "lookup")
		end
		for _, id in
			{
				"multishot",
				"pierce",
				"ricochet",
				"rapid",
				"attack",
				"crit",
				"maxhp",
				"heal",
				"shield",
				"dodge",
				"swift",
				"vampire",
				"explosive",
			}
		do
			H.expect(seen[id], "missing ability " .. id)
		end
	end)

	H.test("combat: every §4.5 perk id is handled", function()
		local required = {
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
		local base = CombatDefs.DefaultStats()
		local plain = CombatDefs.DeriveStats(base, {})
		for _, perk in required do
			local listed = false
			for _, id in CombatDefs.PERK_IDS do
				listed = listed or id == perk
			end
			H.expect(listed, "PERK_IDS lists " .. perk)
			-- from equipment
			local withPerk = CombatDefs.DefaultStats()
			withPerk.perks = { [perk] = 1 }
			local derived = CombatDefs.DeriveStats(withPerk, {})
			local changed = false
			for key, value in derived do
				if key ~= "perks" and plain[key] ~= value then
					changed = true
				end
			end
			H.expect(changed, "equipment perk " .. perk .. " changes a stat")
			H.expect(derived.perks[perk] == 1, "perk stacks kept")
		end
		-- from abilities
		for _, a in CombatDefs.ABILITIES do
			if a.effect.kind ~= "heal" then
				local derived = CombatDefs.DeriveStats(base, { [a.id] = 1 })
				local changed = false
				for key, value in derived do
					if key ~= "perks" and plain[key] ~= value then
						changed = true
					end
				end
				H.expect(changed, "ability " .. a.id .. " changes a stat")
			end
		end
		-- caps
		local many = CombatDefs.DefaultStats()
		many.perks = { dodge = 9 }
		H.expect(CombatDefs.DeriveStats(many, {}).dodge <= 0.4, "dodge max 40%")
		H.near(CombatDefs.DeriveStats(base, { rapid = 2 }).fireRate, base.fireRate * 1.3, 1e-6, "rapid +15% per stack")
		H.expect(
			CombatDefs.DeriveStats(base, { multishot = 2 }).projectiles == base.projectiles + 2,
			"multishot +1 per stack"
		)
	end)

	H.test("combat: DeriveStats survives broken equipment stats", function()
		local broken = CombatDefs.DefaultStats() :: any
		broken.maxHp = 0 / 0
		broken.fireRate = math.huge
		broken.damageReduction = 5
		broken.perks = { dodge = "x", [1] = 3 }
		local s = CombatDefs.DeriveStats(broken, {})
		H.expect(s.maxHp == s.maxHp and s.maxHp > 0, "maxHp sane")
		H.expect(s.fireRate == 2, "fireRate fallback")
		H.expect(s.damageReduction <= 0.6, "damage reduction capped")
	end)

	H.test("combat: ability rolls respect maxStacks and are distinct", function()
		local rng = Rng.new(42)
		for _ = 1, 200 do
			local options = CombatDefs.RollAbilities({}, rng, 3)
			H.expect(#options == 3, "three options")
			H.expect(options[1] ~= options[2] and options[2] ~= options[3] and options[1] ~= options[3], "distinct")
		end
		local maxed = {}
		for _, a in CombatDefs.ABILITIES do
			maxed[a.id] = a.maxStacks
		end
		maxed.heal = 0
		local options = CombatDefs.RollAbilities(maxed, rng, 3)
		H.expect(#options == 1 and options[1] == "heal", "only non-maxed abilities offered")
	end)

	H.test("combat: chest tiers", function()
		local minSeconds = CombatDefs.CHEST_MIN_SECONDS
		H.expect(CombatDefs.ChestTier("barrier", 2, true, 5, 60) == "gold", "barrier win = gold")
		H.expect(CombatDefs.ChestTier("arena", 5, true, 5, 60) == "gold", "5th level win = gold")
		H.expect(CombatDefs.ChestTier("arena", 3, true, 5, 60) == "silver", "win = silver")
		H.expect(CombatDefs.ChestTier("arena", 3, false, 1, 0) == "wood", "defeat with a kill = wood")
		H.expect(CombatDefs.ChestTier("arena", 3, false, 0, minSeconds) == "wood", "long defeat, no kills = wood")
		H.expect(CombatDefs.ChestTier("barrier", 2, false, 0, minSeconds + 5) == "wood", "long barrier defeat = wood")
		H.expect(CombatDefs.ChestTier("arena", 3, false, 0, minSeconds - 0.5) == nil, "start and leave = no chest")
		H.expect(CombatDefs.ChestTier("arena", 3, false, 0, 0) == nil, "no kills, no time = no chest")
	end)

	H.test("combat: snapshot round trip fits an unreliable remote", function()
		local snap = { t = 12345.678, cash = 987.65, enemies = {}, pickups = {} }
		for i = 1, 30 do
			table.insert(snap.enemies, {
				id = i,
				type = CombatDefs.ENEMY_ORDER[(i % 6) + 1],
				x = i - 15.25,
				z = -i * 1.5,
				hp = 100 + i,
				maxHp = 200,
				state = "aim",
			})
		end
		for i = 1, 40 do
			table.insert(snap.pickups, { id = 1000 + i, x = -i, z = i * 0.5, value = 12.5 })
		end
		local b = CombatDefs.EncodeSnap(snap)
		H.expect(buffer.len(b) <= 900, "snapshot must stay under ~900 bytes, got " .. buffer.len(b))
		local out = CombatDefs.DecodeSnap(b)
		H.expect(out ~= nil, "decodes")
		H.near(out.t, snap.t, 1e-6, "time")
		H.near(out.cash, snap.cash, 1e-6, "cash")
		H.expect(#out.enemies == CombatDefs.SNAP_MAX_ENEMIES, "enemy cap")
		H.expect(out.enemies[3].type == snap.enemies[3].type, "type")
		H.expect(out.enemies[3].state == "aim", "state")
		H.near(out.enemies[3].x, snap.enemies[3].x, 0.02, "x")
		H.near(out.enemies[3].z, snap.enemies[3].z, 0.02, "z")
		H.near(out.pickups[2].value, 12.5, 1e-4, "pickup value")
		H.expect(CombatDefs.DecodeSnap("nope") == nil, "rejects junk")
	end)

	H.test("combat: cover blocks stay inside the arena and off the start spot", function()
		for _, c in CombatDefs.COVER do
			H.expect(math.abs(c.x) + c.sx / 2 < CombatDefs.HALF_X, "cover inside x")
			H.expect(math.abs(c.z) + c.sz / 2 < CombatDefs.HALF_Z, "cover inside z")
		end
		H.expect(
			CombatDefs.CoverAt(CombatDefs.PLAYER_START.x, CombatDefs.PLAYER_START.z, 3) == nil,
			"start spot is free"
		)
	end)
end
