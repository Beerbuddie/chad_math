"""Standalone quantitative-reasoning audit for Chad Math vs. The Forces of
the Math Spire, built for Professor Jenson's grading rubric (Section 3:
"Empirical Tracking & Law of Large Numbers").

For every dice/coin-driven player card and every named monster attack in
the game, this script:

  1. Derives the THEORETICAL expected value independently, by exhaustively
     enumerating every outcome in that action's sample space (not by
     re-using the in-game CARD_EV table -- this is a from-scratch check
     against it).
  2. Runs 500,000 EMPIRICAL trials of the same dice math actually used by
     ``polyhedral_spire.py`` (the exact thresholds, dice sizes, bonuses,
     and -- for the boss -- the 5% BOSS_DAMAGE_NERF floor behaviour, all
     read directly from the source) and takes the sample mean.
  3. Reports the absolute error between the two and a PASS/FAIL Law-of-
     Large-Numbers convergence verdict. The tolerance is adaptive, not a
     single flat number: each action's own empirical variance (computed
     from that action's own trials, not guessed) sets its standard error
     (sigma / sqrt(N)), and the pass bar is max(PASS_THRESHOLD, 3 *
     std_error) -- the classic three-sigma rule, so a low-variance action
     (most cards) is held to the tight 0.05 floor, while a high-variance
     action (e.g. Gambler's Flurry, whose outcomes span roughly -20 to +6)
     gets a wider, but still statistically principled, band instead of
     failing on ordinary sampling noise at a fixed absolute threshold.

Pure standard library only (random, math, itertools) -- no pygame, no
third-party packages. Run with:

    python math_audit.py
"""

import itertools
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# polyhedral_spire.py is a pygame program, but this script only needs one
# constant from it (BOSS_DAMAGE_NERF) so the boss's nerf value can never
# silently drift out of sync with the real game. Falls back to the same
# headless pygame stub the test suite uses so this still runs with no
# pygame installed at all.
try:
    import pygame  # noqa: F401
except ImportError:
    import _pygame_stub
    _pygame_stub.install()

import polyhedral_spire as gm

N_TRIALS = 500_000
PASS_THRESHOLD = 0.05  # absolute floor -- see run_audit()'s adaptive tolerance
TOLERANCE_Z = 3.0  # three-sigma: ~99.7% of runs should PASS by chance alone


# ---------------------------------------------------------------------------
# Theoretical E(V) — independent combinatorial enumeration for every action.
# ---------------------------------------------------------------------------

def _die_range(sides):
    return range(1, sides + 1)


def theoretical_flat_die(sides, bonus=0, multiplier=1.0):
    """E(V) for "roll 1dN [+ bonus] [* multiplier]"."""
    return sum((r + bonus) * multiplier for r in _die_range(sides)) / sides


def theoretical_coin(a, b):
    return (a + b) / 2


def theoretical_reckless_roll(threshold):
    """1d10: roll <= threshold backfires (0 enemy damage), else 2x roll."""
    outcomes = _die_range(10)
    gross = sum((2 * r if r > threshold else 0) for r in outcomes) / 10
    recoil = sum((4 if r <= threshold else 0) for r in outcomes) / 10
    return gross, recoil


def theoretical_probability_missile(hits):
    """`hits` separate 1d3 rolls summed (Probability Wizard's Strike/Strike+)."""
    per_die = sum(_die_range(3)) / 3
    return per_die * hits


def theoretical_mandelbrot_recursion():
    """Fractal Fiend: 2d4; rolling doubles adds an extra 1d4. Modeled as
    three independent d4 rolls (d1, d2, and an always-generated d3 that is
    only added on doubles) so every one of the 4*4*4=64 combos is equally
    likely and exactly reproduces the in-game distribution."""
    total = 0.0
    count = 0
    for d1, d2, d3 in itertools.product(_die_range(4), repeat=3):
        value = d1 + d2 + (d3 if d1 == d2 else 0)
        total += value
        count += 1
    return total / count, count


