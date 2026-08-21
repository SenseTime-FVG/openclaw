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
        self.assertTrue(all(2 <= roll <= 12 for roll in first.dice_sequence))

        cubes = {tile.cube for tile in first.hexes}
        self.assertIn((0, 0, 0), cubes)
        self.assertTrue(all(tuple(-axis for axis in cube) in cubes for cube in cubes))

    def test_three_player_placement_reaches_play_phase(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        for player_id in (0, 1, 2, 2, 1, 0):
            state = engine.handle_action("view_game_state", {}, player_id)["result"]
            self.assertEqual(state["current_player"], player_id)
            vertex_id = next(iter(state["expansion_targets"]["settlements"]))
            settlement = engine.handle_action(
                "build", {"type": "settlement", "vertex_id": vertex_id}, player_id
            )
            self.assertTrue(settlement["ok"], settlement)

            state = settlement["result"]["state"]
            self.assertNotIn("map", state)
            self.assertTrue(state["placement_done"])
            edge_id = next(iter(state["expansion_targets"]["roads"]))
            road = engine.handle_action(
                "build", {"type": "road", "edge_id": edge_id}, player_id
            )
            self.assertTrue(road["ok"], road)
            self.assertNotIn("map", road["result"]["state"])
            self.assertFalse(road["result"]["state"]["placement_done"])

        final_state = engine.handle_action("view_game_state", {}, 0)["result"]
        self.assertEqual(final_state["phase"], "play")
        self.assertEqual(final_state["current_player"], 0)
        self.assertGreaterEqual(final_state["dice_roll"], 2)

    def test_game_state_uses_compact_map_and_enriched_topology_targets(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        state = engine.handle_action("view_game_state", {}, 0)["result"]

        self.assertEqual(
            (len(state["map"]["hexes"]),
             len(state["map"]["vertices"]),
             len(state["map"]["edges"])),
            (19, 54, 72),
        )
        self.assertEqual(state["map"]["hexes"]["h0"], ["silk", 9])
        self.assertEqual(state["map"]["vertices"]["v4"], ["h0", "h3", "h4"])
        self.assertEqual(state["map"]["edges"]["e20"], ["v17", "v4"])
        self.assertNotIn("available_vertices", state)
        self.assertNotIn("available_edges", state)

        target = state["expansion_targets"]["settlements"]["v4"]
        self.assertEqual(target, [["silk", 9], ["crystal", 6], ["stone", 11]])
        compact_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(compact_json), 7000)

    def test_expansion_targets_are_not_filtered_by_affordability(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        engine.state.phase = "play"
        engine.state.players[0].buildings["v0"] = 1

        state = engine.handle_action("view_game_state", {}, 0)["result"]

        self.assertEqual(sum(engine.state.players[0].resources.values()), 0)
        self.assertEqual(
            set(state["expansion_targets"]["roads"]),
            {"e0", "e5", "e7"},
        )

    def test_action_snapshot_is_attached_after_action_consumption(self):
        engine = TradeCraftEngine(seed=42, num_players=3)
        engine.state.phase = "play"
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
        self.assertEqual(state["actions_used"], 1)
        building = next(
            item for item in state["players"][0]["buildings"]
            if item["vertex_id"] == "v4"
        )
        self.assertEqual(building["level"], 2)

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
                engine = TradeCraftEngine(seed=42, num_players=3)
                engine.state.phase = "play"
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
                self.assertEqual(player.actions_used, 1 if expected_ok else 0)

    def test_trade_acceptance_and_resource_validation(self):
        engine = TradeCraftEngine(seed=7, num_players=3)
        engine.state.phase = "play"
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

        acceptance = engine.handle_action(
            "respond_trade",
            {"action": "accept", "message": "Accepted; stone is useful to me."},
            1,
        )
        self.assertTrue(acceptance["ok"], acceptance)
        self.assertEqual(engine.state.players[0].resources["stone"], 1)
        self.assertEqual(engine.state.players[1].resources["wood"], 1)
        self.assertEqual(acceptance["result"]["response_message"], {
            "id": 1,
            "from": 1,
            "recipients": [0],
            "content": "Accepted; stone is useful to me.",
            "round": 1,
        })
        self.assertEqual(engine.state.players[1].actions_used, 0)

    def test_trade_response_requires_feedback_for_the_other_party(self):
        engine = TradeCraftEngine(seed=7, num_players=3)
        engine.state.phase = "play"
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[1].resources["stone"] = 1
        proposal = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertTrue(proposal["ok"], proposal)

        missing_feedback = engine.handle_action(
            "respond_trade", {"action": "reject"}, 1
        )
        self.assertFalse(missing_feedback["ok"])
        self.assertEqual(missing_feedback["error"], "Trade response message required.")
        self.assertIsNotNone(engine.state.pending_trade)

        rejection = engine.handle_action(
            "respond_trade",
            {"action": "reject", "message": "I need my stone for an upgrade."},
            1,
        )
        self.assertTrue(rejection["ok"], rejection)
        self.assertIsNone(engine.state.pending_trade)
        self.assertEqual(rejection["result"]["response_message"]["recipients"], [0])
        self.assertEqual(
            rejection["result"]["response_message"]["content"],
            "I need my stone for an upgrade.",
        )
        self.assertEqual(engine.state.players[1].actions_used, 0)

    def test_counteroffer_returns_feedback_and_swaps_trade_roles(self):
        engine = TradeCraftEngine(seed=7, num_players=3)
        engine.state.phase = "play"
        engine.state.players[0].resources["wood"] = 1
        engine.state.players[0].resources["iron"] = 1
        engine.state.players[1].resources["stone"] = 1
        engine.state.players[1].resources["silk"] = 1
        proposal = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 1},
            0,
        )
        self.assertTrue(proposal["ok"], proposal)

        counter = engine.handle_action(
            "respond_trade",
            {
                "action": "counter",
                "offer": {"silk": 1},
                "request": {"iron": 1},
                "message": "I will trade silk, but I need iron instead.",
            },
            1,
        )

        self.assertTrue(counter["ok"], counter)
        pending = counter["result"]["state"]["pending_trade"]
        self.assertEqual((pending["proposer"], pending["responder"]), (1, 0))
        self.assertEqual(counter["result"]["response_message"]["recipients"], [0])
        self.assertEqual(engine.state.players[1].actions_used, 0)

    def test_last_player_trade_waits_for_response_before_round_advance(self):
        for player_count, last_player in ((3, 2), (4, 3)):
            for response_action in ("accept", "reject"):
                with self.subTest(players=player_count, response=response_action):
                    engine = TradeCraftEngine(seed=7, num_players=player_count)
                    engine.state.phase = "play"
                    engine.state.current_player = last_player
                    engine.state.players[last_player].actions_used = 1
                    engine.state.players[last_player].resources["wood"] = 1
                    engine.state.players[0].resources["stone"] = 1

                    proposal = engine.handle_action(
                        "propose_trade",
                        {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 0},
                        last_player,
                    )

                    self.assertTrue(proposal["ok"], proposal)
                    proposed_state = proposal["result"]["state"]
                    self.assertEqual(proposed_state["round"], 1)
                    self.assertEqual(proposed_state["current_player"], last_player)
                    self.assertEqual(proposed_state["current_actions_used"], 2)
                    self.assertIsNotNone(proposed_state["pending_trade"])
                    blocked = engine.handle_action(
                        "send_message", {"content": "A third action must not run."}, last_player
                    )
                    self.assertFalse(blocked["ok"], blocked)
                    self.assertEqual(engine.state.players[last_player].actions_used, 2)

                    response = engine.handle_action(
                        "respond_trade",
                        {
                            "action": response_action,
                            "message": f"Mock {response_action} feedback.",
                        },
                        0,
                    )

                    self.assertTrue(response["ok"], response)
                    resolved_state = response["result"]["state"]
                    self.assertEqual(resolved_state["round"], 2)
                    self.assertEqual(resolved_state["current_player"], 0)
                    self.assertIsNone(resolved_state["pending_trade"])
                    expected_stone = 0 if response_action == "accept" else 1
                    self.assertEqual(
                        engine.state.players[0].resources["stone"], expected_stone
                    )

    def test_final_round_trade_resolves_before_game_over(self):
        engine = TradeCraftEngine(seed=7, num_players=3)
        engine.state.phase = "play"
        engine.state.round_num = TOTAL_ROUNDS
        engine.state.current_player = 2
        engine.state.players[2].actions_used = 1
        engine.state.players[2].resources["wood"] = 1
        engine.state.players[0].resources["stone"] = 1

        proposal = engine.handle_action(
            "propose_trade",
            {"offer": {"wood": 1}, "request": {"stone": 1}, "target": 0},
            2,
        )

        self.assertTrue(proposal["ok"], proposal)
        self.assertFalse(proposal["result"]["state"]["game_over"])
        acceptance = engine.handle_action(
            "respond_trade",
            {"action": "accept", "message": "Accepted for the requested stone."},
            0,
        )
        self.assertTrue(acceptance["ok"], acceptance)
        self.assertTrue(acceptance["result"]["state"]["game_over"])
        self.assertEqual(engine.state.players[0].resources["stone"], 0)
        self.assertEqual(engine.state.players[2].resources["stone"], 1)

    def test_message_is_broadcast_and_consumes_one_action(self):
        engine = TradeCraftEngine(seed=5, num_players=3)
        engine.state.phase = "play"

        rejected = engine.handle_action("send_message", {"content": "   "}, 0)
        self.assertFalse(rejected["ok"])
        self.assertEqual(engine.state.players[0].actions_used, 0)

        sent = engine.handle_action("send_message", {"content": "Trade wood for stone?"}, 0)
        self.assertTrue(sent["ok"], sent)
        self.assertEqual(engine.state.players[0].actions_used, 1)
        self.assertEqual(sent["result"]["message_sent"], {
            "id": 1,
            "from": 0,
            "recipients": [1, 2],
            "content": "Trade wood for stone?",
            "round": 1,
        })
        self.assertEqual(sent["result"]["state"]["messages"], [
            sent["result"]["message_sent"]
        ])
        self.assertNotIn("map", sent["result"]["state"])

    def test_buildings_and_roads_are_globally_exclusive(self):
        engine = TradeCraftEngine(seed=3, num_players=3)
        state = engine.handle_action("view_game_state", {}, 0)["result"]
        vertex_id = next(iter(state["expansion_targets"]["settlements"]))
        placed = engine.handle_action(
            "build", {"type": "settlement", "vertex_id": vertex_id}, 0
        )
        self.assertTrue(placed["ok"], placed)
        edge_id = next(iter(placed["result"]["state"]["expansion_targets"]["roads"]))
        road = engine.handle_action("build", {"type": "road", "edge_id": edge_id}, 0)
        self.assertTrue(road["ok"], road)

        duplicate = engine.handle_action(
            "build", {"type": "settlement", "vertex_id": vertex_id}, 1
        )
        self.assertFalse(duplicate["ok"])
        self.assertIn("already exists", duplicate["error"])


if __name__ == "__main__":
    unittest.main()
