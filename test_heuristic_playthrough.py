"""Verifies the run is actually finishable (not artificially grindy/unwinnable)
using a simple heuristic bot: block when a lethal-ish hit is incoming, rest
when hurt, otherwise attack. This is deliberately not optimal play -- it's a
lower bound on how winnable the game is.

Run with:  python test_heuristic_playthrough.py
"""

import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pygame  # noqa: F401
except ImportError:
    import _pygame_stub
    _pygame_stub.install()

import polyhedral_spire as gm


def smart_combat_step(g):
    if g.push_luck_active:
        # Gambler's Flurry is mid-resolution: a careful bot banks once it
        # has a decent amount, otherwise pushes its luck while the E(V) is
        # still positive (see Game.push_luck_next_roll_ev).
        if g.push_luck_banked >= 8 or g.push_luck_next_roll_ev() <= 0:
            g.push_luck_bank_and_strike()
        else:
            g.push_luck_push_again()
        return
    energy = g.player["energy"]
    playable = [c for c in g.hand if gm.CARD_LIBRARY[c]["cost"] <= energy]
    if not playable:
        g.end_player_turn()
        return
    intent = (g.enemy.get("intent") or {}) if g.enemy else {}
    incoming = intent.get("value", 0) if intent.get("type") in ("attack", "burst") else 0
    need_block = max(0, incoming - g.player["block"])
    skills = [c for c in playable if gm.CARD_LIBRARY[c]["type"] == "skill"]
    attacks = [c for c in playable if gm.CARD_LIBRARY[c]["type"] == "attack"]
    powers = [c for c in playable if gm.CARD_LIBRARY[c]["type"] == "power"]
    if powers and g.turn_number <= 2:
        g.play_card(powers[0])
    elif need_block > 0 and skills:
        g.play_card(skills[0])
    elif attacks:
        g.play_card(attacks[0])
    elif skills:
        g.play_card(skills[0])
    else:
        g.end_player_turn()


def play_one(seed, max_steps=6000):
    random.seed(seed)
    g = gm.Game()
    g.choose_character(random.choice(list(gm.CHARACTER_OPTIONS.keys())))

    for step in range(max_steps):
        state = g.state
        if state == "map":
            available = list(g.available_node_ids())
            nodes = [gm.find_node(g.map_rows, nid) for nid in available]
            low_hp = g.player["hp"] < g.player["max_hp"] * 0.5
            rest_nodes = [n for n in nodes if n["type"] == "rest"]
            node = rest_nodes[0] if (low_hp and rest_nodes) else random.choice(nodes)
            g.enter_node(node)
        elif state == "combat":
            smart_combat_step(g)
        elif state == "event":
            # A careful bot takes the fair-EV Safe Wager when it can afford
            # the stake, otherwise just walks away rather than gambling HP.
            # Confirming a wager defers its roll behind a short tumble
            # animation (wager_pending) -- fast-forward past it since this
            # driver has no real-time render loop to tick it.
            if g.player["gold"] >= gm.WAGER_SAFE_BET_STAKE_GOLD:
                g.choose_safe_bet()
            else:
                g.walk_away_from_wager()
            if g.wager_pending is not None:
                g.update_effects(dt=gm.WAGER_TUMBLE_SECONDS + 0.01)
        elif state == "event_result":
            g.return_to_map()
        elif state == "card_reward":
            g.choose_card_reward(random.choice(g.card_reward_options))
        elif state == "relic_choice":
            g.choose_relic_reward(random.choice(g.relic_choice_options))
        elif state == "treasure_result":
            g.return_to_map()
        elif state == "rest_choice":
            g.rest_heal()
        elif state == "rest_upgrade_pick":
            g.rest_upgrade_choose(random.choice(g.rest_upgrade_options))
        elif state == "shop":
            g.leave_shop()
        elif state in ("victory", "gameover"):
            return state, g.floor
    return "timeout", g.floor


def main():
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    results = [play_one(seed) for seed in range(n_runs)]
    counts = Counter(r[0] for r in results)
    win_rate = counts.get("victory", 0) / n_runs
    print(f"Heuristic playthroughs: {n_runs}, outcomes: {dict(counts)}, win rate: {win_rate:.1%}")
    assert counts.get("timeout", 0) == 0, "Some runs never terminated -- possible soft-lock."
    assert counts.get("victory", 0) > 0, "The run appears unwinnable even for a careful player."


if __name__ == "__main__":
    main()
