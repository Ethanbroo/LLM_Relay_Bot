--!strict
--[[
	Layout
	Single source of truth for world-space positions so that the server
	(validation, static map) and the client (progression visuals) agree.

	World map (top view, +X to the right, +Z towards the camera):

	   Combat arenas      z = -700   (one per player slot, x = slot * 170)
	   Home base          z = 0      (areas 1..N laid out along +X)
	   Runner tracks      z = +700 .. +1600 (one per slot, run towards -Z)
]]

local Layout = {}

-- Home base -------------------------------------------------------------------
Layout.AREA_SIZE = 160 -- each area is a 160 x 160 stud square
Layout.AREA_PITCH = 184 -- centre-to-centre distance (24-stud gate corridor)
Layout.FLOOR_Y = 0 -- top surface of all floors
Layout.PATH_WIDTH = 28 -- central cross-shaped walkway
Layout.PLOT_SIZE = 56 -- each unlock slot is a 56 x 56 plot
Layout.GATE_WIDTH = 20 -- opening in the hedge between two areas
Layout.WALL_HEIGHT = 6
Layout.INTERACT_RANGE = 16 -- server-side proximity check for base actions

-- Slot offsets from the area centre: 1 = north-west, 2 = north-east,
-- 3 = south-west, 4 = south-east.
Layout.SLOT_OFFSETS = {
	Vector3.new(-42, 0, -42),
	Vector3.new(42, 0, -42),
	Vector3.new(-42, 0, 42),
	Vector3.new(42, 0, 42),
}

function Layout.AreaCenter(area: number): Vector3
	return Vector3.new((area - 1) * Layout.AREA_PITCH, Layout.FLOOR_Y, 0)
end

-- Which area a world position belongs to (nil = outside the base).
function Layout.AreaAt(position: Vector3, areaCount: number): number?
	for area = 1, areaCount do
		local c = Layout.AreaCenter(area)
		local half = Layout.AREA_SIZE / 2
		if math.abs(position.X - c.X) <= half and math.abs(position.Z - c.Z) <= half then
			return area
		end
	end
	return nil
end

function Layout.SlotCenter(area: number, slotIndex: number): Vector3
	return Layout.AreaCenter(area) + Layout.SLOT_OFFSETS[slotIndex]
end

-- Point on the plot edge that faces the central walkway (where restock and
-- upgrade prompts sit, so the player never has to walk into the building).
function Layout.SlotFrontPosition(area: number, slotIndex: number): Vector3
	local offset = Layout.SLOT_OFFSETS[slotIndex]
	local towardsPathX = -math.sign(offset.X)
	return Layout.SlotCenter(area, slotIndex)
		+ Vector3.new(towardsPathX * (Layout.PLOT_SIZE / 2 - 6), 0, 0)
end

-- Where the "Hire me" tile / employee stands for a revenue section.
function Layout.SlotHirePosition(area: number, slotIndex: number): Vector3
	local offset = Layout.SLOT_OFFSETS[slotIndex]
	local towardsPathX = -math.sign(offset.X)
	local towardsPathZ = -math.sign(offset.Z)
	return Layout.SlotCenter(area, slotIndex)
		+ Vector3.new(towardsPathX * (Layout.PLOT_SIZE / 2 - 6), 0, towardsPathZ * (Layout.PLOT_SIZE / 2 - 8))
end

-- The gate that leads FROM `area` TO `area + 1` (on the east edge of `area`).
function Layout.GatePosition(area: number): Vector3
	return Layout.AreaCenter(area) + Vector3.new(Layout.AREA_PITCH / 2, 0, 0)
end

function Layout.SpawnPosition(): Vector3
	return Layout.AreaCenter(1) + Vector3.new(0, 0, 58)
end

-- Big "ARMY RUN" pad in area 1 that starts the runner.
function Layout.RunnerPortalPosition(): Vector3
	return Layout.AreaCenter(1) + Vector3.new(0, 0, -60)
end

-- Runner -------------------------------------------------------------------------
Layout.RUNNER_TRACK_WIDTH = 24
Layout.RUNNER_TRACK_LENGTH = 900
Layout.RUNNER_START_Z = 1600
Layout.RUNNER_SLOT_SPACING = 80

-- CFrame at the start of a player's runner track, facing down the track (-Z).
function Layout.RunnerTrackStart(slotIndex: number): CFrame
	local p = Vector3.new(slotIndex * Layout.RUNNER_SLOT_SPACING, Layout.FLOOR_Y, Layout.RUNNER_START_Z)
	return CFrame.lookAt(p, p + Vector3.new(0, 0, -1))
end

-- World position of a point on a track: `distance` studs from the start and
-- `lateral` studs to the right of the centre line (right = +X).
function Layout.RunnerPoint(slotIndex: number, distance: number, lateral: number): Vector3
	return Vector3.new(
		slotIndex * Layout.RUNNER_SLOT_SPACING + lateral,
		Layout.FLOOR_Y,
		Layout.RUNNER_START_Z - distance
	)
end

-- Combat -----------------------------------------------------------------------
Layout.ARENA_SIZE_X = 80
Layout.ARENA_SIZE_Z = 110
Layout.ARENA_SLOT_SPACING = 170
Layout.ARENA_Z = -700

function Layout.ArenaCenter(slotIndex: number): Vector3
	return Vector3.new(slotIndex * Layout.ARENA_SLOT_SPACING, Layout.FLOOR_Y, Layout.ARENA_Z)
end

return Layout
