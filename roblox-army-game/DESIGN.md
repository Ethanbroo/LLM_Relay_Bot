# Army Run Tycoon — Game Design Outline

This outline answers the design brief in `docs/BRIEF.md` and describes the game as it
is built in this project. Every number below comes from the game's own code
(`docs/ECONOMY_TABLES.md` is generated from it).

**Legend**

| Mark | Meaning |
|---|---|
| ✅ **Requirement** | Stated in your brief. Kept exactly. |
| 💡 **Proposal** | My recommended addition or answer. You can change it. Each one has a `[PROPOSAL]` comment in the code. |
| ❓ **Open question** | One of the 10 points from section 7 of the brief. A proposed answer is given and used in the game until you decide. |
| ⚠️ **Conflict** | Two rules that cannot both be true as written. A decision is needed. |

**Reference images.** Both images were received and used (`docs/reference/`). Colours
were measured from the pixels, not guessed (section 10).

---

## 1. The connected gameplay loop

```
        ┌──────────────── permanent runner upgrades ◄──────────────┐
        ▼                                                          │
  ARMY RUN ──► completion summary ──► CASH ──┬──► unlock / upgrade revenue sections ──► passive CASH
  (troops,     (each upgrade's value,        ├──► hire employees (no more restocking)
   weapons)     troops, cash, total)         ├──► Robux-or-cash items (vehicles, boosts)
                                             ├──► upgrade armour & weapons
                                             └──► pay the GATE to the next area
                                                        ▲
  BATTLE (Archero style) ──► CASH + CHEST (armour/weapon) │
     ├─ barrier squad on every gate: must be beaten ───────┘
     └─ repeatable Combat Arena (unlocked in area 2): farm cash and chests
```

1. ✅ Play the **Army Run**: steer left/right to collect troops and weapon upgrades.
2. ✅ The **completion summary** shows each upgrade's cash value, troop and cash payouts, and the total.
3. ✅ Spend cash on **permanent runner upgrades**, **revenue sections**, **section upgrades** and **employees**.
4. ✅ Beat the **barrier squad** at the gate (combat), then pay the gate's cash price to open the next area.
5. ✅ From **area 2**, the repeatable **Combat Arena** is one of the four unlocks: more cash, plus chests with armour and weapons.
6. ✅ Repeat. Each area costs more (section 7), and every system raises your earning power.

**How the systems feed each other** (✅ requirement: "explain how these sources of progression work together"):

| Source of power | What it improves | Why it matters for later areas |
|---|---|---|
| Permanent runner upgrades | Troops, damage, carry capacity | Bigger runs: an upgraded player earns 2–6× more per run than an un-upgraded one from area 2 on |
| Revenue sections + section upgrades | Passive income (about 32% of the active run rate per area) | Money while you run and fight; upgrades add +20% income per level |
| Employees | Remove restocking | Sections never stop; required for full time-skip value |
| Repeated arena combat | Cash (about 3 runs per level) + a chest every game | Gear for harder barriers |
| Armour and weapons (chests, coin upgrades) | HP, damage, fire rate, perks | Beats the barrier squads, which get harder every area |
| Harder combat levels | Higher cash rewards (+17–26% per level) | Combat stays worth playing as prices rise |

---

## 2. Army runner

### 2.1 Controls and course
* ✅ You control **your own Roblox avatar**. It runs forward automatically; you only steer **left/right**
  (A/D or ←/→, drag on phones, gamepad stick).
* ✅ During the run you collect **troops** and **weapon upgrades** for that round only.
* 💡 A course has **18 rows** across a 900-stud white track over the sea, then an **enemy base** at the end.
  A run lasts about **36 seconds** of running (about 52 s with menus).

| Row type | Lanes | What happens |
|---|---|---|
| **Troop gate** | 2 | Maths gates on your troop count: **+N, ×N** (blue) and **−N, ÷N** (red) |
| **Weapon / damage gate** | 2 | Gold **WEAPON +1** (Pistol → Rifle → SMG → Assault Rifle → Minigun → Rocket) or **+20% DMG** |
| **Cash row** | 3 | Stacks of cash, small/medium/large |
| **Enemy squad** | 1–2 | Your squad fires first; survivors cost troops 1-for-1 |
| **Barricade** | 2 | Weak or strong; destroyed if your firepower is high enough (cash), otherwise you lose 25% of troops |
| **Enemy base (end)** | – | Your remaining squad fires for 5 s; the base pays by the share destroyed |

