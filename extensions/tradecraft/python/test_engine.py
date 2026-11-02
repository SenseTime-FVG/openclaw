import json
import re
import unittest

from engine import TradeCraftEngine
from map_generator import TOTAL_ROUNDS, generate_map


class TradeCraftEngineTest(unittest.TestCase):
    def test_map_shape_and_seed_are_deterministic(self):
        first = generate_map(42)
        second = generate_map(42)

        self.assertEqual((len(first.hexes), len(first.vertices), len(first.edges)), (19, 54, 72))
        self.assertEqual(first.dice_sequence, second.dice_sequence)
        self.assertEqual(len(first.dice_sequence), TOTAL_ROUNDS * 4)
        self.assertTrue(all(2 <= roll <= 12 for roll in first.dice_sequence))

        cubes = {tile.cube for tile in first.hexes}
        self.assertIn((0, 0, 0), cubes)
        self.assertTrue(all(tuple(-axis for axis in cube) in cubes for cube in cubes))

    def test_placement_pairs_settlement_then_road_per_player(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)

        # A settlement sets the per-player pending-placement marker.
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertEqual(engine.state.phase, "placement")
        self.assertIsNone(engine.state.players[0].placement_vertex)
        vertex_id = next(iter(state["expansion_targets"]["settlements"]))

        settlement = engine.handle_action(
            "build", {"type": "settlement", "vertex_id": vertex_id}, 0
        )
        self.assertTrue(settlement["ok"], settlement)
        self.assertEqual(engine.state.players[0].placement_vertex, vertex_id)

        # A road must follow the settlement; it connects to placement_vertex.
        road_state = settlement["result"]["state"]
        self.assertNotIn("map", road_state)
        edge_id = next(iter(road_state["expansion_targets"]["roads"]))
        road = engine.handle_action("build", {"type": "road", "edge_id": edge_id}, 0)
        self.assertTrue(road["ok"], road)
        self.assertIsNone(engine.state.players[0].placement_vertex)

    def test_placement_rejects_road_before_settlement(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        road = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 1)
        self.assertFalse(road["ok"])
        self.assertIn("settlement", road["error"].lower())

    def test_placement_rejects_second_settlement_before_road(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        vertices = list(state["expansion_targets"]["settlements"])
        v0 = vertices[0]
        v1 = vertices[1]
        engine.handle_action("build", {"type": "settlement", "vertex_id": v0}, 0)
        # A second settlement before the mandatory road is rejected.
        second = engine.handle_action("build", {"type": "settlement", "vertex_id": v1}, 0)
        self.assertFalse(second["ok"])
        self.assertIn("road", second["error"].lower())

    def _play_engine(self, **kwargs):
        """A play-phase engine with a fixed round, for over-action tests."""
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=6, **kwargs)
        engine.state.phase = "play"
        engine.state.round_num = 1
        return engine

    def test_over_action_message_tiers_by_budget(self):
        # budget=3: the first two over-actions keep the plain cue, the 3rd is the
        # last allowed call (near-budget warning), the 4th crosses the line and
        # is escalated to the forfeit error. Boundary must be exactly: warn at
        # ==budget, error from >budget (matches the runner's Δillegal > budget).
        engine = self._play_engine(max_illegal_per_wake=3)
        engine.state.players[0].acted_this_wake = True

        errors = []
        for _ in range(4):
            res = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
            self.assertFalse(res["ok"])
            self.assertTrue(res["turn_over"])
            errors.append(res["error"])

        self.assertEqual(errors, [
            engine._t("action_complete"),
            engine._t("action_complete"),
            engine._t("action_near_budget", budget=3, used=3),
            engine._t("action_over_budget", budget=3, used=4),
        ])
        # The engine only tallies the single illegal counter; noop mirrors it.
        self.assertEqual(engine.state.players[0].illegal_actions, 4)
        self.assertEqual(engine.state.players[0].noop_actions, 4)

    def test_over_action_error_scales_with_zero_budget(self):
        engine = self._play_engine(max_illegal_per_wake=0)
        engine.state.players[0].acted_this_wake = True
        res = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        self.assertEqual(res["error"], engine._t("action_over_budget", budget=0, used=1))

    def test_noop_actions_is_a_subset_of_illegal_actions(self):
        # A genuine rule rejection is illegal but not a noop; an over-action is
        # both. This is why the budget must read illegal alone (noop ⊆ illegal).
        engine = self._play_engine()
        rejected = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        self.assertFalse(rejected["ok"])  # no road connects / cannot afford
        self.assertEqual(engine.state.players[0].illegal_actions, 1)
        self.assertEqual(engine.state.players[0].noop_actions, 0)

        engine.state.players[0].acted_this_wake = True
        over = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        self.assertFalse(over["ok"])
        self.assertEqual(engine.state.players[0].illegal_actions, 2)
        self.assertEqual(engine.state.players[0].noop_actions, 1)

    def test_illegal_baseline_resets_usage_on_advance(self):
        engine = self._play_engine(max_illegal_per_wake=3)
        engine.state.players[0].acted_this_wake = True
        engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        self.assertEqual(engine.state.players[0].illegal_actions, 2)

        engine._advance(0)
        self.assertEqual(engine.state.players[0].illegal_baseline, 2)
        self.assertFalse(engine.state.players[0].acted_this_wake)

        # The next wake starts fresh: its first over-action is the plain cue.
        engine.state.players[0].acted_this_wake = True
        res = engine.handle_action("build", {"type": "road", "edge_id": "e0"}, 0)
        self.assertEqual(res["error"], engine._t("action_complete"))

    def test_action_budget_messages_exist_in_all_languages(self):
        from i18n import CATALOGS

        for lang in ("en", "zh", "fr", "es", "ru", "ar"):
            catalog = CATALOGS.get(lang, {})
            for key in ("action_near_budget", "action_over_budget"):
                self.assertIn(key, catalog, f"{lang} catalog missing {key}")
                # Placeholders must resolve for the tiered values.
                catalog[key].format(budget=3, used=4)

    def test_end_placement_switches_phase_and_resets_round(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=3)
        self.assertEqual(engine.state.phase, "placement")
        result = engine.end_placement()
        self.assertTrue(result["ok"], result)
        self.assertEqual(engine.state.phase, "play")
        self.assertEqual(engine.state.round_num, 0)

    def test_start_round_produces_resources_without_compensation(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=3)
        engine.state.phase = "play"
        engine.state.round_num = 0
        for tile in engine.game_map.hexes:
            if not tile.is_desert:
                tile.number = 99
        productive_tile = next(tile for tile in engine.game_map.hexes if not tile.is_desert)
        productive_tile.number = 2
        productive_vertex = next(
            vertex for vertex in engine.game_map.vertices
            if productive_tile.id in vertex.hexes
        )
        engine.state.players[0].buildings[productive_vertex.id] = 1
        engine.game_map.dice_sequence[:1] = [2]

        engine.start_round()
        result = engine.roll_for_player(0)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["roll"], 2)
        self.assertEqual(engine.state.round_num, 1)
        self.assertEqual(
            engine.state.dice_this_round, [{"player_id": 0, "roll": 2}]
        )
        self.assertEqual(
            engine.state.players[0].resources[productive_tile.resource], 1
        )
        # Catan has no "no output" bonus: players who produced nothing stay at
        # zero (the robber roll is the only 7-related event).
        self.assertEqual(sum(engine.state.players[0].resources.values()), 1)
        self.assertEqual(sum(engine.state.players[1].resources.values()), 0)
        self.assertEqual(sum(engine.state.players[2].resources.values()), 0)
        self.assertFalse(result["result"]["robber_triggered"])
        # One roll per player per round.
        self.assertFalse(engine.roll_for_player(0)["ok"])

    def test_settle_is_idempotent_and_guarded(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        # Cannot settle during placement or before all rounds are played.
        self.assertFalse(engine.settle()["ok"])
        engine.state.phase = "play"
        engine.state.round_num = 0
        self.assertFalse(engine.settle()["ok"])
        engine.state.round_num = 2
        settled = engine.settle()
        self.assertTrue(settled["ok"], settled)
        self.assertTrue(settled["result"]["game_over"])
        self.assertEqual(len(settled["result"]["scores"]), 3)
        # Idempotent.
        again = engine.settle()
        self.assertTrue(again["ok"])
        self.assertTrue(again["result"]["game_over"])

    def test_settle_after_synchronous_trade(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=1)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["brick"] = 1
        engine.mock_trade_decision = "reject"
        trade = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertTrue(trade["result"]["trade_rejected"])
        self.assertTrue(engine.settle()["ok"])
    def test_snapshot_round_scores_records_per_round(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=6)
        engine.state.phase = "play"
        engine.state.round_num = 1
        snap = engine.snapshot_round_scores()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["result"]["round"], 1)
        self.assertEqual(len(snap["result"]["scores"]), 3)
        self.assertIn(1, engine.state.round_scores)

    def test_partial_view_falls_back_to_latest_completed_round(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=6)
        engine.state.phase = "play"
        # Round 1 completes; record its snapshot.
        engine.state.round_num = 1
        engine.snapshot_round_scores()
        scores_r1 = engine._latest_completed_round_scores()
        # Round 2 completes with a score change and gets its own snapshot.
        engine.state.round_num = 2
        engine.state.players[0].buildings["v0"] = 2
        engine.snapshot_round_scores()
        scores_r2 = engine._latest_completed_round_scores()
        self.assertNotEqual(scores_r1, scores_r2, "snapshot should reflect the score change")
        # Round 3 in-flight (stalled): the view must expose round-2 scores, not
        # the in-flight state and not None.
        engine.state.round_num = 3
        engine.state.players[0].buildings["v1"] = 3  # in-flight change must NOT count
        view = engine._view_game_state(0)
        self.assertEqual(view["scores"], scores_r2)
        self.assertNotEqual(view["scores"], engine._calculate_scores())
        self.assertEqual(view["winner"], engine._winner_from_scores(scores_r2))

    def test_view_scores_none_before_any_completed_round(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=6)
        engine.state.phase = "play"
        engine.state.round_num = 1
        view = engine._view_game_state(0)
        self.assertIsNone(view["scores"], "no completed round yet -> scores None")

    # ── Team mode ─────────────────────────────────────────────────────

    def test_team_mode_freeforall_by_default(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2)
        self.assertEqual(engine.state.team_of, {})
        self.assertIsNone(engine.state.teams)
        self.assertTrue(engine._cross_team(0, 3))
        # Free-for-all settle has no team fields.
        engine.state.phase = "play"
        engine.state.round_num = 2
        settled = engine.settle()
        self.assertTrue(settled["ok"])
        self.assertNotIn("team_scores", settled["result"])

    def test_team_mode_validates_roster(self):
        with self.assertRaises(ValueError):
            TradeCraftEngine(seed=42, num_players=4, teams=[[0, 3], [1]])  # missing player 2
        with self.assertRaises(ValueError):
            TradeCraftEngine(seed=42, num_players=4, teams=[[0, 3], [1, 2, 3]])  # dup player 3
        with self.assertRaises(ValueError):
            TradeCraftEngine(seed=42, num_players=4, teams=[[0, 4], [1, 2], [3]])  # out of range

    def test_cross_team_trade_counts_only_cross_team(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2,
                                  teams=[[0, 3], [1, 2]])
        engine.state.phase = "play"
        engine.state.round_num = 1
        for pid in range(4):
            engine.state.players[pid].resources.update(
                {"wood": 3, "brick": 3, "sheep": 3, "wheat": 3, "ore": 3}
            )
        engine.mock_trade_decision = "accept"
        # Same-team trade (P0 <-> P3): resources move, trade_master does NOT tick.
        same = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 3},
            0,
        )
        self.assertTrue(same["ok"], same)
        self.assertEqual(engine.state.players[0].trades_completed, 0)
        self.assertEqual(engine.state.players[3].trades_completed, 0)
        _ = (engine._advance(0),)  # wake boundary
        # Cross-team trade (P0 -> P1): trade_master ticks.
        cross = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertTrue(cross["ok"], cross)
        self.assertEqual(engine.state.players[0].trades_completed, 1)
        self.assertEqual(engine.state.players[1].trades_completed, 1)
    def test_settle_outputs_team_scores(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2,
                                  teams=[[0, 3], [1, 2]])
        engine.state.phase = "play"
        engine.state.round_num = 2
        # P0 and P3 each get a building worth 2 (team mean 2.0);
        # P1 worth 1, P2 worth 1 (team mean 1.0).
        engine.state.players[0].buildings["v0"] = 2
        engine.state.players[3].buildings["v1"] = 2
        engine.state.players[1].buildings["v2"] = 1
        engine.state.players[2].buildings["v3"] = 1
        settled = engine.settle()["result"]
        self.assertEqual(settled["teams"], [[0, 3], [1, 2]])
        self.assertEqual(settled["team_scores"], [2.0, 1.0])
        self.assertEqual(settled["team_winner"], 0)
        # View exposes team view for agents.
        view = engine._view_game_state(0)
        self.assertEqual(view["team_scores"], [2.0, 1.0])
        self.assertEqual(view["team_winner"], 0)

    def test_game_state_uses_compact_map_view(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        state = engine.handle_action("view_game_state", {}, 0)["result"]

        self.assertEqual(
            (len(state["map"]["hexes"]),
             len(state["map"]["vertices"]),
             len(state["map"]["edges"])),
            (19, 54, 72),
        )
        # h0 must be [resource, number]: resource is a non-empty str, number
        # is an int (desert yields None). Don't hard-code the exact layout —
        # balanced generation may re-arrange resource/number placement.
        h0 = state["map"]["hexes"]["h0"]
        self.assertEqual(len(h0), 2)
        self.assertTrue(isinstance(h0[0], str) and h0[0])
        self.assertTrue(h0[1] is None or (isinstance(h0[1], int) and 2 <= h0[1] <= 12))
        self.assertEqual(state["map"]["vertices"]["v4"], ["h0", "h3", "h4"])
        self.assertEqual(state["map"]["edges"]["e20"], ["v17", "v4"])
        self.assertNotIn("available_vertices", state)
        self.assertNotIn("available_edges", state)
        self.assertEqual(state["recent_actions"], {
            "previous_round": [],
            "current_round": [],
        })
        self.assertNotIn("current_player", state)
        self.assertNotIn("placement_round", state)

    def test_expansion_targets_are_per_player_and_not_affordability_filtered(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].buildings["v0"] = 1

        state = engine.handle_action("view_game_state", {}, 0)["result"]

        self.assertEqual(sum(engine.state.players[0].resources.values()), 0)
        self.assertEqual(
            set(state["expansion_targets"]["roads"]),
            {"e0", "e5", "e7"},
        )
        self.assertEqual(state["players"][0]["placement_vertex"], None)

    def test_agent_facing_state_hides_legality_counters(self):
        # legality is a scheduler/audit side channel: the model learns about
        # rule violations from tool errors, not from counters.
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        view = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertNotIn("legality", view)
        self.assertIn("my_player_id", view)
        # the scheduler snapshot still carries the counters
        snapshot = engine._view_game_state(0, include_legality=True)
        self.assertIn("legality", snapshot)
        self.assertEqual(
            sorted(snapshot["legality"]),
            [
                "forfeits",
                "illegal_actions",
                "noop_actions",
                "responder_forfeits",
                "responder_wakes",
                "total_actions",
            ],
        )

    def test_step_instruction_carries_short_recommendation_list(self):
        # The per-wake shortlist is the "legal move highlight": a handful of
        # ranked, rule-legal targets -- never the full 54-vertex enumeration.
        # Assertions are on the English catalog, so pin the language explicitly.
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=8, language="en")
        text = engine._step_instruction(0)
        self.assertIn("Recommended moves", text)
        self.assertIn("expansion_targets", text)
        listed = re.findall(r"^- (settlement|road|upgrade)", text, re.M)
        self.assertGreater(len(listed), 0)
        self.assertLessEqual(len(listed), engine.RECOMMENDATION_LIMIT)

    def test_recommendations_are_capped_unique_sorted_and_legal(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)
        items = engine._recommendations(0)
        self.assertGreater(len(items), 0)
        self.assertLessEqual(len(items), engine.RECOMMENDATION_LIMIT)
        keys = [(item["kind"], item["id"]) for item in items]
        self.assertEqual(len(keys), len(set(keys)))
        legal = engine._expansion_targets_view(0)["settlements"]
        for item in items:
            if item["kind"] == "settlement":
                self.assertIn(item["id"], legal)
        scores = [item["score"] for item in items]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_recommendations_exclude_sites_too_close_to_buildings(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)
        engine.state.players[1].buildings["v0"] = 1
        self.assertFalse(engine._check_distance("v1"))
        recommended = {
            item["id"]
            for item in engine._recommendations(0)
            if item["kind"] == "settlement"
        }
        self.assertNotIn("v1", recommended)
        self.assertNotIn("v0", recommended)

    def test_recommendations_update_after_each_move(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)
        before = engine._recommendation_block(0)
        self.assertIn("settlement", before)
        engine.handle_action("build", {"type": "settlement", "vertex_id": "v0"}, 0)
        after = engine._recommendation_block(0)
        self.assertNotEqual(before, after)
        # placement pairing: once the settlement is down, the next move is a road
        self.assertIn("road", after)

    def test_recommendations_are_deterministic(self):
        first = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)._recommendations(0)
        second = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)._recommendations(0)
        self.assertEqual(
            [(item["kind"], item["id"]) for item in first],
            [(item["kind"], item["id"]) for item in second],
        )

    def test_recommendations_hide_affordability_verdicts(self):
        # The shortlist may show costs (fixed rules), but never whether the
        # player can currently afford them: the model must judge that itself.
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=8)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].buildings["v0"] = 1
        lowered = engine._recommendation_block(0).lower()
        for marker in ("affordable", "afford", "可负担", "costear", "оплатить"):
            self.assertNotIn(marker, lowered)

    def test_step_instruction_follows_game_language(self):
        cases = {
            "zh": ("摆子阶段", "推荐动作"),
            "fr": ("phase de placement", "Actions recommandées"),
            "es": ("fase de colocación", "Acciones recomendadas"),
            "ru": ("фазе расстановки", "Рекомендуемые действия"),
            "ar": ("مرحلة التوزيع", "الحركات الموصى بها"),
        }
        for lang, (placement, rec) in cases.items():
            text = TradeCraftEngine(
                seed=42, num_players=4, max_rounds=8, language=lang
            )._step_instruction(0)
            self.assertIn(placement, text)
            self.assertIn(rec, text)

    def test_all_languages_define_instruction_keys(self):
        from i18n import LANGS, get_catalog

        keys = (
            "placement_title",
            "placement_intro",
            "placement_need_settlement",
            "placement_need_road",
            "rec_title",
            "rec_settlement",
            "rec_road",
            "rec_upgrade",
            "rec_footer",
            "trade_pending_title",
            "trade_proposer",
            "trade_responder",
        )
        for lang in LANGS:
            catalog = get_catalog(lang)
            for key in keys:
                self.assertIn(key, catalog, f"{lang} catalog missing {key}")

    def test_placement_expansion_targets_reflect_pairing(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertGreaterEqual(len(state["expansion_targets"]["settlements"]), 1)
        self.assertEqual(state["expansion_targets"]["roads"], {})

    def test_action_snapshot_attached_after_success(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.buildings["v4"] = 1
        player.resources["wheat"] = 2
        player.resources["ore"] = 3

        upgraded = engine.handle_action(
            "build", {"type": "upgrade", "vertex_id": "v4", "to_level": 2}, 0
        )

        self.assertTrue(upgraded["ok"], upgraded)
        state = upgraded["result"]["state"]
        self.assertNotIn("map", state)
        building = next(
            item for item in state["players"][0]["buildings"]
            if item["vertex_id"] == "v4"
        )
        self.assertEqual(building["level"], 2)
        self.assertEqual(state["recent_actions"]["current_round"], [{
            "action_id": 1,
            "round_num": 1,
            "phase": "play",
            "actor": 0,
            "action": "build",
            "details": {
                "type": "upgrade",
                "vertex_id": "v4",
                "level": 2,
            },
        }])

    def test_recent_actions_excludes_views_and_failures(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=5)
        engine.state.phase = "play"
        engine.state.round_num = 4

        rejected = engine.handle_action("send_message", {"content": "   "}, 2)
        self.assertFalse(rejected["ok"])
        self.assertEqual(engine.state.action_history, [])

        sent = engine.handle_action(
            "send_message", {"target": 0, "content": "Closing round four."}, 2
        )
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(engine.state.pending_chat.sender, 2)
        self.assertEqual(engine.state.pending_chat.target, 0)
        state = sent["result"]["state"]
        self.assertEqual(state["round"], 4)  # no longer auto-advances the round
        self.assertEqual(state["recent_actions"]["current_round"], [{
            "action_id": 1,
            "round_num": 4,
            "phase": "play",
            "actor": 2,
            "action": "send_message",
            "details": {
                "message_id": 1,
                "recipients": [0],
                "content": "Closing round four.",
            },
        }])


    def test_send_message_requires_int_target_in_range(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2)
        engine.state.phase = "play"
        self.assertFalse(engine.handle_action("send_message", {"content": "hi"}, 0)["ok"])
        self.assertFalse(engine.handle_action("send_message", {"target": "1", "content": "hi"}, 0)["ok"])
        self.assertFalse(engine.handle_action("send_message", {"target": 9, "content": "hi"}, 0)["ok"])
        self.assertFalse(engine.handle_action("send_message", {"target": 0, "content": "hi"}, 0)["ok"])
        ok = engine.handle_action("send_message", {"target": 2, "content": "hi"}, 0)
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(engine.state.pending_chat.target, 2)
        self.assertEqual(ok["result"]["target"], 2)

    def test_pending_chat_resolve_returns_reply_verbatim(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2)
        engine.state.phase = "play"
        engine.handle_action("send_message", {"target": 1, "content": "trade?"}, 0)
        res = engine.resolve_chat("yes, two iron for one crystal")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["reply"]["from"], 1)
        self.assertEqual(res["result"]["reply"]["content"], "yes, two iron for one crystal")
        self.assertIsNone(engine.state.pending_chat)

    def test_pending_chat_forfeit_returns_bracketed_phrase(self):
        engine = TradeCraftEngine(seed=42, num_players=4, max_rounds=2)
        engine.state.phase = "play"
        engine.handle_action("send_message", {"target": 3, "content": "hi"}, 0)
        res = engine.forfeit_player(3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["reply"]["content"], "[forfeited]")
        self.assertIsNone(engine.state.pending_chat)

    def test_upgrade_caps_at_level_two(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.buildings["v4"] = 1
        player.resources.update({"wheat": 2, "ore": 3})

        upgraded = engine.handle_action(
            "build", {"type": "upgrade", "vertex_id": "v4", "to_level": 2}, 0
        )
        self.assertTrue(upgraded["ok"], upgraded)
        self.assertEqual(player.buildings["v4"], 2)
        self.assertEqual(player.resources["wheat"], 0)
        self.assertEqual(player.resources["ore"], 0)

        # Level 2 is the cap: there is no third tier to spend resources on.
        engine._advance(0)
        player.resources.update({"wheat": 3, "ore": 4})
        beyond = engine.handle_action(
            "build", {"type": "upgrade", "vertex_id": "v4", "to_level": 3}, 0
        )
        self.assertFalse(beyond["ok"])
        self.assertIn("Max level is 2", beyond["error"])
        self.assertEqual(player.buildings["v4"], 2)

    def test_upgrade_refuses_when_short_of_cost(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.buildings["v4"] = 1
        player.resources.update({"wheat": 2, "ore": 2})
        refused = engine.handle_action(
            "build", {"type": "upgrade", "vertex_id": "v4", "to_level": 2}, 0
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(player.buildings["v4"], 1)

    def test_get_view_carries_rule_level_step_instruction(self):
        # Text assertions below are English: pin the language so a future
        # default-language change cannot silently break this test.
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2, language="en")

        # Placement guidance is per-player and names the exact pairing step.
        view = engine._get_view(0)
        self.assertIn("instruction", view)
        self.assertIn("Placement for P0", view["instruction"])
        self.assertIn("place a settlement now", view["instruction"])
        # Other players still get guidance (no order/turn gate anymore).
        self.assertIn("Placement for P1", engine._get_view(1)["instruction"])

    def test_trade_acceptance_and_resource_validation(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 2
        engine.state.players[1].resources["brick"] = 2

        invalid = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": -1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertFalse(invalid["ok"])

        engine.mock_trade_decision = "accept"
        engine.mock_trade_reason = "Accepted; stone is useful."
        accepted = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertIn("trade_accepted", accepted["result"])
        self.assertEqual(engine.state.players[0].resources["brick"], 1)
        self.assertEqual(engine.state.players[1].resources["wood"], 1)
        feedback = accepted["result"]["response_message"]
        self.assertEqual(feedback["recipients"], [0])
        self.assertEqual(feedback["content"], "Accepted; stone is useful.")
        self.assertEqual(feedback["kind"], "trade")
    def test_trade_rejection_delivers_feedback(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["brick"] = 1
        engine.mock_trade_decision = "reject"
        engine.mock_trade_reason = "I need my stone."
        result = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["trade_rejected"])
        self.assertEqual(result["result"]["response_message"]["recipients"], [0])
        self.assertEqual(result["result"]["response_message"]["content"], "I need my stone.")
        self.assertEqual(engine.state.players[0].resources["wood"], 1)
    def test_unknown_responder_decision_is_treated_as_rejection(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["brick"] = 1
        engine.mock_trade_decision = "counter"
        result = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"brick": 1}, "target": 1},
            0,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["trade_rejected"])
        self.assertEqual(engine.state.players[0].resources["wood"], 1)
    def test_message_is_directed_without_consuming_action(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1

        sent = engine.handle_action("send_message", {"target": 1, "content": "Trade wood?"}, 0)
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["result"]["target"], 1)
        self.assertEqual(sent["result"]["message_sent"], {
            "id": 1,
            "from": 0,
            "recipients": [1],
            "content": "Trade wood?",
            "round": 1,
            "kind": "chat",
        })
        # A private message is a *free* step: it does not consume the wake and
        # carries no end-of-turn cue.
        self.assertFalse(engine.state.players[0].acted_this_wake)
        self.assertNotIn("turn_instruction", sent["result"])
        self.assertNotIn("turn_over", sent["result"])
        # Several messages in one wake are allowed (message players one by one).
        second = engine.handle_action("send_message", {"target": 2, "content": "and you?"}, 0)
        self.assertTrue(second["ok"], second)
        self.assertFalse(engine.state.players[0].acted_this_wake)

    def test_second_action_same_wake_is_rejected(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        # Once a player commits its scheduled action, the wake is spent.
        engine.state.players[0].acted_this_wake = True
        engine.state.players[0].resources.update(
            {"wood": 9, "brick": 9, "sheep": 9, "wheat": 9, "ore": 9}
        )
        engine.state.players[1].resources.update(
            {"wood": 9, "brick": 9, "sheep": 9, "wheat": 9, "ore": 9}
        )
        # Any further *action* tool call in the same wake is rejected.
        for action, params in (("build", {}),
                               ("claim_project", {})):
            extra = engine.handle_action(action, params, 0)
            self.assertFalse(extra["ok"])
            self.assertIn("complete", extra["error"].lower())
        # The rejected over-actions are counted as illegal.
        self.assertEqual(engine.state.players[0].illegal_actions, 2)
        # A trade proposal is a free step: still allowed after the scheduled
        # action, and still bounded by its own per-wake quota.
        engine.mock_trade_decision = "reject"
        trade = engine.handle_action(
            "propose_trade",
            {"target": 1, "offer": {"sheep": 1}, "request": {"ore": 1}},
            0,
        )
        self.assertTrue(trade["ok"], trade)
        self.assertEqual(engine.state.players[0].wake_trades_used, 1)
        # view_game_state is still allowed without consuming the wake flag.
        view = engine.handle_action("view_game_state", {}, 0)
        self.assertTrue(view["ok"])
        # send_message is a free step: allowed even after the action is done.
        free = engine.handle_action("send_message", {"target": 1, "content": "hi"}, 0)
        self.assertTrue(free["ok"])
        # advance resets the flag so the player can act on the next wake.
        engine._advance(0)
        self.assertFalse(engine.state.players[0].acted_this_wake)

    def test_forfeit_records_without_a_pending_trade(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        result = engine.forfeit_player(1)
        self.assertTrue(result["ok"])
        self.assertEqual(engine.state.players[1].forfeits, 1)
    def test_hex_lord_project_is_unavailable(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1

        state = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertNotIn("hex_lord", {project["id"] for project in state["projects"]})

        claimed = engine.handle_action(
            "claim_project", {"project_id": "hex_lord"}, 0
        )
        self.assertFalse(claimed["ok"])

    def test_claim_project_is_included_in_recent_actions(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].trades_completed = 8

        claimed = engine.handle_action(
            "claim_project", {"project_id": "trade_master"}, 0
        )
        self.assertTrue(claimed["ok"], claimed)
        self.assertEqual(
            claimed["result"]["state"]["recent_actions"]["current_round"][-1],
            {
                "action_id": 1,
                "round_num": 1,
                "phase": "play",
                "actor": 0,
                "action": "claim_project",
                "details": {"project_id": "trade_master"},
            },
        )

    def test_resource_tycoon_requires_twenty_resources(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.resources["wood"] = 19
        self.assertFalse(engine.handle_action(
            "claim_project", {"project_id": "resource_tycoon"}, 0)["ok"])
        player.resources["wood"] = 20
        self.assertTrue(engine.handle_action(
            "claim_project", {"project_id": "resource_tycoon"}, 0)["ok"])

    def test_project_points_are_rebalanced(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertEqual(
            {project["id"]: project["points"] for project in state["projects"]},
            {
                "trade_master": 1,
                "build_master": 2,
                "tech_pioneer": 2,
                "resource_tycoon": 1,
                "road_king": 2,
            },
        )

    def test_held_resources_do_not_score(self):
        # 2026-10-21: the resource-count bonus was removed. Only buildings and
        # projects score, so hoarding is not rewarded.
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        for player, resources in zip(engine.state.players, (3, 4, 8)):
            player.resources["wood"] = resources
        self.assertEqual(engine._calculate_scores(), [0, 0, 0])

    def test_buildings_and_roads_are_globally_exclusive(self):
        engine = TradeCraftEngine(seed=3, num_players=3, max_rounds=2)
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        vertex_id = next(iter(state["expansion_targets"]["settlements"]))
        placed = engine.handle_action(
            "build", {"type": "settlement", "vertex_id": vertex_id}, 0
        )
        self.assertTrue(placed["ok"], placed)
        edge_id = next(iter(placed["result"]["state"]["expansion_targets"]["roads"]))
        engine.handle_action("build", {"type": "road", "edge_id": edge_id}, 0)

        duplicate = engine.handle_action(
            "build", {"type": "settlement", "vertex_id": vertex_id}, 1
        )
        self.assertFalse(duplicate["ok"])
        self.assertIn("already exists", duplicate["error"])

    # ── Map balance b-mode ───────────────────────────────────────────

    def test_map_resources_anticlustered(self):
        from map_generator import _hex_adjacency
        adj = _hex_adjacency()
        for ms in (0, 1, 42, 99, 12345):
            m = generate_map(42, map_seed=ms)
            for i, nbrs in adj.items():
                for j in nbrs:
                    if j < i:
                        continue
                    self.assertNotEqual(
                        m.hexes[i].resource, m.hexes[j].resource,
                        f"map_seed={ms}: same resource on adjacent hexes h{i}/h{j}",
                    )

    def test_map_red_numbers_not_adjacent(self):
        from map_generator import _hex_adjacency, RED_NUMBERS
        adj = _hex_adjacency()
        for ms in (0, 1, 42, 99, 12345):
            m = generate_map(42, map_seed=ms)
            for i, nbrs in adj.items():
                for j in nbrs:
                    if j < i:
                        continue
                    ni, nj = m.hexes[i].number, m.hexes[j].number
                    self.assertFalse(
                        ni in RED_NUMBERS and nj in RED_NUMBERS,
                        f"map_seed={ms}: red numbers adjacent h{i}({ni})/h{j}({nj})",
                    )

    def test_map_seed_deterministic_and_independent(self):
        # Same map_seed -> identical layout; different map_seed -> different layout.
        a1 = generate_map(42, map_seed=7)
        a2 = generate_map(42, map_seed=7)
        b = generate_map(42, map_seed=8)
        la = [(h.resource, h.number) for h in a1.hexes]
        self.assertEqual(la, [(h.resource, h.number) for h in a2.hexes])
        lb = [(h.resource, h.number) for h in b.hexes]
        self.assertNotEqual(la, lb)
        # dice sequence is controlled by `seed`, not `map_seed`
        self.assertEqual(a1.dice_sequence, b.dice_sequence)

    def test_map_seed_none_falls_back_to_seed(self):
        a = generate_map(42, map_seed=None)
        b = generate_map(42)
        self.assertEqual([(h.resource, h.number) for h in a.hexes],
                         [(h.resource, h.number) for h in b.hexes])

    def test_engine_map_seed_passthrough(self):
        m = generate_map(42, map_seed=77)
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2, map_seed=77)
        view = engine._compact_map_view()
        for h in m.hexes:
            self.assertEqual(view["hexes"][h.id], [h.resource, h.number])

    # ── trade normalization (zero padding) ────────────────────────────

    def test_trade_zero_padding_is_normalized(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["ore"] = 2
        engine.state.players[1].resources["wheat"] = 1
        engine.mock_trade_decision = "accept"

        padded_offer = {"wood": 0, "brick": 0, "sheep": 0, "wheat": 0, "ore": 2}
        padded_request = {"wood": 0, "brick": 0, "sheep": 0, "wheat": 1, "ore": 0}
        result = engine.handle_action(
            "propose_trade",
            {"offer": padded_offer, "request": padded_request, "target": 1},
            0,
        )
        self.assertTrue(result["ok"], result)
        accepted = result["result"]["trade_accepted"]
        self.assertEqual(accepted["offer"], {"ore": 2})
        self.assertEqual(accepted["request"], {"wheat": 1})
        self.assertEqual(engine.state.players[0].resources["wheat"], 1)
        self.assertEqual(engine.state.players[1].resources["ore"], 2)
    def test_trade_side_with_only_zeros_is_rejected(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        zeros = {"wood": 0, "brick": 0, "sheep": 0, "wheat": 0, "ore": 0}
        proposal = engine.handle_action(
            "propose_trade", {"offer": zeros, "request": {"brick": 1}, "target": 1}, 0
        )
        self.assertFalse(proposal["ok"])
        self.assertIn("at least one positive", proposal["error"])

    def test_trade_negative_amount_is_rejected(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        proposal = engine.handle_action(
            "propose_trade", {"offer": {"wood": -1}, "request": {"brick": 1}, "target": 1}, 0
        )
        self.assertFalse(proposal["ok"])
        self.assertIn("negative", proposal["error"])

    # ── placement end must be an explicit "end your turn" ────────────

    def test_placement_messages_tell_the_agent_to_end_its_turn(self):
        # The scheduler only advances to the play phase once the agent stops
        # calling tools, so the placement-end text has to say that explicitly.
        from i18n import LANGS, get_catalog

        turn_markers = {
            "en": "END YOUR TURN",
            "zh": "结束本回合",
            "fr": "TERMINEZ VOTRE TOUR",
            "es": "TERMINA TU TURNO",
            "ru": "ЗАВЕРШИТЕ ХОД",
            "ar": "أنهِ دورك",
        }
        done_markers = {
            "en": "end your reply",
            "zh": "结束回复",
            "fr": "terminez votre réponse",
            "es": "termina tu respuesta",
            "ru": "закончите ответ",
            "ar": "أنهِ ردّك",
        }
        for lang in LANGS:
            catalog = get_catalog(lang)
            self.assertIn(turn_markers[lang], catalog["placement_complete"], lang)
            self.assertIn(done_markers[lang], catalog["placement_only_build"], lang)


    # ── Catan base rules: five resources, robber, bank/port ────────────

    def test_map_uses_five_catan_resources_and_central_desert(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        resources = {tile.resource for tile in engine.game_map.hexes}
        self.assertEqual(
            resources - {"desert"},
            {"wood", "brick", "sheep", "wheat", "ore"},
        )
        centre = engine.game_map.hex_by_id("h9")
        self.assertEqual(centre.resource, "desert")
        self.assertIsNone(centre.number)
        # 18 non-desert hexes carry exactly the 18 base-game number tokens.
        numbered = [t.number for t in engine.game_map.hexes if t.number]
        self.assertEqual(len(numbered), 18)
        self.assertNotIn(7, numbered)
        # Twelve ports, each touching exactly one hex.
        self.assertEqual(len(engine.game_map.ports), 12)
        for vid in engine.game_map.ports:
            self.assertEqual(len(engine.game_map.vertex_by_id(vid).hexes), 1)
        self.assertEqual(engine.state.robber_hex, "h9")

    def test_robber_blocks_production_on_its_hex(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=3)
        engine.state.phase = "play"
        engine.state.round_num = 0
        for tile in engine.game_map.hexes:
            if not tile.is_desert:
                tile.number = 99
        productive = next(t for t in engine.game_map.hexes if not t.is_desert)
        productive.number = 2
        vertex = next(v for v in engine.game_map.vertices if productive.id in v.hexes)
        engine.state.players[0].buildings[vertex.id] = 1
        engine.game_map.dice_sequence[:1] = [2]

        # Unblocked: the building produces.
        engine.state.robber_hex = "h9"
        engine.start_round()
        self.assertTrue(engine.roll_for_player(0)["ok"])
        self.assertEqual(
            engine.state.players[0].resources[productive.resource], 1
        )

        # Blocked: the robber sits on the producing hex.
        engine.state.players[0].resources = {r: 0 for r in (
            "wood", "brick", "sheep", "wheat", "ore")}
        engine._dice_idx = 0
        engine.game_map.dice_sequence[:1] = [2]
        engine.state.robber_hex = productive.id
        engine.start_round()
        self.assertTrue(engine.roll_for_player(0)["ok"])
        self.assertEqual(sum(engine.state.players[0].resources.values()), 0)

    def test_seven_triggers_robber_opportunity(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=3)
        engine.state.phase = "play"
        engine.state.round_num = 0
        engine.game_map.dice_sequence[:1] = [7]
        engine.start_round()
        result = engine.roll_for_player(1)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["robber_triggered"])
        self.assertEqual(result["result"]["robber_hex"], "h9")
        # A 7 produces nothing for anyone.
        self.assertEqual(sum(engine.state.players[0].resources.values()), 0)

    def test_bank_trade_is_four_to_one_and_port_trades_three_to_one(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.resources.update({"wood": 4, "brick": 3})

        # 4:1 at the bank.
        trade = engine.handle_action(
            "propose_trade",
            {"target": "system", "offer": {"wood": 4}, "request": {"ore": 1}},
            0,
        )
        self.assertTrue(trade["ok"], trade)
        self.assertEqual(player.resources["wood"], 0)
        self.assertEqual(player.resources["ore"], 1)
        self.assertEqual(trade["result"]["bank_trade"]["rate"], 4)

        # A wrong ratio is refused, not silently accepted.
        engine._advance(0)  # wake boundary: one scheduled action per wake
        wrong = engine.handle_action(
            "propose_trade",
            {"target": "system", "offer": {"brick": 2}, "request": {"sheep": 1}},
            0,
        )
        self.assertFalse(wrong["ok"])
        self.assertIn("4:1", wrong["error"])

        # With a building on a port vertex the ratio drops to 3:1.
        port_vertex = engine.game_map.ports[0]
        player.buildings[port_vertex] = 1
        engine._advance(0)
        port_trade = engine.handle_action(
            "propose_trade",
            {"target": "system", "offer": {"brick": 3}, "request": {"sheep": 1}},
            0,
        )
        self.assertTrue(port_trade["ok"], port_trade)
        self.assertEqual(port_trade["result"]["bank_trade"]["rate"], 3)
        self.assertEqual(player.resources["brick"], 0)
        self.assertEqual(player.resources["sheep"], 1)

    def test_bank_trade_reports_insufficient_stock(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        player = engine.state.players[0]
        player.resources["wood"] = 2
        result = engine.handle_action(
            "propose_trade",
            {"target": "system", "offer": {"wood": 4}, "request": {"ore": 1}},
            0,
        )
        self.assertFalse(result["ok"])
        self.assertIn("wood", result["error"])


    # ── state presentation: projects / bank rate / robber feedback ─────

    def test_projects_view_exposes_eligibility_and_progress(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1

        projects = {p["id"]: p for p in engine._get_view(0)["state"]["projects"]}
        self.assertFalse(projects["resource_tycoon"]["eligible"])
        self.assertEqual(projects["resource_tycoon"]["progress"], "0/20")
        self.assertIsNone(projects["resource_tycoon"]["claimed_by"])

        engine.state.players[0].resources.update(
            {"wood": 4, "brick": 4, "sheep": 4, "wheat": 4, "ore": 4})
        projects = {p["id"]: p for p in engine._get_view(0)["state"]["projects"]}
        self.assertTrue(projects["resource_tycoon"]["eligible"])
        self.assertEqual(projects["resource_tycoon"]["progress"], "20/20")

        # Only the viewing player's eligibility is reported.
        self.assertFalse({p["id"]: p for p in engine._get_view(1)["state"]["projects"]}
                         ["resource_tycoon"]["eligible"])

        self.assertTrue(engine.handle_action(
            "claim_project", {"project_id": "resource_tycoon"}, 0)["ok"])
        projects = {p["id"]: p for p in engine._get_view(0)["state"]["projects"]}
        self.assertFalse(projects["resource_tycoon"]["eligible"])
        self.assertEqual(projects["resource_tycoon"]["claimed_by"], 0)

    def test_view_exposes_the_players_own_bank_rate(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        self.assertEqual(engine._get_view(0)["state"]["my_bank_rate"], 4)
        self.assertEqual(engine._get_view(0)["state"]["bank_rate"], 4)

        port_vertex = engine.game_map.ports[0]
        engine.state.players[0].buildings[port_vertex] = 1
        self.assertEqual(engine._get_view(0)["state"]["my_bank_rate"], 3)
        # Another player's rate is unaffected.
        self.assertEqual(engine._get_view(1)["state"]["my_bank_rate"], 4)

    def test_rejected_robber_moves_are_recorded_for_feedback(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        start = engine.state.robber_hex

        for bad, reason in (("", "robber_needs_hex"),
                            ("h99", "invalid_hex"),
                            (start, "robber_same_hex")):
            self.assertFalse(engine.move_robber(0, bad)["ok"])
            event = engine.state.events[-1]
            self.assertEqual(event["type"], "robber_move_rejected")
            self.assertEqual(event["details"]["reason"], reason)
            self.assertEqual(event["details"]["robber_hex"], start)
            self.assertTrue(event["details"]["unchanged"])
            self.assertEqual(engine.state.robber_hex, start)

        target = next(h.id for h in engine.game_map.hexes if h.id != start)
        self.assertTrue(engine.move_robber(0, target)["ok"])
        self.assertEqual(engine.state.robber_hex, target)
        self.assertEqual(engine.state.events[-1]["type"], "robber_moved")


class ResponderWakeAccountingTest(unittest.TestCase):
    """Responder wakes (chat reply / trade response) use the same reverse
    accounting as the scheduler's scheduled wakes."""

    def _engine(self):
        return TradeCraftEngine(seed=42, num_players=3, max_rounds=2)

    def test_responder_wake_refunds_within_budget(self):
        engine = self._engine()
        player = engine.state.players[0]
        before = engine._begin_responder_wake(0)
        self.assertEqual((player.responder_wakes, player.responder_forfeits), (1, 1))
        player.illegal_actions = before + 2  # tolerated trial-and-error
        engine._end_responder_wake(0, before, True)
        self.assertEqual(player.responder_forfeits, 0)

    def test_responder_wake_keeps_forfeit_on_failed_wake(self):
        engine = self._engine()
        player = engine.state.players[0]
        before = engine._begin_responder_wake(0)
        engine._end_responder_wake(0, before, False)  # timeout / no reply / unparsable
        self.assertEqual(player.responder_forfeits, 1)

    def test_responder_wake_keeps_forfeit_over_budget(self):
        engine = self._engine()
        player = engine.state.players[0]
        before = engine._begin_responder_wake(0)
        player.illegal_actions = before + 4  # exceeds max_illegal_per_wake=3
        engine._end_responder_wake(0, before, True)
        self.assertEqual(player.responder_forfeits, 1)

    def test_legality_view_exposes_responder_counters(self):
        engine = self._engine()
        engine._begin_responder_wake(1)
        view = engine._legality_view()
        self.assertEqual(view["responder_wakes"], [0, 1, 0])
        self.assertEqual(view["responder_forfeits"], [0, 1, 0])


if __name__ == "__main__":
    unittest.main()

class WakeQuotaTest(unittest.TestCase):
    """Per-wake quotas: build/claim share one slot; trade and chat cap at 3."""

    def _engine(self, **kwargs):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=6, **kwargs)
        engine.state.phase = "play"
        engine.state.round_num = 1
        for player in engine.state.players:
            player.resources.update(
                {"wood": 9, "brick": 9, "sheep": 9, "wheat": 9, "ore": 9}
            )
        return engine

    def test_second_consuming_action_is_blocked(self):
        engine = self._engine()
        engine.state.players[0].wake_actions_used = 1
        result = engine.handle_action("build", {"type": "road", "edge_id": "e1"}, 0)
        self.assertFalse(result["ok"])
        self.assertIn("already complete", result["error"].lower())

    def test_build_and_claim_share_one_slot(self):
        engine = self._engine()
        engine.state.players[0].wake_actions_used = 1
        for action, params in (
            ("build", {"type": "road", "edge_id": "e1"}),
            ("claim_project", {"project_id": "trade_master"}),
        ):
            result = engine.handle_action(action, params, 0)
            self.assertFalse(result["ok"], (action, result))

    def test_trade_quota_blocks_fourth_proposal(self):
        engine = self._engine()
        engine.mock_trade_decision = "reject"
        params = {"target": 1, "offer": {"sheep": 1}, "request": {"ore": 1}}
        for _ in range(3):
            result = engine.handle_propose_trade(params, 0)
            self.assertTrue(result.get("ok"), result)
        blocked = engine.handle_propose_trade(params, 0)
        self.assertFalse(blocked.get("ok"))
        self.assertIn("trade", blocked["error"].lower())

    def test_chat_quota_blocks_fourth_message(self):
        engine = self._engine()
        engine.state.players[0].wake_chats_used = 3
        result = engine.handle_action("send_message", {"target": 1, "content": "hi"}, 0)
        self.assertFalse(result["ok"])
        self.assertIn("message", result["error"].lower())

    def test_trade_allowed_after_scheduled_action(self):
        engine = self._engine()
        engine.mock_trade_decision = "reject"
        player = engine.state.players[0]
        player.wake_actions_used = 1
        player.acted_this_wake = True
        params = {"target": 1, "offer": {"sheep": 1}, "request": {"ore": 1}}
        result = engine.handle_action("propose_trade", params, 0)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(player.wake_trades_used, 1)

    def test_trade_quota_still_applies_after_scheduled_action(self):
        engine = self._engine()
        engine.mock_trade_decision = "reject"
        player = engine.state.players[0]
        player.wake_actions_used = 1
        player.acted_this_wake = True
        player.wake_trades_used = 3
        params = {"target": 1, "offer": {"sheep": 1}, "request": {"ore": 1}}
        result = engine.handle_action("propose_trade", params, 0)
        self.assertFalse(result["ok"])
        self.assertIn("trade", result["error"].lower())

    def test_advance_resets_all_quotas(self):
        engine = self._engine()
        player = engine.state.players[0]
        player.wake_actions_used = 1
        player.wake_trades_used = 3
        player.wake_chats_used = 3
        engine._advance(0)
        self.assertEqual(
            (player.wake_actions_used, player.wake_trades_used, player.wake_chats_used),
            (0, 0, 0),
        )
