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
			H.expect(course.boss.hp > 0 and course.boss.reward > 0, "boss values")
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
						H.expect((opt.enemies or 0) >= 1 and (opt.hp or 0) > 0 and (opt.reward or 0) > 0, "squad values")
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
				H.expect(r.total <= best.total + 1e-6, ("random run %s beats best %s (area %d level %d)"):format(r.total, best.total, course.area, course.level))
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

	H.test("Troops never drop below 1", function()
		local rng = Rng.new(99)
		for _, course in courses do
			for _ = 1, 20 do
				local r = RunnerSim.Resolve(course, { startTroops = 1, damage = 0, capacity = 50 }, randomChoices(course, rng))
				H.expect(r.troops >= 1, "final troops")
				for _, oc in r.outcomes do
					H.expect(oc.troopsAfter >= 1 and oc.troopsBefore >= 1, "row troops")
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
		H.expect(r.troops >= 1 and r.total == r.total and r.total >= 0, "garbage stats")
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
					H.expect(#line.label > 0 and type(line.detail) == "string", "line text")
					if line.kind == "weapon" or line.kind == "damage" then
						gold += 1
					end
				end
				H.near(sum, r.total, 0.011, "sum of lines")
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

	H.test("Payout bonus multiplies the total", function()
		local course = courses[4]
		local stats = refStats(course.area)
		local best, choices = RunnerSim.BestCase(course, stats)
		local bonusStats = table.clone(stats)
		bonusStats.payoutBonus = 0.1
		local boosted = RunnerSim.Resolve(course, bonusStats, choices)
		H.near(boosted.total, best.total * 1.1, 0.02, "bonus total")
		H.expect(boosted.lines[#boosted.lines].kind == "bonus", "bonus line")
		local bestBoosted = RunnerSim.BestCase(course, bonusStats)
		H.near(bestBoosted.total, boosted.total, 0.02, "best case with bonus")
	end)

	H.test("Weapon gate at max tier still pays; firepower monotone", function()
		H.expect(RunnerSim.Firepower(10, 10, 1, 1) == 100, "firepower")
		H.expect(RunnerSim.Firepower(10, 10, 99, 1) == RunnerSim.Firepower(10, 10, RunnerDefs.MAX_WEAPON_TIER, 1), "tier clamp")
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
				options = { { lane = 1, x = 0, width = 11, op = "weapon", amount = 1, label = "WEAPON +1", tone = "weapon" } },
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
					elseif line.kind == "boss" then
						bossSum += tonumber(line.label:match("(%d+)%%")) or 0
					end
				end
			end
			H.expect(killed / enemies >= 0.6, ("area %d: only %.0f%% of soldiers beaten"):format(area, 100 * killed / enemies))
			H.expect(bossSum / 10 >= 50, ("area %d: enemy base only %.0f%%"):format(area, bossSum / 10))
		end
	end)
end