❓ **Q2 — Original maths modifiers.** 💡 *Kept.* **+, ×, −, ÷ change the troop count** (the troops replace the
coins of the original game). Exactly two **×2** gates appear per course, so troop counts stay readable.
Red gates can also carry cash ("toll gates": −3 troops but +$14.50), which gives a real choice.
Weapons and damage use their own gold gates, not maths.

### 2.2 Payout and completion summary
✅ After every run a summary lists **every upgrade obtained with its cash value**, and **the total**.

| Summary line | 💡 How it is valued (area 1 values; ×1.6 per area) |
|---|---|
| Weapon: Rifle / SMG / Assault Rifle / Minigun / Rocket | $8 / $12 / $18 / $25 / $35 for each tier reached |
| +20% Damage (each damage gate) | $9 |
| Troops × N | $1.50 per troop alive at the finish |
| Cash carried | Cash you picked up, **capped by your coin-carrying capacity** (overflow shown as lost) |
| Enemies defeated × N | $0.50 per soldier, plus barricade rewards |
| Enemy base destroyed X% | Up to $60 × the share destroyed |
| Vehicle bonus | +2% to +20%, from your equipped vehicle |
| 2× CASH | Doubles the total while a 2× boost is active |

❓ **Q7 — Runner payouts.** 💡 Upgrades are valued by the table above. **Troops pay out** ($ per surviving troop).
**Coin-carrying capacity caps the cash you pick up on the track** (not the troop or upgrade values), so
the capacity upgrade always matters.

### 2.3 Visuals
✅ Matches the colours and saturation of reference image 2 (section 10): cyan sky and sea, white track with faint
blue stripes, translucent red/blue gates with dark posts and huge outlined numbers, a lime-green money tag
above the squad, a black "Level N" banner, orange/blue cards on the left, and the tutorial hand with ←→.

---

## 3. Permanent runner upgrades (before a run)

✅ Bought on the pre-run screen (cards on the left, like image 2) and kept forever.
✅ Each level is a **small** improvement.

| Upgrade | Start | Per level 💡 | Max 💡 | Price 💡 |
|---|---|---|---|---|
| **Starting troops** | 3 | +1 troop | level 50 | $90 at level 0, ×1.17 per level |
| **Damage** | 10 | +4% | level 50 | same as troops |
| **Coin-carrying capacity** | $60 in area 1 | +6% | level 50 | 90% of the troops price |

💡 Carry capacity is re-based per area: an un-upgraded player carries $60 / $77 / $104 in areas 1 / 2 / 3, and a player at the
reference upgrade level carries $60 × 1.6^(area − 1). This keeps the capacity upgrade meaningful in every area.

✅ **Permanent upgrades vs in-run pickups** are kept separate: the pre-run cards are titled **PERMANENT UPGRADES**.
The summary lists **Collected this run** (troops, weapons, damage gates, cash), which reset every run.

---

## 4. Base: areas, revenue sections, restocking, employees

### 4.1 Areas and the four unlocks
✅ Many areas, later ones cost more. 💡 **8 areas**, each with **4 unlock slots**:

| # | Area | Slot 1 | Slot 2 | Slot 3 | Slot 4 |
|---|---|---|---|---|---|
| 1 | Boot Camp | Ration Stand | Boot Shop | Canteen | Barber Hut |
| 2 | Supply Depot | Ammo Store | Uniform Shop | Med Tent | **⚔️ Combat Arena** |
| 3 | Motor Pool | Fuel Station | Tire Shop | Repair Garage | Car Wash |
| 4 | Airfield | Parachute Shop | Radar Tower | Hangar Cafe | Flight School |
| 5 | Harbor | Fish Market | Boat Rentals | Dock Warehouse | Lighthouse Shop |
| 6 | Desert Outpost | Water Depot | Supply Bazaar | Solar Farm | Oasis Cafe |
| 7 | Mountain Fortress | Climbing Gear | Hot Cocoa Hut | Ski Rental | Armory |
| 8 | Command HQ | Medal Shop | War Room Cafe | Satellite Uplink | Officer Club |

❓ **Q5 — Four unlocks at stage two.** 💡 In area 2, combat **replaces the fourth revenue section**:
3 shops + the **Combat Arena**. The arena **counts as the area's fourth cash source**. It earns actively
(you play it) rather than passively. Area 2 shops are priced so they still pay back (about 27 min).

