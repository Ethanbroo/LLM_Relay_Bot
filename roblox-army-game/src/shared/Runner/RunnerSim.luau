--!strict
--[[
	RunnerSim  (STUB written by the lead — ECON replaces the bodies, keeps the types/signatures)
	See docs/ARCHITECTURE.md §4.2.
]]

local RunnerCourse = require(script.Parent.RunnerCourse)

export type Stats = { startTroops: number, damage: number, capacity: number, payoutBonus: number? }

export type RowOutcome = {
	row: number,
	lane: number,
	kind: string,
	label: string,
	troopsBefore: number,
	troopsAfter: number,
	cashBefore: number,
	cashAfter: number,
	weaponTier: number,
	damageMult: number,
	destroyed: boolean?,
	note: string?,
}

export type Line = { kind: string, label: string, detail: string, value: number }

export type Result = {
	troops: number,
	weaponTier: number,
	damageMult: number,
	collected: number,
	carried: number,
	outcomes: { RowOutcome },
	lines: { Line },
	total: number,
}

local RunnerSim = {}

function RunnerSim.Resolve(_course: RunnerCourse.Course, stats: Stats, _choices: { number }): Result
	return { troops = stats.startTroops, weaponTier = 1, damageMult = 1, collected = 0, carried = 0, outcomes = {}, lines = {}, total = 0 }
end

function RunnerSim.BestCase(course: RunnerCourse.Course, stats: Stats): (Result, { number })
	return RunnerSim.Resolve(course, stats, {}), {}
end

function RunnerSim.MinDuration(_course: RunnerCourse.Course): number
	return 30
end

return RunnerSim
