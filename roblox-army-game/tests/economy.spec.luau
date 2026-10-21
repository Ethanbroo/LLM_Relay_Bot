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

	-- Owner request: every price / payout is a whole number of dollars.
	local function isWhole(n: number, what: string)
		H.expect(type(n) == "number" and math.floor(n) == n, what .. " is not whole dollars: " .. tostring(n))
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
			H.near(Economy.RunBenchmark(area), d.total / 10, 0.5, "R = B10 / 10 (whole dollars)")
			isWhole(Economy.RunBenchmark(area), "R")
			isWhole(d.total, "B10")
			local R = Economy.RunBenchmark(area)
			H.expect(R > previous, "R not increasing at area " .. area)
			previous = R
		end
		-- Beyond the last area the benchmark is extrapolated (combat levels use it).
		H.expect(Economy.RunBenchmark(AREAS + 3) > Economy.RunBenchmark(AREAS + 2), "extrapolated R")
	end)

	H.test("Benchmark payouts are the exact BestCase of the reference player", function()
		for _, area in { 1, 4, AREAS } do
			local stats = Economy.BenchmarkStats(area)
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
					isWhole(gate, "gate")
					isWhole(Economy.AreaUnlockTotal(area), "area total")
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
					isWhole(price, slotDef.id)
					if slotDef.kind == "revenue" and count < 3 then
						sum += price
						count += 1
					end
				end
				H.expect(sum == Economy.PriceBenchmark(area), "three sections = eased B10 exactly in area " .. area)
				H.near(
					Economy.PriceBenchmark(area),
					EconomyDefs.ProgressEase * Economy.TenRunBenchmark(area),
					0.5,
					"price benchmark = B10 x ProgressEase"
				)
			end
			H.near(
				Economy.SlotUnlockPrice(Config.ArenaSlotId),
				EconomyDefs.Slots.ArenaShare * Economy.PriceBenchmark(Config.ArenaArea),
				0.5,
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
					isWhole(price, slotDef.id)
					if slotDef.kind == "revenue" and count < 3 then
						count += 1
						H.expect(price == Economy.PriceBenchmark(area), "section = eased B10")
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
						-- Whole-dollar sales stretch the 4-5.8 s base interval by
						-- (rounded / exact) <= 2 (only when a sale is worth < $1.50).
						local baseInterval = EconomyDefs.Sections.SaleInterval[math.min(
							tonumber(slotDef.id:match("s(%d+)$")) :: number,
							#EconomyDefs.Sections.SaleInterval
						)]
						H.expect(
							s.saleInterval >= baseInterval * 0.66 - 1e-9 and s.saleInterval <= baseInterval * 2 + 1e-9,
							("%s sale interval %.2f"):format(slotDef.id, s.saleInterval)
						)
						isWhole(s.stockCapacity, "stock")
						H.expect(s.stockCapacity >= 5, "stock >= 5")
						finitePositive(s.incomePerSale, "income per sale")
						isWhole(s.incomePerSale, "income per sale")
						H.expect(s.incomePerSale >= 1, "at least $1 per sale")
						H.near(s.incomePerSecond, s.incomePerSale / s.saleInterval, 1e-9, "income per second")
						local payback = Economy.SlotUnlockPrice(slotDef.id) / s.incomePerSecond / 60
						-- Prices are eased (EconomyDefs.ProgressEase), so payback is that much faster.
						local ease = EconomyDefs.ProgressEase
						H.expect(
							payback >= 25 * ease and payback <= 45,
							("%s payback %.1f min"):format(slotDef.id, payback)
						)
						passive += s.incomePerSecond
						-- Upgrades: +20% income, +10% stock per level, nil at max.
						-- (incomePerSecond is the exact design rate: rounding a sale to
						-- whole dollars only stretches the interval)
						local s10 = Economy.SectionStats(slotDef.id, Economy.SectionMaxLevel)
						H.near(
							s10.incomePerSecond,
							s.incomePerSecond * (1 + 0.2 * (Economy.SectionMaxLevel - 1)),
							1e-9,
							"level 10 income"
						)
						isWhole(s10.incomePerSale, "level 10 income per sale")
						H.expect(s10.stockCapacity > s.stockCapacity, "level 10 stock")
						local prev = 0
						for level = 1, Economy.SectionMaxLevel - 1 do
							local p = Economy.SectionUpgradePrice(slotDef.id, level)
							finitePositive(p, "upgrade price")
							isWhole(p :: number, "section upgrade price")
							H.expect((p :: number) > prev, "upgrade price increasing")
							prev = p :: number
						end
						H.expect(Economy.SectionUpgradePrice(slotDef.id, Economy.SectionMaxLevel) == nil, "nil at max")
					end
				end
				local share = passive / (Economy.RunBenchmark(area) / T)
				H.expect(share >= 0.25 and share <= 0.4, ("area %d passive share %.2f"):format(area, share))
			end
		end)
		-- Reference passive rate adds up all areas.
		H.expect(Economy.ReferencePassiveRate(3) > Economy.ReferencePassiveRate(2), "reference passive rate")
	end)

	H.test("Sections: a full stock lasts the designed time at every level (whole-dollar sales)", function()
		local d = EconomyDefs.Sections
		for area = 1, AREAS do
			for slotIndex, slotDef in Config.Areas[area].slots do
				if slotDef.kind ~= "revenue" then
					continue
				end
				local i = math.min(slotIndex, #d.SaleInterval)
				local prevDuration, prevIncome = 0, 0
				for level = 1, Economy.SectionMaxLevel do
					local s = Economy.SectionStats(slotDef.id, level)
					-- Designed: StockCapacity x SaleInterval at level 1, +10% per level.
					local designed = d.StockCapacity[i] * (1 + d.StockPerLevel * (level - 1)) * d.SaleInterval[i]
					local duration = s.stockCapacity * s.saleInterval
					H.expect(
						math.abs(duration / designed - 1) <= 0.1,
						("%s L%d: full stock lasts %.0f s, designed %.0f s"):format(
							slotDef.id,
							level,
							duration,
							designed
						)
					)
					isWhole(s.stockCapacity, "stock")
					-- Upgrades raise both the income and how long a full stock lasts.
					H.expect(s.incomePerSecond > prevIncome, ("%s L%d income rises"):format(slotDef.id, level))
					H.expect(duration > prevDuration, ("%s L%d stock duration rises"):format(slotDef.id, level))
					prevDuration, prevIncome = duration, s.incomePerSecond
				end
			end
		end
	end)

	H.test("Employee cash price = 20 runs of R (both scaling rules)", function()
		for area = 1, AREAS do
			local slotId = Config.Areas[area].slots[1].id
			H.near(
				Economy.EmployeeCashPrice(slotId, AREAS),
				Config.Employee.RunsEquivalent * Economy.RunBenchmark(area),
				0.5,
				"SectionArea"
			)
			isWhole(Economy.EmployeeCashPrice(slotId, AREAS), "employee price")
		end
		withConfig(Config.Employee, "PriceScalesWith", "PlayerArea", function()
			H.near(Economy.EmployeeCashPrice("a1s1", 5), 20 * Economy.RunBenchmark(5), 0.5, "PlayerArea")
		end)
	end)

	H.test("Boosts: value / coin price >= 1.25 (whole $, rounded down) and prices scale with area", function()
		for _, boost in Config.Boosts do
			local previous = 0
			for area = 1, AREAS do
				local value = Economy.BoostValue(boost.id, area)
				local price = Economy.BoostCoinPrice(boost.id, area)
				finitePositive(value, "boost value")
				finitePositive(price, "boost price")
				isWhole(price, "boost price")
				isWhole(value, "boost value")
				H.expect(value / price >= 1 / (1 - Config.CoinRoute.NetGainShare) - 1e-9, boost.id .. " ratio >= 1.25")
				H.near(value / price, 1 / (1 - Config.CoinRoute.NetGainShare), 0.005, boost.id .. " ratio ~ 1.25")
				H.expect(price == math.floor((1 - Config.CoinRoute.NetGainShare) * value + 1e-6), "rounded down")
				H.expect(price > previous, "boost price increases with area")
				previous = price
			end
		end
		-- Definitions.
		local T = Economy.RunDurationSeconds()
		H.near(Economy.BoostValue("x2_15", 2), 15 * 60 / T * Economy.RunBenchmark(2), 0.5, "2x value")
		H.near(Economy.BoostValue("skip_4", 3), 4 * 3600 * Economy.ReferencePassiveRate(3), 0.5, "skip value")
	end)

	H.test("TimeSkipPayout: employee pays every sale, no employee stops at stock", function()
		local stats = Economy.SectionStats("a1s1", 1)
		local seconds = 3600
		local sales = math.floor(seconds / stats.saleInterval + 1e-9)
		H.expect(sales > stats.stockCapacity, "1h has more sales than one stock (test premise)")

		-- With an employee: all sales, no stock used.
		local total, per = Economy.TimeSkipPayout({ a1s1 = { level = 1, stock = 3, employee = true } }, seconds)
		H.near(total, sales * stats.incomePerSale, 0.011, "employee pays all sales")
		H.expect(per.a1s1.sales == sales and per.a1s1.stockUsed == 0, "employee uses no stock")
		H.near(per.a1s1.paid, total, 1e-9, "per slot paid")

		-- Without one: capped by floor(stock), and that stock is reported.
		total, per = Economy.TimeSkipPayout({ a1s1 = { level = 1, stock = 7.9, employee = false } }, seconds)
		H.near(total, 7 * stats.incomePerSale, 0.011, "no employee pays only the stock")
		H.expect(per.a1s1.sales == 7 and per.a1s1.stockUsed == 7, "stock used = 7")

		-- Short skip below the stock: every sale paid either way.
		total, per =
			Economy.TimeSkipPayout({ a1s1 = { level = 1, stock = 25, employee = false } }, stats.saleInterval * 3)
		H.expect(per.a1s1.sales == 3 and per.a1s1.stockUsed == 3, "3 sales from stock")
		H.near(total, 3 * stats.incomePerSale, 0.011, "3 sales")

		-- Empty stock, the arena slot, unknown slots and bad input pay nothing.
		total, per = Economy.TimeSkipPayout(
			{
				a1s1 = { level = 1, stock = 0, employee = false },
				a2s4 = { level = 1, stock = 50, employee = true },
				nope = { level = 1, stock = 50, employee = true },
			} :: any,
			seconds
		)
		H.expect(total == 0 and next(per) == nil, "nothing to pay")
		H.expect((Economy.TimeSkipPayout({ a1s1 = { level = 1, stock = 5, employee = true } }, -5)) == 0, "negative")
		H.expect((Economy.TimeSkipPayout({ a1s1 = { level = 1, stock = 5, employee = true } }, 0 / 0)) == 0, "nan")
		H.expect((Economy.TimeSkipPayout(nil :: any, 3600)) == 0, "nil sections")

		-- Several sections and levels: total = sum of rounded per-slot amounts, all in cents.
		local sections = {
			a1s1 = { level = 3, stock = 10, employee = false },
			a1s2 = { level = 2, stock = 4, employee = true },
			a1s3 = { level = 1, stock = 30, employee = false },
		}
		total, per = Economy.TimeSkipPayout(sections, 7 * 3600 + 17)
		local sum = 0
		for slotId, slot in per do
			isWhole(slot.paid, slotId .. " paid")
			sum += slot.paid
		end
		isWhole(total, "total")
		H.near(total, sum, 1e-6, "total = sum of slots")
		H.expect(sections.a1s1.stock == 10, "pure: input stock unchanged")
	end)

	H.test("Time skip: preview of one unstaffed shop vs every shop staffed", function()
		-- Area-1 player with one shop and no employee: pays only its stock.
		local one = { a1s1 = { level = 1, stock = Economy.SectionStats("a1s1", 1).stockCapacity, employee = false } }
		local preview = Economy.TimeSkipPayout(one, 3600)
		local reference = Economy.BoostValue("skip_1", 1)
		H.expect(preview > 0 and preview < reference, "one shop pays less than the reference")
		H.near(Economy.TimeSkipValue("skip_1", 1, preview), preview, 1e-9, "value = preview below the cap")
		H.expect(not Economy.TimeSkipWorthOffering("skip_1", 1, preview), "not worth offering")

		-- All four area-1 shops with employees: pays the reference (cap).
		local all = {}
		for _, slotDef in Config.Areas[1].slots do
			if slotDef.kind == "revenue" then
				all[slotDef.id] = { level = 1, stock = 0, employee = true }
			end
		end
		local full = Economy.TimeSkipPayout(all, 3600)
		H.near(full, reference, reference * 0.01 + 0.05, "full staff ~ reference")
		H.near(Economy.TimeSkipValue("skip_1", 1, full * 10), reference, 1e-9, "capped at the reference")
		H.expect(Economy.TimeSkipWorthOffering("skip_1", 1, full), "worth offering")
		H.expect(Economy.TimeSkipCoinPrice("x2_15", 1, 100) == math.huge, "not a time skip")
		H.expect(Economy.TimeSkipCoinPrice("nope", 1, 100) == math.huge, "unknown boost")
	end)

	H.test("Time skip cash price never exceeds 0.8 x preview (always +25% back)", function()
		local share = 1 - Config.CoinRoute.NetGainShare
		local previews = { 0, 0.01, 0.37, 1, 1.13, 7.77, 24, 99.99, 123.45, 1000.01, 3986.4, 1e7 }
		for area = 1, AREAS do
			for _, boost in Config.Boosts do
				if boost.kind ~= "timeskip" then
					continue
				end
				for _, preview in previews do
					local price = Economy.TimeSkipCoinPrice(boost.id, area, preview)
					H.expect(price >= 0 and price == price, "price is a number")
					isWhole(price, "time skip price")
					H.expect(
						price <= share * preview + 1e-6,
						("%s %g: %g > 0.8 x preview"):format(boost.id, preview, price)
					)
					H.expect(price <= Economy.BoostCoinPrice(boost.id, area) + 1e-6, "never above the reference price")
					if preview >= 1 then
						local value = Economy.TimeSkipValue(boost.id, area, preview)
						isWhole(value, "time skip value")
						H.expect(value == math.floor(math.min(preview, Economy.BoostValue(boost.id, area))), "value")
						H.expect(price == math.floor(share * value + 1e-6), "0.8 V rounded down")
						if price > 0 then
							H.expect(value / price >= 1 / share - 1e-9, "value / price >= 1.25")
						end
					end
				end
			end
		end
		-- Real sections: price from the preview, payout >= 1.25 x price.
		local sections = { a1s1 = { level = 1, stock = 25, employee = false } }
		local preview = Economy.TimeSkipPayout(sections, 3600)
		local price = Economy.TimeSkipCoinPrice("skip_1", 1, preview)
		H.expect(preview >= price / share - 1e-6, "pays back at least +25%")
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
				0.5,
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
				isWhole(p :: number, kind)
				H.expect((p :: number) > previous, kind .. " price increasing")
				previous = p :: number
				H.expect(#Economy.RunnerUpgradeEffect(kind, level) > 0, "effect text")
			end
			H.expect(Economy.RunnerUpgradePrice(kind, Economy.RunnerUpgradeMaxLevel) == nil, "nil at max")
			H.expect(Economy.RunnerUpgradeEffect(kind, Economy.RunnerUpgradeMaxLevel) == "MAX", "MAX text")
		end
		H.near(
			Economy.RunnerUpgradePrice("troops", 0) :: number,
			0.4 * Economy.RunBenchmark(1) * EconomyDefs.ProgressEase,
			0.5,
			"level 0 price"
		)
		local base = Economy.RunnerStats({ troops = 0, damage = 0, capacity = 0 }, 1)
		local one = Economy.RunnerStats({ troops = 1, damage = 1, capacity = 1 }, 1)
		H.expect(base.startTroops == 3 and one.startTroops == 4, "+1 troop")
		H.near(one.damage / base.damage, 1.04, 1e-6, "+4% damage")
		-- Owner request: the coin (carry capacity) upgrade is +20% per level.
		H.near(one.capacity / base.capacity, 1.2, 0.01, "+20% capacity (whole dollars)")
		H.expect(Economy.RunnerUpgradeEffect("capacity", 0) == "+20% carry", "capacity effect text")
		isWhole(one.capacity, "capacity")
		-- Garbage upgrade levels are clamped.
		local junk = Economy.RunnerStats({ troops = -5, damage = 0 / 0, capacity = 1e9 } :: any, 1)
		H.expect(junk.startTroops == 3 and junk.damage == base.damage, "clamped")
		H.expect(Economy.RunnerStats(nil :: any, 1).startTroops == 3, "nil upgrades")
		-- The reference bag keeps up with the area so the bag stays relevant.
		for area = 1, AREAS do
			local bench = Economy.BenchmarkStats(area)
			local ref = Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
			local benchBag = EconomyDefs.Runner.BaseCapacity * EconomyDefs.AreaScale ^ (area - 1)
			H.near(bench.capacity, benchBag, 0.5, "benchmark capacity")
			H.expect(ref.capacity == bench.capacity, "the benchmark is the reference player")
			isWhole(ref.capacity, "reference capacity")
			H.expect(bench.startTroops == ref.startTroops and bench.damage == ref.damage, "same troops / damage")
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
				0.5,
				"barrier reward"
			)
		end
		local previous = 0
		for level = 1, 60 do
			local r = Economy.CombatLevelReward(level)
			finitePositive(r, "combat reward")
			isWhole(r, "combat reward")
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
				isWhole(p :: number, "equipment price")
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
		-- Whole dollars everywhere (owner request: no cents).
		for area = 1, AREAS do
			for _, slotDef in Config.Areas[area].slots do
				isWhole(Economy.SlotUnlockPrice(slotDef.id), slotDef.id .. " unlock")
				isWhole(Economy.EmployeeCashPrice(slotDef.id, area), slotDef.id .. " employee")
				for level = 1, Economy.SectionMaxLevel do
					local st = Economy.SectionStats(slotDef.id, level)
					if slotDef.kind == "revenue" then
						isWhole(st.incomePerSale, slotDef.id .. " income per sale L" .. level)
					end
					local up = Economy.SectionUpgradePrice(slotDef.id, level)
					if up then
						isWhole(up, slotDef.id .. " upgrade L" .. level)
					end
				end
			end
			isWhole(Economy.GatePrice(area), "gate " .. area)
			isWhole(Economy.AreaUnlockTotal(area), "area total " .. area)
			isWhole(Economy.RunBenchmark(area), "R " .. area)
			isWhole(Economy.TenRunBenchmark(area), "B10 " .. area)
			for _, run in Economy.BenchmarkDetails(area).runs do
				isWhole(run.payout, "benchmark run")
			end
			for _, v in Config.Vehicles do
				isWhole(Economy.VehicleCoinPrice(v.id, area), v.id)
			end
			for _, boost in Config.Boosts do
				isWhole(Economy.BoostValue(boost.id, area), boost.id .. " value")
				isWhole(Economy.BoostCoinPrice(boost.id, area), boost.id .. " price")
			end
			local stats = Economy.RunnerStats(Economy.ReferenceUpgrades(area), area)
			isWhole(stats.capacity, "capacity")
		end
		for _, kind in { "troops", "damage", "capacity" } do
			for level = 0, Economy.RunnerUpgradeMaxLevel - 1 do
				isWhole(Economy.RunnerUpgradePrice(kind, level) :: number, kind .. " upgrade")
			end
		end
		for level = 1, 80 do
			isWhole(Economy.CombatLevelReward(level), "combat reward " .. level)
		end
		for rarity, def in Config.Rarities do
			for level = 1, def.maxLevel - 1 do
				isWhole(Economy.EquipmentUpgradePrice(rarity, level) :: number, "equipment upgrade")
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