### 4.2 How a revenue section works
* ✅ Unlocked with earned cash: a green-bordered **unlock pad** with a gold price (image 1). Hold **E** to buy.
* 💡 Each sale pays cash every 4–6 s and uses **1 stock** (25–37 stock).
* ✅ **At 0 stock the shop stops** ("OUT OF STOCK!") **until you restock it by hand** (hold E at the shop).
  That is every 1.7–3.6 minutes.
* ✅ **Improve revenue:** 💡 10 upgrade levels, +20% income and +10% stock each.
* 💡 Payback of a new level-1 shop: about **40 minutes** of stocked selling.

### 4.3 Employees
* ✅ An employee does a specific job: **restocking**. Your shop restocks itself 3 s after it runs out, so the manual work is gone.
* ✅ Two ways to pay, both kept: **200 Robux** or **cash = earnings of 20 army runs**.
* ❓ **Q4 — Employee cash price.** 💡 "20 runs" = **20 × R(area)**, where **R** is the *best-case single-run*
  benchmark of the area the shop is in (section 8). Prices: **$4,521 (area 1) → $286,827 (area 8)**.
  Using the shop's own area keeps automating old shops cheap. Switch: `Config.Employee.PriceScalesWith`
  (`"SectionArea"` or `"PlayerArea"`).
* 💡 The "Hire me" tile from image 1 stands next to every shop without an employee.

### 4.4 Gates between areas
✅ To open the next area you need **both**: beat the **barrier squad** (combat) and pay the **gate price**.
Gates are black iron with a gold padlock (image 1).

---

## 5. Combat (Archero 2 inspired)

### 5.1 Barriers and the repeatable mode
* ✅ Every gate is guarded by a **wave of soldiers**. You must beat them to unlock the next area.
* ✅ Combat = **move around, dodge attacks, collect upgrades** between waves.
* ❓ **Q6 — The first barrier.** 💡 **Barrier fights are always available at every gate, including area 1**,
  and you can retry them freely. What unlocks in area 2 is the **repeatable Combat Arena**: free level choice
  for farming cash and chests. The first barrier (combat level 2) is tuned for starter gear plus in-fight abilities.
* 💡 Barrier of area *a* = combat level **3a − 1**: levels 2, 5, 8 … 20 (7 barriers; area 8 is the last area, so it has no exit gate).
* ✅ **Later levels are harder and pay more**: enemy HP ≈ ×1.18 and damage ≈ ×1.10 per level, more and
  tougher enemy types. Reward for a perfect clear: **$538 (level 1) → $65,209 (level 25)**.

### 5.2 Earning cash
✅ Defeated enemies drop cash, and **cash piles lie on the ground**. 💡 About 70% of a level's cash comes from
enemies and 30% from ground piles. You keep collected cash even if you lose.

### 5.3 Upgrades during a fight vs lasting progression
❓ **Q8 — Combat progression.**
* 💡 **During a fight (this attempt only):** after each cleared wave, pick **1 of 3 random abilities**
  (Archero style). These reset when the fight ends. See section 5.5 for the full list.
* ✅ **Lasting:** only **armour and weapons** from **chests**, plus **coin upgrades** to those items.

### 5.4 Fight flow
1. ✅ **Before round 1** the **inventory** opens: review your armour and weapons, **equip** what you want,
   then press **START BATTLE**.
2. Waves of soldiers. 💡 **Auto-fire at the nearest enemy while standing still**; moving stops your fire
   (the Archero rule). Enemy bullets are slow and dodgeable, and grenades show a red warning circle first.
3. Ability pick after every wave except the last.
4. ✅ **A chest at the end of every game.** 💡 Wood on a loss (only if you defeated at least one enemy),
   silver on a win, gold for barrier wins and every 5th arena level.

### 5.5 Enemies and abilities
💡 **Enemy types** (`Shared/Combat/CombatDefs.luau`). Base stats are for level 1:

