# Assets & Credits

This file covers everything under `assets/` — none of it ships under the
project's own MIT license (see `LICENSE`), which covers the source code
only. Each category below has its own terms.

## Fonts (`assets/fonts/`)

Cinzel, Cinzel Decorative, and EB Garamond, all distributed under the
**SIL Open Font License (OFL)**. The full license text for each family
ships alongside the font files (`OFL-Cinzel.txt`, `OFL-EBGaramond.txt`) —
keep those files with the fonts if you redistribute them.

## Icons (`assets/icons/`)

Sourced from **Twemoji**, licensed **CC-BY 4.0**. The full license text
and attribution ship in `assets/CREDITS_TWEMOJI_LICENSE.txt`. Per the
CC-BY terms, that attribution file must stay with the project if the
icons are redistributed.

## Illustrated artwork (`assets/art/`, `assets/cards/`, `assets/monsters/`,
`assets/heroes/`, `assets/backgrounds/`, `assets/events/`)

The original illustrations (card portraits, enemy/boss art, character portraits,
backdrops, etc.) were **AI-generated using Google Gemini**, from the
prompts recorded in `gemini_art_prompts.md`, specifically for this
project.

A few things worth being upfront about, since AI-generated images don't
have the same well-established licensing history as fonts/icons do:

- This is a personal/educational project (a class project shown to a
  professor and friends), not a commercial release.
- Generated-image terms of service are set by the provider (Google, in
  this case) and can change over time. If you plan to use this artwork
  beyond a student/portfolio context — redistributing it commercially,
  for instance — review Google's current Gemini/Imagen generated-content
  terms yourself before doing so. This isn't something that can be
  settled by a README note.
- If you'd rather not rely on AI-generated art at all, every one of these
  files is optional at runtime: `get_art()` / `get_icon()` / the
  `AssetManager` loaders all fall back to procedurally-drawn shapes when
  a given file is missing, so the game still runs (and still looks
  reasonably intentional) with any subset of `assets/art/` deleted.

## v1.2 artwork

The six new enemy portraits, five Wizard card illustrations, eight Tutoring Session icons, three Rift icons, Rift Compass and replacement relic icons were generated with OpenAI image generation for this game. Inventory artwork uses a transparent 256 × 256 canvas. The new enemy portraits are 800 pixels tall. The v1.2 dungeon backdrop and four alternate monster illustrations were generated for this release. The six flat hero pose files are extracted from the existing character sheets.

## Music (`assets/audio/music/`)

“Power Chord Proof” was composed and synthesized for this game. The loop uses synthesized instruments and percussion throughout. The recording is bundled as `power_chord_proof.wav`.
