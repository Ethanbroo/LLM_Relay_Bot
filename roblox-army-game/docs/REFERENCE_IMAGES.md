# Reference images

`reference/1_base_tycoon.png` and `reference/2_army_runner.png` were supplied by the
owner to set the **colouring, saturation and gameplay feel**. Open them (they are PNGs)
before building visuals. Measured colours are in `src/shared/Config.luau` → `Config.Palette`.

## Image 1 — base / tycoon (top-down, slightly angled camera)
* Sky-blue floor `(106,178,252)` everywhere, a lighter central walkway `(146,188,252)` with
  thin white edge lines `(227,237,253)`; rounded-rectangle **plots** with a light outline band
  `(202,226,252)` and floor-coloured inside.
* **Unlock pad**: white rounded rectangle `(244,250,250)` with a thick green border `(84,212,84)`,
  gold price text with dark outline "$1562.50" and a gold coin icon on its right.
* Perimeter **walls** are chunky Lego bricks with round studs on top: green hedges
  `(88,192,64)`, yellow `(234,182,26)` and red `(226,36,42)` wall segments. A green **grass**
  patch `(154,218,98)` with trees (chunky low-poly green blobs on brown trunks), wooden benches,
  street lamps and a red phone box.
* **Gates** to the next areas: black wrought-iron fence gates with vertical bars and spikes,
  a big gold **padlock** `(254,210,62)` hanging on each.
* Purple **offer bubble** above an object: "+$357.1" (gold coin) over a green "WATCH AD"
  strip — we reuse this look for random 2x / time-skip offers.
* **"Hire me"** tile: blue-grey rounded square `(98,122,170)` with a white border, a running
  businessman (suit, red tie, briefcase) on it, big white "Hire me" text below it, and a small
  video-clapper icon at its corner.
* NPC customers walk the paths in bright single-colour outfits (pink, purple, yellow).
* Parked cartoon cars (orange SUV), a giant pink doughnut shop sign, water `(10,168,250)`
  around the whole island.
* HUD: top-right black pill `(24,24,36)` with a large gold coin and white "$0.00".

## Image 2 — army runner reference (third-person, camera high behind the runner)
* Bright cyan sky `(52,194,250)` blending into a cyan sea `(16,168,250)` with white caustic
  ripples on both sides of a long, **white track** `(236,246,255)` with faint light-blue
  cross stripes `(212,234,252)`.
* **Gates**: pairs of translucent panels spanning the track between dark posts
  `(46,50,66)`; negative ones red (`-$2.50`, `÷2`, `-$20.00`), positive ones blue (`+$2.50`).
  Large white bold numbers with dark outlines.
* The runner is a **squad** (stack of coins in the reference → our troops) with a
  **lime-green money tag** `(108,240,84)` above it showing "$122.50".
* Top-centre black **"Level 39"** banner. Top-right black cash pill "$215.00".
* Left: two **cards** with an orange header `(252,164,12)` and a blue body `(12,142,252)`,
  thick dark outline: "Coin Magnet" (magnet icon) and "Starting coins / LEVEL 49 ↑ / FREE!"
  — each with a small video-clapper badge. Right: a "Skins" card with a pink header
  `(252,124,250)`. Centre-bottom: tutorial hand with a ←→ double arrow ("drag to move").