| Enemy | Appears from | Behaviour | HP / damage |
|---|---|---|---|
| Rifleman | level 1 | Keeps its distance, aims for 0.55 s, then fires a slow bullet (22 studs/s) | 30 / 8 |
| Charger | level 1 | Crouches for 0.7 s ("!" warning), then dashes at you; melee hit | 42 / 12 |
| Grenadier | level 3 | Lobs a grenade; a **red circle** shows where it lands 0.9 s before it explodes | 34 / 15 |
| Gunner | level 5 | Fires a fan of 3–5 bullets | 58 / 6 per bullet |
| Sniper | level 7 | Shows a **laser sight** for 1 s, then fires one fast shot along it | 26 / 20 |
| **Officer (boss)** | last wave of every barrier fight, every 5th arena level | Rings of 14 bullets; every third attack is an aimed 5-bullet burst instead | grows with level |

* 💡 Waves: arena levels have 3 waves, plus 1 every 4 levels (max 6). Barrier fights have 4 waves, with the boss in the last.
* 💡 Per level: enemy HP ×1.18, damage ×1.10, more enemies, and more of the dangerous types.
  Bullets stay slow enough to dodge (17–26 studs/s). The exception is the Sniper's single shot (46–55 studs/s), which is telegraphed by its laser sight first.
* 💡 Cover blocks (crates, sandbags, brick walls) in every arena stop bullets from both sides.

💡 **In-fight abilities** (pick 1 of 3 after each wave; **they reset when the fight ends**):

| Ability | Effect | Max |
|---|---|---|
| 🔱 Multishot | +1 bullet every shot | 3 |
| 🗡️ Pierce | Bullets pass through 1 more enemy | 3 |
| 🔁 Ricochet | Bullets bounce to 1 more enemy | 3 |
| ⚡ Attack Speed+ | +15% fire rate | 5 |
| 💥 Attack+ | +20% damage | 5 |
| 🎯 Crit+ | +10% critical-hit chance | 4 |
| ❤️ Max HP+ | +20% max HP (and heals that much) | 5 |
| 🩹 Heal | Restore 40% of max HP | – |
| 🛡️ Shield | Start every wave with a shield that blocks 1 hit | 2 |
| 💨 Dodge | +10% chance to ignore a hit (max 40%) | 4 |
| 👟 Swift | +8% move speed | 3 |
| 🧛 Vampire | Heal 2% of max HP per kill | 3 |
| 💣 Explosive | Bullets splash 30% damage around the target | 2 |
| 🌿 Regen | Heal 1% of max HP every second | 3 |
| 🌵 Thorns | Melee attackers take 50% of their damage back | 2 |

---

## 6. Equipment, chests, rarity and upgrades

✅ Lasting combat progression = **armour and weapons from chests**, upgraded with **coins**.

### 6.1 Items (`Shared/Combat/EquipmentDefs.luau`)
💡 Two slots: **weapon** and **armour**. Everyone starts with a Common Service Pistol and a Common Field Vest.
Stats below are for a Common level-1 item.

| Weapon | Damage | Shots/s | Special |
|---|---|---|---|
| Service Pistol | 10 | 2 | starter |
| SMG | 5.5 | 5 | small spread |
| Shotgun | 7 × 3 pellets | 1.4 | wide spread, short range |
| Assault Rifle | 9 | 3.2 | long range |
| Sniper Rifle | 40 | 0.7 | very long range, built-in **pierce**, +10% crit |
| Minigun | 3.2 | 10 | spray |
| Rocket Launcher | 30 | 0.8 | built-in **explosive** |

| Armour | Bonus HP | Damage reduction | Special |
|---|---|---|---|
| Field Vest | +20 | 4% | starter |
| Kevlar Vest | +35 | 10% | – |
| Ghillie Suit | +15 | 3% | +1 speed, built-in **dodge** |
| Riot Armour | +40 | 20% | −2 speed |
| Medic Rig | +25 | 6% | built-in **regen** |
| Exo Suit | +30 | 8% | +3 speed |
| Juggernaut | +90 | 15% | −4 speed |

### 6.2 Rarity
✅ Higher rarity = better stats and abilities, and rarer.

| Rarity | Stat multiplier | Max level | Bonus perks (abilities) |
|---|---|---|---|
| Common | ×1.0 | 10 | 0 |
| Uncommon | ×1.25 | 20 | 0 |
| Rare | ×1.6 | 30 | 1 |
| Epic | ×2.1 | 40 | 2 |
| Legendary | ×2.8 | 50 | 3, including the item's **signature** perk (e.g. Rocket "Big Boom") |

