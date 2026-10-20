# Army Run Tycoon — Architecture & Build Contract

This is the binding contract for everyone writing code in this project. Read the
whole file before writing code. If something here conflicts with your own idea,
this file wins. If something is missing, choose the simplest option that fits
these rules and write a comment `-- ASSUMPTION:` in your code.

The source is a [Rojo](https://rojo.space) project. `rojo build` turns `src/`
into `build/ArmyRunTycoon.rbxlx`, which the owner opens in Roblox Studio on a Mac.

```
src/shared  -> ReplicatedStorage.Shared              (ModuleScripts, both sides)
src/server  -> ServerScriptService.Server            (Main.server.luau + Services/*)
src/client  -> StarterPlayer.StarterPlayerScripts.Client (Main.client.luau + Controllers/* + UI/* + World/*)
```
File suffixes: `*.server.luau` = Script, `*.client.luau` = LocalScript, `*.luau` = ModuleScript.

---------------------------------------------------------------------------------

## 0. Hard rules

1. **Verification.** `bash scripts/check.sh <your files...>` must report *no problems*
   for every file you own (luau-lsp type-check against the real Roblox API), and
   `lune run tests/run` must pass. Run it often. Do not edit files you do not own to
   silence errors — report contract problems in your final message instead.
2. **Strict Luau.** Every new file starts with `--!strict`. Tabs for indentation.
   `task.wait/task.spawn/task.delay` only (never `wait`, `spawn`, `delay`).
   No deprecated APIs (no `BodyVelocity`, `BodyGyro`, `Instance:Remove`, `game.Workspace` → use `workspace`
   or `game:GetService("Workspace")`).
3. **No asset IDs.** Do not reference `rbxassetid://` images, meshes, sounds or
   animations (we cannot upload or verify them). Build everything from Parts,
   Frames, UICorner/UIStroke/UIGradient, SurfaceGui/BillboardGui text and emoji.
   (Humanoid characters keep Roblox's built-in default animations automatically.)
4. **UI = Theme.** All UI goes through `Client/UI/Theme.luau` (FredokaOne, dark text
   outlines, cards, pills, buttons, `Theme.PurchaseDialog` for every Robux-or-cash
   purchase). Do not hand-roll a different visual style.
5. **Network = Net.** All remotes go through `Shared/Net.luau`. Requests use
   `Net.Handle` (server) / `Net.Request` (client) and return `Net.Ok{...}` /
   `Net.Fail("message")`. Push events multiplex `(msg: string, payload: table)` on
   the event channel your system owns (table §3). Never trust the client: the
   server re-validates every request (ownership, mode, proximity, cost).
6. **Config.** Requirement-level switches live in `Shared/Config.luau` (owned by the
   lead; read-only for you). Put your own tunables in your own Defs module.
   Tag every invented number/mechanic with `-- [PROPOSAL]` in a comment.
7. **Money.** Only `DataService:AddCash` / `DataService:SpendCash` change cash.
   Gameplay income (runner payout, section revenue, combat cash) passes
   `{ boostable = true }` so the 2x boost applies. Purchases and refunds do not.
8. **Mode.** A player is in exactly one mode: `"Base" | "Runner" | "Combat"`
   (`ModeService`). Runner/combat requests must check the mode; sessions must clean
   up on `ModeService.ModeChanged` (death, leaving, respawn all force `"Base"`).
9. **Comments.** Each file starts with a header comment that explains what it does
   and lists its public API, matching the style of the foundation files.
   Write for a beginner who will read this in Roblox Studio.
10. **Performance.** Server loops run at <= 30 Hz. Pool or reuse parts for
    projectiles/troops when practical; cap rendered troops (~40 models).

## 1. Foundation (already written — read these files, do not edit them)

| File | Purpose |
|---|---|
| `shared/Config.luau` | palette, areas+slots, benchmark switch, employee rule, coin-route rule, boosts, vehicles, rarities, product IDs |
| `shared/Layout.luau` | every world position (areas, slots, gates, spawn, runner tracks, arenas) |
| `shared/Net.luau` | remotes + request router (rate limited) |
| `shared/Types.luau` | `PlayerData` schema + `DefaultData()` + `Reconcile` |
| `shared/Signal.luau`, `shared/Format.luau`, `shared/Rng.luau` | utilities (Rng is deterministic, works in Lune) |
| `server/Main.server.luau` | auto-loads every ModuleScript in `server/Services/` : `Priority`, `Init(registry)`, `Start()` |
| `server/Services/DataService.luau` | load/save/sync, `AddCash`, `SpendCash`, `CanAfford`, receipts, `PlayerLoaded` |
| `server/Services/ModeService.luau` | modes, slots, `Teleport`, `ReturnToBase`, `ModeChanged` |
| `client/Main.client.luau` | waits for data, auto-loads `client/Controllers/*` (same Init/Start pattern) |
| `client/State.luau` | `State.Get()`, `State.Changed`, `State.Mode()`, `State.ModeChanged` |
| `client/UI/Theme.luau` | UI kit (see header for the full list) |
| `client/Controllers/HUDController.luau` | cash pill, `AddMenuButton`, `AddStatusChip`, toasts |
| `tests/harness.luau`, `tests/run.luau` | Lune harness: `H.load("shared/X")`, `H.test`, `H.expect`, `H.near` |

Service/controller registry: in `Init(registry)` store `registry`; call other
services only from `Start()` or later, e.g. `registry.BaseService:AssignEmployee(...)`.
Always guard optional cross-calls: `local s = registry.BoostService; if s then ... end`.

Shared modules that must run in Lune tests (Economy, Runner*, CombatDefs,
EquipmentDefs) **must not touch services or Instances at require time** and must
require siblings with `require(script.Parent.X)` (or `script.Parent.Parent.X`).

## 2. Ownership

| Agent | Owns (creates) |
|---|---|
| **ECON** | `shared/EconomyDefs.luau`, `shared/Economy.luau`, `shared/Runner/RunnerDefs.luau`, `shared/Runner/RunnerCourse.luau`, `shared/Runner/RunnerSim.luau`, `tests/economy.spec.luau`, `tests/runner.spec.luau`, `scripts/simulate.luau`, `docs/ECONOMY_TABLES.md` (generated) |
| **RUNNER** | `server/Services/RunnerService.luau`, `client/Controllers/RunnerController.luau`, `client/UI/RunnerUI.luau`, `client/World/RunnerVisuals.luau` (+ more `client/World/Runner*.luau` if needed) |
| **BASE** | `server/Services/MapBuilder.luau`, `server/Services/BaseService.luau`, `client/Controllers/BaseController.luau`, `client/World/BuildingFactory.luau`, `client/World/BaseVisuals.luau` (+ more `client/World/Base*.luau`) |
| **COMBAT** | `shared/Combat/CombatDefs.luau`, `server/Services/CombatService.luau`, `client/Controllers/CombatController.luau`, `client/UI/CombatUI.luau`, `client/World/CombatVisuals.luau`, `tests/combat.spec.luau` |
| **GEAR** | `shared/Combat/EquipmentDefs.luau`, `server/Services/InventoryService.luau`, `client/Controllers/InventoryController.luau`, `client/UI/InventoryUI.luau`, `client/UI/ChestUI.luau`, `tests/equipment.spec.luau` |
| **SHOP** | `server/Services/ShopService.luau`, `server/Services/BoostService.luau`, `server/Services/VehicleService.luau`, `shared/VehicleModels.luau`, `client/Controllers/ShopController.luau`, `client/UI/ShopUI.luau`, `client/World/BoostOfferVisuals.luau` |

Folders that do not exist yet (`shared/Runner`, `shared/Combat`, `client/World`) are
plain folders — just create the files inside them.

## 3. Network names (no collisions)

| Owner | `Net.Handle` actions | Push channel (`Net.Fire(channel, player, msg, payload)`) |
|---|---|---|
| ECON | – | – |
| RUNNER | `RunnerPrepare`, `RunnerGo`, `RunnerFinish`, `RunnerLeave`, `BuyRunnerUpgrade` | `"Runner"` |
| BASE | `UnlockSlot`, `UpgradeSection`, `Restock`, `HireEmployeeCash`, `AssignEmployeeToken`, `UnlockGate` | `"Base"`, `"Fx"` |
| COMBAT | `CombatPrepare`, `CombatStart`, `CombatChooseAbility`, `CombatLeave` | `"Combat"` (reliable, both directions), `"CombatSnap"` (unreliable, server→client) |
| GEAR | `EquipItem`, `UpgradeItem`, `ScrapItem` | `"Inventory"` |
| SHOP | `BuyBoostOffer`, `HireEmployeeRobux`, `BuyVehicle`, `EquipVehicle`, `ToggleRide` | `"Boost"`, `"Shop"`, `"Vehicle"` |
| lead | `RequestSync` | `"Sync"`, `"Notify"` |

## 4. Cross-agent APIs (exact signatures — implement exactly, consume exactly)

### 4.1 ECON → everyone: `Shared/Economy.luau` (pure, memoized, Lune-testable)

```lua
Economy.RunBenchmark(area: number): number            -- R(area): average best-case single-run payout of the 10 benchmark runs
Economy.TenRunBenchmark(area: number): number         -- B10(area) = sum of the 10 best-case runs
Economy.BenchmarkDetails(area: number): { upgradeLevel: number, runs: { { level: number, payout: number } }, total: number }
Economy.ReferenceUpgrades(area: number): { troops: number, damage: number, capacity: number }

Economy.SlotUnlockPrice(slotId: string): number        -- revenue section or the Combat Arena slot
Economy.GatePrice(area: number): number                -- cash to open the gate from `area` into `area + 1` (strictly increasing)
Economy.SectionStats(slotId: string, level: number): { incomePerSale: number, saleInterval: number, stockCapacity: number, incomePerSecond: number }
Economy.SectionMaxLevel: number
Economy.SectionUpgradePrice(slotId: string, currentLevel: number): number?   -- nil when at max
Economy.EmployeeCashPrice(slotId: string, playerHighestArea: number): number -- 20-run rule (Config.Employee)
Economy.ReferencePassiveRate(area: number): number     -- $/s of every revenue section in areas <= area, level 1, all with employees

Economy.RunnerUpgradeMaxLevel: number
Economy.RunnerUpgradePrice(kind: string, currentLevel: number): number?      -- kind = "troops" | "damage" | "capacity"; nil at max
Economy.RunnerStats(upgrades: { troops: number, damage: number, capacity: number }, area: number): { startTroops: number, damage: number, capacity: number }
Economy.RunnerUpgradeEffect(kind: string, level: number): string             -- e.g. "+1 troop", "Damage 12", "Carry $85"

Economy.BoostValue(boostId: string, playerArea: number): number              -- V, value when used properly
Economy.BoostCoinPrice(boostId: string, playerArea: number): number          -- (1 - Config.CoinRoute.NetGainShare) * V
Economy.VehicleCoinPrice(vehicleId: string, playerArea: number): number

Economy.CombatLevelReward(level: number): number       -- total cash a perfect clear of combat level `level` drops
Economy.BarrierCombatLevel(area: number): number       -- combat level of the barrier squad on `area`'s exit gate
Economy.EquipmentUpgradePrice(rarity: number, currentLevel: number): number? -- nil when at the rarity's max level
Economy.RunDurationSeconds(): number                   -- typical run length incl. menus (used for 2x boost value)
```
`area` is the 1-based index into `Config.Areas`. Every price and payout is a whole number of dollars
(`Format.RoundWhole`, owner request: no cents anywhere); only rates per second stay fractional.

### 4.2 ECON → RUNNER: runner course + resolver (pure, deterministic)

```lua
-- Shared/Runner/RunnerDefs.luau : constants (RUN_SPEED, LANES, WEAPONS tiers, row spacing, ...)
-- Shared/Runner/RunnerCourse.luau
export type Option = {
	lane: number,            -- 1..#row.options, left -> right
	x: number,               -- lateral centre (studs, + = right of centre line)
	width: number,           -- studs
	op: string?,             -- gate: "add" | "mul" | "sub" | "div" | "weapon" | "damage"
	amount: number?,         -- +N troops, xN, -N, /N, +tiers, +damage fraction (0.2 = +20%)
	label: string,           -- what the gate panel shows: "+5", "x2", "-3", "÷2", "RIFLE", "+20% DMG", "$12"
	tone: string,            -- "positive" (blue) | "negative" (red) | "weapon" (gold) | "cash" | "enemy"
	cash: number?,           -- cash row: value of the cash stack in this lane
	enemies: number?,        -- squad row: number of enemy soldiers
	hp: number?,             -- barricade row: hit points
	reward: number?,         -- cash for destroying the barricade / per enemy defeated
}
export type Row = { index: number, kind: string, distance: number, options: { Option } }
-- kind = "gate" | "cash" | "squad" | "barricade"
export type Course = { area: number, level: number, seed: number, length: number, rows: { Row },
	boss: { distance: number, hp: number, reward: number } }  -- hp is always 0: boss HP = RunnerSim.BossHp(course, stats)
RunnerCourse.Generate(area: number, level: number): Course

-- Shared/Runner/RunnerSim.luau
export type Stats = { startTroops: number, damage: number, capacity: number, payoutBonus: number? }
export type RowOutcome = { row: number, lane: number, kind: string, label: string,
	troopsBefore: number, troopsAfter: number, cashBefore: number, cashAfter: number,
	weaponTier: number, damageMult: number, destroyed: boolean?, note: string? }
export type Line = { kind: string, label: string, detail: string, value: number }
-- kind = "troops" | "weapon" | "damage" | "cash" | "enemies" | "boss" | "bonus"
export type Result = { troops: number, weaponTier: number, damageMult: number,
	collected: number, carried: number, outcomes: { RowOutcome }, lines: { Line }, total: number }
RunnerSim.Resolve(course: Course, stats: Stats, choices: { number }): Result
	-- choices[i] = lane picked in row i; missing / invalid lane -> lane 1
RunnerSim.BestCase(course: Course, stats: Stats): (Result, { number })  -- exact optimum + its choices
RunnerSim.MinDuration(course: Course): number  -- fastest legal completion time (anti-cheat, running part only)
RunnerSim.UnitDps(stats: Stats, weaponTier: number, damageMult: number): number  -- one unit's damage per second
RunnerSim.BossHp(course: Course, stats: Stats): number      -- BOSS_HP_SHARE x the most boss damage any lane path can deal
RunnerSim.BossFight(units: number, unitDps: number, bossHp: number): BossOutcome  -- exact closed form
RunnerSim.BossHpAt(outcome: BossOutcome, t: number): number, RunnerSim.AliveAt(outcome: BossOutcome, t: number): number
-- Result also carries: boss: BossOutcome { hp, victory, units, unitDps, interval, killed, survivors,
--   duration, damageDealt, hpLeft }, unitDps: number. result.troops = units alive AFTER the boss (0 on defeat).
```
The completion summary lists `result.lines` (each upgrade collected with its cash value,
troops, carried cash vs capacity, enemies, boss) and `result.total`.

### 4.3 BASE → SHOP / COMBAT (server)

```lua
BaseService:AssignEmployee(player: Player, slotId: string): boolean      -- unlocked revenue section without employee
BaseService:ApplyTimeSkip(player: Player, seconds: number): number        -- pays skipped revenue, returns cash paid (not 2x-boosted)
BaseService:MarkBarrierDefeated(player: Player, area: number)             -- barrier squad on `area`'s exit gate beaten
BaseService:GetPassiveRate(player: Player): number                        -- current $/s of sections that have stock or an employee
```

### 4.4 SHOP → everyone (server)

```lua
BoostService:GetCashMultiplier(player: Player): number   -- 2 while a 2x boost is active, else 1
BoostService:AddMultiplierMinutes(player: Player, minutes: number)
VehicleService:GetRunnerBonus(player: Player): number    -- fraction, e.g. 0.06 = +6% runner payout (equipped vehicle)
VehicleService:Dismount(player: Player)
```

### 4.5 GEAR → COMBAT

```lua
-- Shared/Combat/EquipmentDefs.luau (pure, Lune-testable)
export type CombatStats = {
	maxHp: number, damage: number, fireRate: number,      -- shots per second
	projectiles: number, spreadDeg: number, projectileSpeed: number, range: number,
	critChance: number, critMult: number, damageReduction: number, -- 0..0.6
	moveSpeed: number,                                   -- humanoid WalkSpeed in combat
	perks: { [string]: number },                         -- perk id -> stacks
}
EquipmentDefs.Items: { [string]: ItemDef }       -- must contain "w_pistol" and "a_vest" (starter items)
EquipmentDefs.LoadoutStats(data: PlayerData): CombatStats
EquipmentDefs.RollChest(chestTier: string, rng: Rng): Item   -- chestTier = "wood" | "silver" | "gold"
EquipmentDefs.PERKS: { [string]: { name: string, description: string } }

-- server/Services/InventoryService.luau
InventoryService:GetLoadoutStats(player: Player): CombatStats
InventoryService:AwardChest(player: Player, chestTier: string): (string?, Item?)  -- uid + item granted (nil if failed)

-- client/UI/InventoryUI.luau (plain module, not a controller)
InventoryUI.Open(opts: { Title: string?, ActionText: string?, OnAction: (() -> ())?, OnClose: (() -> ())? }): () -> ()
	-- inventory grid + equipped slots + item detail with Equip / Upgrade / Scrap.
	-- If ActionText/OnAction are given, a big green action button (e.g. "START BATTLE") is shown.
-- client/UI/ChestUI.luau
ChestUI.Play(chestTier: string, item: Item, onDone: (() -> ())?)  -- chest shake/open/reveal animation
```
Perk ids COMBAT must implement (GEAR may give them to items, COMBAT abilities may grant them too):
`multishot` (+1 projectile per stack), `pierce` (bullets pass through 1 more enemy per stack),
`ricochet` (bullet bounces to 1 more enemy per stack), `crit` (+10% crit chance per stack),
`rapid` (+15% fire rate per stack), `explosive` (bullets splash 30% damage in 6 studs),
`regen` (heal 1% max HP per second per stack), `shield` (start each wave with a 1-hit shield),
`dodge` (10% chance per stack to ignore a hit, max 40%), `thorns` (melee attackers take 50% of
the damage back), `swift` (+8% move speed per stack), `vampire` (heal 2% max HP per kill per stack).

Player attribute `CombatActive` (boolean): COMBAT sets it to `true` from `CombatStart`
until the fight ends (and clears it on any cleanup). GEAR rejects `EquipItem`,
`UpgradeItem`, `ScrapItem` while it is true (the loadout is locked mid-fight; the lobby
before the first round is *not* active, so equipping there works).

### 4.6 COMBAT → BASE (client)

```lua
CombatController:OpenArenaMenu()           -- level select for the repeatable Combat Arena
CombatController:StartBarrier(area: number) -- fight the barrier squad on `area`'s exit gate
```

### 4.7 RUNNER (client)

```lua
RunnerController:OpenPrepare()  -- same as stepping on the ARMY RUN portal (called by HUD button)
```

### 4.8 Lead (later)
`server/Services/DevService.luau` (Studio-only test tools) and `DESIGN.md`/`README.md`
are written by the lead after the build — do not create them.

## 5. World, modes and data

* Positions: **only** from `Layout`. Base areas lie along +X (area i centre
  `Layout.AreaCenter(i)`), runner tracks at z 700..1600, arenas at z = -700.
* The **static** world (floors, paths, white lines, plot outlines, Lego-stud
  hedges, gate corridors, decorations, terrain water, lighting, spawn, runner
  portal pad, runner track floors, arena floors) is built by the **server**
  (BASE: base + water + lighting; RUNNER: `workspace.RunnerTracks`; COMBAT:
  `workspace.Arenas`). Root folder for the base is `workspace.Map`; if it already
  exists (pre-baked) skip rebuilding the parts but still apply terrain/lighting.
* **Per-player progression visuals** (unlock pads, shop buildings, gates with
  padlocks, employees, boost offers, runner gates/pickups, combat enemies) are
  built **on the client** for the local player only. Client-created parts still
  collide with the local character (e.g. a locked gate blocks you).
* `PlayerData` (see `shared/Types.luau`) field owners: `runner.*` RUNNER;
  `base.*` + section state BASE; `combat.*` COMBAT; `inventory`, `equipped`,
  `nextUid` GEAR; `vehicles`, `boosts`, `employeeTokens`, receipts SHOP (BASE may
  read/decrement `employeeTokens` in `AssignEmployeeToken`). Mutate, then
  `DataService:MarkDirty(player)`.

## 6. Gameplay specification

### 6.1 Army runner (ECON + RUNNER)
* Player's own avatar runs automatically down the track (−Z) at `RunnerDefs.RUN_SPEED`;
  input only moves left/right (A/D, ←/→, touch-drag, gamepad stick). Track width
  `Layout.RUNNER_TRACK_WIDTH`. Camera: high behind the player looking down the
  track (like reference image 2). Jumping disabled during the run.
* Rows every ~45 studs: **gates** (2 lanes) with the original maths modifiers applied
  to the troop count — `+N` and `xN` are blue, `−N` and `÷N` are red — plus gold
  **weapon** gates (`+1 weapon tier`: Pistol → Rifle → SMG → Assault Rifle → Minigun → Rocket)
  and **damage** gates (`+20% DMG`); **cash** rows (3 lanes of cash stacks, the
  amount carried is capped by coin-carrying capacity); **enemy squads** (troops
  fight: every unit fires the same gun; squad damage = units × UnitDps × ENGAGE_SECONDS
  (guns reach GUN_RANGE = 1.5 rows) kills enemies, survivors cost troops 1:1);
  **barricades** (HP; destroyed if that damage ≥ HP, else lose 25% of troops).
  Cash-row values are hidden until the previous row is crossed.
  Final **castle boss** (owner request): walks out of the castle with a head HP bar and
  kills one unit per interval (troops first, the avatar last); HP = BOSS_HP_SHARE × the
  best path's potential damage, so only the best lanes win. Reward only on victory.
* Gun: RunnerService equips a "RunnerGun" Tool (invisible Handle) at RunnerGo; the client
  welds the current tier's part-built gun (RunnerGuns) to it. Troops stand beside the
  avatar in rows of 5 (2 + avatar + 2, new rows behind) holding the same gun. Run stats
  (troops, gun, squad DPS, carried / capacity cash) sit in a bottom-left screen panel.
* Permanent upgrades (bought on the pre-run screen or anywhere): **Starting troops**,
  **Damage**, **Coin-carrying capacity**. Each level is a *small* improvement.
  In-run pickups only last for that run.
* Completion summary: every upgrade collected with its cash value, troops ×
  value, cash carried vs capacity, enemies defeated, boss line, vehicle
  bonus, 2x boost, **total**. Then back to base.
* Server authority: `RunnerPrepare` generates the course (server) and teleports
  to the track start; `RunnerGo` locks stats + start time; `RunnerFinish(runId, choices)`
  re-simulates with `RunnerSim.Resolve` (lanes checked against the server-observed path),
  rejects runs faster than `MinDuration × 0.9`, pays `result.total` (already includes the
  vehicle bonus) as boostable cash, increments `runner.level`. The client sends RunnerFinish
  when the squad reaches the castle and animates the SERVER's boss outcome.

### 6.2 Base, revenue sections, employees (BASE)
* 8 areas × 4 slots (`Config.Areas`). Area 2 slot 4 is the Combat Arena (kind `"arena"`).
* Locked slot → green unlock pad with gold price text (image 1 "$1562.50", shown as "$1,563"); a
  ProximityPrompt "Unlock" buys it (`UnlockSlot`). Slots of `highestArea` and below.
* Unlocked revenue section → stylised building (BuildingFactory by `slot.style`,
  `slot.color`, sign with `slot.name` + `slot.icon`), billboard with income per minute and a
  stock bar. Every `saleInterval` seconds a sale pays `incomePerSale` (boostable)
  and uses 1 stock. **At 0 stock the section stops** ("OUT OF STOCK!" red billboard)
  **until restocked manually** (ProximityPrompt "Restock", hold 1 s, must be near
  `Layout.SlotFrontPosition`). Upgrade prompt raises the level (`UpgradeSection`).
* **Employees**: a "Hire me" tile (image 1: blue rounded tile, suited businessman
  emoji 🧑‍💼, "Hire me" text) stands at `Layout.SlotHirePosition`. It opens
  `Theme.PurchaseDialog` with **R$ 200** (`HireEmployeeRobux`, SHOP) or
  **cash = Economy.EmployeeCashPrice** (`HireEmployeeCash`, BASE); if the player has
  `employeeTokens > 0` also offer "Use employee token" (`AssignEmployeeToken`).
  A hired employee (blocky NPC in a suit) auto-restocks 3 s after stock hits 0.
* **Gates**: the exit gate of each area ≥ `highestArea` is a black iron gate with a
  gold padlock (image 1). Prompt: if the barrier is not defeated → "Fight the
  barrier squad" (`CombatController:StartBarrier(area)`); else → "Unlock <next> $price"
  (`UnlockGate`, needs barrier defeated + `Economy.GatePrice(area)`).
* Time skip (`ApplyTimeSkip`): per section, sales = floor(seconds / interval);
  with employee: pays all sales; without: pays min(sales, stock) and reduces stock.

### 6.3 Combat (COMBAT + GEAR) — Archero 2 inspired
* Personal arena per player (`Layout.ArenaCenter(slot)`), top-down camera.
  Move with WASD / thumbstick; **auto-fire at the nearest enemy while standing
  still** (Archero signature); stop shooting while moving. Enemy bullets are slow,
  visible and dodgeable; grenades show a red telegraph circle before exploding.
* Enemy types (CombatDefs): Rifleman (keeps distance, aimed shots), Charger (runs
  in, melee), Grenadier (lobbed AoE), Gunner (3–5 bullet spread), Sniper (laser
  sight then fast shot), Officer boss (radial bullet rings) on the last wave of
  barrier fights and every 5th arena level. HP/damage/count scale with level.
* A level = several waves. After each cleared wave (except the last) the player
  picks **1 of 3 random in-run abilities** (Archero-style: Multishot, Pierce,
  Ricochet, Attack Speed+, Attack+, Crit+, Max HP+, Heal, Shield, Dodge, Swift,
  Vampire…). **In-run abilities last only for that attempt.**
* Cash: enemies drop cash pickups; cash piles also appear on the ground; walk over
  them (magnet radius). Total for a perfect clear ≈ `Economy.CombatLevelReward(level)`.
  Collected cash is paid at the end even on defeat ([PROPOSAL]), boostable.
* **Before the first round**: the lobby opens `InventoryUI.Open{ActionText="START BATTLE"}`
  so the player can review armour and weapons and equip them; the fight begins
  only after `CombatStart`.
* End of every combat game: a **chest** (`wood` on defeat, `silver` on win,
  `gold` on barrier wins and every 5th arena level) via `InventoryService:AwardChest`,
  shown with `ChestUI.Play`. Barrier win → `BaseService:MarkBarrierDefeated`.
  Arena win → `combat.highestLevel = max(highestLevel, level)`.
* Server-authoritative sim of enemies/projectiles as **data** (no server parts);
  clients render from `CombatSnap` (20 Hz, interpolated) and `Combat` events
  (projectile spawns with origin/direction/speed/time). Player hit detection:
  the client reports hits on itself (`Net.FireServer("Combat", "Hit", {projectileId})`),
  the server validates the projectile exists and was near; the server also applies
  any hit it sees itself within 0.6 studs (anti-cheat floor), de-duplicated by id.
* Lasting progression = equipment only: random armour/weapons from chests, rarity
  (Common 60 / Uncommon 25 / Rare 10 / Epic 4 / Legendary 1 base weights, chest tier
  shifts them), higher rarity = better stats + perk slots, coins upgrade item level
  (`Economy.EquipmentUpgradePrice`).

### 6.4 Shop, boosts, vehicles (SHOP)
* Every Robux item has a cash alternative (`Theme.PurchaseDialog`). Robux prices are
  fixed (`Config`); cash prices come from `Economy` and scale with the player's area.
* Developer products: `Config.ProductIds[key]` (0 = not set up → Robux button
  disabled with "Robux not set up"). `MarketplaceService.ProcessReceipt` in
  ShopService must be idempotent (`DataService:HasReceipt/AddReceipt`) and return
  `PurchaseGranted` only after the grant is applied.
* Random **boost offers** (`Config.BoostOffers`): per player, weighted by
  `Config.Boosts[*].weight`, placed on a walkway of an unlocked area; shown as a
  purple bubble like image 1 ("+$357.1 / WATCH AD" style → "2x CASH 15m / BUY");
  walking into it opens the dual-route dialog (`BuyBoostOffer(offerId, "robux"|"cash")`).
* 2x: `boosts.multiplierSeconds` ticks down only while in game; HUD status chip
  "2x CASH 12:30". Time skip → `BaseService:ApplyTimeSkip`, toast with the amount.
* Vehicles (`Config.Vehicles`): unlocked by area, bought with Robux or cash
  (`Economy.VehicleCoinPrice`), equipped in the Shop; "Ride" toggle welds a part-built
  vehicle (`VehicleModels.Build(def)`) to the character in Base mode and sets WalkSpeed.
  Auto-dismount when leaving Base mode. Equipped vehicle gives the runner bonus.
* Employee Robux route: `HireEmployeeRobux(slotId)` stores a pending target and
  prompts the Employee product; the receipt calls `BaseService:AssignEmployee`, or
  adds an `employeeTokens` credit if the target is no longer valid.

## 7. Visual rules (match the reference images)
* Bright, saturated toy look; `Enum.Material.SmoothPlastic` (Neon only for glows,
  bullets, highlights). Colours only from `Config.Palette` (or a slot/vehicle colour).
* Base: sky-blue floor, lighter walkways with white edge lines, plot outlines,
  green Lego-stud hedges (studs = small cylinders on top), yellow/red Lego walls,
  black iron gates with gold padlocks, grass patches with trees and benches, water
  around the island.
* Runner: white track with very light blue stripes, sea on both sides, translucent
  red/blue gate panels (Transparency ~0.45) with dark posts and big white outlined
  text, lime-green cash style, black "Level N" banner, orange/blue cards on the left.
* Text everywhere: FredokaOne, white with dark outline; prices gold or green.
