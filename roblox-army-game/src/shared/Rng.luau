--!strict
--[[
	Rng
	Deterministic seeded random numbers (Park-Miller "minimal standard").
	Identical results in Roblox and in the Lune test runner, so the server,
	the client and the economy simulation all see the same runner course.

		local rng = Rng.new(1234)
		rng:Next()          -- [0, 1)
		rng:Int(1, 6)       -- 1..6 inclusive
		rng:Weighted(list, function(item) return item.weight end)
]]

export type Rng = {
	Next: (self: Rng) -> number,
	Int: (self: Rng, min: number, max: number) -> number,
	Range: (self: Rng, min: number, max: number) -> number,
	Chance: (self: Rng, p: number) -> boolean,
	Pick: <T>(self: Rng, list: { T }) -> T,
	Weighted: <T>(self: Rng, list: { T }, weightOf: (T) -> number) -> (T, number),
	Shuffle: <T>(self: Rng, list: { T }) -> { T },
}

local MODULUS = 2147483647
local MULTIPLIER = 48271

local Rng = {}

function Rng.new(seed: number): Rng
	local state: number = math.floor(math.abs(seed)) % MODULUS
	if state == 0 then
		state = 1
	end

	local function nextNumber(): number
		state = (state * MULTIPLIER) % MODULUS
		return (state - 1) / (MODULUS - 1)
	end

	local function int(min: number, max: number): number
		return min + math.floor(nextNumber() * (max - min + 1))
	end

	local rng = {}

	function rng.Next(_self: Rng): number
		return nextNumber()
	end

	function rng.Int(_self: Rng, min: number, max: number): number
		return int(min, max)
	end

	function rng.Range(_self: Rng, min: number, max: number): number
		return min + nextNumber() * (max - min)
	end

	function rng.Chance(_self: Rng, p: number): boolean
		return nextNumber() < p
	end

	function rng.Pick<T>(_self: Rng, list: { T }): T
		return list[int(1, #list)]
	end

	function rng.Weighted<T>(_self: Rng, list: { T }, weightOf: (T) -> number): (T, number)
		local total = 0
		for _, item in list do
			total += math.max(0, weightOf(item))
		end
		local roll = nextNumber() * total
		for index, item in list do
			roll -= math.max(0, weightOf(item))
			if roll < 0 then
				return item, index
			end
		end
		return list[#list], #list
	end

	function rng.Shuffle<T>(_self: Rng, list: { T }): { T }
		for i = #list, 2, -1 do
			local j = int(1, i)
			list[i], list[j] = list[j], list[i]
		end
		return list
	end

	-- Warm up so nearby seeds diverge quickly.
	for _ = 1, 3 do
		nextNumber()
	end
	return (rng :: any) :: Rng
end

return Rng