💡 Perks are rolled when the item drops and use the same effects as the in-fight abilities
(multishot, pierce, ricochet, crit, rapid fire, explosive, regen, shield, dodge, thorns, swift, vampire).
Damage reduction is capped at 60%.

### 6.3 Chests
✅ A chest after **every** combat game, with a random weapon or armour (50/50). ✅ Rarer = less likely.

| Chest | When 💡 | Common | Uncommon | Rare | Epic | Legendary |
|---|---|---|---|---|---|---|
| Wooden | defeat (with at least 1 kill) | 62% | 27% | 8% | 2.5% | 0.5% |
| Silver | arena win | 45% | 32% | 15% | 6% | 2% |
| Golden | barrier win / every 5th arena level | 36% | 30% | 20% | 10% | 4% |

### 6.4 Coin upgrades
* ✅ Spend coins to level up items in the **Gear** menu (or in the battle lobby). 💡 Each level adds +6% weapon damage or armour HP, and +2% to armour damage reduction (cap 60%). Fire rate, bullets and range are fixed per item.
* 💡 Price: Common level 1→2 = $113, ×1.15 per level, ×1 / 1.5 / 2.2 / 3.2 / 4.5 by rarity
  (e.g. Legendary 49→50 = $416,739).
* 💡 **Scrap** unwanted items for cash. The inventory holds 60 items; a drop into a full inventory is paid out as cash.

---

## 7. Difficulty, income and unlock costs

✅ **Each new area costs more than the previous one** (tested automatically):

| Area | Best-case run R | 4 slots | Gate to next | Area total | Employee (cash) |
|---|---|---|---|---|---|
| 1 Boot Camp | $226 | $3,349 | $2,939 | $6,287 | $4,521 |
| 2 Supply Depot | $452 | $7,237 | $5,880 | $13,117 | $9,046 |
| 3 Motor Pool | $803 | $11,897 | $10,440 | $22,337 | $16,062 |
| 4 Airfield | $1,279 | $18,952 | $16,630 | $35,582 | $25,585 |
| 5 Harbor | $2,356 | $34,898 | $30,623 | $65,521 | $47,113 |
| 6 Desert Outpost | $4,545 | $67,331 | $59,083 | $126,413 | $90,896 |
| 7 Mountain Fortress | $7,686 | $113,866 | $99,917 | $213,783 | $153,719 |
| 8 Command HQ | $14,341 | $212,464 | – (last area) | $212,464 | $286,827 |

* 💡 Money values grow **×1.6 per area**. Enemies grow too, so upgrades stay necessary.
* 💡 Pacing estimate (75% of best play, sections kept stocked, combat ignored): **about 26–34 minutes per area,
  about 3.5 hours for all 8 areas**. Combat cash and section upgrades make real play faster. To make the game
  longer, raise `EconomyDefs` gate/slot shares (see `docs/ECONOMY_TABLES.md` §11).

---

## 8. The 10-run benchmark (algorithm and assumptions)

✅ Requirement: estimate **best-case cash across 10 simulated runs** and use it as the baseline for **three area upgrades**.

**Algorithm** (`Shared/Economy.luau`, `Shared/Runner/RunnerSim.luau`):
```
for each area a:
    upgrades  = ReferenceUpgrades(a)          -- 💡 every permanent upgrade at level (a-1) × 4
    stats     = RunnerStats(upgrades, a)
    for run k in 0..9:
        course   = RunnerCourse.Generate(a, level = 1 + 10(a-1) + k)   -- the same course generator players use
        best[k]  = RunnerSim.BestCase(course, stats).total             -- exact optimum over every lane choice
    B10(a) = sum(best)      R(a) = B10(a) / 10
```
* The runner level is global and rises with every run, so players in area 2 and later usually play *later* levels of an
  area than the 10 benchmark levels. Those levels pay up to +20% more and have stronger enemies. **R is the best-case
  estimate at an area's first levels, not a cap** on what a player can earn.
* **BestCase is exact.** It explores every lane choice with dynamic programming. A choice is dropped only if another
  choice is at least as good on troops, weapon tier, damage, cash carried and banked value at the same time.
  Payout can only rise with each of these, so no better path is ever discarded. (Largest search: 29 states.)
* ❓ **Q3 — Which upgrade level?** 💡 The **reference player**: each permanent upgrade at (area − 1) × 4
  (`Config.Benchmark.ReferenceUpgradeLevelsPerArea`).
