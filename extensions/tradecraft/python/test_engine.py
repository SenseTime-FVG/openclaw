import json
import unittest

from engine import TradeCraftEngine
from map_generator import TOTAL_ROUNDS, generate_map


class TradeCraftEngineTest(unittest.TestCase):
    def test_map_shape_and_seed_are_deterministic(self):
        first = generate_map(42)
        second = generate_map(42)

        self.assertEqual((len(first.hexes), len(first.vertices), len(first.edges)), (19, 54, 72))
        self.assertEqual(first.dice_sequence, second.dice_sequence)
        self.assertEqual(len(first.dice_sequence), TOTAL_ROUNDS * 2)
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

    def test_end_placement_switches_phase_and_resets_round(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=3)
        self.assertEqual(engine.state.phase, "placement")
        result = engine.end_placement()
        self.assertTrue(result["ok"], result)
        self.assertEqual(engine.state.phase, "play")
        self.assertEqual(engine.state.round_num, 0)

    def test_start_round_produces_resources_and_grants_gold_on_no_output(self):
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
        engine.game_map.dice_sequence[:2] = [2, 12]

        result = engine.start_round()

        self.assertEqual(result["dice_rolls"], [2, 12])
        self.assertEqual(engine.state.round_num, 1)
        self.assertEqual(engine.state.current_dice_rolls, [2, 12])
        self.assertEqual(
            engine.state.players[0].resources[productive_tile.resource], 1
        )
        self.assertEqual(engine.state.players[0].resources["gold"], 0)
        self.assertEqual(engine.state.players[1].resources["gold"], 1)
        self.assertEqual(engine.state.players[2].resources["gold"], 1)

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

    def test_settle_blocks_on_pending_trade(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=1)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["stone"] = 1
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertFalse(engine.settle()["ok"])

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
        # Give P0 enough of every resource to trade freely.
        for pid in range(4):
            engine.state.players[pid].resources.update(
                {"wood": 3, "stone": 3, "iron": 3, "crystal": 3, "silk": 3, "gold": 3}
            )
        # Same-team trade (P0 <-> P3): resources move, but trade_master does NOT tick.
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 3},
            0,
        )
        self.assertTrue(engine.resolve_trade("accept", "teammate swap")["ok"])
        self.assertEqual(engine.state.players[0].trades_completed, 0)
        self.assertEqual(engine.state.players[3].trades_completed, 0)
        # Wake boundary: the scheduler advances P0, resetting the per-wake gate.
        engine._advance(0)
        # Cross-team trade (P0 -> P1): trade_master ticks.
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertTrue(engine.resolve_trade("accept", "cross-team")["ok"])
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
        player.resources["crystal"] = 2
        player.resources["iron"] = 1

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
            "send_message", {"content": "Closing round four."}, 2
        )
        self.assertTrue(sent["ok"], sent)
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
                "recipients": [0, 1],
                "content": "Closing round four.",
            },
        }])

    def test_l3_upgrade_reserves_explicit_gold_before_substitution(self):
        cases = (
            ("exact cost", {"crystal": 3, "iron": 2, "gold": 1}, True,
             {"crystal": 0, "iron": 0, "gold": 0}),
            ("insufficient total gold", {"crystal": 2, "iron": 2, "gold": 1}, False,
             {"crystal": 2, "iron": 2, "gold": 1}),
            ("explicit and substitution gold", {"crystal": 2, "iron": 2, "gold": 2}, True,
             {"crystal": 0, "iron": 0, "gold": 0}),
        )

        for label, starting_resources, expected_ok, expected_resources in cases:
            with self.subTest(label):
                engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)
                engine.state.phase = "play"
                engine.state.round_num = 1
                player = engine.state.players[0]
                player.buildings["v4"] = 2
                player.resources.update(starting_resources)

                upgraded = engine.handle_action(
                    "build", {"type": "upgrade", "vertex_id": "v4", "to_level": 3}, 0
                )

                self.assertEqual(upgraded["ok"], expected_ok, upgraded)
                self.assertEqual(player.buildings["v4"], 3 if expected_ok else 2)
                self.assertEqual(
                    {resource: player.resources[resource] for resource in expected_resources},
                    expected_resources,
                )

    def test_get_view_carries_rule_level_step_instruction(self):
        engine = TradeCraftEngine(seed=42, num_players=3, max_rounds=2)

        # Placement guidance is per-player and names the exact pairing step.
        view = engine._get_view(0)
        self.assertIn("instruction", view)
        self.assertIn("Placement for P0", view["instruction"])
        self.assertIn("place a settlement now", view["instruction"])
        # Other players still get guidance (no order/turn gate anymore).
        self.assertIn("Placement for P1", engine._get_view(1)["instruction"])

        engine2 = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine2.state.phase = "play"
        engine2.state.round_num = 1
        engine2.state.players[0].resources["wood"] = 1
        engine2.state.players[1].resources["stone"] = 1
        engine2.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        responder_view = engine2._get_view(1)
        self.assertIn("responder to the pending trade", responder_view["instruction"])
        proposer_view = engine2._get_view(0)
        self.assertIn("Trade pending", proposer_view["instruction"])

    def test_trade_acceptance_and_resource_validation(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 2
        engine.state.players[1].resources["stone"] = 2

        invalid = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": -1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertFalse(invalid["ok"])

        proposal = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertTrue(proposal["ok"], proposal)
        self.assertIsNotNone(engine.state.pending_trade)

        acceptance = engine.resolve_trade("accept", "Accepted; stone is useful.")
        self.assertTrue(acceptance["ok"], acceptance)
        self.assertEqual(engine.state.players[0].resources["stone"], 1)
        self.assertEqual(engine.state.players[1].resources["wood"], 1)
        self.assertEqual(acceptance["result"]["response_message"], {
            "id": 1,
            "from": 1,
            "recipients": [0],
            "content": "Accepted; stone is useful.",
            "round": 1,
        })

    def test_trade_response_reject_clears_pending_and_delivers_feedback(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["stone"] = 1
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        rejection = engine.resolve_trade("reject", "I need my stone.")
        self.assertTrue(rejection["ok"], rejection)
        self.assertIsNone(engine.state.pending_trade)
        self.assertEqual(rejection["result"]["response_message"]["recipients"], [0])

    def test_resolve_trade_rejects_unknown_decision(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["stone"] = 1
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        bad = engine.resolve_trade("counter", "counter unsupported")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"],
                         "Unknown trade decision: 'counter'. Use accept or reject.")

    def test_message_is_broadcast_without_consuming_action(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1

        sent = engine.handle_action("send_message", {"content": "Trade wood?"}, 0)
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(sent["result"]["message_sent"], {
            "id": 1,
            "from": 0,
            "recipients": [1, 2],
            "content": "Trade wood?",
            "round": 1,
        })

    def test_second_action_same_wake_is_rejected(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        # First action succeeds and marks the wake as acted.
        sent = engine.handle_action("send_message", {"content": "hello"}, 0)
        self.assertTrue(sent["ok"])
        self.assertTrue(engine.state.players[0].acted_this_wake)
        # A second tool call in the same wake is rejected as an over-action.
        extra = engine.handle_action("send_message", {"content": "again"}, 0)
        self.assertFalse(extra["ok"])
        self.assertIn("complete", extra["error"].lower())
        # The rejected over-action is counted as illegal.
        self.assertEqual(engine.state.players[0].illegal_actions, 1)
        # view_game_state is still allowed without consuming the wake flag.
        view = engine.handle_action("view_game_state", {}, 0)
        self.assertTrue(view["ok"])
        # advance resets the flag so the player can act on the next wake.
        engine._advance(0)
        self.assertFalse(engine.state.players[0].acted_this_wake)
        sent2 = engine.handle_action("send_message", {"content": "next wake"}, 0)
        self.assertTrue(sent2["ok"])

    def test_forfeit_records_and_rejects_trade_on_responder(self):
        engine = TradeCraftEngine(seed=7, num_players=3, max_rounds=2)
        engine.state.phase = "play"
        engine.state.round_num = 1
        engine.forfeit_player(0)
        self.assertEqual(engine.state.players[0].forfeits, 1)

        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["stone"] = 1
        engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        result = engine.forfeit_player(1)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(engine.state.pending_trade)
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

    def test_final_resource_score_is_one_point_per_four_resources(self):
        engine = TradeCraftEngine(seed=5, num_players=3, max_rounds=2)
        for player, resources in zip(engine.state.players, (3, 4, 8)):
            player.resources["wood"] = resources
        self.assertEqual(engine._calculate_scores(), [0, 1, 2])

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


if __name__ == "__main__":
    unittest.main()