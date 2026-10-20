return function(H)
	local Config = H.load("shared/Config")
	local Format = H.load("shared/Format")
	local Rng = H.load("shared/Rng")
	local Types = H.load("shared/Types")
	local Layout = H.load("shared/Layout")

	H.test("Config has four slots per area and the arena in area 2", function()
		for index, area in Config.Areas do
			H.expect(#area.slots == Config.SlotsPerArea, "area " .. index .. " slot count")
		end
		H.expect(Config.Areas[Config.ArenaArea].slots[4].id == Config.ArenaSlotId, "arena slot")
		H.expect(Config.Areas[Config.ArenaArea].slots[4].kind == "arena", "arena kind")
	end)

	H.test("Format.Money matches the reference style", function()
		H.expect(Format.Money(0) == "$0.00", Format.Money(0))
		H.expect(Format.Money(1562.5) == "$1,562.50", Format.Money(1562.5))
		H.expect(Format.Money(215) == "$215.00", Format.Money(215))
		H.expect(Format.Money(2500000) == "$2.5M", Format.Money(2500000))
	end)

	H.test("Rng is deterministic", function()
		local a, b = Rng.new(99), Rng.new(99)
		for _ = 1, 50 do
			H.expect(a:Next() == b:Next(), "same sequence")
		end
		local c = Rng.new(7)
		for _ = 1, 200 do
			local v = c:Int(1, 6)
			H.expect(v >= 1 and v <= 6, "int range")
		end
	end)

	H.test("Default data reconciles without duplicating starter items", function()
		local data = Types.DefaultData()
		data.inventory = { ["5"] = { id = "w_pistol", rarity = 2, level = 3 } }
		data.equipped = { weapon = "5" }
		data.runner.upgrades = nil
		local fixed = Types.Reconcile(data)
		H.expect(fixed.runner.upgrades.troops == 0, "upgrades restored")
		H.expect(fixed.inventory["1"] == nil, "starter not re-added")
		H.expect(fixed.equipped.weapon == "5", "equipped kept")
	end)

	H.test("Layout areas do not overlap", function()
		H.expect(Layout.AREA_PITCH > Layout.AREA_SIZE, "pitch > size")
		H.expect(Layout.AreaAt(Layout.AreaCenter(3), #Config.Areas) == 3, "AreaAt")
	end)
end
