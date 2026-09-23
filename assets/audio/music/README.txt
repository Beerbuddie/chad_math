Drop your MP3s in this folder under these exact names and they'll start
playing automatically at the matching screen -- no code changes needed.
Missing files are silent, not an error, so you can add these one at a
time in any order.

  title_theme.mp3      Title screen, intro, and character select
  spire_ambient.mp3     The dungeon map + all non-combat rooms (events,
                         card rewards, relic choices, treasure)
  combat_theme.mp3      Normal fights
  boss_theme.mp3         Boss fights specifically (overrides combat_theme.mp3)
  rest_theme.mp3         The Grindnight Camp rest site
  shop_theme.mp3          The shop
  victory_theme.mp3       Victory screen
  gameover_theme.mp3      Game over screen

Every track loops automatically and cross-fades (~0.9s) when the game
switches screens. Format: standard MP3, any bitrate/sample rate pygame's
mixer can decode (most exported MP3s work fine).

assets/audio/sfx/ is reserved for short one-shot sound effects (hits,
dice rolls, card plays) -- that loader isn't wired up yet, this pass only
covers looping background music.
