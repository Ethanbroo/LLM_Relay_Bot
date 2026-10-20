--[[
	runner.spec.luau — tests for Shared/Runner/RunnerCourse + RunnerSim.
		lune run tests/run runner
]]

return function(H)
	local Economy = H.load("shared/Economy")
	local Rng = H.load("shared/Rng")
	local RunnerCourse = H.load("shared/Runner/RunnerCourse")
	local RunnerDefs = H.load("shared/Runner/RunnerDefs")
	local RunnerSim = H.load("shared/Runner/RunnerSim")

	local AREAS = 8

	local function refStats(area)
		return Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
	end

	local function deepEqual(a, b): boolean
		if type(a) ~= type(b) then
			return false
		end
		if type(a) ~= "table" then
			return a == b
		end
		for k, v in a do
			if not deepEqual(v, b[k]) then
				return false
			end
		end
		for k in b do
			if a[k] == nil then
				return false
			end
		end
		return true
	end

	local function isWhole(n, what)
		H.expect(type(n) == "number" and math.floor(n) == n, what .. " is not whole dollars: " .. tostring(n))
	end

	-- Damage the squad of a finished run could deal to a boss that never dies.
	local function bossDamage(r)
		return RunnerSim.BossDamagePotential(r.boss.units, r.unitDps)
	end

	local function randomChoices(course, rng)
		local choices = {}
		for i, row in course.rows do
			choices[i] = rng:Int(1, #row.options)
		end
		return choices
	end

	-- A handful of courses per area: the first benchmark level, a mid one and a late one.
	local function sampleCourses()
		local list = {}
		for area = 1, AREAS do
			local first = RunnerDefs.FirstLevelOfArea(area)
			for _, level in { first, first + 5, first + 37 } do
				table.insert(list, RunnerCourse.Generate(area, level))
			end
		end
		return list
	end
	local courses = sampleCourses()

	H.test("Generate is deterministic and seeds differ", function()
		for area = 1, AREAS do
			for level = 1, 25, 6 do
				local a = RunnerCourse.Generate(area, level)
				local b = RunnerCourse.Generate(area, level)
				H.expect(deepEqual(a, b), ("course %d/%d not deterministic"):format(area, level))
			end
		end
		local seeds = {}
		for area = 1, AREAS do
			for level = 1, 100 do
				local seed = RunnerCourse.SeedFor(area, level)
				H.expect(seeds[seed] == nil, "duplicate seed")
				seeds[seed] = true
			end
		end
	end)

	H.test("Course structure, lanes and sensible options", function()
		local tones = { positive = true, negative = true, weapon = true, cash = true, enemy = true }
		for _, course in courses do
			H.expect(#course.rows == RunnerDefs.ROW_COUNT, "row count")
			H.expect(course.length == RunnerDefs.TRACK_LENGTH, "length")
			H.expect(course.boss.distance < course.length, "boss inside track")
			H.expect(course.boss.hp == 0 and course.boss.reward > 0, "boss values (hp comes from RunnerSim.BossHp)")
			isWhole(course.boss.reward, "boss reward")
			local lastDistance = 0
			local mulRows = 0
			for i, row in course.rows do
				H.expect(row.index == i, "row index")
				H.expect(row.distance > lastDistance and row.distance < course.boss.distance, "row distance")
				lastDistance = row.distance
				local sensible = false
				for lane, opt in row.options do
					H.expect(opt.lane == lane, "lane index")
					H.expect(math.abs(opt.x) + opt.width / 2 <= RunnerDefs.TRACK_WIDTH / 2 + 1e-9, "lane inside track")
					H.expect(tones[opt.tone], "tone " .. tostring(opt.tone))
					H.expect(type(opt.label) == "string" and #opt.label > 0, "label")
					H.expect(not opt.label:find("%$%d+%.%d"), "no cents on labels: " .. opt.label)
					if opt.cash then
						isWhole(opt.cash, "cash stack")
					end
					if opt.reward then
						isWhole(opt.reward, "reward")
					end
					if opt.hp then
						H.expect(math.floor(opt.hp) == opt.hp, "whole hp")
					end
					if opt.op == "mul" then
						mulRows += 1
					end
				end
				if row.kind == "gate" then
					H.expect(#row.options == 2, "gate lanes")
					for _, opt in row.options do
						H.expect(opt.op ~= nil and (opt.amount or 0) > 0, "gate op/amount")
						if opt.tone == "positive" or opt.tone == "weapon" then
							sensible = true
						end
					end
				elseif row.kind == "cash" then
					H.expect(#row.options == 3, "cash lanes")
					sensible = true
					for _, opt in row.options do
						H.expect((opt.cash or 0) > 0, "cash value")
					end
				elseif row.kind == "squad" then
					H.expect(#row.options == 1 or #row.options == 2, "squad lanes")
					for _, opt in row.options do
						H.expect(
							(opt.enemies or 0) >= 1 and (opt.hp or 0) > 0 and (opt.reward or 0) > 0,
							"squad values"
						)
					end
					sensible = true -- one squad is unavoidable, or pick the smaller one
				elseif row.kind == "barricade" then
					H.expect(#row.options == 2, "barricade lanes")
					local a, b = row.options[1], row.options[2]
					H.expect(a.hp ~= b.hp, "weak and strong barricade")
					sensible = true
				else
					error("unknown row kind " .. row.kind)
				end
				H.expect(sensible, ("row %d of %d/%d has no sensible option"):format(i, course.area, course.level))
			end
			H.expect(mulRows == RunnerDefs.MUL_ROWS, "x2 rows per course: " .. mulRows)
		end
	end)

	H.test("BestCase >= Resolve for 200 random choice vectors, and it reproduces", function()
		local rng = Rng.new(4242)
		for _, course in courses do
			local stats = refStats(course.area)
			local best, choices = RunnerSim.BestCase(course, stats)
			H.expect(not RunnerSim.LastSearch.capHit, "state cap hit")
			H.expect(#choices == #course.rows, "one choice per row")
			local again = RunnerSim.Resolve(course, stats, choices)
			H.expect(again.total == best.total, ("Resolve(best choices) %s ~= %s"):format(again.total, best.total))
			for _ = 1, 200 do
				local r = RunnerSim.Resolve(course, stats, randomChoices(course, rng))
				H.expect(
					r.total <= best.total + 1e-6,
					("random run %s beats best %s (area %d level %d)"):format(
						r.total,
						best.total,
						course.area,
						course.level
					)
				)
			end
		end
	end)

	H.test("BestCase equals brute force on shortened courses", function()
		for area = 1, AREAS, 3 do
			for _, level in { 1, 7, 13 } do
				local full = RunnerCourse.Generate(area, level)
				local short = table.clone(full)
				short.rows = table.move(full.rows, 1, 11, 1, {})
				local stats = refStats(area)
				local best = RunnerSim.BestCase(short, stats)
				-- Enumerate every lane combination.
				local bruteBest = -math.huge
				local choices = {}
				local function walk(i)
					if i > #short.rows then
						bruteBest = math.max(bruteBest, RunnerSim.Resolve(short, stats, choices).total)
						return
					end
					for lane = 1, #short.rows[i].options do
						choices[i] = lane
						walk(i + 1)
					end
				end
				walk(1)
				H.near(best.total, bruteBest, 1e-6, ("brute force area %d level %d"):format(area, level))
			end
		end
	end)

	H.test("Pareto search stays far below the state cap", function()
		for area = 1, AREAS do
			for level = 1, 120, 7 do
				local course = RunnerCourse.Generate(area, level)
				RunnerSim.BestCase(course, refStats(area))
				H.expect(not RunnerSim.LastSearch.capHit, "cap hit")
				H.expect(RunnerSim.LastSearch.maxFrontier < RunnerSim.STATE_CAP / 10, "frontier too big")
			end
			-- Extreme stats (max upgrades, zero damage) too.
			local course = RunnerCourse.Generate(area, 5)
			RunnerSim.BestCase(course, { startTroops = 53, damage = 30, capacity = 1e6 })
			H.expect(not RunnerSim.LastSearch.capHit, "cap hit (strong)")
			RunnerSim.BestCase(course, { startTroops = 1, damage = 0, capacity = 0 })
			H.expect(not RunnerSim.LastSearch.capHit, "cap hit (weak)")
		end
	end)

	H.test("Capacity caps carried cash and overflow is reported", function()
		local rng = Rng.new(7)
		for _, course in courses do
			for _, capacity in { 0, 10, 55.55, 1e9 } do
				local stats = { startTroops = 5, damage = 10, capacity = capacity }
				local r = RunnerSim.Resolve(course, stats, randomChoices(course, rng))
				H.expect(r.carried <= capacity + 1e-9, "carried over capacity")
				H.expect(r.carried <= r.collected + 1e-9, "carried more than collected")
				if capacity >= 1e9 then
					H.near(r.carried, r.collected, 1e-6, "no cap -> carried = collected")
				end
				local cashLine
				for _, line in r.lines do
					if line.kind == "cash" then
						cashLine = line
					end
				end
				H.expect(cashLine ~= nil and cashLine.value == r.carried, "cash line")
				if r.collected > r.carried + 0.001 then
					H.expect(cashLine.detail:find("lost") ~= nil, "overflow reported")
				end
			end
		end
	end)

	H.test("Troops never drop below 1 on the track; final troops = boss survivors", function()
		local rng = Rng.new(99)
		for _, course in courses do
			for _, stats in { { startTroops = 1, damage = 0, capacity = 50 }, refStats(course.area) } do
				for _ = 1, 20 do
					local r = RunnerSim.Resolve(course, stats, randomChoices(course, rng))
					H.expect(r.boss.units >= 1, "units at the castle")
					H.expect(r.troops == r.boss.survivors, "final troops = survivors")
					H.expect(if r.boss.victory then r.troops >= 1 else r.troops == 0, "victory keeps the avatar")
					for _, oc in r.outcomes do
						H.expect(oc.troopsAfter >= 1 and oc.troopsBefore >= 1, "row troops")
					end
				end
			end
		end
	end)

	H.test("Invalid or missing choices count as lane 1", function()
		local course = courses[1]
		local stats = refStats(1)
		local ones = {}
		for i = 1, #course.rows do
			ones[i] = 1
		end
		local base = RunnerSim.Resolve(course, stats, ones)
		local weird: { any } = { 0, 99, 1.5, -2, "x", true }
		H.expect(RunnerSim.Resolve(course, stats, weird :: any).total == base.total, "garbage choices")
		H.expect(RunnerSim.Resolve(course, stats, {}).total == base.total, "empty choices")
		H.expect(RunnerSim.Resolve(course, stats, nil :: any).total == base.total, "nil choices")
		-- Garbage stats do not crash and still give a valid run.
		local r = RunnerSim.Resolve(course, { startTroops = 0 / 0, damage = -5, capacity = math.huge } :: any, ones)
		H.expect(r.boss.units >= 1 and r.total == r.total and r.total >= 0, "garbage stats")
	end)

	H.test("Summary lines add up and list every upgrade", function()
		local rng = Rng.new(31)
		for _, course in courses do
			for _ = 1, 10 do
				local choices = randomChoices(course, rng)
				local r = RunnerSim.Resolve(course, refStats(course.area), choices)
				local sum, gold, kinds = 0, 0, {}
				for _, line in r.lines do
					sum += line.value
					kinds[line.kind] = true
					H.expect(line.value >= 0, "line value >= 0")
					isWhole(line.value, line.label)
					H.expect(not (line.label .. line.detail):find("%$%d+%.%d"), "no cents: " .. line.detail)
					H.expect(#line.label > 0 and type(line.detail) == "string", "line text")
					if line.kind == "weapon" or line.kind == "damage" then
						gold += 1
					end
				end
				H.expect(sum == r.total, "sum of lines")
				isWhole(r.total, "total")
				for _, k in { "troops", "cash", "enemies", "boss" } do
					H.expect(kinds[k], "missing line " .. k)
				end
				local goldPicked = 0
				for _, oc in r.outcomes do
					local opt = course.rows[oc.row].options[oc.lane]
					if opt.op == "weapon" or opt.op == "damage" then
						goldPicked += 1
					end
				end
				H.expect(gold == goldPicked, "one line per weapon/damage gate")
			end
		end
	end)

	H.test("Troops line separates starting troops from troops gained in the run", function()
		local rng = Rng.new(77)
		local sawGain = false
		for _, course in courses do
			for _ = 1, 10 do
				local stats = refStats(course.area)
				local r = RunnerSim.Resolve(course, stats, randomChoices(course, rng))
				local line
				for _, l in r.lines do
					if l.kind == "troops" then
						line = l
					end
				end
				H.expect(line ~= nil, "troops line")
				H.expect(line.label == "Troops at finish x " .. r.troops, "label: " .. line.label)
				local delta = r.boss.units - stats.startTroops
				local sign = if delta >= 0 then "+" else "-"
				local expected = ("started with %d from upgrades, %s%d this run, %d lost to the boss"):format(
					stats.startTroops,
					sign,
					math.abs(delta),
					r.boss.killed
				)
				H.expect(line.detail:find(expected, 1, true) ~= nil, "detail: " .. line.detail)
				sawGain = sawGain or delta > 0
			end
		end
		H.expect(sawGain, "some random run gained troops")
	end)

	H.test("Payout bonus multiplies the total", function()
		local course = courses[4]
		local stats = refStats(course.area)
		local best, choices = RunnerSim.BestCase(course, stats)
		local bonusStats = table.clone(stats)
		bonusStats.payoutBonus = 0.1
		local boosted = RunnerSim.Resolve(course, bonusStats, choices)
		H.expect(boosted.total == best.total + math.floor(best.total * 0.1 + 0.5), "bonus total")
		H.expect(boosted.lines[#boosted.lines].kind == "bonus", "bonus line")
		isWhole(boosted.lines[#boosted.lines].value, "bonus")
		local bestBoosted = RunnerSim.BestCase(course, bonusStats)
		H.expect(bestBoosted.total == boosted.total, "best case with bonus")
	end)

	H.test("Weapons: DPS strictly rises with tier, tiers feel different, gun range", function()
		local base = { startTroops = 3, damage = 10, capacity = 60 }
		local previous = 0
		for tier, w in RunnerDefs.WEAPONS do
			H.expect(w.damage > 0 and w.fireRate > 0 and w.bulletSpeed > 0, "weapon numbers")
			H.expect(type(w.bulletStyle) == "string" and type(w.short) == "string", "weapon visuals")
			H.expect(w.damageMult == nil, "no second source of truth (damageMult removed)")
			local dps = RunnerSim.UnitDps(base, tier, 1)
			H.near(dps, w.damage * w.fireRate, 1e-9, "UnitDps at base damage")
			H.expect(dps > previous, "DPS rises at tier " .. tier)
			previous = dps
		end
		local W = RunnerDefs.WEAPONS
		H.expect(W[3].damage < W[2].damage and W[3].fireRate > W[2].fireRate, "SMG: weak bullets, fast")
		H.expect(W[6].damage > 5 * W[5].damage and W[6].fireRate < W[1].fireRate, "Rocket: huge hits, slow")
		-- Old damageMult ratios kept roughly: 1, 1.35, 1.75, 2.25, 2.9, 3.7.
		for tier, ratio in { 1, 1.35, 1.75, 2.25, 2.9, 3.7 } do
			H.near(RunnerSim.UnitDps(base, tier, 1) / RunnerSim.UnitDps(base, 1, 1), ratio, 0.12, "ratio " .. tier)
		end
		-- Damage upgrades and in-run damage gates scale it linearly.
		H.near(RunnerSim.UnitDps({ startTroops = 3, damage = 12, capacity = 0 }, 2, 1.4), 27 * 1.2 * 1.4, 1e-9, "scale")
		H.expect(RunnerSim.UnitDps(base, 99, 1) == RunnerSim.UnitDps(base, RunnerDefs.MAX_WEAPON_TIER, 1), "clamp")
		H.near(RunnerDefs.GUN_RANGE, 1.5 * RunnerDefs.ROW_SPACING, 1e-9, "an option and a half")
		H.near(RunnerDefs.ENGAGE_SECONDS, RunnerDefs.GUN_RANGE / RunnerDefs.RUN_SPEED, 1e-9, "engage time")
		H.expect(RunnerDefs.FORMATION_WIDTH == 5, "5 units per row")
	end)

	H.test("Weapon gate at max tier still pays", function()
		local course = {
			area = 1,
			level = 1,
			seed = 1,
			length = 900,
			boss = { distance = 870, hp = 100, reward = 10 },
			rows = {},
		}
		for i = 1, RunnerDefs.MAX_WEAPON_TIER + 1 do
			table.insert(course.rows, {
				index = i,
				kind = "gate",
				distance = 70 + 45 * (i - 1),
				options = {
					{ lane = 1, x = 0, width = 11, op = "weapon", amount = 1, label = "WEAPON +1", tone = "weapon" },
				},
			})
		end
		local r = RunnerSim.Resolve(course, { startTroops = 3, damage = 10, capacity = 60 }, {})
		H.expect(r.weaponTier == RunnerDefs.MAX_WEAPON_TIER, "max tier")
		local last = r.lines[RunnerDefs.MAX_WEAPON_TIER]
		H.expect(last.kind == "weapon" and last.value > 0 and last.label:find("MAX") ~= nil, "max tier still pays")
	end)

	H.test("MinDuration = boss distance / run speed", function()
		for _, course in courses do
			H.near(RunnerSim.MinDuration(course), course.boss.distance / RunnerDefs.RUN_SPEED, 1e-9, "min duration")
		end
	end)

	H.test("Reference player wins most fights in every area", function()
		for area = 1, AREAS do
			local stats = refStats(area)
			local first = RunnerDefs.FirstLevelOfArea(area)
			local enemies, killed, bossSum = 0, 0, 0
			for level = first, first + 9 do
				local course = RunnerCourse.Generate(area, level)
				local best = RunnerSim.BestCase(course, stats)
				for _, oc in best.outcomes do
					local opt = course.rows[oc.row].options[oc.lane]
					if oc.kind == "squad" then
						enemies += opt.enemies
					end
				end
				for _, line in best.lines do
					if line.kind == "enemies" then
						killed += tonumber(line.label:match("x (%d+)")) or 0
					end
				end
				if best.boss.victory then
					bossSum += 1
				end
			end
			H.expect(
				killed / enemies >= 0.6,
				("area %d: only %.0f%% of soldiers beaten"):format(area, 100 * killed / enemies)
			)
			H.expect(bossSum == 10, ("area %d: best lanes beat the boss only %d / 10"):format(area, bossSum))
		end
	end)
	H.test("BossFight closed form: victory, defeat, survivors, duration", function()
		-- 10 units, 10 DPS each, interval 0.55: D(k) = 5.5 (10k - k(k-1)/2), D(10) = 302.5.
		H.near(RunnerDefs.BossKillInterval(10), 0.55, 1e-12, "interval")
		H.near(RunnerSim.BossDamagePotential(10, 10), 302.5, 1e-9, "potential")
		local win = RunnerSim.BossFight(10, 10, 300)
		H.expect(win.victory and win.killed == 9 and win.survivors == 1, "barely won: only the avatar left")
		H.near(win.duration, 9 * 0.55 + 3 / 10, 1e-9, "dies 0.3 s into the last interval")
		H.expect(win.hpLeft == 0 and win.damageDealt == 300, "victory damage")
		local quick = RunnerSim.BossFight(10, 10, 50)
		H.expect(quick.victory and quick.killed == 0 and quick.survivors == 10, "quick win")
		H.near(quick.duration, 0.5, 1e-9, "quick duration")
		local lose = RunnerSim.BossFight(10, 10, 302.6)
		H.expect(not lose.victory and lose.killed == 10 and lose.survivors == 0, "defeat kills everyone")
		H.near(lose.duration, 5.5, 1e-9, "defeat lasts units x interval")
		H.near(lose.damageDealt, 302.5, 1e-9, "damage dealt")
		H.near(lose.hpLeft, 0.1, 1e-9, "hp left")
		-- Big armies: the interval shrinks so the fight never lasts longer than the cap.
		H.near(RunnerDefs.BossKillInterval(100), RunnerDefs.BOSS_MAX_FIGHT_SECONDS / 100, 1e-12, "interval(100)")
		local big = RunnerSim.BossFight(100, 5, 1e9)
		H.near(big.duration, RunnerDefs.BOSS_MAX_FIGHT_SECONDS, 1e-9, "capped fight")
		-- No damage / no units -> defeat.
		H.expect(not RunnerSim.BossFight(10, 0, 5).victory, "no damage")
		H.expect(not RunnerSim.BossFight(0, 50, 5).victory, "no units")

		-- Against a step-by-step simulation (dt = 1 ms) on random fights.
		local rng = Rng.new(5)
		for _ = 1, 60 do
			local n = rng:Int(1, 60)
			local u = rng:Range(1, 40)
			local potential = RunnerSim.BossDamagePotential(n, u)
			local hp = potential * rng:Range(0.05, 1.3)
			local o = RunnerSim.BossFight(n, u, hp)
			H.expect(o.victory == (potential >= hp), "victory iff potential >= hp")
			local interval = RunnerDefs.BossKillInterval(n)
			local t, left, alive, dt = 0, hp, n, 0.001
			while alive > 0 and left > 0 do
				left -= alive * u * dt
				t += dt
				if left > 0 and t + 1e-9 >= (n - alive + 1) * interval then
					alive -= 1
				end
			end
			if o.victory then
				H.near(t, o.duration, 0.003, "victory time")
				H.expect(math.abs(alive - o.survivors) <= 1, "survivors")
			else
				H.expect(alive == 0 and o.survivors == 0, "everyone dies")
				H.near(o.duration, n * interval, 1e-9, "defeat time")
			end
			-- BossHpAt / AliveAt agree with the outcome.
			H.near(RunnerSim.BossHpAt(o, 0), hp, 1e-9, "full hp at t=0")
			H.near(RunnerSim.BossHpAt(o, o.duration), hp - o.damageDealt, 1e-6, "hp at the end")
			H.near(RunnerSim.BossHpAt(o, o.duration + 5), o.hpLeft, 1e-9, "hp after the end")
			H.expect(RunnerSim.AliveAt(o, 0) == n, "alive at t=0")
			H.expect(RunnerSim.AliveAt(o, o.duration) == o.survivors, "alive at the end")
			local prevHp, prevAlive = math.huge, math.huge
			for i = 0, 40 do
				local at = o.duration * i / 40
				local h, a = RunnerSim.BossHpAt(o, at), RunnerSim.AliveAt(o, at)
				H.expect(h <= prevHp + 1e-9 and a <= prevAlive, "hp and units only go down")
				H.expect(a >= o.survivors and a <= n, "alive in range")
				prevHp, prevAlive = h, a
			end
			-- A point inside interval k: alive = n - (k - 1).
			if o.killed >= 2 then
				H.expect(RunnerSim.AliveAt(o, 1.5 * interval) == n - 1, "one kill after 1.5 intervals")
				local expectHp = hp - u * interval * n - u * (n - 1) * 0.5 * interval
				H.near(RunnerSim.BossHpAt(o, 1.5 * interval), expectHp, 1e-6, "hp after 1.5 intervals")
			end
		end
	end)

	H.test("BossHp = BOSS_HP_SHARE x max boss damage over ALL lane paths (brute force)", function()
		for area = 1, AREAS, 2 do
			for _, level in { 2, 9 } do
				local full = RunnerCourse.Generate(area, RunnerDefs.FirstLevelOfArea(area) + level)
				local short = table.clone(full)
				short.rows = table.move(full.rows, 1, 10, 1, {})
				local stats = refStats(area)
				local best = 0
				local choices = {}
				local function walk(i)
					if i > #short.rows then
						best = math.max(best, bossDamage(RunnerSim.Resolve(short, stats, choices)))
						return
					end
					for lane = 1, #short.rows[i].options do
						choices[i] = lane
						walk(i + 1)
					end
				end
				walk(1)
				local hp = RunnerSim.BossHp(short, stats)
				H.expect(hp == math.max(1, math.floor(RunnerDefs.BOSS_HP_SHARE * best)), "boss hp from the max path")
				H.expect(RunnerSim.BossHp(short, stats) == hp, "memoized")
				-- Capacity / payout bonus do not change it.
				local other = table.clone(stats)
				other.capacity, other.payoutBonus = 1, 0.5
				H.expect(RunnerSim.BossHp(short, other) == hp, "capacity-independent")
			end
		end
	end)

	H.test("Best-case path beats the boss on every benchmark course in all areas", function()
		for area = 1, AREAS do
			local stats = refStats(area)
			local first = RunnerDefs.FirstLevelOfArea(area)
			for level = first, first + 9 do
				local course = RunnerCourse.Generate(area, level)
				local best, choices = RunnerSim.BestCase(course, stats)
				H.expect(best.boss.victory, ("area %d level %d: best case loses to the boss"):format(area, level))
				H.expect(best.troops >= 1, "the avatar survives")
				H.expect(RunnerSim.Resolve(course, stats, choices).total == best.total, "Resolve(best) == BestCase")
				local bossLine
				for _, line in best.lines do
					if line.kind == "boss" then
						bossLine = line
					end
				end
				H.expect(bossLine.label == "Boss defeated!" and bossLine.value == course.boss.reward, "boss line")
				local share = bossLine.value / best.total
				H.expect(share >= 0.12 and share <= 0.4, ("boss reward share %.2f"):format(share))
			end
		end
	end)

	H.test("Only (near) best paths beat the boss: 200 random paths per course", function()
		local rng = Rng.new(2024)
		for _, course in courses do
			local stats = refStats(course.area)
			local hp = RunnerSim.BossHp(course, stats)
			local maxDamage = hp / RunnerDefs.BOSS_HP_SHARE
			for _ = 1, 200 do
				local r = RunnerSim.Resolve(course, stats, randomChoices(course, rng))
				local damage = bossDamage(r)
				H.expect(r.boss.hp == hp, "same boss hp")
				H.expect(damage <= maxDamage + 1 + 1e-6, "no path beats the maximum")
				if r.boss.victory then
					H.expect(damage >= hp, "victory implies damage >= boss hp")
				else
					H.expect(damage < hp, "defeat implies damage < boss hp")
					H.expect(r.troops == 0 and r.boss.killed == r.boss.units, "everyone dies")
					for _, line in r.lines do
						if line.kind == "boss" then
							H.expect(line.value == 0 and line.label == "Defeated by the boss", "defeat line")
							H.expect(line.detail:find("HP left") ~= nil, "hp left shown")
						elseif line.kind == "troops" then
							H.expect(line.value == 0, "no troop value on defeat")
						end
					end
				end
			end
		end
	end)

	H.test("Whole dollars: every payout line and total in every area", function()
		local rng = Rng.new(8)
		for _, course in courses do
			for _, bonus in { 0, 0.06, 0.135 } do
				local stats = table.clone(refStats(course.area))
				stats.payoutBonus = bonus
				local best = RunnerSim.BestCase(course, stats)
				for _, r in { best, RunnerSim.Resolve(course, stats, randomChoices(course, rng)) } do
					isWhole(r.total, "total")
					isWhole(r.carried, "carried")
					isWhole(r.collected, "collected")
					local sum = 0
					for _, line in r.lines do
						isWhole(line.value, line.label)
						sum += line.value
					end
					H.expect(sum == r.total, "lines sum to the total")
				end
			end
		end
	end)
end
