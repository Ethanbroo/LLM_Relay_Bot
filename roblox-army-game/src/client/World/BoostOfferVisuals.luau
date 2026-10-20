--!strict
--[[
	BoostOfferVisuals (client, plain module - not a controller)
	Draws the random boost offers on the home map for the local player, in
	the look of the purple "+$357.1 / WATCH AD" bubble of reference image 1:

		* a purple rounded bubble (BillboardGui) with a gold coin and
		  "2x CASH 15m" or "⏩ 4h SKIP", over a green "BUY" strip
		* under it a floating, spinning gold coin pickup (with a purple glow)
		* a glowing purple Neon ring on the walkway floor

	When the local character walks within APPROACH_RANGE studs of an offer
	the OnApproach callback runs once (the ShopController then opens the
	Robux-or-cash dialog). It re-arms after walking more than REARM_RANGE away.
	Offers disappear when the server says "OfferGone" or when they expire.

	Public API (used by Controllers/ShopController):
		BoostOfferVisuals.Start(opts: { OnApproach: (offer: Offer) -> () })
		BoostOfferVisuals.Add(offer: Offer)        -- offer = server "Offer" payload
		BoostOfferVisuals.Remove(offerId: string)
		BoostOfferVisuals.Get(offerId: string): Offer?
		BoostOfferVisuals.Clear()
		BoostOfferVisuals.Stop()                   -- disconnects everything
	type Offer = { id: string, boostId: string, position: Vector3, expiresAt: number }

	Everything is created on the client only (other players never see your
	offers). Parts are Anchored, CanCollide/CanQuery/CanTouch = false.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Client = script.Parent.Parent
local State = require(Client.State)
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)

local P = Config.Palette
local player = Players.LocalPlayer

export type Offer = { id: string, boostId: string, position: Vector3, expiresAt: number }

type Entry = {
	offer: Offer,
	model: Model,
	coin: BasePart,
	rim: BasePart,
	disc: BasePart,
	phase: number,
	inside: boolean,
}

local BoostOfferVisuals = {}

-- Studs: open the dialog this close (server allows ~12).
local APPROACH_RANGE = 6
-- Studs: walk this far away to be able to trigger it again.
local REARM_RANGE = 10
local RING_RADIUS = 3.6
local RING_SEGMENTS = 18
local COIN_HEIGHT = 3.2
local CHECK_INTERVAL = 0.15

local boostById: { [string]: Config.BoostDef } = {}
for _, def in Config.Boosts do
	boostById[def.id] = def
end

local entries: { [string]: Entry } = {}
local folder: Folder? = nil
local heartbeat: RBXScriptConnection? = nil
local onApproach: ((Offer) -> ())? = nil
local sinceCheck = 0

local function getFolder(): Folder
	local existing = folder
	if existing and existing.Parent then
		return existing
	end
	local created = Instance.new("Folder")
	created.Name = "BoostOffers"
	created.Parent = Workspace
	folder = created
	return created
end

local function makePart(
	parent: Instance,
	name: string,
	size: Vector3,
	cf: CFrame,
	color: Color3,
	material: Enum.Material?
): Part
	local part = Instance.new("Part")
	part.Name = name
	part.Size = size
	part.CFrame = cf
	part.Color = color
	part.Material = material or Enum.Material.SmoothPlastic
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.CastShadow = false
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Parent = parent
	return part
end

-- "2x CASH 15m" / "⏩ 4h SKIP"
local function offerText(def: Config.BoostDef?): string
	if not def then
		return "BOOST"
	end
	if def.kind == "multiplier" then
		return ("2x CASH %dm"):format(def.minutes or 0)
	end
	return ("⏩ %dh SKIP"):format(def.hours or 0)
end

-- The purple bubble above the pickup.
local function buildBubble(adornee: BasePart, def: Config.BoostDef?)
	local board = Theme.Billboard({
		Name = "OfferBubble",
		Parent = adornee,
		Adornee = adornee,
		-- Scale-based size = studs, so it looks attached to the world like image 1.
		Size = UDim2.fromScale(7, 3.6),
		StudsOffset = Vector3.new(0, 3.6, 0),
		AlwaysOnTop = false,
		MaxDistance = 260,
	})

	local bubble = Instance.new("Frame")
	bubble.Name = "Bubble"
	bubble.BackgroundColor3 = P.OfferPurple
	bubble.Size = UDim2.fromScale(1, 0.84)
	bubble.Parent = board
	Theme.Corner(bubble, 0.22)
	Theme.Stroke(bubble, P.OfferPurpleDark, 3, true)
	Theme.Gradient(bubble, Theme.Lighten(P.OfferPurple, 0.18), P.OfferPurple)

	-- Little pointer under the bubble.
	local pointer = Instance.new("Frame")
	pointer.Name = "Pointer"
	pointer.BackgroundColor3 = P.OfferPurple
	pointer.AnchorPoint = Vector2.new(0.5, 0.5)
	pointer.Position = UDim2.fromScale(0.5, 0.84)
	pointer.Size = UDim2.fromScale(0.12, 0.2)
	pointer.Rotation = 45
	pointer.ZIndex = 0
	pointer.Parent = board
	Theme.AspectRatio(pointer, 1)
	Theme.Stroke(pointer, P.OfferPurpleDark, 3, true)

	-- Coin + text row.
	local coin = Theme.CoinIcon({
		Size = UDim2.fromScale(0.2, 0.42),
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.fromScale(0.05, 0.3),
		ZIndex = 2,
		Parent = bubble,
	})
	local aspect = coin:FindFirstChildOfClass("UIAspectRatioConstraint")
	if aspect then
		aspect.DominantAxis = Enum.DominantAxis.Height
	end
	Theme.Label({
		Name = "Text",
		Text = offerText(def),
		MaxTextSize = 80,
		Size = UDim2.fromScale(0.7, 0.46),
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.fromScale(0.26, 0.3),
		ZIndex = 2,
		Parent = bubble,
	})

	-- Green "BUY" strip.
	local strip = Instance.new("Frame")
	strip.Name = "BuyStrip"
	strip.BackgroundColor3 = P.ButtonGreen
	strip.AnchorPoint = Vector2.new(0.5, 0)
	strip.Position = UDim2.fromScale(0.5, 0.58)
	strip.Size = UDim2.fromScale(0.86, 0.32)
	strip.ZIndex = 2
	strip.Parent = bubble
	Theme.Corner(strip, 0.3)
	Theme.Stroke(strip, P.ButtonGreenDark, 2, true)
	Theme.Label({
		Text = "🛒 BUY",
		MaxTextSize = 60,
		Size = UDim2.fromScale(0.9, 0.86),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.5),
		ZIndex = 3,
		Parent = strip,
	})
