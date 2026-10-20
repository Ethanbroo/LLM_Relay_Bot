# Roblox Army Game: Design Brief (verbatim from the owner)

> Owner's request: "Change my game to include everything that is in my prompt with correct
> details, colouring, and gameplay by going off and fixing what ChatGPT has already done
> (I have attached 2 images of a game to reference colouring and gameplay)."
> The two images are in `docs/reference/` and described in `docs/REFERENCE_IMAGES.md`.

## Objective

Discard the previous game concept and create a detailed outline for a Roblox game built around **two core systems and one additional system**:

1. **An army runner:** The player moves their Roblox avatar left and right to collect troops and weapon upgrades during a run.
2. **Base expansion and revenue management:** The player spends earned money to develop their base, unlock revenue-generating sections, and hire employees.
3. **An additional combat mode inspired by Archero 2:** The player fights waves of soldiers, dodges attacks, earns cash, and improves equipment to overcome barriers that unlock later areas.

These systems must connect into one progression loop. Keep the requirements below, and clearly label any additional mechanics, numbers, or assumptions you propose.

---

## 1. Overall Gameplay Loop

The intended loop is:

1. Play the army runner to collect troops and weapon upgrades.
2. Receive a completion summary showing the currency value of the upgrades collected and the cash earned from the run.
3. Spend earned money on permanent runner upgrades, base development, revenue sections, and employees.
4. Fight waves of soldiers to overcome barriers to the next area.
5. Once the repeatable combat mode becomes available at the second stage, use it as another source of cash and equipment progression.
6. Continue improving the runner, base revenue, and combat equipment to afford more expensive areas and defeat harder enemies.

Each later area must require more coins than the previous area. Progression should remain achievable through improvements across these connected systems.

## 2. Core System One: Army Runner

### Theme and controls

Replace the original coin-runner concept with an army-themed version.

In the original concept, the player moved left and right as a coin, collecting more coins through doubling, addition, subtraction, and division.

In the new version:

- The player controls their Roblox avatar.
- The player moves left and right through the course.
- The player collects weapon upgrades and additional troops for that round.

Preserve the reference to the original mathematical modifiers, but clarify how—or whether—doubling, addition, subtraction, and division should apply to the new army mechanics.

### Completion summary and cash rewards

At the end of every run, display a completion summary that shows:

- The upgrades the player obtained.
- The currency value assigned to each upgrade.
- The total cash earned from the run.

The player can use this money to progress through the wider game, including upgrading their base and hiring employees.

### Permanent upgrades before a run

Before starting a run, players can purchase permanent improvements to:

- **Starting troops:** The number of troops they begin with.
- **Damage:** Their starting damage.
- **Coin-carrying capacity:** The amount of currency they can carry.

Each purchase must provide only a **small improvement**, but that improvement remains permanently.

Clearly distinguish these purchased starting-stat improvements from the troops and weapon upgrades collected during an individual run.

### Visual requirements

The army runner must match the **colours and saturation of the uploaded reference image**.

Use the image to guide the visual treatment. If the image is unavailable, identify the missing reference before specifying a palette.

## 3. Core System Two: Base Expansion and Revenue

### Base development

Players spend earned money to:

- Upgrade their base.
- Unlock additional areas.
- Unlock revenue-generating sections within those areas.
- Improve their revenue-generating systems.
- Hire employees to help with specific tasks.

There should be many areas to unlock, with later areas costing more than earlier ones.

### Four unlocks per area

Each area is intended to contain **four unlockable cash-revenue sections**.

These sections require money earned through gameplay to unlock.

There is one additional requirement to account for: **at the second stage, the repeatable combat mode takes up one of the four area unlocks.** Clarify how this fits with the requirement for four revenue sections per area.

### Restocking and employees

Revenue sections require **manual restocking until an employee is purchased**.

Employees help with specific tasks that support revenue generation and remove the associated manual work.

To unlock an employee, the player must have two payment options:

- **200 Robux**, or
- **In-game cash equivalent to the earnings from 20 army-runner runs.**

Preserve both payment routes. Define the earnings benchmark used to calculate the 20-run cash price.

## 4. Additional System: Combat Against Waves of Soldiers

### Purpose and area barriers

To unlock the next area, the player must defeat a wave of soldiers.

Combat should involve:

- Moving around the map.
- Dodging enemy attacks.
- Collecting upgrades that make the waves easier to defeat.

Use **Archero 2** as the gameplay reference for this combat concept.

The combat challenge works alongside the cash requirements for area progression.

### Repeatable mode availability

At the **second stage**, players unlock access to this combat mode for repeated play.

This mode occupies **one of that area's four unlocks**.

Repeated play should help the player prepare for the next area barrier by earning money and improving equipment and abilities.

### Cash rewards

Players earn cash in combat by:

- Defeating enemies.
- Collecting cash found on the ground.

Later combat levels must be more difficult but award more coins.

### Equipment and lasting progression

Lasting combat progression should come from obtaining armour and weapons through chests and spending coins to upgrade that equipment.

The intended equipment system is:

- Chests are awarded at the end of each combat game.
- Chests contain random armour or weapons.
- Equipment has different rarities.
- Higher-rarity equipment has better stats and abilities.
- Higher-rarity equipment has a lower chance of appearing than common equipment.
- Players spend coins to upgrade the armour and weapons they obtain.

