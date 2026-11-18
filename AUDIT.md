# Gameplay and artwork audit

Reviewed main at `9e52fb6`. The checkout contains 47 card records, 12 relics and two playable characters. Neither published branch contains Tutoring Sessions or Rifts of Power, including Entropy, Variance and Convergence Rift. This review follows the owner's choice to audit current main and flag those systems as missing.

## Rendering

- Card art covered the cost numeral and the type icon. Both now paint over the art. The badge font fits the small circle. A pixel regression test paints contrasting art beneath the same numeral at 78×90, 150×172 and 190×260.
- Long titles could escape the frame or switch to the internal card name on a small face. Card, shop, reward, deck and removal views now fit the title or show the character's display name where appropriate. Relic choices reserve space for the icon. The combat tooltip uses the character's name and rules text.
- The footer could overrun narrow cards or draw two lines in too little height. Compact cards use a shorter footer and can show just EV. The description measures rendered line height. Shop and reward hover reveal full rules for truncated faces.
- Gambler's Flurry now uses its commissioned dice-cup art on the card itself. The missing-icon letter fallback also fits small illustrated panels.

## Cards, relics and combat

- Warrior Strike, Standard Deviation, Factorial Fury, Integral Strike and Weak Hypothesis used flat attack values in both ranks. Each now rolls d3 with a bonus that preserves the previous unmodified mean per hit. Wizard Strike retains its separate dice per missile. Both burst-AI attacks now roll around their original means. All enemy attack dice use the game's roll API with enemy rolls isolated from player relics, powers and roll history. Missing enemy intent now goes through the normal AI.
- Standard Deviation+ actually delivered 24 mean damage while its preview said 28. Integral Strike's preview counted energy before paying its cost. Progressive Overload omitted its accumulated bonus. Ultimate Blast rounds each outcome up, giving a mean of 16.0. The dragon rounds each scaled outcome down. The Inspector and audit now use the rounded distributions.
- Reckless Roll+ previously doubled the backfire chance with no upside. It keeps the risk on faces 1 and 2 and triples successful rolls. Its unmodified gross enemy damage rises from 10.8 at base to 15.6 upgraded. Expected recoil rises from 0.4 to 0.8.
- Glass Guard blocked its own one-HP payment. The payment now bypasses block and honors lethal saves. Misread Equation rolls its HP loss and uses one effect for drawing and resolution. It remains an intentionally unreachable legacy curse. Weak now affects the side making the attack. Enemy Weak previously reduced player recoil. Ultimate Strike now sends its block through Golden Ratio.
- Block leaked into the next battle. Combat-only powers and statuses could also modify event dice after victory. Combat entry and victory now clear the relevant state. An unused relic combat-start helper was removed.
- Draw cards could reshuffle and draw themselves before their effect resolved. Played cards enter discard after resolution. End Turn now enforces the Flurry decision. Its description distinguishes an opening bust from a later bust, and the next-roll calculation accounts for modified faces.
- Pencil of Fortune could miss its upgrade guarantee on a normal reward containing only non-upgradeable cards. It now recognizes an existing upgrade or supplies one. Boss rewards have their own ultimate-only pool. Descriptions clarify Probability Crown's first-hit trigger, Ritual Prism's cap, Pi's rounding and status durations.
- Checked all declared upgrade edges for reciprocal base links, rarity and cost. Normal rewards, rest upgrades, the normal reward upgrade relic, starter decks and boss rewards provide the live acquisition paths. Several standalone cards have no upgrade defined. The curse has no active acquisition path. The regression suite exercises both characters' normal play-card entry point against every registered damage EV using uniform die faces.

## Balance and verification

Both automated drivers stopped at the first boss quiz because it uses the victory screen. They now answer inter-act quizzes and count a victory only in Act 3. The corrected three-act benchmark required changing `BOSS_DAMAGE_NERF` from 0.95 to 0.48. The heuristic still uses the existing simple combat, rest and purchase decisions. Results assume correct quiz answers.