end

local function buildEntry(offer: Offer): Entry
	local def = boostById[offer.boostId]
	local model = Instance.new("Model")
	model.Name = "Offer_" .. offer.id
	local base = Vector3.new(offer.position.X, offer.position.Y, offer.position.Z)

	-- Glowing ring on the floor (Neon segments in a circle).
	for i = 1, RING_SEGMENTS do
		local angle = (i / RING_SEGMENTS) * math.pi * 2
		local offset = Vector3.new(math.cos(angle) * RING_RADIUS, 0.3, math.sin(angle) * RING_RADIUS)
		local segmentLength = 2 * math.pi * RING_RADIUS / RING_SEGMENTS + 0.15
		local cf = CFrame.lookAt(base + offset, base + offset + Vector3.new(-math.sin(angle), 0, math.cos(angle)))
		makePart(model, "Ring", Vector3.new(0.45, 0.14, segmentLength), cf, P.OfferPurple, Enum.Material.Neon)
	end
	-- Soft purple disc inside the ring (round axis pointing up).
	local disc = makePart(
		model,
		"Disc",
		Vector3.new(0.1, RING_RADIUS * 2 - 0.4, RING_RADIUS * 2 - 0.4),
		CFrame.new(base + Vector3.new(0, 0.22, 0)) * CFrame.Angles(0, 0, math.rad(90)),
		P.OfferPurple
	)
	disc.Shape = Enum.PartType.Cylinder
	disc.Transparency = 0.6

	-- Floating gold coin pickup (round axis along Z so it stands up like a coin).
	local coinCf = CFrame.new(base + Vector3.new(0, COIN_HEIGHT, 0))
	local rim = makePart(model, "CoinRim", Vector3.new(0.45, 3.3, 3.3), coinCf, P.CoinDark)
	rim.Shape = Enum.PartType.Cylinder
	local coin = makePart(model, "Coin", Vector3.new(0.6, 2.7, 2.7), coinCf, P.Coin)
	coin.Shape = Enum.PartType.Cylinder
	local glow = Instance.new("PointLight")
	glow.Color = P.OfferPurple
	glow.Range = 12
	glow.Brightness = 2
	glow.Parent = coin
	-- "2x" / "⏩" printed on both faces of the coin.
	for _, face in { Enum.NormalId.Left, Enum.NormalId.Right } do
		local surface = Instance.new("SurfaceGui")
		surface.Name = "Face"
		surface.Face = face
		surface.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
		surface.PixelsPerStud = 40
		surface.LightInfluence = 0
		surface.Parent = coin
		Theme.Label({
			Text = if def and def.kind == "timeskip" then "⏩" else "2x",
			TextColor3 = P.White,
			MaxTextSize = 80,
			Size = UDim2.fromScale(0.8, 0.8),
			AnchorPoint = Vector2.new(0.5, 0.5),
			Position = UDim2.fromScale(0.5, 0.5),
			Parent = surface,
		})
	end
	buildBubble(coin, def)

	model.PrimaryPart = coin
	model.Parent = getFolder()
	return {
		offer = offer,
		model = model,
		coin = coin,
		rim = rim,
		disc = disc,
		phase = math.random() * math.pi * 2,
		inside = false,
	}
