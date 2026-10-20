--!strict
--[[
	RunnerCourse  (STUB written by the lead — ECON replaces the body, keeps the types/signature)
	See docs/ARCHITECTURE.md §4.2.
]]

export type Option = {
	lane: number,
	x: number,
	width: number,
	op: string?,
	amount: number?,
	label: string,
	tone: string,
	cash: number?,
	enemies: number?,
	hp: number?,
	reward: number?,
}

export type Row = { index: number, kind: string, distance: number, options: { Option } }

export type Course = {
	area: number,
	level: number,
	seed: number,
	length: number,
	rows: { Row },
	boss: { distance: number, hp: number, reward: number },
}

local RunnerCourse = {}

function RunnerCourse.Generate(area: number, level: number): Course
	return { area = area, level = level, seed = level, length = 900, rows = {}, boss = { distance = 880, hp = 100, reward = 10 } }
end

return RunnerCourse