Keep this progression requirement distinct from the upgrades collected while fighting waves. Clarify which upgrades are available during combat and whether those upgrades last beyond the current attempt.

### Inventory and equipping items

Inside the combat game, **before the first round begins**, players must be able to:

- Access their inventory.
- Review their collected armour and weapons.
- Equip the items they want to use.

Improved equipment and abilities should make it easier to defeat the soldiers blocking access to the next area.

## 5. Economy and Progression Requirements

### Army-runner simulation benchmark

The army runner needs an algorithm that estimates **best-case cash accumulation across 10 simulated runs**.

Use this estimate as the baseline for **three area upgrades**.

The meaning of "three area upgrades" needs clarification before finalizing prices. Preserve the 10-run and three-upgrade requirements without silently choosing whether they refer to individual sections, complete areas, or upgrade tiers.

### Increasing costs and earning power

Each new area requires more coins than the previous stage.

Players should improve their ability to afford later unlocks through:

- Permanent army-runner upgrades.
- Upgrades to revenue-generating sections.
- Employees that handle required tasks such as restocking.
- Repeated combat runs.
- Stronger armour, weapons, and abilities.
- Harder combat levels that award more coins.

Explain how these sources of progression work together when creating the outline.

## 6. Purchases, Boosts, and Microtransactions

### Two payment routes

The game will include items and advantages purchasable with Robux.

These must also be obtainable using earned coins, although the coin prices should be high enough to require substantial playtime.

The purchase categories are:

| Category | Required behaviour |
|---|---|
| **Vehicles** | Different and better vehicles become available as the player unlocks new areas. |
| **2× multipliers** | Provide a doubling effect. The affected rewards or stats and the duration still need to be defined. |
| **Time skips** | Skip between **1 and 24 hours of in-game time**, allowing automatic revenue sections to pay the player for the skipped period. |
| **Employees** | Help with specific revenue-related tasks and remove the associated manual work. Cost **200 Robux** or cash equivalent to **20 army-runner runs**. |

### Random appearances on the home map

**2× multipliers and time skips** appear randomly on the home map.

Their appearance must be based on luck:

- Less valuable options appear more frequently.
- More valuable options appear less frequently.

Clarify whether these appearances represent purchasable offers, collectible rewards, or another arrangement before finalizing that interaction.

### Coin prices and progression scaling

Coin prices for these purchases must:

- Be expensive enough to take time to obtain.
- Vary according to rarity.
- Scale according to the area the player has unlocked.
- Reflect the cost of nearby or surrounding upgrades and area unlocks.

**Robux prices remain fixed**, while the equivalent coin prices change with progression.

### Intended efficiency and profitability

Preserve this balancing intention:

> Buying an upgrade or boost with earned coins should be "80% less efficient than Robux," while still being slightly profitable if used properly.

Before applying this numerically, define:

- What "efficiency" measures.
- How Robux spending and earned-coin spending are being compared.
- What counts as "slightly profitable."
- Which purchase categories this profitability requirement applies to.

Do not convert the 80% target into a specific price multiplier without defining the comparison first.

## 7. Points to Resolve Before Finalizing the Design

Keep these questions visible in the outline. Where useful, recommend an answer and label it as a proposal.

1. **Currency terminology:** My notes use "cash," "coins," and "money." Are these names for one earned currency, or should cash and coins be separate currencies?
2. **Original mathematical modifiers:** Should doubling, addition, subtraction, and division remain in the army runner? If so, what do they affect?
3. **The 10-run benchmark:** What exactly are the "three area upgrades"? Should the earnings from 10 simulated runs fund all three together or provide the benchmark for each? Which player upgrade level is used in the simulation?
4. **Employee cash pricing:** Does "20 runs" mean average earnings, best-case earnings, or another benchmark? How should this price scale as players reach new areas?
5. **Four area unlocks:** At the second stage, does combat replace one revenue section, resulting in three base-revenue sections plus combat? Is combat itself counted as the fourth cash-generating section?
6. **The first combat barrier:** If combat is required to unlock the next area, but repeatable combat becomes available at stage two, how does the first barrier work?
7. **Runner payouts:** How are upgrades valued? Do troops contribute to the payout, and how does coin-carrying capacity affect the final amount?
8. **Combat progression:** What upgrades are collected during waves, and which improvements persist between attempts? Keep this consistent with obtaining armour and weapons from chests at the end of each game.
9. **Boost behaviour:** Are randomly appearing multipliers and time skips free rewards or purchase opportunities? What does 2× affect, how long does it last, and how do time skips interact with restocking and employees?
10. **The 80% efficiency target:** What measurable comparison makes coin purchases 80% less efficient than Robux while still allowing profitable use?

## Requested Output

Create a structured game-design outline covering:

- The connected gameplay loop.
- Army-runner mechanics and rewards.
- Permanent runner upgrades.
- Base areas, revenue sections, restocking, and employees.
- Combat barriers and the repeatable combat mode.
- Equipment, chests, rarity, and upgrades.
- Increasing difficulty, income, and unlock costs.
- The algorithm and assumptions for the 10-run benchmark.
- Robux purchases, earned-coin alternatives, random boosts, and price scaling.
- The visual direction based on the attached image.

Preserve every stated requirement. Separate confirmed requirements from recommended additions, and identify any conflicting rules that need a decision before the design can be finalized.