| Check | Result |
|---|---|
| Unit suite with real pygame-ce 2.5.8 on Python 3.14 | 257 passed |
| Independent math audit | 60/60 PASS, 500,000 trials per row |
| Random full-run soak | 300 completed, 300 gameovers, zero exceptions |
| Heuristic, seeds 0–199 | 104 final victories, 96 losses, 52.0% |
| Heuristic, seeds 0–999 | 504 final victories, 50.4% |
| Render sweep | Both characters × 47 cards × four sizes, 376 renders |

The seeded balance runs used `PYTHONHASHSEED=0`. The 1,000-seed set includes the 200 default seeds.

## Artwork and remaining issues

All 148 PNGs decoded with pygame. All four bundled font files loaded. A contact-sheet review covered the PNGs, and a separate render review covered compact, hand and reward faces. The weightlifting and guitar card commissions match their character presentations. The hero sheets have opaque scenic backgrounds. Several relic icons, visibly including Abacus Charm, Calculator Shield and Infinite Series, contain opaque gray checkerboard tiles. Replacement transparent source art would avoid damaging the item silhouettes.

The optional combat background `backgrounds/spire_dungeon_bg.png` is missing. The act backdrops provide fallback art. Six optional flat hero poses and the four flat alternatives for Decimal Demon, Fractal Fiend, Vector Viper and Radical Wraith are missing. Their primary sheets or monster PNGs are present. The eight mapped music tracks are placeholders and the music directory contains its README only.

The following PNGs have no direct runtime asset-name match. The list includes legacy icons, alternate commissions and the title screenshot. These files were retained for review.

- `assets/art/` — `card_bellcurve.png`, `card_bellcurve_interval.png`, `card_book.png`, `card_d20.png`, `card_exclaim.png`, `card_fist.png`, `card_footstep.png`, `card_hourglass.png`, `card_lightning.png`, `card_shield.png`, `card_warrior_book.png`, `card_wave.png`, `card_xmark.png`, `enemy_husk_of_probability.png`, `node_event_alt.png`, `relic_weighted_lens.png`.
- `assets/cards/` — `gamblers_flurry-1.png`.
- `assets/events/` — `casino_event-1.png`.
- `assets/icons/` — `deco_barchart.png`, `deco_dart.png`, `enemy_zombie.png`, `relic_abacus.png`, `relic_candle.png`, `relic_circle.png`, `relic_infinity.png`, `relic_lens.png`, `relic_lifering.png`, `relic_pencil.png`, `relic_ruler.png`, `rest_tent.png`, `shop_moneybag.png`, `treasure_gem.png`, `wizard_accent.png`.
- `assets/` — `screenshot_title.png`.

### Design questions and Slay the Spire 1 comparison

- Damage footers describe base output before mitigation and most roll modifiers. Flurry has a separate stopping-decision EV. A conditional preview that includes relics, block, odd resistance and individual hits would need a wider UI and additional distribution tests.
- Hands beyond the minimum card scale can extend past the screen, and the relic strip only shows as many icons as fit. Paging or an expanded inspection view would preserve access in extreme runs.
- Cards and relics already support reward drafting and synergies. Adding a reward rarity curve and showing the next removal price in advance would make card acquisition and shop decisions easier to plan. The current shop uses a fixed 40-gold removal fee.
- The game has visible enemy intents and a branching map. Giving regular enemies short move histories and giving the player a visible replay seed would make probability experiments repeatable. The original Slay the Spire's shared-seed Daily Climb is a relevant precedent. [Mega Crit's Daily Climb notes](https://store.steampowered.com/news/posts/?appids=646570&enddate=1522974602&feed=steam_community_announcements)
- A future Tutoring Sessions implementation could use the original game's limited consumable slots. The Wizard's missing Rifts of Power would need explicit channel, passive and evoke behavior. Both are separately scoped design work for the existing two-character roster.