* ❓ **Q3 — What are the "three area upgrades"?** Not silently chosen; it is a **switch** (`Config.Benchmark.Mode`):

| Mode | Meaning | Area 1 prices |
|---|---|---|
| `"CombinedSections"` 💡 **default** | The first three revenue-section unlocks of an area **together** cost B10 | $586 + $753 + $921 = $2,260 = B10 |
| `"EachSection"` | **Each** of the first three section unlocks costs B10 | 3 × $2,260 (about 2× slower game) |
| *upgrade tiers* (not built) | Three upgrade tiers of one section cost B10 | — |

R values: **$226, $452, $803, $1,279, $2,356, $4,545, $7,686, $14,341** (areas 1–8).

---

## 9. Robux purchases, cash alternatives, random boosts, price scaling

### 9.1 Two payment routes for everything (✅)
| Item | Robux (fixed ✅) | Cash price (scales with your area ✅) |
|---|---|---|
| Employee | 200 ✅ | 20 × R(area) ✅ ($4,521 → $286,827) |
| 2× Cash 15 / 30 / 60 min | 29 / 49 / 89 💡 | 80% of its value (area 1: $3,115 / $6,230 / $12,459) |
| Time skip 1/2/4/8/12/24 h ✅ | 25 / 45 / 79 / 149 / 199 / 349 💡 | 80% of its value (area 1: $3,986 … $95,672) |
| Vehicles (8, one per area ✅) | 49 → 799 💡 | price factor × the gate price of your area (area 1 bike: $1,763) |

* ✅ **Vehicles**: a better one unlocks with each area (Army Bike → Jeep → Supply Truck → Humvee → APC → Light Tank →
  Heavy Tank → Hover Tank). 💡 Ride them around the base (faster: speed 26 → 46) and get a **runner payout bonus**
  (+2% → +20%) while equipped.
* ✅ **Coin prices** vary by rarity/value, scale with the area you have unlocked, and are tied to nearby costs
  (boost values come from your area's run and shop income; vehicle prices from your area's gate price).

### 9.2 Boost behaviour
❓ **Q9 — Boosts.**
* 💡 **2× CASH** doubles **all cash you earn**: run payouts, shop sales, combat cash. Durations **stack** (the
  multiplier stays 2×). The timer only counts down **while you are in the game**.
* 💡 **Time skip** instantly pays what your shops would earn in that many hours. **Shops with an employee pay the full
  time** (the employee keeps restocking). **Shops without one pay only until their stock runs out.** This rewards
  hiring employees. 2× does not double time skips.

### 9.3 Random appearances on the home map
✅ 2× boosts and time skips appear randomly; cheaper ones appear more often.
❓ 💡 They are **purchase offers** (like the purple "+$357.1" bubble in image 1): a glowing bubble appears on a
walkway every 2.5–5 minutes and stays 90 s (max 2 at once). Walk into it to choose **Robux or cash**.

| Boost | Chance of each appearance |
|---|---|
| 2× 15 min | 26.7% |
| 2× 30 min | 10.7% |
| 2× 60 min | 3.6% |
| Skip 1 h | 26.7% |
| Skip 2 h | 16.0% |
| Skip 4 h | 8.9% |
| Skip 8 h | 4.4% |
| Skip 12 h | 2.2% |
| Skip 24 h | 0.9% |

### 9.4 The 80% efficiency rule
❓ **Q10.** 💡 The comparison is defined first, before any price multiplier is chosen:
* **Efficiency = net in-game cash a purchase gives you.**
* **Value V** = cash the boost produces **when used properly**. For 2×: playing runs for its whole duration.
  For a time skip: all shops in your areas running with employees.
* **Robux route:** you get V and spend no cash, so you are up by **+V**.
* **Cash route:** you get V and pay P, so you are up by **V − P**.
* **"80% less efficient":** V − P = 20% of V, so **P = 0.8 × V**.
* **"Slightly profitable":** you still gain **+25%** on what you paid (V / P = 1.25), but only if you use it fully.
* **Applies to:** 2× multipliers and time skips (their value can be measured). Employees use the explicit
  20-run rule. Vehicles use the gate-price anchor (their value is speed, not cash).
* Change it with `Config.CoinRoute.NetGainShare` (0.2 = 80% less efficient).

---

## 10. Visual direction (from the attached images)

