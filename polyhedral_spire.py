"""Math Meme Spire — an original math/probability-themed roguelike deckbuilder.

Architecture notes
-------------------
* Importing this module performs NO side effects (no window, no game loop).
  All pygame setup happens lazily in ``init_display()``, and the game loop
  only runs inside ``main()`` / ``if __name__ == "__main__"``.  This makes
  the module safe to import for automated tests (see
  ``test_math_meme_spire.py``), including constructing a ``Game`` instance
  and exercising its logic without ever opening a window.
* Relic and Power effects are centralized in ``RELIC_LIBRARY`` /
  ``POWER_LIBRARY`` as data + small hook callables, dispatched through
  ``trigger_hook`` / ``apply_roll_modifiers`` / ``apply_block_modifiers``.
  Nothing grants a relic without also wiring its effect through these hooks.
* Map generation lives in ``generate_map`` and is fully data-driven &
  connectivity-checked (see ``MapGenerationError`` / ``validate_map``).
"""

import math
import os
import random
import re
import sys

import pygame

# ---------------------------------------------------------------------------
# Asset paths
#
# Ships as a real, packaged "assets/" folder next to this file (fonts +
# icons — see assets/CREDITS_TWEMOJI_LICENSE.txt and assets/fonts/OFL-*.txt
# for attribution/license text). ``resource_path`` also finds it inside a
# PyInstaller --onefile bundle via ``sys._MEIPASS``. When building the exe,
# include it with:
#     --add-data "assets;assets"
# ---------------------------------------------------------------------------


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def writable_dir():
    """Directory to write user-visible files (right now: just crash_log.txt)
    into. This is deliberately NOT resource_path()/_MEIPASS: that's a
    temporary extraction folder for a --onefile .exe build that gets deleted
    after the program exits, so anything written there would vanish before
    anyone could read it. This instead resolves to the folder the actual
    .exe (frozen) or .py file (dev) lives in, which persists."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ASSET_DIR = resource_path("assets")
FONT_DIR = os.path.join(ASSET_DIR, "fonts")
ICON_DIR = os.path.join(ASSET_DIR, "icons")
CRASH_LOG_PATH = os.path.join(writable_dir(), "crash_log.txt")


def log_crash(exc):
    """Append a timestamped traceback to crash_log.txt next to the .exe/.py.
    There is no other persistent log in this game (the in-game "Run Log" /
    combat log is transient, never written to disk) — this is the one file
    worth checking after a friend reports "it crashed" or "it did something
    weird": it survives after the window closes, unlike whatever scrolled by
    in a console window (which doesn't even exist in this windowed build)."""
    import datetime
    import traceback
    try:
        with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"{datetime.datetime.now().isoformat()}  Math Meme Spire crash\n")
            f.write(f"Python {sys.version}\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            f.write("\n")
    except Exception:
        pass  # logging the crash must never itself crash the crash handler
ART_DIR = os.path.join(ASSET_DIR, "art")

# -- Music framework --------------------------------------------------------
# Skeleton for the MP3 music the user is going to add. Drop files into
# assets/audio/music/ under the exact names in MUSIC_FILES below and they
# start playing automatically at the matching screen -- nothing else to
# wire up. Missing files (the expected state until those MP3s are added)
# are silent, not an error: MusicManager tracks the requested key either
# way so it doesn't reattempt a load every frame, and a completely missing
# audio device (no `pygame.mixer`, or init failing) degrades to a permanent
# no-op the same way. assets/audio/sfx/ is reserved for short one-shot
# sound effects (hits, dice, card plays) -- no loader for those yet, this
# pass only wires up looping background music.
AUDIO_DIR = os.path.join(ASSET_DIR, "audio")
MUSIC_DIR = os.path.join(AUDIO_DIR, "music")
SFX_DIR = os.path.join(AUDIO_DIR, "sfx")

MUSIC_FILES = {
    "title": "title_theme.mp3",
    "explore": "spire_ambient.mp3",
    "combat": "combat_theme.mp3",
    "boss": "boss_theme.mp3",
    "rest_site": "rest_theme.mp3",
    "shop": "shop_theme.mp3",
    "victory": "victory_theme.mp3",
    "gameover": "gameover_theme.mp3",
}

# Every game.state maps to one of the keys above (or None for a handful of
# states -- like the deck viewer overlay -- that shouldn't interrupt
# whatever track is already playing underneath them).
MUSIC_STATE_MAP = {
    "title": "title",
    "intro": "title",
    "character_select": "title",
    "map": "explore",
    "event": "explore",
    "event_result": "explore",
    "card_reward": "explore",
    "relic_choice": "explore",
    "treasure_result": "explore",
    "combat": "combat",  # overridden to "boss" for boss fights -- see music_key_for_state()
    "rest_choice": "rest_site",
    "rest_upgrade_pick": "rest_site",
    "shop": "shop",
    "shop_remove_pick": "shop",
    "victory": "victory",
    "gameover": "gameover",
}


def music_key_for_state(game):
    if game.state == "combat" and getattr(game, "combat_boss", False):
        return "boss"
    return MUSIC_STATE_MAP.get(game.state)


class MusicManager:
    """Thin wrapper around pygame.mixer.music. Initialization is deferred
    to the first real call (not module import) so importing this file --
    including in headless tests, where the stub pygame has no `mixer` at
    all -- never touches an audio device. Every operation is wrapped in
    try/except: a missing MP3, a missing audio device, or a completely
    absent mixer module all degrade to silence, never a crash."""

    def __init__(self):
        self._current_key = None
        self._init_attempted = False
        self._enabled = False

    def _ensure_init(self):
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            if hasattr(pygame, "mixer"):
                pygame.mixer.init()
                self._enabled = True
        except Exception:
            self._enabled = False

    def play_for_state(self, game):
        key = music_key_for_state(game)
        if key is None or key == self._current_key:
            return
        self._current_key = key
        self._ensure_init()
        if not self._enabled:
            return
        filename = MUSIC_FILES.get(key)
        path = os.path.join(MUSIC_DIR, filename) if filename else None
        if not path or not os.path.exists(path):
            self._stop()
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(loops=-1, fade_ms=900)
        except Exception:
            pass

    def _stop(self, fade_ms=600):
        if not self._enabled:
            return
        try:
            pygame.mixer.music.fadeout(fade_ms)
        except Exception:
            pass

    def set_volume(self, volume):
        if not self._enabled:
            return
        try:
            pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
        except Exception:
            pass


MUSIC_MANAGER = MusicManager()

FONT_FILES = {
    "title": os.path.join(FONT_DIR, "CinzelDecorative-Black.ttf"),
    "header": os.path.join(FONT_DIR, "CinzelDecorative-Bold.ttf"),
    "label": os.path.join(FONT_DIR, "Cinzel-Variable.ttf"),
    "body": os.path.join(FONT_DIR, "EBGaramond-Variable.ttf"),
}

# Semantic icon name -> file (assets/icons/<name>.png), sourced from Twemoji
# (CC-BY 4.0 — credited on the title screen and in CREDITS_TWEMOJI_LICENSE.txt).
ICON_NODE = {
    "combat": "node_combat", "elite": "elite_star", "event": "node_event",
    "rest": "node_rest", "shop": "node_shop", "treasure": "node_treasure", "boss": "boss_skull",
}
ICON_CARD_TYPE = {"attack": "attack_fist", "skill": "skill_shield", "power": "power_sparkles"}
CARD_ICON = {
    # Real Gemini-generated icons (assets/art/card_*.png) where we have a good
    # thematic match; get_icon() checks assets/art/ before assets/icons/, so
    # these resolve to the hand-picked art automatically.
    "Strike": "card_warrior_sword", "Defend": "card_warrior_armor", "Arcane Dart": "card_wizard_wave",
    "Reckless Roll": "card_wizard_dice", "Gambler's Flurry": "card_wizard_dice", "Progressive Overload": "card_warrior_fist", "Spotter's Axiom": "card_warrior_armor", "Probability Shield": "card_warrior_calculator", "Chaos Bloom": "card_wizard_orbit",
    "Weighted Step": "card_warrior_numbers", "Null Burst": "card_wizard_constellation", "Glass Guard": "card_warrior_armor",
    "Dice Slash": "card_wizard_dice", "Dice Burst": "card_wizard_wave", "Standard Deviation": "card_wizard_bellcurve",
    "Confidence Interval": "card_wizard_bellcurve", "Bayesian Update": "card_wizard_book", "Integral Strike": "card_warrior_sword",
    "Factorial Fury": "card_warrior_fist", "Central Limit Theorem": "card_wizard_orbit",
    "Law of Large Numbers": "card_numbers",
    "Ultimate Strike": "card_warrior_sword",
    "Ultimate Blast": "card_wizard_orbit",
}

# Real illustrated card portraits (assets/cards/<file>.png), checked by
# draw_card_art() BEFORE the procedural icon-panel fallback above. Only
# cards with a hand-picked commission go here -- everything else keeps
# using its CARD_ICON entry (or the bare type icon) exactly as before.
CARD_ART_FILES = {
    "Progressive Overload": "progressive_overload",
    "Spotter's Axiom": "spotters_axiom",
}

# Per-character art overrides, checked BEFORE CARD_ART_FILES. Needed
# because a handful of raw CARD_LIBRARY names are shared by both starting
# decks with different thematic re-skins (see CARD_PRESENTATION) -- e.g.
# "Strike" is Math Warrior's "Equation Breaker" AND Probability Wizard's
# "Power Chord Barrage". Keying by base name alone would put the same
# portrait on both characters' version of the card, so a commission that
# only matches ONE character's re-skin goes here instead, keyed by
# (character -> raw card name). A raw name with no entry for the active
# character falls back to CARD_ART_FILES exactly as before.
CHARACTER_CARD_ART_FILES = {
    "Math Warrior": {
        "Dice Slash": "one_rep_max",
    },
    "Probability Wizard": {
        "Strike": "power_chord_barrage",
        "Reckless Roll": "whammy_bar_gambit",
    },
}

# Per-character re-skins: same card, same numbers, different flavor. Math
# Warrior reads as weightlifting + violence; Probability Wizard reads as
# spellcasting + rock guitar. Every card in BOTH starting decks (see
# CHARACTER_OPTIONS) has an entry here, base card and "+" upgrade alike, so
# nothing reverts to its raw math-club name once upgraded. A card that's
# only ever drop-only/reward-only for the OTHER character (e.g. a Warrior
# who gets handed a Reckless Roll from a card reward) still falls back to
# its plain name via card_presentation()'s default -- only cards that
# actually start in a deck are re-skinned for that deck's character.
CARD_PRESENTATION = {
    "Math Warrior": {
        "Strike": ("Equation Breaker", "Deal 8 damage with brute-force algebra."),
        "Strike+": ("Equation Breaker+", "Deal 12 damage with brute-force algebra."),
        "Defend": ("Iron Guard", "Gain 8 block."),
        "Defend+": ("Iron Guard+", "Gain 12 block."),
        "Arcane Dart": ("Dumbbell Jab", "Roll 1d6 + 2 damage."),
        "Arcane Dart+": ("Dumbbell Jab+", "Roll 1d6 + 5 damage."),
        "Probability Shield": ("Iron Bulwark", "Roll 1d8 and gain that much block."),
        "Probability Shield+": ("Iron Bulwark+", "Roll 1d8 + 3 and gain that much block."),
        "Weighted Step": ("Loaded Lunge", "Gain 6 block and draw 1 card."),
        "Weighted Step+": ("Loaded Lunge+", "Gain 9 block and draw 2 cards."),
        "Chaos Bloom": ("Max-Out Slam", "Deal 12 + 1d6 damage."),
        "Chaos Bloom+": ("Max-Out Slam+", "Deal 18 + 1d6 damage."),
        "Dice Slash": ("One-Rep Max", "Roll 1d20. Deal that much damage."),
        "Dice Slash+": ("One-Rep Max+", "Roll 1d20. Deal that much damage + 4."),
        "Ultimate Strike": (
            "Ultimate Equation Breaker",
            "Roll 1d20 — deal that damage, gain that block. Brain dump so "
            "hard you forget it forever after one use.",
        ),
    },
    "Probability Wizard": {
        "Strike": ("Power Chord Barrage", "Strum 3d3; deal the total as three chords of damage."),
        "Strike+": ("Power Chord Barrage+", "Strum 4d3; deal the total as four chords of damage."),
        "Defend": ("Wall of Sound", "Gain 8 block behind a wall of amps."),
        "Defend+": ("Wall of Sound+", "Gain 12 block behind a wall of amps."),
        "Arcane Dart": ("Riff Bolt", "Roll 1d6 + 2 damage."),
        "Arcane Dart+": ("Riff Bolt+", "Roll 1d6 + 5 damage."),
        "Reckless Roll": ("Whammy Bar Gambit", "Roll 1d10. 1 = hit yourself for 4, else deal double the roll."),
        "Reckless Roll+": ("Whammy Bar Gambit+", "Roll 1d10. 1-2 = hit yourself for 4, else deal double the roll."),
        "Null Burst": ("Schrödinger's Riff", "Deal 15 or 7 damage (coin flip)."),
        "Null Burst+": ("Schrödinger's Riff+", "Deal 20 or 10 damage (coin flip)."),
        "Probability Shield": ("Amp Barrier", "Roll 1d8 and gain that much block."),
        "Probability Shield+": ("Amp Barrier+", "Roll 1d8 + 3 and gain that much block."),
        "Glass Guard": ("Overdriven Ward", "Gain 10 block and lose 1 HP."),
        "Glass Guard+": ("Overdriven Ward+", "Gain 15 block and lose 1 HP."),
        "Chaos Bloom": ("Feedback Loop", "Deal 12 + 1d6 damage."),
        "Chaos Bloom+": ("Feedback Loop+", "Deal 18 + 1d6 damage."),
        "Weighted Step": ("Encore Step", "Gain 6 block and draw 1 card."),
        "Weighted Step+": ("Encore Step+", "Gain 9 block and draw 2 cards."),
        "Dice Burst": ("Shred Solo", "Roll 1d12. Deal 8 + result damage."),
        "Dice Burst+": ("Shred Solo+", "Roll 1d12. Deal 13 + result damage."),
        "Ultimate Blast": (
            "Arcane Singularity",
            "Roll 1d20 and deal 1.5x that much damage. Brain dump so hard "
            "you forget it forever after one use.",
        ),
    },
}


def card_presentation(character, card_name):
    return CARD_PRESENTATION.get(character, {}).get(card_name, (card_name, CARD_LIBRARY[card_name]["desc"]))
ICON_RELIC = {
    "Pi's Defense": "relic_pis_defense", "Calculator Shield": "relic_calculator_shield",
    "Ritual Prism": "relic_ritual_prism", "Pencil of Fortune": "relic_pencil_of_fortune",
    "Proof by Contradiction": "relic_proof_by_contradiction", "Abacus Charm": "relic_abacus_charm",
    "Theorem of Calm": "relic_theorem_of_calm", "Dice Sigil": "relic_dice_sigil",
    "Probability Crown": "relic_probability_crown", "Golden Ratio": "relic_golden_ratio",
    "Infinite Series": "relic_infinite_series", "Null Set": "relic_null_set",
}
ICON_ENEMY_ACCENT = {
    "oracle": "enemy_brain", "brain": "enemy_brain", "leech": "enemy_brain",
    "wraith": "enemy_ghost", "loop": "enemy_ghost", "recursive": "enemy_ghost",
    "engine": "enemy_robot", "ogre": "enemy_robot",
    "zero": "dice", "dice": "dice", "anarchist": "dice",
}
ICON_BOSS_ACCENT = {
    "dragon": "boss_dragon", "colossus": "boss_colossus", "null hypothesis": "boss_cyclone",
    "singularity": "boss_cyclone", "fractal": "power_sparkles", "empress": "power_sparkles",
}

SHOP_ART = {
    "Math Warrior": ("shop_equation_merchant", "shop_scroll_sanctum"),
    "Probability Wizard": ("shop_calculus_armorer", "shop_bronze_foundry"),
}

REWARD_ART = "upgrade_reward_station"
ENTRANCE_ART = {
    "Sentry of Error": "elite_entrance_sentry_of_error",
    "The Abacus King": "boss_entrance_abacus_king",
}

_ICON_CACHE = {}


def get_icon(name, size):
    """Load+scale (and cache) a bundled icon PNG. Checks the hand-picked
    art set first (assets/art/<name>.png — real generated illustrations)
    then falls back to the Twemoji icon set (assets/icons/<name>.png).
    Returns None if missing so callers can fall back gracefully instead of
    crashing a frame."""
    key = (name, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = None
    for directory in (ART_DIR, ICON_DIR):
        path = os.path.join(directory, f"{name}.png")
        try:
            img = pygame.image.load(path)
            if hasattr(img, "convert_alpha"):
                img = img.convert_alpha()
            if hasattr(pygame.transform, "smoothscale"):
                img = pygame.transform.smoothscale(img, (size, size))
            else:
                img = pygame.transform.scale(img, (size, size))
            break
        except Exception:
            img = None
            continue
    _ICON_CACHE[key] = img
    return img


_ART_CACHE = {}


def get_art(name, max_height):
    """Load+scale (preserving aspect ratio, capped to max_height) and cache a
    non-square illustration from assets/art/<name>.png. Returns None if the
    file doesn't exist so callers fall back to procedural art."""
    key = (name, max_height)
    if key in _ART_CACHE:
        return _ART_CACHE[key]
    path = os.path.join(ART_DIR, f"{name}.png")
    img = None
    try:
        raw = pygame.image.load(path)
        if hasattr(raw, "convert_alpha"):
            raw = raw.convert_alpha()
        w, h = raw.get_size()
        if h > 0:
            scale = max_height / h
            new_size = (max(1, int(w * scale)), max(1, int(max_height)))
            scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
            img = scaler(raw, new_size)
    except Exception:
        img = None
    _ART_CACHE[key] = img
    return img


def draw_real_art_image(surface, img, anchor_x, anchor_y, scale):
    """Blits an already-loaded illustration Surface footprint-matched to
    where the procedural sprite of the same (x, y, scale) would sit. Split
    out from draw_real_art() so callers that need a custom lookup chain
    (e.g. AssetManager's per-hero-state fallback) can resolve the Surface
    themselves and still share this positioning math."""
    w, h = img.get_size()
    bottom_center_x = anchor_x + 74 * scale
    bottom_y = anchor_y + 150 * scale
    surface.blit(img, (bottom_center_x - w / 2, bottom_y - h))


def draw_real_art(surface, name, anchor_x, anchor_y, scale):
    """Draws a real illustration (if one exists for ``name``) footprint-matched
    to where the procedural sprite of the same (x, y, scale) would sit, so it
    drops into the same combat/character-select layout with no other changes.
    Returns True if art was drawn, False if the caller should fall back to
    the procedural shapes."""
    max_height = int(190 * scale)
    img = get_art(name, max_height)
    if img is None:
        return False
    draw_real_art_image(surface, img, anchor_x, anchor_y, scale)
    return True


# Real illustrations (assets/art/) mapped onto specific characters/enemies/
# bosses. Anything not listed here still gets the procedural sprite.
PORTRAIT_ART = {
    "Math Warrior": "math_warrior_portrait",
    "Probability Wizard": "wizard_portrait",
}
ENEMY_ART = {
    "Husk of Unsolvable Variables": "enemy_husk_of_unsolvable_variables",
    "Recursive Wraith": "enemy_recursive_wraith",
    "Glass Mathematician": "enemy_glass_mathematician",
    "Oracle of Odds": "enemy_oracle_of_odds",
    "Sentry of Error": "enemy_sentry_of_error",
    "Gambler's Shade": "enemy_gamblers_shade",
    "Axiom Warden": "enemy_axiom_warden",
    "Dice Hoarder": "enemy_dice_hoarder",
    "Probability Leech": "enemy_probability_leech",
    "Infinite Loop": "enemy_infinite_loop",
    "Rounding Error": "enemy_rounding_error",
    # Elites are combat_boss=False and drawn via draw_enemy_portrait, with
    # their display name prefixed "Elite <name>" (see generate_elite()).
    "Elite Cinder Engine": "elite_cinder_engine",
    "Elite Chalkboard Titan": "elite_chalkboard_titan",
    "Elite Derivative Ogre": "elite_derivative_ogre",
    "Elite Abacus Anarchist": "elite_abacus_anarchist",
}
BOSS_ART = {
    "The Derivative Dragon": "boss_number_dragon",
    "The Calculus Colossus": "boss_calculus_colossus",
    "The Abacus King": "boss_abacus_king",
    "The Null Hypothesis": "boss_null_hypothesis",
    "The Prime Minister of Pain": "boss_prime_minister_of_pain",
    "The Fractal Empress": "boss_fractal_empress",
    "The Singularity": "boss_singularity",
}
ENEMY_ART.update({
    "Decimal Demon": "enemy_decimal_demon",
    "Fractal Fiend": "enemy_fractal_fiend",
    "Vector Viper": "enemy_vector_viper",
    "Elite Radical Wraith": "elite_radical_wraith",
})

# ---------------------------------------------------------------------------
# AssetManager — hero sprite STATE handling ("Chad Math" rebrand)
#
# Each hero now has three distinct visual states instead of one static
# portrait: the Character Select pose, the Dungeon Combat idle/ready pose,
# and the Power-Up/Card Surge pose. HERO_STATE_ART maps (character, state)
# to an assets/art/<name>.png filename; drop matching PNGs in there (no
# code changes needed) and they're picked up automatically. Any state
# missing its file transparently falls back to the single-pose
# PORTRAIT_ART entry, and if THAT is also missing, falls back further to
# the existing procedural silhouette sprite -- the game never crashes or
# shows a blank hero for a missing asset.
# ---------------------------------------------------------------------------
HERO_STATES = ("select", "ready", "powerup")
HERO_STATE_ART = {
    "Math Warrior": {
        "select": "hero_warrior_select",
        "ready": "hero_warrior_ready",
        "powerup": "hero_warrior_powerup",
    },
    "Probability Wizard": {
        "select": "hero_wizard_select",
        "ready": "hero_wizard_ready",
        "powerup": "hero_wizard_powerup",
    },
}


HEROES_DIR = os.path.join(ASSET_DIR, "heroes")
BACKGROUNDS_DIR = os.path.join(ASSET_DIR, "backgrounds")
MONSTERS_DIR = os.path.join(ASSET_DIR, "monsters")
UI_DIR = os.path.join(ASSET_DIR, "ui")
EVENTS_DIR = os.path.join(ASSET_DIR, "events")
CARDS_DIR = os.path.join(ASSET_DIR, "cards")

# Nested-folder asset names for the "Chad Math" re-skin overhaul. This is a
# SECOND, additive lookup convention layered on top of the original flat
# assets/art/<name>.png one (PORTRAIT_ART/ENEMY_ART/BOSS_ART above) rather
# than a replacement for it -- everything that already worked (relics,
# cards, the aura art, the shop backdrop, the original enemy roster) keeps
# using assets/art/ untouched. AssetManager below tries this nested
# convention first and only then falls back to the flat one, so either
# convention (or neither, or a mix) works with zero crashes.
HERO_SHEET_FILES = {
    "Math Warrior": "math_warrior_sheet.png",
    "Probability Wizard": "prob_wizard_sheet.png",
}
BACKGROUND_FILES = {
    "rest_site": "campfire_rest.png",
    "combat": "spire_dungeon_bg.png",
    "title": "title_screen.png",
    "map": "spire_ascent_map.png",
}
MONSTER_FILES = {
    "Decimal Demon": "decimal_demon.png",
    "Fractal Fiend": "fractal_fiend.png",
    "Vector Viper": "vector_viper.png",
    "Elite Radical Wraith": "radical_wraith.png",
    "The Derivative Dragon": "derivative_dragon.png",
}


class AssetManager:
    """Central lookup for hero/monster/background art. Thin wrapper around
    get_art()/get_icon()'s existing load-scale-cache-with-None-on-missing
    behavior, so every call site gets the same "real art if present, else
    procedural fallback" guarantee without duplicating the fallback logic.
    Handles three loading strategies, each independently optional:
      1. Nested-folder singles/sheets (assets/heroes/, /backgrounds/,
         /monsters/) -- the newer convention, including 3-frame hero
         sprite-sheet slicing (see get_hero_sprite).
      2. Flat assets/art/<name>.png singles -- the original convention
         (HERO_STATE_ART / PORTRAIT_ART / ENEMY_ART / BOSS_ART).
      3. Procedural vector-shape fallback, drawn by the caller when this
         class returns None from every lookup above.
    Nothing here ever raises on a missing file -- every load is wrapped in
    try/except and a miss just means "try the next strategy."
    """

    def __init__(self):
        self._raw_cache = {}
        self._sheet_cache = {}
        self._bg_cache = {}
        self._misc_cache = {}

    def _load_raw(self, path):
        """Loads+converts (but does not scale) an arbitrary image path,
        caching hits AND misses so a missing file is only stat'd once."""
        if path in self._raw_cache:
            return self._raw_cache[path]
        img = None
        try:
            img = pygame.image.load(path)
            if hasattr(img, "convert_alpha"):
                img = img.convert_alpha()
        except Exception:
            img = None
        self._raw_cache[path] = img
        return img

    # -- hero stance sheets / singles ---------------------------------------

    def hero_art_name(self, character, state="ready"):
        """Resolve the flat assets/art/ filename (without .png) for a hero
        in a given state, falling back to the character's single-pose
        PORTRAIT_ART entry if that specific state's file isn't mapped/found."""
        per_state = HERO_STATE_ART.get(character, {})
        return per_state.get(state) or PORTRAIT_ART.get(character)

    def _slice_hero_sheet(self, character, max_height):
        """Loads assets/heroes/<character>_sheet.png (a single image with 3
        equal-width frames side by side: Select, Combat Ready, Power Surge)
        and slices+scales it into those 3 frames, caching the whole set
        together since slicing is the expensive part. Returns a dict
        {"select": Surface, "ready": Surface, "powerup": Surface} or None
        if the sheet file doesn't exist / fails to load."""
        cache_key = (character, max_height)
        if cache_key in self._sheet_cache:
            return self._sheet_cache[cache_key]
        filename = HERO_SHEET_FILES.get(character)
        result = None
        if filename:
            path = os.path.join(HEROES_DIR, filename)
            raw = self._load_raw(path)
            if raw is not None:
                sheet_w, sheet_h = raw.get_size()
                frame_w = sheet_w // 3
                if frame_w > 0 and sheet_h > 0:
                    scale = max_height / sheet_h
                    frame_size = (max(1, int(frame_w * scale)), max(1, int(max_height)))
                    scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
                    result = {}
                    for state, col in zip(HERO_STATES, range(3)):
                        try:
                            frame = raw.subsurface(pygame.Rect(col * frame_w, 0, frame_w, sheet_h)).copy()
                            result[state] = scaler(frame, frame_size)
                        except Exception:
                            result = None
                            break
        self._sheet_cache[cache_key] = result
        return result

    def get_hero_sprite(self, character, state, max_height):
        """Returns a loaded+scaled Surface for this hero/state, trying (in
        order) the sliced sheet, the flat per-state file, the flat base
        portrait, and finally None (caller then draws the procedural
        silhouette)."""
        sheet = self._slice_hero_sheet(character, max_height)
        if sheet is not None and sheet.get(state) is not None:
            return sheet[state]
        name = self.hero_art_name(character, state)
        if not name:
            return None
        img = get_art(name, max_height)
        if img is not None:
            return img
        # State-specific file missing -- fall back to the base single-pose
        # portrait (if the state lookup itself wasn't already that).
        base_name = PORTRAIT_ART.get(character)
        if base_name and base_name != name:
            return get_art(base_name, max_height)
        return None

    # -- monsters -------------------------------------------------------------

    def monster_art_name(self, name):
        return ENEMY_ART.get(name) or BOSS_ART.get(name)

    def get_monster_sprite(self, name, max_height):
        """Tries assets/monsters/<slug>.png first (new convention, MONSTER_
        FILES), then the flat assets/art/ convention (ENEMY_ART/BOSS_ART)."""
        filename = MONSTER_FILES.get(name)
        if filename:
            path = os.path.join(MONSTERS_DIR, filename)
            raw = self._load_raw(path)
            if raw is not None:
                w, h = raw.get_size()
                if h > 0:
                    scale = max_height / h
                    new_size = (max(1, int(w * scale)), max(1, int(max_height)))
                    scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
                    try:
                        return scaler(raw, new_size)
                    except Exception:
                        pass
        art_name = self.monster_art_name(name)
        if not art_name:
            return None
        return get_art(art_name, max_height)

    # -- full-screen backgrounds -----------------------------------------------

    def get_background(self, key):
        """Loads assets/backgrounds/<file> (BACKGROUND_FILES[key]) and
        scales it to exactly (WIDTH, HEIGHT) -- backgrounds are meant to
        fill the window edge-to-edge regardless of their source aspect
        ratio, unlike hero/monster art which preserves aspect ratio.
        Returns None if missing (caller keeps its existing procedural
        backdrop)."""
        cache_key = (key, WIDTH, HEIGHT)
        if cache_key in self._bg_cache:
            return self._bg_cache[cache_key]
        filename = BACKGROUND_FILES.get(key)
        result = None
        if filename:
            path = os.path.join(BACKGROUNDS_DIR, filename)
            raw = self._load_raw(path)
            if raw is not None:
                scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
                try:
                    result = scaler(raw, (WIDTH, HEIGHT))
                except Exception:
                    result = None
        self._bg_cache[cache_key] = result
        return result

    # -- event backdrops (assets/events/) -------------------------------------

    def get_event_backdrop(self, name, max_w, max_h):
        """Loads assets/events/<name>.png and scales it to FIT INSIDE
        (max_w, max_h) preserving its own aspect ratio (unlike
        get_background(), which stretches to exactly fill the window) --
        appropriate for a fixed-aspect event/modal backdrop like the
        wagering shrine's 16:9 art. Returns None if the file is missing,
        corrupted, or unreadable so the caller can fall back to a
        procedural drawing without crashing."""
        cache_key = ("event_backdrop", name, max_w, max_h)
        if cache_key in self._misc_cache:
            return self._misc_cache[cache_key]
        path = os.path.join(EVENTS_DIR, f"{name}.png")
        raw = self._load_raw(path)
        result = None
        if raw is not None:
            try:
                w, h = raw.get_size()
                if w > 0 and h > 0:
                    scale = min(max_w / w, max_h / h)
                    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                    scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
                    result = scaler(raw, new_size)
            except Exception:
                result = None
        self._misc_cache[cache_key] = result
        return result

    # -- card art (assets/cards/) ----------------------------------------------

    def get_card_art(self, name, max_height, max_width=None):
        """Loads assets/cards/<name>.png for a full card-frame illustration,
        aspect-preserving and capped to max_height (mirrors the flat
        get_art() convention, but reads from the dedicated assets/cards/
        directory). If max_width is also given, fits inside BOTH bounds
        (like get_event_backdrop) instead of height-only, so a card art
        frame that's wider than it is tall never overflows its panel.
        Returns None if missing/unreadable -- callers always fall back to
        a procedural placeholder rather than crash or show a blank frame."""
        cache_key = ("card_art", name, max_height, max_width)
        if cache_key in self._misc_cache:
            return self._misc_cache[cache_key]
        path = os.path.join(CARDS_DIR, f"{name}.png")
        raw = self._load_raw(path)
        result = None
        if raw is not None:
            try:
                w, h = raw.get_size()
                if w > 0 and h > 0:
                    scale = max_height / h
                    if max_width is not None:
                        scale = min(scale, max_width / w)
                    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                    scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
                    result = scaler(raw, new_size)
            except Exception:
                result = None
        self._misc_cache[cache_key] = result
        return result

    def get_card_icon(self, name, size):
        """Loads assets/cards/<name>.png scaled down to a small size x size
        square for miniature UI use (badges, banners). Returns None if
        missing/unreadable."""
        cache_key = ("card_icon", name, size)
        if cache_key in self._misc_cache:
            return self._misc_cache[cache_key]
        path = os.path.join(CARDS_DIR, f"{name}.png")
        raw = self._load_raw(path)
        result = None
        if raw is not None:
            scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
            try:
                result = scaler(raw, (size, size))
            except Exception:
                result = None
        self._misc_cache[cache_key] = result
        return result


ASSET_MANAGER = AssetManager()


def blit_icon(surface, name, center, size, alpha=255):
    """Draw a cached icon centered at ``center``. No-ops quietly if the icon
    file wasn't found (keeps every screen renderable even with assets missing)."""
    img = get_icon(name, size)
    if img is None:
        return False
    if alpha < 255 and hasattr(img, "set_alpha"):
        img.set_alpha(alpha)
    rect = img.get_rect(center=center)
    surface.blit(img, rect.topleft)
    if alpha < 255 and hasattr(img, "set_alpha"):
        img.set_alpha(255)
    return True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 1280, 720

# Palette
BG = (14, 18, 24)
PANEL = (27, 34, 41)
PANEL_LIGHT = (36, 44, 54)
CARD = (41, 49, 60)
EDGE = (140, 160, 180)
TEXT = (244, 246, 249)
MUTED = (155, 170, 183)
BLUE = (80, 152, 255)
GREEN = (76, 210, 123)
GOLD = (255, 200, 83)
RED = (233, 89, 89)
PURPLE = (177, 123, 255)
ORANGE = (255, 152, 84)
BLACK = (0, 0, 0)
DIM = (58, 66, 76)
WHITE = (255, 255, 255)

RARITY_COLORS = {
    "common": (168, 178, 190),
    "uncommon": (94, 170, 255),
    "rare": (255, 200, 83),
    "curse": (200, 50, 50),
}

TYPE_COLORS = {
    "attack": (233, 110, 92),
    "skill": (95, 206, 150),
    "power": (177, 123, 255),
}

NODE_COLORS = {
    "combat": BLUE,
    "elite": ORANGE,
    "event": PURPLE,
    "rest": GREEN,
    "shop": GOLD,
    "treasure": (255, 225, 130),
    "boss": RED,
}

NODE_ICONS = {
    "combat": "⚔",   # crossed swords
    "elite": "★",    # star
    "event": "?",
    "rest": "☀",     # sun-ish / campfire glow stand-in
    "shop": "$",
    "treasure": "✦",
    "boss": "☠",     # skull
}

NODE_LABELS = {
    "combat": "Combat",
    "elite": "Elite",
    "event": "Unknown",
    "rest": "Rest",
    "shop": "Shop",
    "treasure": "Treasure",
    "boss": "Boss",
}

# ---------------------------------------------------------------------------
# Lazy pygame / display state
#
# Nothing here touches SDL at import time. ``init_display()`` must be
# called once (by ``main()``, or explicitly by a test that wants to render)
# before any of the draw_* functions are used.
# ---------------------------------------------------------------------------

_DISPLAY_READY = False
screen = None          # fixed 1280x720 canvas everything is drawn onto
real_display = None    # the actual OS window/monitor surface (windowed or fullscreen)
clock = None
FONT_TITLE = None
FONT_H1 = None
FONT_HEADER = None
FONT_BODY = None
FONT_SMALL = None
FONT_TINY = None
FONT_MICRO = None

is_fullscreen = False
_present_scale = 1.0
_present_offset = (0, 0)
_real_mouse_get_pos = None  # pygame.mouse.get_pos before we wrap it


def _update_present_transform():
    """Recompute the scale/letterbox offset used to blit the fixed-size
    ``screen`` canvas onto ``real_display``, whatever size that window or
    monitor actually is. Keeps the game's 1280x720 aspect ratio intact
    (black bars on the sides/top-bottom rather than stretching)."""
    global _present_scale, _present_offset
    if real_display is None:
        return
    rw, rh = real_display.get_size()
    scale = max(0.01, min(rw / WIDTH, rh / HEIGHT))
    scaled_w, scaled_h = int(WIDTH * scale), int(HEIGHT * scale)
    _present_scale = scale
    _present_offset = ((rw - scaled_w) // 2, (rh - scaled_h) // 2)


def _scaled_mouse_pos():
    """Replacement for pygame.mouse.get_pos() that maps real window/monitor
    coordinates back into the fixed 1280x720 canvas space every draw_*
    function actually works in. Without this, hover states and clicks would
    be misaligned any time the window is resized or fullscreened."""
    raw_x, raw_y = _real_mouse_get_pos()
    ox, oy = _present_offset
    if _present_scale <= 0:
        return (0, 0)
    return (int((raw_x - ox) / _present_scale), int((raw_y - oy) / _present_scale))


def screen_pos_from_real(pos):
    """Map a real mouse-event position (e.g. event.pos) into canvas space,
    same transform as _scaled_mouse_pos but for one-off event coordinates."""
    rx, ry = pos
    ox, oy = _present_offset
    if _present_scale <= 0:
        return (0, 0)
    return (int((rx - ox) / _present_scale), int((ry - oy) / _present_scale))


def set_fullscreen(enabled):
    """Switch the actual OS window between windowed and fullscreen. The
    drawing canvas (``screen``) never changes size — only how it's
    presented does — so no rendering code needs to know or care."""
    global real_display, is_fullscreen
    is_fullscreen = enabled
    if enabled:
        real_display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        real_display = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Chad Math vs. The Forces of the Math Spire")
    _update_present_transform()


def toggle_fullscreen():
    set_fullscreen(not is_fullscreen)


WINDOW_SIZE_PRESETS = [(1280, 720), (1600, 900), (1920, 1080)]


def set_window_size(w, h):
    """Resize the real window to a preset while staying windowed (with
    normal minimize/maximize/close chrome) — this is the non-fullscreen
    'bigger screen' option from the pause menu. Also drops out of
    fullscreen if it was on, since a windowed size only makes sense
    windowed."""
    global real_display, is_fullscreen
    is_fullscreen = False
    real_display = pygame.display.set_mode((w, h), pygame.RESIZABLE)
    pygame.display.set_caption("Chad Math vs. The Forces of the Math Spire")
    _update_present_transform()


def present():
    """Blit the fixed-size canvas onto the real window/monitor, scaled and
    letterboxed to fit, then flip. Call this instead of pygame.display.flip()."""
    real_display.fill((0, 0, 0))
    rw, rh = real_display.get_size()
    if (rw, rh) == (WIDTH, HEIGHT):
        real_display.blit(screen, (0, 0))
    else:
        scaled_w = max(1, int(WIDTH * _present_scale))
        scaled_h = max(1, int(HEIGHT * _present_scale))
        scaled = pygame.transform.smoothscale(screen, (scaled_w, scaled_h))
        real_display.blit(scaled, _present_offset)
    pygame.display.flip()


def init_display():
    """Initialize pygame + the window + fonts. Safe to call more than once."""
    global _DISPLAY_READY, screen, clock, real_display, _real_mouse_get_pos
    global FONT_TITLE, FONT_H1, FONT_HEADER, FONT_BODY, FONT_SMALL, FONT_TINY, FONT_MICRO

    if _DISPLAY_READY:
        return

    pygame.init()
    screen = pygame.Surface((WIDTH, HEIGHT))
    real_display = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Chad Math vs. The Forces of the Math Spire")
    _update_present_transform()

    _real_mouse_get_pos = pygame.mouse.get_pos
    pygame.mouse.get_pos = _scaled_mouse_pos

    clock = pygame.time.Clock()

    def load(style, size, fallback_bold=False):
        path = FONT_FILES.get(style)
        if path and os.path.isfile(path):
            try:
                return pygame.font.Font(path, size)
            except Exception:
                pass
        return pygame.font.SysFont("georgia,timesnewroman,serif", size, bold=fallback_bold)

    # Cinzel Decorative (engraved-stone display face) for titles/headers,
    # Cinzel for section labels, EB Garamond (old-book serif) for body copy.
    FONT_TITLE = load("title", 46)
    FONT_H1 = load("header", 30)
    FONT_HEADER = load("label", 22)
    FONT_BODY = load("body", 20)
    FONT_SMALL = load("body", 16)
    FONT_TINY = load("body", 13)
    FONT_MICRO = load("body", 10)

    _DISPLAY_READY = True


# ---------------------------------------------------------------------------
# Character data
# ---------------------------------------------------------------------------

CHARACTER_OPTIONS = {
    "Math Warrior": {
        "hp": 94,
        "max_hp": 94,
        "energy": 3,
        "max_energy": 3,
        "description": "A brute-force math bruiser who loves clean equations and brutal damage.",
        "role_blurb": "A calculated brute who relies primarily on blocking and skills.",
        "starting_relic": "Pi's Defense",
        "deck": [
            "Strike", "Strike", "Strike", "Strike",
            "Defend", "Defend", "Defend", "Defend",
            "Arcane Dart", "Arcane Dart",
            "Probability Shield", "Probability Shield",
            "Weighted Step", "Chaos Bloom",
            "Dice Slash",
        ],
    },
    "Probability Wizard": {
        "hp": 80,
        "max_hp": 80,
        "energy": 3,
        "max_energy": 3,
        "description": "High variance spellcaster who rolls the odds into pure chaos.",
        "role_blurb": "A fast-paced probability caster whose Abacus Charm turns a base 3 energy into 4 every turn.",
        "starting_relic": "Abacus Charm",
        "deck": [
            "Strike", "Strike", "Defend", "Defend",
            "Arcane Dart", "Arcane Dart", "Arcane Dart",
            "Reckless Roll", "Reckless Roll",
            "Null Burst", "Probability Shield",
            "Glass Guard", "Chaos Bloom",
            "Weighted Step", "Dice Burst",
        ],
    },
}


# ---------------------------------------------------------------------------
# Relic + Power hook system
#
# Every relic/power is pure data plus a handful of optional callables keyed
# by "hook name". `trigger_hook` walks the player's owned relics AND active
# powers and calls whichever of those hooks is present. This is the single
# place effects are wired up, so nothing can be "added to the list" without
# also being applied.
#
# Recognized hooks:
#   on_acquire(game)                       -- once, when first obtained
#   on_combat_start(game)                  -- start of every combat
#   on_turn_start(game)                    -- start of every player turn
#   on_roll(game, value, sides) -> value   -- folds over every dice roll
#   on_block(game, amount) -> amount       -- folds over every block gain
#   on_roll_fail(game)                     -- fires on a "low" dice roll
#   on_rest(game, amount) -> amount        -- folds over rest-site healing
#   on_lethal(game) -> bool                -- return True to cancel death
# ---------------------------------------------------------------------------


def _relic_pis_defense_turn_start(game):
    # 3.14 repeating -> rounds down to a flat 3 block, every turn (not just
    # the first one of combat -- that's the whole point of the rename from
    # the old "Weighted Lens", which only fired once per fight and was
    # barely worth having).
    game.gain_block(3, "Pi's Defense", silent=True)


def _relic_calculator_shield_victory(game):
    game.player["max_hp"] += 8
    game.player["hp"] = min(game.player["max_hp"], game.player["hp"] + 8)


def _relic_ritual_prism_roll(game, value, sides):
    return min(sides, value + 2)


def _relic_proof_by_contradiction_fail(game):
    game.gain_block(4, "Proof by Contradiction", silent=True)


# Relic name -> flat energy bonus granted every turn. A single source of
# truth for "how much" so the turn-start hook below and the HUD's display
# helper (energy_bonus_from_relics()) can never drift out of sync with
# each other -- they used to duplicate this "+1" as two separate literals.
PER_TURN_ENERGY_RELICS = {"Abacus Charm": 1}


def _relic_abacus_charm_turn_start(game):
    game.player["energy"] += PER_TURN_ENERGY_RELICS["Abacus Charm"]


def energy_bonus_from_relics(game):
    """Total flat per-turn energy bonus from the player's owned relics
    (currently just Abacus Charm's +1, see PER_TURN_ENERGY_RELICS). Used
    by the combat HUD so the energy readout shows an honest current/max
    (e.g. Probability Wizard's "4/4") instead of current energy exceeding
    the character's raw base max_energy ("4/3"), which reads as a broken
    number rather than "3 base + a relic bonus" -- and so the bonus stays
    visibly labeled on-screen instead of being silently folded in."""
    owned = game.player.get("relics", [])
    return sum(bonus for relic, bonus in PER_TURN_ENERGY_RELICS.items() if relic in owned)


def _relic_theorem_of_calm_rest(game, amount):
    return amount + 8


def _relic_dice_sigil_roll(game, value, sides):
    return max(2, value)


def _relic_probability_crown_combat_start(game):
    game._crown_pending = True


def _relic_golden_ratio_block(game, amount):
    return max(amount, round(amount * 1.2))


def _relic_infinite_series_turn_start(game):
    game.draw_cards(1)


def _relic_null_set_lethal(game):
    if not game._null_set_used:
        game._null_set_used = True
        game.player["hp"] = 1
        game.log_text = "Null Set intervenes: you survive with 1 HP!"
        game.floating_text.append({"text": "SAVED!", "x": 320, "y": 190, "color": GOLD, "life": 1.3})
        return True
    return False


def _relic_lucky_denominator_combat_start(game):
    game.player["energy"] = game.player["max_energy"] + (1 if "Abacus Charm" in game.player["relics"] else 0)


RELIC_LIBRARY = {
    "Pi's Defense": {
        "description": "Gain 3.14 (repeating) block every turn. Rounds down to 3 block.",
        "rarity": "common",
        "hooks": {"on_turn_start": _relic_pis_defense_turn_start},
    },
    "Calculator Shield": {
        "description": "Gain 8 max HP after every battle you win.",
        "rarity": "common",
        "hooks": {"on_victory": _relic_calculator_shield_victory},
    },
    "Ritual Prism": {
        "description": "All dice rolls (combat and events) gain +2 to the result.",
        "rarity": "uncommon",
        "hooks": {"on_roll": _relic_ritual_prism_roll},
    },
    "Pencil of Fortune": {
        "description": "Card rewards always offer at least one upgraded card.",
        "rarity": "uncommon",
        "hooks": {},  # consulted directly by generate_card_reward_options
    },
    "Proof by Contradiction": {
        "description": "Whenever a die roll comes up low, gain 4 block.",
        "rarity": "common",
        "hooks": {"on_roll_fail": _relic_proof_by_contradiction_fail},
    },
    "Abacus Charm": {
        "description": "Gain 1 extra energy at the start of every turn.",
        "rarity": "rare",
        "hooks": {"on_turn_start": _relic_abacus_charm_turn_start},
    },
    "Theorem of Calm": {
        "description": "Rest sites heal 8 additional HP.",
        "rarity": "common",
        "hooks": {"on_rest": _relic_theorem_of_calm_rest},
    },
    "Dice Sigil": {
        "description": "Every die you roll is treated as at least 2.",
        "rarity": "uncommon",
        "hooks": {"on_roll": _relic_dice_sigil_roll},
    },
    "Probability Crown": {
        "description": "Your first attack each combat automatically deals double damage.",
        "rarity": "rare",
        "hooks": {"on_combat_start": _relic_probability_crown_combat_start},
    },
    "Golden Ratio": {
        "description": "Block gained from cards and relics is increased by 20%.",
        "rarity": "uncommon",
        "hooks": {"on_block": _relic_golden_ratio_block},
    },
    "Infinite Series": {
        "description": "Draw 1 additional card at the start of every turn.",
        "rarity": "rare",
        "hooks": {"on_turn_start": _relic_infinite_series_turn_start},
    },
    "Null Set": {
        "description": "The first time you would fall to 0 HP each combat, survive with 1 HP instead.",
        "rarity": "rare",
        "hooks": {"on_lethal": _relic_null_set_lethal},
    },
}

RELIC_NAMES = list(RELIC_LIBRARY.keys())


def _power_central_limit_turn_start(game):
    game.gain_block(3, "Central Limit Theorem", silent=True)


def _power_law_of_large_numbers_roll(game, value, sides):
    return min(sides, value + 1)


POWER_LIBRARY = {
    "Central Limit Theorem": {
        "description": "At the start of each turn, gain 3 block.",
        "hooks": {"on_turn_start": _power_central_limit_turn_start},
    },
    "Law of Large Numbers": {
        "description": "Your dice rolls gain +1 for the rest of combat.",
        "hooks": {"on_roll": _power_law_of_large_numbers_roll},
    },
}


def trigger_hook(game, hook_name, *args, **kwargs):
    """Call ``hook_name`` on every owned relic and every active power."""
    for name in list(game.player["relics"]):
        fn = RELIC_LIBRARY.get(name, {}).get("hooks", {}).get(hook_name)
        if fn:
            fn(game, *args, **kwargs)
    for name in list(game.player.get("active_powers", [])):
        fn = POWER_LIBRARY.get(name, {}).get("hooks", {}).get(hook_name)
        if fn:
            fn(game, *args, **kwargs)


def apply_roll_modifiers(game, value, sides):
    for name in list(game.player["relics"]):
        fn = RELIC_LIBRARY.get(name, {}).get("hooks", {}).get("on_roll")
        if fn:
            value = fn(game, value, sides)
    for name in list(game.player.get("active_powers", [])):
        fn = POWER_LIBRARY.get(name, {}).get("hooks", {}).get("on_roll")
        if fn:
            value = fn(game, value, sides)
    # Decimal Demon's "Floating Point Shift" applies Truncate: -1 to every
    # player dice roll for their next turn (see enemy_turn()'s
    # "decimal_shift" branch). Floored at 1 -- a die can't roll below 1.
    if game.player.get("statuses", {}).get("truncate", 0) > 0:
        value = max(1, value - 1)
    return value


def apply_block_modifiers(game, amount):
    for name in list(game.player["relics"]):
        fn = RELIC_LIBRARY.get(name, {}).get("hooks", {}).get("on_block")
        if fn:
            amount = fn(game, amount)
    return amount


def any_lethal_save(game):
    """Return True if some relic prevented death (e.g. Null Set)."""
    for name in list(game.player["relics"]):
        fn = RELIC_LIBRARY.get(name, {}).get("hooks", {}).get("on_lethal")
        if fn and fn(game):
            return True
    return False


# ---------------------------------------------------------------------------
# Card data
#
# Every base card also has an "upgraded" entry under "<Name>+" used by the
# rest-site "Study" option and by Pencil of Fortune reward pools. Upgraded
# cards keep the same cost/type/art but hit harder or do more.
# ---------------------------------------------------------------------------

CARD_LIBRARY = {
    "Strike": {
        "cost": 1, "type": "attack", "rarity": "common",
        "desc": "Deal 8 damage.",
        "upgrades_to": "Strike+",
        "effect": lambda game: game.class_strike(),
    },
    "Strike+": {
        "cost": 1, "type": "attack", "rarity": "common",
        "desc": "Deal 12 damage.",
        "base": "Strike",
        "effect": lambda game: game.class_strike(upgraded=True),
    },
    "Defend": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Gain 8 block.",
        "upgrades_to": "Defend+",
        "effect": lambda game: game.gain_block(8, "Defend"),
    },
    "Defend+": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Gain 12 block.",
        "base": "Defend",
        "effect": lambda game: game.gain_block(12, "Defend+"),
    },
    "Arcane Dart": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d6 + 2 damage.",
        "upgrades_to": "Arcane Dart+",
        "effect": lambda game: game.deal_damage(game.enemy, game.roll_dice(6, "Arcane Dart") + 2, "Arcane Dart"),
    },
    "Arcane Dart+": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d6 + 5 damage.",
        "base": "Arcane Dart",
        "effect": lambda game: game.deal_damage(game.enemy, game.roll_dice(6, "Arcane Dart+") + 5, "Arcane Dart+"),
    },
    "Reckless Roll": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d10. 1 = hit yourself for 4, else deal double the roll.",
        "upgrades_to": "Reckless Roll+",
        "effect": lambda game: game.reckless_roll(),
    },
    "Reckless Roll+": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d10. 1-2 = hit yourself for 4, else deal double the roll.",
        "base": "Reckless Roll",
        "effect": lambda game: game.reckless_roll(upgraded=True),
    },
    "Gambler's Flurry": {
        "cost": 1, "type": "attack", "rarity": "rare",
        "desc": "Roll 1d6 for damage. Bust (1) ends your turn and costs 3 HP. "
                "Otherwise, bank it and choose: strike now, or push your "
                "luck and roll again.",
        "effect": lambda game: game.play_gamblers_flurry(),
    },
    "Progressive Overload": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d8 + 2 damage. If the roll is 5+, this card gains "
                "+1 damage for the rest of combat.",
        "effect": lambda game: game.play_progressive_overload(),
    },
    "Spotter's Axiom": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Roll 1d6. Gain block equal to double the roll (2-12 block).",
        "effect": lambda game: game.play_spotters_axiom(),
    },
    "Probability Shield": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Roll 1d8 and gain that much block.",
        "upgrades_to": "Probability Shield+",
        "effect": lambda game: game.roll_block(),
    },
    "Probability Shield+": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Roll 1d8 + 3 and gain that much block.",
        "base": "Probability Shield",
        "effect": lambda game: game.roll_block(bonus=3, label="Probability Shield+"),
    },
    "Chaos Bloom": {
        "cost": 2, "type": "attack", "rarity": "rare",
        "desc": "Deal 12 + 1d6 damage.",
        "upgrades_to": "Chaos Bloom+",
        "effect": lambda game: game.deal_damage(game.enemy, 12 + game.roll_dice(6, "Chaos Bloom"), "Chaos Bloom"),
    },
    "Chaos Bloom+": {
        "cost": 2, "type": "attack", "rarity": "rare",
        "desc": "Deal 18 + 1d6 damage.",
        "base": "Chaos Bloom",
        "effect": lambda game: game.deal_damage(game.enemy, 18 + game.roll_dice(6, "Chaos Bloom+"), "Chaos Bloom+"),
    },
    "Weighted Step": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Gain 6 block and draw 1 card.",
        "upgrades_to": "Weighted Step+",
        "effect": lambda game: (game.gain_block(6, "Weighted Step"), game.draw_cards(1)),
    },
    "Weighted Step+": {
        "cost": 1, "type": "skill", "rarity": "common",
        "desc": "Gain 9 block and draw 2 cards.",
        "base": "Weighted Step",
        "effect": lambda game: (game.gain_block(9, "Weighted Step+"), game.draw_cards(2)),
    },
    "Null Burst": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 15 or 7 damage (coin flip).",
        "upgrades_to": "Null Burst+",
        "effect": lambda game: game.coin_flip_damage(15, 7, "Null Burst"),
    },
    "Null Burst+": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 20 or 10 damage (coin flip).",
        "base": "Null Burst",
        "effect": lambda game: game.coin_flip_damage(20, 10, "Null Burst+"),
    },
    "Glass Guard": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Gain 10 block and lose 1 HP.",
        "upgrades_to": "Glass Guard+",
        "effect": lambda game: (game.gain_block(10, "Glass Guard"), game.deal_damage(game.player, 1, "Glass Guard")),
    },
    "Glass Guard+": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Gain 15 block and lose 1 HP.",
        "base": "Glass Guard",
        "effect": lambda game: (game.gain_block(15, "Glass Guard+"), game.deal_damage(game.player, 1, "Glass Guard+")),
    },
    "Dice Slash": {
        "cost": 1, "type": "attack", "rarity": "common",
        "desc": "Roll 1d20. Deal that much damage.",
        "upgrades_to": "Dice Slash+",
        "effect": lambda game: game.roll_damage_card(20, "Dice Slash"),
    },
    "Dice Slash+": {
        "cost": 1, "type": "attack", "rarity": "common",
        "desc": "Roll 1d20. Deal that much damage + 4.",
        "base": "Dice Slash",
        "effect": lambda game: game.roll_damage_card(20, "Dice Slash+", bonus=4),
    },
    "Dice Burst": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d12. Deal 8 + result damage.",
        "upgrades_to": "Dice Burst+",
        "effect": lambda game: game.roll_damage_card(12, "Dice Burst", bonus=8),
    },
    "Dice Burst+": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Roll 1d12. Deal 13 + result damage.",
        "base": "Dice Burst",
        "effect": lambda game: game.roll_damage_card(12, "Dice Burst+", bonus=13),
    },
    "Standard Deviation": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 6 damage 3 times.",
        "upgrades_to": "Standard Deviation+",
        "effect": lambda game: [game.deal_damage(game.enemy, 6, "Standard Deviation") for _ in range(3)],
    },
    "Standard Deviation+": {
        "cost": 2, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 6 damage 4 times.",
        "base": "Standard Deviation",
        "effect": lambda game: [game.deal_damage(game.enemy, 6, "Standard Deviation+") for _ in range(4)],
    },
    "Confidence Interval": {
        "cost": 2, "type": "skill", "rarity": "uncommon",
        "desc": "Gain 8 block and heal 5 HP.",
        "upgrades_to": "Confidence Interval+",
        "effect": lambda game: (game.gain_block(8, "Confidence Interval"), game.heal_player(5)),
    },
    "Confidence Interval+": {
        "cost": 2, "type": "skill", "rarity": "uncommon",
        "desc": "Gain 12 block and heal 8 HP.",
        "base": "Confidence Interval",
        "effect": lambda game: (game.gain_block(12, "Confidence Interval+"), game.heal_player(8)),
    },
    "Bayesian Update": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Draw 2 cards.",
        "upgrades_to": "Bayesian Update+",
        "effect": lambda game: game.draw_cards(2),
    },
    "Bayesian Update+": {
        "cost": 0, "type": "skill", "rarity": "uncommon",
        "desc": "Draw 2 cards. Costs 0 energy.",
        "base": "Bayesian Update",
        "effect": lambda game: game.draw_cards(2),
    },
    "Integral Strike": {
        "cost": 2, "type": "attack", "rarity": "rare",
        "desc": "Deal damage equal to 6 times your remaining energy.",
        "upgrades_to": "Integral Strike+",
        "effect": lambda game: game.deal_damage(game.enemy, 6 * (game.player["energy"] + 1), "Integral Strike"),
    },
    "Integral Strike+": {
        "cost": 2, "type": "attack", "rarity": "rare",
        "desc": "Deal damage equal to 9 times your remaining energy.",
        "base": "Integral Strike",
        "effect": lambda game: game.deal_damage(game.enemy, 9 * (game.player["energy"] + 1), "Integral Strike+"),
    },
    "Factorial Fury": {
        "cost": 3, "type": "attack", "rarity": "rare",
        "desc": "Deal 5 damage 5 times.",
        "upgrades_to": "Factorial Fury+",
        "effect": lambda game: [game.deal_damage(game.enemy, 5, "Factorial Fury") for _ in range(5)],
    },
    "Factorial Fury+": {
        "cost": 3, "type": "attack", "rarity": "rare",
        "desc": "Deal 7 damage 5 times.",
        "base": "Factorial Fury",
        "effect": lambda game: [game.deal_damage(game.enemy, 7, "Factorial Fury+") for _ in range(5)],
    },
    "Central Limit Theorem": {
        "cost": 2, "type": "power", "rarity": "rare",
        "desc": "Power. At the start of each turn this combat, gain 3 block.",
        "upgrades_to": "Central Limit Theorem+",
        "effect": lambda game: game.gain_power("Central Limit Theorem"),
    },
    "Central Limit Theorem+": {
        "cost": 1, "type": "power", "rarity": "rare",
        "desc": "Power. At the start of each turn this combat, gain 3 block. Costs 1 energy.",
        "base": "Central Limit Theorem",
        "effect": lambda game: game.gain_power("Central Limit Theorem"),
    },
    "Law of Large Numbers": {
        "cost": 1, "type": "power", "rarity": "uncommon",
        "desc": "Power. Your dice rolls gain +1 for the rest of combat.",
        "upgrades_to": "Law of Large Numbers+",
        "effect": lambda game: game.gain_power("Law of Large Numbers"),
    },
    "Law of Large Numbers+": {
        "cost": 0, "type": "power", "rarity": "uncommon",
        "desc": "Power. Your dice rolls gain +1 for the rest of combat. Costs 0 energy.",
        "base": "Law of Large Numbers",
        "effect": lambda game: game.gain_power("Law of Large Numbers"),
    },
    "Weak Hypothesis": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 5 damage and apply 2 Weak for 2 turns.",
        "drop_only": True,
        "upgrades_to": "Weak Hypothesis+",
        "effect": lambda game: (game.deal_damage(game.enemy, 5, "Weak Hypothesis"), game.apply_status(game.enemy, "weak", 2)),
    },
    "Weak Hypothesis+": {
        "cost": 1, "type": "attack", "rarity": "uncommon",
        "desc": "Deal 8 damage and apply 3 Weak for 2 turns.",
        "base": "Weak Hypothesis",
        "drop_only": True,
        "effect": lambda game: (game.deal_damage(game.enemy, 8, "Weak Hypothesis+"), game.apply_status(game.enemy, "weak", 3)),
    },
    "Confidence Collapse": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Apply 2 Vulnerable for 2 turns and draw 1 card.",
        "drop_only": True,
        "upgrades_to": "Confidence Collapse+",
        "effect": lambda game: (game.apply_status(game.enemy, "vulnerable", 2), game.draw_cards(1)),
    },
    "Confidence Collapse+": {
        "cost": 1, "type": "skill", "rarity": "uncommon",
        "desc": "Apply 3 Vulnerable for 2 turns and draw 2 cards.",
        "base": "Confidence Collapse",
        "drop_only": True,
        "effect": lambda game: (game.apply_status(game.enemy, "vulnerable", 3), game.draw_cards(2)),
    },
    "Misread Equation": {
        "cost": 0, "type": "skill", "rarity": "curse",
        "desc": "Curse. When drawn, lose 6 HP. Never offered as a card reward. "
                "Currently unreachable in normal play (no active source grants "
                "it), but kept defined since CURSE_CARD_NAMES/REWARDABLE_CARDS "
                "and older save-compatible tooling still reference it by name.",
        "drop_only": True,
        "effect": lambda game: game.deal_damage(game.player, 6, "Misread Equation"),
    },
    "Ultimate Strike": {
        "cost": 0, "type": "attack", "rarity": "rare",
        "desc": "Roll 1d20 — deal that damage, gain that block. Brain dump "
                "so hard you forget it forever after one use.",
        "drop_only": True,
        "exhaust": True,
        "effect": lambda game: game.ultimate_strike_effect(),
    },
    "Ultimate Blast": {
        "cost": 0, "type": "attack", "rarity": "rare",
        "desc": "Roll 1d20 and deal 1.5x that much damage. Brain dump so "
                "hard you forget it forever after one use.",
        "drop_only": True,
        "exhaust": True,
        "effect": lambda game: game.ultimate_blast_effect(),
    },
}