end

local function characterRoot(): BasePart?
	local character = player.Character
	if not character then
		return nil
	end
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	if humanoid and humanoid.Health <= 0 then
		return nil
	end
	local root = character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root
	end
	return nil
end

local function onHeartbeat(dt: number)
	local t = os.clock()
	-- Bob + spin.
	for _, entry in entries do
		local base = entry.offer.position
		local y = COIN_HEIGHT + math.sin(t * 2 + entry.phase) * 0.35
		local cf = CFrame.new(base.X, base.Y + y, base.Z) * CFrame.Angles(0, t * 1.6 + entry.phase, 0)
		entry.coin.CFrame = cf
		entry.rim.CFrame = cf
		entry.disc.Transparency = 0.55 + math.sin(t * 3 + entry.phase) * 0.12
	end

	sinceCheck += dt
	if sinceCheck < CHECK_INTERVAL then
		return
	end
	sinceCheck = 0

	-- Expiry (the server also sends "OfferGone").
	local serverNow = Workspace:GetServerTimeNow()
	for id, entry in entries do
		if serverNow >= entry.offer.expiresAt + 1 then
			BoostOfferVisuals.Remove(id)
		end
	end

	-- Walk-in detection (home map only).
	if State.Mode() ~= "Base" then
		return
	end
	local root = characterRoot()
	if not root then
		return
	end
	local position = root.Position
	for _, entry in entries do
		local p = entry.offer.position
		local distance = Vector3.new(position.X - p.X, 0, position.Z - p.Z).Magnitude
		local closeEnough = distance <= APPROACH_RANGE and math.abs(position.Y - p.Y) < 12
		if closeEnough and not entry.inside then
			entry.inside = true
			local callback = onApproach
			if callback then
				task.spawn(callback, entry.offer)
			end
		elseif entry.inside and distance > REARM_RANGE then
			entry.inside = false
		end
	end
end

function BoostOfferVisuals.Start(opts: { OnApproach: (Offer) -> () })
	onApproach = opts.OnApproach
	if not heartbeat then
		heartbeat = RunService.Heartbeat:Connect(onHeartbeat)
	end
end

function BoostOfferVisuals.Add(offer: Offer)
	if type(offer) ~= "table" or type(offer.id) ~= "string" or typeof(offer.position) ~= "Vector3" then
		return
	end
	if type(offer.expiresAt) ~= "number" then
		return
	end
	local existing = entries[offer.id]
	if existing then
		-- Already shown (e.g. re-sent after "Ready"): just update the data.
		existing.offer = offer
		return
	end
	entries[offer.id] = buildEntry(offer)
end

function BoostOfferVisuals.Get(offerId: string): Offer?
	local entry = entries[offerId]
	return if entry then entry.offer else nil
end

function BoostOfferVisuals.Remove(offerId: string)
	local entry = entries[offerId]
	if not entry then
		return
	end
	entries[offerId] = nil
	entry.model:Destroy()
end

function BoostOfferVisuals.Clear()
	for id in entries do
		BoostOfferVisuals.Remove(id)
	end
end

function BoostOfferVisuals.Stop()
	local connection = heartbeat
	heartbeat = nil
	if connection then
		connection:Disconnect()
	end
	BoostOfferVisuals.Clear()
	onApproach = nil
end

return BoostOfferVisuals
