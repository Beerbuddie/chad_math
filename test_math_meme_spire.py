"""Automated tests for Math Meme Spire.

Run with:  python -m pytest test_math_meme_spire.py -v
       or:  python test_math_meme_spire.py

These tests are fully headless: they never open a real window. On the
user's Windows machine, the real pygame in their venv is used (with
video/display init, but no event pump is required for these tests). If
pygame cannot be imported at all (e.g. this sandbox, which has no network
access to install it), a tiny in-process fake ``pygame`` module is
installed first so the game's logic can still be exercised end-to-end.
"""

import inspect
import math
import os
import random
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pygame  # noqa: F401
    _HAVE_REAL_PYGAME = True
except ImportError:
    import _pygame_stub
    _pygame_stub.install()
    _HAVE_REAL_PYGAME = False

# SDL needs a driver even for a real pygame install in CI/headless contexts.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import polyhedral_spire as game_module  # noqa: E402


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_start_game_loop(self):
        """Importing the module must not open a window or block."""
        self.assertFalse(game_module._DISPLAY_READY)
        self.assertIsNone(game_module.screen)

    def test_game_instance_without_display(self):
        g = game_module.Game()
        self.assertEqual(g.state, "title")
        self.assertEqual(g.player["hp"], 94)


class RelicSystemTests(unittest.TestCase):
    def setUp(self):
        random.seed(1234)
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")

    def test_every_relic_has_description_and_is_in_library(self):
        for name, data in game_module.RELIC_LIBRARY.items():
            self.assertTrue(data["description"])
            self.assertIn("hooks", data)

    def test_duplicate_relic_is_a_no_op(self):
        self.assertTrue(self.g.apply_relic("Calculator Shield"))
        self.assertFalse(self.g.apply_relic("Calculator Shield"))
        self.assertEqual(self.g.player["relics"].count("Calculator Shield"), 1)

    def test_pis_defense_grants_block_every_turn_not_just_combat_start(self):
        # Regression test: the old "Weighted Lens" only granted block once,
        # on_combat_start, making it nearly worthless past turn 1. The
        # renamed "Pi's Defense" grants 3 block via on_turn_start instead,
        # so it must show up again on later turns too.
        self.g.apply_relic("Pi's Defense")
        self.g.start_combat(self.g.generate_enemy(1))
        self.assertGreaterEqual(self.g.player["block"], 3)
        # Spend the block, then advance to a new turn in the SAME combat
        # (not a new fight) -- it must be granted again.
        self.g.player["block"] = 0
        self.g.turn_number += 1
        self.g.start_player_turn()
        self.assertGreaterEqual(self.g.player["block"], 3)

    def test_pis_defense_rounds_pi_down_to_a_flat_3_block(self):
        self.g.player["block"] = 0
        game_module._relic_pis_defense_turn_start(self.g)
        self.assertEqual(self.g.player["block"], 3)

    def test_calculator_shield_increases_max_hp_after_victory(self):
        self.g.apply_relic("Calculator Shield")
        before = self.g.player["max_hp"]
        self.g.start_combat({"name": "Test Dummy", "hp": 1, "max_hp": 1, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})
        self.g.deal_damage(self.g.enemy, 999, "Test")
        self.assertEqual(self.g.player["max_hp"], before + 8)

    def test_dice_sigil_floors_rolls_at_two(self):
        self.g.apply_relic("Dice Sigil")
        random.seed(0)
        low_rolls = [self.g.roll_dice(20, "test") for _ in range(200)]
        self.assertTrue(all(r >= 2 for r in low_rolls))

    def test_ritual_prism_boosts_rolls(self):
        random.seed(42)
        without = [game_module.apply_roll_modifiers(self.g, v, 20) for v in range(1, 21)]
        self.g.apply_relic("Ritual Prism")
        with_relic = [game_module.apply_roll_modifiers(self.g, v, 20) for v in range(1, 21)]
        for a, b in zip(without, with_relic):
            self.assertGreaterEqual(b, a)

    def test_abacus_charm_grants_extra_energy_each_turn(self):
        base_energy = self.g.player["max_energy"]
        self.g.apply_relic("Abacus Charm")
        self.g.start_combat(self.g.generate_enemy(1))
        self.assertEqual(self.g.player["energy"], base_energy + 1)
        self.g.end_player_turn()
        if self.g.state == "combat":
            self.assertEqual(self.g.player["energy"], base_energy + 1)

    def test_theorem_of_calm_boosts_rest_healing(self):
        self.g.player["hp"] = 1
        self.g.apply_relic("Theorem of Calm")
        self.g.rest_heal()
        # base heal (30% of max hp) + 8 bonus
        expected = max(1, round(self.g.player["max_hp"] * 0.3)) + 8
        self.assertEqual(self.g.player["hp"], min(self.g.player["max_hp"], 1 + expected))

    def test_pencil_of_fortune_forces_upgraded_reward(self):
        self.g.apply_relic("Pencil of Fortune")
        found_upgraded = False
        for _ in range(20):
            options = self.g.generate_card_reward_options()
            if any(o.endswith("+") for o in options):
                found_upgraded = True
                break
        self.assertTrue(found_upgraded)

    def test_probability_crown_doubles_first_attack(self):
        self.g.apply_relic("Probability Crown")
        self.g.start_combat(self.g.generate_enemy(1))
        enemy_hp_before = self.g.enemy["hp"]
        self.g.deal_damage(self.g.enemy, 10, "Test Strike")
        # first hit should be doubled to 20 (crown consumed)
        self.assertEqual(enemy_hp_before - self.g.enemy["hp"], 20)
        hp_before_2 = self.g.enemy["hp"]
        self.g.deal_damage(self.g.enemy, 10, "Test Strike 2")
        self.assertEqual(hp_before_2 - self.g.enemy["hp"], 10)

    def test_null_set_saves_from_lethal_once(self):
        self.g.apply_relic("Null Set")
        self.g.start_combat(self.g.generate_enemy(1))
        self.g.player["hp"] = 5
        self.g.player["block"] = 0
        self.g.deal_damage(self.g.player, 999, "Overkill")
        self.assertEqual(self.g.player["hp"], 1)
        self.assertNotEqual(self.g.state, "gameover")
        # second lethal hit this combat should not be saved again
        self.g.deal_damage(self.g.player, 999, "Overkill 2")
        self.assertEqual(self.g.state, "gameover")

    def test_golden_ratio_boosts_block(self):
        without = game_module.apply_block_modifiers(self.g, 10)
        self.g.apply_relic("Golden Ratio")
        with_relic = game_module.apply_block_modifiers(self.g, 10)
        self.assertGreater(with_relic, without)

    def test_block_persists_through_enemy_attack_until_next_player_turn(self):
        """Regression test for the original bug where block was cleared
        before the enemy got to attack, making Defend-style cards useless."""
        self.g.start_combat({"name": "Weak Foe", "hp": 40, "max_hp": 40, "attack": 5, "block": 0, "ai": "aggressive", "intent": None})
        self.g.player["block"] = 0
        self.g.gain_block(50, "Test Defend")
        hp_before = self.g.player["hp"]
        self.g.end_player_turn()
        # the huge block should have absorbed the enemy's whole attack
        self.assertEqual(self.g.player["hp"], hp_before)


class CardLibraryAndQuizTests(unittest.TestCase):
    # NOTE: this class used to be named RewardAndProgressionTests, same as
    # the *other* class further down in this file — Python silently keeps
    # only the second class-level binding of that name, so every test in
    # here was being skipped without any error or warning. Renamed so these
    # actually run; found while updating the Probability Wizard Strike test
    # below for the new roll_dice_multi() behavior.
    def test_status_cards_are_drop_only_and_ultimate_cards_are_reward_only(self):
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        shop_names = {item["name"] for item in g.generate_shop_offer() if item["kind"] == "card"}
        self.assertNotIn("Weak Hypothesis", shop_names)
        self.assertNotIn("Confidence Collapse", shop_names)
        self.assertIn("Ultimate Blast", game_module.CARD_LIBRARY)
        self.assertEqual(game_module.CARD_LIBRARY["Ultimate Blast"]["cost"], 0)
        self.assertIn("Ultimate Blast", game_module.REWARDABLE_CARDS)
        self.assertNotIn("Ultimate Blast", shop_names)

    def test_victory_quiz_tracks_attempts_and_accepts_threshold_answers(self):
        # There is no separate "algebra_quiz" state in the actual game — the
        # equation is shown inline on the "victory" screen (draw_victory
        # branches on quiz_equation being set), and submitting the right
        # answer clears quiz_equation while staying in "victory". This test
        # was asserting a state that never existed; it was only ever run
        # once the CardLibraryAndQuizTests/RewardAndProgressionTests name
        # collision above was fixed, which is how this mismatch surfaced.
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.start_victory_quiz()
        self.assertEqual(g.state, "victory")
        self.assertIsNotNone(g.quiz_equation)
        self.assertEqual(g.quiz_attempts, 0)
        self.assertEqual(g.quiz_answer, g.quiz_equation["answer"])
        g.submit_quiz_answer(g.quiz_answer)
        self.assertEqual(g.state, "victory")
        self.assertIsNone(g.quiz_equation)

    def test_curse_card_hurts_when_drawn(self):
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.player["hp"] = 20
        g.draw_pile = ["Misread Equation"]
        g.discard = []
        g.hand = []
        g.draw_cards(1)
        self.assertEqual(g.player["hp"], 14)
        self.assertNotIn("Misread Equation", g.hand)

    def test_misread_equation_is_never_offered_as_a_reward_or_shop_choice(self):
        """Regression test: Misread Equation used to be eligible for normal
        card-reward picks (it had no "base" key, so it slipped into
        REWARDABLE_CARDS) and had an extra 45% chance of being force-swapped
        into a reward via the old STATUS_CARD_NAMES set, which also
        included it. It must now be impossible to receive this card except
        through the forced Unknown-room curse (see EventTests)."""
        self.assertNotIn("Misread Equation", game_module.REWARDABLE_CARDS)
        self.assertNotIn("Misread Equation", game_module.STATUS_CARD_NAMES)
        self.assertIn("Misread Equation", game_module.CURSE_CARD_NAMES)

        g = game_module.Game()
        g.choose_character("Math Warrior")
        for _ in range(200):
            options = g.generate_card_reward_options()
            self.assertNotIn("Misread Equation", options)
        for _ in range(50):
            shop_names = {item["name"] for item in g.generate_shop_offer() if item["kind"] == "card"}
            self.assertNotIn("Misread Equation", shop_names)

    def test_probability_wizard_strike_rolls_each_die_individually_and_logs_them_together(self):
        # class_strike() used to call roll_dice() three times in a loop,
        # which spawned three floating-text popups stacked in the exact same
        # spot (only the last was ever readable) and three separate
        # battle-log lines. It now calls roll_dice_multi() once, which rolls
        # every die and reports them together in one line, e.g.
        # "Probability Missile: 3d3 rolled [2, 1, 3] = 6." — this locks that
        # behavior in.
        random.seed(42)
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 2, "block": 0, "ai": "aggressive", "intent": None})
        damage_before = g.enemy["hp"]
        total = g.class_strike()
        self.assertEqual(damage_before - g.enemy["hp"], total)
        self.assertGreaterEqual(total, 3)
        self.assertLessEqual(total, 9)
        # The combined-roll line is logged once, before the per-hit damage
        # lines that follow it (one per die, from deal_damage) — so check
        # the recent tail of the log rather than assuming it's the very
        # last entry.
        roll_lines = [e for e in g.combat_log[-4:] if "3d3 rolled [" in e and f"= {total}" in e]
        self.assertEqual(len(roll_lines), 1)

    def test_roll_dice_multi_reports_every_individual_die(self):
        random.seed(3)
        g = game_module.Game()
        g.choose_character("Math Warrior")
        rolls, total = g.roll_dice_multi(6, 4, "Test Roll")
        self.assertEqual(len(rolls), 4)
        self.assertEqual(sum(rolls), total)
        for r in rolls:
            self.assertGreaterEqual(r, 1)
            self.assertLessEqual(r, 6)
        # One combined flash, not one per die.
        self.assertEqual(g.floating_text[-1]["text"], f"4d6: {', '.join(str(r) for r in rolls)} = {total}")