Colours were measured from the reference pixels (`Config.Palette`).

| Element | Colour (RGB) | From |
|---|---|---|
| Base floor / walkway / plot band | (106,178,252) / (146,188,252) / (202,226,252) | image 1 |
| Lego hedge / yellow wall / red wall | (88,192,64) / (234,182,26) / (226,36,42) | image 1 |
| Unlock pad fill / border / price | (244,250,250) / (84,212,84) / gold | image 1 |
| Gold padlock / iron gate | (254,210,62)-ish / (40,40,46) | image 1 |
| Sea / sky | (16,168,250) / (52,194,250) | images 1+2 |
| Track / stripes | (236,246,255) / (212,234,252) | image 2 |
| Gate red / blue (at 45% transparency) | (238,40,60) / (78,145,242) | image 2 |
| Money tag | lime (108,240,84) | image 2 |
| Card header / body / pink header | (252,164,12) / (12,142,252) / (252,124,250) | image 2 |
| Cash pill / level banner | black (24,24,36) | images 1+2 |

* Bright, saturated "toy" look: smooth plastic, Lego studs on walls, chunky rounded font (FredokaOne) with dark
  outlines, coins drawn in gold, no grey or realistic textures. Colour correction adds saturation.
* No image uploads are needed: every icon is built from shapes and emoji, so the game runs immediately.

---

## 11. Points to resolve (from brief section 7)

| # | Question | 💡 Proposed answer used in the game | Change it in |
|---|---|---|---|
| 1 | Currency names | **One** earned currency ("cash", shown as $ with a coin). Robux is the only other currency. | `Config.Currency` |
| 2 | Maths modifiers | Kept: + × − ÷ act on **troop count**; weapons/damage have their own gates | `RunnerCourse`, `RunnerDefs` |
| 3 | 10-run benchmark | Best-case, exact optimiser, reference upgrades (area−1)×4; "three upgrades" = **switch**, default *first three sections together* | `Config.Benchmark` |
| 4 | Employee price | 20 × best-case run of the **shop's** area | `Config.Employee` |
| 5 | Four unlocks in area 2 | 3 shops + Combat Arena; the arena counts as the 4th cash source | `Config.Areas[2]` |
| 6 | First barrier | Barrier fights exist at every gate from the start; area 2 unlocks the *repeatable* arena | `Economy.BarrierCombatLevel` |
| 7 | Runner payouts | Value table in §2.2; troops pay; capacity caps track cash | `RunnerDefs` |
| 8 | Combat progression | In-fight abilities = this attempt only; gear from chests + coin upgrades = lasting | `CombatDefs`, `EquipmentDefs` |
| 9 | Boosts | Purchase offers; 2× = all cash, stacks, play-time; skips pay full with employees, until empty without | `Config.Boosts`, `BoostService` |
| 10 | 80% rule | Net-gain definition: cash price = 0.8 × value (+25% if used fully), for boosts | `Config.CoinRoute` |

## 12. Conflicting rules that need your decision

1. ⚠️ **"Four revenue sections per area" vs "combat takes one of area 2's four unlocks."** Both cannot hold in area 2.
   *Used:* area 2 has 3 shops + the arena. *Alternative:* give area 2 a 5th slot.
2. ⚠️ **"Combat is required to unlock the next area" vs "repeatable combat starts at stage 2."** Area 1's gate needs
   combat before the mode exists. *Used:* one-off barrier fights at every gate from the start; only the *repeatable*
   arena waits for area 2.
3. ⚠️ **"Coin prices 80% less efficient than Robux" vs the employee rule "200 Robux = 20 runs of cash."** The employee
   rule is a fixed equivalence, not 80% less efficient. *Used:* the explicit employee rule wins; the 80% rule applies to boosts.
4. ⚠️ **"80% less efficient" vs "slightly profitable".** Under most readings (e.g. 5× the price) a coin purchase would lose
   money. *Used:* the net-gain definition (§9.4), which satisfies both.
5. ⚠️ **Robux prices fixed vs cash prices scaling** makes the cash/Robux exchange rate drift with progression.
   This is intended by the brief and is noted here because it changes the value of Robux over time.
6. ⚠️ **"Three area upgrades" is undefined.** Kept as a switch (§8) until you choose.

## 13. Requirement checklist

REQUIREMENT_CHECKLIST_PLACEHOLDER
