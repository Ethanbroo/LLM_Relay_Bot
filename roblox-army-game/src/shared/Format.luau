--!strict
--[[
	Format
	Number / time formatting shared by every UI.
]]

local Format = {}

local SUFFIXES = { "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc" }

local function withCommas(integer: number): string
	local s = tostring(math.floor(math.abs(integer)))
	local out = s:reverse():gsub("(%d%d%d)", "%1,"):reverse()
	if out:sub(1, 1) == "," then
		out = out:sub(2)
	end
	return out
end

-- Short number: 950 -> "950", 1520 -> "1.52K", 2500000 -> "2.5M".
function Format.Short(n: number): string
	local negative = n < 0
	n = math.abs(n)
	local text: string
	if n < 1000 then
		text = if n == math.floor(n) then tostring(n) else string.format("%.1f", n)
	else
		local index = math.min(math.floor(math.log10(n) / 3), #SUFFIXES)
		local scaled = n / 10 ^ (index * 3)
		local digits = if scaled < 10 then "%.2f" elseif scaled < 100 then "%.1f" else "%.0f"
		text = string.format(digits, scaled):gsub("%.?0+$", "") .. SUFFIXES[index]
	end
	return (if negative then "-" else "") .. text
end

-- Money in whole dollars (no cents, owner request): "$0", "$1,563", then "$1.25M".
function Format.Money(n: number): string
	local negative = n < 0
	n = math.abs(n)
	local text: string
	if n < 1e6 then
		text = withCommas(math.floor(n + 0.5))
	else
		text = Format.Short(n)
	end
	return (if negative then "-$" else "$") .. text
end

function Format.Robux(n: number): string
	return "R$ " .. withCommas(n)
end

-- 75 -> "1:15", 3700 -> "1:01:40"
function Format.Clock(seconds: number): string
	seconds = math.max(0, math.floor(seconds))
	local h = math.floor(seconds / 3600)
	local m = math.floor((seconds % 3600) / 60)
	local s = seconds % 60
	if h > 0 then
		return string.format("%d:%02d:%02d", h, m, s)
	end
	return string.format("%d:%02d", m, s)
end

-- 90 -> "1m 30s", 7200 -> "2h", 5400 -> "1h 30m"
function Format.Duration(seconds: number): string
	seconds = math.max(0, math.floor(seconds))
	local h = math.floor(seconds / 3600)
	local m = math.floor((seconds % 3600) / 60)
	local s = seconds % 60
	if h > 0 then
		return if m > 0 then string.format("%dh %dm", h, m) else string.format("%dh", h)
	elseif m > 0 then
		return if s > 0 then string.format("%dm %ds", m, s) else string.format("%dm", m)
	end
	return string.format("%ds", s)
end

function Format.Percent(fraction: number): string
	local p = fraction * 100
	if p == math.floor(p) then
		return string.format("%d%%", p)
	end
	return string.format("%.1f%%", p)
end

-- Round to cents (only for intermediate maths such as rates per second).
function Format.RoundCents(n: number): number
	return math.floor(n * 100 + 0.5) / 100
end

-- Round money to whole dollars. Every price, payout and stored cash value
-- goes through this (owner request: no cents anywhere).
function Format.RoundWhole(n: number): number
	return math.floor(n + 0.5)
end

return Format