class CombatFlowTests(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")

    def test_play_card_costs_energy_and_moves_to_discard(self):
        self.g.start_combat(self.g.generate_enemy(1))
        self.g.hand = ["Strike"]
        self.g.draw_pile = []
        energy_before = self.g.player["energy"]
        self.g.play_card("Strike")
        self.assertEqual(self.g.player["energy"], energy_before - 1)
        self.assertIn("Strike", self.g.discard)
        self.assertNotIn("Strike", self.g.hand)

    def test_cannot_play_card_without_energy(self):
        self.g.start_combat(self.g.generate_enemy(1))
        self.g.player["energy"] = 0
        self.g.hand = ["Strike"]
        self.g.play_card("Strike")
        self.assertIn("Strike", self.g.hand)

    def test_enemy_defeat_triggers_reward_flow_not_direct_map(self):
        self.g.start_combat({"name": "Paper Foe", "hp": 1, "max_hp": 1, "attack": 3, "block": 0, "ai": "aggressive", "intent": None})
        self.g.deal_damage(self.g.enemy, 5, "Strike")
        self.assertEqual(self.g.state, "card_reward")
        self.assertEqual(len(self.g.card_reward_options), 3)

    def test_elite_reward_flow_includes_relic_choice(self):
        self.g.start_combat({"name": "Elite Paper", "hp": 1, "max_hp": 1, "attack": 3, "block": 0, "ai": "aggressive", "intent": None}, elite=True)
        self.g.deal_damage(self.g.enemy, 5, "Strike")
        self.assertEqual(self.g.state, "card_reward")
        self.g.choose_card_reward(None)
        self.assertEqual(self.g.state, "relic_choice")
        self.assertEqual(len(self.g.relic_choice_options), 2)

    def test_boss_defeat_triggers_victory(self):
        self.g.start_combat({"name": "Test Boss", "hp": 1, "max_hp": 1, "attack": 3, "block": 0, "ai": "aggressive", "intent": None}, boss=True)
        self.g.deal_damage(self.g.enemy, 5, "Strike")
        self.assertEqual(self.g.state, "victory")

    def test_player_death_triggers_gameover(self):
        self.g.start_combat({"name": "Killer", "hp": 999, "max_hp": 999, "attack": 999, "block": 0, "ai": "aggressive", "intent": {"type": "attack", "value": 999}})
        self.g.player["block"] = 0
        self.g.deal_damage(self.g.player, 999, "Killer")
        self.assertEqual(self.g.state, "gameover")

    def test_hand_is_discarded_and_refilled_at_end_of_turn(self):
        self.g.start_combat(self.g.generate_enemy(1))
        self.assertEqual(len(self.g.hand), 5)
        original_hand = list(self.g.hand)
        self.g.end_player_turn()
        # a fresh hand of 5 is drawn for the next turn
        self.assertEqual(len(self.g.hand), 5)
        # no cards were lost or duplicated in the discard/draw/reshuffle cycle
        total = len(self.g.draw_pile) + len(self.g.discard) + len(self.g.hand)
        self.assertEqual(total, len(game_module.CHARACTER_OPTIONS["Math Warrior"]["deck"]))
        self.assertNotEqual(original_hand, [])

    def test_deck_reshuffles_when_draw_pile_empties(self):
        self.g.draw_pile = []
        self.g.discard = ["Strike"] * 5
        self.g.hand = []
        self.g.draw_cards(3)
        self.assertEqual(len(self.g.hand), 3)
        self.assertEqual(len(self.g.discard), 0)

    def test_dice_sigil_and_roll_fail_hook_integration(self):
        self.g.apply_relic("Proof by Contradiction")
        random.seed(3)
        block_before = self.g.player["block"]
        # force enough rolls that at least one should be "low"
        triggered = False
        for _ in range(50):
            self.g.player["block"] = 0
            self.g.roll_dice(20, "test")
            if self.g.player["block"] > 0:
                triggered = True
                break
        self.assertTrue(triggered)

    def test_ultimate_strike_deals_and_blocks_equal_to_one_d20(self):
        """Regression test: Ultimate Strike used to roll 2d20 for pure
        damage, which could one-shot an entire encounter. It now rolls a
        single d20 and grants that same amount as both damage and block."""
        self.g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})
        self.g.player["block"] = 0
        hp_before = self.g.enemy["hp"]
        roll = self.g.ultimate_strike_effect()
        self.assertEqual(self.g.enemy["hp"], hp_before - roll)
        self.assertEqual(self.g.player["block"], roll)

    def test_ultimate_blast_deals_one_point_five_times_one_d20(self):
        """Regression test: Ultimate Blast used to roll 2d20 for pure
        damage. It now rolls a single d20 and deals 1.5x that as damage."""
        self.g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})
        hp_before = self.g.enemy["hp"]
        amount = self.g.ultimate_blast_effect()
        self.assertEqual(self.g.enemy["hp"], hp_before - amount)
        self.assertEqual(amount, math.ceil(self.g.dice_roll * 1.5))

    def test_ultimate_cards_exhaust_and_skip_discard(self):
        self.g.start_combat(self.g.generate_enemy(1))
        self.g.hand = ["Ultimate Strike"]
        self.g.draw_pile = []
        self.g.player["energy"] = 3
        self.g.play_card("Ultimate Strike")
        self.assertNotIn("Ultimate Strike", self.g.hand)
        self.assertNotIn("Ultimate Strike", self.g.discard)

    def test_weak_enemy_halves_damage_taken_by_the_player(self):
        """Regression test for a bug a playtester reported ("the weak
        debuff doesn't seem to work"): applying Weak to the enemy DID
        correctly halve the player's HP loss all along, but enemy_turn()'s
        on-screen announcement (and the banner text) kept reporting the
        raw, pre-Weak intent value instead of what actually landed -- so
        it always looked like Weak had done nothing even though the HP
        total was right. Locks in that the actual damage taken is halved
        (already worked) AND that deal_damage()'s return value / the
        displayed message reflect the reduced number (the actual fix)."""
        self.g.start_combat({"name": "Weak Target", "hp": 999, "max_hp": 999, "attack": 12, "block": 0, "ai": "basic", "intent": {"type": "attack", "value": 12}})
        self.g.player["block"] = 0
        self.g.apply_status(self.g.enemy, "weak", 2)
        # start_combat's start_player_turn() re-rolls the enemy's intent
        # (roll_enemy_intent adds a random 1-5 for "basic" AI), so pin it
        # to a known value after combat setup rather than fighting the RNG.
        self.g.enemy["intent"] = {"type": "attack", "value": 12}
        hp_before = self.g.player["hp"]
        self.g.enemy_turn()
        dealt = hp_before - self.g.player["hp"]
        self.assertEqual(dealt, 6)
        # The displayed message must match what actually happened, not the
        # unmitigated intent value.
        self.assertIn("6", self.g.log_text)
        self.assertNotIn("12", self.g.log_text)

    def test_deal_damage_returns_the_actual_post_mitigation_amount(self):
        """deal_damage() now returns what actually landed (after Weak/
        Vulnerable/block), so callers like enemy_turn() can announce the
        real number instead of the pre-mitigation input."""
        self.g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})
        self.g.player["block"] = 0
        returned = self.g.deal_damage(self.g.player, 10, "test hit")
        self.assertEqual(returned, 10)
        self.g.enemy["block"] = 4
        returned = self.g.deal_damage(self.g.enemy, 10, "test hit")
        self.assertEqual(returned, 6)

    def test_multi_hit_attack_spawns_separate_visible_floating_texts(self):
        """Regression test for a playtester-reported visual bug: a multi-
        hit attack (e.g. Probability Missile, which calls deal_damage
        three times for one card play) used to spawn every floating damage
        number at the exact same x/y, so they rendered perfectly stacked
        and only one number was ever visible on screen -- even though each
        hit's damage (and the resulting HP/block totals) was correct the
        whole time. Each hit must now get its own, distinctly positioned
        floating text."""
        self.g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})
        self.g.floating_text = []
        for _ in range(3):
            self.g.deal_damage(self.g.enemy, 2, "Probability Missile")
        positions = {(item["x"], item["y"]) for item in self.g.floating_text}
        self.assertEqual(len(self.g.floating_text), 3)
        self.assertEqual(len(positions), 3, "each hit's floating text must render at a distinct position")


class EventTests(unittest.TestCase):
    """Covers "The Shrine of Expected Value" -- the three explicit player
    choices (Chad Wager / Giga Chad Wager / Coward Chad) that replaced the
    old passive 1d20 EVENT_OUTCOMES roll. Confirming a wager defers its
    actual roll behind a short die-tumble animation (wager_pending), so
    tests advance past it with _resolve_pending_wager()."""

    def setUp(self):
        random.seed(99)
        self.g = game_module.Game()
        self.g.choose_character("Probability Wizard")
        self.g.state = "event"

    def _resolve_pending_wager(self):
        self.assertIsNotNone(self.g.wager_pending, "expected a wager tumble to be pending")
        self.g.update_effects(dt=game_module.WAGER_TUMBLE_SECONDS + 0.01)
        self.assertIsNone(self.g.wager_pending)

    def test_safe_bet_win_pays_out_net_five_gold(self):
        self.g.player["gold"] = 50
        with unittest.mock.patch.object(random, "randint", return_value=6):
            self.g.choose_safe_bet()
            self._resolve_pending_wager()
        self.assertEqual(self.g.state, "event_result")
        self.assertEqual(self.g.roll_result, 6)
        self.assertEqual(self.g.player["gold"], 55)  # 50 - 10 stake + 15 win

    def test_safe_bet_loss_forfeits_only_the_stake(self):
        self.g.player["gold"] = 50
        with unittest.mock.patch.object(random, "randint", return_value=1):
            self.g.choose_safe_bet()
            self._resolve_pending_wager()
        self.assertEqual(self.g.state, "event_result")
        self.assertEqual(self.g.roll_result, 1)
        self.assertEqual(self.g.player["gold"], 40)  # 50 - 10 stake, no win

    def test_safe_bet_blocked_without_enough_gold(self):
        self.g.player["gold"] = 5
        self.g.choose_safe_bet()
        self.assertEqual(self.g.state, "event")  # never leaves the table
        self.assertIsNone(self.g.wager_pending)  # no tumble was even started
        self.assertEqual(self.g.player["gold"], 5)  # stake never taken

    def test_safe_bet_theoretical_ev_is_zero(self):
        self.assertAlmostEqual(game_module.WAGER_SAFE_BET_EV, 0.0)

    def test_second_wager_is_ignored_while_a_tumble_is_pending(self):
        self.g.player["gold"] = 50
        self.g.choose_safe_bet()
        self.assertIsNotNone(self.g.wager_pending)
        gold_after_first_stake = self.g.player["gold"]
        self.g.choose_safe_bet()  # should be a no-op -- a tumble is already in flight
        self.g.walk_away_from_wager()  # likewise
        self.assertEqual(self.g.player["gold"], gold_after_first_stake)
        self.assertEqual(self.g.state, "event")

    def test_degenerate_bet_win_grants_a_rare_relic_or_gold(self):
        self.g.player["hp"] = self.g.player["max_hp"]
        with unittest.mock.patch.object(random, "randint", return_value=20):
            self.g.choose_degenerate_bet()
            self._resolve_pending_wager()
        self.assertEqual(self.g.state, "event_result")
        self.assertEqual(self.g.roll_result, 20)
        got_relic = any(
            game_module.RELIC_LIBRARY.get(r, {}).get("rarity") == "rare"
            for r in self.g.player["relics"]
        )
        got_gold = self.g.player["gold"] > 0
        self.assertTrue(got_relic or got_gold)

    def test_degenerate_bet_loss_costs_exactly_the_hp_stake(self):
        start_hp = self.g.player["max_hp"]
        self.g.player["hp"] = start_hp
        with unittest.mock.patch.object(random, "randint", return_value=1):
            self.g.choose_degenerate_bet()
            self._resolve_pending_wager()
        self.assertEqual(self.g.state, "event_result")
        self.assertEqual(self.g.player["hp"], start_hp - game_module.WAGER_DEGENERATE_STAKE_HP)

    def test_degenerate_bet_can_end_the_run_if_it_is_lethal(self):
        self.g.player["hp"] = game_module.WAGER_DEGENERATE_STAKE_HP  # exactly lethal
        self.g.player["relics"] = []
        self.g.choose_degenerate_bet()
        self.assertEqual(self.g.state, "gameover")
        self.assertEqual(self.g.player["hp"], 0)
        self.assertIsNone(self.g.wager_pending)  # death is immediate, no tumble needed

    def test_walk_away_changes_nothing_and_returns_to_the_map(self):
        gold, hp = self.g.player["gold"], self.g.player["hp"]
        self.g.walk_away_from_wager()
        self.assertEqual(self.g.state, "map")
        self.assertEqual(self.g.player["gold"], gold)
        self.assertEqual(self.g.player["hp"], hp)

    def test_wager_methods_are_no_ops_outside_the_event_state(self):
        self.g.state = "map"
        gold, hp = self.g.player["gold"], self.g.player["hp"]
        self.g.choose_safe_bet()
        self.g.choose_degenerate_bet()
        self.assertEqual(self.g.state, "map")
        self.assertEqual(self.g.player["gold"], gold)
        self.assertEqual(self.g.player["hp"], hp)


