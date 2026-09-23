# Chad Math vs. The Forces of the Math Spire

*(internal/dev name: **Math Meme Spire**; build artifact name: **ChadMathSpire**
— all three refer to the same game, see "A note on naming" below)*

An original math/probability-themed roguelike deckbuilder, built with
Python + [pygame](https://www.pygame.org/), inspired by *Slay the Spire*.
Climb a 3-act spire as one of two characters, building a deck of
probability-flavored attacks and skills, fighting your way past regular
enemies, elites, and act bosses, and picking up relics that bend the odds
in your favor along the way.

Every card and enemy intent shows its real math — expected value, dice
odds, cost efficiency — right on the card or in the combat HUD, so the
game doubles as a (much more fun than usual) way to build intuition for
probability and expected value.

## Screenshots

*(add a screenshot or two here before sharing the repo — a combat screen
and the character select screen are good picks)*

## Characters

- **Math Warrior** — a calculated brute who relies primarily on blocking
  and skills, starting with the **Pi's Defense** relic (free block every
  turn).
- **Probability Wizard** — a fast-paced, high-variance caster with the
  **Abacus Charm** relic, which turns his lower base energy into extra
  energy every single turn.

## Running the game

### Windows: prebuilt .exe

If you just want to play, grab `ChadMathSpire.exe` from a build (see
"Building the .exe" below) or wherever it's been shared with you, and
double-click it. No Python install required.

This build is **Windows-only**. macOS and Linux players should run the
game from source instead (see below) — it's cross-platform, since it's
just Python + pygame.

### From source (Windows, macOS, or Linux)

Use **Python 3.12** for the verified Windows setup. The supplied pygame dependency failed to install with Python 3.14 on this machine. Create the virtual environment with Python 3.12, then use that environment for builds and tests.

```bash
git clone <this-repo-url>
cd <repo-folder>
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
python polyhedral_spire.py
```

### Building the .exe yourself (Windows)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
```

Then either double-click **`build_exe.bat`**, or run:

```bash
pyinstaller ChadMathSpire.spec
```

The finished executable lands at `dist\ChadMathSpire.exe`. It bundles the
`assets/` folder into itself, so it's a single, self-contained file you
can hand to someone else.

## Controls

| Input | Action |
|---|---|
| Mouse click | Select a card, button, map node, or menu option |
| `Enter` / `Space` | Advance past the title screen |
| `Esc` | Pause menu (or back out of the deck viewer, if it's open) |
| `D` | Toggle the deck viewer |
| `M` | Toggle the Math Inspector during combat (live EV/odds breakdown); opens the deck viewer's audit stats from the rest site |
| `L` | Toggle the run log |
| `F11` or `Alt+Enter` | Toggle fullscreen |
| Digits, `+`, `-`, `Enter`, `Backspace` | Answer the end-of-run equation quiz on the victory screen |

## Music (optional)

The game ships and runs silently by design — no music files are
committed to this repository. Drop your own MP3s into
`assets/audio/music/` following the exact filenames listed in
[`assets/audio/music/README.txt`](assets/audio/music/README.txt) and
they'll start playing automatically; nothing else to configure. Missing
files are always treated as "no music for this screen," never an error.

## Project structure

```
polyhedral_spire.py        Game source (single module — see its own
                            docstring for the architecture notes)
test_math_meme_spire.py    Automated test suite (python -m unittest)
math_audit.py              Standalone script auditing every card/enemy's
                            math (EV formulas, dice odds) against the code
fuzz_playthrough.py        Headless fuzz-tester: runs many full
                            playthroughs with randomized input to catch
                            crashes
test_heuristic_playthrough.py
                            A scripted "reasonable player" playthrough test
_pygame_stub.py             A tiny in-process fake pygame, used only when
                            the real pygame isn't installed (e.g. this
                            sandbox); lets the test suite run headless
                            anywhere
assets/                    Fonts, icons, artwork, audio -- see
                            ASSETS_AND_CREDITS.md for what's licensed how
ChadMathSpire.spec         PyInstaller build config
build_exe.bat              One-click Windows .exe builder
requirements.txt           Runtime dependency (pygame)
requirements-dev.txt       + pyinstaller / pytest, for building/testing
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m unittest test_math_meme_spire -v
```

The suite is fully headless (no window opens) and runs the same way
whether or not pygame is actually installed — see `_pygame_stub.py`'s
docstring. There's also a headless fuzz test that plays many full random
runs looking for crashes:

```bash
python fuzz_playthrough.py
```

## A note on naming

This project went through a rename mid-development (**Math Meme Spire** →
**Chad Math vs. The Forces of the Math Spire**, packaged as
**ChadMathSpire**). A few internal strings and the main module's filename
(`polyhedral_spire.py`) still reflect the earlier name — that's cosmetic
and left as-is rather than risk breaking anything by renaming files
mid-repo-cleanup.

## License

The source code is MIT-licensed — see `LICENSE`.

Everything under `assets/` (fonts, icons, AI-generated artwork, and any
music you add) ships under its own separate terms — see
`ASSETS_AND_CREDITS.md` before reusing or redistributing any of it.