def theoretical_root_extraction():
    """Radical Wraith: 1d12; a perfect square (1, 4, 9) triples the roll."""
    squares = {1, 4, 9}
    outcomes = list(_die_range(12))
    total = sum((r * 3 if r in squares else r) for r in outcomes)
    return total / len(outcomes)


def theoretical_chain_rule_breath(bonus=0, boss_nerf=True):
    """Derivative Dragon: 2d8 (+ rage bonus once Phase 2 triggers), then --
    for a boss fight -- floor(raw * BOSS_DAMAGE_NERF) is what actually gets
    used as the attack's value (see roll_enemy_intent()). That floor makes
    the *exact* theoretical mean slightly different from the naive
    "continuous" raw*nerf approximation the in-game Math Inspector shows,
    so this enumerates every floored outcome directly rather than assuming
    the continuous formula is exact."""
    total = 0.0
    count = 0
    for d1, d2 in itertools.product(_die_range(8), repeat=2):
        raw = d1 + d2 + bonus
        value = math.floor(raw * gm.BOSS_DAMAGE_NERF) if boss_nerf else raw
        total += value
        count += 1
    return total / count, count


def theoretical_cross_product_strike():
    """Vector Viper: 3d4, summed, pierces block entirely."""
    total = 0.0
    count = 0
    for rolls in itertools.product(_die_range(4), repeat=3):
        total += sum(rolls)
        count += 1
    return total / count, count


def theoretical_safe_wager():
    """The Shrine of Expected Value's Safe Wager: stake gm.WAGER_SAFE_BET_
    STAKE_GOLD, roll 1d6, win gm.WAGER_SAFE_BET_WIN_GOLD gold on a roll in
    gm.WAGER_SAFE_BET_WIN_ROLLS or lose the stake otherwise. Derived from
    the game's own stake/payout/win-roll CONSTANTS (structural inputs), not
    from its already-derived WAGER_SAFE_BET_EV convenience constant -- same
    independence principle as every other row in this script (see the
    module docstring): this recomputes the formula, it doesn't just quote
    the game's answer."""
    win_prob = len(gm.WAGER_SAFE_BET_WIN_ROLLS) / 6
    win_net = gm.WAGER_SAFE_BET_WIN_GOLD - gm.WAGER_SAFE_BET_STAKE_GOLD
    lose_net = -gm.WAGER_SAFE_BET_STAKE_GOLD
    return win_prob * win_net + (1 - win_prob) * lose_net


def theoretical_flurry_next_roll(banked):
    """Gambler's Flurry: E(Next Roll) given currently-banked damage B.
    P=1/6 bust: lose the whole bank AND take the recoil hit (gm.Game.
    GAMBLERS_FLURRY_RECOIL); the 5 non-bust faces (2..6) each land as-is.
    At the game's actual recoil value (3), this is exactly (17-B)/6."""
    recoil = gm.Game.GAMBLERS_FLURRY_RECOIL
    bust_value = -(banked + recoil)
    non_bust_total = sum(range(2, 7))  # faces 2-6
    return (bust_value + non_bust_total) / 6


# ---------------------------------------------------------------------------
# Empirical trials — the *exact* same dice math as the live game code,
# reproduced here with random.randint so this script needs no Game/pygame
# instance at all (see module docstring).
# ---------------------------------------------------------------------------

def trial_flat_die(sides, bonus=0, multiplier=1.0):
    return (random.randint(1, sides) + bonus) * multiplier


def trial_coin(a, b):
    return a if random.random() < 0.5 else b


def trial_reckless_roll(threshold):
    roll = random.randint(1, 10)
    if roll <= threshold:
        return 0.0, 4.0  # (gross enemy damage, recoil self-damage)
    return float(2 * roll), 0.0


def trial_probability_missile(hits):
    return float(sum(random.randint(1, 3) for _ in range(hits)))


def trial_mandelbrot_recursion():
    d1, d2 = random.randint(1, 4), random.randint(1, 4)
    total = d1 + d2
    if d1 == d2:
        total += random.randint(1, 4)
    return float(total)


def trial_root_extraction():
    roll = random.randint(1, 12)
    return float(roll * 3 if roll in (1, 4, 9) else roll)