class MapGenerationTests(unittest.TestCase):
    def test_generated_map_is_valid_many_times(self):
        for _ in range(50):
            rows, edges = game_module.generate_map()
            self.assertTrue(game_module.validate_map(rows, edges))

    def test_boss_is_on_final_row_only(self):
        rows, edges = game_module.generate_map()
        for row in rows[:-1]:
            for node in row:
                self.assertNotEqual(node["type"], "boss")
        self.assertEqual(len(rows[-1]), 1)
        self.assertEqual(rows[-1][0]["type"], "boss")

    def test_every_node_has_a_forward_path_to_the_boss(self):
        rows, edges = game_module.generate_map()
        boss_id = rows[-1][0]["id"]
        for row in rows[:-1]:
            for node in row:
                # BFS forward from this node
                seen = {node["id"]}
                frontier = [node["id"]]
                while frontier:
                    nxt = []
                    for nid in frontier:
                        for t in edges.get(nid, ()):
                            if t not in seen:
                                seen.add(t)
                                nxt.append(t)
                    frontier = nxt
                self.assertIn(boss_id, seen, f"{node['id']} cannot reach the boss")

    def test_row_zero_has_no_elite_rest_or_shop(self):
        rows, edges = game_module.generate_map()
        for node in rows[0]:
            self.assertIn(node["type"], ("combat", "event"))

    def test_pre_boss_row_is_all_rest(self):
        rows, edges = game_module.generate_map()
        for node in rows[-2]:
            self.assertEqual(node["type"], "rest")

    def test_available_node_ids_only_row_zero_at_start(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        available = g.available_node_ids()
        row0_ids = {n["id"] for n in g.map_rows[0]}
        self.assertEqual(available, row0_ids)

    def test_cannot_enter_a_node_that_is_not_available(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        far_node = g.map_rows[-1][0]  # the boss, not reachable turn 1
        g.enter_node(far_node)
        self.assertIsNone(g.current_node_id)
        self.assertEqual(g.state, "map")

    def test_entering_a_node_restricts_next_choices_to_its_edges(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        first = g.map_rows[0][0]
        g.state = "map"
        g.enter_node(first)
        # force back to map regardless of what the node triggered
        g.state = "map"
        g.enemy = None
        expected = set(g.map_edges.get(first["id"], ()))
        self.assertEqual(g.available_node_ids(), expected)


class ActScalingTests(unittest.TestCase):
    """self.floor resets to 1 at the start of every act (begin_next_act),
    so enemy/elite/boss generation must scale off something that keeps
    climbing across acts instead — see effective_floor(). Without this,
    Act 2 and Act 3 were exactly as easy as Act 1, which (combined with a
    since-removed auto-heal-every-floor bug) was massively inflating the
    win rate."""

    def setUp(self):
        random.seed(11)
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")

    def test_effective_floor_keeps_climbing_across_acts(self):
        self.g.act, self.g.floor = 1, 1
        floor_act1 = self.g.effective_floor()
        self.g.act, self.g.floor = 2, 1
        floor_act2 = self.g.effective_floor()
        self.g.act, self.g.floor = 3, 1
        floor_act3 = self.g.effective_floor()
        self.assertLess(floor_act1, floor_act2)
        self.assertLess(floor_act2, floor_act3)

    def test_enemies_get_harder_in_later_acts_at_the_same_in_act_floor(self):
        random.seed(5)
        self.g.act = 1
        enemy_act1 = self.g.generate_enemy(self.g.effective_floor())
        random.seed(5)
        self.g.act = 3
        enemy_act3 = self.g.generate_enemy(self.g.effective_floor())
        self.assertGreater(enemy_act3["hp"], enemy_act1["hp"])

    def test_entering_a_new_floor_does_not_auto_heal(self):
        """Regression test: floor transitions used to fully heal the player
        to max HP, erasing all run-attrition pressure and making rest sites
        and healing relics pointless. Only rest sites and act transitions
        restore HP now."""
        self.g.player["hp"] = 1
        node = self.g.map_rows[1][0]
        node["type"] = "event"  # sidestep starting combat; just test the heal side effect
        self.g.current_node_id = self.g.map_rows[0][0]["id"]
        self.g.visited_node_ids = {self.g.current_node_id}
        self.g.map_edges[self.g.current_node_id] = {node["id"]}
        self.g.enter_node(node)
        self.assertEqual(self.g.player["hp"], 1)


class RewardAndProgressionTests(unittest.TestCase):
    def test_shop_offer_never_exceeds_gold_silently(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.player["gold"] = 0
        offer = g.generate_shop_offer()
        item_index = 0
        g.shop_offer = offer
        g.buy_shop_item(item_index)
        self.assertFalse(offer[item_index]["bought"])

    def test_treasure_grants_unowned_relic(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.resolve_treasure()
        self.assertEqual(g.state, "treasure_result")
        self.assertTrue(len(g.player["relics"]) >= 0)

    def test_card_removal_from_shop(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        before = len(g.full_deck_cards())
        card = g.full_deck_cards()[0]
        g.remove_card_instance(card)
        self.assertEqual(len(g.full_deck_cards()), before - 1)


class PauseMenuTests(unittest.TestCase):
    """Escape opens a pause menu (window size presets, fullscreen toggle,
    quit) instead of forcing real fullscreen — this locks in that the menu
    renders, registers clickable buttons, and that handle_click intercepts
    everything while it's open instead of leaking clicks to whatever is
    underneath (a combat card, the map, etc.)."""

    @classmethod
    def setUpClass(cls):
        game_module.init_display()

    def test_escape_toggles_pause_menu_except_from_deck_view(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        esc = type("Evt", (), {"key": game_module.pygame.K_ESCAPE, "unicode": ""})()
        game_module.handle_keydown(g, esc)
        self.assertTrue(g.show_pause_menu)
        game_module.handle_keydown(g, esc)
        self.assertFalse(g.show_pause_menu)

        g.state = "deck_view"
        g.deck_return_state = "map"
        g.show_pause_menu = False
        game_module.handle_keydown(g, esc)
        self.assertEqual(g.state, "map")
        self.assertFalse(g.show_pause_menu)

    def test_pause_menu_renders_and_registers_buttons(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        g.show_pause_menu = True
        g.button_rects = {}
        game_module.render_frame(g)
        for key in ("pause_resume", "pause_toggle_fullscreen", "pause_quit"):
            self.assertIn(key, g.button_rects)
        for w, h in game_module.WINDOW_SIZE_PRESETS:
            self.assertIn(f"pause_window_{w}x{h}", g.button_rects)

    def test_pause_menu_intercepts_clicks_and_resume_closes_it(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        g.show_pause_menu = True
        g.button_rects = {}
        game_module.render_frame(g)
        resume_rect = g.button_rects["pause_resume"]
        game_module.handle_click(g, resume_rect.center)
        self.assertFalse(g.show_pause_menu)

    def test_pause_menu_quit_button_sets_quit_requested(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        g.show_pause_menu = True
        g.button_rects = {}
        game_module.render_frame(g)
        quit_rect = g.button_rects["pause_quit"]
        self.assertFalse(g.quit_requested)
        game_module.handle_click(g, quit_rect.center)
        self.assertTrue(g.quit_requested)

    def test_pause_menu_window_preset_resizes_and_clears_fullscreen(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        game_module.set_fullscreen(True)
        g.show_pause_menu = True
        g.button_rects = {}
        game_module.render_frame(g)
        w, h = game_module.WINDOW_SIZE_PRESETS[1]
        rect = g.button_rects[f"pause_window_{w}x{h}"]
        game_module.handle_click(g, rect.center)
        self.assertFalse(game_module.is_fullscreen)
        actual_size = game_module.real_display.get_size()
        if actual_size != (1024, 768):  # SDL fallback size in some headless/terminal test runs
            self.assertEqual(actual_size, (w, h))
        game_module.set_fullscreen(False)


class VictoryAndResetTests(unittest.TestCase):
    def test_reset_run_after_victory_starts_a_fresh_map(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        old_map = g.map_rows
        g.state = "victory"
        g.reset_run()
        self.assertEqual(g.state, "map")
        self.assertEqual(g.player["hp"], g.player["max_hp"])
        self.assertEqual(g.player["relics"], [game_module.CHARACTER_OPTIONS[g.character]["starting_relic"]])
        self.assertIsNot(g.map_rows, old_map)

    def test_reset_run_after_gameover(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "gameover"
        g.player["hp"] = 0
        g.reset_run()
        self.assertEqual(g.state, "map")
        self.assertGreater(g.player["hp"], 0)


class VictoryQuizLayoutTests(unittest.TestCase):
    """Regression test for the victory/act-clear equation screen: the answer
    input box used to be drawn only 38px below the equation text, which sat
    inside a 46px-tall title-font line, so the input field covered the
    bottom of the equation. draw_victory() now derives every element's
    position from the one above it instead of separate hardcoded numbers,
    so this mirrors that same math to lock in the non-overlap."""

    def test_equation_formula_input_box_attempts_and_submit_do_not_overlap(self):
        panel = game_module.pygame.Rect(230, 330, 820, 230)
        formula_y = panel.y + 30
        formula_h = 34  # approx FONT_H1 rendered height
        input_box = game_module.pygame.Rect(640 - 150, panel.y + 86, 300, 46)
        attempts_y = input_box.bottom + 14
        submit = game_module.pygame.Rect(640 - 100, input_box.bottom + 44, 200, 44)

        self.assertLessEqual(formula_y + formula_h, input_box.y)
        self.assertLessEqual(input_box.bottom, attempts_y)
        self.assertLessEqual(attempts_y, submit.y)
        self.assertLessEqual(submit.bottom, panel.bottom)


class CrashLoggingTests(unittest.TestCase):
    """Regression tests for the crash_log.txt mechanism added so a friend's
    'it crashed' report leaves something checkable — this is a windowed
    (console=False) build, so without this an uncaught exception just closes
    silently with nothing anyone could look at afterward."""

    def test_log_crash_writes_timestamp_and_traceback_to_the_log_file(self):
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, "crash_log.txt")
        original_path = game_module.CRASH_LOG_PATH
        game_module.CRASH_LOG_PATH = tmp_path
        try:
            try:
                raise ValueError("simulated crash for testing")
            except ValueError as exc:
                game_module.log_crash(exc)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("simulated crash for testing", content)
            self.assertIn("ValueError", content)
        finally:
            game_module.CRASH_LOG_PATH = original_path
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_log_crash_never_raises_even_if_the_log_path_is_unwritable(self):
        original_path = game_module.CRASH_LOG_PATH
        # A path inside a file (not a directory) can never be opened for
        # writing -- log_crash must swallow that, not propagate it and mask
        # the real exception that triggered it.
        game_module.CRASH_LOG_PATH = os.path.join(__file__, "crash_log.txt")
        try:
            game_module.log_crash(RuntimeError("boom"))
        finally:
            game_module.CRASH_LOG_PATH = original_path

    def test_writable_dir_is_not_the_meipass_resource_path(self):
        """Regression test for the specific bug this would otherwise cause:
        if crash logging used resource_path()/_MEIPASS like assets do, the
        log would be written into a --onefile build's temp extraction
        folder and vanish the moment the game closes -- useless for a friend
        to send back. writable_dir() must resolve to the real .exe/.py
        folder instead."""
        self.assertEqual(game_module.writable_dir(), os.path.dirname(os.path.abspath(game_module.__file__)))
        self.assertNotEqual(game_module.writable_dir(), game_module.ASSET_DIR)


class PowerAuraAndShopUiTests(unittest.TestCase):
    """Regression tests for the DBZ-style charge-up aura played when a power
    card is played, the power icon/tooltip row that replaced the old plain
    "Powers: X, Y" text, and the shop's gold badge (which replaced an
    illegible draw_center_panel subtitle)."""

    def setUp(self):
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})

    def test_gain_power_starts_the_aura_timer_on_the_player(self):
        self.assertIsNone(self.g.power_aura["actor"])
        self.g.gain_power("Central Limit Theorem")
        self.assertEqual(self.g.power_aura["actor"], "player")
        self.assertEqual(self.g.power_aura["timer"], self.g.power_aura["duration"])
        self.assertGreater(self.g.power_aura["timer"], 0)
        self.assertIn("Central Limit Theorem", self.g.player["active_powers"])

    def test_power_aura_decays_over_time_and_clears_the_actor(self):
        self.g.gain_power("Law of Large Numbers")
        self.g.update_effects(self.g.power_aura["duration"] / 2)
        self.assertEqual(self.g.power_aura["actor"], "player")
        self.assertGreater(self.g.power_aura["timer"], 0)
        self.g.update_effects(self.g.power_aura["duration"])
        self.assertLessEqual(self.g.power_aura["timer"], 0)
        self.assertIsNone(self.g.power_aura["actor"])

    def test_draw_power_aura_does_not_raise_across_the_full_progress_range(self):
        game_module.init_display()
        for character in ("Math Warrior", "Probability Wizard"):
            for progress in (0.0, 0.05, 0.5, 0.85, 1.0):
                game_module.draw_power_aura(game_module.screen, character, 200, 250, 270, progress)

    def test_powers_row_renders_with_and_without_active_powers(self):
        game_module.init_display()
        # No active powers yet: draws the "Powers: none yet" placeholder,
        # mirroring draw_relic_panel's own "none yet" state, rather than
        # vanishing entirely — this is the persistent HUD panel (not the
        # 2s charge-up flash), so it must always be visible during combat.
        game_module.draw_powers_row(game_module.screen, self.g, 680, 16, 300)
        self.g.gain_power("Central Limit Theorem")
        self.g.gain_power("Law of Large Numbers")
        # Should not raise with two active powers, mouse hovering over one.
        game_module.draw_powers_row(game_module.screen, self.g, 680, 16, 300)

    def test_powers_row_is_called_every_combat_frame_not_gated_by_the_aura_flash(self):
        """Regression test: the power icon row is a permanent HUD element
        that must render every frame draw_combat runs, not something that
        only shows up during/after the ~2s charge-up aura. Locks in that
        draw_combat() calls draw_powers_row() unconditionally."""
        source = inspect.getsource(game_module.draw_combat)
        self.assertIn("draw_powers_row(", source)
        # Make sure that call isn't nested inside the power_aura timer check.
        aura_check_idx = source.index('game.power_aura["actor"] == "player"')
        powers_row_idx = source.index("draw_powers_row(")
        self.assertLess(powers_row_idx, aura_check_idx)

    def test_player_power_badges_renders_with_and_without_active_powers(self):
        """Second, more prominent power indicator pinned directly to the
        player's sprite stage -- added because players reported not
        noticing the top-strip draw_powers_row() HUD panel even after it
        was redesigned to be a permanent boxed element. Must not raise
        whether or not any power is active."""
        game_module.init_display()
        stage = game_module.pygame.Rect(60, 200, 300, 320)
        # No active powers: should just return without drawing anything.
        game_module.draw_player_power_badges(game_module.screen, self.g, stage)
        self.g.gain_power("Central Limit Theorem")
        self.g.gain_power("Law of Large Numbers")
        game_module.draw_player_power_badges(game_module.screen, self.g, stage)

    def test_player_power_badges_is_called_from_draw_combat(self):
        """Locks in that draw_combat() actually wires up the character-
        overlay badges, not just defines the function."""
        source = inspect.getsource(game_module.draw_combat)
        self.assertIn("draw_player_power_badges(", source)

    def test_power_aura_layers_real_art_over_the_procedural_effect(self):
        """draw_power_aura() blits power_aura_warrior/wizard.png on top of
        the procedural rings/sparks. (An earlier version of those two PNGs
        was salvaged from a flattened JPEG mockup with a checkerboard
        "transparent" placeholder baked in -- no real alpha -- and blitting
        it showed up in-game as a large, not-actually-transparent block.
        That pair was replaced with black-background renders re-keyed with a
        proper premultiplied-alpha recovery; see test_power_aura_art_files_
        have_real_gradient_alpha below for the asset-level check.)"""
        source = inspect.getsource(game_module.draw_power_aura)
        code_lines = [ln for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        live_code = "\n".join(code_lines)
        self.assertIn("real_art = get_art(", live_code)
        self.assertIn(".blit(real_art", live_code)

    def test_power_aura_art_files_have_real_gradient_alpha_not_a_flat_block(self):
        """Asset-level check on the actual PNG files (independent of
        pygame): each must be RGBA with a real, varied alpha channel -- some
        fully transparent pixels (the black background was keyed out), some
        fully opaque (the flame core), and a smooth spread between, not the
        flat "0 or 255 only" pattern a bad key or a solid rectangle would
        produce. This is exactly the property that was missing from the
        checkerboard-sourced version that caused the "covers the whole
        screen, doesn't go transparent" bug."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not available in this environment")
        for name in ("power_aura_warrior", "power_aura_wizard"):
            path = os.path.join("assets", "art", f"{name}.png")
            self.assertTrue(os.path.exists(path), f"missing {path}")
            im = Image.open(path)
            self.assertEqual(im.mode, "RGBA")
            alpha = im.split()[-1]
            hist = alpha.histogram()
            transparent_frac = hist[0] / sum(hist)
            opaque_frac = hist[255] / sum(hist)
            midtone_frac = 1.0 - transparent_frac - opaque_frac
            self.assertGreater(transparent_frac, 0.1, f"{name}: expected a real transparent background")
            self.assertGreater(opaque_frac, 0.02, f"{name}: expected a real opaque core")
            self.assertGreater(midtone_frac, 0.05, f"{name}: expected soft gradient edges, not a hard cutout")

    def test_gold_badge_renders_without_raising(self):
        game_module.init_display()
        badge_rect = game_module.draw_gold_badge(game_module.screen, 400, 100, self.g.player["gold"])
        self.assertEqual(badge_rect.width, 210)
        self.assertEqual(badge_rect.height, 52)

    def test_shop_backdrop_renders_without_raising_and_is_wired_into_draw_shop(self):
        game_module.init_display()
        game_module.draw_shop_backdrop(game_module.screen)
        source = inspect.getsource(game_module.draw_shop)
        self.assertIn("background_fn=draw_shop_backdrop", source)

    def test_shop_screen_no_longer_uses_gold_as_a_dim_subtitle(self):
        """The old draw_shop() passed f"Gold: {gold}" as draw_center_panel's
        MUTED-gray subtitle, which is why the user couldn't see it. Locks in
        that draw_shop() now passes no subtitle there at all (the gold badge
        carries that information instead, with its own GOLD-colored text)."""
        source = inspect.getsource(game_module.draw_shop)
        self.assertNotIn('f"Gold: {game.player', source)
        self.assertIn("draw_gold_badge(", source)


class RenderingSmokeTests(unittest.TestCase):
    """Exercises every render function with the stub/real pygame backend to
    catch API-usage bugs (e.g. calls incompatible with the installed pygame
    version) without needing a visible display."""

    @classmethod
    def setUpClass(cls):
        game_module.init_display()

    def test_fullscreen_toggle_updates_state_and_present_transform(self):
        """Regression test for the fullscreen feature: toggling should flip
        is_fullscreen, recreate real_display, and keep the present() letterbox
        transform in sync with whatever size real_display now reports."""
        was_fullscreen = game_module.is_fullscreen
        try:
            game_module.set_fullscreen(True)
            self.assertTrue(game_module.is_fullscreen)
            self.assertIsNotNone(game_module.real_display)
            rw, rh = game_module.real_display.get_size()
            expected_scale = min(rw / game_module.WIDTH, rh / game_module.HEIGHT)
            self.assertAlmostEqual(game_module._present_scale, max(0.01, expected_scale), places=4)

            game_module.set_fullscreen(False)
            self.assertFalse(game_module.is_fullscreen)
            actual_size = game_module.real_display.get_size()
            if actual_size != (1024, 768):  # SDL fallback size in some headless/terminal test runs
                self.assertEqual(actual_size, (game_module.WIDTH, game_module.HEIGHT))
                self.assertAlmostEqual(game_module._present_scale, 1.0, places=4)
        finally:
            game_module.set_fullscreen(was_fullscreen)

    def test_screen_pos_from_real_round_trips_through_present_transform(self):
        game_module.set_fullscreen(False)
        game_module.real_display = game_module.pygame.display.set_mode((2560, 1080))
        game_module._update_present_transform()
        try:
            canvas_pos = (100, 50)
            scale = game_module._present_scale
            ox, oy = game_module._present_offset
            real_pos = (int(canvas_pos[0] * scale) + ox, int(canvas_pos[1] * scale) + oy)
            mapped_back = game_module.screen_pos_from_real(real_pos)
            self.assertAlmostEqual(mapped_back[0], canvas_pos[0], delta=1)
            self.assertAlmostEqual(mapped_back[1], canvas_pos[1], delta=1)
        finally:
            game_module.set_fullscreen(False)

    def test_every_screen_renders_without_raising(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")

        g.state = "title"
        game_module.render_frame(g)
        g.state = "character_select"
        game_module.render_frame(g)
        g.state = "map"
        game_module.render_frame(g)

        g.start_combat(g.generate_enemy(1))
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "event"
        g.button_rects = {}
        game_module.render_frame(g)
        g.player["gold"] = max(g.player["gold"], game_module.WAGER_SAFE_BET_STAKE_GOLD)
        g.choose_safe_bet()
        g.button_rects = {}
        game_module.render_frame(g)  # mid-tumble
        g.update_effects(dt=game_module.WAGER_TUMBLE_SECONDS + 0.01)
        g.button_rects = {}
        game_module.render_frame(g)  # event_result

        g.state = "combat"
        g.push_luck_active = True
        g.push_luck_banked = 4
        g.button_rects = {}
        game_module.render_frame(g)  # Gambler's Flurry push-your-luck banner
        g.push_luck_active = False

        g.card_reward_options = ["Strike", "Defend", "Chaos Bloom"]
        g.state = "card_reward"
        g.button_rects = {}
        game_module.render_frame(g)

        g.relic_choice_options = ["Pi's Defense", "Dice Sigil"]
        g.state = "relic_choice"
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "rest_choice"
        g.button_rects = {}
        game_module.render_frame(g)

        g.rest_upgrade_options = ["Strike", "Defend"]
        g.state = "rest_upgrade_pick"
        g.button_rects = {}
        game_module.render_frame(g)

        g.shop_offer = g.generate_shop_offer()
        g.state = "shop"
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "shop_remove_pick"
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "treasure_result"
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "victory"
        g.button_rects = {}
        game_module.render_frame(g)

        g.state = "gameover"
        g.button_rects = {}
        game_module.render_frame(g)


class SpriteStateTests(unittest.TestCase):
    """AssetManager / hero sprite State 1/2/3 handling (Chad Math rebrand)."""

    def test_hero_art_name_falls_back_to_base_portrait_when_state_missing(self):
        # HERO_STATE_ART has no "select"/"ready"/"powerup" PNGs shipped yet
        # (they're user-supplied), so every state should resolve back to
        # the existing single-pose PORTRAIT_ART entry.
        for character in ("Math Warrior", "Probability Wizard"):
            for state in game_module.HERO_STATES:
                name = game_module.ASSET_MANAGER.hero_art_name(character, state)
                self.assertEqual(name, game_module.HERO_STATE_ART[character][state])

    def test_get_hero_sprite_returns_none_when_nothing_on_disk(self):
        # No hero_*_*.png files are shipped in this test environment, and
        # PORTRAIT_ART's own files aren't guaranteed either -- either way
        # this must not raise, and returns None so callers draw procedural.
        game_module.init_display()
        for character in ("Math Warrior", "Probability Wizard"):
            for state in game_module.HERO_STATES:
                game_module.ASSET_MANAGER.get_hero_sprite(character, state, 190)  # must not raise

    def test_sprite_functions_accept_a_state_argument_and_never_raise(self):
        game_module.init_display()
        for state in game_module.HERO_STATES:
            game_module.draw_math_warrior_sprite(game_module.screen, 100, 100, 1, state=state)
            game_module.draw_probability_wizard_sprite(game_module.screen, 100, 100, 1, state=state)

    def test_combat_swaps_to_powerup_state_while_power_aura_is_active(self):
        """draw_combat() must pick state="powerup" for the player's sprite
        while the charge-up aura is running, and "ready" otherwise -- that
        swap (State 2 -> State 3 -> back to State 2) is the whole point of
        AssetManager's per-state lookup."""
        source = inspect.getsource(game_module.draw_combat)
        self.assertIn('"powerup" if (game.power_aura["actor"] == "player"', source)
        self.assertIn('else "ready"', source)

    def test_character_select_uses_the_select_state(self):
        source = inspect.getsource(game_module.draw_character_select)
        self.assertIn('state="select"', source)

    def test_power_pose_duration_is_1_2_seconds(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        self.assertEqual(g.power_aura["duration"], 1.2)
        g.trigger_power_pose("player")
        self.assertEqual(g.power_aura["timer"], 1.2)

    def test_gain_power_ultimate_and_huge_roll_all_trigger_the_power_pose(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.start_combat({"name": "Dummy", "hp": 999, "max_hp": 999, "attack": 0, "block": 0, "ai": "aggressive", "intent": None})

        g.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        g.gain_power("Central Limit Theorem")
        self.assertEqual(g.power_aura["actor"], "player")

        g.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        g.ultimate_strike_effect()
        self.assertEqual(g.power_aura["actor"], "player")

        g.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        with unittest.mock.patch.object(random, "randint", return_value=20):
            g.roll_dice(20, "test max roll")
        self.assertEqual(g.power_aura["actor"], "player")

        g.power_aura = {"actor": None, "timer": 0.0, "duration": 1.2}
        with unittest.mock.patch.object(random, "randint", return_value=1):
            g.roll_dice(20, "test min roll")
        self.assertIsNone(g.power_aura["actor"])  # a low roll shouldn't pose


class NewMonsterArchetypeTests(unittest.TestCase):
    """Act 1/2 probabilistic foe archetypes: Decimal Demon, Fractal Fiend,
    Vector Viper, Radical Wraith (elite)."""

    def _combat(self, ai, **overrides):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        enemy = {"name": "Test Foe", "hp": 999, "max_hp": 999, "attack": 5, "block": 0, "ai": ai, "intent": None, "statuses": {}}
        enemy = game_module.apply_special_enemy_traits(enemy)
        enemy.update(overrides)
        g.start_combat(enemy)
        g.player["block"] = 0
        return g

    def test_new_archetypes_are_registered_in_the_template_pools(self):
        enemy_ais = {ai for _, _, _, ai in game_module.ENEMY_TEMPLATES}
        elite_ais = {ai for _, _, _, ai in game_module.ELITE_TEMPLATES}
        self.assertIn("decimal_demon", enemy_ais)
        self.assertIn("fractal_fiend", enemy_ais)
        self.assertIn("vector_viper", enemy_ais)
        self.assertIn("radical_wraith", elite_ais)

    def test_decimal_demon_deals_1d10_and_applies_truncate(self):
        g = self._combat("decimal_demon")
        with unittest.mock.patch.object(random, "randint", return_value=7):
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"], {"type": "decimal_shift", "value": 7})
        hp_before = g.player["hp"]
        g.enemy_turn()
        self.assertEqual(hp_before - g.player["hp"], 7)
        self.assertGreater(g.player["statuses"].get("truncate", 0), 0)

    def test_truncate_status_reduces_the_players_next_dice_rolls(self):
        g = self._combat("decimal_demon")
        g.apply_status(g.player, "truncate", 2)
        with unittest.mock.patch.object(random, "randint", return_value=6):
            rolled = g.roll_dice(10, "test")
        self.assertEqual(rolled, 5)  # 6 - 1 truncate

    def test_truncate_never_reduces_a_roll_below_1(self):
        g = self._combat("decimal_demon")
        g.apply_status(g.player, "truncate", 2)
        with unittest.mock.patch.object(random, "randint", return_value=1):
            rolled = g.roll_dice(10, "test")
        self.assertEqual(rolled, 1)

    def test_decimal_demon_passive_reduces_odd_damage_taken_by_2(self):
        g = self._combat("decimal_demon")
        hp_before = g.enemy["hp"]
        g.deal_damage(g.enemy, 5, "test odd hit")  # odd -> reduced by 2 -> 3
        self.assertEqual(hp_before - g.enemy["hp"], 3)

    def test_decimal_demon_passive_does_not_reduce_even_damage(self):
        g = self._combat("decimal_demon")
        hp_before = g.enemy["hp"]
        g.deal_damage(g.enemy, 6, "test even hit")  # even -> unaffected
        self.assertEqual(hp_before - g.enemy["hp"], 6)

    def test_fractal_fiend_doubles_triggers_a_recursive_extra_hit(self):
        g = self._combat("fractal_fiend")
        with unittest.mock.patch.object(random, "randint", side_effect=[3, 3, 2]):
            g.roll_enemy_intent(g.enemy)
        # 2d4 doubles (3,3)=6, plus recursive 1d4=2 -> 8 total.
        self.assertEqual(g.enemy["intent"], {"type": "fractal_recursion", "value": 8, "doubles": True})

    def test_fractal_fiend_no_doubles_is_just_2d4(self):
        g = self._combat("fractal_fiend")
        with unittest.mock.patch.object(random, "randint", side_effect=[2, 4]):
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"], {"type": "fractal_recursion", "value": 6, "doubles": False})

    def test_fractal_fiend_gains_block_once_it_crosses_half_hp(self):
        g = self._combat("fractal_fiend")
        g.enemy["max_hp"] = 56
        g.enemy["hp"] = 56
        g.enemy["block"] = 0
        g.deal_damage(g.enemy, 30, "big hit")  # drops to 26, below half of 56
        self.assertEqual(g.enemy["block"], 12)
        self.assertFalse(g.enemy["fractal_split_pending"])
        # Doesn't re-trigger on a second hit.
        g.enemy["block"] = 0
        g.deal_damage(g.enemy, 5, "second hit")
        self.assertEqual(g.enemy["block"], 0)

    def test_vector_viper_alternates_pierce_and_block_intents(self):
        g = self._combat("vector_viper")
        # start_combat()'s own start_player_turn() already called
        # roll_enemy_intent() once during setup, so pin the phase back to
        # its "about to pierce" starting point rather than assume it.
        g.enemy["viper_phase"] = 0
        with unittest.mock.patch.object(random, "randint", return_value=2):
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"]["type"], "vector_pierce")
        g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"]["type"], "block")
        with unittest.mock.patch.object(random, "randint", return_value=2):
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"]["type"], "vector_pierce")

    def test_vector_pierce_bypasses_player_block_entirely(self):
        g = self._combat("vector_viper")
        g.player["block"] = 50
        g.enemy["intent"] = {"type": "vector_pierce", "value": 9, "rolls": [3, 3, 3]}
        hp_before = g.player["hp"]
        g.enemy_turn()
        self.assertEqual(hp_before - g.player["hp"], 9)
        self.assertEqual(g.player["block"], 50)  # untouched

    def test_radical_wraith_perfect_square_triples_damage_and_heals(self):
        g = self._combat("radical_wraith")
        g.enemy["hp"] = 40
        g.enemy["max_hp"] = 62
        with unittest.mock.patch.object(random, "randint", return_value=4):  # perfect square
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"], {"type": "root_extraction", "value": 12, "roll": 4, "square": True})
        hp_before = g.player["hp"]
        g.enemy_turn()
        self.assertEqual(hp_before - g.player["hp"], 12)
        self.assertEqual(g.enemy["hp"], 44)  # healed by the roll (4)

    def test_radical_wraith_non_square_deals_flat_roll_damage_no_heal(self):
        g = self._combat("radical_wraith")
        g.enemy["hp"] = 40
        g.enemy["max_hp"] = 62
        with unittest.mock.patch.object(random, "randint", return_value=7):  # not a perfect square
            g.roll_enemy_intent(g.enemy)
        self.assertEqual(g.enemy["intent"], {"type": "root_extraction", "value": 7, "roll": 7, "square": False})
        hp_before = g.player["hp"]
        g.enemy_turn()
        self.assertEqual(hp_before - g.player["hp"], 7)
        self.assertEqual(g.enemy["hp"], 40)  # no heal


class RestSiteOverhaulTests(unittest.TestCase):
    def setUp(self):
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.state = "rest_choice"

    def test_rest_heal_amount_uses_floor_not_round(self):
        self.g.player["max_hp"] = 80
        base, total = self.g.rest_heal_amount()
        self.assertEqual(base, 24)  # floor(80 * 0.30) = 24, matches the spec's worked example
        self.assertEqual(total, base)  # no relics in this test

    def test_rest_choice_screen_renders_a_prominent_hp_bar(self):
        """Regression test for the reported bug: the rest site used to show
        no HP bar anywhere on screen. render_health_bar_with_block must be
        called from draw_rest_choice."""
        source = inspect.getsource(game_module.draw_rest_choice)
        self.assertIn("render_health_bar_with_block(screen, hp_bar", source)

    def test_rest_choice_offers_all_four_named_options(self):
        game_module.init_display()
        self.g.button_rects = {}
        game_module.draw_rest_choice(self.g)
        for action in ("heal", "study", "audit", "skip"):
            self.assertIn(f"rest_{action}", self.g.button_rects)

    def test_audit_deck_opens_deck_view_and_returns_to_rest_choice(self):
        game_module.init_display()
        self.g.button_rects = {}
        game_module.draw_rest_choice(self.g)
        audit_rect = self.g.button_rects["rest_audit"]
        game_module.handle_click(self.g, audit_rect.center)
        self.assertEqual(self.g.state, "deck_view")
        self.assertEqual(self.g.deck_return_state, "rest_choice")

    def test_rest_choice_shows_the_exact_calculation_live_before_committing(self):
        """Spec: 'Show the exact calculation dynamically on screen (e.g.
        Heals floor(80 * 0.30) = 24 HP)' -- this must be visible on the
        Rest & Recompute card itself, since the resulting log line gets
        immediately overwritten by return_to_map()'s "Choose your next
        path." the instant the player actually clicks it."""
        game_module.init_display()
        self.g.player["max_hp"] = 80
        self.g.player["hp"] = 10
        self.g.button_rects = {}
        source_before = None
        # draw_rest_choice renders the formula text directly; capture what
        # it computes via the same helper it calls.
        base, total = self.g.rest_heal_amount()
        self.assertEqual(base, 24)
        game_module.draw_rest_choice(self.g)  # must not raise with these HP values
        self.assertIn("rest_heal", self.g.button_rects)


class MathInspectorTests(unittest.TestCase):
    def setUp(self):
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")

    def test_card_ev_matches_hand_computed_values(self):
        self.assertAlmostEqual(game_module.card_ev(self.g, "Arcane Dart"), 5.5)
        self.assertAlmostEqual(game_module.card_ev(self.g, "Dice Slash"), 10.5)
        self.assertAlmostEqual(game_module.card_ev(self.g, "Standard Deviation"), 18.0)
        self.assertAlmostEqual(game_module.card_ev(self.g, "Ultimate Blast"), 15.75)

    def test_strike_ev_is_character_dependent(self):
        self.assertAlmostEqual(game_module.card_ev(self.g, "Strike"), 8.0)  # Math Warrior: flat 8
        self.g.choose_character("Probability Wizard")
        self.assertAlmostEqual(game_module.card_ev(self.g, "Strike"), 6.0)  # 3d3 -> 3*2.0

    def test_non_damage_cards_have_no_ev(self):
        self.assertIsNone(game_module.card_ev(self.g, "Defend"))
        self.assertIsNone(game_module.card_ev(self.g, "Central Limit Theorem"))

    def test_enemy_intent_ev_formulas_for_the_four_new_archetypes(self):
        self.assertEqual(game_module.enemy_intent_ev({"type": "decimal_shift", "value": 7})[1], 5.5)
        self.assertAlmostEqual(game_module.enemy_intent_ev({"type": "fractal_recursion", "value": 6, "doubles": False})[1], 5.625)
        self.assertEqual(game_module.enemy_intent_ev({"type": "vector_pierce", "value": 9})[1], 7.5)
        self.assertAlmostEqual(game_module.enemy_intent_ev({"type": "root_extraction", "value": 7})[1], 106 / 12)

    def test_block_intent_has_no_ev(self):
        self.assertIsNone(game_module.enemy_intent_ev({"type": "block", "value": 10}))

    def test_m_key_toggles_math_inspector_only_in_combat(self):
        self.g.state = "map"
        game_module.handle_keydown(self.g, type("Evt", (), {"key": game_module.pygame.K_m, "unicode": "m"})())
        self.assertFalse(self.g.show_math_inspector)

        self.g.start_combat({"name": "Dummy", "hp": 10, "max_hp": 10, "attack": 1, "block": 0, "ai": "aggressive", "intent": None})
        game_module.handle_keydown(self.g, type("Evt", (), {"key": game_module.pygame.K_m, "unicode": "m"})())
        self.assertTrue(self.g.show_math_inspector)
        game_module.handle_keydown(self.g, type("Evt", (), {"key": game_module.pygame.K_m, "unicode": "m"})())
        self.assertFalse(self.g.show_math_inspector)

    def test_math_inspector_renders_without_raising_with_and_without_hand_cards(self):
        game_module.init_display()
        self.g.start_combat({"name": "Dummy", "hp": 10, "max_hp": 10, "attack": 1, "block": 0, "ai": "aggressive", "intent": None})
        self.g.show_math_inspector = True
        self.g.hand = []
        game_module.draw_math_inspector(self.g)
        self.g.hand = ["Strike", "Defend"]
        game_module.draw_math_inspector(self.g)


# ---------------------------------------------------------------------------
# Round 2: nested asset folders, hero sheet slicing, background art,
# the Derivative Dragon's two phases, Rest Site v2 renames, and the
# empirical roll-history / Law of Large Numbers tracking.
#
# NOTE on class naming: unittest's default discovery walks dir(module),
# which is alphabetical by class name, not file order. ImportSafetyTests'
# test_import_does_not_start_game_loop asserts game_module._DISPLAY_READY
# is still False, so any class here that calls init_display() must sort
# after "ImportSafetyTests" alphabetically -- hence the "RoundTwo..." prefix
# (all start with "R", same trick already used by RestSiteOverhaulTests).
# ---------------------------------------------------------------------------

class RoundTwoAssetManagerTests(unittest.TestCase):
    """The nested assets/heroes|backgrounds|monsters/ AssetManager rewrite.
    Uses a fresh AssetManager() per test (not the module-level singleton,
    which caches) and mocks _load_raw directly rather than staging real PNG
    files -- the stub's fake pygame.image.load doesn't actually decode
    image bytes, so controlling the "loaded" Surface's size directly is the
    only way to exercise the scaling/slicing math headlessly."""

    def setUp(self):
        self.am = game_module.AssetManager()

    def test_get_background_returns_none_without_raising_when_file_missing(self):
        # "not_a_real_key" isn't in BACKGROUND_FILES at all, so this holds
        # regardless of whether real background art has been dropped into
        # assets/backgrounds/ (it has, as of the art pass that supplied
        # campfire_rest.png -- see the nested-vs-missing test below for
        # that path instead).
        self.assertIsNone(self.am.get_background("not_a_real_key"))

    def test_get_background_returns_none_without_raising_when_directory_is_empty(self):
        # Same guarantee, but pinned to an empty temp directory so it holds
        # independent of whatever real art has since been dropped into the
        # shipped assets/backgrounds/ folder.
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "BACKGROUNDS_DIR", empty_dir):
            self.assertIsNone(self.am.get_background("rest_site"))
            self.assertIsNone(self.am.get_background("combat"))

    def test_get_background_stretches_to_the_window_resolution(self):
        fake = game_module.pygame.Surface((400, 300))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            bg = self.am.get_background("rest_site")
        self.assertIsNotNone(bg)
        self.assertEqual(bg.get_size(), (game_module.WIDTH, game_module.HEIGHT))

    def test_get_monster_sprite_nested_convention_scales_to_max_height(self):
        fake = game_module.pygame.Surface((200, 100))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            sprite = self.am.get_monster_sprite("Decimal Demon", 150)
        self.assertIsNotNone(sprite)
        self.assertEqual(sprite.get_height(), 150)
        self.assertEqual(sprite.get_width(), 300)  # 200/100 * 150

    def test_get_monster_sprite_falls_back_to_none_when_nothing_on_disk(self):
        # Pinned to an empty temp directory (rather than asserting nothing
        # is shipped) so this holds independent of the real monster art
        # that has since been dropped into assets/monsters/ -- the
        # guarantee under test is the fallback behavior, not the absence
        # of art.
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "MONSTERS_DIR", empty_dir):
            sprite = self.am.get_monster_sprite("Decimal Demon", 150)
        self.assertIsNone(sprite)

    def test_draw_enemy_portrait_uses_the_nested_monster_sprite_when_present(self):
        # Regression test: draw_enemy_portrait() used to only ever consult
        # the OLD flat ENEMY_ART/assets/art/ convention and never called
        # AssetManager.get_monster_sprite() at all, so real art dropped
        # into assets/monsters/ (e.g. decimal_demon.png) was silently
        # ignored and combat always rendered the procedural fallback shape
        # instead, even though the file was present and loadable.
        fake = game_module.pygame.Surface((200, 100))
        with unittest.mock.patch.object(
            game_module.ASSET_MANAGER, "get_monster_sprite", return_value=fake
        ) as mock_get, unittest.mock.patch.object(
            game_module, "draw_real_art_image"
        ) as mock_blit, unittest.mock.patch.object(
            game_module, "_supersample_sprite"
        ) as mock_procedural:
            game_module.draw_enemy_portrait(game_module.screen, "Decimal Demon", 100, 100, scale=1)
        mock_get.assert_called_once_with("Decimal Demon", 190)
        mock_blit.assert_called_once()
        mock_procedural.assert_not_called()

    def test_draw_enemy_portrait_falls_back_to_procedural_when_no_art_anywhere(self):
        with unittest.mock.patch.object(
            game_module.ASSET_MANAGER, "get_monster_sprite", return_value=None
        ), unittest.mock.patch.object(
            game_module, "_supersample_sprite"
        ) as mock_procedural:
            game_module.draw_enemy_portrait(game_module.screen, "Totally Unknown Foe", 100, 100, scale=1)
        mock_procedural.assert_called_once()

    def test_draw_boss_portrait_uses_the_nested_monster_sprite_when_present(self):
        fake = game_module.pygame.Surface((200, 100))
        with unittest.mock.patch.object(
            game_module.ASSET_MANAGER, "get_monster_sprite", return_value=fake
        ) as mock_get, unittest.mock.patch.object(
            game_module, "draw_real_art_image"
        ) as mock_blit, unittest.mock.patch.object(
            game_module, "_supersample_sprite"
        ) as mock_procedural:
            game_module.draw_boss_portrait(game_module.screen, "The Derivative Dragon", 100, 100, scale=1)
        mock_get.assert_called_once_with("The Derivative Dragon", 190)
        mock_blit.assert_called_once()
        mock_procedural.assert_not_called()

    def test_hero_sheet_slices_into_the_three_named_states_by_column(self):
        fake = game_module.pygame.Surface((300, 100))  # 3 frames of 100x100
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            sliced = self.am._slice_hero_sheet("Math Warrior", 100)
        self.assertIsNotNone(sliced)
        self.assertEqual(set(sliced.keys()), set(game_module.HERO_STATES))
        for state in game_module.HERO_STATES:
            self.assertEqual(sliced[state].get_height(), 100)

    def test_hero_sheet_slicing_never_raises_on_a_degenerate_sheet(self):
        # A 1x1 placeholder (e.g. a stub/corrupt file) must fall through to
        # None cleanly rather than crash on a zero-width frame slice.
        fake = game_module.pygame.Surface((1, 1))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            sliced = self.am._slice_hero_sheet("Math Warrior", 100)
        self.assertIsNone(sliced)

    def test_get_hero_sprite_prefers_the_sheet_over_the_flat_single_file(self):
        fake = game_module.pygame.Surface((300, 90))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            sprite = self.am.get_hero_sprite("Math Warrior", "ready", 90)
        self.assertIsNotNone(sprite)
        self.assertEqual(sprite.get_height(), 90)

    def test_combat_backdrop_uses_the_nested_background_when_present(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        fake = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_background", return_value=fake) as mocked:
            game_module.draw_combat_backdrop(g)
        mocked.assert_called_with("combat")

    def test_rest_choice_uses_the_nested_background_when_present(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "rest_choice"
        g.button_rects = {}
        fake = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_background", return_value=fake) as mocked:
            game_module.draw_rest_choice(g)
        mocked.assert_called_with("rest_site")

    def test_map_uses_the_spire_ascent_background_when_present(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        g.button_rects = {}
        fake = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_background", return_value=fake) as mocked:
            game_module.draw_map(g)
        mocked.assert_called_with("map")

    def test_map_falls_back_to_the_procedural_spire_background_when_missing(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "map"
        g.button_rects = {}
        with unittest.mock.patch.object(
            game_module.ASSET_MANAGER, "get_background", return_value=None
        ), unittest.mock.patch.object(
            game_module, "draw_spire_background"
        ) as mock_procedural:
            game_module.draw_map(g)
        mock_procedural.assert_called_once()


class RoundTwoBossPhaseTests(unittest.TestCase):
    """The Derivative Dragon: fixed HP:140, Phase 1 "Chain Rule Breath"
    (2d8), and the Phase 2 "L'Hopital's Rage" transition below 50% HP."""

    def _combat(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        enemy = {
            "name": "The Derivative Dragon", "hp": 140, "max_hp": 140,
            "attack": 20, "block": 0, "ai": "derivative_dragon",
            "intent": None, "statuses": {},
        }
        enemy = game_module.apply_special_enemy_traits(enemy)
        g.start_combat(enemy, boss=True)
        g.player["block"] = 0
        return g

    def test_generate_boss_gives_the_dragon_a_fixed_140_hp(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        with unittest.mock.patch.object(random, "choice", return_value=("The Derivative Dragon", "derivative_dragon")):
            boss = g.generate_boss(row_number=7)
        self.assertEqual(boss["hp"], 140)
        self.assertEqual(boss["max_hp"], 140)
        self.assertEqual(boss["dragon_phase"], 1)
        self.assertTrue(boss["dragon_phase2_pending"])

    def test_chain_rule_breath_is_2d8_with_no_rage_bonus_in_phase_1(self):
        g = self._combat()
        with unittest.mock.patch.object(random, "randint", side_effect=[5, 6]):
            g.roll_enemy_intent(g.enemy)
        # Raw 2d8 = 5 + 6 = 11, then the 5% BOSS_DAMAGE_NERF: floor(11*0.95) = 10.
        # "rolls"/"bonus" stay the pre-nerf dice values (only "value" -- the
        # actual damage that will land -- is reduced).
        self.assertEqual(g.enemy["intent"], {"type": "chain_rule_breath", "value": 10, "rolls": [5, 6], "bonus": 0})

    def test_phase_2_triggers_below_half_hp_and_clears_player_debuffs(self):
        g = self._combat()
        g.apply_status(g.player, "truncate", 2)
        self.assertGreater(g.player["statuses"].get("truncate", 0), 0)
        g.deal_damage(g.enemy, 75, "big hit")  # 140 -> 65, below half (70)
        self.assertEqual(g.enemy["dragon_phase"], 2)
        self.assertFalse(g.enemy["dragon_phase2_pending"])
        self.assertEqual(g.enemy["dragon_rage_bonus"], 3)
        self.assertEqual(g.player["statuses"], {})
        # Doesn't re-trigger / re-clear on a later hit.
        g.apply_status(g.player, "truncate", 2)
        g.deal_damage(g.enemy, 5, "second hit")
        self.assertGreater(g.player["statuses"].get("truncate", 0), 0)

    def test_rage_bonus_applies_to_every_roll_after_phase_2(self):
        g = self._combat()
        g.enemy["dragon_phase2_pending"] = False
        g.enemy["dragon_phase"] = 2
        g.enemy["dragon_rage_bonus"] = 3
        with unittest.mock.patch.object(random, "randint", side_effect=[1, 1]):
            g.roll_enemy_intent(g.enemy)
        # Raw 1 + 1 + 3 rage = 5, then floor(5 * 0.95) = 4 from BOSS_DAMAGE_NERF.
        self.assertEqual(g.enemy["intent"]["value"], 4)

    def test_chain_rule_breath_deals_damage_on_enemy_turn(self):
        g = self._combat()
        g.enemy["intent"] = {"type": "chain_rule_breath", "value": 12, "rolls": [6, 6], "bonus": 0}
        hp_before = g.player["hp"]
        g.enemy_turn()
        self.assertEqual(hp_before - g.player["hp"], 12)

    def test_chain_rule_breath_ev_formula(self):
        # E(V) includes the BOSS_DAMAGE_NERF (5% boss damage reduction)
        # applied to the theoretical 2d8 (+rage bonus) average.
        _, value = game_module.enemy_intent_ev({"type": "chain_rule_breath", "value": 11, "bonus": 0})
        self.assertAlmostEqual(value, 9.0 * game_module.BOSS_DAMAGE_NERF)
        _, value2 = game_module.enemy_intent_ev({"type": "chain_rule_breath", "value": 14, "bonus": 3})
        self.assertAlmostEqual(value2, 12.0 * game_module.BOSS_DAMAGE_NERF)


class RoundTwoRestSiteTests(unittest.TestCase):
    """Rest Site v2: renamed buttons, and 'M' also opening Audit Stats."""

    def setUp(self):
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.state = "rest_choice"

    def test_options_carry_the_renamed_v2_labels(self):
        game_module.init_display()
        self.g.button_rects = {}
        game_module.draw_rest_choice(self.g)
        source = inspect.getsource(game_module.draw_rest_choice)
        self.assertIn("Forge the Gains / Bench Press", source)
        self.assertIn("Audit Stats (M)", source)
        # The buttons themselves are still keyed the same way regardless of
        # label text, so existing click handling (rest_study/rest_audit)
        # keeps working unchanged.
        for action in ("heal", "study", "audit", "skip"):
            self.assertIn(f"rest_{action}", self.g.button_rects)

    def test_m_key_opens_audit_stats_from_the_rest_site(self):
        game_module.handle_keydown(self.g, type("Evt", (), {"key": game_module.pygame.K_m, "unicode": "m"})())
        self.assertEqual(self.g.state, "deck_view")
        self.assertEqual(self.g.deck_return_state, "rest_choice")


class RoundTwoRollHistoryTests(unittest.TestCase):
    """Empirical roll-distribution tracking (Law of Large Numbers)."""

    def setUp(self):
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")

    def test_fresh_run_has_no_roll_history(self):
        self.assertEqual(self.g.roll_history, {})

    def test_roll_dice_tallies_by_die_size_and_face(self):
        with unittest.mock.patch.object(random, "randint", return_value=4):
            self.g.roll_dice(6, "test")
            self.g.roll_dice(6, "test")
        self.assertEqual(self.g.roll_history, {6: {4: 2}})

    def test_roll_dice_multi_tallies_every_individual_die(self):
        with unittest.mock.patch.object(random, "randint", side_effect=[2, 3, 2]):
            self.g.roll_dice_multi(4, 3, "test")
        self.assertEqual(self.g.roll_history, {4: {2: 2, 3: 1}})

    def test_reset_run_clears_roll_history(self):
        with unittest.mock.patch.object(random, "randint", return_value=1):
            self.g.roll_dice(6, "test")
        self.assertNotEqual(self.g.roll_history, {})
        self.g.reset_run()
        self.assertEqual(self.g.roll_history, {})

    def test_math_inspector_shows_empirical_counters_without_raising(self):
        game_module.init_display()
        self.g.start_combat({"name": "Dummy", "hp": 10, "max_hp": 10, "attack": 1, "block": 0, "ai": "aggressive", "intent": None})
        self.g.show_math_inspector = True
        with unittest.mock.patch.object(random, "randint", return_value=3):
            self.g.roll_dice(6, "test")
        game_module.draw_math_inspector(self.g)  # must not raise with history present

    def test_deck_view_audit_stats_panel_renders_without_raising(self):
        game_module.init_display()
        with unittest.mock.patch.object(random, "randint", return_value=3):
            self.g.roll_dice(6, "test")
        self.g.deck_page = 0
        game_module.draw_deck_view(self.g)  # must not raise with history present


class RoundTwoFractalFiendBlockAmountTests(unittest.TestCase):
    """Spec update: Fractal Fiend now hardens with 12 Block at 50% HP
    (previously 10) -- covered again here at the module-docstring level to
    guard against the number silently drifting back."""

    def test_apply_special_enemy_traits_docstring_mentions_12_block(self):
        self.assertIn("12 Block", inspect.getdoc(game_module.apply_special_enemy_traits))


class RoundThreeTitleSplashTests(unittest.TestCase):
    """The real key-art splash (assets/backgrounds/title_screen.png) as the
    title screen background, with Enter/Space now also advancing past it."""

    def test_title_screen_uses_the_splash_background_when_present(self):
        game_module.init_display()
        g = game_module.Game()
        fake = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_background", return_value=fake) as mocked:
            game_module.draw_title_screen(g)
        mocked.assert_called_with("title")
        self.assertIn("start_run", g.button_rects)

    def test_title_screen_falls_back_to_procedural_when_no_splash_art(self):
        game_module.init_display()
        g = game_module.Game()
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_background", return_value=None):
            game_module.draw_title_screen(g)  # must not raise
        self.assertIn("start_run", g.button_rects)

    def test_enter_and_space_advance_past_the_title_screen(self):
        for key in (game_module.pygame.K_RETURN, game_module.pygame.K_KP_ENTER, game_module.pygame.K_SPACE):
            g = game_module.Game()
            self.assertEqual(g.state, "title")
            game_module.handle_keydown(g, type("Evt", (), {"key": key, "unicode": ""})())
            self.assertEqual(g.state, "intro")

    def test_enter_key_does_nothing_outside_the_title_screen(self):
        g = game_module.Game()
        g.state = "map"
        game_module.handle_keydown(g, type("Evt", (), {"key": game_module.pygame.K_RETURN, "unicode": ""})())
        self.assertEqual(g.state, "map")


class RoundThreeMusicFrameworkTests(unittest.TestCase):
    """Skeleton for the MP3 music the user is adding -- MUSIC_MANAGER must
    never crash regardless of whether real audio files or even a real
    mixer module are present (the headless test stub has neither)."""

    def test_music_key_for_state_covers_every_registered_screen(self):
        for state in game_module.STATE_RENDERERS:
            g = game_module.Game()
            g.state = state
            # Every screen either maps to a track or intentionally has no
            # opinion (None, e.g. an overlay state) -- either way this must
            # not raise or KeyError.
            game_module.music_key_for_state(g)

    def test_boss_combat_overrides_to_the_boss_track(self):
        g = game_module.Game()
        g.state = "combat"
        g.combat_boss = False
        self.assertEqual(game_module.music_key_for_state(g), "combat")
        g.combat_boss = True
        self.assertEqual(game_module.music_key_for_state(g), "boss")

    def test_title_and_character_select_share_the_title_track(self):
        g = game_module.Game()
        for state in ("title", "intro", "character_select"):
            g.state = state
            self.assertEqual(game_module.music_key_for_state(g), "title")

    def test_music_manager_play_for_state_never_raises_without_a_real_mixer(self):
        # The stub pygame module has no `mixer` attribute at all -- this is
        # exactly the "completely absent audio device" case the manager
        # must degrade silently for.
        mgr = game_module.MusicManager()
        g = game_module.Game()
        for state in ("title", "map", "combat", "rest_choice", "shop", "victory", "gameover"):
            g.state = state
            mgr.play_for_state(g)  # must not raise
        mgr.set_volume(0.5)  # must not raise

    def test_music_manager_does_not_reload_on_repeated_calls_with_the_same_key(self):
        mgr = game_module.MusicManager()
        g = game_module.Game()
        g.state = "title"
        mgr.play_for_state(g)
        first_key = mgr._current_key
        mgr.play_for_state(g)
        mgr.play_for_state(g)
        self.assertEqual(mgr._current_key, first_key)

    def test_render_frame_drives_the_music_manager(self):
        game_module.init_display()
        g = game_module.Game()
        with unittest.mock.patch.object(game_module.MUSIC_MANAGER, "play_for_state") as mocked:
            game_module.render_frame(g)
        mocked.assert_called_once_with(g)


class RoundFourRestSiteTransparencyTests(unittest.TestCase):
    """The rest site's panel/buttons were made translucent and smaller so
    the campfire backdrop actually shows through, instead of the old
    near-opaque 900x500 card covering almost the whole window."""

    def test_lerp_color_preserves_an_alpha_channel_when_present(self):
        # Regression test: _lerp_color used to always return a 3-tuple
        # (range(3)), silently dropping any alpha component passed in --
        # which would have flattened draw_center_panel's panel_alpha back
        # to fully opaque no matter what value was requested.
        result = game_module._lerp_color((10, 20, 30, 100), (10, 20, 30, 200), 0.5)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[3], 150)

    def test_lerp_color_still_works_for_plain_opaque_rgb_triples(self):
        result = game_module._lerp_color((0, 0, 0), (100, 100, 100), 0.5)
        self.assertEqual(result, (50, 50, 50))

    def test_draw_center_panel_defaults_stay_fully_opaque(self):
        # Every other screen calls draw_center_panel() without the new
        # kwargs -- confirm the default is still the original opaque look.
        source = inspect.getsource(game_module.draw_center_panel)
        self.assertIn("panel_alpha=255", source)
        self.assertIn("dim_alpha=130", source)

    def test_rest_choice_uses_a_translucent_smaller_panel(self):
        source = inspect.getsource(game_module.draw_rest_choice)
        self.assertIn("panel_alpha=150", source)
        self.assertIn("dim_alpha=55", source)
        self.assertIn("card_h = 140", source)

    def test_rest_choice_renders_without_raising(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.state = "rest_choice"
        g.button_rects = {}
        game_module.draw_rest_choice(g)
        # The four option buttons should still all be clickable despite
        # being smaller.
        for action in ("heal", "study", "audit", "skip"):
            self.assertIn(f"rest_{action}", g.button_rects)


class RoundFourCardForgeDeltaTests(unittest.TestCase):
    """The rest-site "Forge the Gains" upgrade picker now shows exactly how
    much a card's numbers improve (e.g. "8->12 (+4)") instead of making the
    player compare the base and upgraded desc text themselves."""

    def test_strike_shows_a_single_damage_delta(self):
        self.assertEqual(game_module.card_upgrade_deltas("Strike"), [(8, 12, 4)])

    def test_weighted_step_shows_both_block_and_draw_deltas(self):
        self.assertEqual(
            game_module.card_upgrade_deltas("Weighted Step"), [(6, 9, 3), (1, 2, 1)]
        )

    def test_unchanged_numbers_are_not_reported_as_deltas(self):
        # "Roll 1d6 + 2 damage." -> "Roll 1d6 + 5 damage.": the die size (6)
        # is identical in both and must not show up as a "6->6 (+0)" line.
        deltas = game_module.card_upgrade_deltas("Arcane Dart")
        self.assertEqual(deltas, [(2, 5, 3)])
        self.assertNotIn(6, [d[2] for d in deltas])

    def test_mismatched_number_count_returns_no_deltas_instead_of_guessing(self):
        # "1 = hit..." -> "1-2 = hit..." adds an extra number, so the
        # positional zip would misalign -- must bail out to [] rather than
        # report a wrong pairing.
        deltas = game_module.card_upgrade_deltas("Reckless Roll")
        self.assertEqual(deltas, [])

    def test_non_upgradeable_card_returns_no_deltas(self):
        self.assertEqual(game_module.card_upgrade_deltas("Strike+"), [])

    def test_rest_upgrade_pick_renders_the_delta_line_without_raising(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.rest_upgrade_options = ["Strike", "Defend", "Arcane Dart"]
        g.state = "rest_upgrade_pick"
        g.button_rects = {}
        game_module.draw_rest_upgrade_pick(g)
        for idx in range(3):
            self.assertIn(f"upgrade_{idx}", g.button_rects)


class RoundFourWeakIntentDisplayTests(unittest.TestCase):
    """The boss/enemy's telegraphed "X dmg" intent number now reflects
    Weak's halving, so it matches what deal_damage() will actually land
    instead of showing the pre-Weak raw value."""

    def _game_with_enemy(self, weak_stacks=0):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.enemy = {
            "name": "The Derivative Dragon", "hp": 100, "max_hp": 140,
            "attack": 13, "block": 0, "ai": "derivative_dragon",
            "intent": None, "statuses": {},
        }
        if weak_stacks:
            g.enemy["statuses"]["weak"] = weak_stacks
        return g

    def test_no_weak_leaves_the_raw_value_unchanged(self):
        g = self._game_with_enemy(weak_stacks=0)
        self.assertEqual(game_module.weak_adjusted_damage(g, 13), 13)

    def test_weak_on_the_enemy_halves_and_floors_the_predicted_damage(self):
        g = self._game_with_enemy(weak_stacks=3)
        # Matches deal_damage()'s own math.floor(damage * 0.5).
        self.assertEqual(game_module.weak_adjusted_damage(g, 13), 6)
        self.assertEqual(game_module.weak_adjusted_damage(g, 9), 4)

    def test_weak_reduction_matches_deal_damage_exactly(self):
        g = self._game_with_enemy(weak_stacks=2)
        g.enemy["intent"] = {"type": "attack", "value": 13}
        predicted = game_module.weak_adjusted_damage(g, g.enemy["intent"]["value"])
        actual = g.deal_damage(g.player, g.enemy["intent"]["value"], g.enemy["name"])
        # actual may additionally be reduced by block, but with 0 block
        # (the default here) it must equal the predicted value exactly.
        self.assertEqual(predicted, actual)

    def test_draw_combat_intent_label_uses_the_weak_adjusted_helper(self):
        source = inspect.getsource(game_module.draw_combat)
        self.assertIn("weak_adjusted_damage(game, raw_value)", source)

    def test_draw_combat_renders_with_a_weak_enemy_without_raising(self):
        game_module.init_display()
        g = self._game_with_enemy(weak_stacks=3)
        g.enemy["intent"] = {"type": "attack", "value": 13}
        g.state = "combat"
        g.button_rects = {}
        game_module.draw_combat(g)


class RoundFiveBossDamageNerfTests(unittest.TestCase):
    """A flat 5% reduction to every damage-dealing intent a boss rolls,
    across every ai archetype a boss can use -- applied in
    roll_enemy_intent() via BOSS_DAMAGE_NERF, and left out of block intents
    and non-boss fights (regular enemies/elites share some of the same ai
    names, e.g. "guard"/"burst", and must be unaffected)."""

    def _boss_combat(self, ai, attack=20):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        enemy = {
            "name": "Test Boss", "hp": 100, "max_hp": 100,
            "attack": attack, "block": 0, "ai": ai, "intent": None, "statuses": {},
        }
        g.start_combat(enemy, boss=True)
        return g

    def _non_boss_combat(self, ai, attack=20, elite=False):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        enemy = {
            "name": "Test Enemy", "hp": 100, "max_hp": 100,
            "attack": attack, "block": 0, "ai": ai, "intent": None, "statuses": {},
        }
        g.start_combat(enemy, boss=False, elite=elite)
        return g

    def test_boss_attack_intent_is_reduced_by_5_percent(self):
        g = self._boss_combat("aggressive", attack=20)  # falls to the "else" branch
        with unittest.mock.patch.object(random, "randint", return_value=5):
            g.roll_enemy_intent(g.enemy)
        # Raw = 20 + 5 = 25, floor(25 * 0.95) = 23.
        self.assertEqual(g.enemy["intent"]["value"], 23)

    def test_boss_guard_attack_branch_is_reduced(self):
        g = self._boss_combat("guard", attack=20)
        with unittest.mock.patch.object(random, "random", return_value=0.99), \
             unittest.mock.patch.object(random, "randint", return_value=6):
            g.roll_enemy_intent(g.enemy)  # random.random() >= 0.45 -> attack branch
        # Raw = 20 + 6 = 26, floor(26 * 0.95) = 24.
        self.assertEqual(g.enemy["intent"], {"type": "attack", "value": 24})

    def test_boss_guard_block_intent_is_left_untouched(self):
        g = self._boss_combat("guard", attack=20)
        with unittest.mock.patch.object(random, "random", return_value=0.0):
            g.roll_enemy_intent(g.enemy)  # random.random() < 0.45 -> block branch
        self.assertEqual(g.enemy["intent"]["type"], "block")
        raw_block_value = 6 + g.effective_floor()
        self.assertEqual(g.enemy["intent"]["value"], raw_block_value)

    def test_boss_burst_intent_is_reduced(self):
        g = self._boss_combat("burst", attack=20)
        with unittest.mock.patch.object(random, "random", return_value=0.0):
            g.roll_enemy_intent(g.enemy)  # random.random() < 0.35 -> burst branch
        # Raw = 20 + 8 = 28, floor(28 * 0.95) = 26.
        self.assertEqual(g.enemy["intent"], {"type": "burst", "value": 26})

    def test_regular_enemy_and_elite_with_shared_ai_names_are_unaffected(self):
        # Each ai branch's attack-path formula differs (guard/aggressive add
        # a random 1-6/1-5 roll on top of "attack", burst's attack-path
        # doesn't add anything), so expected values are per-branch rather
        # than one shared formula -- the point under test is just that none
        # of them get the boss nerf's floor(value * 0.95).
        expected_raw_value = {"guard": 26, "burst": 20, "aggressive": 26}
        for ai, expected in expected_raw_value.items():
            g = self._non_boss_combat(ai, attack=20)
            with unittest.mock.patch.object(random, "random", return_value=0.99), \
                 unittest.mock.patch.object(random, "randint", return_value=6):
                g.roll_enemy_intent(g.enemy)
            self.assertFalse(g.combat_boss)
            self.assertEqual(g.enemy["intent"]["value"], expected)

    def test_derivative_dragons_chain_rule_breath_is_also_nerfed(self):
        g = self._boss_combat("derivative_dragon")
        with unittest.mock.patch.object(random, "randint", side_effect=[5, 6]):
            g.roll_enemy_intent(g.enemy)
        # Raw 2d8 = 11, floor(11 * 0.95) = 10 -- same nerf as every other
        # boss archetype, even though this one doesn't use enemy["attack"].
        self.assertEqual(g.enemy["intent"]["value"], 10)


class RoundSixCharacterSelectContrastTests(unittest.TestCase):
    """The starting-relic callout on the character select screen used to
    draw its "subtle" backing tint directly onto `screen` (a plain,
    non-SRCALPHA Surface), which silently drops the alpha channel and
    renders a fully opaque near-white box instead -- washing out the GOLD/
    MUTED text on top of it. Fixed by compositing onto a dedicated SRCALPHA
    surface first, like every other translucent overlay in the file does."""

    def test_relic_panel_tint_is_composited_on_its_own_srcalpha_surface(self):
        source = inspect.getsource(game_module.draw_character_select)
        # Locks in the fix, not just the current color values: the tint
        # must be drawn onto a Surface(..., pygame.SRCALPHA) and blitted,
        # never drawn with an alpha color straight onto `screen`.
        self.assertIn("pygame.SRCALPHA", source)
        self.assertNotIn("pygame.draw.rect(screen, (255, 255, 255, 18)", source)

    def test_draw_character_select_renders_without_raising(self):
        game_module.init_display()
        g = game_module.Game()
        g.state = "character_select"
        g.button_rects = {}
        game_module.draw_character_select(g)
        self.assertIn("select_Math Warrior", g.button_rects)
        self.assertIn("select_Probability Wizard", g.button_rects)


class RoundSixPisDefenseTests(unittest.TestCase):
    """Renamed from "Weighted Lens" to "Pi's Defense": 3.14 (repeating)
    block every turn, rounding down to a flat 3, instead of a one-time +1
    at combat start."""

    def test_renamed_consistently_everywhere(self):
        self.assertIn("Pi's Defense", game_module.RELIC_LIBRARY)
        self.assertNotIn("Weighted Lens", game_module.RELIC_LIBRARY)
        self.assertEqual(game_module.CHARACTER_OPTIONS["Math Warrior"]["starting_relic"], "Pi's Defense")
        self.assertIn("Pi's Defense", game_module.ICON_RELIC)

    def test_tooltip_mentions_3_14_repeating_and_that_it_rounds_to_3(self):
        desc = game_module.RELIC_LIBRARY["Pi's Defense"]["description"]
        self.assertIn("3.14", desc)
        self.assertIn("3", desc)

    def test_hook_is_on_turn_start_not_on_combat_start(self):
        hooks = game_module.RELIC_LIBRARY["Pi's Defense"]["hooks"]
        self.assertIn("on_turn_start", hooks)
        self.assertNotIn("on_combat_start", hooks)

    def test_character_select_shows_the_new_name_and_tooltip(self):
        game_module.init_display()
        g = game_module.Game()
        g.state = "character_select"
        g.button_rects = {}
        # Must not raise looking up the renamed relic's icon/description
        # while rendering the character select screen.
        game_module.draw_character_select(g)


class RoundSevenCasinoAssetManagerTests(unittest.TestCase):
    """New AssetManager loaders for the wagering shrine's backdrop
    (assets/events/) and Gambler's Flurry's card art (assets/cards/), with
    the same "real art if present, else None so the caller falls back to
    procedural drawing" guarantee as every other AssetManager loader."""

    def setUp(self):
        self.am = game_module.AssetManager()

    def test_get_event_backdrop_returns_none_when_missing(self):
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "EVENTS_DIR", empty_dir):
            self.assertIsNone(self.am.get_event_backdrop("casino_event", 1280, 720))

    def test_get_event_backdrop_fits_inside_the_box_preserving_aspect(self):
        fake = game_module.pygame.Surface((1376, 768))  # ~1.79:1, like the real art
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            img = self.am.get_event_backdrop("casino_event", 1280, 720)
        self.assertIsNotNone(img)
        w, h = img.get_size()
        self.assertLessEqual(w, 1280)
        self.assertLessEqual(h, 720)
        # Fit-inside (not stretch-to-fill): aspect ratio must be preserved.
        self.assertAlmostEqual(w / h, 1376 / 768, places=2)

    def test_get_card_art_returns_none_when_missing(self):
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "CARDS_DIR", empty_dir):
            self.assertIsNone(self.am.get_card_art("gamblers_flurry", 200))

    def test_get_card_art_preserves_aspect_capped_to_max_height(self):
        fake = game_module.pygame.Surface((848, 1264))  # portrait, like the real art
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            img = self.am.get_card_art("gamblers_flurry", 200)
        self.assertIsNotNone(img)
        self.assertEqual(img.get_height(), 200)

    def test_get_card_icon_returns_none_when_missing(self):
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "CARDS_DIR", empty_dir):
            self.assertIsNone(self.am.get_card_icon("gamblers_flurry", 64))

    def test_get_card_icon_is_square(self):
        fake = game_module.pygame.Surface((848, 1264))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            icon = self.am.get_card_icon("gamblers_flurry", 64)
        self.assertIsNotNone(icon)
        self.assertEqual(icon.get_size(), (64, 64))

    def test_procedural_fallbacks_render_without_raising(self):
        game_module.init_display()
        surface = game_module.pygame.Surface((1280, 720))
        game_module.draw_procedural_casino_backdrop(surface, game_module.pygame.Rect(0, 0, 1280, 720))
        game_module.draw_procedural_dice_cup(surface, (200, 200), 110)


class RoundEightCardArtTests(unittest.TestCase):
    """New illustrated card portraits (assets/cards/progressive_overload.png,
    assets/cards/spotters_axiom.png), registered via CARD_ART_FILES and
    checked by draw_card_art() before its procedural icon-panel fallback.
    Same "real art if present, else None" guarantee as every other loader,
    plus the new max_width fit-inside-both-dimensions behavior."""

    def setUp(self):
        self.am = game_module.AssetManager()

    def test_get_card_art_returns_none_when_missing(self):
        empty_dir = tempfile.mkdtemp()
        with unittest.mock.patch.object(game_module, "CARDS_DIR", empty_dir):
            self.assertIsNone(self.am.get_card_art("progressive_overload", 110, 140))
            self.assertIsNone(self.am.get_card_art("spotters_axiom", 110, 140))

    def test_get_card_art_returns_surface_when_present(self):
        fake = game_module.pygame.Surface((1024, 1024))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            img = self.am.get_card_art("progressive_overload", 110, 140)
        self.assertIsNotNone(img)
        self.assertIsInstance(img, game_module.pygame.Surface)

    def test_get_card_art_fits_inside_both_dimensions_when_wide(self):
        # A wide (landscape) source: height-only fitting would leave it
        # wider than the panel, so max_width must also constrain it.
        fake = game_module.pygame.Surface((2000, 400))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            img = self.am.get_card_art("progressive_overload", 110, 140)
        self.assertIsNotNone(img)
        w, h = img.get_size()
        self.assertLessEqual(w, 140)
        self.assertLessEqual(h, 110)
        self.assertAlmostEqual(w / h, 2000 / 400, places=2)

    def test_get_card_art_fits_inside_both_dimensions_when_tall(self):
        # A tall (portrait) source, like the real commissioned art: width
        # is the binding constraint, not height.
        fake = game_module.pygame.Surface((848, 1264))
        with unittest.mock.patch.object(self.am, "_load_raw", return_value=fake):
            img = self.am.get_card_art("spotters_axiom", 110, 140)
        self.assertIsNotNone(img)
        w, h = img.get_size()
        self.assertLessEqual(w, 140)
        self.assertLessEqual(h, 110)

    def test_draw_card_art_uses_real_portrait_when_present(self):
        game_module.init_display()
        surface = game_module.pygame.Surface((150, 172))
        fake = game_module.pygame.Surface((848, 1264))
        body = game_module.pygame.Rect(0, 0, 150, 172)
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_card_art", return_value=fake) as mocked:
            game_module.draw_card_art(surface, "Progressive Overload", body, game_module.GOLD)
        mocked.assert_called_once()

    def test_draw_card_art_falls_back_to_procedural_when_asset_missing(self):
        # Force CARDS_DIR to an empty directory AND swap in a fresh
        # AssetManager (real PNGs now ship in assets/cards/, and the
        # module-global ASSET_MANAGER caches by (name, height, width) --
        # not by CARDS_DIR -- so without a fresh instance, an earlier test
        # in this class that already loaded the real art at this same
        # body size would make this hit that stale cache entry instead of
        # actually exercising the "missing" path). Must not raise for
        # either new card.
        game_module.init_display()
        empty_dir = tempfile.mkdtemp()
        surface = game_module.pygame.Surface((150, 172))
        body = game_module.pygame.Rect(0, 0, 150, 172)
        with unittest.mock.patch.object(game_module, "CARDS_DIR", empty_dir), \
             unittest.mock.patch.object(game_module, "ASSET_MANAGER", game_module.AssetManager()):
            game_module.draw_card_art(surface, "Progressive Overload", body, game_module.GOLD)
            game_module.draw_card_art(surface, "Spotter's Axiom", body, game_module.GOLD)

    def test_card_rendering_executes_cleanly_at_hand_and_reward_sizes(self):
        # Surface size mismatch / blit errors would show up across a range
        # of the real sizes render_card() is actually called at.
        game_module.init_display()
        for size in ((150, 172), (190, 260), (240, 90)):
            surface = game_module.pygame.Surface(size)
            rect = game_module.pygame.Rect(0, 0, *size)
            game_module.render_card(surface, "Progressive Overload", rect, character="Math Warrior")

    def test_progressive_overload_math_footer_appears_in_hand_tooltip(self):
        # The hover tooltip (draw_combat) still shows the full-precision
        # math sub-footer alongside render_card()'s own on-card footer
        # (RoundEightMathFooterTests below covers that one specifically) --
        # this just confirms the combat screen renders cleanly with a
        # math-bearing card hovered, exercising the tooltip path.
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Math Warrior")
        g.start_combat(g.generate_enemy(1))
        g.hand = ["Progressive Overload"]
        g.button_rects = {}
        real_get_pos = game_module.pygame.mouse.get_pos
        try:
            game_module.pygame.mouse.get_pos = lambda: (100, 500)
            game_module.render_frame(g)
        finally:
            game_module.pygame.mouse.get_pos = real_get_pos


class RoundEightNewCardMechanicsTests(unittest.TestCase):
    """Behavioral tests for the two Chad-Math-themed cards added alongside
    their art: Progressive Overload's within-combat escalation, and
    Spotter's Axiom's roll-and-double-for-block."""

    def setUp(self):
        random.seed(11)
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.start_combat(self.g.generate_enemy(1))

    def test_progressive_overload_deals_roll_plus_2_with_no_bonus_at_first(self):
        with unittest.mock.patch("random.randint", return_value=3):
            before = self.g.enemy["hp"]
            self.g.play_progressive_overload()
        self.assertEqual(before - self.g.enemy["hp"], 5)  # 3 + 2 + 0
        self.assertEqual(self.g.combat_card_bonus.get("Progressive Overload", 0), 0)

    def test_progressive_overload_gains_permanent_bonus_on_roll_5_or_higher(self):
        with unittest.mock.patch("random.randint", return_value=7):
            self.g.play_progressive_overload()
        self.assertEqual(self.g.combat_card_bonus["Progressive Overload"], 1)

        with unittest.mock.patch("random.randint", return_value=2):
            before = self.g.enemy["hp"]
            self.g.play_progressive_overload()
        # 2 + 2 base + 1 accumulated bonus = 5; this low roll doesn't add more bonus.
        self.assertEqual(before - self.g.enemy["hp"], 5)
        self.assertEqual(self.g.combat_card_bonus["Progressive Overload"], 1)

    def test_progressive_overload_bonus_resets_between_combats(self):
        with unittest.mock.patch("random.randint", return_value=8):
            self.g.play_progressive_overload()
        self.assertEqual(self.g.combat_card_bonus["Progressive Overload"], 1)
        self.g.start_combat(self.g.generate_enemy(1))
        self.assertEqual(self.g.combat_card_bonus, {})

    def test_spotters_axiom_gains_double_the_roll_in_block(self):
        self.g.player["block"] = 0
        with unittest.mock.patch("random.randint", return_value=4):
            self.g.play_spotters_axiom()
        self.assertEqual(self.g.player["block"], 8)  # 4 * 2

    def test_spotters_axiom_feeds_roll_history_for_the_lln_audit(self):
        before = sum(self.g.roll_history.get(6, {}).values())
        with unittest.mock.patch("random.randint", return_value=5):
            self.g.play_spotters_axiom()
        after = sum(self.g.roll_history.get(6, {}).values())
        self.assertEqual(after - before, 1)

    def test_spotters_axiom_is_excluded_from_damage_ev_like_other_block_cards(self):
        # Pure block cards (Defend, Probability Shield) aren't in CARD_EV,
        # which is damage-only; Spotter's Axiom follows that precedent.
        self.assertIsNone(game_module.card_ev(self.g, "Spotter's Axiom"))
        self.assertIsNone(game_module.card_ev(self.g, "Probability Shield"))


class RoundEightMathFooterTests(unittest.TestCase):
    """The two-tier math footer (E(V) / cost-efficiency / recoil) is now
    baked directly onto the card face by render_card(), at every size it's
    reused at -- not just the hand hover tooltip. Cards with no damage
    E(V) (block/draw/power cards) get no footer and keep their full-height
    description instead."""

    def setUp(self):
        game_module.init_display()
        self.g = game_module.Game()
        self.g.choose_character("Probability Wizard")

    def test_math_footer_data_is_none_for_a_card_with_no_ev(self):
        self.assertIsNone(game_module._card_math_footer_data(self.g, "Defend"))
        self.assertIsNone(game_module._card_math_footer_data(self.g, "Probability Shield"))

    def test_math_footer_data_matches_the_tooltip_numbers(self):
        ev, eff, recoil = game_module._card_math_footer_data(self.g, "Reckless Roll")
        self.assertAlmostEqual(ev, game_module.card_ev(self.g, "Reckless Roll"))
        self.assertAlmostEqual(eff, game_module.card_cost_efficiency(self.g, "Reckless Roll"))
        self.assertAlmostEqual(recoil, game_module.card_recoil_ev(self.g, "Reckless Roll"))
        self.assertGreater(recoil, 0)  # Reckless Roll is the one card with real backfire risk

    def test_card_with_no_recoil_reports_none_for_it(self):
        ev, eff, recoil = game_module._card_math_footer_data(self.g, "Arcane Dart")
        self.assertIsNotNone(ev)
        self.assertIsNone(recoil)

    def test_render_card_without_a_game_draws_no_footer_but_still_renders(self):
        # character-select / deck-view style calls that don't pass game=
        # must keep working exactly as before (no crash, no footer).
        surface = game_module.pygame.Surface((150, 172))
        rect = game_module.pygame.Rect(0, 0, 150, 172)
        game_module.render_card(surface, "Reckless Roll", rect, character="Probability Wizard")

    def test_footer_reservation_is_conditional_on_the_card_having_ev(self):
        # _card_footer_rect() itself is unconditional (pure geometry); it's
        # render_card() that only reserves/draws it when the card actually
        # has math data, so a card with no EV keeps its full-height desc.
        self.assertIsNone(game_module._card_math_footer_data(self.g, "Defend"))
        self.assertIsNotNone(game_module._card_math_footer_data(self.g, "Reckless Roll"))

    def test_card_footer_renders_without_raising_at_every_render_card_size(self):
        # Hand (150x172), reward (190x260), and the smallest shop/relic-list
        # size (240x90 wide, but height can shrink as low as ~90) all have
        # to survive a card that DOES carry a footer.
        for size in ((150, 172), (190, 260), (240, 90), (156, 90)):
            surface = game_module.pygame.Surface(size)
            rect = game_module.pygame.Rect(0, 0, *size)
            game_module.render_card(surface, "Reckless Roll", rect, character="Probability Wizard", game=self.g)

    def test_footer_rect_is_smaller_and_higher_up_at_small_card_sizes(self):
        big_body = game_module.pygame.Rect(0, 0, 150, 172)
        small_body = game_module.pygame.Rect(0, 0, 156, 90)
        big_footer = game_module._card_footer_rect(big_body)
        small_footer = game_module._card_footer_rect(small_body)
        self.assertLess(small_footer.height, big_footer.height)

    def test_unaffordable_card_dims_the_footer_along_with_the_rest_of_the_card(self):
        # draw_card_frame() no longer applies the unaffordable dim itself
        # (that moved to the end of render_card() so it happens AFTER the
        # footer is drawn) -- this just guards against that dim overlay
        # silently going missing again for a card with a footer.
        surface = game_module.pygame.Surface((150, 172))
        rect = game_module.pygame.Rect(0, 0, 150, 172)
        game_module.render_card(surface, "Reckless Roll", rect, character="Probability Wizard",
                                 game=self.g, affordable=False)


class RoundEightCharacterCardArtTests(unittest.TestCase):
    """CHARACTER_CARD_ART_FILES lets a raw CARD_LIBRARY name that's shared
    by both starting decks (e.g. "Strike") resolve to a DIFFERENT
    commissioned portrait per character, since CARD_PRESENTATION already
    re-skins it differently (Math Warrior's Equation Breaker vs.
    Probability Wizard's Power Chord Barrage) -- keying art by base name
    alone would put the same picture under both re-skins."""

    def test_strike_resolves_to_different_art_per_character(self):
        warrior_art = game_module.CHARACTER_CARD_ART_FILES.get("Math Warrior", {}).get("Strike")
        wizard_art = game_module.CHARACTER_CARD_ART_FILES.get("Probability Wizard", {}).get("Strike")
        self.assertIsNone(warrior_art)  # no commission for Equation Breaker yet -> falls back
        self.assertEqual(wizard_art, "power_chord_barrage")

    def test_reckless_roll_and_dice_slash_have_their_new_commissions_registered(self):
        self.assertEqual(
            game_module.CHARACTER_CARD_ART_FILES["Probability Wizard"]["Reckless Roll"],
            "whammy_bar_gambit",
        )
        self.assertEqual(
            game_module.CHARACTER_CARD_ART_FILES["Math Warrior"]["Dice Slash"],
            "one_rep_max",
        )

    def test_the_three_new_art_files_exist_on_disk(self):
        for fname in ("one_rep_max", "power_chord_barrage", "whammy_bar_gambit"):
            path = os.path.join(game_module.CARDS_DIR, f"{fname}.png")
            self.assertTrue(os.path.isfile(path), f"missing {path}")

    def test_draw_card_art_picks_the_character_specific_file_over_the_base_one(self):
        game_module.init_display()
        surface = game_module.pygame.Surface((150, 172))
        body = game_module.pygame.Rect(0, 0, 150, 172)
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_card_art", return_value=None) as mocked:
            game_module.draw_card_art(surface, "Strike", body, game_module.GOLD, character="Probability Wizard")
        mocked.assert_called_once()
        called_name = mocked.call_args[0][0]
        self.assertEqual(called_name, "power_chord_barrage")

    def test_draw_card_art_falls_back_to_base_art_files_when_no_character_override(self):
        game_module.init_display()
        surface = game_module.pygame.Surface((150, 172))
        body = game_module.pygame.Rect(0, 0, 150, 172)
        with unittest.mock.patch.object(game_module.ASSET_MANAGER, "get_card_art", return_value=None) as mocked:
            game_module.draw_card_art(surface, "Progressive Overload", body, game_module.GOLD, character="Math Warrior")
        called_name = mocked.call_args[0][0]
        self.assertEqual(called_name, "progressive_overload")

    def test_draw_card_art_renders_the_real_commission_without_raising(self):
        game_module.init_display()
        surface = game_module.pygame.Surface((150, 172))
        body = game_module.pygame.Rect(0, 0, 150, 172)
        with unittest.mock.patch.object(game_module, "ASSET_MANAGER", game_module.AssetManager()):
            game_module.draw_card_art(surface, "Strike", body, game_module.GOLD, character="Probability Wizard")
            game_module.draw_card_art(surface, "Reckless Roll", body, game_module.GOLD, character="Probability Wizard")
            game_module.draw_card_art(surface, "Dice Slash", body, game_module.GOLD, character="Math Warrior")


class RoundNineWizardEnergyRebalanceTests(unittest.TestCase):
    """Probability Wizard's base energy was nerfed from 4 to 3: at 4 base
    energy, Abacus Charm's "+1 energy every turn" relic gave him 5 energy
    every single turn, making the relic (and thus his kit) too strong. Now
    the relic's +1 just brings his effective turn energy back up to 4 --
    the number he used to start with outright -- which is the intended
    gimmick (Math Warrior stays a flat, relic-independent 3)."""

    def test_probability_wizard_base_energy_is_three(self):
        opts = game_module.CHARACTER_OPTIONS["Probability Wizard"]
        self.assertEqual(opts["energy"], 3)
        self.assertEqual(opts["max_energy"], 3)

    def test_math_warrior_energy_is_unchanged(self):
        opts = game_module.CHARACTER_OPTIONS["Math Warrior"]
        self.assertEqual(opts["energy"], 3)
        self.assertEqual(opts["max_energy"], 3)

    def test_probability_wizard_still_starts_with_abacus_charm(self):
        self.assertEqual(game_module.CHARACTER_OPTIONS["Probability Wizard"]["starting_relic"], "Abacus Charm")

    def test_wizard_turn_energy_is_four_with_abacus_charm_not_five(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat(g.generate_enemy(1))
        self.assertEqual(g.player["max_energy"], 3)
        self.assertEqual(g.player["energy"], 4)  # 3 base + Abacus Charm's +1 on_turn_start

    def test_wizard_turn_energy_stays_at_four_on_later_turns_too(self):
        game_module.init_display()
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat(g.generate_enemy(1))
        g.player["energy"] = 0
        g.start_player_turn()
        self.assertEqual(g.player["energy"], 4)


class RoundNineEnergyHudDisplayTests(unittest.TestCase):
    """A player rebuilding the .exe from source still saw the Probability
    Wizard's HUD read a flat "5" for energy -- turned out that was a stale
    build, but digging into it surfaced a real, separate display bug: the
    combat HUD rendered f"{energy}/{max_energy}" using the character's RAW
    base max_energy, so once Abacus Charm's turn-start bonus fired, the
    Wizard's own HUD read "4/3" -- current exceeding max, which looks like
    a bug even once the underlying numbers are correct. PER_TURN_ENERGY_
    RELICS + energy_bonus_from_relics() are the fix: the HUD now shows an
    honest current/effective-max ("4/4") plus a small "+1" tag so the
    relic bonus stays visible instead of disappearing into a bigger max."""

    def setUp(self):
        game_module.init_display()

    def test_energy_bonus_is_zero_with_no_energy_relics(self):
        g = game_module.Game()
        g.choose_character("Math Warrior")
        self.assertEqual(game_module.energy_bonus_from_relics(g), 0)

    def test_energy_bonus_is_one_for_abacus_charm(self):
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        self.assertEqual(game_module.energy_bonus_from_relics(g), 1)

    def test_per_turn_energy_relics_is_the_single_source_of_truth(self):
        # The turn-start hook and the HUD helper must read the SAME
        # constant, not two independently hardcoded "1"s that could drift.
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat(g.generate_enemy(1))
        expected_bonus = game_module.PER_TURN_ENERGY_RELICS["Abacus Charm"]
        self.assertEqual(g.player["energy"] - g.player["max_energy"], expected_bonus)
        self.assertEqual(game_module.energy_bonus_from_relics(g), expected_bonus)

    def test_draw_combat_renders_without_raising_for_the_wizard(self):
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat(g.generate_enemy(1))
        g.button_rects = {}
        game_module.render_frame(g)

    def test_current_energy_never_exceeds_the_displayed_max_in_the_hud(self):
        # Regression guard for the "4/3" bug: whatever the HUD's own
        # effective-max math is (game.player['max_energy'] + relic bonus),
        # current energy right after a turn start must never exceed it.
        g = game_module.Game()
        g.choose_character("Probability Wizard")
        g.start_combat(g.generate_enemy(1))
        effective_max = g.player["max_energy"] + game_module.energy_bonus_from_relics(g)
        self.assertLessEqual(g.player["energy"], effective_max)
        self.assertEqual(g.player["energy"], effective_max)


class RoundEightCharacterThemingTests(unittest.TestCase):
    """Every card in each character's starting deck (base card and its "+"
    upgrade, if it has one) must have a CARD_PRESENTATION re-skin for that
    character -- otherwise it silently reverts to its raw math-club name
    the moment it's upgraded, which is exactly the gap this covers. Also
    locks in that re-skinning is cosmetic only: the displayed numbers must
    match the real CARD_LIBRARY mechanics word for word."""

    def _deck_cards_with_upgrades(self, character):
        names = set(game_module.CHARACTER_OPTIONS[character]["deck"])
        names |= {
            game_module.CARD_LIBRARY[n]["upgrades_to"]
            for n in names if game_module.CARD_LIBRARY[n].get("upgrades_to")
        }
        return names

    def test_math_warrior_deck_is_fully_themed(self):
        for name in self._deck_cards_with_upgrades("Math Warrior"):
            display_name, _ = game_module.card_presentation("Math Warrior", name)
            self.assertNotEqual(display_name, name, f"{name} has no Math Warrior re-skin")

    def test_probability_wizard_deck_is_fully_themed(self):
        for name in self._deck_cards_with_upgrades("Probability Wizard"):
            display_name, _ = game_module.card_presentation("Probability Wizard", name)
            self.assertNotEqual(display_name, name, f"{name} has no Probability Wizard re-skin")

    def test_theming_is_cosmetic_only_numbers_match_card_library(self):
        # Spot-check a representative sample: the presented desc's numbers
        # must be identical to the real card's desc numbers, so re-theming
        # never silently drifts from what the card actually does.
        cases = [
            ("Math Warrior", "Dice Slash", "Dice Slash"),
            ("Math Warrior", "Chaos Bloom+", "Chaos Bloom+"),
            ("Probability Wizard", "Null Burst+", "Null Burst+"),
            ("Probability Wizard", "Dice Burst+", "Dice Burst+"),
        ]
        for character, presented_name, library_name in cases:
            _, display_desc = game_module.card_presentation(character, presented_name)
            real_nums = game_module.re.findall(r"\d+", game_module.CARD_LIBRARY[library_name]["desc"])
            presented_nums = game_module.re.findall(r"\d+", display_desc)
            self.assertEqual(presented_nums, real_nums, f"{character}/{presented_name} numbers drifted from CARD_LIBRARY")

    def test_reckless_roll_reskin_only_applies_to_the_wizard_who_starts_with_it(self):
        # Reckless Roll never appears in Math Warrior's starting deck, so it
        # should NOT get a Warrior re-skin -- it falls back to its plain
        # name there, exactly like any other reward-only card would.
        display_name, _ = game_module.card_presentation("Math Warrior", "Reckless Roll")
        self.assertEqual(display_name, "Reckless Roll")
        display_name, _ = game_module.card_presentation("Probability Wizard", "Reckless Roll")
        self.assertNotEqual(display_name, "Reckless Roll")


class RoundSevenGamblersFlurryTests(unittest.TestCase):
    """The interactive push-your-luck resolution for Gambler's Flurry."""

    def setUp(self):
        random.seed(7)
        self.g = game_module.Game()
        self.g.choose_character("Probability Wizard")
        self.g.start_combat(self.g.generate_enemy(1))
        self.g.enemy["hp"] = self.g.enemy["max_hp"] = 999
        self.g.player["hp"] = self.g.player["max_hp"] = 999
        self.g.player["block"] = 0

    def test_card_is_defined_and_reward_eligible(self):
        self.assertIn("Gambler's Flurry", game_module.CARD_LIBRARY)
        self.assertEqual(game_module.CARD_LIBRARY["Gambler's Flurry"]["cost"], 1)
        self.assertIn("Gambler's Flurry", game_module.REWARDABLE_CARDS)

    def test_opening_roll_of_1_busts_ends_turn_and_deals_no_damage(self):
        enemy_hp_before = self.g.enemy["hp"]
        turn_before = self.g.turn_number
        # Isolate the flurry's own recoil from whatever the enemy does on
        # its subsequent turn (end_player_turn() -> enemy_turn() also runs
        # here, and random.randint is mocked globally so it would otherwise
        # roll the enemy's attack too) by stubbing out enemy_turn entirely.
        with unittest.mock.patch.object(random, "randint", return_value=1), \
             unittest.mock.patch.object(self.g, "enemy_turn"):
            self.g.play_gamblers_flurry()
        self.assertFalse(self.g.push_luck_active)
        self.assertEqual(self.g.push_luck_banked, 0)
        self.assertEqual(self.g.enemy["hp"], enemy_hp_before)  # 0 damage dealt
        self.assertEqual(self.g.player["hp"], 999 - game_module.Game.GAMBLERS_FLURRY_RECOIL)
        self.assertEqual(self.g.turn_number, turn_before + 1)  # turn immediately ended

    def test_opening_roll_of_2_to_6_banks_and_stays_active(self):
        with unittest.mock.patch.object(random, "randint", return_value=4):
            self.g.play_gamblers_flurry()
        self.assertTrue(self.g.push_luck_active)
        self.assertEqual(self.g.push_luck_banked, 4)

    def test_bank_and_strike_deals_banked_damage_and_clears_state(self):
        with unittest.mock.patch.object(random, "randint", return_value=5):
            self.g.play_gamblers_flurry()
        enemy_hp_before = self.g.enemy["hp"]
        self.g.push_luck_bank_and_strike()
        self.assertFalse(self.g.push_luck_active)
        self.assertEqual(self.g.push_luck_banked, 0)
        self.assertEqual(self.g.enemy["hp"], enemy_hp_before - 5)

    def test_push_your_luck_success_adds_to_the_bank(self):
        with unittest.mock.patch.object(random, "randint", return_value=3):
            self.g.play_gamblers_flurry()
        with unittest.mock.patch.object(random, "randint", return_value=6):
            self.g.push_luck_push_again()
        self.assertTrue(self.g.push_luck_active)
        self.assertEqual(self.g.push_luck_banked, 9)

    def test_push_your_luck_bust_wipes_the_bank_and_deals_no_damage(self):
        with unittest.mock.patch.object(random, "randint", return_value=5):
            self.g.play_gamblers_flurry()
        enemy_hp_before = self.g.enemy["hp"]
        player_hp_before = self.g.player["hp"]
        with unittest.mock.patch.object(random, "randint", return_value=1):
            self.g.push_luck_push_again()
        self.assertFalse(self.g.push_luck_active)
        self.assertEqual(self.g.push_luck_banked, 0)
        self.assertEqual(self.g.enemy["hp"], enemy_hp_before)  # 0 damage dealt
        self.assertEqual(self.g.player["hp"], player_hp_before - game_module.Game.GAMBLERS_FLURRY_RECOIL)

    def test_next_roll_ev_formula(self):
        self.g.push_luck_banked = 0
        self.assertAlmostEqual(self.g.push_luck_next_roll_ev(), 17 / 6)
        self.g.push_luck_banked = 17
        self.assertAlmostEqual(self.g.push_luck_next_roll_ev(), 0.0)
        self.g.push_luck_banked = 20
        self.assertLess(self.g.push_luck_next_roll_ev(), 0.0)

    def test_other_cards_cannot_be_played_while_push_luck_is_active(self):
        with unittest.mock.patch.object(random, "randint", return_value=4):
            self.g.play_gamblers_flurry()
        self.g.hand = ["Strike"]
        hp_before = self.g.enemy["hp"]
        self.g.play_card("Strike")
        self.assertEqual(self.g.enemy["hp"], hp_before)  # ignored -- must resolve the flurry first
        self.assertIn("Strike", self.g.hand)  # never consumed

    def test_rolls_feed_the_law_of_large_numbers_roll_history(self):
        self.g.roll_history = {}
        with unittest.mock.patch.object(random, "randint", return_value=4):
            self.g.play_gamblers_flurry()
        self.assertIn(6, self.g.roll_history)
        self.assertEqual(sum(self.g.roll_history[6].values()), 1)

    def test_push_luck_banner_renders_without_raising(self):
        game_module.init_display()
        self.g.push_luck_active = True
        self.g.push_luck_banked = 5
        self.g.button_rects = {}
        game_module.draw_combat(self.g)
        self.assertIn("push_luck_bank", self.g.button_rects)
        self.assertIn("push_luck_again", self.g.button_rects)


class RoundSevenWagerTumbleTests(unittest.TestCase):
    """The 0.4s die-tumble animation between confirming a wager and its
    actual roll resolving (see Game.choose_safe_bet/choose_degenerate_bet,
    update_effects(), and draw_event())."""

    def setUp(self):
        random.seed(11)
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.state = "event"
        self.g.player["gold"] = 50

    def test_choosing_a_wager_defers_resolution_behind_the_tumble_timer(self):
        self.g.choose_safe_bet()
        self.assertEqual(self.g.state, "event")  # not resolved yet
        self.assertIsNotNone(self.g.wager_pending)
        self.assertAlmostEqual(self.g.wager_pending["timer"], game_module.WAGER_TUMBLE_SECONDS)

    def test_tumble_resolves_after_its_timer_elapses(self):
        self.g.choose_safe_bet()
        self.g.update_effects(dt=game_module.WAGER_TUMBLE_SECONDS / 2)
        self.assertIsNotNone(self.g.wager_pending)  # not yet -- only half elapsed
        self.assertEqual(self.g.state, "event")
        self.g.update_effects(dt=game_module.WAGER_TUMBLE_SECONDS / 2 + 0.01)
        self.assertIsNone(self.g.wager_pending)
        self.assertEqual(self.g.state, "event_result")

    def test_event_screen_renders_the_tumble_without_raising(self):
        game_module.init_display()
        self.g.choose_safe_bet()
        self.g.button_rects = {}
        game_module.draw_event(self.g)
        # Wager buttons must be hidden/disabled while the tumble is pending.
        self.assertNotIn("wager_safe", self.g.button_rects)
        self.assertNotIn("wager_degenerate", self.g.button_rects)
        self.assertNotIn("wager_walk_away", self.g.button_rects)


class RoundNineEventBackdropVisibilityTests(unittest.TestCase):
    """The wagering shrine (draw_event) used to hide its own casino
    backdrop behind fully-opaque (alpha=255) choice boxes and a solid
    backing bar behind the header -- the opposite of the rest site, whose
    campfire art shows through both a translucent panel and translucent
    option cards. This brings the shrine in line: the header has no
    backing box any more (a drop-shadow keeps it legible instead) and is
    rendered bigger, and the three wager choice boxes are translucent."""

    def setUp(self):
        game_module.init_display()
        self.g = game_module.Game()
        self.g.choose_character("Math Warrior")
        self.g.state = "event"
        self.g.player["gold"] = 50

    def test_draw_event_renders_without_raising(self):
        self.g.button_rects = {}
        game_module.draw_event(self.g)

    def test_wager_option_boxes_are_translucent_not_fully_opaque(self):
        # _rounded_gradient_body's fill colors carry the alpha that ends up
        # on screen -- fully opaque (255) is exactly what hid the backdrop
        # before, so this pins it below that.
        surface = game_module.pygame.Surface((300, 380))
        rect = game_module.pygame.Rect(0, 0, 300, 380)
        calls = []
        real_gradient = game_module._rounded_gradient_body

        def spy(surf, r, top_color, bottom_color, **kwargs):
            calls.append((top_color, bottom_color))
            return real_gradient(surf, r, top_color, bottom_color, **kwargs)

        with unittest.mock.patch.object(game_module, "_rounded_gradient_body", spy):
            game_module._draw_wager_option(
                self.g, rect, "Test Wager", game_module.BLUE, ["line"], ["math line"], "Go", "wager_safe",
            )
        self.assertTrue(calls)
        top_color, bottom_color = calls[0]
        self.assertEqual(len(top_color), 4, "fill color must carry an alpha channel")
        self.assertLess(top_color[3], 255)
        self.assertLess(bottom_color[3], 255)

    def test_shadowed_title_helper_has_no_backing_rect_and_scales_up(self):
        surface = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        plain = game_module.FONT_TITLE.render("THE SHRINE OF EXPECTED VALUE", True, game_module.GOLD)
        rect = game_module._draw_shadowed_title(surface, "THE SHRINE OF EXPECTED VALUE", 46, scale=1.4)
        self.assertGreater(rect.width, plain.get_width())
        self.assertGreater(rect.height, plain.get_height())

    def test_shadowed_title_is_used_for_the_shrine_header(self):
        surface = game_module.pygame.Surface((game_module.WIDTH, game_module.HEIGHT))
        with unittest.mock.patch.object(game_module, "_draw_shadowed_title", wraps=game_module._draw_shadowed_title) as spy:
            self.g.button_rects = {}
            game_module.draw_event(self.g)
        spy.assert_called_once()
        args, kwargs = spy.call_args
        self.assertEqual(args[1], "THE SHRINE OF EXPECTED VALUE")
        self.assertGreater(kwargs.get("scale", 1.0), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
