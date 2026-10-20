--!strict
--[[
	Config
	Global, requirement-level configuration for Army Run Tycoon.

	Every value tagged [REQUIREMENT] comes straight from the design brief.
	Every value tagged [PROPOSAL] is a design assumption that can be changed
	here without touching gameplay code (see DESIGN.md, "Points to resolve").

	Detailed balancing numbers (incomes, scaling curves, price splits) live in
	Shared/EconomyDefs.luau so that this file stays readable.
]]

local Config = {}

Config.GameName = "Army Run Tycoon"

-------------------------------------------------------------------------------
-- Currency
-------------------------------------------------------------------------------
-- [PROPOSAL] "cash", "coins" and "money" in the brief are treated as ONE earned
-- currency, shown as "$" with a gold coin icon (exactly like both reference
-- images). Robux is the only other currency.
Config.Currency = {
	Name = "Cash",
	Symbol = "$",
}

-------------------------------------------------------------------------------
-- Palette (sampled from the two reference images)
-------------------------------------------------------------------------------
-- Image 1 (base / tycoon) and image 2 (runner) share the same bright, highly
-- saturated "toy" look: sky-blue floors, white lane lines, Lego-stud hedges,
-- chunky white text with dark outlines, black pills for currency.
-- Values were measured from the reference images (docs/reference/*.png).
-- Gate colours are the solid colours that render like the images when the
-- panel has Transparency 0.45 over the white track.
Config.Palette = {
	-- Shared UI
	White = Color3.fromRGB(255, 255, 255),
	TextStroke = Color3.fromRGB(28, 30, 46),
	PillBlack = Color3.fromRGB(24, 24, 36),
	Coin = Color3.fromRGB(255, 202, 32),
	CoinDark = Color3.fromRGB(226, 146, 0),
	CoinShine = Color3.fromRGB(255, 236, 140),
	MoneyGreen = Color3.fromRGB(108, 240, 84),
	MoneyGreenDark = Color3.fromRGB(56, 176, 48),
	CardOrange = Color3.fromRGB(252, 164, 12),
	CardOrangeDark = Color3.fromRGB(232, 124, 0),
	CardBlue = Color3.fromRGB(12, 142, 252),
	CardBlueDark = Color3.fromRGB(8, 98, 222),
	CardPink = Color3.fromRGB(252, 124, 250),
	CardPinkDark = Color3.fromRGB(214, 70, 214),
	CardOutline = Color3.fromRGB(24, 34, 62),
	ButtonGreen = Color3.fromRGB(64, 214, 76),
	ButtonGreenDark = Color3.fromRGB(30, 158, 46),
	ButtonRed = Color3.fromRGB(255, 82, 92),
	ButtonRedDark = Color3.fromRGB(196, 38, 54),
	ButtonGrey = Color3.fromRGB(150, 160, 178),
	ButtonGreyDark = Color3.fromRGB(96, 104, 122),
	Robux = Color3.fromRGB(0, 176, 111),
	RobuxDark = Color3.fromRGB(0, 122, 76),
	OfferPurple = Color3.fromRGB(122, 64, 236),
	OfferPurpleDark = Color3.fromRGB(78, 30, 172),
	PanelBlue = Color3.fromRGB(64, 160, 248),
	PanelBlueDark = Color3.fromRGB(30, 104, 214),
	Shade = Color3.fromRGB(10, 14, 30),

	-- Runner (image 2)
	Sky = Color3.fromRGB(52, 194, 250),
	Sea = Color3.fromRGB(16, 168, 250),
	TrackWhite = Color3.fromRGB(236, 246, 255),
	TrackStripe = Color3.fromRGB(212, 234, 252),
	TrackRail = Color3.fromRGB(196, 222, 250),
	GatePositive = Color3.fromRGB(78, 145, 242),
	GateNegative = Color3.fromRGB(238, 40, 60),
	GateWeapon = Color3.fromRGB(255, 198, 44),
	GatePost = Color3.fromRGB(46, 50, 66),
	EnemyRed = Color3.fromRGB(214, 52, 52),
	TroopGreen = Color3.fromRGB(78, 150, 62),
	TroopGreenDark = Color3.fromRGB(52, 104, 42),

	-- Base (image 1)
	BaseFloor = Color3.fromRGB(106, 178, 252),
	BasePath = Color3.fromRGB(146, 188, 252),
	BaseLine = Color3.fromRGB(236, 245, 255),
	PlotOutline = Color3.fromRGB(202, 226, 252),
	PlotInner = Color3.fromRGB(106, 178, 252),
	Hedge = Color3.fromRGB(88, 192, 64),
	HedgeDark = Color3.fromRGB(66, 160, 48),
	Grass = Color3.fromRGB(154, 218, 98),
	WallYellow = Color3.fromRGB(234, 182, 26),
	WallRed = Color3.fromRGB(226, 36, 42),
	GateIron = Color3.fromRGB(40, 40, 46),
	Padlock = Color3.fromRGB(250, 198, 30),
	PadFill = Color3.fromRGB(244, 250, 250),
	PadBorder = Color3.fromRGB(84, 212, 84),
	PriceGold = Color3.fromRGB(255, 214, 60),
	HireTile = Color3.fromRGB(98, 122, 170),
	Pink = Color3.fromRGB(240, 72, 160),
	Wood = Color3.fromRGB(150, 92, 52),

	-- Rarities (Common -> Legendary)
	Rarity = {
		Color3.fromRGB(176, 184, 196),
		Color3.fromRGB(92, 210, 92),
		Color3.fromRGB(62, 156, 255),
		Color3.fromRGB(178, 92, 255),
		Color3.fromRGB(255, 194, 26),
	},
}

-- Chunky rounded font that matches both reference images.
Config.Font = Enum.Font.FredokaOne

-------------------------------------------------------------------------------
-- Areas (stages) and their four unlock slots
-------------------------------------------------------------------------------
-- [REQUIREMENT] Many areas, each with four unlockable cash-revenue sections.
-- [REQUIREMENT] At the second stage, the repeatable combat mode takes one of
-- the four unlocks.
-- [PROPOSAL] Area 2 therefore has three revenue sections + the Combat Arena
-- (slot 4). The arena counts as the area's fourth (active) cash source.
--
-- slot.kind: "revenue" | "arena"
-- slot.style: building look used by the client BuildingFactory
-- slot.incomeWeight: relative income of this section inside its area
export type SlotDef = {
	id: string,
	kind: string,
	name: string,
	style: string,
	icon: string,
	color: Color3,
	incomeWeight: number,
}

export type AreaDef = {
	name: string,
	wall: string, -- Palette key of the perimeter hedge/wall colour
	accent: Color3,
	slots: { SlotDef },
}

local function slot(
	id: string,
	kind: string,
	name: string,
	style: string,
	icon: string,
	color: Color3,
	weight: number
): SlotDef
	return {
		id = id,
		kind = kind,
		name = name,
		style = style,
		icon = icon,
		color = color,
		incomeWeight = weight,
	}
end

local C = Color3.fromRGB

Config.Areas = {
	{
		name = "Boot Camp",
		wall = "Hedge",
		accent = C(92, 200, 58),
		slots = {
			slot("a1s1", "revenue", "Ration Stand", "stand", "🍞", C(255, 170, 60), 0.7),
			slot("a1s2", "revenue", "Boot Shop", "shop", "🥾", C(150, 100, 60), 0.9),
			slot("a1s3", "revenue", "Canteen", "diner", "🍔", C(240, 72, 160), 1.1),
			slot("a1s4", "revenue", "Barber Hut", "hut", "💈", C(70, 140, 255), 1.3),
		},
	},
	{
		name = "Supply Depot",
		wall = "WallYellow",
		accent = C(246, 182, 26),
		slots = {
			slot("a2s1", "revenue", "Ammo Store", "shop", "🎯", C(214, 80, 52), 0.8),
			slot("a2s2", "revenue", "Uniform Shop", "shop", "🎽", C(78, 150, 62), 1.0),
			slot("a2s3", "revenue", "Med Tent", "tent", "🩹", C(255, 255, 255), 1.2),
			slot("a2s4", "arena", "Combat Arena", "arena", "⚔️", C(255, 82, 92), 0),
		},
	},
	{
		name = "Motor Pool",
		wall = "WallRed",
		accent = C(232, 52, 46),
		slots = {
			slot("a3s1", "revenue", "Fuel Station", "station", "⛽", C(255, 210, 40), 0.7),
			slot("a3s2", "revenue", "Tire Shop", "shop", "🛞", C(60, 60, 70), 0.9),
			slot("a3s3", "revenue", "Repair Garage", "garage", "🔧", C(90, 120, 150), 1.1),
			slot("a3s4", "revenue", "Car Wash", "garage", "🧽", C(80, 200, 255), 1.3),
		},
	},
	{
		name = "Airfield",
		wall = "Hedge",
		accent = C(92, 200, 58),
		slots = {
			slot("a4s1", "revenue", "Parachute Shop", "tent", "🪂", C(255, 120, 40), 0.7),
			slot("a4s2", "revenue", "Radar Tower", "tower", "📡", C(200, 210, 220), 0.9),
			slot("a4s3", "revenue", "Hangar Cafe", "hangar", "☕", C(150, 100, 60), 1.1),
			slot("a4s4", "revenue", "Flight School", "hangar", "✈️", C(62, 156, 255), 1.3),
		},
	},
	{
		name = "Harbor",
		wall = "WallYellow",
		accent = C(246, 182, 26),
		slots = {
			slot("a5s1", "revenue", "Fish Market", "stand", "🐟", C(80, 170, 255), 0.7),
			slot("a5s2", "revenue", "Boat Rentals", "hut", "🚤", C(255, 255, 255), 0.9),
			slot("a5s3", "revenue", "Dock Warehouse", "garage", "📦", C(160, 110, 70), 1.1),
			slot("a5s4", "revenue", "Lighthouse Shop", "tower", "🗼", C(232, 52, 46), 1.3),
		},
	},
	{
		name = "Desert Outpost",
		wall = "WallRed",
		accent = C(232, 52, 46),
		slots = {
			slot("a6s1", "revenue", "Water Depot", "station", "💧", C(80, 200, 255), 0.7),
			slot("a6s2", "revenue", "Supply Bazaar", "tent", "🧺", C(240, 190, 90), 0.9),
			slot("a6s3", "revenue", "Solar Farm", "station", "☀️", C(255, 214, 60), 1.1),
			slot("a6s4", "revenue", "Oasis Cafe", "diner", "🥤", C(92, 210, 92), 1.3),
		},
	},
	{
		name = "Mountain Fortress",
		wall = "Hedge",
		accent = C(92, 200, 58),
		slots = {
			slot("a7s1", "revenue", "Climbing Gear", "shop", "🧗", C(255, 120, 40), 0.7),
			slot("a7s2", "revenue", "Hot Cocoa Hut", "hut", "☕", C(150, 92, 52), 0.9),
			slot("a7s3", "revenue", "Ski Rental", "shop", "🎿", C(80, 170, 255), 1.1),
			slot("a7s4", "revenue", "Armory", "garage", "🛡️", C(90, 100, 120), 1.3),
		},
	},
	{
		name = "Command HQ",
		wall = "WallYellow",
		accent = C(246, 182, 26),
		slots = {
			slot("a8s1", "revenue", "Medal Shop", "shop", "🎖️", C(255, 194, 26), 0.7),
			slot("a8s2", "revenue", "War Room Cafe", "diner", "🗺️", C(78, 150, 62), 0.9),
			slot("a8s3", "revenue", "Satellite Uplink", "tower", "🛰️", C(200, 210, 220), 1.1),
			slot("a8s4", "revenue", "Officer Club", "hangar", "🎩", C(122, 64, 236), 1.3),
		},
	},
} :: { AreaDef }

Config.SlotsPerArea = 4 -- [REQUIREMENT]

-- The Combat Arena unlock (repeatable combat mode). [REQUIREMENT: stage 2]
Config.ArenaSlotId = "a2s4"
Config.ArenaArea = 2

-------------------------------------------------------------------------------
-- 10-run benchmark  [REQUIREMENT + OPEN QUESTION 3]
-------------------------------------------------------------------------------
-- The army runner estimates best-case cash accumulation across 10 simulated
-- runs (B10). B10 is the baseline for "three area upgrades".
--
-- Because the brief does not say what "three area upgrades" are, the mapping
-- is a switch instead of a silent choice:
--   "CombinedSections" [DEFAULT PROPOSAL]: the first three revenue-section
--       unlocks of an area cost B10 *together* (split by EconomyDefs).
--   "EachSection": each of the first three revenue-section unlocks costs B10.
-- (A third reading, "three upgrade tiers of one section", is described in
-- DESIGN.md but not implemented.)
Config.Benchmark = {
	Runs = 10, -- [REQUIREMENT]
	Mode = "CombinedSections",
	-- [PROPOSAL] Player upgrade level assumed by the simulation: each of the
	-- three permanent runner upgrades is at (area - 1) * this value.
	ReferenceUpgradeLevelsPerArea = 4,
}

-------------------------------------------------------------------------------
-- Employees  [REQUIREMENT]
-------------------------------------------------------------------------------
Config.Employee = {
	Robux = 200, -- [REQUIREMENT]
	RunsEquivalent = 20, -- [REQUIREMENT] cash price = earnings of 20 runs
	-- [PROPOSAL] "20 runs" = 20 x the best-case single-run benchmark of the
	-- area the section belongs to (B10 / 10). "SectionArea" keeps old sections
	-- cheap to automate; "PlayerArea" would re-price them as you progress.
	PriceScalesWith = "SectionArea",
}

-------------------------------------------------------------------------------
-- Coin route for Robux items  [REQUIREMENT + OPEN QUESTION 10]
-------------------------------------------------------------------------------
-- [PROPOSAL definition] "Efficiency" = net in-game cash a purchase gives you.
--   Robux route: you receive the item's full value V and spend no cash -> +V.
--   Coin route:  you receive V and spend P -> net V - P.
-- "80% less efficient" => V - P = 0.2 * V  =>  P = 0.8 * V.
-- That is still "slightly profitable if used properly" (+25% return).
-- Applies to boosts (2x multipliers, time skips), whose value is measurable.
-- Vehicles and employees use their own anchors (see EconomyDefs / DESIGN.md).
Config.CoinRoute = {
	NetGainShare = 0.2,
}

-------------------------------------------------------------------------------
-- Boosts  [REQUIREMENT: 2x multipliers + 1-24h time skips, luck-based]
-------------------------------------------------------------------------------
-- [PROPOSAL] 2x doubles ALL cash earned (runner payouts, section revenue,
-- combat cash) for the duration, counted in play-time (only ticks while you
-- are in the game). Durations stack; the multiplier itself does not (max 2x).
-- [PROPOSAL] Time skips instantly pay the revenue your automatic sections
-- would earn in that many hours. Sections with an employee pay for the full
-- period; sections without one pay only until their stock runs out.
-- weight: relative chance of appearing on the home map (rarer = more valuable).
export type BoostDef = {
	id: string,
	kind: string, -- "multiplier" | "timeskip"
	name: string,
	minutes: number?,
	hours: number?,
	robux: number,
	weight: number,
	rarity: number,
}

Config.Boosts = {
	{ id = "x2_15", kind = "multiplier", name = "2x Cash (15 min)", minutes = 15, robux = 29, weight = 30, rarity = 1 },
	{ id = "x2_30", kind = "multiplier", name = "2x Cash (30 min)", minutes = 30, robux = 49, weight = 12, rarity = 2 },
	{ id = "x2_60", kind = "multiplier", name = "2x Cash (60 min)", minutes = 60, robux = 89, weight = 4, rarity = 3 },
	{ id = "skip_1", kind = "timeskip", name = "Time Skip 1h", hours = 1, robux = 25, weight = 30, rarity = 1 },
	{ id = "skip_2", kind = "timeskip", name = "Time Skip 2h", hours = 2, robux = 45, weight = 18, rarity = 2 },
	{ id = "skip_4", kind = "timeskip", name = "Time Skip 4h", hours = 4, robux = 79, weight = 10, rarity = 3 },
	{ id = "skip_8", kind = "timeskip", name = "Time Skip 8h", hours = 8, robux = 149, weight = 5, rarity = 4 },
	{ id = "skip_12", kind = "timeskip", name = "Time Skip 12h", hours = 12, robux = 199, weight = 2.5, rarity = 4 },
	{ id = "skip_24", kind = "timeskip", name = "Time Skip 24h", hours = 24, robux = 349, weight = 1, rarity = 5 },
} :: { BoostDef }

-- [PROPOSAL] Random home-map appearances are purchase OFFERS (like the purple
-- "+$357.1" bubble in image 1): walk into one to open a Robux-or-cash dialog.
Config.BoostOffers = {
	MinInterval = 150, -- seconds between offers (per player)
	MaxInterval = 300,
	Lifetime = 90, -- seconds an offer stays on the map
	MaxActive = 2,
}

-------------------------------------------------------------------------------
-- Vehicles  [REQUIREMENT: better vehicles as areas unlock]
-------------------------------------------------------------------------------
-- [PROPOSAL] A vehicle is ridden around the base (faster movement) and gives a
-- small bonus to runner payouts. Coin price scales with the player's current
-- area (EconomyDefs.VehiclePriceFactor x that area's gate price).
export type VehicleDef = {
	id: string,
	name: string,
	area: number, -- becomes available once this area is unlocked
	robux: number,
	speed: number, -- WalkSpeed while riding
	runnerBonus: number, -- +x runner payout (0.02 = +2%)
	priceFactor: number,
	color: Color3,
	style: string,
}

Config.Vehicles = {
	{ id = "bike", name = "Army Bike", area = 1, robux = 49, speed = 26, runnerBonus = 0.02, priceFactor = 0.6, color = C(78, 150, 62), style = "bike" },
	{ id = "jeep", name = "Jeep", area = 2, robux = 99, speed = 30, runnerBonus = 0.04, priceFactor = 0.8, color = C(110, 140, 70), style = "jeep" },
	{ id = "truck", name = "Supply Truck", area = 3, robux = 149, speed = 32, runnerBonus = 0.06, priceFactor = 1.0, color = C(255, 140, 40), style = "truck" },
	{ id = "humvee", name = "Humvee", area = 4, robux = 199, speed = 34, runnerBonus = 0.08, priceFactor = 1.25, color = C(196, 170, 110), style = "jeep" },
	{ id = "apc", name = "APC", area = 5, robux = 299, speed = 36, runnerBonus = 0.10, priceFactor = 1.5, color = C(90, 110, 80), style = "apc" },
	{ id = "tank", name = "Light Tank", area = 6, robux = 399, speed = 38, runnerBonus = 0.12, priceFactor = 1.8, color = C(70, 130, 70), style = "tank" },
	{ id = "heavytank", name = "Heavy Tank", area = 7, robux = 549, speed = 40, runnerBonus = 0.15, priceFactor = 2.2, color = C(60, 70, 80), style = "tank" },
	{ id = "hovertank", name = "Hover Tank", area = 8, robux = 799, speed = 46, runnerBonus = 0.20, priceFactor = 2.6, color = C(122, 64, 236), style = "hover" },
} :: { VehicleDef }

-------------------------------------------------------------------------------
-- Rarities (combat equipment)  [REQUIREMENT: higher rarity = better + rarer]
-------------------------------------------------------------------------------
export type RarityDef = {
	name: string,
	weight: number, -- base chest weight
	statMult: number,
	maxLevel: number,
	perkSlots: number,
}

Config.Rarities = {
	{ name = "Common", weight = 60, statMult = 1.0, maxLevel = 10, perkSlots = 0 },
	{ name = "Uncommon", weight = 25, statMult = 1.25, maxLevel = 20, perkSlots = 0 },
	{ name = "Rare", weight = 10, statMult = 1.6, maxLevel = 30, perkSlots = 1 },
	{ name = "Epic", weight = 4, statMult = 2.1, maxLevel = 40, perkSlots = 2 },
	{ name = "Legendary", weight = 1, statMult = 2.8, maxLevel = 50, perkSlots = 3 },
} :: { RarityDef }

-------------------------------------------------------------------------------
-- Developer Products (Robux)
-------------------------------------------------------------------------------
-- HOW TO SET UP: publish the place, then Creator Hub > your experience >
-- Monetization > Developer Products > Create. Make one product per key below
-- with the Robux price shown in this file, and paste its ID here.
-- 0 = not set up yet: the Robux button is shown greyed out ("Robux not set up").
Config.ProductIds = {
	Employee = 0,
	x2_15 = 0,
	x2_30 = 0,
	x2_60 = 0,
	skip_1 = 0,
	skip_2 = 0,
	skip_4 = 0,
	skip_8 = 0,
	skip_12 = 0,
	skip_24 = 0,
	vehicle_bike = 0,
	vehicle_jeep = 0,
	vehicle_truck = 0,
	vehicle_humvee = 0,
	vehicle_apc = 0,
	vehicle_tank = 0,
	vehicle_heavytank = 0,
	vehicle_hovertank = 0,
} :: { [string]: number }

-------------------------------------------------------------------------------
-- Misc
-------------------------------------------------------------------------------
Config.MaxSlots = 12 -- simultaneous runner tracks / combat arenas per server
Config.DataStoreName = "ArmyRunTycoon_v1"
Config.AutosaveInterval = 120

return Config
