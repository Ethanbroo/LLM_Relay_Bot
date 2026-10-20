--!strict
--[[
	Economy  (STUB written by the lead — ECON replaces the bodies, keeps every signature)
	See docs/ARCHITECTURE.md §4.1.
]]

local Economy = {}

Economy.SectionMaxLevel = 10
Economy.RunnerUpgradeMaxLevel = 50

function Economy.RunBenchmark(_area: number): number
	return 100
end

function Economy.TenRunBenchmark(area: number): number
	return Economy.RunBenchmark(area) * 10
end

function Economy.BenchmarkDetails(_area: number): { upgradeLevel: number, runs: { { level: number, payout: number } }, total: number }
	return { upgradeLevel = 0, runs = {}, total = 0 }
end

function Economy.ReferenceUpgrades(_area: number): { troops: number, damage: number, capacity: number }
	return { troops = 0, damage = 0, capacity = 0 }
end

function Economy.SlotUnlockPrice(_slotId: string): number
	return 100
end

function Economy.GatePrice(area: number): number
	return 1000 * area
end

function Economy.SectionStats(_slotId: string, _level: number): { incomePerSale: number, saleInterval: number, stockCapacity: number, incomePerSecond: number }
	return { incomePerSale = 1, saleInterval = 5, stockCapacity = 20, incomePerSecond = 0.2 }
end

function Economy.SectionUpgradePrice(_slotId: string, _currentLevel: number): number?
	return 100
end

function Economy.EmployeeCashPrice(_slotId: string, _playerHighestArea: number): number
	return 2000
end

function Economy.ReferencePassiveRate(_area: number): number
	return 1
end

function Economy.RunnerUpgradePrice(_kind: string, _currentLevel: number): number?
	return 50
end

function Economy.RunnerStats(_upgrades: { troops: number, damage: number, capacity: number }, _area: number): { startTroops: number, damage: number, capacity: number }
	return { startTroops = 3, damage = 10, capacity = 60 }
end

function Economy.RunnerUpgradeEffect(_kind: string, _level: number): string
	return ""
end

function Economy.BoostValue(_boostId: string, _playerArea: number): number
	return 100
end

function Economy.BoostCoinPrice(_boostId: string, _playerArea: number): number
	return 80
end

function Economy.VehicleCoinPrice(_vehicleId: string, _playerArea: number): number
	return 1000
end

function Economy.CombatLevelReward(_level: number): number
	return 100
end

function Economy.BarrierCombatLevel(area: number): number
	return area
end

function Economy.EquipmentUpgradePrice(_rarity: number, _currentLevel: number): number?
	return 50
end

function Economy.RunDurationSeconds(): number
	return 60
end

return Economy
