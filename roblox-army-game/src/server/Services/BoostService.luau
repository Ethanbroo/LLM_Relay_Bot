--!strict
--[[
	BoostService (server)
	Runs the two kinds of boosts from the brief:
		* 2x cash multiplier: doubles every "boostable" cash payout (runner,
		  shops, combat) while data.boosts.multiplierSeconds > 0. The timer only
		  goes down while the player is in the game (play-time). Buying more
		  adds more TIME; the multiplier itself is never more than 2x.
		* Time skip: instantly pays what the player's shops would earn in that
		  many hours (BaseService:ApplyTimeSkip does the maths). Shops without
		  an employee only pay until their stock runs out.
		  [PROPOSAL] Cash price = (1 - NetGainShare) x V with
		  V = min(Economy.BoostValue (area reference), BaseService:PreviewTimeSkip)
		  = Economy.TimeSkipCoinPrice, computed here at purchase time, so a
		  cash time skip always returns +25% of what was paid. A skip that would
		  pay less than $1 is never sold (cash or Robux).

	It also spawns the random boost OFFERS on the home map (Config.BoostOffers):
		* Per player, every MinInterval..MaxInterval seconds (only while the
		  player is in "Base" mode), at most MaxActive at a time.
		* Which boost appears is a weighted random pick using
		  Config.Boosts[i].weight, so cheap boosts show up much more often than
		  valuable ones (the brief's "luck" rule). Time skips are only in the
		  draw when they would pay >= 25% of their reference value right now
		  (Economy.TimeSkipWorthOffering); otherwise only 2x boosts appear.
		* The offer sits on a walkway of a random unlocked area and lives for
		  Lifetime seconds. Walking into it (client) opens a Robux-or-cash dialog.

	Requests (Net actions):
		BuyBoostOffer(offerId: string, route: "cash" | "robux")
			Offer must be alive and the player within OFFER_RANGE studs.
			"cash"  -> pays BoostService:GetCashPrice(player, boostId), applies it.
			"robux" -> reserves the offer for 60 s and opens the Roblox purchase
			           window; ShopService's receipt applies the boost and calls
			           RemoveReservedOffer.

	Push events on the "Boost" channel (Net.Fire("Boost", player, msg, payload)):
		"Offer"     { id, boostId, position: Vector3, expiresAt: number }  (server time)
		"OfferGone" { id }
		"Timer"     { seconds }  2x seconds left (sent when it changes a lot)
	Client -> server on the same channel: "Ready" (the client asks for every
	live offer + the timer again, e.g. after its UI loaded).

	Public API (docs/ARCHITECTURE.md §4.4 + helpers for ShopService/DevService):
		BoostService:GetCashMultiplier(player): number            -- 2 while 2x is active, else 1
		BoostService:AddMultiplierMinutes(player, minutes)
		BoostService:ApplyBoost(player, boostId): boolean         -- true = applied
		BoostService:CanApplyBoost(player, boostId): (boolean, string?)  -- e.g. time skip that pays < $1
		BoostService:GetTimeSkipPreview(player, boostId): number  -- what a time skip pays now (0 for 2x)
		BoostService:GetCashPrice(player, boostId): number        -- cash price right now (math.huge = not for sale)
		BoostService:RemoveReservedOffer(player, boostId)         -- receipt finished
		BoostService:GetBoostDef(boostId): Config.BoostDef?
]]

local Players = game:GetService("Players")
local MarketplaceService = game:GetService("MarketplaceService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Economy = require(Shared.Economy)
local Format = require(Shared.Format)
local Layout = require(Shared.Layout)
local Net = require(Shared.Net)

local BoostService = {}
BoostService.Priority = 55

local Services: { [string]: any } = {}

-- Studs: how close the player must be to buy an offer (client opens at ~6).
local OFFER_RANGE = 12
-- Allowed height difference for the proximity check (jumping players).
local OFFER_HEIGHT = 20
-- Seconds a Robux purchase window keeps an offer alive.
local RESERVE_SECONDS = 60
-- [PROPOSAL] The very first offer after joining appears 45..75 s in, so new
-- players see the feature quickly; later ones use Config.BoostOffers.
local FIRST_OFFER_MIN = 45
local FIRST_OFFER_MAX = 75
-- [PROPOSAL] After returning to the base from a run/fight, wait at least
-- this long before a new offer appears.
local BACK_IN_BASE_DELAY = 10
-- Walkway placement (studs from the area centre along one arm of the
-- cross-shaped path). Plots start 14 studs from the centre line, the path is
-- 28 wide, so |lateral| <= 5 always stays on the walkway. [PROPOSAL]
local ARM_MIN = 22
local ARM_MAX = 46
local LATERAL_MAX = 5
local MIN_OFFER_SPACING = 14
-- Save the 2x timer at least this often (seconds) while it runs.
local TIMER_DIRTY_EVERY = 10
-- Safety cap for one AddMultiplierMinutes call (24 h).
local MAX_MINUTES_PER_CALL = 24 * 60

type Offer = {
	id: string,
	boostId: string,
	position: Vector3,
	expiresAt: number, -- workspace:GetServerTimeNow() based
	reservedUntil: number, -- os.clock(); 0 = not reserved
}

type PlayerState = {
	offers: { [string]: Offer },
	nextOfferAt: number, -- os.clock()
	sinceDirty: number,
	lastReady: number,
}

local states: { [Player]: PlayerState } = {}
local boostById: { [string]: Config.BoostDef } = {}
for _, def in Config.Boosts do
	boostById[def.id] = def
end

-- slot ids of every revenue section (not the Combat Arena)
local revenueSlots: { [string]: boolean } = {}
for _, area in Config.Areas do
	for _, slot in area.slots do
		if slot.kind == "revenue" then
			revenueSlots[slot.id] = true
		end
	end
end

local offerCounter = 0
local random = Random.new()

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------

local function isFiniteNumber(value: any): boolean
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function dataService(): any
	return Services.DataService
end

local function getData(player: Player): any
	local ds = dataService()
	return if ds then ds:Get(player) else nil
end

local function getMode(player: Player): string
	local modeService = Services.ModeService
	if modeService then
		return modeService:GetMode(player)
	end
	return (player:GetAttribute("Mode") :: string?) or "Base"
end

local function markDirty(player: Player)
	local ds = dataService()
	if ds then
		ds:MarkDirty(player)
	end
end

local function serverNow(): number
	return Workspace:GetServerTimeNow()
end

local function getState(player: Player): PlayerState
	local state = states[player]
	if not state then
		state = {
			offers = {},
			nextOfferAt = os.clock() + random:NextNumber(FIRST_OFFER_MIN, FIRST_OFFER_MAX),
			sinceDirty = 0,
			lastReady = 0,
		}
		states[player] = state
	end
	return state
end

local function countOffers(state: PlayerState): number
	local n = 0
	for _ in state.offers do
		n += 1
	end
	return n
end

local function rootPosition(player: Player): Vector3?
	local character = player.Character
	if not character then
		return nil
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root.Position
	end
	return nil
end

local function hasRevenueSection(data: any): boolean
	for slotId, section in data.base.sections do
		if type(section) == "table" and revenueSlots[slotId] then
			return true
		end
	end
	return false
end

-- What the time skip `def` would pay the player right now (0 if it is not a
-- time skip or BaseService is missing).
local function timeSkipPreview(player: Player, def: Config.BoostDef): number
	if def.kind ~= "timeskip" then
		return 0
	end
	local base = Services.BaseService
	if not base or type(base.PreviewTimeSkip) ~= "function" then
		return 0
	end
	local preview = base:PreviewTimeSkip(player, (def.hours or 0) * 3600)
	return if isFiniteNumber(preview) then math.max(0, preview) else 0
end

local function sendOffer(player: Player, offer: Offer)
	Net.Fire("Boost", player, "Offer", {
		id = offer.id,
		boostId = offer.boostId,
		position = offer.position,
		expiresAt = offer.expiresAt,
	})
end

local function removeOffer(player: Player, offerId: string)
	local state = states[player]
	if not state or not state.offers[offerId] then
		return
	end
	state.offers[offerId] = nil
	if player.Parent then
		Net.Fire("Boost", player, "OfferGone", { id = offerId })
	end
end

local function sendTimer(player: Player)
	local data = getData(player)
	if data and player.Parent then
		Net.Fire("Boost", player, "Timer", { seconds = math.max(0, data.boosts.multiplierSeconds or 0) })
	end
end

-- A random point on the walkway of one of the player's unlocked areas.
local function pickOfferPosition(data: any, state: PlayerState): Vector3
	local areaCount = #Config.Areas
	local highest = math.clamp(math.floor(tonumber(data.base.highestArea) or 1), 1, areaCount)
	local best: Vector3? = nil
	for _ = 1, 10 do
		local area = random:NextInteger(1, highest)
		local centre = Layout.AreaCenter(area)
		local along = random:NextNumber(ARM_MIN, ARM_MAX)
		local lateral = random:NextNumber(-LATERAL_MAX, LATERAL_MAX)
		local arm = random:NextInteger(1, 4)
		local offset: Vector3
		if arm == 1 then
			offset = Vector3.new(along, 0, lateral) -- east arm
		elseif arm == 2 then
			offset = Vector3.new(-along, 0, lateral) -- west arm
		elseif arm == 3 then
			offset = Vector3.new(lateral, 0, along) -- south arm
		else
			offset = Vector3.new(lateral, 0, -along) -- north arm
		end
		local candidate = Vector3.new(centre.X + offset.X, Layout.FLOOR_Y, centre.Z + offset.Z)
		local tooClose = false
		for _, other in state.offers do
			if (other.position - candidate).Magnitude < MIN_OFFER_SPACING then
				tooClose = true
				break
			end
		end
		best = candidate
		if not tooClose then
			break
		end
	end
	return best or (Layout.AreaCenter(1) + Vector3.new(ARM_MIN, 0, 0))
end

local function spawnOffer(player: Player, data: any, state: PlayerState)
	-- [PROPOSAL] Time skips only appear when they would pay at least 25% of
	-- their reference value right now (Economy.TimeSkipWorthOffering), so
	-- an offer is always worth buying. ShopUI shows chances from the same rule.
	local candidates: { Config.BoostDef } = {}
	for _, boost in Config.Boosts do
		if
			boost.kind ~= "timeskip"
			or Economy.TimeSkipWorthOffering(boost.id, data.base.highestArea, timeSkipPreview(player, boost))
		then
			table.insert(candidates, boost)
		end
	end
	-- Weighted pick: cheaper boosts (higher weight) appear more often.
	local total = 0
	for _, boost in candidates do
		total += math.max(0, boost.weight)
	end
	if total <= 0 or #candidates == 0 then
		return
	end
	local roll = random:NextNumber() * total
	local chosen = candidates[#candidates]
	for _, boost in candidates do
		roll -= math.max(0, boost.weight)
		if roll < 0 then
			chosen = boost
			break
		end
	end

	offerCounter += 1
	local offer: Offer = {
		id = "offer" .. offerCounter,
		boostId = chosen.id,
		position = pickOfferPosition(data, state),
		expiresAt = serverNow() + Config.BoostOffers.Lifetime,
		reservedUntil = 0,
	}
	state.offers[offer.id] = offer
	sendOffer(player, offer)
end

-------------------------------------------------------------------------------
-- 1 Hz loop: 2x timer + offers
-------------------------------------------------------------------------------

local function tickPlayer(player: Player, dt: number)
	local data = getData(player)
	if not data or not player.Parent then
		return
	end
	local state = getState(player)

	-- 2x timer (play-time only: this loop only sees players in the server).
	local seconds = tonumber(data.boosts.multiplierSeconds) or 0
	if seconds > 0 then
		seconds = math.max(0, seconds - dt)
		data.boosts.multiplierSeconds = seconds
		state.sinceDirty += dt
		if seconds <= 0 then
			state.sinceDirty = 0
			markDirty(player)
			sendTimer(player)
			Net.Notify(player, "⏱️ Your 2x cash boost has ended", "info")
		elseif state.sinceDirty >= TIMER_DIRTY_EVERY then
			state.sinceDirty = 0
			markDirty(player)
		end
	end

	-- Expire old offers (a reserved offer waits for its Robux window).
	local nowServer = serverNow()
	local nowClock = os.clock()
	for id, offer in state.offers do
		if nowServer >= offer.expiresAt and offer.reservedUntil <= nowClock then
			removeOffer(player, id)
		end
	end

	-- Spawn new offers only while the player is on the home map.
	if getMode(player) ~= "Base" then
		state.nextOfferAt = math.max(state.nextOfferAt, nowClock + BACK_IN_BASE_DELAY)
		return
	end
	if nowClock >= state.nextOfferAt then
		local interval = random:NextNumber(
			Config.BoostOffers.MinInterval,
			math.max(Config.BoostOffers.MinInterval, Config.BoostOffers.MaxInterval)
		)
		state.nextOfferAt = nowClock + interval
		if countOffers(state) < Config.BoostOffers.MaxActive then
			spawnOffer(player, data, state)
		end
	end
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------

function BoostService:GetBoostDef(boostId: string): Config.BoostDef?
	return boostById[boostId]
end

function BoostService:GetCashMultiplier(player: Player): number
	local data = getData(player)
	if data and (tonumber(data.boosts.multiplierSeconds) or 0) > 0 then
		return 2
	end
	return 1
end

function BoostService:AddMultiplierMinutes(player: Player, minutes: number)
	local data = getData(player)
	if not data or not isFiniteNumber(minutes) or minutes <= 0 then
		return
	end
	minutes = math.min(minutes, MAX_MINUTES_PER_CALL)
	local current = tonumber(data.boosts.multiplierSeconds) or 0
	data.boosts.multiplierSeconds = math.max(0, current) + minutes * 60
	markDirty(player)
	sendTimer(player)
end

function BoostService:GetTimeSkipPreview(player: Player, boostId: string): number
	local def = boostById[boostId]
	return if def then timeSkipPreview(player, def) else 0
end

-- Cash price right now. 2x: Economy.BoostCoinPrice. Time skip: 80% of what it
-- would pay now, capped at the area reference (Economy.TimeSkipCoinPrice).
function BoostService:GetCashPrice(player: Player, boostId: string): number
	local def = boostById[boostId]
	local data = getData(player)
	if not def or not data then
		return math.huge
	end
	if def.kind == "timeskip" then
		return Economy.TimeSkipCoinPrice(def.id, data.base.highestArea, timeSkipPreview(player, def))
	end
	return Economy.BoostCoinPrice(def.id, data.base.highestArea)
end

-- Can this boost do anything for the player right now? (A time skip that
-- would pay less than $1 is refused, for cash AND Robux.) [PROPOSAL]
function BoostService:CanApplyBoost(player: Player, boostId: string): (boolean, string?)
	local def = boostById[boostId]
	if not def then
		return false, "Unknown boost"
	end
	local data = getData(player)
	if not data then
		return false, "Your data is still loading"
	end
	if def.kind == "timeskip" then
		if not hasRevenueSection(data) then
			return false, "Unlock a revenue section first: time skips pay your shops' earnings"
		end
		if timeSkipPreview(player, def) < 1 then
			return false, "Your shops are out of stock: restock them or hire employees first"
		end
	end
	return true, nil
end

-- Give the player a boost (already paid for). Returns true when applied.
function BoostService:ApplyBoost(player: Player, boostId: string): boolean
	local def = boostById[boostId]
	local data = getData(player)
	if not def or not data then
		return false
	end
	if def.kind == "multiplier" then
		local minutes = def.minutes or 0
		if minutes <= 0 then
			return false
		end
		self:AddMultiplierMinutes(player, minutes)
		Net.Notify(player, ("⚡ 2x CASH for %d more minutes!"):format(minutes), "success")
		return true
	elseif def.kind == "timeskip" then
		local base = Services.BaseService
		if not base or type(base.ApplyTimeSkip) ~= "function" then
			return false
		end
		local hours = def.hours or 0
		local paid = base:ApplyTimeSkip(player, hours * 3600)
		paid = if isFiniteNumber(paid) then paid else 0
		Net.Notify(player, ("⏩ Time skip: +%s"):format(Format.Money(paid)), "cash")
		return true
	end
	return false
end

-- Called by ShopService after a Robux receipt for `boostId`: remove the offer
-- this player reserved for it (if it is still there).
function BoostService:RemoveReservedOffer(player: Player, boostId: string)
	local state = states[player]
	if not state then
		return
	end
	local bestId: string? = nil
	local bestReserve = 0
	for id, offer in state.offers do
		if offer.boostId == boostId and offer.reservedUntil > bestReserve then
			bestId = id
			bestReserve = offer.reservedUntil
		end
	end
	if bestId then
		removeOffer(player, bestId)
	end
end

-------------------------------------------------------------------------------
-- Requests
-------------------------------------------------------------------------------

local function buyBoostOffer(player: Player, offerId: any, route: any): Net.Result
	if type(offerId) ~= "string" or #offerId > 40 then
		return Net.Fail("Bad offer")
	end
	if route ~= "cash" and route ~= "robux" then
		return Net.Fail("Bad payment option")
	end
	local data = getData(player)
	if not data then
		return Net.Fail("Your data is still loading")
	end
	if getMode(player) ~= "Base" then
		return Net.Fail("Go back to your base first")
	end
	local state = getState(player)
	local offer = state.offers[offerId]
	if not offer then
		return Net.Fail("That offer is gone")
	end
	local nowClock = os.clock()
	if serverNow() >= offer.expiresAt and offer.reservedUntil <= nowClock then
		removeOffer(player, offerId)
		return Net.Fail("That offer is gone")
	end
	local position = rootPosition(player)
	if not position then
		return Net.Fail("Your character is not ready")
	end
	local flat = Vector3.new(position.X - offer.position.X, 0, position.Z - offer.position.Z)
	if flat.Magnitude > OFFER_RANGE or math.abs(position.Y - offer.position.Y) > OFFER_HEIGHT then
		return Net.Fail("Walk closer to the offer")
	end
	local def = boostById[offer.boostId]
	if not def then
		removeOffer(player, offerId)
		return Net.Fail("That offer is gone")
	end
	local canApply, reason = BoostService:CanApplyBoost(player, def.id)
	if not canApply then
		return Net.Fail(reason or "You cannot use this boost yet")
	end

	if route == "cash" then
		-- Priced on the server right now (never trust a client price).
		local price = BoostService:GetCashPrice(player, def.id)
		if not isFiniteNumber(price) or price <= 0 then
			return Net.Fail("This boost cannot be bought with cash")
		end
		local ds = dataService()
		if not ds or not ds:SpendCash(player, price) then
			return Net.Fail("Not enough cash")
		end
		-- Remove first so a double click can never buy the same offer twice.
		removeOffer(player, offerId)
		if not BoostService:ApplyBoost(player, def.id) then
			-- Could not apply (service missing): give the money back.
			ds:AddCash(player, price, { source = "refund" })
			return Net.Fail("Boosts are not available right now")
		end
		return Net.Ok({ boostId = def.id, price = price })
	end

	-- Robux route.
	local productId = Config.ProductIds[def.id] or 0
	if productId == 0 then
		return Net.Fail("Robux purchases are not set up yet")
	end
	offer.reservedUntil = nowClock + RESERVE_SECONDS
	local ok, err = pcall(function()
		MarketplaceService:PromptProductPurchase(player, productId)
	end)
	if not ok then
		warn("[BoostService] PromptProductPurchase failed: " .. tostring(err))
		return Net.Fail("Could not open the Robux purchase window")
	end
	return Net.Ok({ prompted = true })
end

-------------------------------------------------------------------------------
-- Lifecycle
-------------------------------------------------------------------------------

function BoostService:Init(registry: { [string]: any })
	Services = registry
	Net.Handle("BuyBoostOffer", buyBoostOffer)
end

function BoostService:Start()
	-- The client asks for its offers + timer again once its visuals are ready.
	Net.On("Boost", function(player: Player, msg: any)
		if msg ~= "Ready" or not player.Parent then
			return
		end
		local state = getState(player)
		local nowClock = os.clock()
		if nowClock - state.lastReady < 2 then
			return
		end
		state.lastReady = nowClock
		for _, offer in state.offers do
			sendOffer(player, offer)
		end
		sendTimer(player)
	end)

	Players.PlayerRemoving:Connect(function(player)
		states[player] = nil
	end)

	task.spawn(function()
		local last = os.clock()
		while true do
			task.wait(1)
			local now = os.clock()
			-- Clamp so a long server hiccup cannot eat minutes of boost time.
			local dt = math.clamp(now - last, 0, 2)
			last = now
			local ds = dataService()
			if ds then
				for _, player in ds:GetLoadedPlayers() do
					local ok, err = pcall(tickPlayer :: any, player, dt)
					if not ok then
						warn("[BoostService] tick failed for " .. player.Name .. ": " .. tostring(err))
					end
				end
			end
		end
	end)
end

return BoostService
