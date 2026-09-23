"""Randomized full-playthrough soak test.

Repeatedly auto-plays entire runs (random legal choices at every screen)
across many random seeds, asserting the game never raises and every run
terminates in victory or gameover within a bounded number of steps. This
exercises map connectivity, combat, rewards, rest, shop, and events far
more thoroughly than the unit tests alone.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pygame  # noqa: F401
except ImportError:
    import _pygame_stub
    _pygame_stub.install()

import polyhedral_spire as gm


def play_one_run(seed, max_steps=4000):
    random.seed(seed)
    g = gm.Game()
    g.choose_character(random.choice(list(gm.CHARACTER_OPTIONS.keys())))

    for step in range(max_steps):
        state = g.state

        if state == "map":
            available = list(g.available_node_ids())
            node = gm.find_node(g.map_rows, random.choice(available))
            g.enter_node(node)

        elif state == "combat":
            if g.push_luck_active:
                # Gambler's Flurry is mid-resolution -- randomly bank or
                # push (nothing else is playable until this resolves; see
                # play_card()'s guard and draw_combat()'s hidden hand/End
                # Turn row while push_luck_active is True).
                if random.random() < 0.5:
                    g.push_luck_bank_and_strike()
                else:
                    g.push_luck_push_again()
                continue
            playable = [c for c in g.hand if gm.CARD_LIBRARY[c]["cost"] <= g.player["energy"]]
            if playable and random.random() < 0.85:
                g.play_card(random.choice(playable))
            else:
                g.end_player_turn()

        elif state == "event":
            # Randomly exercise all three wagering-shrine choices. Confirming
            # a wager defers its actual roll behind a short die-tumble
            # animation (wager_pending) -- fast-forward past it immediately
            # since this driver has no real-time render loop to tick it.
            choice = random.random()
            if choice < 0.4 and g.player["gold"] >= gm.WAGER_SAFE_BET_STAKE_GOLD:
                g.choose_safe_bet()
            elif choice < 0.8:
                g.choose_degenerate_bet()
            else:
                g.walk_away_from_wager()
            if g.wager_pending is not None:
                g.update_effects(dt=gm.WAGER_TUMBLE_SECONDS + 0.01)

        elif state == "event_result":
            g.return_to_map()

        elif state == "card_reward":
            if random.random() < 0.15:
                g.choose_card_reward(None)
            else:
                g.choose_card_reward(random.choice(g.card_reward_options))

        elif state == "relic_choice":
            g.choose_relic_reward(random.choice(g.relic_choice_options))

        elif state == "treasure_result":
            g.return_to_map()

        elif state == "rest_choice":
            choice = random.choice(["heal", "study", "skip"])
            if choice == "heal":
                g.rest_heal()
            elif choice == "study":
                g.rest_upgrade_open()
                if g.state != "rest_upgrade_pick":
                    g.return_to_map()
            else:
                g.rest_skip()

        elif state == "rest_upgrade_pick":
            g.rest_upgrade_choose(random.choice(g.rest_upgrade_options))

        elif state == "shop":
            if random.random() < 0.6:
                idx = random.randrange(len(g.shop_offer))
                g.buy_shop_item(idx)
                if g.state == "shop_remove_pick":
                    candidates = sorted(set(g.full_deck_cards()))
                    g.remove_card_instance(random.choice(candidates))
            else:
                g.leave_shop()

        elif state == "shop_remove_pick":
            candidates = sorted(set(g.full_deck_cards()))
            g.remove_card_instance(random.choice(candidates))

        elif state in ("victory", "gameover"):
            return state, step

        else:
            raise AssertionError(f"Unhandled state reached: {state}")

    raise AssertionError(f"Run with seed {seed} did not terminate within {max_steps} steps (stuck in {g.state})")


def main():
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    outcomes = {"victory": 0, "gameover": 0}
    max_step_seen = 0
    for seed in range(n_runs):
        outcome, steps = play_one_run(seed)
        outcomes[outcome] += 1
        max_step_seen = max(max_step_seen, steps)
    print(f"Completed {n_runs} full playthroughs with no exceptions.")
    print(f"Outcomes: {outcomes}")
    print(f"Longest run: {max_step_seen} steps.")
    # NOTE: this driver plays uniformly-random legal moves (it does not
    # prioritize blocking incoming damage), so a low/zero win rate here is
    # expected and is NOT a balance bug -- see test_heuristic_playthrough.py
    # for a smarter bot that verifies the run is actually winnable.
    assert outcomes["gameover"] > 0, "No run ever lost — combat may be trivial/undertuned."


if __name__ == "__main__":
    main()