def trial_reckless_roll_net(threshold):
    """Net per-trial value = Gross - Recoil: 0 - 4 = -4 on a bust (no
    enemy damage, full recoil), or 2*roll - 0 on a win (no recoil taken).
    Averaging this over N trials converges on Net E(V), not just Gross or
    Recoil in isolation."""
    gross, recoil = trial_reckless_roll(threshold)
    return gross - recoil


def trial_safe_wager():
    roll = random.randint(1, 6)
    if roll in gm.WAGER_SAFE_BET_WIN_ROLLS:
        return float(gm.WAGER_SAFE_BET_WIN_GOLD - gm.WAGER_SAFE_BET_STAKE_GOLD)
    return float(-gm.WAGER_SAFE_BET_STAKE_GOLD)


def trial_flurry_next_roll(banked):
    roll = random.randint(1, 6)
    if roll == 1:
        return float(-(banked + gm.Game.GAMBLERS_FLURRY_RECOIL))
    return float(roll)


def trial_chain_rule_breath(bonus=0, boss_nerf=True):
    raw = random.randint(1, 8) + random.randint(1, 8) + bonus
    value = math.floor(raw * gm.BOSS_DAMAGE_NERF) if boss_nerf else raw
    return float(value)


def trial_cross_product_strike():
    return float(sum(random.randint(1, 4) for _ in range(3)))


# ---------------------------------------------------------------------------
# Action table: (name, sample_space_size, theoretical_ev, trial_fn)
# trial_fn takes no args and returns a single float outcome per call.
# ---------------------------------------------------------------------------

