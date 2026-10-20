# Army Run Tycoon (Roblox)

A complete Roblox game built from your design brief (`docs/BRIEF.md`):

1. **Army Run** – your avatar runs down a track, steering left/right through
   `+ × − ÷` troop gates, weapon gates, cash, enemy squads and an enemy base.
2. **Base tycoon** – 8 areas × 4 unlocks, shops that earn money, manual restocking,
   employees (200 Robux **or** 20 runs of cash), gates with padlocks.
3. **Battle mode** (Archero 2 style) – dodge bullets, auto-shoot when standing still,
   pick 1-of-3 abilities between waves, earn cash, open chests for armour and weapons.

The full design outline, with every rule and number, is in **`DESIGN.md`**.

---

## 1. Open it in Roblox Studio (Mac)

1. Download **`build/ArmyRunTycoon.rbxlx`** from this folder (on GitHub, open the file
   and click **Download raw file**).
2. Open **Roblox Studio** → **File → Open from File…** → pick `ArmyRunTycoon.rbxlx`.
3. Press **Play** (▶ or `F5`). The map, shops, runner tracks and arenas are built
   automatically when the game starts.
4. While testing in Studio you get a grey **DEV** button on the left (Studio only, it
   never appears in the real game): free test cash, beat the current barrier, open
   chests, try boosts, reset your progress.

> **About your ChatGPT version:** this is a complete replacement, not a patch.
> Your old version is not touched. Keep it until you are happy with this one.

## 2. Make it your game (publish)

1. In Studio: **File → Publish to Roblox As…**
   * Choose **Create new experience** to keep your ChatGPT version separate (safest), or
   * choose your existing experience to replace it. Roblox keeps old versions: Creator
     Hub → your experience → the place → **Version History** lets you restore one.
2. **Turn on saving:** **Home → Game Settings → Security → Enable Studio Access to API
   Services** → Save. Without this, Studio shows a warning "DataStores unavailable" and
   progress is not saved between test sessions. Published servers always save.
3. **Server size:** **Game Settings → Places → (your place) → Max Players = 12**.
   The game has 12 personal runner tracks and arenas per server.

## 3. Turn on Robux purchases (optional)

Everything can also be bought with in-game cash, so the game works without this.
Until a product is set up its Robux button is greyed out ("Robux not set up").

1. Go to **create.roblox.com → Creations → your experience → Monetization →
   Developer Products → Create a Developer Product**.
2. Create one product for each line below with **exactly this price**:

   | Config key | What it is | Price (Robux) |
   |---|---|---|
   | `Employee` | Hire one employee | 200 |
   | `x2_15` / `x2_30` / `x2_60` | 2x cash for 15 / 30 / 60 min | 29 / 49 / 89 |
   | `skip_1` / `skip_2` / `skip_4` | Time skip 1h / 2h / 4h | 25 / 45 / 79 |
   | `skip_8` / `skip_12` / `skip_24` | Time skip 8h / 12h / 24h | 149 / 199 / 349 |
   | `vehicle_bike` … `vehicle_hovertank` | Vehicles (see Config.Vehicles) | 49, 99, 149, 199, 299, 399, 549, 799 |

3. Copy each product's **ID** (a long number).
4. In Studio's **Explorer**: **ReplicatedStorage → Shared → Config**, double-click it,
   find `Config.ProductIds` and paste each ID next to its key (replace the `0`).
5. Publish again. In Studio, purchases show a **test** dialog and never charge real Robux.

## 4. Where things are (Explorer)

| Explorer location | What is inside |
|---|---|
| `ReplicatedStorage/Shared/Config` | colours, areas, prices in Robux, boosts, vehicles, rarities, product IDs |
| `ReplicatedStorage/Shared/EconomyDefs` | balancing numbers (incomes, price splits, scaling) |
| `ReplicatedStorage/Shared/Economy` | price formulas and the 10-run benchmark |
| `ReplicatedStorage/Shared/Runner/*` | runner course generator and simulator |
| `ReplicatedStorage/Shared/Combat/*` | enemies, waves, abilities, equipment |
| `ServerScriptService/Server/Services/*` | server game logic (saving, base, runner, combat, shop…) |
| `StarterPlayer/StarterPlayerScripts/Client/*` | screens, camera, controls, visuals |

Every script starts with a comment that explains what it does. Values marked
`[PROPOSAL]` are design assumptions you can change (see `DESIGN.md`).

## 5. Controls

| | Computer | Phone / tablet |
|---|---|---|
| Base | WASD / arrows, walk into prompts and hold **E** | joystick, tap prompts |
| Army Run | **A / D** or **← / →** | drag left / right |
| Battle | WASD to dodge; **stand still to shoot** | joystick; let go to shoot |

---

## For developers (optional)

The source is a [Rojo](https://rojo.space) project; the `.rbxlx` file is built from `src/`.

```bash
bash scripts/check.sh          # type-check (luau-lsp + Roblox API), Lune tests, build
lune run scripts/simulate      # economy simulation → docs/ECONOMY_TABLES.md
lune run scripts/test-saving   # save-system simulation (locks, slow DataStore, races)
rojo serve                     # live-sync into Studio with the Rojo plugin
```

* `docs/ARCHITECTURE.md` – code structure and the contracts between systems.
* `docs/ECONOMY_TABLES.md` – generated prices, benchmarks and pacing.
* `docs/reference/` – the two reference images the colours were measured from.
