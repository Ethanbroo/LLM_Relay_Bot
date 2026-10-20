--[[
	economy.spec.luau — tests for Shared/Economy (prices, incomes, benchmark).
		lune run tests/run economy
]]

return function(H)
	local Config = H.load("shared/Config")
	local Economy = H.load("shared/Economy")
	local EconomyDefs = H.load("shared/EconomyDefs")
	local RunnerCourse = H.load("shared/Runner/RunnerCourse")
	local RunnerDefs = H.load("shared/Runner/RunnerDefs")
	local RunnerSim = H.load("shared/Runner/RunnerSim")

	local AREAS = #Config.Areas

	local function finitePositive(n: any, what: string)
		H.expect(type(n) == "number", what .. " is not a number")
		H.expect(n == n and n ~= math.huge and n > 0, what .. " = " .. tostring(n))
	end

	local function isCents(n: number, what: string)
		H.near(n * 100, math.floor(n * 100 + 0.5), 1e-6, what .. " not rounded to cents")
	end

	-- Runs `fn` with a temporary Config value, then restores it and the memo.
	local function withConfig(tbl: any, key: string, value: any, fn: () -> ())
		local saved = tbl[key]
		tbl[key] = value
		Economy._ClearCache()
		local ok, err = pcall(fn)
		tbl[key] = saved
		Economy._ClearCache()
		if not ok then
			error(err, 0)
		end
	end

	H.test("Benchmark: 10 runs, B10 = sum, R = B10 / 10, R strictly increasing", function()
		local previous = 0
		for area = 1, AREAS do
			local d = Economy.BenchmarkDetails(area)
			H.expect(#d.runs == Config.Benchmark.Runs, "10 runs")
			H.expect(d.upgradeLevel == (area - 1) * Config.Benchmark.ReferenceUpgradeLevelsPerArea, "upgrade level")
			local sum = 0
			for i, run in d.runs do
				H.expect(run.level == 1 + 10 * (area - 1) + i - 1, "benchmark level")
				finitePositive(run.payout, "run payout")
				sum += run.payout
			end
			H.near(d.total, sum, 0.01, "B10 = sum")
			H.near(Economy.TenRunBenchmark(area), d.total, 1e-9, "TenRunBenchmark")
			H.near(Economy.RunBenchmark(area), d.total / 10, 0.006, "R = B10 / 10")
			local R = Economy.RunBenchmark(area)
			H.expect(R > previous, "R not increasing at area " .. area)
			previous = R
		end
		-- Beyond the last area the benchmark is extrapolated (combat levels use it).
		H.expect(Economy.RunBenchmark(AREAS + 3) > Economy.RunBenchmark(AREAS + 2), "extrapolated R")
	end)

	H.test("Benchmark payouts are the exact BestCase of the reference player", function()
		for _, area in { 1, 4, AREAS } do
			local stats = Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
			local d = Economy.BenchmarkDetails(area)
			for _, run in d.runs do
				local best = RunnerSim.BestCase(RunnerCourse.Generate(area, run.level), stats)
				H.expect(best.total == run.payout, "payout mismatch")
			end
		end
	end)

	H.test("Memoized values are stable and BenchmarkDetails returns a copy", function()
		local a = Economy.RunBenchmark(3)
		local d = Economy.BenchmarkDetails(3)
		d.runs[1].payout = -1
		d.total = -1
		H.expect(Economy.BenchmarkDetails(3).total ~= -1, "cache was mutated")
		Economy._ClearCache()
		H.expect(Economy.RunBenchmark(3) == a, "deterministic after clearing")
	end)

	H.test("Gate prices strictly increasing; area totals strictly increasing (both modes)", function()
		for _, mode in { "CombinedSections", "EachSection" } do
			withConfig(Config.Benchmark, "Mode", mode, function()
				local prevGate, prevTotal = 0, 0
				for area = 1, AREAS do
					local gate = Economy.GatePrice(area)
					finitePositive(gate, "gate")
					isCents(gate, "gate")
					H.expect(gate > prevGate, mode .. ": gate not increasing at " .. area)
					local total = Economy.AreaUnlockTotal(area)
					H.expect(total > prevTotal, mode .. ": area total not increasing at " .. area)
					prevGate, prevTotal = gate, total
				end
				H.expect(Economy.GatePrice(AREAS + 4) > Economy.GatePrice(AREAS + 3), "gates beyond last area")
			end)
		end
	end)

	H.test("CombinedSections: first three revenue sections cost B10 together", function()
		withConfig(Config.Benchmark, "Mode", "CombinedSections", function()
			for area = 1, AREAS do
				local sum, count = 0, 0
				for _, slotDef in Config.Areas[area].slots do
					local price = Economy.SlotUnlockPrice(slotDef.id)
					finitePositive(price, slotDef.id)
					isCents(price, slotDef.id)
					if slotDef.kind == "revenue" and count < 3 then
						sum += price
						count += 1
					end
				end
				H.near(sum, Economy.TenRunBenchmark(area), 0.03, "three sections = B10 in area " .. area)
			end
			H.near(
				Economy.SlotUnlockPrice(Config.ArenaSlotId),
				EconomyDefs.Slots.ArenaShare * Economy.TenRunBenchmark(Config.ArenaArea),
				0.01,
				"arena price"
			)
		end)
	end)

	H.test("EachSection: each of the first three revenue sections costs B10", function()
		withConfig(Config.Benchmark, "Mode", "EachSection", function()
			for area = 1, AREAS do
				local count = 0
				for _, slotDef in Config.Areas[area].slots do
					local price = Economy.SlotUnlockPrice(slotDef.id)
					finitePositive(price, slotDef.id)
					if slotDef.kind == "revenue" and count < 3 then
						count += 1
						H.near(price, Economy.TenRunBenchmark(area), 0.01, "section = B10")
					end
				end
			end
		end)
	end)

	H.test("Unknown ids can never be bought", function()
		H.expect(Economy.SlotUnlockPrice("nope") == math.huge, "slot")
		H.expect(Economy.BoostCoinPrice("nope", 1) == math.huge, "boost")
		H.expect(Economy.VehicleCoinPrice("nope", 1) == math.huge, "vehicle")
		H.expect(Economy.SectionUpgradePrice("nope", 1) == nil, "section upgrade")
		H.expect(Economy.SectionUpgradePrice(Config.ArenaSlotId, 1) == nil, "arena has no upgrade")
		H.expect(Economy.SectionStats(Config.ArenaSlotId, 1).incomePerSecond == 0, "arena has no income")
		H.expect(Economy.RunnerUpgradePrice("speed", 0) == nil, "unknown runner upgrade")
	end)

	H.test("Sections: timings, stock, payback and passive share", function()
		local T = Economy.RunDurationSeconds()
		withConfig(Config.Benchmark, "Mode", "CombinedSections", function()
			for area = 1, AREAS do
				local passive = 0
				for _, slotDef in Config.Areas[area].slots do
					if slotDef.kind == "revenue" then
						local s = Economy.SectionStats(slotDef.id, 1)
						H.expect(s.saleInterval >= 4 and s.saleInterval <= 6, "sale interval")
						H.expect(s.stockCapacity >= 25 and s.stockCapacity <= 40, "stock")
						finitePositive(s.incomePerSale, "income per sale")
						isCents(s.incomePerSale, "income per sale")
						H.near(s.incomePerSecond, s.incomePerSale / s.saleInterval, 1e-9, "income per second")
						local payback = Economy.SlotUnlockPrice(slotDef.id) / s.incomePerSecond / 60
						H.expect(payback >= 25 and payback <= 45, ("%s payback %.1f min"):format(slotDef.id, payback))
						passive += s.incomePerSecond
						-- Upgrades: +20% income, +10% stock per level, nil at max.
						local s10 = Economy.SectionStats(slotDef.id, Economy.SectionMaxLevel)
						H.near(
							s10.incomePerSale,
							s.incomePerSale * (1 + 0.2 * (Economy.SectionMaxLevel - 1)),
							0.02,
							"level 10 income"
						)
						H.expect(s10.stockCapacity > s.stockCapacity, "level 10 stock")
						local prev = 0
						for level = 1, Economy.SectionMaxLevel - 1 do
							local p = Economy.SectionUpgradePrice(slotDef.id, level)
							finitePositive(p, "upgrade price")
							H.expect((p :: number) > prev, "upgrade price increasing")
							prev = p :: number
						end
						H.expect(Economy.SectionUpgradePrice(slotDef.id, Economy.SectionMaxLevel) == nil, "nil at max")
					end
				end
				local share = passive / (Economy.RunBenchmark(area) / T)
				H.expect(share >= 0.25 and share <= 0.35, ("area %d passive share %.2f"):format(area, share))
			end
		end)
		-- Reference passive rate adds up all areas.
		H.expect(Economy.ReferencePassiveRate(3) > Economy.ReferencePassiveRate(2), "reference passive rate")
	end)

	H.test("Employee cash price = 20 runs of R (both scaling rules)", function()
		for area = 1, AREAS do
			local slotId = Config.Areas[area].slots[1].id
			H.near(
				Economy.EmployeeCashPrice(slotId, AREAS),
				Config.Employee.RunsEquivalent * Economy.RunBenchmark(area),
				0.01,
				"SectionArea"
			)
		end
		withConfig(Config.Employee, "PriceScalesWith", "PlayerArea", function()
			H.near(Economy.EmployeeCashPrice("a1s1", 5), 20 * Economy.RunBenchmark(5), 0.01, "PlayerArea")
		end)
	end)

	H.test("Boosts: value / coin price = 1.25 and prices scale with area", function()
		for _, boost in Config.Boosts do
			local previous = 0
			for area = 1, AREAS do
				local value = Economy.BoostValue(boost.id, area)
				local price = Economy.BoostCoinPrice(boost.id, area)
				finitePositive(value, "boost value")
				finitePositive(price, "boost price")
				isCents(price, "boost price")
				H.near(value / price, 1 / (1 - Config.CoinRoute.NetGainShare), 0.005, boost.id .. " ratio")
				H.expect(price > previous, "boost price increases with area")
				previous = price
			end
		end
		-- Definitions.
		local T = Economy.RunDurationSeconds()
		H.near(Economy.BoostValue("x2_15", 2), 15 * 60 / T * Economy.RunBenchmark(2), 0.01, "2x value")
		H.near(Economy.BoostValue("skip_4", 3), 4 * 3600 * Economy.ReferencePassiveRate(3), 0.01, "skip value")
	end)

	H.test("Vehicles: coin price follows nearby gate prices", function()
		for _, v in Config.Vehicles do
			local previous = 0
			for area = 1, AREAS do
				local price = Economy.VehicleCoinPrice(v.id, area)
				finitePositive(price, v.id)
				H.expect(price >= previous, "vehicle price never drops")
				previous = price
			end
			H.near(
				Economy.VehicleCoinPrice(v.id, 1),
				v.priceFactor * Economy.GatePrice(math.max(1, v.area)),
				0.01,
				"definition"
			)
		end
	end)

	H.test("Runner upgrades: small steps, rising prices, nil at max", function()
		for _, kind in { "troops", "damage", "capacity" } do
			local previous = 0
			for level = 0, Economy.RunnerUpgradeMaxLevel - 1 do
				local p = Economy.RunnerUpgradePrice(kind, level)
				finitePositive(p, kind .. " price")
				isCents(p :: number, kind)
				H.expect((p :: number) > previous, kind .. " price increasing")
				previous = p :: number
				H.expect(#Economy.RunnerUpgradeEffect(kind, level) > 0, "effect text")
			end
			H.expect(Economy.RunnerUpgradePrice(kind, Economy.RunnerUpgradeMaxLevel) == nil, "nil at max")
			H.expect(Economy.RunnerUpgradeEffect(kind, Economy.RunnerUpgradeMaxLevel) == "MAX", "MAX text")
		end
		H.near(Economy.RunnerUpgradePrice("troops", 0) :: number, 0.4 * Economy.RunBenchmark(1), 0.01, "level 0 price")
		local base = Economy.RunnerStats({ troops = 0, damage = 0, capacity = 0 }, 1)
		local one = Economy.RunnerStats({ troops = 1, damage = 1, capacity = 1 }, 1)
		H.expect(base.startTroops == 3 and one.startTroops == 4, "+1 troop")
		H.near(one.damage / base.damage, 1.04, 1e-6, "+4% damage")
		H.near(one.capacity / base.capacity, 1.06, 0.001, "+6% capacity")
		-- Garbage upgrade levels are clamped.
		local junk = Economy.RunnerStats({ troops = -5, damage = 0 / 0, capacity = 1e9 } :: any, 1)
		H.expect(junk.startTroops == 3 and junk.damage == base.damage, "clamped")
		H.expect(Economy.RunnerStats(nil :: any, 1).startTroops == 3, "nil upgrades")
		-- Base capacity keeps up with the area so the bag stays relevant.
		for area = 1, AREAS do
			local ref = Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
			H.near(
				ref.capacity,
				EconomyDefs.Runner.BaseCapacity * EconomyDefs.AreaScale ^ (area - 1),
				0.02,
				"reference capacity"
			)
		end
	end)

	H.test("Combat: barrier levels and strictly increasing rewards", function()
		H.expect(Economy.BarrierCombatLevel(1) == 2, "area 1 barrier is level 2")
		local previousBarrier = 0
		for area = 1, AREAS do
			local b = Economy.BarrierCombatLevel(area)
			H.expect(b > previousBarrier, "barrier levels increase")
			previousBarrier = b
			-- A barrier pays about 3 runs of its area.
			H.near(
				Economy.CombatLevelReward(b),
				EconomyDefs.Combat.RewardRuns * Economy.RunBenchmark(area),
				0.02,
				"barrier reward"
			)
		end
		local previous = 0
		for level = 1, 60 do
			local r = Economy.CombatLevelReward(level)
			finitePositive(r, "combat reward")
			isCents(r, "combat reward")
			H.expect(r > previous, "combat reward increasing at level " .. level)
			previous = r
		end
	end)

	H.test("Equipment upgrade prices: rise with level and rarity, nil at max", function()
		for rarity, def in Config.Rarities do
			local previous = 0
			for level = 1, def.maxLevel - 1 do
				local p = Economy.EquipmentUpgradePrice(rarity, level)
				finitePositive(p, "equipment price")
				H.expect((p :: number) > previous, "equipment price increasing")
				previous = p :: number
			end
			H.expect(Economy.EquipmentUpgradePrice(rarity, def.maxLevel) == nil, "nil at max")
			if rarity > 1 then
				H.expect(
					(Economy.EquipmentUpgradePrice(rarity, 1) :: number)
						> Economy.EquipmentUpgradePrice(rarity - 1, 1) :: number,
					"rarity factor"
				)
			end
		end
	end)

	H.test("Every price and income is finite and positive", function()
		H.expect(Economy.RunDurationSeconds() > RunnerDefs.BOSS_DISTANCE / RunnerDefs.RUN_SPEED, "run duration")
		for area = 1, AREAS do
			for _, slotDef in Config.Areas[area].slots do
				finitePositive(Economy.SlotUnlockPrice(slotDef.id), slotDef.id)
				finitePositive(Economy.EmployeeCashPrice(slotDef.id, area), "employee")
			end
			finitePositive(Economy.GatePrice(area), "gate")
			finitePositive(Economy.ReferencePassiveRate(area), "passive")
			for _, v in Config.Vehicles do
				finitePositive(Economy.VehicleCoinPrice(v.id, area), "vehicle")
			end
		end
		-- Silly inputs do not crash and stay sane.
		finitePositive(Economy.GatePrice(0), "gate 0")
		finitePositive(Economy.GatePrice(0 / 0), "gate nan")
		finitePositive(Economy.BoostCoinPrice("x2_15", 999), "boost area 999")
		finitePositive(Economy.CombatLevelReward(-3), "combat level -3")
		finitePositive(Economy.EquipmentUpgradePrice(0, 1) :: number, "rarity 0")
	end)
end