def build_actions():
    actions = []

    # -- Player cards --------------------------------------------------------
    actions.append(("Strike (Math Warrior — Equation Breaker)", 1, 8.0, lambda: 8.0))
    actions.append(("Strike+ (Math Warrior — Equation Breaker+)", 1, 12.0, lambda: 12.0))
    actions.append((
        "Strike (Probability Wizard — Probability Missile, 3d3)", 3 ** 3,
        theoretical_probability_missile(3), lambda: trial_probability_missile(3),
    ))
    actions.append((
        "Strike+ (Probability Wizard — Probability Missile+, 4d3)", 3 ** 4,
        theoretical_probability_missile(4), lambda: trial_probability_missile(4),
    ))
    actions.append(("Arcane Dart (1d6+2)", 6, theoretical_flat_die(6, 2), lambda: trial_flat_die(6, 2)))
    actions.append(("Arcane Dart+ (1d6+5)", 6, theoretical_flat_die(6, 5), lambda: trial_flat_die(6, 5)))
    # Progressive Overload's baseline EV (no accumulated combat_card_bonus,
    # matching CARD_EV's static "first play" convention -- see the comment
    # on CARD_EV["Progressive Overload"] in polyhedral_spire.py).
    actions.append(("Progressive Overload (1d8+2, baseline)", 8, theoretical_flat_die(8, 2), lambda: trial_flat_die(8, 2)))
    actions.append(("Chaos Bloom (12+1d6)", 6, 12 + theoretical_flat_die(6), lambda: 12 + trial_flat_die(6)))
    actions.append(("Chaos Bloom+ (18+1d6)", 6, 18 + theoretical_flat_die(6), lambda: 18 + trial_flat_die(6)))
    actions.append(("Null Burst (coin: 15/7)", 2, theoretical_coin(15, 7), lambda: trial_coin(15, 7)))
    actions.append(("Null Burst+ (coin: 20/10)", 2, theoretical_coin(20, 10), lambda: trial_coin(20, 10)))
    actions.append(("Dice Slash (1d20)", 20, theoretical_flat_die(20), lambda: trial_flat_die(20)))
    actions.append(("Dice Slash+ (1d20+4)", 20, theoretical_flat_die(20, 4), lambda: trial_flat_die(20, 4)))
    actions.append(("Dice Burst (1d12+8)", 12, theoretical_flat_die(12, 8), lambda: trial_flat_die(12, 8)))
    actions.append(("Dice Burst+ (1d12+13)", 12, theoretical_flat_die(12, 13), lambda: trial_flat_die(12, 13)))
    actions.append(("Ultimate Strike (1d20)", 20, theoretical_flat_die(20), lambda: trial_flat_die(20)))
    actions.append(("Ultimate Blast (1d20 x1.5)", 20, theoretical_flat_die(20, multiplier=1.5), lambda: trial_flat_die(20, multiplier=1.5)))

    # Reckless Roll(+): each has two rows -- the gross damage EV (what
    # lands on the enemy) and the expected recoil EV (what backfires onto
    # the player). Section 1 of the rubric (Net EV / Cost Efficiency in the
    # Math Inspector HUD) is built on exactly these two numbers.
    for label, threshold in (("Reckless Roll", 1), ("Reckless Roll+", 2)):
        gross_theory, recoil_theory = theoretical_reckless_roll(threshold)

        def gross_trial(threshold=threshold):
            return trial_reckless_roll(threshold)[0]

        def recoil_trial(threshold=threshold):
            return trial_reckless_roll(threshold)[1]

        actions.append((f"{label} — Gross Damage (1d10, threshold={threshold})", 10, gross_theory, gross_trial))
        actions.append((f"{label} — Expected Recoil (1d10, threshold={threshold})", 10, recoil_theory, recoil_trial))

        def net_trial(threshold=threshold):
            return trial_reckless_roll_net(threshold)

        actions.append((f"{label} — Net Damage EV (Gross - Recoil, threshold={threshold})", 10, gross_theory - recoil_theory, net_trial))

    # -- Shrine of Expected Value: Safe Wager (fair-game audit) --------------
    # Stake gm.WAGER_SAFE_BET_STAKE_GOLD, roll 1d6: win on gm.WAGER_SAFE_
    # BET_WIN_ROLLS (net WIN_GOLD-STAKE), else lose the stake. Verifies
    # Professor Jenson's "fair game" claim: Theoretical E(V) should land at
    # 0.00 gold.
    actions.append(("Shrine — Safe Wager (1d6, Net Gold EV)", 6, theoretical_safe_wager(), trial_safe_wager))

    # -- Gambler's Flurry: push-your-luck stopping-threshold audit -----------
    # E(Next Roll) = (17 - B)/6 for banked damage B (at the game's actual
    # GAMBLERS_FLURRY_RECOIL=3): sampled at a few banked levels straddling
    # the break-even point (B=17, where pushing further stops being
    # positive-EV) to prove the optimal-stopping boundary empirically, not
    # just algebraically.
    for bank in (10, 16, 17, 20):
        expected_next = theoretical_flurry_next_roll(bank)

        def flurry_trial(bank=bank):
            return trial_flurry_next_roll(bank)

        actions.append((f"Gambler's Flurry — Next Roll E(V) at Bank={bank}", 6, expected_next, flurry_trial))

    # -- Monster attacks (named special moves) -------------------------------
    actions.append(("Decimal Demon — Floating Point Shift (1d10)", 10, theoretical_flat_die(10), lambda: trial_flat_die(10)))

    mandelbrot_theory, mandelbrot_space = theoretical_mandelbrot_recursion()
    actions.append(("Fractal Fiend — Mandelbrot Recursion (2d4 + cond. 1d4)", mandelbrot_space, mandelbrot_theory, trial_mandelbrot_recursion))

    cross_theory, cross_space = theoretical_cross_product_strike()
    actions.append(("Vector Viper — Cross Product Strike (3d4, pierces block)", cross_space, cross_theory, trial_cross_product_strike))

    actions.append(("Radical Wraith — Root Extraction (1d12, squares tripled)", 12, theoretical_root_extraction(), trial_root_extraction))

    dragon_theory, dragon_space = theoretical_chain_rule_breath(bonus=0, boss_nerf=True)
    actions.append((
        f"Derivative Dragon — Chain Rule Breath (2d8, Phase 1, x{gm.BOSS_DAMAGE_NERF} boss nerf)",
        dragon_space, dragon_theory, lambda: trial_chain_rule_breath(bonus=0, boss_nerf=True),
    ))
    dragon_rage_theory, dragon_rage_space = theoretical_chain_rule_breath(bonus=3, boss_nerf=True)
    actions.append((
        f"Derivative Dragon — Chain Rule Breath (2d8+3 rage, Phase 2, x{gm.BOSS_DAMAGE_NERF} boss nerf)",
        dragon_rage_space, dragon_rage_theory, lambda: trial_chain_rule_breath(bonus=3, boss_nerf=True),
    ))

    return actions


