# Gemini image prompts — Math Meme Spire

Paste each prompt into Gemini on its own. Save the results with the exact
filenames noted below, into `assets/art/`, and the game will automatically
pick them up next run (no code changes needed — it already checks for these
files and falls back to the procedural version if they're missing).

---

## 1. Shop backdrop — `shop_backdrop.png`

> A wide, atmospheric fantasy marketplace backdrop for a math-themed roguelike
> deckbuilder card game, viewed as if standing at a merchant's stall. Warm
> dusk sky fading from deep plum-purple at the top to warm amber-orange near
> the horizon, a few faint stars visible. In the distance, a row of triangular
> market tent canopies in warm browns, maroons, and gold, silhouetted against
> the sky. Strings of small glowing lanterns hang across the upper part of the
> scene, casting a warm golden glow. Faint drifting mathematical symbols (π,
> ∑, dice pips, +, ×) glow softly in the background like magical motes, subtle
> and not distracting. Rich, painterly digital-art style with dramatic warm
> lighting, slightly stylized/cartoonish (not photorealistic) to match a
> whimsical math-wizard fantasy game. Landscape orientation, 16:9, no text, no
> characters, no UI elements — this is a background layer only, framed so the
> bottom two-thirds stays relatively simple/uncluttered since UI panels and
> item cards will be placed on top of it.

---

## 2. Power charge-up aura, Math Warrior — `power_aura_warrior.png`

> A dynamic Dragon-Ball-Z-style energy charge-up aura effect, isolated on a
> fully transparent background, for a fantasy math-themed video game
> character (a stoic sword-and-shield "Math Warrior"). Bright golden-amber
> energy radiating outward from a central point, with sharp jagged flame-like
> spikes of light shooting upward and outward, concentric glowing rings
> pulsing outward, and small bright sparks/embers scattered around the edges.
> The energy should feel powerful, solid, and disciplined, with faint
> glowing equation symbols (+, ×, =) woven into the light streaks. Vertical
> composition, character-sized (tall rather than wide), transparent PNG, no
> character/body drawn — just the pure energy effect so it can be overlaid on
> top of the character sprite in-game. Vibrant, saturated, glowing colors on
> pure transparency, digital game-art style.

---

## 3. Power charge-up aura, Probability Wizard — `power_aura_wizard.png`

> A dynamic Dragon-Ball-Z-style energy charge-up aura effect, isolated on a
> fully transparent background, for a fantasy math-themed video game
> character (a mystical "Probability Wizard"). Vivid violet-and-magenta
> swirling energy radiating outward from a central point, with sharp jagged
> spikes of light shooting upward and outward, concentric glowing rings
> pulsing outward, and small bright sparkling motes and tiny glowing dice
> pips scattered around the edges. The energy should feel arcane and
> probabilistic — wisps curling like chaotic swirling probability clouds,
> with faint glowing dice/percentage symbols woven into the light streaks.
> Vertical composition, character-sized (tall rather than wide), transparent
> PNG, no character/body drawn — just the pure energy effect so it can be
> overlaid on top of the character sprite in-game. Vibrant, saturated,
> glowing colors on pure transparency, digital game-art style.

---

---

## 4. Hero sprite states (NEW — "Chad Math" rebrand)

Each hero now has 3 distinct visual states instead of one static portrait.
Drop matching PNGs into `assets/art/` under these exact filenames and the
game picks them up automatically — no code changes needed. Any state you
skip falls back to the existing single portrait; if that's missing too, it
falls back further to the procedural silhouette. So you can add these one
at a time in any order.

| State | Math Warrior filename | Probability Wizard filename |
|---|---|---|
| 1. Character Select (stoic pose / arms folded) | `hero_warrior_select.png` | `hero_wizard_select.png` |
| 2. Dungeon Combat idle/ready (dual swords / flying V-guitar) | `hero_warrior_ready.png` | `hero_wizard_ready.png` |
| 3. Power-Up / Card Surge (Integration Flame / Probability Vortex) | `hero_warrior_powerup.png` | `hero_wizard_powerup.png` |

State 3 is shown automatically for 1.2 seconds whenever you play a Power
card, play Ultimate Strike/Blast, or roll max value on a d8+ die, in sync
with the existing charge-up glow ring (which already uses
`power_aura_warrior.png` / `power_aura_wizard.png` — the black-background
aura art from earlier is untouched and still layers on top of whichever
hero state is showing).

The reference art you shared (character-select "arms folded"/"stoic hero"
poses, "guitar shred"/"dual-wielding" combat poses, and the golden/purple
Integration Flame & Probability Vortex power-up poses) maps directly onto
these three states per hero — crop/export each pose to its own transparent
PNG under the filenames above and they'll slot straight in.

## 5. New monster portraits

| Monster | Filename |
|---|---|
| Decimal Demon | `enemy_decimal_demon.png` |
| Fractal Fiend | `enemy_fractal_fiend.png` |
| Vector Viper | `enemy_vector_viper.png` |
| Radical Wraith (Elite) | `elite_radical_wraith.png` |

Same rule: missing file → procedural silhouette fallback, no crash either way.

---

### Notes

- All three already have working procedural fallbacks in-game, so nothing
  breaks if you skip these or don't like the results — the shop backdrop
  currently shows a hand-drawn lantern/market-tent scene, and the power aura
  shows pulsing rings + rising ki-sparks in each character's matching color
  (gold for Math Warrior, purple for Probability Wizard).
- For the aura images specifically: Gemini's default aspect ratio may come
  out closer to square/landscape. If so, just ask it to "make it taller and
  narrower, like a vertical burst" and it'll usually correct on a follow-up.
- Drop the finished PNGs straight into `assets/art/` next to the other
  `combat_effect_*` / `combat_backdrop_*` files with exactly the filenames
  above — that's the only step needed to activate them.