# Curses: strictly negative cards that must NEVER appear as a card-reward
# choice (post-combat picks, the shop, ...). Misread Equation currently has
# no active source in normal play — the old "?" room curse-on-bad-roll was
# replaced by the Math Club Wagering Table (see choose_safe_bet /
# choose_degenerate_bet / walk_away_from_wager). Kept separate from
# STATUS_CARD_NAMES, which is for good-but-uncommon "drop_only" cards that
# ARE still fair reward options.
CURSE_CARD_NAMES = {"Misread Equation"}

# Cards that can appear as random rewards (upgraded "+" cards are reward-only
# via Pencil of Fortune / rest-site study, never dealt as a base drop; curses
# are never a reward at all — see CURSE_CARD_NAMES above).
REWARDABLE_CARDS = [name for name, c in CARD_LIBRARY.items() if "base" not in c and name not in CURSE_CARD_NAMES]

STATUS_CARD_NAMES = {"Weak Hypothesis", "Weak Hypothesis+", "Confidence Collapse", "Confidence Collapse+"}
ULTIMATE_CARD_NAMES = {"Ultimate Strike", "Ultimate Blast"}

# ---------------------------------------------------------------------------
# Math Inspector -- card damage Expected Value: E(V) = Σ [ outcome_i * P(outcome_i) ]
#
# Only cards whose damage comes from a well-defined probability distribution
# get an entry (pure-block/draw/power cards deal no damage, so "damage EV"
# doesn't apply to them and they're left out of this table entirely -- see
# card_ev() below, which returns None for anything not listed here, and
# every call site treats None as "no EV to show" rather than 0). A few
# cards resolve their damage from *current game state* (Integral Strike
# scales with remaining energy) rather than a fixed distribution, so those
# entries are callables taking ``game`` instead of a plain float.
# ---------------------------------------------------------------------------
CARD_EV = {
    # Strike is character-dependent (class_strike()): Math Warrior hits for
    # a flat 8/12, Probability Wizard rolls 3d3/4d3 for Probability Missile.
    "Strike": lambda game: 3 * 2.0 if game.character == "Probability Wizard" else 8.0,
    "Strike+": lambda game: 4 * 2.0 if game.character == "Probability Wizard" else 12.0,
    "Arcane Dart": 3.5 + 2,
    "Arcane Dart+": 3.5 + 5,
    # Reckless Roll: 1d10, roll<=threshold backfires (0 enemy damage),
    # otherwise deals 2x the roll. E(V) = Σ_{roll>threshold} (2*roll)/10.
    "Reckless Roll": sum(2 * r for r in range(2, 11)) / 10,       # threshold 1
    "Reckless Roll+": sum(2 * r for r in range(3, 11)) / 10,      # threshold 2
    # Progressive Overload: 1d8 + 2, baseline (no accumulated combat bonus).
    "Progressive Overload": 4.5 + 2,
    "Chaos Bloom": 12 + 3.5,
    "Chaos Bloom+": 18 + 3.5,
    "Null Burst": (15 + 7) / 2,       # coin flip: heads 15, tails 7
    "Null Burst+": (20 + 10) / 2,
    "Dice Slash": (1 + 20) / 2,       # 1d20
    "Dice Slash+": (1 + 20) / 2 + 4,
    "Dice Burst": (1 + 12) / 2 + 8,   # 1d12 + bonus
    "Dice Burst+": (1 + 12) / 2 + 13,
    "Standard Deviation": 6 * 3,      # deterministic (no dice): 3 fixed hits
    "Standard Deviation+": 7 * 4,
    "Integral Strike": lambda game: 6 * (game.player["energy"] + 1),
    "Integral Strike+": lambda game: 9 * (game.player["energy"] + 1),
    "Factorial Fury": 5 * 5,          # deterministic: 5 fixed hits
    "Factorial Fury+": 7 * 5,
    "Weak Hypothesis": 5.0,           # deterministic damage portion (Weak debuff not counted in EV)
    "Weak Hypothesis+": 8.0,
    "Ultimate Strike": (1 + 20) / 2,  # 1d20 (also grants that much block, EV shown is damage only)
    "Ultimate Blast": (1 + 20) / 2 * 1.5,
}


def card_ev(game, card_name):
    """Expected damage value for ``card_name`` given the current game state,
    or None if this card doesn't deal damage from a probability
    distribution (pure block/draw/power cards, curses, etc.). CARD_EV keys
    every upgraded "+" card separately from its base (their odds/bonuses
    differ), so this looks the exact card_name up directly -- no
    base-card collapsing here."""
    entry = CARD_EV.get(card_name)
    if entry is None:
        return None
    return entry(game) if callable(entry) else entry


# Expected self-inflicted recoil/backfire damage for cards whose effect can
# hurt the player who plays them. Only Reckless Roll(+) currently has a
# probabilistic recoil branch (reckless_roll(): roll<=threshold backfires
# for 4 to the player instead of hitting the enemy).
#   Reckless Roll  (threshold=1): backfire on roll==1,   P=1/10 -> 4*0.1=0.4
#   Reckless Roll+ (threshold=2): backfire on roll in{1,2}, P=2/10 -> 4*0.2=0.8
CARD_RECOIL_EV = {
    "Reckless Roll": 4 * (1 / 10),
    "Reckless Roll+": 4 * (2 / 10),
}


def card_recoil_ev(game, card_name):
    """Expected self-damage ("recoil") from playing card_name, or None if
    this card has no recoil/backfire branch."""
    entry = CARD_RECOIL_EV.get(card_name)
    if entry is None:
        return None
    return entry(game) if callable(entry) else entry


def card_net_ev(game, card_name):
    """Net E(V) = Gross E(Dmg) - Expected Recoil. None if the card has no
    recoil (nothing to net against) or no gross damage EV."""
    gross = card_ev(game, card_name)
    recoil = card_recoil_ev(game, card_name)
    if gross is None or recoil is None:
        return None
    return gross - recoil


def card_cost_efficiency(game, card_name):
    """Net E(V) per energy spent. None if net EV isn't defined, or if the
    card is free (division by zero)."""
    net = card_net_ev(game, card_name)
    if net is None:
        return None
    cost = CARD_LIBRARY.get(card_name, {}).get("cost", 0)
    if not cost:
        return None
    return net / cost


def wrapped_text(font, text, width):
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        trial = current + (" " if current else "") + word
        if font.size(trial)[0] <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_lines(font, lines, color=TEXT):
    return [font.render(line, True, color) for line in lines]


def render_bar(surface, rect, value, max_value, color, bg_color):
    pygame.draw.rect(surface, bg_color, rect, border_radius=8)
    if max_value <= 0:
        return
    fill_width = max(0, min(rect.width, int((value / max_value) * rect.width)))
    fill_rect = pygame.Rect(rect.left, rect.top, fill_width, rect.height)
    pygame.draw.rect(surface, color, fill_rect, border_radius=8)


def render_health_bar_with_block(surface, rect, value, maximum, block, color, bg_color):
    render_bar(surface, rect, value, maximum, color, bg_color)
    if block <= 0 or maximum <= 0:
        return
    block_width = max(3, min(rect.width, int((block / maximum) * rect.width)))
    block_rect = pygame.Rect(rect.right - block_width, rect.top, block_width, rect.height)
    overlay = pygame.Surface((block_rect.width, block_rect.height), pygame.SRCALPHA)
    pygame.draw.rect(overlay, (255, 214, 82, 150), overlay.get_rect(), border_radius=8)
    surface.blit(overlay, block_rect.topleft)
    pygame.draw.rect(surface, GOLD, block_rect, width=2, border_radius=8)


# ---------------------------------------------------------------------------
# Procedural artwork
# ---------------------------------------------------------------------------

# A handful of fixed "random" points so the background is the same every
# frame (deterministic) without needing to store state on the Game object.
_STAR_FIELD = [
    (int((37 * i * 97 + 13) % WIDTH), int((17 * i * 53 + 7) % 430), (i * 29) % 3 + 1)
    for i in range(70)
]

# Faint drifting +/-/x/div/number glyphs flanking the tower, kept out of its
# silhouette (roughly x 470-720) so they read as ambient set-dressing.
_MATH_MOTIFS = [
    ("deco_plus", (120, 320, 30)), ("deco_minus", (980, 260, 26)),
    ("deco_multiply", (200, 460, 24)), ("deco_divide", (1080, 440, 28)),
    ("deco_numbers", (60, 200, 22)), ("deco_multiply", (1150, 560, 20)),
]