# ---------------------------------------------------------------------------
# Runner + report
# ---------------------------------------------------------------------------

def run_audit(actions, n_trials=N_TRIALS):
    """Runs n_trials of every action and, for each one, computes the sample
    mean AND the sample variance in a single pass (Welford's online
    algorithm -- numerically stable, one pass, no need to store every
    trial). The variance feeds an adaptive per-action tolerance: this is
    what actually fixes high-variance actions (Gambler's Flurry, whose
    single-trial outcomes range roughly -20 to +6) failing on ordinary
    sampling noise under one fixed absolute threshold, without just
    loosening the bar for every action indiscriminately."""
    rows = []
    for name, sample_space, theoretical_ev, trial_fn in actions:
        n = 0
        mean = 0.0
        m2 = 0.0  # sum of squared deviations from the running mean
        for _ in range(n_trials):
            x = trial_fn()
            n += 1
            delta1 = x - mean
            mean += delta1 / n
            delta2 = x - mean
            m2 += delta1 * delta2
        variance = m2 / (n - 1) if n > 1 else 0.0
        std_error = math.sqrt(variance / n_trials)
        tolerance = max(PASS_THRESHOLD, TOLERANCE_Z * std_error)
        delta = abs(mean - theoretical_ev)
        status = "PASS" if delta < tolerance else "FAIL"
        rows.append((name, sample_space, theoretical_ev, mean, delta, tolerance, status))
    return rows


def print_report(rows, n_trials=N_TRIALS):
    headers = ("Action Name", "Sample Space", "Theory E(V)", f"Empirical Mean (N={n_trials:,})", "|Delta|", "Tolerance", "LLN Status")
    col_w = [
        max(len(headers[0]), max(len(r[0]) for r in rows)),
        max(len(headers[1]), 12),
        max(len(headers[2]), 12),
        max(len(headers[3]), 24),
        max(len(headers[4]), 9),
        max(len(headers[5]), 9),
        max(len(headers[6]), 10),
    ]

    def fmt_row(cells):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, col_w)) + " |"

    sep = "+-" + "-+-".join("-" * w for w in col_w) + "-+"

    print()
    print("=" * len(sep))
    print("  Chad Math vs. The Forces of the Math Spire — Law of Large Numbers Audit")
    print(f"  {n_trials:,} trials per action | tolerance: max({PASS_THRESHOLD}, {TOLERANCE_Z} * std_error), std_error = sigma / sqrt(N)")
    print("=" * len(sep))
    print(sep)
    print(fmt_row(headers))
    print(sep)
    n_pass = 0
    for name, sample_space, theory, empirical, delta, tolerance, status in rows:
        print(fmt_row((
            name,
            sample_space,
            f"{theory:.4f}",
            f"{empirical:.4f}",
            f"{delta:.4f}",
            f"{tolerance:.4f}",
            status,
        )))
        if status == "PASS":
            n_pass += 1
    print(sep)
    print(f"  {n_pass}/{len(rows)} actions converged within tolerance.")
    print("=" * len(sep))
    print()


def main():
    n_trials = int(sys.argv[1]) if len(sys.argv) > 1 else N_TRIALS
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if seed is not None:
        random.seed(seed)
    actions = build_actions()
    rows = run_audit(actions, n_trials=n_trials)
    print_report(rows, n_trials=n_trials)
    failures = [r for r in rows if r[6] == "FAIL"]
    if failures:
        print(f"WARNING: {len(failures)} action(s) failed to converge within tolerance:")
        for name, *_ in failures:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