def _lerp_color(a, b, t):
    # Lerps every channel present (3 for opaque RGB, 4 when an alpha
    # channel is included, e.g. draw_center_panel's panel_alpha) so a
    # translucent color pair doesn't silently get flattened back to opaque.
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def draw_spire_background(surface, width=WIDTH, height=HEIGHT):
    # Vertical night-sky gradient (deep indigo -> near-black horizon).
    sky_top = (12, 14, 30)
    sky_bottom = (24, 20, 30)
    band = max(1, height // 3)
    for i in range(0, band, 2):
        t = i / band
        pygame.draw.line(surface, _lerp_color(sky_top, sky_bottom, t), (0, i), (width, i), 2)
    surface.fill(sky_bottom, pygame.Rect(0, band, width, height - band))

    for sx, sy, r in _STAR_FIELD:
        if sy < band + 40:
            shade = 180 + (r * 25)
            pygame.draw.circle(surface, (shade, shade, min(255, shade + 20)), (sx, sy), r)

    # Moon with a soft glow halo.
    moon_glow = pygame.Surface((260, 260), pygame.SRCALPHA)
    pygame.draw.circle(moon_glow, (210, 210, 235, 55), (130, 130), 130)
    pygame.draw.circle(moon_glow, (220, 220, 240, 90), (130, 130), 80)
    surface.blit(moon_glow, (940, -20))
    pygame.draw.circle(surface, (232, 232, 224), (1075, 100), 46)
    pygame.draw.circle(surface, (205, 205, 200), (1090, 88), 8)
    pygame.draw.circle(surface, (205, 205, 200), (1062, 112), 5)

    # Distant jagged mountain silhouettes (parallax layers).
    far_peaks = [(0, 420)] + [(x, 420 - (60 if x % 240 < 120 else 20) - (x % 90)) for x in range(0, width + 1, 60)] + [(width, 420)]
    pygame.draw.polygon(surface, (18, 20, 34), far_peaks)
    near_peaks = [(0, 460)] + [(x, 460 - (90 if (x + 40) % 260 < 130 else 30) - (x % 70)) for x in range(0, width + 1, 70)] + [(width, 460)]
    pygame.draw.polygon(surface, (14, 15, 26), near_peaks)

    # The Spire itself: prefer the real illustrated tower (assets/art/) when
    # present, drawn ground-anchored and centered; otherwise fall back to the
    # tall, tapered procedural fortress tower with buttresses, glowing
    # windows and a torch-lit parapet.
    tower_art = get_art("math_meme_spire_bg", min(height - 60, 640))
    if tower_art is not None:
        aw, ah = tower_art.get_size()
        tower = pygame.Rect(width // 2 - aw // 2, height - 40 - ah, aw, ah)
        ground_glow = pygame.Surface((aw + 120, 70), pygame.SRCALPHA)
        pygame.draw.ellipse(ground_glow, (255, 190, 110, 45), ground_glow.get_rect())
        surface.blit(ground_glow, (tower.centerx - (aw + 120) // 2, tower.bottom - 30))
        surface.blit(tower_art, tower.topleft)
    else:
        tower_top_y = 95
        tower = pygame.Rect(560, 150, 160, 520)
        pygame.draw.polygon(
            surface, (30, 33, 46),
            [(tower.centerx, tower_top_y), (tower.right + 70, height), (tower.left - 70, height)],
        )
        # Tapered stone body (a few stacked trapezoid segments, narrower near the top).
        segments = 6
        for i in range(segments):
            t0, t1 = i / segments, (i + 1) / segments
            top_w = tower.width * (0.55 + 0.45 * t0)
            bot_w = tower.width * (0.55 + 0.45 * t1)
            y0 = tower.top + t0 * tower.height
            y1 = tower.top + t1 * tower.height
            shade = 60 - i * 3
            poly = [
                (tower.centerx - top_w / 2, y0), (tower.centerx + top_w / 2, y0),
                (tower.centerx + bot_w / 2, y1), (tower.centerx - bot_w / 2, y1),
            ]
            pygame.draw.polygon(surface, (shade + 10, shade + 22, shade + 34), poly)
            pygame.draw.polygon(surface, (shade - 6, shade + 4, shade + 14), poly, width=2)
        # Glowing arched windows.
        for row, offset in enumerate(range(0, 460, 78)):
            y = tower.top + 60 + offset
            span = tower.width * (0.55 + 0.45 * (offset / 460))
            wx = tower.centerx - span * 0.18
            glow = pygame.Surface((40, 46), pygame.SRCALPHA)
            pygame.draw.ellipse(glow, (255, 196, 110, 70), (0, 0, 40, 46))
            surface.blit(glow, (wx - 12, y - 8))
            pygame.draw.rect(surface, (255, 214, 140), pygame.Rect(wx, y, 16, 28), border_radius=8)
            pygame.draw.rect(surface, (120, 70, 30), pygame.Rect(wx, y, 16, 28), width=2, border_radius=8)
        # Crenellations + twin torches at the peak.
        for cx in range(tower.centerx - 60, tower.centerx + 61, 24):
            pygame.draw.rect(surface, (48, 54, 66), pygame.Rect(cx, tower.top - 14, 14, 20))
        for tx in (tower.centerx - 74, tower.centerx + 60):
            flame = pygame.Surface((30, 40), pygame.SRCALPHA)
            pygame.draw.circle(flame, (255, 150, 60, 130), (15, 20), 15)
            pygame.draw.circle(flame, (255, 200, 90, 200), (15, 20), 7)
            surface.blit(flame, (tx - 15, tower.top - 42))
            pygame.draw.rect(surface, (70, 60, 55), pygame.Rect(tx - 3, tower.top - 6, 6, 22))

    # Cool ambient glow + ground mist.
    glow = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.circle(glow, (95, 120, 220, 45), (650, 260), 260)
    surface.blit(glow, (0, 0))
    mist = pygame.Surface((width, 90), pygame.SRCALPHA)
    for i in range(90):
        alpha = int(70 * (1 - i / 90))
        pygame.draw.line(mist, (60, 66, 80, alpha), (0, i), (width, i))
    surface.blit(mist, (0, height - 90))

    # A handful of faint drifting math-symbol motifs, flanking the tower —
    # a small thematic touch ("math meme" spire) without cluttering the scene.
    for sym, (fx, fy, fsize) in _MATH_MOTIFS:
        if fx < width and fy < height:
            blit_icon(surface, sym, (fx, fy), fsize, alpha=46)


# Combat backdrops — three distinct "acts" of the Spire as you climb, purely
# atmospheric (behind the HUD panels), separate from the menu/title tower art.
# Drop a PNG named assets/art/combat_backdrop_act<N>.png in and it's used
# automatically in place of the procedural fallback below.
COMBAT_ACT_THEMES = {
    1: {  # Floors 1-2: Lower Spire — torchlit stone catacombs
        "sky_top": (20, 16, 18), "sky_bottom": (34, 24, 22),
        "peak_far": (28, 20, 20), "peak_near": (20, 14, 14),
        "accent": (255, 140, 60), "art": "combat_backdrop_act1",
    },
    2: {  # Floors 3-5: Mid Spire — arcane library / observatory halls
        "sky_top": (14, 16, 32), "sky_bottom": (24, 22, 36),
        "peak_far": (20, 22, 38), "peak_near": (14, 16, 28),
        "accent": (140, 130, 255), "art": "combat_backdrop_act2",
    },
    3: {  # Floors 6-7: Upper Spire — cosmic summit
        "sky_top": (8, 8, 22), "sky_bottom": (18, 12, 30),
        "peak_far": (16, 12, 30), "peak_near": (10, 8, 20),
        "accent": (170, 100, 255), "art": "combat_backdrop_act3",
    },
}


def combat_act(floor, act=None):
    """Return the explicit run act, with legacy floor fallback."""
    if act in COMBAT_ACT_THEMES:
        return act
    if floor <= 2:
        return 1
    if floor <= 5:
        return 2
    return 3


def draw_combat_backdrop(game, width=WIDTH, height=HEIGHT):
    act = combat_act(getattr(game, "floor", 1), getattr(game, "act", None))
    theme = COMBAT_ACT_THEMES[act]

    bg = ASSET_MANAGER.get_background("combat")
    if bg is not None:
        screen.blit(bg, (0, 0))
        return

    art = get_art(theme["art"], height)
    if art is not None:
        aw, ah = art.get_size()
        if aw < width:
            scale = width / aw
            art = pygame.transform.smoothscale(art, (width, int(ah * scale)))
            aw, ah = art.get_size()
        screen.blit(art, ((width - aw) // 2, height - ah))
        return

    sky_top, sky_bottom = theme["sky_top"], theme["sky_bottom"]
    band = max(1, height // 3)
    for i in range(0, band, 2):
        t = i / band
        pygame.draw.line(screen, _lerp_color(sky_top, sky_bottom, t), (0, i), (width, i), 2)
    screen.fill(sky_bottom, pygame.Rect(0, band, width, height - band))

    for sx, sy, r in _STAR_FIELD:
        if sy < band + 40:
            shade = 150 + r * 20
            pygame.draw.circle(screen, (shade, shade, min(255, shade + 25)), (sx, sy), r)

    far_peaks = [(0, 420)] + [(x, 420 - (60 if x % 240 < 120 else 20) - (x % 90)) for x in range(0, width + 1, 60)] + [(width, 420)]
    pygame.draw.polygon(screen, theme["peak_far"], far_peaks)
    near_peaks = [(0, 460)] + [(x, 460 - (90 if (x + 40) % 260 < 130 else 30) - (x % 70)) for x in range(0, width + 1, 70)] + [(width, 460)]
    pygame.draw.polygon(screen, theme["peak_near"], near_peaks)

    glow = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.circle(glow, (*theme["accent"], 40), (width // 2, 260), 280)
    screen.blit(glow, (0, 0))

    mist_color = tuple(c // 3 for c in theme["peak_near"])
    mist = pygame.Surface((width, 90), pygame.SRCALPHA)
    for i in range(90):
        alpha = int(70 * (1 - i / 90))
        pygame.draw.line(mist, (*mist_color, alpha), (0, i), (width, i))
    screen.blit(mist, (0, height - 90))

    if act == 1:
        # Squat stone columns flanking the arena, with flickering torches.
        for cx in (90, width - 90):
            pygame.draw.rect(screen, (50, 42, 38), pygame.Rect(cx - 16, height - 140, 32, 140))
            pygame.draw.circle(screen, (50, 42, 38), (cx, height - 140), 20)
        for tx in (90, width - 90):
            flame = pygame.Surface((26, 34), pygame.SRCALPHA)
            pygame.draw.circle(flame, (255, 150, 60, 150), (13, 17), 13)
            pygame.draw.circle(flame, (255, 200, 90, 210), (13, 17), 6)
            screen.blit(flame, (tx - 13, height - 170))
    elif act == 2:
        # Faint drifting equation glyphs, like the title/menu tower's motif.
        for sym, (fx, fy, fsize) in _MATH_MOTIFS:
            if fx < width and fy < height * 0.6:
                blit_icon(screen, sym, (fx, fy + 40), fsize, alpha=70)
    else:
        # A cosmic ring glowing near the top of the frame.
        ring = pygame.Surface((360, 360), pygame.SRCALPHA)
        for rr in range(40, 170, 24):
            pygame.draw.circle(ring, (*theme["accent"], 30), (180, 180), rr, 3)
        screen.blit(ring, (width // 2 - 180, -60))


# ---------------------------------------------------------------------------
# High-fidelity sprite rendering: every portrait is drawn once at ~3x
# resolution onto its own surface, smooth-downscaled for anti-aliased edges
# and soft shading, then cached — so the expensive part happens once per
# (shape, variant, scale) rather than every frame.
# ---------------------------------------------------------------------------

_SPRITE_CACHE = {}
_SPRITE_CANVAS = 260
_SPRITE_PAD = 50
_SPRITE_SUPERSAMPLE = 3


def _rounded_gradient_body(surface, rect, top_color, bottom_color, border_radius=0, steps=24):
    """A vertical gradient fill clipped to a rounded rect — gives sprites soft
    shaded volume instead of flat single-color shapes."""
    mask = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, *rect.size), border_radius=border_radius)
    grad = pygame.Surface(rect.size, pygame.SRCALPHA)
    for i in range(steps):
        t0 = i / steps
        y0 = int(t0 * rect.height)
        y1 = int((i + 1) / steps * rect.height)
        pygame.draw.rect(grad, _lerp_color(top_color, bottom_color, t0), pygame.Rect(0, y0, rect.width, max(1, y1 - y0)))
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surface.blit(grad, rect.topleft)


def _supersample_sprite(surface, shape_fn, x, y, scale, variant=None):
    key = (shape_fn, variant, round(scale, 3))
    cached = _SPRITE_CACHE.get(key)
    if cached is None:
        size = max(2, int((_SPRITE_CANVAS + 2 * _SPRITE_PAD) * scale))
        big_size = max(2, size * _SPRITE_SUPERSAMPLE)
        big = pygame.Surface((big_size, big_size), pygame.SRCALPHA)
        sx = scale * _SPRITE_SUPERSAMPLE
        ox = oy = _SPRITE_PAD * sx
        if variant is None:
            shape_fn(big, ox, oy, sx)
        else:
            shape_fn(big, variant, ox, oy, sx)
        scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
        cached = scaler(big, (size, size))
        _SPRITE_CACHE[key] = cached
    surface.blit(cached, (x - _SPRITE_PAD * scale, y - _SPRITE_PAD * scale))


def _math_warrior_shapes(surface, x, y, sx):
    # Cloak/legs shadow
    pygame.draw.ellipse(surface, (10, 10, 14), pygame.Rect(x + 4 * sx, y + 128 * sx, 56 * sx, 16 * sx))
    # Head
    pygame.draw.circle(surface, (200, 186, 148), (x + 30 * sx, y + 18 * sx), 18 * sx)
    pygame.draw.circle(surface, (230, 214, 170), (x + 27 * sx, y + 15 * sx), 15 * sx)
    pygame.draw.arc(surface, (90, 70, 50), pygame.Rect(x + 12 * sx, y + 2 * sx, 36 * sx, 30 * sx), 3.4, 6.0, max(1, int(3 * sx / 2)))
    # Torso armor (gradient steel)
    torso = pygame.Rect(int(x + 12 * sx), int(y + 38 * sx), int(48 * sx), int(80 * sx))
    _rounded_gradient_body(surface, torso, (86, 92, 104), (44, 48, 58), border_radius=int(14 * sx))
    pygame.draw.rect(surface, (30, 32, 40), torso, width=max(1, int(sx)), border_radius=int(14 * sx))
    pygame.draw.line(surface, (255, 214, 120), (torso.centerx, torso.top + 6 * sx), (torso.centerx, torso.bottom - 10 * sx), max(1, int(2 * sx)))
    # Legs / arms
    pygame.draw.rect(surface, (255, 214, 102), pygame.Rect(x + 54 * sx, y + 48 * sx, 18 * sx, 90 * sx), border_radius=int(6 * sx))
    pygame.draw.rect(surface, (76, 158, 255), pygame.Rect(x - 12 * sx, y + 60 * sx, 18 * sx, 78 * sx), border_radius=int(6 * sx))
    pygame.draw.rect(surface, (120, 120, 130), pygame.Rect(x + 42 * sx, y + 60 * sx, 18 * sx, 80 * sx), border_radius=int(6 * sx))
    # Pencil-sword with rim highlight
    blade = pygame.Rect(int(x + 74 * sx), int(y + 58 * sx), int(70 * sx), int(12 * sx))
    pygame.draw.rect(surface, (235, 132, 95), blade, border_radius=int(6 * sx))
    pygame.draw.rect(surface, (255, 200, 170), pygame.Rect(blade.x, blade.y, blade.width, max(1, int(3 * sx))), border_radius=int(3 * sx))
    pygame.draw.polygon(surface, (60, 60, 60), [
        (x + 144 * sx, y + 58 * sx), (x + 160 * sx, y + 64 * sx), (x + 144 * sx, y + 70 * sx),
    ])
    pygame.draw.circle(surface, (255, 210, 100), (x + 110 * sx, y + 64 * sx), 8 * sx)
    # Calculator shield with screen glow
    shield = pygame.Rect(int(x + 14 * sx), int(y + 46 * sx), int(28 * sx), int(56 * sx))
    _rounded_gradient_body(surface, shield, (225, 225, 228), (170, 174, 182), border_radius=int(8 * sx))
    pygame.draw.rect(surface, (50, 54, 60), shield, width=max(1, int(sx)), border_radius=int(8 * sx))
    screen_glow = pygame.Surface((int(20 * sx), int(12 * sx)), pygame.SRCALPHA)
    pygame.draw.rect(screen_glow, (90, 255, 170, 220), pygame.Rect(0, 0, int(20 * sx), int(12 * sx)), border_radius=int(2 * sx))
    surface.blit(screen_glow, (shield.x + 4 * sx, shield.y + 4 * sx))
    for gy in range(3):
        for gx in range(3):
            pygame.draw.rect(
                surface, (70, 70, 80),
                pygame.Rect(shield.x + 4 * sx + gx * 7 * sx, shield.y + 18 * sx + gy * 10 * sx, 5 * sx, 6 * sx),
            )


def _wizard_shapes(surface, x, y, sx):
    pygame.draw.ellipse(surface, (10, 10, 14), pygame.Rect(x + 2 * sx, y + 130 * sx, 60 * sx, 16 * sx))
    # Robe (gradient) + head
    robe = pygame.Rect(int(x + 8 * sx), int(y + 34 * sx), int(60 * sx), int(96 * sx))
    _rounded_gradient_body(surface, robe, (132, 100, 210), (72, 52, 130), border_radius=int(20 * sx))
    pygame.draw.rect(surface, (48, 34, 90), robe, width=max(1, int(sx)), border_radius=int(20 * sx))
    pygame.draw.circle(surface, (240, 220, 180), (x + 32 * sx, y + 18 * sx), 18 * sx)
    pygame.draw.arc(surface, (70, 50, 110), pygame.Rect(x + 10 * sx, y - 2 * sx, 44 * sx, 30 * sx), 3.3, 6.1, max(1, int(3 * sx)))
    # Wizard hat
    pygame.draw.polygon(surface, (98, 56, 155), [(x + 8 * sx, y + 4 * sx), (x + 60 * sx, y - 34 * sx), (x + 68 * sx, y + 10 * sx)])
    pygame.draw.circle(surface, (254, 223, 102), (x + 62 * sx, y - 30 * sx), 5 * sx)
    # Staff with a glowing d20-ish gem
    pygame.draw.rect(surface, (120, 84, 46), pygame.Rect(x + 60 * sx, y + 30 * sx, 6 * sx, 100 * sx), border_radius=int(3 * sx))
    gem_glow = pygame.Surface((int(46 * sx), int(46 * sx)), pygame.SRCALPHA)
    pygame.draw.circle(gem_glow, (120, 255, 210, 90), (int(23 * sx), int(23 * sx)), int(23 * sx))
    surface.blit(gem_glow, (x + 96 * sx, y + 58 * sx))
    pygame.draw.polygon(surface, (254, 223, 102), [(x + 62 * sx, y + 68 * sx), (x + 122 * sx, y + 82 * sx), (x + 62 * sx, y + 120 * sx)])
    pygame.draw.circle(surface, (82, 255, 200), (x + 118 * sx, y + 82 * sx), 10 * sx)
    for i in range(6):
        angle = i * math.pi / 3
        px = x + 118 * sx + int(10 * sx * math.cos(angle))
        py = y + 82 * sx + int(10 * sx * math.sin(angle))
        pygame.draw.line(surface, (30, 60, 55), (x + 118 * sx, y + 82 * sx), (px, py), max(1, int(sx / 2)))
    # Floating probability sparks
    for dx, dy, r in ((-10, 34, 3), (2, 14, 2), (-18, 60, 2)):
        pygame.draw.circle(surface, (255, 255, 255), (x + dx * sx, y + dy * sx), r * sx)


ENEMY_ACCENT_RULES = [
    (("chalk", "mathematician", "titan"), "deco_book"),
    (("dice", "zero", "anarchist"), "dice"),
    (("oracle", "brain", "leech"), "enemy_brain"),
    (("recursive", "wraith", "loop"), "enemy_ghost"),
    (("engine", "ogre"), "enemy_robot"),
]


def _enemy_shapes(surface, enemy_name, x, y, sx):
    name = enemy_name.lower()
    accent_icon = None
    for keys, icon in ENEMY_ACCENT_RULES:
        if any(k in name for k in keys):
            accent_icon = icon
            break

    if "chalk" in name or "mathematician" in name or "titan" in name:
        body = pygame.Rect(int(x), int(y), int(140 * sx), int(140 * sx))
        _rounded_gradient_body(surface, body, (76, 80, 88), (42, 45, 52), border_radius=int(18 * sx))
        pygame.draw.rect(surface, (200, 204, 228), pygame.Rect(x + 12 * sx, y + 20 * sx, 116 * sx, 78 * sx), border_radius=int(10 * sx))
        for ly in (52, 68, 84):
            pygame.draw.line(surface, (60, 60, 70), (x + 24 * sx, y + ly * sx), (x + 116 * sx, y + ly * sx), max(1, int(3 * sx / 2)))
        pygame.draw.circle(surface, (90, 90, 96), (x + 66 * sx, y + 120 * sx), 16 * sx)
        pygame.draw.circle(surface, (255, 90, 90), (x + 40 * sx, y + 118 * sx), 6 * sx)
        pygame.draw.circle(surface, (255, 90, 90), (x + 92 * sx, y + 118 * sx), 6 * sx)
    elif "dice" in name or "zero" in name or "anarchist" in name:
        cx, cy, r = x + 70 * sx, y + 70 * sx, 52 * sx
        glow = pygame.Surface((int(r * 2.6), int(r * 2.6)), pygame.SRCALPHA)
        pygame.draw.circle(glow, (255, 200, 90, 70), (int(r * 1.3), int(r * 1.3)), int(r * 1.3))
        surface.blit(glow, (cx - r * 1.3, cy - r * 1.3))
        body_rect = pygame.Rect(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        _rounded_gradient_body(surface, body_rect, (255, 210, 110), (200, 130, 30), border_radius=int(r * 0.3))
        pygame.draw.rect(surface, (110, 60, 10), body_rect, width=max(1, int(sx)), border_radius=int(r * 0.3))
        for i in range(6):
            angle = i * math.pi / 3
            px = cx + int(r * 0.72 * math.cos(angle))
            py = cy + int(r * 0.72 * math.sin(angle))
            pygame.draw.circle(surface, (25, 15, 5), (px, py), 6 * sx)
        pygame.draw.circle(surface, (25, 15, 5), (cx, cy), 10 * sx)
    elif "oracle" in name or "brain" in name or "leech" in name:
        cx, cy, r = x + 70 * sx, y + 70 * sx, 52 * sx
        body_rect = pygame.Rect(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        _rounded_gradient_body(surface, body_rect, (198, 160, 255), (110, 74, 190), border_radius=int(r))
        for ridge in range(4):
            pygame.draw.arc(surface, (60, 40, 100), pygame.Rect(x + 38 * sx + ridge * 12 * sx, y + 40 * sx, 32 * sx, 52 * sx), 0.5, 2.7, max(1, int(3 * sx / 2)))
        pygame.draw.circle(surface, (255, 240, 255), (cx, cy), 8 * sx)
    elif "recursive" in name or "wraith" in name or "loop" in name:
        for i in range(5):
            r = 50 - i * 8
            alpha_layer = pygame.Surface((int(r * 2 * sx), int(r * 2 * sx)), pygame.SRCALPHA)
            pygame.draw.circle(alpha_layer, (100 + i * 22, 80, 160 + i * 12, 210 - i * 25), (int(r * sx), int(r * sx)), int(r * sx))
            surface.blit(alpha_layer, (x + 70 * sx - r * sx, y + 70 * sx - r * sx))
        pygame.draw.circle(surface, (240, 235, 255), (x + 60 * sx, y + 62 * sx), 4 * sx)
        pygame.draw.circle(surface, (240, 235, 255), (x + 80 * sx, y + 62 * sx), 4 * sx)
    elif "rounding" in name or "error" in name:
        body = pygame.Rect(int(x + 10 * sx), int(y + 10 * sx), int(120 * sx), int(120 * sx))
        _rounded_gradient_body(surface, body, (235, 120, 120), (170, 55, 55), border_radius=int(30 * sx))
        pygame.draw.line(surface, (30, 20, 20), (x + 40 * sx, y + 40 * sx), (x + 100 * sx, y + 100 * sx), max(1, int(6 * sx)))
        pygame.draw.line(surface, (30, 20, 20), (x + 100 * sx, y + 40 * sx), (x + 40 * sx, y + 100 * sx), max(1, int(6 * sx)))
    elif "engine" in name or "ogre" in name:
        body = pygame.Rect(int(x), int(y + 20 * sx), int(140 * sx), int(110 * sx))
        _rounded_gradient_body(surface, body, (130, 82, 76), (68, 44, 42), border_radius=int(14 * sx))
        for ex in (45, 95):
            eye_glow = pygame.Surface((int(30 * sx), int(30 * sx)), pygame.SRCALPHA)
            pygame.draw.circle(eye_glow, (255, 170, 70, 130), (int(15 * sx), int(15 * sx)), int(15 * sx))
            surface.blit(eye_glow, (x + ex * sx - 15 * sx, y + 65 * sx - 15 * sx))
            pygame.draw.circle(surface, (255, 150, 60), (x + ex * sx, y + 65 * sx), 12 * sx)
        pygame.draw.rect(surface, (35, 35, 35), pygame.Rect(x + 40 * sx, y + 95 * sx, 60 * sx, 16 * sx), border_radius=int(6 * sx))
    else:
        body = pygame.Rect(int(x), int(y), int(140 * sx), int(140 * sx))
        _rounded_gradient_body(surface, body, (225, 145, 130), (165, 85, 75), border_radius=int(70 * sx))
        pygame.draw.circle(surface, (20, 20, 20), (x + 52 * sx, y + 58 * sx), 10 * sx)
        pygame.draw.circle(surface, (20, 20, 20), (x + 88 * sx, y + 58 * sx), 10 * sx)
        pygame.draw.arc(surface, (20, 20, 20), pygame.Rect(x + 42 * sx, y + 78 * sx, 54 * sx, 22 * sx), math.pi, 0, max(1, int(4 * sx)))

    if accent_icon:
        blit_icon(surface, accent_icon, (int(x + 20 * sx), int(y + 20 * sx)), max(8, int(26 * sx)))


BOSS_ACCENT_RULES = [
    (("dragon",), "boss_dragon"),
    (("colossus",), "boss_colossus"),
    (("null hypothesis", "singularity"), "boss_cyclone"),
    (("fractal", "empress"), "power_sparkles"),
]


def _boss_shapes(surface, boss_name, x, y, sx):
    name = boss_name.lower()
    accent_icon = None
    for keys, icon in BOSS_ACCENT_RULES:
        if any(k in name for k in keys):
            accent_icon = icon
            break

    # Ominous ground shadow + backlight for every boss.
    pygame.draw.ellipse(surface, (0, 0, 0), pygame.Rect(x - 10 * sx, y + 160 * sx, 190 * sx, 24 * sx))
    aura = pygame.Surface((int(220 * sx), int(220 * sx)), pygame.SRCALPHA)
    pygame.draw.circle(aura, (200, 80, 90, 60), (int(110 * sx), int(110 * sx)), int(110 * sx))
    surface.blit(aura, (x - 30 * sx, y - 30 * sx))

    if "colossus" in name:
        body = pygame.Rect(int(x), int(y), int(160 * sx), int(170 * sx))
        _rounded_gradient_body(surface, body, (160, 160, 172), (90, 90, 100), border_radius=int(20 * sx))
        pygame.draw.rect(surface, (190, 190, 200), pygame.Rect(x + 20 * sx, y + 20 * sx, 120 * sx, 60 * sx), border_radius=int(10 * sx))
        for i in range(3):
            pygame.draw.line(surface, (70, 70, 78), (x + 20 * sx, y + (100 + i * 18) * sx), (x + 140 * sx, y + (100 + i * 18) * sx), max(1, int(3 * sx)))
        glow = pygame.Surface((int(24 * sx), int(24 * sx)), pygame.SRCALPHA)
        pygame.draw.circle(glow, (255, 140, 90, 200), (int(12 * sx), int(12 * sx)), int(12 * sx))
        surface.blit(glow, (x + 68 * sx, y + 38 * sx))
    elif "null hypothesis" in name or "singularity" in name:
        cx, cy = x + 80 * sx, y + 85 * sx
        pygame.draw.circle(surface, (8, 8, 14), (cx, cy), 60 * sx)
        for r in range(4):
            ring = pygame.Surface((int((70 + r * 18) * 2 * sx), int((70 + r * 18) * 2 * sx)), pygame.SRCALPHA)
            pygame.draw.circle(ring, (150, 100, 230, 160 - r * 30), (ring.get_width() // 2, ring.get_height() // 2), int((70 + r * 18) * sx), max(1, int(2 * sx)))
            surface.blit(ring, (cx - ring.get_width() / 2, cy - ring.get_height() / 2))
        pygame.draw.circle(surface, (230, 210, 255), (cx, cy), 14 * sx)
    elif "dragon" in name:
        body = pygame.Rect(int(x + 10 * sx), int(y + 40 * sx), int(140 * sx), int(100 * sx))
        _rounded_gradient_body(surface, body, (100, 200, 140), (40, 100, 70), border_radius=int(40 * sx))
        pygame.draw.polygon(surface, (40, 120, 80), [(x + 20 * sx, y + 50 * sx), (x - 24 * sx, y + 6 * sx), (x + 42 * sx, y + 28 * sx)])
        pygame.draw.polygon(surface, (40, 120, 80), [(x + 130 * sx, y + 50 * sx), (x + 184 * sx, y + 6 * sx), (x + 118 * sx, y + 28 * sx)])
        for ex in (60, 100):
            glow = pygame.Surface((int(20 * sx), int(20 * sx)), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 230, 110, 200), (int(10 * sx), int(10 * sx)), int(10 * sx))
            surface.blit(glow, (x + ex * sx - 10 * sx, y + 75 * sx - 10 * sx))
    elif "abacus" in name:
        body = pygame.Rect(int(x + 10 * sx), int(y + 30 * sx), int(140 * sx), int(110 * sx))
        _rounded_gradient_body(surface, body, (160, 110, 60), (90, 58, 26), border_radius=int(10 * sx))
        for row in range(4):
            yy = y + (48 + row * 22) * sx
            pygame.draw.line(surface, (60, 40, 20), (x + 20 * sx, yy), (x + 140 * sx, yy), max(1, int(2 * sx)))
            pygame.draw.circle(surface, (255, 200, 90), (x + (30 + row * 20) * sx, yy), 8 * sx)
        pygame.draw.polygon(surface, (255, 210, 90), [(x + 60 * sx, y + 10 * sx), (x + 100 * sx, y + 10 * sx), (x + 80 * sx, y - 14 * sx)])
    elif "prime minister" in name:
        body = pygame.Rect(int(x + 20 * sx), int(y + 30 * sx), int(100 * sx), int(120 * sx))
        _rounded_gradient_body(surface, body, (200, 50, 60), (120, 20, 30), border_radius=int(14 * sx))
        pygame.draw.circle(surface, (230, 200, 180), (x + 70 * sx, y + 20 * sx), 22 * sx)
        pygame.draw.rect(surface, (20, 20, 20), pygame.Rect(x + 55 * sx, y, 30 * sx, 14 * sx))
    elif "fractal" in name or "empress" in name:
        def spiral(cx, cy, depth, r):
            if depth <= 0 or r < 4:
                return
            ring = pygame.Surface((int(r * 2 * sx), int(r * 2 * sx)), pygame.SRCALPHA)
            pygame.draw.circle(ring, (170 + depth * 12, 110, 220, 220), (int(r * sx), int(r * sx)), int(r * sx), max(1, int(2 * sx)))
            surface.blit(ring, (cx - r * sx, cy - r * sx))
            spiral(cx + int(r * 0.6 * sx), cy, depth - 1, r * 0.55)
        spiral(x + 70 * sx, y + 85 * sx, 5, 60)
    else:
        body = pygame.Rect(int(x), int(y + 10 * sx), int(150 * sx), int(140 * sx))
        _rounded_gradient_body(surface, body, (190, 100, 230), (110, 40, 150), border_radius=int(70 * sx))
        pygame.draw.circle(surface, (255, 255, 255), (x + 55 * sx, y + 65 * sx), 8 * sx)
        pygame.draw.circle(surface, (255, 255, 255), (x + 95 * sx, y + 65 * sx), 8 * sx)

    if accent_icon:
        blit_icon(surface, accent_icon, (int(x + 130 * sx), int(y + 16 * sx)), max(10, int(30 * sx)))


def draw_math_warrior_sprite(surface, x, y, scale=1, state="ready"):
    """``state`` is one of HERO_STATES ("select" / "ready" / "powerup") --
    see AssetManager.get_hero_sprite() for the per-state -> base-portrait ->
    procedural fallback chain."""
    max_height = int(190 * scale)
    img = ASSET_MANAGER.get_hero_sprite("Math Warrior", state, max_height)
    if img is not None:
        draw_real_art_image(surface, img, x, y, scale)
        return
    _supersample_sprite(surface, _math_warrior_shapes, x, y, scale)


def draw_probability_wizard_sprite(surface, x, y, scale=1, state="ready"):
    max_height = int(190 * scale)
    img = ASSET_MANAGER.get_hero_sprite("Probability Wizard", state, max_height)
    if img is not None:
        draw_real_art_image(surface, img, x, y, scale)
        return
    _supersample_sprite(surface, _wizard_shapes, x, y, scale)


def draw_enemy_portrait(surface, enemy_name, x, y, scale=1):
    # Try the newer assets/monsters/<file>.png convention first (this is
    # where the real cropped monster art lives), then the older flat
    # assets/art/ convention (ENEMY_ART), then the procedural shapes.
    # AssetManager.get_monster_sprite() already chains those first two
    # lookups internally, so this is a single call.
    max_height = int(190 * scale)
    img = ASSET_MANAGER.get_monster_sprite(enemy_name, max_height)
    if img is not None:
        draw_real_art_image(surface, img, x, y, scale)
        return
    _supersample_sprite(surface, _enemy_shapes, x, y, scale, variant=enemy_name)


def draw_boss_portrait(surface, boss_name, x, y, scale=1):
    max_height = int(190 * scale)
    img = ASSET_MANAGER.get_monster_sprite(boss_name, max_height)
    if img is not None:
        draw_real_art_image(surface, img, x, y, scale)
        return
    _supersample_sprite(surface, _boss_shapes, x, y, scale, variant=boss_name)


CARD_ART_PALETTE = {
    "Strike": (104, 160, 255), "Defend": (95, 206, 125), "Arcane Dart": (180, 120, 255),
    "Reckless Roll": (255, 168, 82), "Probability Shield": (255, 210, 90), "Chaos Bloom": (237, 101, 101),
    "Weighted Step": (255, 191, 98), "Null Burst": (140, 163, 255), "Glass Guard": (110, 255, 190),
    "Dice Slash": (255, 187, 86), "Dice Burst": (192, 130, 255), "Standard Deviation": (120, 200, 255),
    "Confidence Interval": (120, 230, 170), "Bayesian Update": (255, 235, 150),
    "Integral Strike": (255, 130, 130), "Factorial Fury": (255, 100, 100),
    "Central Limit Theorem": (200, 150, 255), "Law of Large Numbers": (150, 210, 255),
}


def _base_card_name(card_name):
    return CARD_LIBRARY[card_name].get("base", card_name)


def card_upgrade_deltas(base_card_name):
    """Compares a base card's desc text against its upgraded desc text and
    returns a list of (original, upgraded, delta) for every number that
    increased, in reading order -- e.g. "Deal 8 damage." -> "Deal 12
    damage." gives [(8, 12, 4)]. Used by the rest-site upgrade picker so
    forging a card shows exactly how much it gains instead of just the
    upgraded card's raw description. Numbers that are identical between the
    two descriptions (like an unchanged "1d6" die size) are skipped, and if
    the two descriptions don't have the same count of numbers (a rare
    phrasing change, e.g. "1 = ..." becoming "1-2 = ...") this returns an
    empty list rather than guessing a misaligned pairing."""
    upgraded_name = CARD_LIBRARY[base_card_name].get("upgrades_to")
    if not upgraded_name:
        return []
    base_desc = CARD_LIBRARY[base_card_name]["desc"]
    upgraded_desc = CARD_LIBRARY[upgraded_name]["desc"]
    base_nums = [int(n) for n in re.findall(r"\d+", base_desc)]
    upgraded_nums = [int(n) for n in re.findall(r"\d+", upgraded_desc)]
    if len(base_nums) != len(upgraded_nums):
        return []
    return [(b, u, u - b) for b, u in zip(base_nums, upgraded_nums) if u != b]


def draw_card_art(surface, card_name, body, rarity_color, character=None):
    """Art panel for a card, used at every size render_card() is called at
    (hand, rewards, deck view, shop, relic list). ``body`` here is the
    CONTENT rect (card body minus any reserved math-footer band at the
    bottom -- see render_card), not necessarily the full card frame, so the
    16%/34% art-panel split still lands in the same visual spot whether or
    not this particular card has a footer. Tries a real illustrated
    portrait first, checking CHARACTER_CARD_ART_FILES (keyed by the active
    character, for raw card names re-skinned differently per starting deck)
    before the character-agnostic CARD_ART_FILES, fit inside the panel on
    both axes so a portrait- or landscape-oriented commission never
    overflows it; if nothing matches it falls back to the original
    gradient-shaded panel + big bitmap icon (or a single-letter glyph as
    the last resort) -- the same procedural chain every other card without
    a commission already used, so nothing about their rendering changes."""
    base_name = _base_card_name(card_name)
    tint = CARD_ART_PALETTE.get(base_name, (148, 160, 180))
    art_rect = pygame.Rect(
        body.x + int(body.width * 0.06), body.y + int(body.height * 0.16),
        int(body.width * 0.88), int(body.height * 0.34),
    )
    dark = tuple(max(0, c - 70) for c in tint)
    _rounded_gradient_body(surface, art_rect, tint, dark, border_radius=int(body.width * 0.08))
    pygame.draw.rect(surface, tuple(min(255, c + 40) for c in rarity_color), art_rect,
                      width=max(1, int(body.width * 0.012)), border_radius=int(body.width * 0.08))

    art_file = CHARACTER_CARD_ART_FILES.get(character, {}).get(base_name) or CARD_ART_FILES.get(base_name)
    portrait = ASSET_MANAGER.get_card_art(art_file, art_rect.height, art_rect.width) if art_file else None
    if portrait is not None:
        pr = portrait.get_rect(center=art_rect.center)
        surface.blit(portrait, pr)
        return art_rect

    icon_name = CARD_ICON.get(base_name, ICON_CARD_TYPE.get(CARD_LIBRARY[card_name]["type"]))
    icon_size = int(art_rect.height * 0.82)
    if not blit_icon(surface, icon_name, art_rect.center, icon_size):
        glyph = FONT_H1.render(base_name[:1], True, WHITE)
        gr = glyph.get_rect(center=art_rect.center)
        surface.blit(glyph, gr)
    return art_rect


def draw_card_frame(surface, card_name, rect, hovered=False, selected=False, affordable=True):
    """Card chrome only (background gradient, border, type stripe, glows) --
    no art and no unaffordable-dimming. Both of those now happen in
    render_card() *after* the math footer is drawn, so the dim overlay
    (when present) darkens the footer's numbers along with everything else
    instead of leaving them undimmed on an unaffordable card."""
    card = CARD_LIBRARY[card_name]
    rarity = card.get("rarity", "common")
    type_color = TYPE_COLORS.get(card["type"], MUTED)
    rarity_color = RARITY_COLORS.get(rarity, MUTED)
    radius = int(rect.width * 0.09)

    lift = 6 if hovered else 0
    body = pygame.Rect(rect.x, rect.y - lift, rect.width, rect.height)
    panel_top = CARD if affordable else (30, 32, 38)
    panel_bottom = tuple(max(0, c - 16) for c in panel_top)
    _rounded_gradient_body(surface, body, panel_top, panel_bottom, border_radius=radius)

    is_ultimate = card_name in ULTIMATE_CARD_NAMES
    border_width = 5 if is_ultimate else (3 if (hovered or selected) else 2)
    border_color = GOLD if selected else ((255, 238, 128) if is_ultimate else (WHITE if hovered else rarity_color))
    pygame.draw.rect(surface, border_color, body, width=border_width, border_radius=radius)
    pygame.draw.rect(surface, type_color, pygame.Rect(body.x + 3, body.y + 3, body.width - 6, 6), border_radius=3)

    if is_ultimate:
        glow = pygame.Surface((body.width + 18, body.height + 18), pygame.SRCALPHA)
        pygame.draw.rect(glow, (255, 205, 70, 78), glow.get_rect(), width=7, border_radius=radius + 7)
        surface.blit(glow, (body.x - 9, body.y - 9))

    if hovered and not selected:
        glow = pygame.Surface((body.width + 16, body.height + 16), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*GOLD, 60), glow.get_rect(), width=6, border_radius=radius + 6)
        surface.blit(glow, (body.x - 8, body.y - 8))

    return body


def _card_math_footer_data(game, card_name):
    """(ev, cost_efficiency, recoil_ev) for the on-card math footer, or
    None if this card has no damage E(V) (block/draw/power cards, curses,
    etc. all render without a footer, exactly like the hover tooltip they
    mirror). Mirrors the same three numbers the combat hover tooltip has
    always shown -- this just also bakes them onto the card face."""
    if game is None:
        return None
    ev = card_ev(game, card_name)
    if ev is None:
        return None
    eff = card_cost_efficiency(game, card_name)
    if eff is None:
        cost = CARD_LIBRARY.get(card_name, {}).get("cost", 0)
        eff = (ev / cost) if cost else None
    recoil = card_recoil_ev(game, card_name)
    return (ev, eff, recoil)


def _card_footer_rect(body):
    """Reserved band at the bottom of the FULL card body for the math
    footer. Scales down instead of disappearing at the small sizes
    render_card() is reused at (as low as ~90px tall in the shop/relic
    list): below the ``compact`` threshold it asks for a thinner band and
    draw_card_math_footer() collapses to one abbreviated line to fit it.
    Called both to reserve space (before the description is laid out) and
    to actually draw into (after), so the two never disagree."""
    compact = body.height < 130
    footer_h = max(15, int(body.height * (0.13 if compact else 0.19)))
    return pygame.Rect(body.x + 10, body.bottom - footer_h - 5, body.width - 20, footer_h)


def draw_card_math_footer(surface, body, math_data):
    """Draws the reserved math-footer band computed by _card_footer_rect().
    Two lines (E(V) and cost-efficiency/recoil) at hand/reward sizes; one
    abbreviated line at the small shop/relic-list size, in FONT_MICRO if
    even that doesn't fit FONT_TINY's width."""
    ev, eff, recoil = math_data
    footer = _card_footer_rect(body)
    compact = body.height < 130

    pygame.draw.line(surface, (255, 205, 70, 130), (footer.x, footer.y - 3), (footer.right, footer.y - 3), 1)

    if compact:
        parts = [f"E(V) {ev:.1f}"]
        if eff is not None:
            parts.append(f"{eff:.1f}/E")
        if recoil:
            parts.append(f"-{recoil:.1f}")
        line = " · ".join(parts)
        font = FONT_TINY if FONT_TINY.size(line)[0] <= footer.width else FONT_MICRO
        text = font.render(line, True, GREEN)
        ty = footer.y + max(0, (footer.height - text.get_height()) // 2)
        surface.blit(text, (footer.centerx - text.get_width() // 2, ty))
    else:
        line1 = f"E(V) = {ev:.2f}"
        tail = []
        if eff is not None:
            tail.append(f"{eff:.2f} dmg/E")
        if recoil:
            tail.append(f"-{recoil:.2f} recoil")
        line2 = "  ".join(tail) if tail else None

        t1 = FONT_TINY.render(line1, True, GREEN)
        surface.blit(t1, (footer.centerx - t1.get_width() // 2, footer.y))
        if line2:
            t2 = FONT_TINY.render(line2, True, GREEN)
            if t2.get_width() > footer.width:
                t2 = FONT_MICRO.render(line2, True, GREEN)
            ly = footer.y + t1.get_height()
            surface.blit(t2, (footer.centerx - t2.get_width() // 2, ly))
    return footer


def render_card(surface, card_name, rect, hovered=False, selected=False, affordable=True, character=None, game=None):
    card = CARD_LIBRARY[card_name]
    body = draw_card_frame(surface, card_name, rect, hovered, selected, affordable)
    rarity_color = RARITY_COLORS.get(card.get("rarity", "common"), MUTED)

    # Cost badge (top-left, overlapping the frame corner like Slay the Spire).
    badge_r = max(12, int(body.width * 0.115))
    badge_center = (body.x + badge_r + 4, body.y + badge_r + 2)
    pygame.draw.circle(surface, (16, 14, 20), badge_center, badge_r)
    pygame.draw.circle(surface, GOLD, badge_center, badge_r, max(2, badge_r // 8))
    cost_text = FONT_HEADER.render(str(card["cost"]), True, GOLD)
    ctr = cost_text.get_rect(center=badge_center)
    surface.blit(cost_text, ctr)

    # Small type icon, top-right corner (art panel already carries the big icon).
    type_icon = ICON_CARD_TYPE.get(card["type"])
    blit_icon(surface, type_icon, (body.right - badge_r - 6, body.y + badge_r + 2), badge_r)

    # Math footer (E(V)/cost-efficiency/recoil) is baked permanently onto
    # the card face at every render_card() size, not just in the hover
    # tooltip -- reserve its band FIRST so the art/name/description above
    # it lay out against the shrunk content height instead of overlapping.
    math_data = _card_math_footer_data(game, card_name)
    if math_data is not None:
        footer_rect = _card_footer_rect(body)
        content = pygame.Rect(body.x, body.y, body.width, footer_rect.y - 4 - body.y)
    else:
        footer_rect = None
        content = body

    draw_card_art(surface, card_name, content, rarity_color, character)

    name_y = content.y + int(content.height * 0.52)
    display_name, display_desc = card_presentation(character, card_name) if character else (card_name, card["desc"])
    name_surface = FONT_SMALL.render(display_name, True, TEXT)
    if name_surface.get_size()[0] > body.width - 16:
        name_surface = FONT_TINY.render(card_name, True, TEXT)
    nr = name_surface.get_rect(centerx=body.centerx, y=name_y)
    surface.blit(name_surface, nr)

    divider_y = name_y + name_surface.get_size()[1] + 4
    pygame.draw.line(surface, (255, 255, 255, 40), (body.x + 16, divider_y), (body.right - 16, divider_y), 1)

    desc_lines = wrapped_text(FONT_TINY, display_desc, body.width - 24)
    y = divider_y + 8
    max_y = content.bottom - 6
    for line in render_lines(FONT_TINY, desc_lines[:5], color=MUTED):
        if y + 14 > max_y:
            break
        lr = line.get_rect(centerx=body.centerx, y=y)
        surface.blit(line, lr)
        y += 15

    if math_data is not None:
        draw_card_math_footer(surface, body, math_data)

    if not affordable:
        radius = int(rect.width * 0.09)
        dim = pygame.Surface((body.width, body.height), pygame.SRCALPHA)
        pygame.draw.rect(dim, (10, 10, 14, 140), pygame.Rect(0, 0, body.width, body.height), border_radius=radius)
        surface.blit(dim, (body.x, body.y))


def _shop_item_frame(surface, rect, hovered, affordable, top_color, border_color, accent_color, icon_name, icon_fallback_letter):
    """Shared card-frame chrome for non-card shop items (relics, services) so
    every item on the stall reads as the same 'kind of object' as the cards
    you actually play, just re-skinned per category — matches draw_card_frame
    + draw_card_art's visual language without requiring a CARD_LIBRARY entry."""
    radius = int(rect.width * 0.09)
    lift = 6 if hovered else 0
    body = pygame.Rect(rect.x, rect.y - lift, rect.width, rect.height)
    panel_top = top_color if affordable else (30, 32, 38)
    panel_bottom = tuple(max(0, c - 16) for c in panel_top)
    _rounded_gradient_body(surface, body, panel_top, panel_bottom, border_radius=radius)
    border_width = 3 if hovered else 2
    pygame.draw.rect(surface, WHITE if hovered else border_color, body, width=border_width, border_radius=radius)
    pygame.draw.rect(surface, accent_color, pygame.Rect(body.x + 3, body.y + 3, body.width - 6, 6), border_radius=3)
    if hovered:
        glow = pygame.Surface((body.width + 16, body.height + 16), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*accent_color, 60), glow.get_rect(), width=6, border_radius=radius + 6)
        surface.blit(glow, (body.x - 8, body.y - 8))

    art_rect = pygame.Rect(
        body.x + int(body.width * 0.12), body.y + int(body.height * 0.12),
        int(body.width * 0.76), int(body.height * 0.34),
    )
    art_top = tuple(max(0, c - 4) for c in panel_top)
    art_bottom = tuple(max(0, c - 34) for c in panel_top)
    _rounded_gradient_body(surface, art_rect, art_top, art_bottom, border_radius=int(body.width * 0.08))
    pygame.draw.rect(surface, tuple(min(255, c + 40) for c in border_color), art_rect,
                      width=max(1, int(body.width * 0.012)), border_radius=int(body.width * 0.08))
    icon_size = int(art_rect.height * 0.8)
    if not blit_icon(surface, icon_name, art_rect.center, icon_size):
        glyph = FONT_H1.render(icon_fallback_letter, True, WHITE)
        gr = glyph.get_rect(center=art_rect.center)
        surface.blit(glyph, gr)

    if not affordable:
        dim = pygame.Surface((body.width, body.height), pygame.SRCALPHA)
        pygame.draw.rect(dim, (10, 10, 14, 140), pygame.Rect(0, 0, body.width, body.height), border_radius=radius)
        surface.blit(dim, (body.x, body.y))
    return body


def _shop_item_text(surface, body, name, desc):
    name_y = body.y + int(body.height * 0.52)
    name_surface = FONT_SMALL.render(name, True, TEXT)
    if name_surface.get_size()[0] > body.width - 16:
        name_surface = FONT_TINY.render(name, True, TEXT)
    nr = name_surface.get_rect(centerx=body.centerx, y=name_y)
    surface.blit(name_surface, nr)

    divider_y = name_y + name_surface.get_size()[1] + 4
    pygame.draw.line(surface, (255, 255, 255, 40), (body.x + 16, divider_y), (body.right - 16, divider_y), 1)

    desc_lines = wrapped_text(FONT_TINY, desc, body.width - 24)
    y = divider_y + 8
    max_y = body.bottom - 10
    for line in render_lines(FONT_TINY, desc_lines[:5], color=MUTED):
        if y + 14 > max_y:
            break
        lr = line.get_rect(centerx=body.centerx, y=y)
        surface.blit(line, lr)
        y += 15


def render_shop_relic_card(surface, relic_name, rect, hovered=False, affordable=True):
    rarity_color = RARITY_COLORS.get(RELIC_LIBRARY.get(relic_name, {}).get("rarity", "common"), MUTED)
    body = _shop_item_frame(surface, rect, hovered, affordable, CARD, rarity_color, GOLD,
                             ICON_RELIC.get(relic_name), relic_name[:1])
    _shop_item_text(surface, body, relic_name, RELIC_LIBRARY.get(relic_name, {}).get("description", ""))


def render_shop_service_card(surface, item, rect, hovered=False, affordable=True):
    body = _shop_item_frame(surface, rect, hovered, affordable, (48, 34, 36), RED, RED,
                             "relic_scale", item["name"][:1])
    _shop_item_text(surface, body, item["name"], "Permanently remove one card from your deck.")


# ---------------------------------------------------------------------------
# Map generation
# ---------------------------------------------------------------------------

class MapGenerationError(Exception):
    pass


ROW_SIZES = [3, 4, 4, 4, 3, 2]  # regular rows; a single boss node is appended after
MAP_LEFT, MAP_RIGHT = 150, 1130
MAP_TOP, MAP_BOTTOM = 175, 640


def _node_type_pool(row_index, total_rows):
    if row_index == 0:
        return [("combat", 0.7), ("event", 0.3)]
    if row_index == total_rows - 2:  # last regular row, right before boss
        return [("rest", 1.0)]
    return [
        ("combat", 0.38),
        ("event", 0.18),
        ("elite", 0.10),
        ("rest", 0.10),
        ("shop", 0.10),
        ("treasure", 0.14),
    ]


def _weighted_choice(pool):
    total = sum(w for _, w in pool)
    roll = random.uniform(0, total)
    upto = 0
    for value, weight in pool:
        upto += weight
        if roll <= upto:
            return value
    return pool[-1][0]


def generate_map(row_sizes=None):
    """Build a connected, branching tower map.

    Returns (rows, edges) where ``rows`` is a list of lists of node dicts
    and ``edges`` maps a node id to the set of node ids it connects to in
    the next row. Guarantees:
      * every node in row 0 is reachable from the (virtual) start
      * every node has at least one outgoing edge, except the boss
      * every node below the boss row has at least one incoming edge
      * at least one full path exists from row 0 to the boss
    """
    sizes = list(row_sizes) if row_sizes else list(ROW_SIZES)
    total_rows = len(sizes) + 1  # + boss row

    rows = []
    for r, size in enumerate(sizes):
        row = [
            {"id": f"r{r}c{c}", "row": r, "col": c, "type": None, "name": "", "icon": ""}
            for c in range(size)
        ]
        rows.append(row)
    boss_row = [{"id": f"r{len(sizes)}c0", "row": len(sizes), "col": 0, "type": "boss", "name": "Boss", "icon": NODE_ICONS["boss"]}]
    rows.append(boss_row)

    edges = {}

    def add_edge(a, b):
        edges.setdefault(a, set()).add(b)

    # 1. Several random walks guarantee at least a handful of full routes and
    #    natural branching between them.
    num_paths = max(4, sizes[0] + 2)
    for _ in range(num_paths):
        col = random.randrange(sizes[0])
        for r in range(len(sizes)):
            cur_id = rows[r][col]["id"]
            if r + 1 < len(sizes):
                next_size = sizes[r + 1]
                candidates = [c for c in (col - 1, col, col + 1) if 0 <= c < next_size]
                col = random.choice(candidates)
                add_edge(cur_id, rows[r + 1][col]["id"])
            else:
                add_edge(cur_id, boss_row[0]["id"])

    # 2. Connectivity cleanup: guarantee every node has >=1 outgoing edge
    #    (except boss) and every non-row0 node has >=1 incoming edge.
    for r in range(len(sizes)):
        next_row = rows[r + 1] if r + 1 < len(sizes) else boss_row
        for node in rows[r]:
            if node["id"] not in edges or not edges[node["id"]]:
                target = random.choice(next_row)
                add_edge(node["id"], target["id"])

    incoming = {node["id"]: False for row in rows for node in row}
    for row0_node in rows[0]:
        incoming[row0_node["id"]] = True  # virtual start connects to all of row 0
    for src, targets in edges.items():
        for t in targets:
            incoming[t] = True

    for r in range(1, len(rows)):
        prev_row = rows[r - 1]
        for node in rows[r]:
            if not incoming[node["id"]]:
                source = random.choice(prev_row)
                add_edge(source["id"], node["id"])
                incoming[node["id"]] = True

    # 3. Assign node types.
    for r, row in enumerate(rows[:-1]):
        pool = _node_type_pool(r, total_rows)
        row_types = []
        for node in row:
            t = _weighted_choice(pool)
            row_types.append(t)
        # Guarantee at least one rest node in the pre-boss row (pool already
        # forces this since it's 100% rest, kept generic for smaller maps).
        if r == len(rows) - 3 and "rest" not in row_types:
            row_types[random.randrange(len(row_types))] = "rest"
        for node, t in zip(row, row_types):
            node["type"] = t
            node["name"] = NODE_LABELS[t]
            node["icon"] = NODE_ICONS[t]

    # 4. Pixel coordinates for rendering.
    for r, row in enumerate(rows):
        n = len(row)
        y = MAP_BOTTOM - r * ((MAP_BOTTOM - MAP_TOP) / (total_rows - 1))
        for c, node in enumerate(row):
            x = MAP_LEFT + (c + 0.5) * ((MAP_RIGHT - MAP_LEFT) / n)
            node["x"] = int(x)
            node["y"] = int(y)

    validate_map(rows, edges)
    return rows, edges


def validate_map(rows, edges):
    """Raise MapGenerationError if the map is malformed. Used by tests too."""
    all_ids = {node["id"] for row in rows for node in row}
    boss_id = rows[-1][0]["id"]

    # BFS from a virtual start connected to every row-0 node.
    reachable = set()
    frontier = [n["id"] for n in rows[0]]
    reachable.update(frontier)
    while frontier:
        nxt = []
        for node_id in frontier:
            for target in edges.get(node_id, ()):
                if target not in reachable:
                    reachable.add(target)
                    nxt.append(target)
        frontier = nxt

    if boss_id not in reachable:
        raise MapGenerationError("Boss node is not reachable from the start of the map.")

    for row in rows[:-1]:
        for node in row:
            if node["id"] not in edges or not edges[node["id"]]:
                raise MapGenerationError(f"Node {node['id']} has no outgoing path.")

    for row in rows[1:]:
        for node in row:
            if node["id"] not in reachable:
                raise MapGenerationError(f"Node {node['id']} is disconnected from the start.")

    return True


def find_node(rows, node_id):
    for row in rows:
        for node in row:
            if node["id"] == node_id:
                return node
    return None


# ---------------------------------------------------------------------------
# Enemy / boss data
# ---------------------------------------------------------------------------

ENEMY_TEMPLATES = [
    ("Husk of Unsolvable Variables", 28, 7, "aggressive"),
    ("Glass Mathematician", 24, 10, "burst"),
    ("Sentry of Error", 32, 8, "guard"),
    ("Gambler's Shade", 30, 9, "aggressive"),
    ("Axiom Warden", 36, 7, "guard"),
    ("Oracle of Odds", 30, 11, "burst"),
    ("Dice Hoarder", 26, 9, "aggressive"),
    ("Probability Leech", 35, 8, "guard"),
    ("Recursive Wraith", 27, 9, "burst"),
    ("Infinite Loop", 31, 8, "guard"),
    ("Rounding Error", 22, 12, "aggressive"),
    # New Act 1/2 probabilistic archetypes ("Chad Math" content) -- each has
    # its own custom intent/attack logic (see roll_enemy_intent/enemy_turn):
    # HP below is the template's base before per-floor scaling (+4/floor).
    ("Decimal Demon", 48, 5, "decimal_demon"),
    ("Fractal Fiend", 56, 5, "fractal_fiend"),
    ("Vector Viper", 42, 7, "vector_viper"),
]

ELITE_TEMPLATES = [
    ("Cinder Engine", 55, 13, "burst"),
    ("Chalkboard Titan", 52, 12, "guard"),
    ("Derivative Ogre", 50, 14, "burst"),
    ("Abacus Anarchist", 48, 15, "aggressive"),
    ("Radical Wraith", 62, 8, "radical_wraith"),
]

BOSS_TEMPLATES = [
    ("The Calculus Colossus", "burst"),
    ("The Null Hypothesis", "guard"),
    ("The Derivative Dragon", "derivative_dragon"),
    ("The Abacus King", "guard"),
    ("The Prime Minister of Pain", "aggressive"),
    ("The Fractal Empress", "burst"),
    ("The Singularity", "guard"),
]

# Bosses were hitting too hard -- a flat 5% reduction applied to every
# damage-dealing intent a boss rolls (see roll_enemy_intent()), regardless
# of which ai archetype it's using. Block intents are untouched (that's the
# boss defending, not attacking) and this never applies outside combat_boss
# fights, so regular enemies/elites sharing the same ai names (guard/burst/
# aggressive) are unaffected.
BOSS_DAMAGE_NERF = 0.95


def apply_special_enemy_traits(enemy):
    """Stamps ai-specific passive flags onto a freshly generated enemy dict.
    Kept out of the plain (name, hp, attack, ai) template tuples since these
    are boolean/threshold flags rather than stats, and only 2 of the 4 new
    archetypes need one:
      - Decimal Demon: "Reduces incoming damage from odd rolls by 2" --
        odd_resist flag consulted in Game.deal_damage()'s enemy-target branch.
      - Fractal Fiend: "gains 12 Block upon reaching 50% HP" (once) --
        fractal_split_pending flag, consumed the first time its HP crosses
        the halfway mark in Game.deal_damage(). (Scoped down from "splits
        into a mini-fragment": this engine tracks exactly one enemy per
        combat, so a literal second-monster split isn't supported -- the
        spec explicitly offers block-gain as the alternative.)
    """
    if enemy["ai"] == "decimal_demon":
        enemy["odd_resist"] = 2
    elif enemy["ai"] == "fractal_fiend":
        enemy["fractal_split_pending"] = True
    elif enemy["ai"] == "derivative_dragon":
        # The Derivative Dragon (Act Boss, multi-phase): Phase 1 "Chain Rule
        # Breath" is a flat 2d8. The first time its HP crosses the halfway
        # mark, dragon_phase2_pending fires "L'Hopital's Rage" (consumed in
        # Game.deal_damage(), same pending-flag pattern as Fractal Fiend's
        # block gain): every dragon roll thereafter gets +3, and every
        # player debuff is cleared.
        enemy["dragon_phase"] = 1
        enemy["dragon_phase2_pending"] = True
        enemy["dragon_rage_bonus"] = 0
    return enemy


# ---------------------------------------------------------------------------
# Event (question-mark room) outcomes: "The Shrine of Expected Value"
# ---------------------------------------------------------------------------
# Replaces the old passive 1d20 EVENT_OUTCOMES roll with three explicit
# player choices, each with its stake/odds/payout shown before confirming
# (Professor Jenson's rubric, Section 2: risk vs. reward the player can
# actually evaluate, not a black-box roll).
#
#   Chad Wager:      stake 10 gold, roll 1d6.
#                    3-6 (P=4/6=66.7%) -> win 15 gold (net +5)
#                    1-2 (P=2/6=33.3%) -> lose the stake (net -10)
#                    E(V) = (4/6 * +5) + (2/6 * -10) = 0.00 gold
#                    ("fair game" -- zero expected value either way)
#   Giga Chad Wager: stake 15 HP, roll 1d20.
#                    15-20 (P=6/20=30%) -> a Rare Relic OR +40 gold
#                    1-14  (P=14/20=70%) -> lose 15 HP
#                    Asymmetric risk: the player must weigh a 70% "risk of
#                    ruin" against the reward, not just chase the raw E(V).
#   Coward Chad:     walk away -- no changes, no risk.
WAGER_SAFE_BET_STAKE_GOLD = 10
WAGER_SAFE_BET_WIN_GOLD = 15
WAGER_SAFE_BET_WIN_ROLLS = (3, 4, 5, 6)        # of 1d6
WAGER_SAFE_BET_WIN_PROB = len(WAGER_SAFE_BET_WIN_ROLLS) / 6
WAGER_SAFE_BET_EV = (
    WAGER_SAFE_BET_WIN_PROB * (WAGER_SAFE_BET_WIN_GOLD - WAGER_SAFE_BET_STAKE_GOLD)
    + (1 - WAGER_SAFE_BET_WIN_PROB) * (-WAGER_SAFE_BET_STAKE_GOLD)
)

WAGER_DEGENERATE_STAKE_HP = 15
WAGER_DEGENERATE_WIN_GOLD = 40
WAGER_DEGENERATE_WIN_ROLLS = tuple(range(15, 21))  # of 1d20
WAGER_DEGENERATE_WIN_PROB = len(WAGER_DEGENERATE_WIN_ROLLS) / 20
WAGER_DEGENERATE_RUIN_PROB = 1 - WAGER_DEGENERATE_WIN_PROB

# The die "tumble" animation shown while a wager result is pending (see
# Game.choose_safe_bet/choose_degenerate_bet, which set wager_pending
# instead of resolving immediately, and update_effects(), which ticks the
# timer down and resolves once it hits 0).
WAGER_TUMBLE_SECONDS = 0.4


def _wager_safe_bet_roll(game):
    """Resolves the Chad Wager: stakes are already deducted by the caller."""
    roll = game.roll_dice(6, "Chad Wager")
    if roll in WAGER_SAFE_BET_WIN_ROLLS:
        game.player["gold"] += WAGER_SAFE_BET_WIN_GOLD
        net = WAGER_SAFE_BET_WIN_GOLD - WAGER_SAFE_BET_STAKE_GOLD
        game.event_outcome_label = "Chad Wager — You Win"
        game.log_text = f"Rolled {roll}: you win {WAGER_SAFE_BET_WIN_GOLD} gold (net +{net})."
    else:
        game.event_outcome_label = "Chad Wager — You Lose"
        game.log_text = f"Rolled {roll}: you lose your {WAGER_SAFE_BET_STAKE_GOLD} gold stake."
    game.roll_result = roll
    game.state = "event_result"


def _wager_degenerate_bet_roll(game):
    """Resolves the Giga Chad Wager: the HP stake is already deducted by
    the caller (and checked for lethality there)."""
    roll = game.roll_dice(20, "Giga Chad Wager")
    if roll in WAGER_DEGENERATE_WIN_ROLLS:
        pool = [r for r in RELIC_NAMES if r not in game.player["relics"] and RELIC_LIBRARY[r]["rarity"] == "rare"]
        if pool:
            relic = random.choice(pool)
            game.apply_relic(relic)
            game.event_outcome_label = "Giga Chad Wager — Jackpot!"
            game.log_text = f"Rolled {roll}: jackpot! You gain the rare relic {relic}."
        else:
            game.player["gold"] += WAGER_DEGENERATE_WIN_GOLD
            game.event_outcome_label = "Giga Chad Wager — Jackpot!"
            game.log_text = f"Rolled {roll}: jackpot! +{WAGER_DEGENERATE_WIN_GOLD} gold (all rare relics already owned)."
    else:
        game.event_outcome_label = "Giga Chad Wager — Bust"
        game.log_text = f"Rolled {roll}: bust. You already paid the {WAGER_DEGENERATE_STAKE_HP} HP stake."
    game.roll_result = roll
    game.state = "event_result"


GOLD_COMBAT_RANGE = (12, 20)
GOLD_ELITE_RANGE = (25, 38)
SHOP_PRICE_BY_RARITY = {"common": 55, "uncommon": 75, "rare": 100}


# ---------------------------------------------------------------------------
# Game state / logic
# ---------------------------------------------------------------------------

class Game:
    """All game logic lives here. Nothing in this class touches pygame's
    display, event, or time modules directly — that keeps it fully testable
    without a window. Only CARD_LIBRARY effects call back into methods here.
    """

    def __init__(self):
        self.state = "title"
        self.character = "Math Warrior"
        self.act = 1
        self.floor = 1
        self.map_rows = []
        self.map_edges = {}
        self.current_node_id = None
        self.visited_node_ids = set()
        self.button_rects = {}
        self.combat_log = []
        self.log_text = "Welcome to Chad Math vs. The Forces of the Math Spire."
        self.floating_text = []
        self.hit_flash = None
        self.turn_banner = None
        self.shake = {"timer": 0.0, "magnitude": 0}
        self.lunge = {"actor": None, "timer": 0.0, "duration": 0.22}
        self.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        self.roll_result = None
        self.event_outcome_label = ""
        self.enemy = None
        self.last_enemy_action = ""
        self.card_reward_options = []
        self.relic_choice_options = []
        self.rest_upgrade_options = []
        self.shop_offer = []
        self.remove_pick_page = 0
        self.pending_reward_stages = []
        self.dice_roll = None
        self.dice_sides = None
        self.dice_anim_timer = 0.0
        self.quiz_attempts = 0
        self.quiz_equation = None
        self.quiz_answer = None
        self.quiz_input = ""
        self.quiz_is_act_transition = False
        self.boss_act = None
        self.deck_page = 0
        self.deck_return_state = "map"
        self.show_log = False
        self.show_pause_menu = False
        self.show_math_inspector = False
        self.quit_requested = False
        self.turn_number = 1
        self.combat_elite = False
        self.combat_boss = False
        self._crown_pending = False
        self._null_set_used = False
        self.roll_history = {}
        self.wager_pending = None
        self.push_luck_active = False
        self.push_luck_banked = 0
        self.combat_card_bonus = {}
        self.player = {
            "name": self.character,
            "hp": 94,
            "max_hp": 94,
            "block": 0,
            "energy": 3,
            "max_energy": 3,
            "relics": [],
            "active_powers": [],
            "gold": 30,
            "statuses": {},
        }
        self.draw_pile = []
        self.discard = []
        self.hand = []

    # -- setup / run lifecycle --------------------------------------------

    @property
    def log_text(self):
        return self._log_text

    @log_text.setter
    def log_text(self, value):
        self._log_text = str(value)
        if hasattr(self, "combat_log"):
            self.combat_log.append(self._log_text)
            del self.combat_log[:-300]

    def choose_character(self, name):
        self.character = name
        self.reset_run()
        self.log_text = f"{name} enters the Spire."

    def reset_run(self):
        base = CHARACTER_OPTIONS.get(self.character, CHARACTER_OPTIONS["Math Warrior"])
        self.player = {
            "name": self.character,
            "hp": base["hp"],
            "max_hp": base["max_hp"],
            "block": 0,
            "energy": base["energy"],
            "max_energy": base["energy"],
            "relics": [base["starting_relic"]],
            "active_powers": [],
            "gold": 30,
            "statuses": {},
        }
        self.floor = 1
        self.act = 1
        self.log_text = "Prepare for the next ascent."
        self.combat_log = []
        self.floating_text = []
        self.hit_flash = None
        self.turn_banner = None
        self.shake = {"timer": 0.0, "magnitude": 0}
        self.lunge = {"actor": None, "timer": 0.0, "duration": 0.22}
        self.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        self.enemy = None
        self.roll_result = None
        self.dice_roll = None
        self.dice_sides = None
        self.dice_anim_timer = 0.0
        self.pending_reward_stages = []
        self.card_reward_options = []
        self.relic_choice_options = []
        self.rest_upgrade_options = []
        self.shop_offer = []
        self.quiz_attempts = 0
        self.quiz_equation = None
        self.quiz_answer = None
        self.quiz_input = ""
        self.quiz_is_act_transition = False
        self.boss_act = None
        self.deck_page = 0
        self.deck_return_state = "map"
        self.show_log = False
        self.show_pause_menu = False
        self.show_math_inspector = False
        self.remove_pick_page = 0
        self._crown_pending = False
        self._null_set_used = False
        self.roll_history = {}
        self.build_deck()
        self.start_new_map()

    def build_deck(self):
        base = CHARACTER_OPTIONS.get(self.character, CHARACTER_OPTIONS["Math Warrior"])
        self.draw_pile = list(base["deck"])
        random.shuffle(self.draw_pile)
        self.discard = []
        self.hand = []

    def start_new_map(self):
        self.map_rows, self.map_edges = generate_map()
        self.current_node_id = None
        self.visited_node_ids = set()
        self.state = "map"

    def begin_next_act(self):
        self.act += 1
        self.floor = 1
        self.player["hp"] = self.player["max_hp"]
        self.player["block"] = 0
        self.start_new_map()
        self.log_text = f"Act {self.act} begins. A new section of the Spire unfolds."

    # -- map traversal ------------------------------------------------------

    def available_node_ids(self):
        if self.current_node_id is None:
            return {n["id"] for n in self.map_rows[0]}
        return set(self.map_edges.get(self.current_node_id, ()))

    def node_visual_state(self, node):
        if node["id"] == self.current_node_id:
            return "current"
        if node["id"] in self.visited_node_ids:
            return "visited"
        if node["id"] in self.available_node_ids():
            return "available"
        return "locked"

    def effective_floor(self):
        """Floor number used for enemy/elite/boss scaling. self.floor resets
        to 1 at the start of every act (see begin_next_act), but the fights
        still need to keep getting harder across acts rather than repeating
        Act 1's difficulty three times over — so this adds a full act's
        worth of rows for every completed act."""
        return self.floor + (self.act - 1) * (len(ROW_SIZES) + 1)

    def enter_node(self, node):
        if self.state != "map":
            return
        if node["id"] not in self.available_node_ids():
            return
        self.current_node_id = node["id"]
        self.visited_node_ids.add(node["id"])
        self.floor = node["row"] + 1
        # NOTE: floor advancement does NOT auto-heal the player — only rest
        # sites (rest_heal, 30% of max HP) and act transitions do. A full
        # heal on every single floor step used to erase all run-attrition
        # pressure, making rest sites and healing relics pointless.
        node_type = node["type"]

        if node_type == "combat":
            self.start_combat(self.generate_enemy(self.effective_floor()))
        elif node_type == "elite":
            self.start_combat(self.generate_elite(self.effective_floor()), elite=True)
        elif node_type == "boss":
            self.start_combat(self.generate_boss(self.effective_floor()), boss=True)
        elif node_type == "event":
            self.state = "event"
            self.roll_result = None
            self.log_text = "The Math Club has set up a wagering table. Place your bet, or walk away."
        elif node_type == "rest":
            self.state = "rest_choice"
            self.log_text = "A quiet rest site. Recover before you continue."
        elif node_type == "shop":
            self.shop_offer = self.generate_shop_offer()
            self.state = "shop"
            self.log_text = "A strange math merchant sets up shop."
        elif node_type == "treasure":
            self.resolve_treasure()

    def return_to_map(self):
        self.state = "map"
        self.log_text = "Choose your next path."

    # -- relics / powers ------------------------------------------------------

    def apply_relic(self, relic_name):
        if relic_name in self.player["relics"] or relic_name not in RELIC_LIBRARY:
            return False
        self.player["relics"].append(relic_name)
        trigger_hook(self, "on_acquire")
        self.log_text = f"Relic acquired: {relic_name} — {RELIC_LIBRARY[relic_name]['description']}"
        self.floating_text.append({"text": f"+ {relic_name}", "x": 420, "y": 200, "color": GOLD, "life": 1.4})
        return True

    def gain_power(self, power_name):
        if power_name not in self.player["active_powers"]:
            self.player["active_powers"].append(power_name)
        self.log_text = f"Power gained: {power_name} — {POWER_LIBRARY[power_name]['description']}"
        self.trigger_power_pose("player")

    def trigger_power_pose(self, actor="player"):
        """Starts the DBZ-style charge-up aura flash (glow ring + hero
        sprite swapped to its State-3 Power-Up pose — see
        draw_power_aura()/draw_math_warrior_sprite()'s ``state`` param) for
        power_aura["duration"] seconds (1.2s by default). Called whenever
        the player plays a Power card (gain_power), plays a Surge/Ultimate
        card (ultimate_strike_effect/ultimate_blast_effect), or banks a
        huge dice roll (roll_dice, on a max-value roll)."""
        duration = self.power_aura.get("duration", 1.2)
        self.power_aura = {"actor": actor, "timer": duration, "duration": duration}

    # -- basic player mutation helpers --------------------------------------

    def heal_player(self, amount):
        before = self.player["hp"]
        self.player["hp"] = min(self.player["max_hp"], self.player["hp"] + amount)
        healed = self.player["hp"] - before
        if healed > 0:
            self.floating_text.append({"text": f"+{healed}", "x": 200, "y": 210, "color": GREEN, "life": 0.9})
        return healed

    def gain_block(self, amount, source_name, silent=False):
        total = apply_block_modifiers(self, amount)
        self.player["block"] += total
        if not silent:
            self.floating_text.append({"text": f"+{total} block", "x": 200, "y": 230, "color": GOLD, "life": 0.9})
        self.log_text = f"{source_name} grants {total} block."
        return total

    # -- dice ----------------------------------------------------------------

    def _record_roll(self, sides, value):
        """Tally every die actually rolled, by face value, so the Math
        Inspector / Audit Stats screen can show the empirical distribution
        next to the theoretical one -- a live demonstration of the Law of
        Large Numbers as the sample size grows across a run."""
        bucket = self.roll_history.setdefault(sides, {})
        bucket[value] = bucket.get(value, 0) + 1

    def roll_dice(self, sides, label="Roll"):
        raw = random.randint(1, sides)
        value = apply_roll_modifiers(self, raw, sides)
        self._record_roll(sides, value)
        self.dice_roll = value
        self.dice_sides = sides
        self.dice_anim_timer = 0.35
        note = f" (raw {raw})" if value != raw else ""
        self.log_text = f"{label}: d{sides} rolled {value}{note}."
        self.floating_text.append({"text": f"d{sides}={value}", "x": 420, "y": 210, "color": GOLD, "life": 1.0})
        if value <= max(1, sides // 3):
            trigger_hook(self, "on_roll_fail")
        # "Banks huge dice damage": a max-value roll on a big-enough die
        # (d8+) is exactly the kind of clutch moment the Power-Up pose is
        # meant to celebrate, same as playing a Power/Surge card.
        if sides >= 8 and value >= sides:
            self.trigger_power_pose("player")
        return value

    def roll_dice_multi(self, sides, count, label):
        """Roll several dice for one action (e.g. a '3d3' card like
        Probability Missile). Rolling `count` separate roll_dice() calls in a
        tight loop used to spawn `count` floating-text popups on top of each
        other in the exact same spot (only the last one was ever readable)
        and `count` separate one-line battle-log entries — this instead
        reports every individual die together in one flash and one log line,
        e.g. "3d3 rolled [2, 1, 3] = 6.", so the player can actually see what
        each die came up as."""
        rolls = []
        for _ in range(count):
            raw = random.randint(1, sides)
            value = apply_roll_modifiers(self, raw, sides)
            self._record_roll(sides, value)
            rolls.append(value)
        total = sum(rolls)
        self.dice_roll = rolls[-1]
        self.dice_sides = sides
        self.dice_anim_timer = 0.35
        rolls_text = ", ".join(str(r) for r in rolls)
        self.log_text = f"{label}: {count}d{sides} rolled [{rolls_text}] = {total}."
        self.floating_text.append({
            "text": f"{count}d{sides}: {rolls_text} = {total}",
            "x": 380, "y": 210, "color": GOLD, "life": 1.4,
        })
        for value in rolls:
            if value <= max(1, sides // 3):
                trigger_hook(self, "on_roll_fail")
        return rolls, total

    def roll_damage_card(self, sides, label, bonus=0):
        result = self.roll_dice(sides, label)
        total = bonus + result
        self.deal_damage(self.enemy, total, label)
        return total

    def ultimate_strike_effect(self):
        """Ultimate Strike: roll 1d20, deal that much damage AND gain that
        much block. A single big d20-worth-of-damage nuke was one-shotting
        entire encounters, so this trades raw burst for damage + survivability."""
        self.trigger_power_pose("player")  # Ultimates are Surge cards — always pose, win or lose the roll.
        roll = self.roll_dice(20, "Ultimate Strike")
        self.deal_damage(self.enemy, roll, "Ultimate Strike")
        self.player["block"] += roll
        self.log_text = f"Ultimate Strike: d20={roll} — {roll} damage dealt and {roll} block gained."
        return roll

    def ultimate_blast_effect(self):
        """Ultimate Blast: roll 1d20, deal 1.5x that as damage. Kept as the
        pure-damage ultimate (rebalanced down from 2d20) since Ultimate
        Strike now covers the damage+survivability role."""
        self.trigger_power_pose("player")
        roll = self.roll_dice(20, "Ultimate Blast")
        amount = math.ceil(roll * 1.5)
        self.deal_damage(self.enemy, amount, "Ultimate Blast")
        self.log_text = f"Ultimate Blast: d20={roll} — {amount} damage (1.5x)."
        return amount

    def coin_flip_damage(self, heads_damage, tails_damage, label):
        result = self.roll_dice(2, f"{label} coin flip")
        amount = heads_damage if result == 2 else tails_damage
        self.log_text = f"{label}: d2={result}, dealing {amount} damage."
        self.deal_damage(self.enemy, amount, label)
        return amount

    def class_strike(self, upgraded=False):
        if self.character == "Probability Wizard":
            hits = 4 if upgraded else 3
            rolls, total = self.roll_dice_multi(3, hits, "Probability Missile")
            for result in rolls:
                self.deal_damage(self.enemy, result, "Probability Missile")
            return total
        amount = 12 if upgraded else 8
        self.deal_damage(self.enemy, amount, "Equation Breaker" if not upgraded else "Equation Breaker+")
        return amount

    def reckless_roll(self, upgraded=False):
        threshold = 2 if upgraded else 1
        roll = self.roll_dice(10, "Reckless Roll")
        if roll <= threshold:
            self.deal_damage(self.player, 4, "Reckless Roll backfire")
        else:
            self.deal_damage(self.enemy, roll * 2, "Reckless Roll")

    # -- Progressive Overload: escalates within a single combat --------------
    #
    # Roll 1d8 + 2 (+ this card's accumulated combat_card_bonus) damage. On a
    # roll of 5+ (P=4/8=50%), the bonus goes up by 1 for the rest of THIS
    # combat, so replaying the card later in the same fight hits harder --
    # "every set must be heavier than the last." combat_card_bonus is reset
    # to {} at the start of every combat (see start_combat/__init__), so
    # nothing carries over between fights. CARD_EV's static 6.5 baseline
    # deliberately doesn't include this live bonus, same as how Reckless
    # Roll's CARD_EV ignores the player's actual current state.
    def play_progressive_overload(self):
        bonus = self.combat_card_bonus.get("Progressive Overload", 0)
        roll = self.roll_dice(8, "Progressive Overload")
        damage = roll + 2 + bonus
        self.deal_damage(self.enemy, damage, "Progressive Overload")
        if roll >= 5:
            self.combat_card_bonus["Progressive Overload"] = bonus + 1
            self.log_text = (
                f"Progressive Overload rolled {roll}: {damage} damage. "
                f"Bonus increases to +{bonus + 1} for the rest of combat!"
            )
        else:
            self.log_text = f"Progressive Overload rolled {roll}: {damage} damage."

    # -- Gambler's Flurry: interactive push-your-luck ------------------------
    #
    # Playing the card enters push_luck_active (an in-combat sub-state, not
    # a separate game.state -- draw_combat() renders the special banner/
    # buttons on top of the normal combat screen and hides the hand/end-turn
    # controls while it's True; see draw_push_luck_banner() and play_card()'s
    # guard below). E(Next Roll) = (17-B)/6 for banked damage B: derived from
    # -(B+3)/6 [P=1/6 bust: lose the whole bank AND take 3 recoil] +
    # sum_{k=2..6} k/6 [the 5 non-bust faces] = -(B+3)/6 + 20/6.
    GAMBLERS_FLURRY_RECOIL = 3

    def play_gamblers_flurry(self):
        self.push_luck_active = True
        self.push_luck_banked = 0
        roll = self.roll_dice(6, "Gambler's Flurry")
        if roll == 1:
            self.push_luck_active = False
            self.push_luck_banked = 0
            self.deal_damage(self.player, self.GAMBLERS_FLURRY_RECOIL, "Gambler's Flurry backfire")
            self.log_text = (
                f"Gambler's Flurry busts on the opening roll (rolled 1)! "
                f"Turn ends, {self.GAMBLERS_FLURRY_RECOIL} recoil damage."
            )
            self.end_player_turn()
            return
        self.push_luck_banked = roll
        self.log_text = f"Gambler's Flurry: rolled {roll}. Banked damage: {roll}. Bank & strike, or push your luck?"

    def push_luck_bank_and_strike(self):
        if not self.push_luck_active:
            return
        banked = self.push_luck_banked
        self.push_luck_active = False
        self.push_luck_banked = 0
        dealt = self.deal_damage(self.enemy, banked, "Gambler's Flurry")
        self.log_text = f"Gambler's Flurry: banked {banked} damage struck for {dealt}."

    def push_luck_push_again(self):
        if not self.push_luck_active:
            return
        roll = self.roll_dice(6, "Gambler's Flurry")
        if roll == 1:
            self.push_luck_active = False
            self.push_luck_banked = 0
            self.deal_damage(self.player, self.GAMBLERS_FLURRY_RECOIL, "Gambler's Flurry backfire")
            self.log_text = (
                f"Gambler's Flurry: BUSTED! Rolled a 1! Banked damage wiped, "
                f"{self.GAMBLERS_FLURRY_RECOIL} recoil damage."
            )
            return
        self.push_luck_banked += roll
        self.log_text = f"Gambler's Flurry: rolled {roll}. Banked damage: {self.push_luck_banked}."

    def push_luck_next_roll_ev(self):
        """E(Next Roll) = -(B+3)/6 + 20/6 = (17-B)/6, B = current bank."""
        return (17 - self.push_luck_banked) / 6

    def roll_block(self, bonus=0, label="Probability Shield"):
        roll = self.roll_dice(8, label)
        self.gain_block(roll + bonus, label)

    def play_spotters_axiom(self):
        """Roll 1d6, gain block equal to DOUBLE the roll (2-12) -- the
        spotter "catching" the lift, and a direct nod to the x2 die shown
        in the card's art. A pure block card like Defend/Probability
        Shield, so (matching their precedent) it's deliberately left out
        of CARD_EV/CARD_RECOIL_EV, which are damage-only, and out of
        math_audit.py's action list, which only audits damage actions."""
        roll = self.roll_dice(6, "Spotter's Axiom")
        self.gain_block(roll * 2, "Spotter's Axiom")

    # -- deck / hand -----------------------------------------------------------

    def draw_cards(self, count):
        for _ in range(count):
            if not self.draw_pile:
                if not self.discard:
                    break
                self.draw_pile = self.discard
                self.discard = []
                random.shuffle(self.draw_pile)
                self.log_text = "Discard pile reshuffled into the draw pile."
            if self.draw_pile:
                card = self.draw_pile.pop()
                self.hand.append(card)
                if card == "Misread Equation":
                    self.hand.remove(card)
                    self.deal_damage(self.player, 6, "Misread Equation")
                    self.log_text = "Misread Equation: you lose 6 HP from the bad calculation."

    def full_deck_cards(self):
        return self.draw_pile + self.discard + self.hand

    # -- combat ------------------------------------------------------------

    def generate_enemy(self, row_number):
        name, hp, attack, ai = random.choice(ENEMY_TEMPLATES)
        hp += row_number * 4
        attack += row_number // 2
        return apply_special_enemy_traits(
            {"name": name, "hp": hp, "max_hp": hp, "attack": attack, "block": 0, "ai": ai, "intent": None, "statuses": {}}
        )

    def generate_elite(self, row_number):
        name, hp, attack, ai = random.choice(ELITE_TEMPLATES)
        hp += row_number * 6
        attack += row_number // 2 + 2
        return apply_special_enemy_traits(
            {"name": f"Elite {name}", "hp": hp, "max_hp": hp, "attack": attack, "block": 0, "ai": ai, "intent": None, "statuses": {}}
        )

    def generate_boss(self, row_number):
        name, ai = random.choice(BOSS_TEMPLATES)
        if name == "The Derivative Dragon":
            # Fixed boss HP per spec, not the generic per-floor scaling.
            hp = 140
            attack = 13 + row_number * 2
        else:
            hp = 95 + row_number * 10
            attack = 13 + row_number * 2
        return apply_special_enemy_traits(
            {"name": name, "hp": hp, "max_hp": hp, "attack": attack, "block": 0, "ai": ai, "intent": None, "statuses": {}}
        )

    def start_combat(self, enemy, elite=False, boss=False):
        self.enemy = enemy
        self.combat_elite = elite
        self.combat_boss = boss
        self.discard.extend(self.hand)
        self.hand = []
        self.player["statuses"] = {}
        self.enemy["statuses"] = {}
        self.player["active_powers"] = []
        self._crown_pending = False
        self._null_set_used = False
        self.turn_number = 1
        trigger_hook(self, "on_combat_start")  # e.g. Probability Crown arming
        self.dice_roll = None
        self.dice_sides = None
        self.push_luck_active = False
        self.push_luck_banked = 0
        self.combat_card_bonus = {}
        self.state = "combat"
        kind = "The final boss descends" if boss else ("An elite challenger appears" if elite else "A foe blocks the path")
        self.log_text = f"{kind}: {enemy['name']}"
        # Block from any on_combat_start hook must survive into the first
        # turn, so it is NOT re-zeroed here. (Pi's Defense itself now grants
        # its block via on_turn_start, so it fires here too via the
        # start_player_turn() call below -- every turn, not just this one.)
        self.start_player_turn(reset_block=False)

    def roll_enemy_intent(self, enemy):
        if not enemy:
            return
        ai = enemy["ai"]
        if ai == "guard":
            if random.random() < 0.45:
                enemy["intent"] = {"type": "block", "value": 6 + self.effective_floor()}
            else:
                enemy["intent"] = {"type": "attack", "value": enemy["attack"] + random.randint(1, 6)}
        elif ai == "burst":
            if random.random() < 0.35:
                enemy["intent"] = {"type": "burst", "value": enemy["attack"] + 8}
            else:
                enemy["intent"] = {"type": "attack", "value": enemy["attack"]}
        elif ai == "decimal_demon":
            # "Floating Point Shift": 1d10 damage + Truncate (-1 to the
            # player's dice rolls next turn). E(V) = 5.5 -- see the Math
            # Inspector's enemy_intent_ev().
            enemy["intent"] = {"type": "decimal_shift", "value": random.randint(1, 10)}
        elif ai == "fractal_fiend":
            # "Mandelbrot Recursion": 2d4; rolling doubles recurses an extra
            # 1d4 hit. E(V) = 5 + 0.25*2.5 = 5.625.
            d1, d2 = random.randint(1, 4), random.randint(1, 4)
            total = d1 + d2
            doubles = d1 == d2
            if doubles:
                total += random.randint(1, 4)
            enemy["intent"] = {"type": "fractal_recursion", "value": total, "doubles": doubles}
        elif ai == "vector_viper":
            # Alternates between a high-speed piercing strike (3d4, bypasses
            # block entirely) and a turn spent building evade/defend charges.
            phase = enemy.get("viper_phase", 0)
            if phase == 0:
                rolls = [random.randint(1, 4) for _ in range(3)]
                enemy["intent"] = {"type": "vector_pierce", "value": sum(rolls), "rolls": rolls}
            else:
                enemy["intent"] = {"type": "block", "value": 10 + self.effective_floor()}
            enemy["viper_phase"] = 1 - phase
        elif ai == "radical_wraith":
            # "Root Extraction": 1d12; a perfect square (1, 4, 9) triples
            # the damage AND heals the wraith for the roll amount.
            roll = random.randint(1, 12)
            perfect_square = roll in (1, 4, 9)
            value = roll * 3 if perfect_square else roll
            enemy["intent"] = {"type": "root_extraction", "value": value, "roll": roll, "square": perfect_square}
        elif ai == "derivative_dragon":
            # "Chain Rule Breath": 2d8, plus the +3 L'Hopital's Rage bonus
            # to every roll once Phase 2 has triggered (see deal_damage()).
            rolls = [random.randint(1, 8) for _ in range(2)]
            bonus = enemy.get("dragon_rage_bonus", 0)
            enemy["intent"] = {"type": "chain_rule_breath", "value": sum(rolls) + bonus, "rolls": rolls, "bonus": bonus}
        else:
            enemy["intent"] = {"type": "attack", "value": enemy["attack"] + random.randint(1, 5)}

        # 5% boss damage nerf, applied uniformly across every ai archetype
        # above (including the Derivative Dragon's dice-based chain_rule_
        # breath) so it can't be missed on a case-by-case basis. Only boss
        # fights are affected -- combat_boss is False for regular enemies
        # and elites even when they share the same ai name (guard/burst).
        # Block intents are the boss defending, not attacking, so they're
        # left alone.
        if self.combat_boss and enemy["intent"]["type"] != "block":
            enemy["intent"]["value"] = max(0, math.floor(enemy["intent"]["value"] * BOSS_DAMAGE_NERF))

    def start_player_turn(self, reset_block=True):
        if reset_block:
            self.player["block"] = 0
        self.player["energy"] = self.player["max_energy"]
        for target in (self.player, self.enemy):
            if target:
                target["statuses"] = {name: turns - 1 for name, turns in target.get("statuses", {}).items() if turns > 1}
        trigger_hook(self, "on_turn_start")
        self.draw_cards(5)
        self.roll_enemy_intent(self.enemy)
        self.turn_banner = {"text": f"Turn {self.turn_number}", "timer": 0.8}

    def play_card(self, card_name):
        if self.state != "combat":
            return
        if self.push_luck_active:
            # Gambler's Flurry is mid-resolution -- the player must Bank &
            # Strike or Push Your Luck before touching anything else.
            self.log_text = "Resolve Gambler's Flurry first: bank your damage or push your luck."
            return
        card = CARD_LIBRARY[card_name]
        if self.player["energy"] < card["cost"]:
            self.log_text = "Not enough energy."
            return
        if card_name not in self.hand:
            return

        self.player["energy"] -= card["cost"]
        self.hand.remove(card_name)
        if not card.get("exhaust"):
            self.discard.append(card_name)
        card["effect"](self)
        # card effects (deal_damage / _on_enemy_defeated / lethal checks) own
        # every resulting state transition — nothing else to do here.

    def end_player_turn(self):
        if self.state != "combat":
            return
        self.discard.extend(self.hand)
        self.hand = []
        self.log_text = "Turn ends. The enemy moves."
        self.turn_banner = {"text": "Enemy Turn", "timer": 0.7}
        self.enemy_turn()
        if self.state == "combat":
            self.turn_number += 1
            self.start_player_turn()

    def enemy_turn(self):
        if not self.enemy or self.state != "combat":
            return
        self.enemy["block"] = 0
        intent = self.enemy.get("intent") or {"type": "attack", "value": self.enemy["attack"]}

        if intent["type"] == "block":
            gained = intent["value"]
            self.enemy["block"] += gained
            self.last_enemy_action = f"{self.enemy['name']} braces and gains {gained} block."
        elif intent["type"] == "burst":
            hit = intent["value"]
            dealt = self.deal_damage(self.player, hit, self.enemy["name"])
            # Announce what actually landed (post-Weak/block), not the raw
            # intent value -- otherwise Weak looks broken: the HP loss is
            # correctly halved, but this banner used to still brag about
            # the full, un-mitigated number, so it never looked like Weak
            # had done anything even though it had.
            self.last_enemy_action = f"{self.enemy['name']} unleashes a burst for {dealt}!"
        elif intent["type"] == "decimal_shift":
            dealt = self.deal_damage(self.player, intent["value"], self.enemy["name"])
            self.apply_status(self.player, "truncate", 2)  # survives exactly one player turn -- see start_player_turn's decrement
            self.last_enemy_action = f"{self.enemy['name']} shifts the decimal point for {dealt} and truncates your next rolls!"
        elif intent["type"] == "fractal_recursion":
            dealt = self.deal_damage(self.player, intent["value"], self.enemy["name"])
            note = " (doubles! recursed for extra damage)" if intent.get("doubles") else ""
            self.last_enemy_action = f"{self.enemy['name']} recurses for {dealt}{note}."
        elif intent["type"] == "vector_pierce":
            dealt = self.deal_damage(self.player, intent["value"], self.enemy["name"], pierce=True)
            self.last_enemy_action = f"{self.enemy['name']} lands a piercing cross product for {dealt} (bypasses block)!"
        elif intent["type"] == "root_extraction":
            roll = intent.get("roll", intent["value"])
            dealt = self.deal_damage(self.player, intent["value"], self.enemy["name"])
            if intent.get("square"):
                healed = min(self.enemy["max_hp"] - self.enemy["hp"], roll)
                self.enemy["hp"] += healed
                if healed > 0:
                    self.floating_text.append({"text": f"+{healed}", "x": 1060, "y": 230, "color": GREEN, "life": 0.9})
                self.last_enemy_action = f"{self.enemy['name']} extracts a perfect root! {dealt} damage, heals {healed}."
            else:
                self.last_enemy_action = f"{self.enemy['name']} extracts a root for {dealt}."
        elif intent["type"] == "chain_rule_breath":
            dealt = self.deal_damage(self.player, intent["value"], self.enemy["name"])
            rage_note = " (L'Hopital's Rage empowered!)" if intent.get("bonus") else ""
            self.last_enemy_action = f"{self.enemy['name']} exhales Chain Rule Breath for {dealt}{rage_note}."
        else:
            hit = intent["value"]
            dealt = self.deal_damage(self.player, hit, self.enemy["name"])
            self.last_enemy_action = f"{self.enemy['name']} attacks for {dealt}."

        self.log_text = self.last_enemy_action

    def apply_status(self, target, status_name, turns):
        if target is None:
            return
        statuses = target.setdefault("statuses", {})
        statuses[status_name] = statuses.get(status_name, 0) + turns
        label = status_name.title()
        target_name = "the enemy" if target is self.enemy else "you"
        self.log_text = f"{target_name} gains {turns} {label}."
        self.floating_text.append({"text": f"{label} {turns}", "x": 1060 if target is self.enemy else 200, "y": 245, "color": PURPLE, "life": 1.0})

    def _spawn_damage_text(self, anchor_x, anchor_y, text, color):
        """Append a floating damage number, staggered away from any other
        still-fresh number already sitting at this same anchor point.

        Multi-hit attacks (e.g. the Probability Wizard's Probability
        Missile, which calls deal_damage three times in a row for one card
        play) used to spawn every floating number at the *exact* same
        x/y -- they rendered perfectly stacked on top of each other, so
        only the topmost was ever visible on screen even though each hit
        landed correctly (confirmed by the log text and the resulting HP/
        block totals, just not shown on screen). Staggering by how many
        recent texts already claim this anchor makes each individual hit
        visible as its own number fanning outward."""
        stack = sum(
            1 for item in self.floating_text
            if item.get("anchor") == (anchor_x, anchor_y) and item["life"] > 0.75
        )
        self.floating_text.append({
            "text": text,
            "x": anchor_x + stack * 26,
            "y": anchor_y - stack * 20,
            "color": color, "life": 0.9,
            "anchor": (anchor_x, anchor_y),
        })

    def deal_damage(self, target, amount, source_name, pierce=False):
        if target is self.enemy and self.enemy is not None:
            dealt = amount
            if self.enemy.get("statuses", {}).get("vulnerable", 0) > 0:
                dealt = math.ceil(dealt * 1.5)
            crit = False
            if self._crown_pending:
                dealt *= 2
                crit = True
                self._crown_pending = False
            # Decimal Demon passive: "Reduces incoming damage from
            # non-integer/odd rolls by 2" -- judged against the raw hit
            # amount passed in (the roll/value that produced this attack),
            # flat-reduced like armor, floored at 0.
            if self.enemy.get("odd_resist") and amount % 2 == 1:
                dealt = max(0, dealt - self.enemy["odd_resist"])
            damage = dealt
            if self.enemy["block"] > 0:
                absorbed = min(self.enemy["block"], damage)
                self.enemy["block"] -= absorbed
                damage -= absorbed
            if damage > 0:
                self.enemy["hp"] = max(0, self.enemy["hp"] - damage)
            # Fractal Fiend passive: gains 12 Block the first time it's
            # brought to (or below) half HP by this hit.
            if self.enemy.get("fractal_split_pending") and self.enemy["hp"] <= self.enemy["max_hp"] // 2:
                self.enemy["fractal_split_pending"] = False
                self.enemy["block"] += 12
                self.log_text = f"{self.enemy['name']} fractures at half HP and gains 12 block!"
            # The Derivative Dragon's Phase 2 transition: below 50% HP, it
            # clears every player debuff and every dragon roll thereafter
            # gets +3 (applied in roll_enemy_intent()).
            if self.enemy.get("dragon_phase2_pending") and self.enemy["hp"] <= self.enemy["max_hp"] // 2:
                self.enemy["dragon_phase2_pending"] = False
                self.enemy["dragon_phase"] = 2
                self.enemy["dragon_rage_bonus"] = 3
                self.player["statuses"] = {}
                self.log_text = f"{self.enemy['name']} enters L'Hopital's Rage! All your debuffs are cleared, and its rolls surge +3."
            note = " (Probability Crown crit!)" if crit else ""
            self.log_text = f"{source_name} hits {self.enemy['name']} for {dealt}{note}."
            self._spawn_damage_text(1060, 210, f"-{damage}" if damage > 0 else "BLOCKED", RED if damage > 0 else MUTED)
            self.hit_flash = {"target": "enemy", "timer": 0.18}
            self.lunge = {"actor": "player", "timer": 0.22, "duration": 0.22}
            if damage > 0:
                self.shake = {"timer": 0.16, "magnitude": min(12, 3 + damage // 4)}
            if self.enemy["hp"] <= 0:
                self._on_enemy_defeated()
            return damage
        elif target is self.player:
            damage = amount
            if self.enemy is not None and self.enemy.get("statuses", {}).get("weak", 0) > 0:
                damage = max(0, math.floor(damage * 0.5))
            elif self.player.get("statuses", {}).get("weak", 0) > 0:
                damage = max(0, math.floor(damage * 0.5))
            if pierce:
                # Vector Viper's "Cross Product Strike" bypasses block
                # entirely -- Weak (above) still applies (it reduces the
                # enemy's output, not the player's mitigation), block does not.
                pass
            elif self.player["block"] > 0:
                absorbed = min(self.player["block"], damage)
                self.player["block"] -= absorbed
                damage -= absorbed
            if damage > 0:
                self.player["hp"] -= damage
            self.log_text = f"{source_name} hits you for {damage}."
            self._spawn_damage_text(200, 210, f"-{damage}" if damage > 0 else "BLOCKED", RED if damage > 0 else MUTED)
            self.hit_flash = {"target": "player", "timer": 0.18}
            if self.enemy is not None and source_name == self.enemy.get("name"):
                self.lunge = {"actor": "enemy", "timer": 0.22, "duration": 0.22}
            if damage > 0:
                self.shake = {"timer": 0.16, "magnitude": min(12, 3 + damage // 4)}
            if self.player["hp"] <= 0:
                if any_lethal_save(self):
                    pass
                else:
                    self.player["hp"] = 0
                    self.state = "gameover"
                    self.log_text = f"{source_name} finishes you off."
            return damage
        return None

    def _on_enemy_defeated(self):
        trigger_hook(self, "on_victory")
        low, high = GOLD_ELITE_RANGE if self.combat_elite else GOLD_COMBAT_RANGE
        gold_gain = random.randint(low, high)
        self.player["gold"] += gold_gain
        defeated_name = self.enemy["name"]
        was_elite = self.combat_elite
        was_boss = self.combat_boss
        self.enemy = None

        if was_boss:
            if self.act == 3:
                self.state = "victory"
                self.log_text = f"{defeated_name} falls. You conquer Chad Math vs. The Forces of the Math Spire!"
                return
            self.boss_act = self.act
            self.pending_reward_stages = ["boss_card", "boss_relic"]
            self.start_victory_quiz(act_transition=True)
            self.log_text = f"{defeated_name} falls. Real Chads don't use calculators or paper. Head math or nothing."
            return

        self.log_text = f"{defeated_name} falls! (+{gold_gain} gold) Choose your reward."
        self.pending_reward_stages = ["card"] + (["relic_choice"] if was_elite else [])
        self.advance_reward_stage()

    # -- rewards -------------------------------------------------------------

    def generate_card_reward_options(self, count=3):
        pool = [name for name in REWARDABLE_CARDS if name not in ULTIMATE_CARD_NAMES]
        random.shuffle(pool)
        picks = pool[:count]
        if random.random() < 0.45:
            status_pool = [name for name in STATUS_CARD_NAMES if name in CARD_LIBRARY and name not in picks]
            if status_pool and picks:
                picks[-1] = random.choice(status_pool)
        if not picks:
            picks = [random.choice(pool or list(STATUS_CARD_NAMES))]
        if "Pencil of Fortune" in self.player["relics"]:
            for i, name in enumerate(picks):
                upgraded = CARD_LIBRARY[name].get("upgrades_to")
                if upgraded:
                    picks[i] = upgraded
                    break
        return picks

    def generate_boss_card_reward_options(self, count=3):
        options = list(ULTIMATE_CARD_NAMES)
        random.shuffle(options)
        return options[:min(count, len(options))]

    def generate_ultimate_reward_options(self, count=1):
        options = list(ULTIMATE_CARD_NAMES)
        random.shuffle(options)
        return options[:count]

    def start_victory_quiz(self, act_transition=False):
        self.quiz_attempts = 0
        self.quiz_input = ""
        self.quiz_is_act_transition = act_transition
        a = random.randint(2, 12)
        b = random.randint(2, 12)
        c = random.randint(1, 9)
        answer = a * b + c
        expr = f"{a} × {b} + {c} = ?"
        self.quiz_equation = {"text": expr, "answer": answer}
        self.quiz_answer = answer
        self.state = "victory"
        self.log_text = "Real Chads don't use calculators or paper. Head math or nothing."

    def submit_quiz_answer(self, raw_answer):
        if self.state != "victory" or not self.quiz_equation:
            return
        self.quiz_attempts += 1
        try:
            answer = int(raw_answer)
        except (TypeError, ValueError):
            answer = None
        if answer == self.quiz_answer:
            self.player["hp"] = self.player["max_hp"]
            relic_pool = [name for name in RELIC_NAMES if name not in self.player["relics"]]
            relic = random.choice(relic_pool) if relic_pool else "Golden Ratio"
            self.apply_relic(relic)
            self.log_text = f"Correct! The tower reveals {relic}."
            self.quiz_equation = None
            if self.quiz_is_act_transition:
                self.quiz_is_act_transition = False
                self.advance_reward_stage()
            return True
        if self.quiz_attempts >= 3:
            self.log_text = "The equation remains unsolved. The Spire withholds its extra relic."
            self.quiz_equation = None
            if self.quiz_is_act_transition:
                self.quiz_is_act_transition = False
                self.advance_reward_stage()
            return False
        self.log_text = f"Incorrect. {3 - self.quiz_attempts} attempt(s) remaining."
        return False

    def advance_reward_stage(self):
        if not self.pending_reward_stages:
            if self.boss_act and self.boss_act < 3:
                self.boss_act = None
                self.begin_next_act()
            elif self.boss_act == 3:
                self.boss_act = None
                self.state = "victory"
                self.log_text = "The final boss is defeated. The Spire is yours."
            else:
                self.return_to_map()
            return
        stage = self.pending_reward_stages.pop(0)
        if stage in ("card", "boss_card"):
            self.state = "card_reward"
            self.card_reward_options = self.generate_boss_card_reward_options() if stage == "boss_card" else self.generate_card_reward_options()
        elif stage in ("relic_choice", "boss_relic"):
            options = self.generate_relic_choice_options(2)
            if not options:
                self.advance_reward_stage()
                return
            self.state = "relic_choice"
            self.relic_choice_options = options

    def choose_card_reward(self, card_name):
        if card_name:
            self.draw_pile.insert(0, card_name)
            self.log_text = f"{card_name} added to your deck."
        self.advance_reward_stage()

    def generate_relic_choice_options(self, count=2):
        pool = [r for r in RELIC_NAMES if r not in self.player["relics"]]
        random.shuffle(pool)
        return pool[:count]

    def choose_relic_reward(self, relic_name):
        self.apply_relic(relic_name)
        self.advance_reward_stage()

    def resolve_treasure(self):
        pool = [r for r in RELIC_NAMES if r not in self.player["relics"]]
        if pool:
            relic = random.choice(pool)
            self.apply_relic(relic)
            gold_gain = random.randint(5, 15)
            self.player["gold"] += gold_gain
            self.log_text = f"Treasure! You found {relic} and {gold_gain} gold."
        else:
            gold_gain = random.randint(30, 45)
            self.player["gold"] += gold_gain
            self.log_text = f"Treasure! All relics already claimed — you find {gold_gain} gold instead."
        self.state = "treasure_result"

    # -- rest site -----------------------------------------------------------

    def rest_heal_amount(self):
        """Computes the campfire heal total (30% of max HP, floored, plus
        any relic modifiers) without applying it -- shared by rest_heal()
        (which actually heals) and draw_rest_choice() (which shows the
        player the exact "floor(<max_hp> * 0.30) = <amount> HP" math before
        they commit to the choice)."""
        base = max(1, math.floor(self.player["max_hp"] * 0.3))
        total = base
        for name in list(self.player["relics"]):
            fn = RELIC_LIBRARY.get(name, {}).get("hooks", {}).get("on_rest")
            if fn:
                total = fn(self, total)
        return base, total

    def rest_heal(self):
        base, total = self.rest_heal_amount()
        max_hp = self.player["max_hp"]
        healed = self.heal_player(total)
        formula = f"floor({max_hp} × 0.30) = {base} HP"
        if total != base:
            formula += f" (relics: +{total - base})"
        if healed < total:
            formula += f" — capped at {healed} (already near full HP)"
        self.log_text = f"Rest & Recompute: {formula}."
        self.return_to_map()

    def rest_upgrade_open(self):
        candidates = sorted({c for c in self.full_deck_cards() if CARD_LIBRARY[c].get("upgrades_to")})
        if not candidates:
            self.log_text = "Nothing left to study — every card is already upgraded."
            return
        self.rest_upgrade_options = candidates[:6]
        self.state = "rest_upgrade_pick"

    def rest_upgrade_choose(self, card_name):
        upgraded = CARD_LIBRARY[card_name]["upgrades_to"]
        for pile in (self.draw_pile, self.discard, self.hand):
            if card_name in pile:
                pile[pile.index(card_name)] = upgraded
                break
        self.log_text = f"{card_name} was upgraded to {upgraded}!"
        self.return_to_map()

    def rest_skip(self):
        self.log_text = "You press onward without resting."
        self.return_to_map()

    # -- shop ------------------------------------------------------------------

    def generate_shop_offer(self):
        relic_pool = [r for r in RELIC_NAMES if r not in self.player["relics"]]
        random.shuffle(relic_pool)
        card_pool = [name for name in REWARDABLE_CARDS if name not in STATUS_CARD_NAMES and name not in ULTIMATE_CARD_NAMES]
        random.shuffle(card_pool)

        offer = []
        for r in relic_pool[:2]:
            offer.append({"kind": "relic", "name": r, "cost": SHOP_PRICE_BY_RARITY.get(RELIC_LIBRARY[r]["rarity"], 70), "bought": False})
        for c in card_pool[:2]:
            offer.append({"kind": "card", "name": c, "cost": SHOP_PRICE_BY_RARITY.get(CARD_LIBRARY[c]["rarity"], 50), "bought": False})
        offer.append({"kind": "remove", "name": "Remove a Card", "cost": 40, "bought": False})
        return offer

    def buy_shop_item(self, index):
        if index < 0 or index >= len(self.shop_offer):
            return
        item = self.shop_offer[index]
        if item["bought"]:
            return
        if self.player["gold"] < item["cost"]:
            self.log_text = "Not enough gold for that."
            return

        if item["kind"] == "remove":
            if not self.full_deck_cards():
                self.log_text = "Your deck is empty."
                return
            self.player["gold"] -= item["cost"]
            item["bought"] = True
            self.remove_pick_page = 0
            self.state = "shop_remove_pick"
            return

        self.player["gold"] -= item["cost"]
        item["bought"] = True
        if item["kind"] == "relic":
            self.apply_relic(item["name"])
        elif item["kind"] == "card":
            self.discard.append(item["name"])
            self.log_text = f"Bought {item['name']}."

    def remove_card_instance(self, card_name):
        for pile in (self.draw_pile, self.discard, self.hand):
            if card_name in pile:
                pile.remove(card_name)
                self.log_text = f"Removed {card_name} from your deck."
                break
        self.state = "shop"

    def leave_shop(self):
        self.return_to_map()

    # -- events: "The Math Club Wagering Table" ---------------------------------

    def choose_safe_bet(self):
        """Stake 10 gold, roll 1d6. 3-6 wins 15 gold (net +5); 1-2 loses the
        stake (net -10). Theoretical E(V) = 0.00 gold -- a fair game. The
        stake is deducted immediately, but the roll itself is deferred
        behind a short die-tumble animation (see wager_pending /
        update_effects / WAGER_TUMBLE_SECONDS)."""
        if self.state != "event" or self.wager_pending is not None:
            return
        if self.player["gold"] < WAGER_SAFE_BET_STAKE_GOLD:
            self.log_text = f"Not enough gold to place the Chad Wager (need {WAGER_SAFE_BET_STAKE_GOLD})."
            return
        self.player["gold"] -= WAGER_SAFE_BET_STAKE_GOLD
        self.wager_pending = {"kind": "safe", "timer": WAGER_TUMBLE_SECONDS}
        self.log_text = "The dice tumble..."

    def choose_degenerate_bet(self):
        """Stake 15 HP, roll 1d20. 15-20 (30%) wins a Rare Relic or gold;
        1-14 (70%) costs the HP stake. High risk of ruin vs. a high-value
        reward -- the player has to weigh that tradeoff, not just chase raw
        E(V). The HP stake (and any resulting death) is resolved
        immediately; only the winning roll itself waits on the tumble."""
        if self.state != "event" or self.wager_pending is not None:
            return
        self.player["hp"] = max(0, self.player["hp"] - WAGER_DEGENERATE_STAKE_HP)
        if self.player["hp"] <= 0 and not any_lethal_save(self):
            self.event_outcome_label = "Giga Chad Wager — Ruin"
            self.log_text = f"You stake {WAGER_DEGENERATE_STAKE_HP} HP... and it's the last of it."
            self.state = "gameover"
            return
        self.wager_pending = {"kind": "degenerate", "timer": WAGER_TUMBLE_SECONDS}
        self.log_text = "The dice tumble..."

    def walk_away_from_wager(self):
        """No stake, no roll, no change -- just leave the table."""
        if self.state != "event" or self.wager_pending is not None:
            return
        self.log_text = "Coward Chad walks away from the shrine, coin and HP untouched."
        self.return_to_map()

    # -- per-frame effect bookkeeping -------------------------------------------

    def update_effects(self, dt=1 / 60):
        for item in self.floating_text:
            item["life"] -= dt
            item["y"] -= 1
        self.floating_text = [i for i in self.floating_text if i["life"] > 0]

        if self.hit_flash:
            self.hit_flash["timer"] -= dt
            if self.hit_flash["timer"] <= 0:
                self.hit_flash = None

        if self.turn_banner:
            self.turn_banner["timer"] -= dt
            if self.turn_banner["timer"] <= 0:
                self.turn_banner = None

        if self.dice_anim_timer > 0:
            self.dice_anim_timer = max(0.0, self.dice_anim_timer - dt)

        if self.wager_pending is not None:
            self.wager_pending["timer"] -= dt
            if self.wager_pending["timer"] <= 0:
                kind = self.wager_pending["kind"]
                self.wager_pending = None
                if kind == "safe":
                    _wager_safe_bet_roll(self)
                elif kind == "degenerate":
                    _wager_degenerate_bet_roll(self)

        if self.shake["timer"] > 0:
            self.shake["timer"] = max(0.0, self.shake["timer"] - dt)

        if self.lunge["timer"] > 0:
            self.lunge["timer"] = max(0.0, self.lunge["timer"] - dt)
            if self.lunge["timer"] <= 0:
                self.lunge["actor"] = None

        if self.power_aura["timer"] > 0:
            self.power_aura["timer"] = max(0.0, self.power_aura["timer"] - dt)
            if self.power_aura["timer"] <= 0:
                self.power_aura["actor"] = None

    def shake_offset(self):
        """Current screen-shake pixel offset, deterministic per-frame so it
        doesn't need a random import at render time."""
        t = self.shake["timer"]
        if t <= 0:
            return (0, 0)
        mag = self.shake["magnitude"]
        phase = t * 90
        return (int(mag * math.sin(phase)), int(mag * math.cos(phase * 1.3)))

    def lunge_offset(self, actor):
        """Returns (dx, dy) for the given actor ('player' or 'enemy') — a
        quick forward-and-back punch toward the opponent while an attack
        lands, 0 once the lunge has finished."""
        if self.lunge["actor"] != actor or self.lunge["timer"] <= 0:
            return (0, 0)
        duration = self.lunge["duration"]
        progress = 1 - (self.lunge["timer"] / duration)  # 0 -> 1 over the lunge
        # Ease out-and-back: punch forward in the first half, return in the second.
        punch = math.sin(min(1.0, progress) * math.pi)
        distance = 26 if actor == "player" else -26
        return (int(distance * punch), int(-6 * punch))


# ---------------------------------------------------------------------------
# Rendering — title / character select
# ---------------------------------------------------------------------------

def draw_title_screen(game):
    splash = ASSET_MANAGER.get_background("title")
    if splash is not None:
        # The real key-art splash (title text, both heroes, and the Spire
        # already painted into the image) -- just show it full-bleed and
        # overlay the Start prompt near the bottom, rather than redrawing
        # a redundant procedural headline/heroes/panel on top of it.
        screen.blit(splash, (0, 0))

        scrim_h = 150
        scrim = pygame.Surface((WIDTH, scrim_h), pygame.SRCALPHA)
        for i in range(scrim_h):
            alpha = int(190 * (i / scrim_h))
            pygame.draw.line(scrim, (6, 6, 12, alpha), (0, i), (WIDTH, i))
        screen.blit(scrim, (0, HEIGHT - scrim_h))

        btn = pygame.Rect(WIDTH // 2 - 150, HEIGHT - 108, 300, 60)
        hovered = btn.collidepoint(pygame.mouse.get_pos())
        draw_button(screen, btn, "Start Run", BLUE, hovered=hovered, icon="combat_swords")
        game.button_rects["start_run"] = btn

        prompt = FONT_SMALL.render("Press Enter / Space or click Start Run", True, MUTED)
        screen.blit(prompt, (WIDTH // 2 - prompt.get_width() // 2, HEIGHT - 40))
    else:
        draw_spire_background(screen)
        logo_img = get_art("game_logo", 150)
        if logo_img is not None:
            lw, lh = logo_img.get_size()
            screen.blit(logo_img, (WIDTH // 2 - lw // 2, 90))
        else:
            # "Chad Math vs. The Forces of the Math Spire" is too long to
            # fit FONT_TITLE on one line at this width -- split into a big
            # headline plus a smaller subtitle line (mirrors the reference
            # key-art: big "CHAD MATH", smaller "vs. The Forces of the
            # Math Spire" beneath).
            headline = FONT_TITLE.render("Chad Math", True, TEXT)
            screen.blit(headline, (WIDTH // 2 - headline.get_width() // 2, 96))
            tagline = FONT_H1.render("vs. The Forces of the Math Spire", True, GOLD)
            screen.blit(tagline, (WIDTH // 2 - tagline.get_width() // 2, 96 + headline.get_height() + 4))
        draw_math_warrior_sprite(screen, 100, 270, 2)
        draw_probability_wizard_sprite(screen, 940, 270, 2)

        panel = pygame.Rect(420, 260, 440, 240)
        pygame.draw.rect(screen, PANEL, panel, border_radius=18)
        pygame.draw.rect(screen, BLUE, panel, width=2, border_radius=18)
        label = FONT_H1.render("Enter the Derivative Dragon's Domain", True, TEXT)
        screen.blit(label, (panel.centerx - label.get_width() // 2, 300))
        screen.blit(FONT_BODY.render("The tower hums with despair at math puns.", True, MUTED), (456, 350))

        btn = pygame.Rect(500, 395, 280, 60)
        hovered = btn.collidepoint(pygame.mouse.get_pos())
        draw_button(screen, btn, "Start Run", BLUE, hovered=hovered, icon="combat_swords")
        game.button_rects["start_run"] = btn

    credit = FONT_TINY.render(
        "Icons: Twemoji by Twitter, Inc — CC-BY 4.0.  Fonts: Cinzel & EB Garamond — SIL OFL.",
        True, DIM,
    )
    cr = credit.get_rect(centerx=WIDTH // 2, y=HEIGHT - 26)
    if splash is None:
        screen.blit(credit, cr)

    draw_settings_button(game)


def draw_settings_button(game):
    """Small corner button, shown on the title screen, that opens the pause
    menu (window size / fullscreen / quit — see draw_pause_menu). Escape
    does the same thing from anywhere else in the game."""
    settings_btn = pygame.Rect(WIDTH - 160, 18, 144, 38)
    hovered = settings_btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, settings_btn, "Settings [Esc]", DIM, hovered=hovered, font=FONT_SMALL)
    game.button_rects["open_settings"] = settings_btn


def draw_intro(game):
    panel = draw_center_panel("Before You Ascend", None, GOLD, pygame.Rect(230, 90, 820, 540))
    blit_icon(screen, "boss_skull", (WIDTH // 2, panel.y + 132), 70)
    intro_lines = [
        "Choose a path through the Spire and survive its strange math.",
        "Fight enemies by playing cards, rolling dice, and managing energy.",
        "Unknown rooms can change your run. Relics permanently bend the rules.",
        "Climb floor by floor, build your deck, and solve the final equation.",
    ]
    y = panel.y + 190
    for line in intro_lines:
        text = FONT_BODY.render(line, True, TEXT)
        screen.blit(text, (panel.centerx - text.get_width() // 2, y))
        y += 34
    continue_btn = pygame.Rect(WIDTH // 2 - 150, panel.bottom - 78, 300, 54)
    draw_button(screen, continue_btn, "Choose Your Warrior", GOLD, hovered=continue_btn.collidepoint(pygame.mouse.get_pos()), icon="combat_swords")
    game.button_rects["continue_intro"] = continue_btn


_METAL_TEXT_CACHE = {}


def render_metal_text(text, font, max_width=None, top_color=(232, 236, 242), bottom_color=(104, 110, 122),
                       outline_color=(8, 8, 10), glow_color=(126, 14, 18)):
    """A brushed-steel fill, heavy black outline, and blood-red under-glow --
    a thrash-metal-album-cover treatment for display text, built procedurally
    on top of the existing Cinzel Decorative Black face rather than a new
    font asset (no actual metal/gothic font ships with the game, and this
    avoids reproducing a specific band's trademarked logo lettering).
    Cached per (text, font, max_width) since building it -- stamping the
    outline, painting the gradient, masking it to the glyph shape -- is too
    expensive to redo every frame for a string that never changes."""
    key = (text, id(font), max_width)
    cached = _METAL_TEXT_CACHE.get(key)
    if cached is not None:
        return cached

    upper = text.upper()
    mask = font.render(upper, True, (255, 255, 255))
    w, h = mask.get_size()
    pad = 6

    surf = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)

    # Heavy black outline: stamp the glyph at a ring of offsets around it.
    outline_glyph = font.render(upper, True, outline_color)
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            if dx * dx + dy * dy > 9 or (dx == 0 and dy == 0):
                continue
            surf.blit(outline_glyph, (pad + dx, pad + dy))

    # Blood-red under-glow, offset like a hard drop shadow.
    glow_glyph = font.render(upper, True, glow_color)
    surf.blit(glow_glyph, (pad + 4, pad + 4))

    # Brushed-steel vertical gradient, masked down to the glyph shape.
    gradient = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        t = y / max(1, h - 1)
        col = tuple(int(top_color[i] + (bottom_color[i] - top_color[i]) * t) for i in range(3))
        pygame.draw.line(gradient, col, (0, y), (w, y))
    gradient.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surf.blit(gradient, (pad, pad))

    if max_width is not None and surf.get_width() > max_width:
        scale = max_width / surf.get_width()
        surf = pygame.transform.smoothscale(
            surf, (max(1, int(surf.get_width() * scale)), max(1, int(surf.get_height() * scale)))
        )

    _METAL_TEXT_CACHE[key] = surf
    return surf


def draw_character_select(game):
    draw_spire_background(screen)
    title = render_metal_text("Choose Your Chad Math Warrior", FONT_TITLE, max_width=WIDTH - 80)
    screen.blit(title, (WIDTH // 2 - title.get_size()[0] // 2, 50))
    mpos = pygame.mouse.get_pos()

    for idx, (name, data) in enumerate(CHARACTER_OPTIONS.items()):
        accent = BLUE if idx == 0 else GOLD
        rect = pygame.Rect(200 + idx * 430, 130, 380, 560)
        _rounded_gradient_body(screen, rect, (40, 46, 58), (24, 27, 35), border_radius=18)
        pygame.draw.rect(screen, accent, rect, width=2, border_radius=18)

        name_surf = FONT_H1.render(name, True, TEXT)
        screen.blit(name_surf, (rect.centerx - name_surf.get_size()[0] // 2, rect.y + 16))
        desc_lines = wrapped_text(FONT_SMALL, data["description"], rect.width - 40)
        for line_index, line in enumerate(render_lines(FONT_SMALL, desc_lines[:2])):
            screen.blit(line, (rect.centerx - line.get_width() // 2, rect.y + 48 + line_index * 17))

        stats_y = rect.y + 88
        blit_icon(screen, "hp_heart", (rect.x + 60, stats_y + 8), 18)
        screen.blit(FONT_SMALL.render(str(data["hp"]), True, GREEN), (rect.x + 74, stats_y))
        blit_icon(screen, "energy_bolt", (rect.x + 160, stats_y + 8), 18)
        screen.blit(FONT_SMALL.render(str(data["energy"]), True, BLUE), (rect.x + 174, stats_y))

        # Sprite stage — its own backdrop, nothing else drawn over it.
        stage = pygame.Rect(rect.x + 30, rect.y + 116, rect.width - 60, 180)
        stage_glow = pygame.Surface((stage.width, stage.height), pygame.SRCALPHA)
        pygame.draw.ellipse(stage_glow, (*accent, 35), pygame.Rect(20, stage.height - 46, stage.width - 40, 40))
        pygame.draw.rect(stage_glow, (255, 255, 255, 10), stage_glow.get_rect(), border_radius=16)
        screen.blit(stage_glow, stage.topleft)
        if name == "Math Warrior":
            draw_math_warrior_sprite(screen, stage.x + 100, stage.y + 34, 0.95, state="select")
        else:
            draw_probability_wizard_sprite(screen, stage.x + 90, stage.y + 34, 0.95, state="select")

        screen.blit(FONT_BODY.render("Playstyle", True, GOLD), (rect.x + 26, rect.y + 310))
        lines = wrapped_text(FONT_SMALL, data["role_blurb"], rect.width - 52)
        y = rect.y + 340
        for line in render_lines(FONT_SMALL, lines[:3]):
            screen.blit(line, (rect.x + 26, y))
            y += 20

        # Relic block gets its own dedicated strip well above the Select
        # button (which used to sit right on top of it, hiding the starting
        # relic entirely) — a subtle backing panel makes it read as a
        # distinct "starting relic" callout too.
        relic_y = rect.y + 415
        relic_panel = pygame.Rect(rect.x + 18, relic_y - 6, rect.width - 36, 64)
        # Regression fix: this used to draw straight onto `screen`, which is
        # a plain (non-SRCALPHA) Surface -- pygame silently drops the alpha
        # channel on a draw like that, so the intended faint white tint
        # rendered as a solid opaque white box instead, washing out the
        # GOLD/MUTED text on top of it. Drawing onto its own SRCALPHA
        # surface first and blitting that actually respects the alpha, and
        # a dark tint (matching the rest of the card) gives real contrast
        # for the text rather than a near-white background.
        panel_tint = pygame.Surface((relic_panel.width, relic_panel.height), pygame.SRCALPHA)
        pygame.draw.rect(panel_tint, (8, 9, 14, 200), pygame.Rect(0, 0, relic_panel.width, relic_panel.height), border_radius=10)
        screen.blit(panel_tint, relic_panel.topleft)
        pygame.draw.rect(screen, accent, relic_panel, width=1, border_radius=10)
        blit_icon(screen, ICON_RELIC.get(data["starting_relic"]), (rect.x + 40, relic_y + 12), 26)
        screen.blit(FONT_TINY.render(f"Starts with: {data['starting_relic']}", True, GOLD), (rect.x + 62, relic_y + 4))
        relic_desc = wrapped_text(FONT_TINY, RELIC_LIBRARY[data["starting_relic"]]["description"], rect.width - 92)
        for line_index, line in enumerate(render_lines(FONT_TINY, relic_desc[:2], color=MUTED)):
            screen.blit(line, (rect.x + 62, relic_y + 22 + line_index * 14))

        # Select button now sits a clean gap below the relic panel instead
        # of on top of it (rect.bottom - 66 used to land squarely inside the
        # relic block, hiding the icon/name/description entirely).
        btn = pygame.Rect(rect.x + 80, rect.bottom - 77, 220, 52)
        hovered = btn.collidepoint(mpos)
        draw_button(screen, btn, "Select", accent, hovered=hovered)
        game.button_rects[f"select_{name}"] = btn


# ---------------------------------------------------------------------------
# Rendering — shared widgets
# ---------------------------------------------------------------------------

def draw_relic_panel(surface, game, x, y, width, show_tooltip=True):
    label = FONT_SMALL.render("Relics:", True, MUTED)
    surface.blit(label, (x, y))
    if not game.player["relics"]:
        surface.blit(FONT_TINY.render("none yet", True, DIM), (x + 62, y + 2))
        return

    mpos = pygame.mouse.get_pos()
    icon_size = 30
    gap = 8
    hovered_relic = None
    max_icons = max(1, (width - 70) // (icon_size + gap))
    for i, relic in enumerate(game.player["relics"][:max_icons]):
        cx = x + 70 + i * (icon_size + gap) + icon_size // 2
        cy = y + 12
        rarity = RELIC_LIBRARY.get(relic, {}).get("rarity", "common")
        color = RARITY_COLORS.get(rarity, MUTED)
        pygame.draw.circle(surface, PANEL_LIGHT, (cx, cy), icon_size // 2)
        pygame.draw.circle(surface, color, (cx, cy), icon_size // 2, 2)
        icon_name = ICON_RELIC.get(relic)
        if not blit_icon(surface, icon_name, (cx, cy), int(icon_size * 0.72)):
            letter = FONT_SMALL.render(relic[0], True, TEXT)
            lw, lh = letter.get_size() if hasattr(letter, "get_size") else (10, 14)
            surface.blit(letter, (cx - lw // 2, cy - lh // 2))
        hit_rect = pygame.Rect(cx - icon_size // 2, cy - icon_size // 2, icon_size, icon_size)
        if hit_rect.collidepoint(mpos):
            hovered_relic = relic

    if hovered_relic and show_tooltip:
        desc = RELIC_LIBRARY[hovered_relic]["description"]
        lines = wrapped_text(FONT_SMALL, f"{hovered_relic}: {desc}", 340)
        box_w, box_h = 360, 16 + 18 * len(lines)
        box_x = min(mpos[0] + 12, WIDTH - box_w - 10)
        box_y = min(mpos[1] + 12, HEIGHT - box_h - 10)
        box = pygame.Rect(box_x, box_y, box_w, box_h)
        pygame.draw.rect(surface, PANEL, box, border_radius=8)
        pygame.draw.rect(surface, GOLD, box, width=1, border_radius=8)
        yy = box.y + 6
        for line in render_lines(FONT_TINY, lines):
            surface.blit(line, (box.x + 10, yy))
            yy += 17


def draw_powers_row(surface, game, x, y, width):
    """Boxed HUD row for the player's active powers (Central Limit Theorem,
    Law of Large Numbers, etc.) — deliberately built to mirror
    draw_relic_panel exactly (same "<Label>:" text, same icon style, same
    "none yet" placeholder, same hover tooltip) so it reads as an obvious,
    permanent HUD element next to the relics panel rather than something
    that only briefly flashes on-screen. Replaces the old plain-text
    "Powers: X, Y" line that used to sit at the bottom of the card hand."""
    label = FONT_SMALL.render("Powers:", True, MUTED)
    surface.blit(label, (x, y))
    powers = game.player.get("active_powers", [])
    if not powers:
        surface.blit(FONT_TINY.render("none yet", True, DIM), (x + 74, y + 2))
        return

    mpos = pygame.mouse.get_pos()
    icon_size = 30
    gap = 8
    cy = y + 12
    max_icons = max(1, (width - 74) // (icon_size + gap))
    hovered_power = None
    for i, power_name in enumerate(powers[:max_icons]):
        cx = x + 74 + i * (icon_size + gap) + icon_size // 2
        pygame.draw.circle(surface, PANEL_LIGHT, (cx, cy), icon_size // 2)
        pygame.draw.circle(surface, PURPLE, (cx, cy), icon_size // 2, 2)
        if not blit_icon(surface, "power_sparkles", (cx, cy), int(icon_size * 0.72)):
            letter = FONT_SMALL.render(power_name[0], True, TEXT)
            lw, lh = letter.get_size() if hasattr(letter, "get_size") else (10, 14)
            surface.blit(letter, (cx - lw // 2, cy - lh // 2))
        hit_rect = pygame.Rect(cx - icon_size // 2, cy - icon_size // 2, icon_size, icon_size)
        if hit_rect.collidepoint(mpos):
            hovered_power = power_name

    if hovered_power:
        desc = POWER_LIBRARY[hovered_power]["description"]
        lines = wrapped_text(FONT_SMALL, f"{hovered_power}: {desc}", 340)
        box_w, box_h = 360, 16 + 18 * len(lines)
        box_x = min(mpos[0] + 12, WIDTH - box_w - 10)
        box_y = min(mpos[1] + 12, HEIGHT - box_h - 10)
        box = pygame.Rect(box_x, box_y, box_w, box_h)
        pygame.draw.rect(surface, PANEL, box, border_radius=8)
        pygame.draw.rect(surface, PURPLE, box, width=1, border_radius=8)
        yy = box.y + 6
        for line in render_lines(FONT_TINY, lines):
            surface.blit(line, (box.x + 10, yy))
            yy += 17


def draw_player_power_badges(surface, game, stage):
    """Small glowing badges for the player's active powers, pinned to the
    top-left corner of their sprite stage -- directly on the character, so
    the fact a power is still active for the rest of combat is impossible
    to miss (not just tucked into the top HUD strip). This is deliberately
    a SECOND indicator layered on top of draw_powers_row()'s HUD panel,
    not a replacement -- players reported still not noticing the HUD-panel
    version, so this puts an unmissable glowing badge right on the
    character itself as well."""
    powers = game.player.get("active_powers", [])
    if not powers:
        return
    mpos = pygame.mouse.get_pos()
    icon_size = 34
    gap = 6
    hovered_power = None
    for i, power_name in enumerate(powers):
        cx = stage.x + 26 + i * (icon_size + gap)
        cy = stage.y + 24
        glow = pygame.Surface((icon_size + 16, icon_size + 16), pygame.SRCALPHA)
        pygame.draw.circle(glow, (*PURPLE, 90), (glow.get_width() // 2, glow.get_height() // 2), icon_size // 2 + 6)
        surface.blit(glow, (cx - glow.get_width() // 2, cy - glow.get_height() // 2))
        pygame.draw.circle(surface, PANEL_LIGHT, (cx, cy), icon_size // 2)
        pygame.draw.circle(surface, PURPLE, (cx, cy), icon_size // 2, 3)
        if not blit_icon(surface, "power_sparkles", (cx, cy), int(icon_size * 0.72)):
            letter = FONT_HEADER.render(power_name[0], True, TEXT)
            lw, lh = letter.get_size() if hasattr(letter, "get_size") else (10, 14)
            surface.blit(letter, (cx - lw // 2, cy - lh // 2))
        hit_rect = pygame.Rect(cx - icon_size // 2, cy - icon_size // 2, icon_size, icon_size)
        if hit_rect.collidepoint(mpos):
            hovered_power = power_name

    if hovered_power:
        desc = POWER_LIBRARY[hovered_power]["description"]
        lines = wrapped_text(FONT_SMALL, f"{hovered_power}: {desc}", 340)
        box_w, box_h = 360, 16 + 18 * len(lines)
        box_x = min(mpos[0] + 12, WIDTH - box_w - 10)
        box_y = min(mpos[1] + 12, HEIGHT - box_h - 10)
        box = pygame.Rect(box_x, box_y, box_w, box_h)
        pygame.draw.rect(surface, PANEL, box, border_radius=8)
        pygame.draw.rect(surface, PURPLE, box, width=1, border_radius=8)
        yy = box.y + 6
        for line in render_lines(FONT_TINY, lines):
            surface.blit(line, (box.x + 10, yy))
            yy += 17


def render_floating_texts(game):
    for item in game.floating_text:
        text = FONT_BODY.render(item["text"], True, item["color"])
        screen.blit(text, (item["x"], item["y"]))


def draw_center_panel(title, subtitle=None, color=GOLD, rect=None, background_fn=None, panel_alpha=255, dim_alpha=130):
    """panel_alpha/dim_alpha let a specific screen show more of its backdrop
    through the panel body (e.g. the rest site, so the campfire art behind
    it stays visible) without changing every other screen that shares this
    widget -- both default to fully opaque, matching the original look."""
    (background_fn or draw_spire_background)(screen)
    if dim_alpha > 0:
        dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        pygame.draw.rect(dim, (6, 6, 12, dim_alpha), pygame.Rect(0, 0, WIDTH, HEIGHT))
        screen.blit(dim, (0, 0))

    panel = rect or pygame.Rect(200, 120, 880, 480)
    _rounded_gradient_body(screen, panel, (38, 44, 56, panel_alpha), (22, 25, 33, panel_alpha), border_radius=20)
    pygame.draw.rect(screen, color, panel, width=3, border_radius=20)
    pygame.draw.rect(screen, tuple(min(255, c + 40) for c in color), panel.inflate(-6, -6), width=1, border_radius=17)
    # Small corner flourishes, echoing engraved-stone framing.
    for cx, cy in ((panel.left + 14, panel.top + 14), (panel.right - 14, panel.top + 14),
                   (panel.left + 14, panel.bottom - 14), (panel.right - 14, panel.bottom - 14)):
        pygame.draw.circle(screen, color, (cx, cy), 3)

    title_surf = FONT_H1.render(title, True, TEXT)
    screen.blit(title_surf, (WIDTH // 2 - title_surf.get_size()[0] // 2, panel.y + 26))
    underline_w = title_surf.get_size()[0] + 40
    pygame.draw.line(screen, color, (WIDTH // 2 - underline_w // 2, panel.y + 62), (WIDTH // 2 + underline_w // 2, panel.y + 62), 1)
    if subtitle:
        sub_surf = FONT_BODY.render(subtitle, True, MUTED)
        screen.blit(sub_surf, (WIDTH // 2 - sub_surf.get_size()[0] // 2, panel.y + 74))
    return panel


def draw_button(surface, rect, label, base_color, hovered=False, text_color=None, icon=None, font=None, disabled=False):
    """Shared gradient/rounded button widget used across every menu so the
    whole game has one consistent, high-quality button look."""
    font = font or FONT_HEADER
    lift = 3 if hovered and not disabled else 0
    body = pygame.Rect(rect.x, rect.y - lift, rect.width, rect.height)
    radius = max(8, int(body.height * 0.24))

    if disabled:
        top, bottom = (46, 48, 54), (30, 32, 38)
    else:
        top = tuple(min(255, int(c * 1.18) + 10) for c in base_color)
        bottom = tuple(max(0, int(c * 0.72)) for c in base_color)
    _rounded_gradient_body(surface, body, top, bottom, border_radius=radius)
    border_color = WHITE if (hovered and not disabled) else tuple(max(0, c - 30) for c in base_color)
    pygame.draw.rect(surface, border_color, body, width=2 if hovered else 1, border_radius=radius)

    if hovered and not disabled:
        glow = pygame.Surface((body.width + 10, body.height + 10), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*WHITE, 40), glow.get_rect(), width=4, border_radius=radius + 4)
        surface.blit(glow, (body.x - 5, body.y - 5))

    luminance = 0.299 * base_color[0] + 0.587 * base_color[1] + 0.114 * base_color[2]
    tc = text_color or (DIM if disabled else BLACK if luminance > 165 else TEXT)
    label_surf = font.render(label, True, tc)
    lw, lh = label_surf.get_size()
    icon_w = 0
    if icon:
        icon_w = int(lh * 1.1)
    total_w = lw + (icon_w + 8 if icon else 0)
    start_x = body.centerx - total_w // 2
    if icon:
        blit_icon(surface, icon, (start_x + icon_w // 2, body.centery), icon_w)
        start_x += icon_w + 8
    surface.blit(label_surf, (start_x, body.centery - lh // 2))
    return body


# ---------------------------------------------------------------------------
# Rendering — the branching map
# ---------------------------------------------------------------------------

NODE_RADIUS = 26

ICON_INTENT = {"attack": "combat_swords", "burst": "impact_boom", "block": "skill_shield"}
INTENT_COLOR = {"attack": RED, "burst": ORANGE, "block": GREEN}


def draw_map(game):
    bg = ASSET_MANAGER.get_background("map")
    if bg is not None:
        screen.blit(bg, (0, 0))
    else:
        draw_spire_background(screen)
    header = FONT_H1.render(f"Act {game.act}  •  The Ascent — Row {game.floor}", True, TEXT)
    screen.blit(header, (50, 24))
    draw_relic_panel(screen, game, 50, 64, 560)
    screen.blit(FONT_BODY.render(f"Gold: {game.player['gold']}", True, GOLD), (820, 26))
    screen.blit(FONT_BODY.render(f"HP: {game.player['hp']}/{game.player['max_hp']}", True, GREEN), (820, 50))
    prompt = FONT_SMALL.render("Choose a route  •  arrows point toward the next floor", True, GOLD)
    screen.blit(prompt, (WIDTH // 2 - prompt.get_width() // 2, 86))

    mpos = pygame.mouse.get_pos()
    available = game.available_node_ids()

    for row in game.map_rows:
        for node in row:
            for target_id in game.map_edges.get(node["id"], ()):
                target = find_node(game.map_rows, target_id)
                if not target:
                    continue
                visited_edge = node["id"] in game.visited_node_ids and target["id"] in game.visited_node_ids
                lit_edge = node["id"] == game.current_node_id and target["id"] in available
                if visited_edge:
                    color, width = (220, 174, 72), 4
                elif lit_edge:
                    color, width = (255, 214, 82), 5
                else:
                    color, width = (78, 88, 104), 2

                start = pygame.Vector2(node["x"], node["y"])
                end = pygame.Vector2(target["x"], target["y"])
                direction = end - start
                distance = direction.length()
                if distance <= 0:
                    continue
                direction.normalize_ip()
                start += direction * (NODE_RADIUS - 2)
                end -= direction * (NODE_RADIUS + 7)
                pygame.draw.line(screen, (18, 22, 30), start, end, width + 5)
                pygame.draw.line(screen, color, start, end, width)

                arrow_size = 9 if lit_edge else 7
                side = pygame.Vector2(-direction.y, direction.x)
                arrow_base = end - direction * arrow_size
                arrow_points = [
                    end,
                    arrow_base + side * (arrow_size * 0.62),
                    arrow_base - side * (arrow_size * 0.62),
                ]
                pygame.draw.polygon(screen, color, arrow_points)

    hovered_node = None
    for row in game.map_rows:
        for node in row:
            vs = game.node_visual_state(node)
            base_color = NODE_COLORS.get(node["type"], MUTED)
            rect = pygame.Rect(node["x"] - NODE_RADIUS, node["y"] - NODE_RADIUS, NODE_RADIUS * 2, NODE_RADIUS * 2)
            hovered = rect.collidepoint(mpos)
            if hovered:
                hovered_node = node

            if vs == "locked":
                pygame.draw.circle(screen, DIM, (node["x"], node["y"]), NODE_RADIUS)
                pygame.draw.circle(screen, (70, 76, 84), (node["x"], node["y"]), NODE_RADIUS, 2)
                icon_color = (90, 96, 104)
            elif vs == "visited":
                pygame.draw.circle(screen, (46, 54, 62), (node["x"], node["y"]), NODE_RADIUS)
                pygame.draw.circle(screen, base_color, (node["x"], node["y"]), NODE_RADIUS, 2)
                icon_color = base_color
            elif vs == "current":
                pygame.draw.circle(screen, base_color, (node["x"], node["y"]), NODE_RADIUS + 5)
                pygame.draw.circle(screen, WHITE, (node["x"], node["y"]), NODE_RADIUS + 5, 3)
                icon_color = BLACK
            else:  # available
                r = NODE_RADIUS + (3 if hovered else 0)
                pygame.draw.circle(screen, base_color, (node["x"], node["y"]), r)
                pygame.draw.circle(screen, WHITE, (node["x"], node["y"]), r, 2)
                icon_color = BLACK
                game.button_rects[f"node_{node['id']}"] = rect

            icon_name = ICON_NODE.get(node["type"])
            if not blit_icon(screen, icon_name, (node["x"], node["y"]), int(NODE_RADIUS * 1.15)):
                icon_surf = FONT_HEADER.render(node["type"][:1].upper(), True, icon_color)
                iw, ih = icon_surf.get_size() if hasattr(icon_surf, "get_size") else (14, 18)
                screen.blit(icon_surf, (node["x"] - iw // 2, node["y"] - ih // 2))
            if vs == "visited":
                screen.blit(FONT_TINY.render("cleared", True, GOLD), (node["x"] - 20, node["y"] + NODE_RADIUS + 4))

    if hovered_node:
        label = f"{hovered_node['name']}"
        tip = FONT_SMALL.render(label, True, TEXT)
        tw, th = tip.get_size() if hasattr(tip, "get_size") else (60, 16)
        tip_rect = pygame.Rect(mpos[0] + 14, mpos[1] - 8, tw + 16, th + 12)
        pygame.draw.rect(screen, PANEL, tip_rect, border_radius=8)
        pygame.draw.rect(screen, GOLD, tip_rect, width=1, border_radius=8)
        screen.blit(tip, (tip_rect.x + 8, tip_rect.y + 6))

    legend_y = 660
    legend_x = 50
    for kind in ("combat", "elite", "event", "rest", "shop", "treasure", "boss"):
        pygame.draw.circle(screen, NODE_COLORS[kind], (legend_x, legend_y), 9)
        if not blit_icon(screen, ICON_NODE.get(kind), (legend_x, legend_y), 12):
            pass
        label = FONT_TINY.render(NODE_LABELS[kind], True, MUTED)
        screen.blit(label, (legend_x + 14, legend_y - 8))
        legend_x += 120


# ---------------------------------------------------------------------------
# Rendering — combat
# ---------------------------------------------------------------------------

def draw_battle_log(game, log_box):
    """Always-visible scrolling combat log for the middle of the combat
    screen. Shows the tail of game.combat_log (which already records every
    event, including each individual die from a multi-roll card like
    Probability Missile — see roll_dice/roll_dice_multi) so the player can
    see what happened this turn and last turn without opening the full
    Run Log (L). Auto-scrolls: it always shows the newest entries at the
    bottom, growing upward as new events happen."""
    pygame.draw.rect(screen, PANEL, log_box, border_radius=14)
    pygame.draw.rect(screen, tuple(max(0, c - 14) for c in PANEL_LIGHT), log_box, width=1, border_radius=14)
    screen.blit(FONT_SMALL.render("Battle Log", True, MUTED), (log_box.x + 16, log_box.y + 10))
    pygame.draw.line(screen, (255, 255, 255, 30), (log_box.x + 14, log_box.y + 32), (log_box.right - 14, log_box.y + 32), 1)

    content_top = log_box.y + 40
    content_bottom = log_box.bottom - 10
    line_h = 19
    max_lines = max(1, (content_bottom - content_top) // line_h)
    recent = game.combat_log[-max_lines:]
    y = content_top
    for i, entry in enumerate(recent):
        short_lines = wrapped_text(FONT_TINY, entry, log_box.width - 32)
        text = short_lines[0] if short_lines else entry
        if len(short_lines) > 1 and text:
            text = text.rstrip() + "…"
        is_latest = i == len(recent) - 1
        color = GOLD if is_latest else MUTED
        screen.blit(FONT_TINY.render(text, True, color), (log_box.x + 16, y))
        y += line_h


POWER_AURA_THEME = {
    "Math Warrior": {"art": "power_aura_warrior", "core": (255, 214, 120), "ring": (255, 170, 60), "spark": (255, 240, 200)},
    "Probability Wizard": {"art": "power_aura_wizard", "core": (210, 150, 255), "ring": (150, 90, 230), "spark": (230, 200, 255)},
}


def draw_power_aura(surface, character, cx, cy, height, progress):
    """DBZ-style charge-up aura, played for ~2s (see gain_power()) whenever
    the player plays a power card. Layers a real power_aura_warrior/wizard.png
    image (see the note further down for how its transparency was produced)
    over procedural pulsing rings + rising ki-sparks, so the effect reads
    clearly even if that art is ever missing/swapped out.

    `progress` is 0..1 across the full duration; intensity ramps up fast,
    holds, then fades in the last ~20%.
    """
    theme = POWER_AURA_THEME.get(character, POWER_AURA_THEME["Math Warrior"])
    if progress < 0.15:
        intensity = progress / 0.15
    elif progress > 0.8:
        intensity = max(0.0, (1.0 - progress) / 0.2)
    else:
        intensity = 1.0
    if intensity <= 0:
        return

    pulse = 0.75 + 0.25 * math.sin(progress * 26.0)
    radius_base = height * 0.62

    # Concentric pulsing rings, outermost faintest.
    ring_layer = pygame.Surface((int(height * 2.2), int(height * 2.2)), pygame.SRCALPHA)
    lcx, lcy = ring_layer.get_width() // 2, ring_layer.get_height() // 2
    for i, mult in enumerate((0.55, 0.78, 1.0)):
        r = int(radius_base * mult * pulse)
        alpha = int(150 * intensity * (1.0 - i * 0.28))
        if r > 0 and alpha > 0:
            pygame.draw.circle(ring_layer, (*theme["ring"], alpha), (lcx, lcy), r, width=max(2, int(height * 0.03)))
    # Bright core glow.
    core_r = max(1, int(radius_base * 0.5 * pulse))
    pygame.draw.circle(ring_layer, (*theme["core"], int(90 * intensity)), (lcx, lcy), core_r)
    surface.blit(ring_layer, (cx - lcx, cy - lcy))

    # Rising jagged ki sparks shooting up past the character — the classic
    # DBZ "power surging off the body" silhouette lines.
    spark_count = 10
    for i in range(spark_count):
        seed = (i * 37 + int(progress * 997)) % 1000
        rng_local = random.Random(seed)
        angle = (i / spark_count) * math.tau + rng_local.uniform(-0.15, 0.15)
        rise = rng_local.uniform(0.5, 1.0) * pulse
        base_r = radius_base * rng_local.uniform(0.55, 0.85)
        bx = cx + math.cos(angle) * base_r * 0.5
        by = cy + math.sin(angle) * base_r * 0.35 + height * 0.25
        tip_x = bx + rng_local.uniform(-10, 10)
        tip_y = by - height * (0.5 + 0.6 * rise)
        alpha = int(200 * intensity * rng_local.uniform(0.5, 1.0))
        if alpha <= 0:
            continue
        spark_surf_color = (*theme["spark"], alpha)
        width = max(1, int(height * 0.018))
        pygame.draw.line(surface, spark_surf_color, (int(bx), int(by)), (int(tip_x), int(tip_y)), width)

    # Real art on top of the procedural rings/sparks. NOTE: the first version
    # of power_aura_warrior/wizard.png was salvaged from a flattened JPEG
    # collage with a checkerboard "transparent" mockup baked in — no real
    # alpha channel — and blitting it showed up in-game as a large,
    # not-actually-transparent block. That pair was replaced with images
    # generated on a solid black background instead and re-keyed properly
    # (alpha recovered from brightness, then the RGB un-premultiplied so
    # pygame's straight-alpha blit doesn't render them too dark), which
    # produces clean, real per-pixel transparency — verified with the
    # RenderingSmokeTests / PowerAuraAndShopUiTests smoke tests plus a visual
    # check against both light and dark backgrounds before shipping this.
    real_art = get_art(theme["art"], int(height * 1.4))
    if real_art is not None:
        real_art = real_art.copy()
        real_art.set_alpha(int(255 * intensity))
        surface.blit(real_art, (cx - real_art.get_width() // 2, cy - real_art.get_height() // 2))


def draw_combat(game):
    draw_combat_backdrop(game)
    mpos = pygame.mouse.get_pos()
    sx, sy = game.shake_offset()

    # -- Top HUD strip: relics + powers each get their own boxed panel
    #    (same background/border language) so both stay obvious HUD
    #    elements instead of icons floating loose over the backdrop, and
    #    neither collides with either character's status panel. -------------
    relic_strip = pygame.Rect(50 + sx, 16 + sy, 620, 42)
    pygame.draw.rect(screen, PANEL, relic_strip, border_radius=12)
    draw_relic_panel(screen, game, relic_strip.x + 14, relic_strip.y + 6, relic_strip.width - 20, show_tooltip=False)

    powers_strip = pygame.Rect(680 + sx, 16 + sy, 300, 42)
    pygame.draw.rect(screen, PANEL, powers_strip, border_radius=12)
    draw_powers_row(screen, game, powers_strip.x + 14, powers_strip.y + 6, powers_strip.width - 20)

    # -- Player status panel (left) and enemy status panel (right). Neither
    #    overlaps the sprite stages below them, nor the center dice/log
    #    column, so nothing ever draws over a portrait. ---------------------
    player_panel = pygame.Rect(50 + sx, 66 + sy, 330, 104)
    pygame.draw.rect(screen, PANEL, player_panel, border_radius=12)
    if game.hit_flash and game.hit_flash["target"] == "player":
        flash = pygame.Surface((player_panel.width, player_panel.height), pygame.SRCALPHA)
        pygame.draw.rect(flash, (255, 60, 60, 90), pygame.Rect(0, 0, player_panel.width, player_panel.height), border_radius=12)
        screen.blit(flash, player_panel.topleft)
    px, py = player_panel.x, player_panel.y
    screen.blit(FONT_HEADER.render(game.character, True, TEXT), (px + 14, py + 8))
    # Bar is narrower than the panel on purpose: it must stay clear of the
    # energy/block badge column on the right (starting ~px+236), or the
    # badges draw on top of the bar's tail end.
    player_bar = pygame.Rect(px + 14, py + 38, 190, 16)
    render_health_bar_with_block(screen, player_bar, game.player["hp"], game.player["max_hp"], game.player["block"], GREEN, (60, 76, 86))
    screen.blit(FONT_SMALL.render(f"HP {game.player['hp']}/{game.player['max_hp']}", True, TEXT), (px + 14, py + 58))
    blit_icon(screen, "energy_bolt", (px + 250, py + 42), 20)
    # effective_max folds in any per-turn relic bonus (Abacus Charm) so
    # current energy never reads as exceeding the displayed max -- e.g.
    # Probability Wizard used to show "4/3" here (current > max looks like
    # a bug); it now shows "4/4" plus a small "+1" tag naming the relic
    # that's responsible, instead of silently hiding where the bonus came
    # from behind a bigger flat number.
    energy_bonus = energy_bonus_from_relics(game)
    effective_max = game.player["max_energy"] + energy_bonus
    energy_surf = FONT_SMALL.render(f"{game.player['energy']}/{effective_max}", True, BLUE)
    screen.blit(energy_surf, (px + 264, py + 34))
    if energy_bonus:
        bonus_surf = FONT_TINY.render(f"+{energy_bonus}", True, GREEN)
        screen.blit(bonus_surf, (px + 264 + energy_surf.get_width() + 4, py + 38))
    if game.player["block"] > 0:
        blit_icon(screen, "skill_shield", (px + 250, py + 72), 18)
        screen.blit(FONT_SMALL.render(str(game.player["block"]), True, GOLD), (px + 264, py + 64))
    screen.blit(FONT_TINY.render(f"Gold {game.player['gold']}", True, GOLD), (px + 14, py + 82))
    player_status = "  ".join(f"{k.title()} {v}" for k, v in game.player.get("statuses", {}).items())
    if player_status:
        screen.blit(FONT_TINY.render(player_status, True, PURPLE), (px + 120, py + 82))

    # -- Player sprite stage: a dedicated backdrop with nothing else drawn
    #    on top of it. -------------------------------------------------------
    player_stage = pygame.Rect(40 + sx, 182 + sy, 340, 270)
    stage_glow = pygame.Surface((player_stage.width, player_stage.height), pygame.SRCALPHA)
    pygame.draw.ellipse(stage_glow, (90, 120, 200, 40), pygame.Rect(20, player_stage.height - 70, player_stage.width - 40, 60))
    pygame.draw.rect(stage_glow, (255, 255, 255, 10), stage_glow.get_rect(), border_radius=18)
    screen.blit(stage_glow, player_stage.topleft)
    lx, ly = game.lunge_offset("player")
    idle_bob = int(3 * math.sin(pygame.time.get_ticks() / 480.0))
    anchor_x, anchor_y = player_stage.x + 90 + lx, player_stage.y + 60 + ly + idle_bob
    # Default combat pose is State 2 (Combat Ready). Playing a Power/Surge
    # card or banking a huge dice roll (see Game.trigger_power_pose) swaps
    # to State 3 (Power-Up) for the duration of power_aura's timer, sharing
    # the same clock as the existing charge-up glow ring so the sprite swap
    # and the glow always start/end together.
    hero_state = "powerup" if (game.power_aura["actor"] == "player" and game.power_aura["timer"] > 0) else "ready"
    if game.character == "Math Warrior":
        draw_math_warrior_sprite(screen, anchor_x, anchor_y, 1.35, state=hero_state)
    else:
        draw_probability_wizard_sprite(screen, anchor_x, anchor_y, 1.35, state=hero_state)
    if game.lunge["actor"] == "player" and game.lunge["timer"] > 0:
        effect_name = "combat_effect_warrior" if game.character == "Math Warrior" else "combat_effect_wizard"
        effect = get_art(effect_name, 105)
        if effect is not None:
            effect = effect.copy()
            effect.set_alpha(150)
            screen.blit(effect, (player_stage.centerx - effect.get_width() // 2, player_stage.y + 78))
    if game.power_aura["actor"] == "player" and game.power_aura["timer"] > 0:
        aura_progress = 1.0 - (game.power_aura["timer"] / game.power_aura["duration"])
        draw_power_aura(screen, game.character, player_stage.centerx, player_stage.centery, player_stage.height, aura_progress)
    draw_player_power_badges(screen, game, player_stage)

    enemy = game.enemy
    enemy_panel = pygame.Rect(900 + sx, 66 + sy, 330, 104)
    enemy_stage = pygame.Rect(900 + sx, 182 + sy, 340, 270)
    if enemy is not None:
        pygame.draw.rect(screen, PANEL, enemy_panel, border_radius=12)
        if game.hit_flash and game.hit_flash["target"] == "enemy":
            flash = pygame.Surface((enemy_panel.width, enemy_panel.height), pygame.SRCALPHA)
            pygame.draw.rect(flash, (255, 60, 60, 90), pygame.Rect(0, 0, enemy_panel.width, enemy_panel.height), border_radius=12)
            screen.blit(flash, enemy_panel.topleft)
        ex, ey = enemy_panel.x, enemy_panel.y
        name_surf = FONT_HEADER.render(enemy["name"], True, TEXT)
        if name_surf.get_size()[0] > enemy_panel.width - 24:
            name_surf = FONT_BODY.render(enemy["name"], True, TEXT)
        screen.blit(name_surf, (ex + 14, ey + 8))
        enemy_bar = pygame.Rect(ex + 14, ey + 38, 190, 16)
        render_health_bar_with_block(screen, enemy_bar, enemy["hp"], enemy["max_hp"], enemy["block"], RED, (75, 42, 42))
        screen.blit(FONT_SMALL.render(f"HP {enemy['hp']}/{enemy['max_hp']}", True, TEXT), (ex + 14, ey + 58))
        if enemy["block"] > 0:
            blit_icon(screen, "skill_shield", (ex + 250, ey + 42), 18)
            screen.blit(FONT_SMALL.render(str(enemy["block"]), True, GOLD), (ex + 264, ey + 34))
        intent = enemy.get("intent") or {}
        itype = intent.get("type", "attack")
        color = INTENT_COLOR.get(itype, MUTED)
        if itype == "block":
            intent_label = f"Guarding ({intent.get('value', '?')})"
        else:
            raw_value = intent.get("value")
            if raw_value is None:
                intent_label = "? dmg"
            else:
                # Show what will actually land, not the raw pre-Weak
                # number -- otherwise Weak looks like it does nothing.
                predicted = weak_adjusted_damage(game, raw_value)
                intent_label = f"{predicted} dmg (Weak)" if predicted != raw_value else f"{raw_value} dmg"
        blit_icon(screen, ICON_INTENT.get(itype), (ex + 22, ey + 90), 18)
        screen.blit(FONT_SMALL.render(intent_label, True, color), (ex + 38, ey + 80))
        enemy_status = "  ".join(f"{k.title()} {v}" for k, v in enemy.get("statuses", {}).items())
        if enemy_status:
            screen.blit(FONT_TINY.render(enemy_status, True, PURPLE), (ex + 148, ey + 84))

        stage_glow2 = pygame.Surface((enemy_stage.width, enemy_stage.height), pygame.SRCALPHA)
        pygame.draw.ellipse(stage_glow2, (200, 90, 90, 40), pygame.Rect(20, enemy_stage.height - 70, enemy_stage.width - 40, 60))
        pygame.draw.rect(stage_glow2, (255, 255, 255, 10), stage_glow2.get_rect(), border_radius=18)
        screen.blit(stage_glow2, enemy_stage.topleft)
        elx, ely = game.lunge_offset("enemy")
        enemy_bob = int(3 * math.sin(pygame.time.get_ticks() / 480.0 + 1.7))
        eanchor_x, eanchor_y = enemy_stage.x + 90 + elx, enemy_stage.y + 60 + ely + enemy_bob
        if game.combat_boss:
            entrance_art = ENTRANCE_ART.get(enemy["name"]) if game.turn_number == 1 else None
            if entrance_art:
                art = get_art(entrance_art, 190)
                if art is not None:
                    screen.blit(art, (enemy_stage.centerx - art.get_width() // 2, enemy_stage.y + 10))
                else:
                    draw_boss_portrait(screen, enemy["name"], eanchor_x, eanchor_y, 1.15)
            else:
                draw_boss_portrait(screen, enemy["name"], eanchor_x, eanchor_y, 1.15)
        else:
            entrance_art = ENTRANCE_ART.get(enemy["name"]) if game.turn_number == 1 else None
            if entrance_art:
                art = get_art(entrance_art, 190)
                if art is not None:
                    screen.blit(art, (enemy_stage.centerx - art.get_width() // 2, enemy_stage.y + 10))
                else:
                    draw_enemy_portrait(screen, enemy["name"], eanchor_x, eanchor_y, 1.05)
            else:
                draw_enemy_portrait(screen, enemy["name"], eanchor_x, eanchor_y, 1.05)

    # -- Center column: dice core + battle log, in the gap between the two
    #    sprite stages so nothing is ever drawn over a portrait. ------------
    center_x = 400 + sx
    dice_box = pygame.Rect(center_x, 182 + sy, 480, 92)
    pygame.draw.rect(screen, PANEL, dice_box, border_radius=12)
    blit_icon(screen, "dice", (dice_box.x + 30, dice_box.y + 26), 24)
    screen.blit(FONT_HEADER.render("Dice Core", True, GOLD), (dice_box.x + 50, dice_box.y + 14))
    if game.dice_anim_timer and game.dice_anim_timer > 0 and game.dice_sides:
        dice_value = str(random.randint(1, game.dice_sides))
    else:
        dice_value = game.dice_roll if game.dice_roll is not None else "—"
    screen.blit(FONT_H1.render(str(dice_value), True, TEXT), (dice_box.x + 30, dice_box.y + 46))
    if game.dice_sides:
        screen.blit(FONT_SMALL.render(f"out of d{game.dice_sides}", True, MUTED), (dice_box.x + 90, dice_box.y + 58))

    draw_battle_log(game, pygame.Rect(center_x, 286 + sy, 480, 176))

    if game.push_luck_active:
        # Gambler's Flurry is mid-resolution: swap the hand/End Turn row
        # for the push-your-luck banner (Banked Damage / Next Roll E(V) +
        # Bank & Strike / Push Your Luck) until the player resolves it.
        draw_push_luck_banner(game, pygame.Rect(0, 468, WIDTH, 178))
    else:
        hand_y = 468
        card_w, card_h = 150, 172
        spacing = 14
        n = len(game.hand)
        total_w = n * card_w + max(0, n - 1) * spacing
        max_w = WIDTH - 40
        if n > 0 and total_w > max_w:
            # A big hand (extra draws, Infinite Series, etc.) would otherwise run
            # off the sides of the screen — shrink the cards uniformly to fit
            # instead, keeping their bottom edge anchored in place.
            scale = max(0.55, max_w / total_w)
            card_w, card_h = int(150 * scale), int(172 * scale)
            spacing = max(6, int(14 * scale))
            total_w = n * card_w + max(0, n - 1) * spacing
            hand_y = 468 + (172 - card_h)
        start_x = max(20, (WIDTH - total_w) // 2)
        hovered_card, hovered_rect = None, None
        for idx, card_name in enumerate(game.hand):
            rect = pygame.Rect(start_x + idx * (card_w + spacing), hand_y, card_w, card_h)
            hovered = rect.collidepoint(mpos)
            if hovered:
                hovered_card, hovered_rect = card_name, rect
            affordable = game.player["energy"] >= CARD_LIBRARY[card_name]["cost"]
            render_card(screen, card_name, rect, hovered=hovered, affordable=affordable, character=game.character, game=game)
            game.button_rects[f"card_{idx}"] = rect

        screen.blit(FONT_SMALL.render(f"Draw pile: {len(game.draw_pile)}", True, MUTED), (20, 646))
        screen.blit(FONT_SMALL.render(f"Discard: {len(game.discard)}", True, MUTED), (20, 666))

        # Full-text hover tooltip so a shrunk or truncated card's description is
        # always readable somewhere, even if it doesn't fit on the card itself.
        # The math numbers below the description here duplicate what's now
        # ALSO baked directly onto the card face by render_card()'s own
        # math footer (draw_card_math_footer(), scaled down at small sizes
        # rather than omitted) -- kept here too since the tooltip's roomier
        # layout can show full precision even when the card's own footer
        # had to abbreviate to a single compact line.
        if hovered_card is not None:
            card_data = CARD_LIBRARY[hovered_card]
            lines = wrapped_text(FONT_SMALL, f"{hovered_card} ({card_data['cost']} energy): {card_data['desc']}", 340)

            math_lines = []
            ev = card_ev(game, hovered_card)
            if ev is not None:
                math_lines.append(f"Base E(V) = {ev:.2f}")
                eff = card_cost_efficiency(game, hovered_card)
                if eff is not None:
                    math_lines.append(f"Cost Efficiency = {eff:.2f} DMG/E")
                elif card_data["cost"]:
                    math_lines.append(f"Cost Efficiency = {ev / card_data['cost']:.2f} DMG/E")
                recoil = card_recoil_ev(game, hovered_card)
                if recoil is not None:
                    math_lines.append(f"Recoil E(V) = {recoil:.2f}")

            box_w = 360
            box_h = 16 + 18 * len(lines) + (8 + 16 * len(math_lines) if math_lines else 0)
            box_x = min(max(10, hovered_rect.centerx - box_w // 2), WIDTH - box_w - 10)
            box_y = max(10, hovered_rect.y - box_h - 10)
            box = pygame.Rect(box_x, box_y, box_w, box_h)
            pygame.draw.rect(screen, PANEL, box, border_radius=8)
            pygame.draw.rect(screen, GOLD, box, width=1, border_radius=8)
            yy = box.y + 6
            for line in render_lines(FONT_TINY, lines):
                screen.blit(line, (box.x + 10, yy))
                yy += 17
            if math_lines:
                yy += 2
                pygame.draw.line(screen, GOLD, (box.x + 10, yy), (box.right - 10, yy), 1)
                yy += 6
                for line in math_lines:
                    screen.blit(FONT_TINY.render(line, True, GREEN), (box.x + 10, yy))
                    yy += 16

        end_turn = pygame.Rect(1080, 646, 150, 46)
        hovered = end_turn.collidepoint(mpos)
        draw_button(screen, end_turn, "End Turn", BLUE, hovered=hovered)
        game.button_rects["end_turn"] = end_turn

    if game.turn_banner:
        banner_text = FONT_TITLE.render(game.turn_banner["text"], True, GOLD)
        tw, th = banner_text.get_size() if hasattr(banner_text, "get_size") else (200, 40)
        bx, by = WIDTH // 2 - tw // 2, 130
        bg_box = pygame.Surface((tw + 40, th + 20), pygame.SRCALPHA)
        pygame.draw.rect(bg_box, (10, 10, 16, 160), pygame.Rect(0, 0, tw + 40, th + 20), border_radius=12)
        screen.blit(bg_box, (bx - 20, by - 10))
        screen.blit(banner_text, (bx, by))

    # Draw the relic tooltip last so the character status panels cannot cover it.
    draw_relic_panel(screen, game, relic_strip.x + 14, relic_strip.y + 6, relic_strip.width - 20)

    hint = FONT_TINY.render("[M] Math Inspector", True, DIM)
    screen.blit(hint, (50 + sx, HEIGHT - 26))
    draw_math_inspector(game)


def weak_adjusted_damage(game, raw_value):
    """Mirrors deal_damage()'s own Weak halving (floor(damage * 0.5), same
    0.5 multiplier, never stacked) so the enemy's telegraphed intent number
    matches what will actually land once Weak is in play, instead of
    showing the pre-Weak raw value and looking like Weak does nothing."""
    if game.enemy is not None and game.enemy.get("statuses", {}).get("weak", 0) > 0:
        return max(0, math.floor(raw_value * 0.5))
    if game.player.get("statuses", {}).get("weak", 0) > 0:
        return max(0, math.floor(raw_value * 0.5))
    return raw_value


# ---------------------------------------------------------------------------
# Math Inspector -- Press [M] in combat. Shows E(V) = Σ [ outcome_i * P(outcome_i) ]
# for the enemy's current intent and every damage card in hand, so a math
# class can see the actual probability math the game is running on, not
# just the flavor text.
# ---------------------------------------------------------------------------

def enemy_intent_ev(intent):
    """Returns (formula_str, value) for the enemy's current intent, or None
    if it deals no damage (block intents). The four new probabilistic
    archetypes get their real dice-probability formula spelled out; the
    older archetypes (block/burst/attack) already resolve their randomness
    once at the start of the player's turn (see roll_enemy_intent), so
    their "E(V)" is just that already-known value -- there's no remaining
    uncertainty left to average over."""
    if intent is None:
        return None
    t = intent["type"]
    if t == "block":
        return None
    if t == "decimal_shift":
        return ("E(V) = Σ(1..10)/10", 5.5)
    if t == "fractal_recursion":
        # 2d4 (E=5) plus a 1/4 chance (rolling doubles) of an extra 1d4 (E=2.5).
        return ("E(V) = 2d4 + 0.25×1d4 = 5 + 0.25×2.5", 5.625)
    if t == "vector_pierce":
        return ("E(V) = 3d4 = 3×2.5", 7.5)
    if t == "root_extraction":
        # 1d12; squares {1,4,9} (p=3/12) triple the roll, else flat roll.
        non_square = sum(d for d in range(1, 13) if d not in (1, 4, 9))
        square = sum(3 * d for d in (1, 4, 9))
        return ("E(V) = [Σ_non-square(d) + Σ_square(3d)] / 12", (non_square + square) / 12)
    if t == "chain_rule_breath":
        # 2d8 (+3 to every roll once L'Hopital's Rage has triggered), then
        # the same BOSS_DAMAGE_NERF applied to the actually-rolled value in
        # roll_enemy_intent() -- kept in sync here so the Math Inspector's
        # theoretical E(V) matches what the boss will really deal.
        bonus = intent.get("bonus", 0)
        raw = 9.0 + bonus
        return (
            f"E(V) = (2d8{f' + {bonus} rage' if bonus else ''}) × {BOSS_DAMAGE_NERF} boss nerf"
            f" = (2×4.5{f' + {bonus}' if bonus else ''}) × {BOSS_DAMAGE_NERF}",
            raw * BOSS_DAMAGE_NERF,
        )
    # Already-resolved intents (block/burst/attack): no distribution left to
    # average over, just show the known outcome.
    return (f"resolved this turn", float(intent.get("value", 0)))


def draw_math_inspector(game):
    if not game.show_math_inspector or game.state != "combat":
        return
    panel = pygame.Rect(WIDTH // 2 - 280, 120, 560, 280)
    bg = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
    pygame.draw.rect(bg, (12, 14, 22, 235), pygame.Rect(0, 0, panel.width, panel.height), border_radius=14)
    screen.blit(bg, panel.topleft)
    pygame.draw.rect(screen, BLUE, panel, width=2, border_radius=14)
    title = FONT_HEADER.render("Math Inspector", True, BLUE)
    screen.blit(title, (panel.x + 16, panel.y + 10))
    subtitle = FONT_TINY.render("E(V) = Σ [ outcome_i × P(outcome_i) ]", True, MUTED)
    screen.blit(subtitle, (panel.x + 16, panel.y + 34))

    y = panel.y + 60
    if game.enemy is not None:
        result = enemy_intent_ev(game.enemy.get("intent"))
        if result is not None:
            formula, value = result
            line = FONT_SMALL.render(f"{game.enemy['name']} intent: {formula} = {value:.2f}", True, GOLD)
            screen.blit(line, (panel.x + 16, y))
        else:
            line = FONT_SMALL.render(f"{game.enemy['name']} intent: no damage this turn", True, GOLD)
            screen.blit(line, (panel.x + 16, y))
        y += 24

    header = FONT_SMALL.render("Hand — card damage EV:", True, TEXT)
    screen.blit(header, (panel.x + 16, y))
    y += 22
    shown = 0
    for card_name in game.hand:
        ev = card_ev(game, card_name)
        if ev is None:
            continue
        recoil = card_recoil_ev(game, card_name)
        if recoil is not None:
            # Cards with non-zero recoil/backfire get the full Net EV &
            # Cost Efficiency breakdown instead of a single EV line, per
            # Professor Jenson's rubric (Section 1: cost-adjusted, risk-
            # adjusted value rather than raw gross damage).
            net = card_net_ev(game, card_name)
            cost = CARD_LIBRARY.get(card_name, {}).get("cost", 0)
            eff = card_cost_efficiency(game, card_name)
            name_line = FONT_TINY.render(f"{card_name}:", True, GOLD)
            screen.blit(name_line, (panel.x + 24, y))
            y += 16
            for label, val in (
                ("Gross E(Dmg)", ev),
                ("Expected Recoil", recoil),
                ("Net E(V)", net),
            ):
                line = FONT_TINY.render(f"  {label} = {val:.2f}", True, TEXT)
                screen.blit(line, (panel.x + 24, y))
                y += 16
            eff_text = f"  Cost Efficiency = Net E(V)/{cost} = {eff:.2f}" if eff is not None else "  Cost Efficiency = n/a"
            screen.blit(FONT_TINY.render(eff_text, True, GREEN), (panel.x + 24, y))
            y += 18
        else:
            line = FONT_TINY.render(f"{card_name}: E(V) = {ev:.2f}", True, TEXT)
            screen.blit(line, (panel.x + 24, y))
            y += 18
        shown += 1
        if y > panel.bottom - 14:
            break
    if shown == 0:
        screen.blit(FONT_TINY.render("(no damage cards in hand)", True, MUTED), (panel.x + 24, y))

    # Real-time empirical roll counters (Law of Large Numbers): shows the
    # running average of every die actually rolled this run, next to each
    # die's theoretical mean, converging as more dice are rolled.
    if game.roll_history:
        y = panel.bottom - 14 - 16 * len(game.roll_history)
        y = max(y, panel.y + 60)
        divider_y = min(y - 4, panel.bottom - 14)
        pygame.draw.line(screen, (255, 255, 255, 40), (panel.x + 16, divider_y), (panel.right - 16, divider_y), 1)
        for sides in sorted(game.roll_history):
            bucket = game.roll_history[sides]
            n = sum(bucket.values())
            if n <= 0 or y > panel.bottom - 14:
                continue
            avg = sum(face * cnt for face, cnt in bucket.items()) / n
            theory = (sides + 1) / 2
            line = FONT_TINY.render(f"d{sides} empirical: n={n}, avg={avg:.2f} (theory {theory:.2f})", True, GREEN)
            screen.blit(line, (panel.x + 16, y))
            y += 16


def draw_log_overlay(game):
    if not game.show_log:
        return
    panel = pygame.Rect(180, 70, 920, 580)
    pygame.draw.rect(screen, (16, 20, 28, 245), panel, border_radius=16)
    pygame.draw.rect(screen, GOLD, panel, width=2, border_radius=16)
    title = FONT_H1.render("Run Log", True, GOLD)
    screen.blit(title, (panel.centerx - title.get_width() // 2, panel.y + 18))
    hint = FONT_SMALL.render("Press L to close", True, MUTED)
    screen.blit(hint, (panel.right - hint.get_width() - 20, panel.y + 24))
    entries = game.combat_log[-22:]
    y = panel.y + 70
    for entry in entries:
        lines = wrapped_text(FONT_SMALL, entry, panel.width - 50)
        for line in lines[:2]:
            screen.blit(FONT_SMALL.render(line, True, TEXT), (panel.x + 24, y))
            y += 22
        y += 3
        if y > panel.bottom - 24:
            break


def draw_pause_menu(game):
    """Escape opens this from anywhere (except it closes the deck view
    first, if that's open). Covers the 'no maximize/settings/quit' gap —
    the window itself is resizable/maximizable via its normal title bar,
    and this adds an in-game way to jump to a specific size, toggle real
    fullscreen, or quit cleanly without the OS 'X' button."""
    if not game.show_pause_menu:
        return
    dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(dim, (6, 6, 12, 170), pygame.Rect(0, 0, WIDTH, HEIGHT))
    screen.blit(dim, (0, 0))

    mpos = pygame.mouse.get_pos()
    panel = pygame.Rect(WIDTH // 2 - 300, 140, 600, 450)
    _rounded_gradient_body(screen, panel, (38, 44, 56), (22, 25, 33), border_radius=20)
    pygame.draw.rect(screen, GOLD, panel, width=3, border_radius=20)

    title = FONT_H1.render("Paused", True, GOLD)
    screen.blit(title, (panel.centerx - title.get_width() // 2, panel.y + 22))

    resume_btn = pygame.Rect(panel.centerx - 140, panel.y + 74, 280, 50)
    draw_button(screen, resume_btn, "Resume", BLUE, hovered=resume_btn.collidepoint(mpos))
    game.button_rects["pause_resume"] = resume_btn

    section_y = resume_btn.bottom + 26
    screen.blit(FONT_SMALL.render("Display", True, MUTED), (panel.x + 40, section_y))
    pygame.draw.line(screen, (255, 255, 255, 40), (panel.x + 40, section_y + 22), (panel.right - 40, section_y + 22), 1)

    fs_label = "Fullscreen: On" if is_fullscreen else "Fullscreen: Off"
    fs_btn = pygame.Rect(panel.centerx - 140, section_y + 34, 280, 46)
    draw_button(screen, fs_btn, fs_label, GOLD if is_fullscreen else DIM, hovered=fs_btn.collidepoint(mpos), font=FONT_SMALL)
    game.button_rects["pause_toggle_fullscreen"] = fs_btn

    current_size = None if real_display is None else real_display.get_size()
    preset_y = fs_btn.bottom + 16
    preset_w, preset_gap = 176, 16
    row_w = len(WINDOW_SIZE_PRESETS) * preset_w + (len(WINDOW_SIZE_PRESETS) - 1) * preset_gap
    start_x = panel.centerx - row_w // 2
    for i, (w, h) in enumerate(WINDOW_SIZE_PRESETS):
        rect = pygame.Rect(start_x + i * (preset_w + preset_gap), preset_y, preset_w, 46)
        is_active = (not is_fullscreen) and current_size == (w, h)
        draw_button(screen, rect, f"{w}×{h}", GOLD if is_active else DIM, hovered=rect.collidepoint(mpos), font=FONT_TINY)
        game.button_rects[f"pause_window_{w}x{h}"] = rect

    quit_y = preset_y + 46 + 34
    screen.blit(FONT_SMALL.render("Game", True, MUTED), (panel.x + 40, quit_y))
    pygame.draw.line(screen, (255, 255, 255, 40), (panel.x + 40, quit_y + 22), (panel.right - 40, quit_y + 22), 1)
    quit_btn = pygame.Rect(panel.centerx - 140, quit_y + 34, 280, 50)
    draw_button(screen, quit_btn, "Quit Game", RED, hovered=quit_btn.collidepoint(mpos))
    game.button_rects["pause_quit"] = quit_btn

    hint = FONT_TINY.render("Press Esc to resume", True, DIM)
    screen.blit(hint, (panel.centerx - hint.get_width() // 2, panel.bottom - 28))


def draw_roll_distribution_panel(game, panel):
    """'Audit Stats' empirical roll distributions -- a live Law of Large
    Numbers demo. Every die actually rolled this run is tallied in
    game.roll_history (see Game._record_roll); here we compare the running
    empirical average against the theoretical mean E(V) = (sides+1)/2 for a
    fair d(sides), per die size, so the player can watch the empirical
    average converge toward the theoretical one as the sample size grows."""
    if not game.roll_history:
        return
    header = FONT_SMALL.render("Empirical Roll Distribution (Law of Large Numbers)", True, BLUE)
    x = panel.right - 30 - max(header.get_width(), 300)
    y = panel.y + 60
    screen.blit(header, (x, y))
    y += 20
    for sides in sorted(game.roll_history):
        bucket = game.roll_history[sides]
        n = sum(bucket.values())
        if n <= 0:
            continue
        empirical_avg = sum(face * cnt for face, cnt in bucket.items()) / n
        theory_avg = (sides + 1) / 2
        line = f"d{sides}: n={n}, avg={empirical_avg:.2f} (theory {theory_avg:.2f})"
        screen.blit(FONT_TINY.render(line, True, MUTED), (x, y))
        y += 16


def draw_deck_view(game):
    draw_spire_background(screen)
    panel = pygame.Rect(90, 54, 1100, 610)
    pygame.draw.rect(screen, PANEL, panel, border_radius=16)
    pygame.draw.rect(screen, GOLD, panel, width=2, border_radius=16)

    title = FONT_TITLE.render("Your Deck", True, GOLD)
    screen.blit(title, (panel.centerx - title.get_width() // 2, panel.y + 18))
    cards = sorted(game.full_deck_cards())
    counts = {}
    for card_name in cards:
        counts[card_name] = counts.get(card_name, 0) + 1
    entries = sorted(counts.items())
    page_size = 12
    page_count = max(1, math.ceil(len(entries) / page_size))
    game.deck_page = max(0, min(game.deck_page, page_count - 1))
    page_entries = entries[game.deck_page * page_size:(game.deck_page + 1) * page_size]

    summary = FONT_SMALL.render(f"{len(cards)} cards  •  {len(entries)} unique", True, MUTED)
    screen.blit(summary, (panel.x + 28, panel.y + 78))
    draw_roll_distribution_panel(game, panel)
    card_w, card_h = 245, 116
    for slot, (card_name, count) in enumerate(page_entries):
        col, row = slot % 4, slot // 4
        rect = pygame.Rect(panel.x + 28 + col * 266, panel.y + 110 + row * 132, card_w, card_h)
        rarity = CARD_LIBRARY[card_name].get("rarity", "common")
        border = RARITY_COLORS.get(rarity, MUTED)
        pygame.draw.rect(screen, (38, 44, 55), rect, border_radius=10)
        pygame.draw.rect(screen, border, rect, width=2, border_radius=10)
        icon_center = (rect.x + 30, rect.y + 30)
        pygame.draw.circle(screen, PANEL_LIGHT, icon_center, 20)
        pygame.draw.circle(screen, border, icon_center, 20, 2)
        blit_icon(screen, CARD_ICON.get(_base_card_name(card_name), ICON_CARD_TYPE.get(CARD_LIBRARY[card_name]["type"])), icon_center, 24)
        name = FONT_HEADER.render(card_name, True, TEXT)
        if name.get_width() > rect.width - 64:
            name = FONT_SMALL.render(card_name, True, TEXT)
        screen.blit(name, (rect.x + 58, rect.y + 13))
        screen.blit(FONT_TINY.render(f"x{count}  •  {CARD_LIBRARY[card_name]['cost']} energy", True, GOLD), (rect.x + 58, rect.y + 38))
        # "Audit Deck" (Rest Site): show each card's damage Expected Value
        # next to its cost, when it has one (see card_ev()/CARD_EV).
        ev = card_ev(game, card_name)
        if ev is not None:
            screen.blit(FONT_TINY.render(f"E(V) = {ev:.2f}", True, BLUE), (rect.x + 58, rect.y + 54))
        desc_lines = wrapped_text(FONT_TINY, CARD_LIBRARY[card_name]["desc"], rect.width - 24)
        for line_index, line in enumerate(render_lines(FONT_TINY, desc_lines[:2], color=MUTED)):
            screen.blit(line, (rect.x + 12, rect.y + 68 + line_index * 15))

    close = pygame.Rect(panel.right - 168, panel.y + 18, 136, 38)
    draw_button(screen, close, "Close", BLUE, hovered=close.collidepoint(pygame.mouse.get_pos()), font=FONT_SMALL)
    game.button_rects["close_deck"] = close
    if page_count > 1:
        nav_y = panel.bottom - 44
        label = FONT_SMALL.render(f"Page {game.deck_page + 1} / {page_count}", True, MUTED)
        screen.blit(label, (panel.centerx - label.get_width() // 2, nav_y + 8))
        if game.deck_page > 0:
            prev = pygame.Rect(panel.x + 28, nav_y, 120, 36)
            draw_button(screen, prev, "< Prev", DIM, hovered=prev.collidepoint(pygame.mouse.get_pos()), font=FONT_SMALL)
            game.button_rects["deck_prev"] = prev
        if game.deck_page < page_count - 1:
            nxt = pygame.Rect(panel.right - 148, nav_y, 120, 36)
            draw_button(screen, nxt, "Next >", DIM, hovered=nxt.collidepoint(pygame.mouse.get_pos()), font=FONT_SMALL)
            game.button_rects["deck_next"] = nxt


# ---------------------------------------------------------------------------
# Rendering — events, rewards, rest, shop
# ---------------------------------------------------------------------------

def _draw_shadowed_title(surface, text, top_y, color=GOLD, scale=1.0, shadow_offset=3):
    """Renders big title text straight over a background image -- no
    backing bar/border -- using a soft drop-shadow (a dark copy offset a
    few px down-right) for legibility instead. ``scale`` upsizes the
    rendered text via smoothscale since there's no separate "bigger title"
    font loaded; returns the on-screen rect so callers can lay out
    whatever comes next relative to its actual (scaled) height instead of
    guessing at a fixed offset."""
    base = FONT_TITLE.render(text, True, color)
    if scale != 1.0:
        w, h = base.get_size()
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
        base = scaler(base, new_size)
    shadow = FONT_TITLE.render(text, True, (8, 8, 12))
    if scale != 1.0:
        w, h = shadow.get_size()
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        scaler = pygame.transform.smoothscale if hasattr(pygame.transform, "smoothscale") else pygame.transform.scale
        shadow = scaler(shadow, new_size)
    x = WIDTH // 2 - base.get_width() // 2
    shadow_surf = pygame.Surface(shadow.get_size(), pygame.SRCALPHA)
    shadow_surf.blit(shadow, (0, 0))
    shadow_surf.set_alpha(160)
    surface.blit(shadow_surf, (x + shadow_offset, top_y + shadow_offset))
    surface.blit(base, (x, top_y))
    return pygame.Rect(x, top_y, base.get_width(), base.get_height())


def draw_procedural_casino_backdrop(surface, rect):
    """Fallback backdrop for the wagering shrine when assets/events/
    casino_event.png is missing/unreadable: a dark-slate roulette dial with
    gold runes, drawn with plain shapes so it never crashes or needs a real
    asset on disk."""
    pygame.draw.rect(surface, (16, 17, 22), rect)
    center = rect.center
    max_r = min(rect.width, rect.height) // 2 - 20
    for i, r in enumerate(range(max_r, 20, -34)):
        shade = 30 + (i * 6) % 40
        pygame.draw.circle(surface, (shade, shade + 4, shade + 10), center, r, 2)
    tick_count = 20
    for i in range(tick_count):
        angle = (2 * math.pi * i) / tick_count
        inner = max_r - 14
        outer = max_r + 6
        x1, y1 = center[0] + inner * math.cos(angle), center[1] + inner * math.sin(angle)
        x2, y2 = center[0] + outer * math.cos(angle), center[1] + outer * math.sin(angle)
        pygame.draw.line(surface, GOLD, (x1, y1), (x2, y2), 2)
    pi_surf = FONT_TITLE.render("π", True, GOLD)
    surface.blit(pi_surf, (center[0] - pi_surf.get_width() // 2, center[1] - pi_surf.get_height() // 2))


def draw_procedural_dice_cup(surface, center, size):
    """Fallback art for Gambler's Flurry when assets/cards/gamblers_
    flurry.png is missing/unreadable: an iron dice cup with gold pips."""
    cup_w, cup_h = size, int(size * 0.8)
    cup_rect = pygame.Rect(0, 0, cup_w, cup_h)
    cup_rect.center = (center[0], center[1] + size // 6)
    _rounded_gradient_body(surface, cup_rect, (120, 122, 130), (58, 60, 68), border_radius=int(size * 0.14))
    pygame.draw.rect(surface, (30, 30, 36), cup_rect, width=2, border_radius=int(size * 0.14))
    die_size = int(size * 0.32)
    for dx, dy in ((-die_size * 0.55, -die_size * 0.1), (die_size * 0.15, -die_size * 0.5)):
        die_rect = pygame.Rect(0, 0, die_size, die_size)
        die_rect.center = (center[0] + dx, center[1] - size // 5 + dy)
        pygame.draw.rect(surface, GOLD, die_rect, border_radius=int(die_size * 0.18))
        pygame.draw.rect(surface, (140, 106, 20), die_rect, width=2, border_radius=int(die_size * 0.18))
        pip_r = max(2, die_size // 10)
        for px, py in ((-1, -1), (1, 1), (0, 0)):
            pygame.draw.circle(surface, (40, 30, 10), (die_rect.centerx + px * die_size // 4, die_rect.centery + py * die_size // 4), pip_r)


def draw_push_luck_banner(game, rect):
    """The Gambler's Flurry push-your-luck banner: card art (or its
    procedural fallback) + a live Banked Damage / Next Roll E(V) readout +
    the Bank & Strike / Push Your Luck buttons. Replaces the hand/End Turn
    row in draw_combat() while game.push_luck_active is True."""
    pygame.draw.rect(screen, PANEL, rect, border_radius=14)
    pygame.draw.rect(screen, GOLD, rect, width=2, border_radius=14)

    art_center = (rect.x + 90, rect.centery)
    art = ASSET_MANAGER.get_card_icon("gamblers_flurry", 120)
    if art is not None:
        art_rect = art.get_rect(center=art_center)
        screen.blit(art, art_rect.topleft)
    else:
        draw_procedural_dice_cup(screen, art_center, 110)

    header_x = rect.x + 170
    screen.blit(FONT_HEADER.render("Gambler's Flurry — Push Your Luck", True, GOLD), (header_x, rect.y + 14))
    screen.blit(FONT_BODY.render(f"Banked Damage: {game.push_luck_banked}", True, TEXT), (header_x, rect.y + 46))

    ev = game.push_luck_next_roll_ev()
    ev_color = GREEN if ev > 0 else (RED if ev < 0 else MUTED)
    sign = "positive" if ev > 0 else ("negative" if ev < 0 else "break-even")
    screen.blit(FONT_BODY.render(f"Next Roll E(V): {ev:+.2f} damage ({sign})", True, ev_color), (header_x, rect.y + 72))
    formula = FONT_TINY.render("E(Next Roll) = (17 - Banked)/6  [1/6 bust: lose bank + 3 recoil; 5/6: +2..+6]", True, MUTED)
    screen.blit(formula, (header_x, rect.y + 98))

    btn_w, btn_h = 220, 48
    bank_btn = pygame.Rect(header_x, rect.y + 118, btn_w, btn_h)
    push_btn = pygame.Rect(header_x + btn_w + 20, rect.y + 118, btn_w, btn_h)
    mpos = pygame.mouse.get_pos()
    draw_button(screen, bank_btn, "BANK & STRIKE", GREEN, hovered=bank_btn.collidepoint(mpos))
    draw_button(screen, push_btn, "PUSH YOUR LUCK", RED, hovered=push_btn.collidepoint(mpos))
    game.button_rects["push_luck_bank"] = bank_btn
    game.button_rects["push_luck_again"] = push_btn


def _draw_wager_option(game, rect, title, color, summary_lines, math_lines, button_label, button_key, disabled=False):
    """One of the three wagering-table choice cards. Split in two by a hard
    divider line: a plain-English summary up top (what a non-math player
    needs to decide), and the full stake/odds/payout/E(V) audit below it
    (what a math player wants to double-check) -- both are always visible,
    never hidden behind a tooltip, before the player can click confirm.
    Translucent (not the old fully-opaque 255-alpha fill) so the casino
    backdrop shows through the foreground choices the same way the rest
    site's option cards let the campfire art show through theirs."""
    _rounded_gradient_body(screen, rect, (30, 34, 44, 205), (18, 20, 27, 205), border_radius=14)
    pygame.draw.rect(screen, color, rect, width=2, border_radius=14)
    title_surf = FONT_SMALL.render(title, True, color)
    screen.blit(title_surf, (rect.centerx - title_surf.get_width() // 2, rect.y + 12))

    y = rect.y + 42
    for line in summary_lines:
        for wrapped in wrapped_text(FONT_BODY, line, rect.width - 24):
            screen.blit(FONT_BODY.render(wrapped, True, TEXT), (rect.x + 12, y))
            y += 24
        y += 2

    y += 6
    pygame.draw.line(screen, color, (rect.x + 12, y), (rect.right - 12, y), 2)
    y += 10
    screen.blit(FONT_TINY.render("THE MATH:", True, MUTED), (rect.x + 12, y))
    y += 18

    for line in math_lines:
        for wrapped in wrapped_text(FONT_TINY, line, rect.width - 24):
            screen.blit(FONT_TINY.render(wrapped, True, TEXT), (rect.x + 12, y))
            y += 17
        y += 3

    btn = pygame.Rect(rect.x + 14, rect.bottom - 54, rect.width - 28, 40)
    hovered = btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, btn, button_label, color, hovered=hovered, disabled=disabled, font=FONT_SMALL)
    if not disabled:
        game.button_rects[button_key] = btn
    else:
        game.button_rects.pop(button_key, None)


def draw_event(game):
    # Backdrop: casino_event.png (fit-inside, aspect-preserved) behind the
    # procedural roulette-dial fallback if the asset is missing/unreadable
    # -- either way this never crashes. The art stays visible the same way
    # draw_combat_backdrop's art does. The header used to sit on its own
    # small translucent backing bar (a hard-edged box over the art); that's
    # gone now -- the title is rendered directly over the backdrop, bigger,
    # with a soft drop-shadow (not a box/border) for legibility instead.
    draw_spire_background(screen)
    backdrop = ASSET_MANAGER.get_event_backdrop("casino_event", WIDTH, HEIGHT)
    if backdrop is not None:
        bx = (WIDTH - backdrop.get_width()) // 2
        by = (HEIGHT - backdrop.get_height()) // 2
        screen.blit(backdrop, (bx, by))
    else:
        draw_procedural_casino_backdrop(screen, pygame.Rect(0, 0, WIDTH, HEIGHT))

    title_rect = _draw_shadowed_title(screen, "THE SHRINE OF EXPECTED VALUE", 46, scale=1.4)
    sub_y = title_rect.bottom + 6
    sub = FONT_BODY.render("Every wager's odds and payout are audited below — read them before you commit.", True, MUTED)
    sub_shadow = FONT_BODY.render("Every wager's odds and payout are audited below — read them before you commit.", True, (6, 6, 10))
    screen.blit(sub_shadow, (WIDTH // 2 - sub.get_width() // 2 + 2, sub_y + 2))
    screen.blit(sub, (WIDTH // 2 - sub.get_width() // 2, sub_y))

    box_w, box_h = 300, 380
    gap = 20
    start_x = WIDTH // 2 - (box_w * 3 + gap * 2) // 2
    top_y = sub_y + 30

    tumbling = game.wager_pending is not None
    can_afford_safe = game.player["gold"] >= WAGER_SAFE_BET_STAKE_GOLD

    _draw_wager_option(
        game, pygame.Rect(start_x, top_y, box_w, box_h), "Chad Wager", BLUE,
        [
            "Small, safe bet.",
            "Roughly break-even in the long run.",
        ],
        [
            f"Stake {WAGER_SAFE_BET_STAKE_GOLD} gold. Roll 1d6.",
            f"Win on 3-6 (P=4/6={WAGER_SAFE_BET_WIN_PROB:.1%}): +{WAGER_SAFE_BET_WIN_GOLD} gold (net +{WAGER_SAFE_BET_WIN_GOLD - WAGER_SAFE_BET_STAKE_GOLD}).",
            f"Lose on 1-2 (P=2/6={1 - WAGER_SAFE_BET_WIN_PROB:.1%}): lose your stake (net -{WAGER_SAFE_BET_STAKE_GOLD}).",
            f"Fair Game | Theoretical E(V) = (4/6 * +5) + (2/6 * -10) = {WAGER_SAFE_BET_EV:+.2f} Gold",
            f"Your gold: {game.player['gold']}" + ("" if can_afford_safe else " (not enough!)"),
        ],
        "Place Chad Wager", "wager_safe", disabled=tumbling or not can_afford_safe,
    )

    _draw_wager_option(
        game, pygame.Rect(start_x + box_w + gap, top_y, box_w, box_h), "Giga Chad Wager", RED,
        [
            "Huge risk, huge reward.",
            "You'll lose HP most of the time.",
        ],
        [
            f"Stake {WAGER_DEGENERATE_STAKE_HP} HP. Roll 1d20.",
            f"Win on 15-20 (P=6/20={WAGER_DEGENERATE_WIN_PROB:.1%}): a Rare Relic or +{WAGER_DEGENERATE_WIN_GOLD} gold.",
            f"Lose on 1-14 (P=14/20={WAGER_DEGENERATE_RUIN_PROB:.1%}): lose {WAGER_DEGENERATE_STAKE_HP} HP.",
            f"Asymmetric Risk | Win P = {WAGER_DEGENERATE_WIN_PROB:.1%} | Risk of Ruin: {WAGER_DEGENERATE_RUIN_PROB:.1%}",
            f"Your HP: {game.player['hp']}/{game.player['max_hp']}",
        ],
        "Place Giga Chad Wager", "wager_degenerate", disabled=tumbling,
    )

    _draw_wager_option(
        game, pygame.Rect(start_x + 2 * (box_w + gap), top_y, box_w, box_h), "Coward Chad", MUTED,
        [
            "No risk. No reward.",
            "Walk away exactly as you are.",
        ],
        [
            "No stake. No roll.",
            "Leave the shrine — gold and HP untouched.",
        ],
        "Coward Chad", "wager_walk_away", disabled=tumbling,
    )

    if tumbling:
        # A short die-tumble animation covers the ~WAGER_TUMBLE_SECONDS
        # between confirming a wager and its actual roll resolving (see
        # Game.choose_safe_bet/choose_degenerate_bet + update_effects()).
        sides = 6 if game.wager_pending["kind"] == "safe" else 20
        tumble_value = random.randint(1, sides)
        tumble_box = pygame.Rect(0, 0, 180, 120)
        tumble_box.center = (WIDTH // 2, top_y + box_h + 70)
        pygame.draw.rect(screen, PANEL, tumble_box, border_radius=16)
        pygame.draw.rect(screen, GOLD, tumble_box, width=3, border_radius=16)
        val_surf = FONT_H1.render(str(tumble_value), True, GOLD)
        screen.blit(val_surf, (tumble_box.centerx - val_surf.get_width() // 2, tumble_box.centery - val_surf.get_height() // 2))
        tumble_label = FONT_SMALL.render(f"Rolling d{sides}...", True, TEXT)
        screen.blit(tumble_label, (tumble_box.centerx - tumble_label.get_width() // 2, tumble_box.bottom + 8))


def draw_event_result(game):
    panel = draw_center_panel(game.event_outcome_label or "Event Result", None, GREEN)
    blit_icon(screen, "dice", (WIDTH // 2 - 78, panel.y + 138), 24)
    screen.blit(FONT_HEADER.render(f"Roll: {game.roll_result}", True, GREEN), (WIDTH // 2 - 56, panel.y + 128))
    lines = wrapped_text(FONT_BODY, game.log_text, panel.width - 80)
    y = panel.y + 180
    for line in render_lines(FONT_BODY, lines[:4]):
        screen.blit(line, (panel.x + 40, y))
        y += 26
    btn = pygame.Rect(WIDTH // 2 - 140, panel.y + 340, 280, 56)
    hovered = btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, btn, "Continue", GREEN, hovered=hovered)
    game.button_rects["continue_event"] = btn


def draw_card_reward(game):
    panel = draw_center_panel("Choose a Card", None, GOLD, pygame.Rect(140, 90, 1000, 540))
    lines = wrapped_text(FONT_BODY, game.log_text, panel.width - 80)
    y = panel.y + 70
    for line in render_lines(FONT_BODY, lines[:2]):
        screen.blit(line, (panel.x + 40, y))
        y += 22

    mpos = pygame.mouse.get_pos()
    card_w, card_h = 190, 260
    n = len(game.card_reward_options)
    total_w = n * card_w + max(0, n - 1) * 30
    start_x = (WIDTH - total_w) // 2
    for idx, card_name in enumerate(game.card_reward_options):
        rect = pygame.Rect(start_x + idx * (card_w + 30), panel.y + 130, card_w, card_h)
        hovered = rect.collidepoint(mpos)
        render_card(screen, card_name, rect, hovered=hovered, affordable=True, character=game.character, game=game)
        game.button_rects[f"cardreward_{idx}"] = rect

    skip_btn = pygame.Rect(WIDTH // 2 - 90, panel.y + 430, 180, 50)
    hovered = skip_btn.collidepoint(mpos)
    draw_button(screen, skip_btn, "Skip", DIM, hovered=hovered)
    game.button_rects["skip_card_reward"] = skip_btn


def draw_relic_choice(game):
    panel = draw_center_panel("Choose a Relic", "An elite trophy — pick one to keep.", GOLD)
    mpos = pygame.mouse.get_pos()
    n = len(game.relic_choice_options)
    card_w, card_h = 320, 220
    total_w = n * card_w + max(0, n - 1) * 40
    start_x = (WIDTH - total_w) // 2
    for idx, relic in enumerate(game.relic_choice_options):
        rect = pygame.Rect(start_x + idx * (card_w + 40), panel.y + 120, card_w, card_h)
        hovered = rect.collidepoint(mpos)
        color = RARITY_COLORS.get(RELIC_LIBRARY[relic]["rarity"], MUTED)
        pygame.draw.rect(screen, (42, 50, 62), rect, border_radius=16)
        pygame.draw.rect(screen, WHITE if hovered else color, rect, width=3 if hovered else 2, border_radius=16)
        badge_center = (rect.right - 36, rect.y + 34)
        pygame.draw.circle(screen, PANEL_LIGHT, badge_center, 24)
        pygame.draw.circle(screen, color, badge_center, 24, 2)
        blit_icon(screen, ICON_RELIC.get(relic), badge_center, 28)
        screen.blit(FONT_HEADER.render(relic, True, TEXT), (rect.x + 16, rect.y + 18))
        desc_lines = wrapped_text(FONT_SMALL, RELIC_LIBRARY[relic]["description"], rect.width - 74)
        yy = rect.y + 60
        for line in render_lines(FONT_SMALL, desc_lines):
            screen.blit(line, (rect.x + 16, yy))
            yy += 20
        game.button_rects[f"relicchoice_{idx}"] = rect


def draw_treasure_result(game):
    panel = draw_center_panel("Treasure!", None, GOLD)
    icon_center = (WIDTH // 2, panel.y + 130)
    pygame.draw.circle(screen, PANEL_LIGHT, icon_center, 40)
    pygame.draw.circle(screen, GOLD, icon_center, 40, 2)
    blit_icon(screen, "reward_gift", icon_center, 48)
    lines = wrapped_text(FONT_BODY, game.log_text, panel.width - 80)
    y = panel.y + 190
    for line in render_lines(FONT_BODY, lines[:4]):
        screen.blit(line, (panel.x + 40, y))
        y += 26
    btn = pygame.Rect(WIDTH // 2 - 140, panel.y + 340, 280, 56)
    hovered = btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, btn, "Continue", GOLD, hovered=hovered)
    game.button_rects["continue_treasure"] = btn


def draw_rest_choice(game):
    """Rest Site / campfire screen. CRITICAL BUG FIX: the player's Current
    HP / Max HP bar used to be nowhere on this screen at all -- you could
    not see whether resting was even worth it without leaving to check
    elsewhere. It's now rendered prominently across the top of the panel,
    directly under the title, and stays visible the whole time this screen
    is up (not just after choosing an option)."""
    def _rest_backdrop(surface):
        bg = ASSET_MANAGER.get_background("rest_site")
        if bg is not None:
            surface.blit(bg, (0, 0))
        else:
            draw_spire_background(surface)

    # Smaller panel, and translucent (panel_alpha/dim_alpha well below the
    # opaque default) so the campfire backdrop actually reads through it
    # instead of being almost entirely covered by the card.
    panel = draw_center_panel(
        "Grindnight Camp", None, GREEN, pygame.Rect(220, 150, 840, 380),
        background_fn=_rest_backdrop, panel_alpha=150, dim_alpha=55,
    )

    # -- Prominent HP bar (the fix) -----------------------------------------
    hp_bar = pygame.Rect(panel.x + 60, panel.y + 76, panel.width - 120, 28)
    render_health_bar_with_block(screen, hp_bar, game.player["hp"], game.player["max_hp"], game.player["block"], GREEN, (60, 76, 86))
    hp_label = FONT_HEADER.render(f"HP  {game.player['hp']} / {game.player['max_hp']}", True, TEXT)
    screen.blit(hp_label, (hp_bar.centerx - hp_label.get_width() // 2, hp_bar.y + 3))

    log_lines = wrapped_text(FONT_SMALL, game.log_text, panel.width - 100)
    y = panel.y + 118
    for line in render_lines(FONT_SMALL, log_lines[:2], color=MUTED):
        screen.blit(line, (panel.x + 60, y))
        y += 20

    base, heal_amt = game.rest_heal_amount()
    heal_amt = min(heal_amt, game.player["max_hp"] - game.player["hp"])
    heal_amt = max(0, heal_amt)
    formula = f"floor({game.player['max_hp']} × 0.30) = {base} HP"

    options = [
        ("heal", "Rest & Recompute", formula, "hp_heart"),
        ("study", "Forge the Gains / Bench Press", "Upgrade a card (+1 modifier / lower variance)", "deco_book"),
        ("audit", "Audit Stats (M)", "Deck EV, plus empirical roll distributions (Law of Large Numbers)", "deco_hourglass"),
        ("skip", "Move On", "Continue without resting", "combat_swords"),
    ]
    mpos = pygame.mouse.get_pos()
    # Smaller option buttons (roughly half the old card height) and slightly
    # translucent bodies, so the campfire backdrop shows through both the
    # panel and the buttons themselves rather than being fully covered.
    card_w = (panel.width - 100) // len(options) - 15
    card_h = 140
    for idx, (action, title, desc, icon) in enumerate(options):
        rect = pygame.Rect(panel.x + 50 + idx * (card_w + 15), panel.y + 180, card_w, card_h)
        hovered = rect.collidepoint(mpos)
        top = (48, 56, 68, 215) if hovered else (42, 50, 62, 190)
        _rounded_gradient_body(screen, rect, top, (28, 32, 40, 190), border_radius=14)
        pygame.draw.rect(screen, WHITE if hovered else GOLD, rect, width=3 if hovered else 2, border_radius=14)
        blit_icon(screen, icon, (rect.centerx, rect.y + 20), 22)
        title_lines = wrapped_text(FONT_TINY, title, rect.width - 20)
        yy = rect.y + 42
        for line in render_lines(FONT_TINY, title_lines[:2], color=GOLD):
            screen.blit(line, (rect.x + 10, yy))
            yy += 16
        yy += 4
        lines = wrapped_text(FONT_TINY, desc, rect.width - 20)
        for line in render_lines(FONT_TINY, lines[:4]):
            screen.blit(line, (rect.x + 10, yy))
            yy += 14
        game.button_rects[f"rest_{action}"] = rect


def draw_rest_upgrade_pick(game):
    panel = draw_center_panel("Study — choose a card to upgrade", None, GOLD, pygame.Rect(160, 100, 960, 520))
    reward_art = get_art(REWARD_ART, 92)
    if reward_art is not None:
        screen.blit(reward_art, (panel.x + 14, panel.y + 8))
    mpos = pygame.mouse.get_pos()
    cols = 3
    card_w, card_h = 240, 148
    for idx, card_name in enumerate(game.rest_upgrade_options):
        col, row = idx % cols, idx // cols
        rect = pygame.Rect(panel.x + 60 + col * (card_w + 20), panel.y + 90 + row * (card_h + 18), card_w, card_h)
        hovered = rect.collidepoint(mpos)
        top = (48, 56, 68) if hovered else (42, 50, 62)
        _rounded_gradient_body(screen, rect, top, (28, 32, 40), border_radius=12)
        pygame.draw.rect(screen, WHITE if hovered else GOLD, rect, width=3 if hovered else 2, border_radius=12)
        badge_center = (rect.right - 24, rect.y + 24)
        pygame.draw.circle(screen, PANEL_LIGHT, badge_center, 18)
        pygame.draw.circle(screen, GOLD, badge_center, 18, 2)
        blit_icon(screen, CARD_ICON.get(card_name, ICON_CARD_TYPE.get(CARD_LIBRARY[card_name]["type"])), badge_center, 20)
        screen.blit(FONT_HEADER.render(card_name, True, TEXT), (rect.x + 12, rect.y + 12))
        upgraded = CARD_LIBRARY[card_name]["upgrades_to"]
        screen.blit(FONT_SMALL.render(f"→ {upgraded}", True, GOLD), (rect.x + 12, rect.y + 40))
        yy = rect.y + 62
        # Show exactly what forging gains you -- e.g. "8 -> 12  (+4)" -- in
        # green, before the upgraded card's full description, rather than
        # making the player compare the two desc strings themselves.
        deltas = card_upgrade_deltas(card_name)
        if deltas:
            delta_text = "  ".join(f"{b}→{u} (+{d})" for b, u, d in deltas)
            delta_lines = wrapped_text(FONT_TINY, delta_text, rect.width - 24)
            for line in render_lines(FONT_TINY, delta_lines[:2], color=GREEN):
                screen.blit(line, (rect.x + 12, yy))
                yy += 15
        desc_lines = wrapped_text(FONT_TINY, CARD_LIBRARY[upgraded]["desc"], rect.width - 48)
        for line in render_lines(FONT_TINY, desc_lines[:3]):
            screen.blit(line, (rect.x + 12, yy))
            yy += 15
        game.button_rects[f"upgrade_{idx}"] = rect

    cancel_btn = pygame.Rect(WIDTH // 2 - 90, panel.bottom - 60, 180, 44)
    hovered = cancel_btn.collidepoint(mpos)
    draw_button(screen, cancel_btn, "Cancel", DIM, hovered=hovered)
    game.button_rects["cancel_upgrade"] = cancel_btn


def draw_gold_badge(surface, x, y, gold_amount):
    """Prominent gold display for the shop screen. The old version passed
    "Gold: N" as draw_center_panel's dim MUTED-gray subtitle text, which is
    why it read as invisible. This draws a real badge with a procedural
    coin-bag icon (always visible, no asset dependency) and layers real art
    on top via get_art("shop_gold_bag") once that asset exists."""
    badge = pygame.Rect(x, y, 210, 52)
    _rounded_gradient_body(surface, badge, (64, 50, 22), (30, 24, 12), border_radius=16)
    pygame.draw.rect(surface, GOLD, badge, width=2, border_radius=16)

    bag_cx, bag_cy = badge.x + 32, badge.y + 28
    pygame.draw.circle(surface, (196, 148, 60), (bag_cx, bag_cy + 4), 16)
    pygame.draw.polygon(surface, (196, 148, 60), [
        (bag_cx - 10, bag_cy - 6), (bag_cx + 10, bag_cy - 6),
        (bag_cx + 6, bag_cy - 16), (bag_cx - 6, bag_cy - 16),
    ])
    pygame.draw.line(surface, (120, 88, 30), (bag_cx - 6, bag_cy - 16), (bag_cx - 2, bag_cy - 23), 2)
    pygame.draw.line(surface, (120, 88, 30), (bag_cx + 6, bag_cy - 16), (bag_cx + 2, bag_cy - 23), 2)
    for i in range(3):
        cx2 = bag_cx - 6 + i * 6
        pygame.draw.circle(surface, GOLD, (cx2, bag_cy + 2), 5)
        pygame.draw.circle(surface, (120, 88, 30), (cx2, bag_cy + 2), 5, 1)

    real_art = get_art("shop_gold_bag", 40)
    if real_art is not None:
        surface.blit(real_art, (bag_cx - real_art.get_width() // 2, bag_cy - real_art.get_height() // 2))

    label = FONT_HEADER.render(str(gold_amount), True, GOLD)
    surface.blit(label, (badge.x + 58, badge.y + 6))
    sub = FONT_TINY.render("Gold", True, MUTED)
    surface.blit(sub, (badge.x + 58, badge.y + 30))
    return badge


_SHOP_STAR_FIELD = [
    (int((41 * i * 89 + 11) % WIDTH), int((23 * i * 61 + 9) % 360), (i * 17) % 3 + 1)
    for i in range(36)
]


def draw_shop_backdrop(surface, width=WIDTH, height=HEIGHT):
    """A proper marketplace backdrop for the shop screen, replacing the
    generic spire background draw_center_panel used before (a dusky-sky/
    starfield/mountain scene meant for the map and combat screens, not a
    merchant's stall). Tries real art first via get_art("shop_backdrop") —
    see the Gemini prompt in the project notes — and always draws a warm,
    lantern-lit market-stall scene underneath/instead so the shop reads as
    its own place even before that art exists."""
    art = get_art("shop_backdrop", height)
    if art is not None:
        aw, ah = art.get_size()
        if aw < width:
            scale = width / aw
            art = pygame.transform.smoothscale(art, (width, int(ah * scale)))
            aw, ah = art.get_size()
        surface.blit(art, ((width - aw) // 2, height - ah))
        return

    sky_top, sky_bottom = (30, 18, 30), (58, 32, 22)
    band = max(1, height // 3)
    for i in range(0, band, 2):
        t = i / band
        pygame.draw.line(surface, _lerp_color(sky_top, sky_bottom, t), (0, i), (width, i), 2)
    surface.fill(sky_bottom, pygame.Rect(0, band, width, height - band))

    for sx, sy, r in _SHOP_STAR_FIELD:
        if sy < band + 20:
            shade = 160 + r * 18
            pygame.draw.circle(surface, (min(255, shade + 30), shade, shade - 30), (sx, sy), r)

    # Distant market-tent skyline: triangular canopy silhouettes in
    # alternating warm tones.
    tent_colors = ((70, 40, 30), (60, 36, 46), (72, 46, 28))
    tent_w = 130
    for i, tx in enumerate(range(-tent_w // 2, width + tent_w, tent_w)):
        color = tent_colors[i % len(tent_colors)]
        peak = (tx + tent_w // 2, height - 300)
        pygame.draw.polygon(surface, color, [(tx, height - 210), peak, (tx + tent_w, height - 210)])

    # Warm glow behind the counter, like lantern-light spilling over goods.
    glow = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.circle(glow, (255, 190, 110, 45), (width // 2, height - 160), 340)
    surface.blit(glow, (0, 0))

    # A row of hanging lanterns strung across the top of the stall.
    string_y = height - 300
    pygame.draw.line(surface, (90, 60, 40), (60, string_y), (width - 60, string_y), 2)
    for lx in range(140, width - 100, 160):
        sway = int(6 * math.sin(lx / 90.0))
        lantern_top = (lx + sway, string_y)
        lantern_center = (lx + sway, string_y + 22)
        pygame.draw.line(surface, (90, 60, 40), lantern_top, lantern_center, 2)
        glow2 = pygame.Surface((44, 44), pygame.SRCALPHA)
        pygame.draw.circle(glow2, (255, 200, 110, 90), (22, 22), 20)
        surface.blit(glow2, (lantern_center[0] - 22, lantern_center[1] - 10))
        pygame.draw.ellipse(surface, (230, 150, 70), pygame.Rect(lantern_center[0] - 9, lantern_center[1] - 2, 18, 22))
        pygame.draw.ellipse(surface, (255, 210, 130), pygame.Rect(lantern_center[0] - 5, lantern_center[1] + 2, 10, 12))

    mist_color = (40, 26, 20)
    mist = pygame.Surface((width, 70), pygame.SRCALPHA)
    for i in range(70):
        alpha = int(70 * (1 - i / 70))
        pygame.draw.line(mist, (*mist_color, alpha), (0, i), (width, i))
    surface.blit(mist, (0, height - 70))


def draw_shop(game):
    panel = draw_center_panel("Strange Math Merchant", None, GOLD, pygame.Rect(110, 60, 1060, 600), background_fn=draw_shop_backdrop)
    draw_gold_badge(screen, panel.centerx - 105, panel.y + 58, game.player['gold'])
    merchant_name, interior_name = SHOP_ART.get(game.character, SHOP_ART["Math Warrior"])
    merchant_art = get_art(merchant_name, 74)
    interior_art = get_art(interior_name, 74)
    if merchant_art is not None:
        screen.blit(merchant_art, (panel.x + 12, panel.y + 8))
    if interior_art is not None:
        screen.blit(interior_art, (panel.right - interior_art.get_width() - 12, panel.y + 8))

    # A wooden merchant's counter under the header portraits, so the goods
    # below read as wares laid out on a stall rather than a settings list.
    counter = pygame.Rect(panel.x + 30, panel.y + 90, panel.width - 60, 12)
    pygame.draw.rect(screen, (94, 64, 38), counter, border_radius=5)
    pygame.draw.rect(screen, (150, 110, 60), pygame.Rect(counter.x, counter.y, counter.width, 4), border_radius=3)
    pygame.draw.rect(screen, (60, 40, 24), counter, width=2, border_radius=5)

    mpos = pygame.mouse.get_pos()
    n = len(game.shop_offer)
    cols = 3
    rows = max(1, math.ceil(n / cols))
    grid_top = panel.y + 116
    grid_bottom = panel.bottom - 66  # leave room for the Leave Shop button
    tag_zone = 30
    gap_x = 36
    row_pitch = (grid_bottom - grid_top) / rows
    card_h = max(90, row_pitch - tag_zone)
    card_w = min(200, card_h * (150 / 172))
    grid_w = cols * card_w + (cols - 1) * gap_x
    start_x = panel.centerx - grid_w / 2

    for idx, item in enumerate(game.shop_offer):
        col, row = idx % cols, idx // cols
        rect = pygame.Rect(
            int(start_x + col * (card_w + gap_x)), int(grid_top + row * row_pitch),
            int(card_w), int(card_h),
        )
        bought = item["bought"]
        can_afford = (not bought) and game.player["gold"] >= item["cost"]
        hovered = (not bought) and rect.collidepoint(mpos)

        # Cards render exactly the way they do in your hand (same frame,
        # art, cost badge, description) so what you're buying is instantly
        # recognizable as the same card you'll later draw and play.
        if item["kind"] == "card":
            render_card(screen, item["name"], rect, hovered=hovered, affordable=can_afford or bought, character=game.character, game=game)
        elif item["kind"] == "relic":
            render_shop_relic_card(screen, item["name"], rect, hovered=hovered, affordable=can_afford or bought)
        else:
            render_shop_service_card(screen, item, rect, hovered=hovered, affordable=can_afford or bought)

        tag_y = rect.bottom + 4
        if bought:
            tag_surf = FONT_SMALL.render("SOLD", True, MUTED)
        else:
            tag_surf = FONT_SMALL.render(f"{item['cost']} gold", True, GOLD if can_afford else RED)
        screen.blit(tag_surf, (rect.centerx - tag_surf.get_size()[0] // 2, tag_y))

        if not bought:
            game.button_rects[f"shop_{idx}"] = rect

    leave_btn = pygame.Rect(WIDTH // 2 - 100, panel.bottom - 56, 200, 44)
    hovered = leave_btn.collidepoint(mpos)
    draw_button(screen, leave_btn, "Leave Shop", BLUE, hovered=hovered)
    game.button_rects["leave_shop"] = leave_btn


SHOP_REMOVE_COLS = 3
SHOP_REMOVE_ROWS = 4
SHOP_REMOVE_PAGE_SIZE = SHOP_REMOVE_COLS * SHOP_REMOVE_ROWS


def draw_shop_remove_pick(game):
    panel = draw_center_panel("Choose a card to remove", None, RED, pygame.Rect(160, 100, 960, 520))
    mpos = pygame.mouse.get_pos()
    candidates = sorted(set(game.full_deck_cards()))
    game.button_rects = {k: v for k, v in game.button_rects.items() if not k.startswith("remove_") and k not in ("remove_prev", "remove_next")}

    page_count = max(1, math.ceil(len(candidates) / SHOP_REMOVE_PAGE_SIZE))
    game.remove_pick_page = max(0, min(game.remove_pick_page, page_count - 1))
    page_start = game.remove_pick_page * SHOP_REMOVE_PAGE_SIZE
    page_items = list(enumerate(candidates))[page_start:page_start + SHOP_REMOVE_PAGE_SIZE]

    card_w, card_h = 240, 90
    for slot, (idx, card_name) in enumerate(page_items):
        col, row = slot % SHOP_REMOVE_COLS, slot // SHOP_REMOVE_COLS
        rect = pygame.Rect(panel.x + 60 + col * (card_w + 20), panel.y + 90 + row * (card_h + 16), card_w, card_h)
        hovered = rect.collidepoint(mpos)
        top = (54, 40, 44) if hovered else (42, 34, 36)
        _rounded_gradient_body(screen, rect, top, (26, 22, 24), border_radius=12)
        pygame.draw.rect(screen, WHITE if hovered else RED, rect, width=3 if hovered else 2, border_radius=12)
        badge_center = (rect.right - 22, rect.y + 22)
        pygame.draw.circle(screen, PANEL_LIGHT, badge_center, 16)
        pygame.draw.circle(screen, RED, badge_center, 16, 2)
        blit_icon(screen, CARD_ICON.get(card_name, ICON_CARD_TYPE.get(CARD_LIBRARY[card_name]["type"])), badge_center, 18)
        name_surf = FONT_HEADER.render(card_name, True, TEXT)
        if name_surf.get_size()[0] > rect.width - 40:
            name_surf = FONT_BODY.render(card_name, True, TEXT)
        screen.blit(name_surf, (rect.x + 12, rect.y + 14))
        desc_lines = wrapped_text(FONT_TINY, CARD_LIBRARY[card_name]["desc"], rect.width - 24)
        dy = rect.y + 46
        for line in render_lines(FONT_TINY, desc_lines[:2], color=MUTED):
            if dy + 14 > rect.bottom - 6:
                break
            screen.blit(line, (rect.x + 12, dy))
            dy += 15
        game.button_rects[f"remove_{idx}"] = rect

    if page_count > 1:
        nav_y = panel.bottom - 46
        page_label = FONT_SMALL.render(f"Page {game.remove_pick_page + 1} / {page_count}", True, MUTED)
        screen.blit(page_label, (panel.centerx - page_label.get_size()[0] // 2, nav_y + 10))
        if game.remove_pick_page > 0:
            prev_btn = pygame.Rect(panel.x + 40, nav_y, 120, 40)
            draw_button(screen, prev_btn, "< Prev", DIM, hovered=prev_btn.collidepoint(mpos))
            game.button_rects["remove_prev"] = prev_btn
        if game.remove_pick_page < page_count - 1:
            next_btn = pygame.Rect(panel.right - 160, nav_y, 120, 40)
            draw_button(screen, next_btn, "Next >", DIM, hovered=next_btn.collidepoint(mpos))
            game.button_rects["remove_next"] = next_btn


def draw_victory(game):
    draw_spire_background(screen)
    dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(dim, (10, 8, 4, 140), pygame.Rect(0, 0, WIDTH, HEIGHT))
    screen.blit(dim, (0, 0))

    title_text = f"Act {game.boss_act} Cleared" if game.quiz_equation and game.quiz_is_act_transition else "Victory!"
    title = FONT_TITLE.render(title_text, True, GOLD)
    screen.blit(title, (WIDTH // 2 - title.get_size()[0] // 2, 150))
    blit_icon(screen, "relic_crown", (WIDTH // 2, 240), 64)
    sub_text = "Real Chads don't use calculators or paper. Head math or nothing." if game.quiz_equation else "You reached the summit of Chad Math vs. The Forces of the Math Spire."
    sub = FONT_BODY.render(sub_text, True, TEXT)
    screen.blit(sub, (WIDTH // 2 - sub.get_size()[0] // 2, 300))

    if game.quiz_equation:
        # Panel grew (and every element inside got its own clear band) because
        # the equation text and the answer input box used to sit only 38px
        # apart with a 46px-tall title-font formula between them — the input
        # field was drawn right on top of the bottom of the equation text.
        panel = pygame.Rect(230, 330, 820, 230)
        pygame.draw.rect(screen, PANEL, panel, border_radius=14)
        pygame.draw.rect(screen, GOLD, panel, width=2, border_radius=14)
        formula = FONT_H1.render(game.quiz_equation["text"], True, TEXT)
        screen.blit(formula, (WIDTH // 2 - formula.get_size()[0] // 2, panel.y + 30))
        input_box = pygame.Rect(WIDTH // 2 - 150, panel.y + 86, 300, 46)
        pygame.draw.rect(screen, PANEL_LIGHT, input_box, border_radius=10)
        pygame.draw.rect(screen, GOLD, input_box, width=1, border_radius=10)
        answer_text = FONT_H1.render(game.quiz_input or "", True, TEXT)
        screen.blit(answer_text, (input_box.x + 16, input_box.y + 8))
        attempts = FONT_SMALL.render(f"Attempts: {game.quiz_attempts}/3", True, MUTED)
        screen.blit(attempts, (WIDTH // 2 - attempts.get_size()[0] // 2, input_box.bottom + 14))
        submit = pygame.Rect(WIDTH // 2 - 100, input_box.bottom + 44, 200, 44)
        hovered = submit.collidepoint(pygame.mouse.get_pos())
        draw_button(screen, submit, "Submit", GOLD, hovered=hovered)
        game.button_rects["submit_quiz"] = submit

    relics = game.player["relics"]
    if not game.quiz_equation:
        label = FONT_SMALL.render(f"Relics collected: {len(relics)}", True, MUTED)
        screen.blit(label, (WIDTH // 2 - label.get_size()[0] // 2, 336))
    if relics and not game.quiz_equation:
        icon_size, gap = 30, 10
        total_w = len(relics) * icon_size + max(0, len(relics) - 1) * gap
        start_x = WIDTH // 2 - total_w // 2
        for i, relic in enumerate(relics):
            cx = start_x + i * (icon_size + gap) + icon_size // 2
            color = RARITY_COLORS.get(RELIC_LIBRARY.get(relic, {}).get("rarity", "common"), MUTED)
            pygame.draw.circle(screen, PANEL_LIGHT, (cx, 366), icon_size // 2)
            pygame.draw.circle(screen, color, (cx, 366), icon_size // 2, 2)
            blit_icon(screen, ICON_RELIC.get(relic), (cx, 366), int(icon_size * 0.72))

    btn = pygame.Rect(470, 430 if not game.quiz_equation else 570, 340, 70)
    hovered = btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, btn, "Play Again", GOLD, hovered=hovered)
    game.button_rects["restart"] = btn


def draw_gameover(game):
    draw_spire_background(screen)
    dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    pygame.draw.rect(dim, (14, 4, 4, 160), pygame.Rect(0, 0, WIDTH, HEIGHT))
    screen.blit(dim, (0, 0))

    title = FONT_TITLE.render("The Spire Wins", True, RED)
    screen.blit(title, (WIDTH // 2 - title.get_size()[0] // 2, 150))
    blit_icon(screen, "boss_skull", (WIDTH // 2, 250), 72)
    sub = FONT_BODY.render("Your run collapsed under impossible odds.", True, TEXT)
    screen.blit(sub, (WIDTH // 2 - sub.get_size()[0] // 2, 320))
    label = FONT_SMALL.render(f"You reached row {game.floor}.", True, MUTED)
    screen.blit(label, (WIDTH // 2 - label.get_size()[0] // 2, 352))

    btn = pygame.Rect(470, 420, 340, 70)
    hovered = btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, btn, "Restart Run", RED, hovered=hovered)
    game.button_rects["restart"] = btn

    select_btn = pygame.Rect(470, 505, 340, 52)
    hovered = select_btn.collidepoint(pygame.mouse.get_pos())
    draw_button(screen, select_btn, "Choose Another Warrior", BLUE, hovered=hovered, font=FONT_SMALL)
    game.button_rects["choose_character"] = select_btn


# ---------------------------------------------------------------------------
# Input dispatch
# ---------------------------------------------------------------------------

def handle_keydown(game, event):
    if game.state == "title" and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        game.state = "intro"
        return
    if event.key == pygame.K_l and game.state not in ("title", "character_select"):
        game.show_log = not game.show_log
        return
    if event.key == pygame.K_m and game.state == "combat":
        game.show_math_inspector = not game.show_math_inspector
        return
    if event.key == pygame.K_m and game.state == "rest_choice":
        game.deck_return_state = "rest_choice"
        game.deck_page = 0
        game.state = "deck_view"
        return
    if event.key == pygame.K_d and game.state not in ("title", "character_select"):
        if game.state == "deck_view":
            game.state = game.deck_return_state
        else:
            game.deck_return_state = game.state
            game.deck_page = 0
            game.state = "deck_view"
        return
    if event.key == pygame.K_ESCAPE:
        if game.state == "deck_view":
            game.state = game.deck_return_state
        else:
            game.show_pause_menu = not game.show_pause_menu
        return
    if game.state != "victory" or not game.quiz_equation:
        return
    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
        game.submit_quiz_answer(game.quiz_input)
        game.quiz_input = ""
        return
    if event.key == pygame.K_BACKSPACE:
        game.quiz_input = game.quiz_input[:-1]
        return
    if event.unicode.isdigit() or event.unicode in ("-", "+"):
        game.quiz_input += event.unicode


def handle_click(game, pos):
    state = game.state

    if game.show_pause_menu:
        # Pause menu eats every click while it's open — nothing behind it
        # (a card, an End Turn button, etc.) should be reachable.
        if game.button_rects.get("pause_resume", pygame.Rect(0, 0, 0, 0)).collidepoint(pos):
            game.show_pause_menu = False
        elif game.button_rects.get("pause_toggle_fullscreen", pygame.Rect(0, 0, 0, 0)).collidepoint(pos):
            toggle_fullscreen()
        elif game.button_rects.get("pause_quit", pygame.Rect(0, 0, 0, 0)).collidepoint(pos):
            game.quit_requested = True
        else:
            for w, h in WINDOW_SIZE_PRESETS:
                rect = game.button_rects.get(f"pause_window_{w}x{h}")
                if rect and rect.collidepoint(pos):
                    set_window_size(w, h)
                    break
        return

    settings_btn = game.button_rects.get("open_settings")
    if settings_btn and settings_btn.collidepoint(pos):
        game.show_pause_menu = True
        return

    if state != "deck_view":
        deck_btn = game.button_rects.get("open_deck")
        if deck_btn and deck_btn.collidepoint(pos) and state not in ("title", "character_select"):
            game.deck_return_state = state
            game.deck_page = 0
            game.state = "deck_view"
            return

    if state == "deck_view":
        btn = game.button_rects.get("close_deck")
        if btn and btn.collidepoint(pos):
            game.state = game.deck_return_state
            return
        btn = game.button_rects.get("deck_prev")
        if btn and btn.collidepoint(pos):
            game.deck_page = max(0, game.deck_page - 1)
            return
        btn = game.button_rects.get("deck_next")
        if btn and btn.collidepoint(pos):
            game.deck_page += 1
            return
        return

    if state == "title":
        btn = game.button_rects.get("start_run")
        if btn and btn.collidepoint(pos):
            game.state = "intro"
        return

    if state == "intro":
        btn = game.button_rects.get("continue_intro")
        if btn and btn.collidepoint(pos):
            game.state = "character_select"
        return

    if state == "character_select":
        for name in CHARACTER_OPTIONS:
            btn = game.button_rects.get(f"select_{name}")
            if btn and btn.collidepoint(pos):
                game.choose_character(name)
                return
        return

    if state == "map":
        for row in game.map_rows:
            for node in row:
                btn = game.button_rects.get(f"node_{node['id']}")
                if btn and btn.collidepoint(pos):
                    game.enter_node(node)
                    return
        return

    if state == "combat":
        if game.push_luck_active:
            btn = game.button_rects.get("push_luck_bank")
            if btn and btn.collidepoint(pos):
                game.push_luck_bank_and_strike()
                return
            btn = game.button_rects.get("push_luck_again")
            if btn and btn.collidepoint(pos):
                game.push_luck_push_again()
                return
            return
        for idx, card_name in enumerate(game.hand):
            btn = game.button_rects.get(f"card_{idx}")
            if btn and btn.collidepoint(pos):
                game.play_card(card_name)
                return
        btn = game.button_rects.get("end_turn")
        if btn and btn.collidepoint(pos):
            game.end_player_turn()
        return

    if state == "event":
        btn = game.button_rects.get("wager_safe")
        if btn and btn.collidepoint(pos):
            game.choose_safe_bet()
            return
        btn = game.button_rects.get("wager_degenerate")
        if btn and btn.collidepoint(pos):
            game.choose_degenerate_bet()
            return
        btn = game.button_rects.get("wager_walk_away")
        if btn and btn.collidepoint(pos):
            game.walk_away_from_wager()
            return
        return

    if state == "event_result":
        btn = game.button_rects.get("continue_event")
        if btn and btn.collidepoint(pos):
            game.return_to_map()
        return

    if state == "card_reward":
        for idx, card_name in enumerate(game.card_reward_options):
            btn = game.button_rects.get(f"cardreward_{idx}")
            if btn and btn.collidepoint(pos):
                game.choose_card_reward(card_name)
                return
        btn = game.button_rects.get("skip_card_reward")
        if btn and btn.collidepoint(pos):
            game.choose_card_reward(None)
        return

    if state == "relic_choice":
        for idx, relic in enumerate(game.relic_choice_options):
            btn = game.button_rects.get(f"relicchoice_{idx}")
            if btn and btn.collidepoint(pos):
                game.choose_relic_reward(relic)
                return
        return

    if state == "treasure_result":
        btn = game.button_rects.get("continue_treasure")
        if btn and btn.collidepoint(pos):
            game.return_to_map()
        return

    if state == "rest_choice":
        for action, method in (("heal", game.rest_heal), ("study", game.rest_upgrade_open), ("skip", game.rest_skip)):
            btn = game.button_rects.get(f"rest_{action}")
            if btn and btn.collidepoint(pos):
                method()
                return
        audit_btn = game.button_rects.get("rest_audit")
        if audit_btn and audit_btn.collidepoint(pos):
            game.deck_return_state = "rest_choice"
            game.deck_page = 0
            game.state = "deck_view"
        return

    if state == "rest_upgrade_pick":
        for idx, card_name in enumerate(game.rest_upgrade_options):
            btn = game.button_rects.get(f"upgrade_{idx}")
            if btn and btn.collidepoint(pos):
                game.rest_upgrade_choose(card_name)
                return
        btn = game.button_rects.get("cancel_upgrade")
        if btn and btn.collidepoint(pos):
            game.state = "rest_choice"
        return

    if state == "shop":
        for idx in range(len(game.shop_offer)):
            btn = game.button_rects.get(f"shop_{idx}")
            if btn and btn.collidepoint(pos):
                game.buy_shop_item(idx)
                return
        btn = game.button_rects.get("leave_shop")
        if btn and btn.collidepoint(pos):
            game.leave_shop()
        return

    if state == "shop_remove_pick":
        btn = game.button_rects.get("remove_prev")
        if btn and btn.collidepoint(pos):
            game.remove_pick_page = max(0, game.remove_pick_page - 1)
            return
        btn = game.button_rects.get("remove_next")
        if btn and btn.collidepoint(pos):
            game.remove_pick_page += 1
            return
        candidates = sorted(set(game.full_deck_cards()))
        for idx, card_name in enumerate(candidates):
            btn = game.button_rects.get(f"remove_{idx}")
            if btn and btn.collidepoint(pos):
                game.remove_card_instance(card_name)
                return
        return

    if state == "victory":
        btn = game.button_rects.get("submit_quiz")
        if btn and btn.collidepoint(pos):
            game.submit_quiz_answer(game.quiz_input)
            game.quiz_input = ""
            return
        btn = game.button_rects.get("restart")
        if btn and btn.collidepoint(pos):
            game.reset_run()
            return
        return

    if state == "gameover":
        btn = game.button_rects.get("restart")
        if btn and btn.collidepoint(pos):
            game.reset_run()
            return
        btn = game.button_rects.get("choose_character")
        if btn and btn.collidepoint(pos):
            game.state = "character_select"
        return


STATE_RENDERERS = {
    "title": draw_title_screen,
    "intro": draw_intro,
    "character_select": draw_character_select,
    "map": draw_map,
    "combat": draw_combat,
    "deck_view": draw_deck_view,
    "event": draw_event,
    "event_result": draw_event_result,
    "card_reward": draw_card_reward,
    "relic_choice": draw_relic_choice,
    "treasure_result": draw_treasure_result,
    "rest_choice": draw_rest_choice,
    "rest_upgrade_pick": draw_rest_upgrade_pick,
    "shop": draw_shop,
    "shop_remove_pick": draw_shop_remove_pick,
    "victory": draw_victory,
    "gameover": draw_gameover,
}


def render_frame(game):
    MUSIC_MANAGER.play_for_state(game)
    renderer = STATE_RENDERERS.get(game.state)
    if renderer:
        renderer(game)
    if game.state not in ("title", "character_select", "deck_view"):
        deck_btn = pygame.Rect(WIDTH - 190, 12, 160, 38)
        draw_button(screen, deck_btn, "Deck  [D]", BLUE, hovered=deck_btn.collidepoint(pygame.mouse.get_pos()), icon="deco_book", font=FONT_SMALL)
        game.button_rects["open_deck"] = deck_btn
    draw_log_overlay(game)
    render_floating_texts(game)
    draw_pause_menu(game)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    init_display()
    game = Game()
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handle_click(game, screen_pos_from_real(event.pos))
            elif event.type == pygame.VIDEORESIZE:
                # User dragged the window edge while windowed — recompute the
                # letterbox transform for the new size (fullscreen resizes go
                # through set_fullscreen/toggle_fullscreen instead).
                if not is_fullscreen:
                    _update_present_transform()
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_F11,) or (
                    event.key == pygame.K_RETURN and (event.mod & pygame.KMOD_ALT)
                ):
                    toggle_fullscreen()
                else:
                    handle_keydown(game, event)

        if game.quit_requested:
            running = False

        game.button_rects = {}
        game.update_effects(dt)
        render_frame(game)
        present()

    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_crash(exc)
        raise
