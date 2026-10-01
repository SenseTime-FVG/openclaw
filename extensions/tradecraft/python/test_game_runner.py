import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from game_runner import (
    AgentFailureKind,
    GameRunner,
    _classify_agent_failure,
    _gameplay_progress_key,
    _progress_key,
    load_settings,
    prepare_profiles,
)


class GameRunnerTest(unittest.TestCase):
    def _settings(self, root: Path, mode="mock", players=3):
        config_path = root / "runner.json"
        config_path.write_text(json.dumps({
            "mode": mode,
            "players": players,
            "seed": 11,
            "runtimeRoot": str(root / "runtime"),
            "openclaw": {
                "basePort": 28789,
                "portStep": 20,
                "model": "",
                "apiKeyEnv": "",
            },
        }), encoding="utf-8")
        return load_settings(config_path)

    @staticmethod
    def _play_state(advanced=False):
        players = (
            [{"buildings": ["v0"], "roads": [], "resources": {"wood": 1}},
             {"buildings": [], "roads": [], "resources": {}},
             {"buildings": [], "roads": [], "resources": {}}]
            if advanced else
            [{"buildings": [], "roads": [], "resources": {}},
             {"buildings": [], "roads": [], "resources": {}},
             {"buildings": [], "roads": [], "resources": {}}]
        )
        return {
            "phase": "play",
            "round": 1,
            "max_rounds": 12,
            "pending_trade": None,
            "game_over": False,
            "players": players,
        }

    def test_prepare_profiles_isolates_players_without_llm_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._settings(Path(temporary))
            players = prepare_profiles(settings)

            self.assertEqual([player.port for player in players], [28789, 28809, 28829])
            self.assertEqual(len({player.gateway_token for player in players}), 3)
            for player in players:
                config = json.loads(player.config_path.read_text(encoding="utf-8"))
                plugin = config["plugins"]["entries"]["tradecraft"]["config"]
                self.assertEqual(plugin["playerId"], player.player_id)
                self.assertEqual(plugin["socketPath"], str(settings.socket_path))
                self.assertNotIn(
                    "tradecraft_pass",
                    config["agents"]["list"][0]["tools"]["allow"],
                )
                self.assertEqual(config["gateway"]["auth"], {
                    "mode": "token",
                    "token": player.gateway_token,
                })
                self.assertEqual(player.config_path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("model", config["agents"]["defaults"])
                instructions = (player.workspace / "AGENTS.md").read_text(encoding="utf-8")
                self.assertIn(f"P{player.player_id}", instructions)
                self.assertNotIn("strategy style", instructions)
                self.assertIn("A pending trade automatically wakes its responder", instructions)
                self.assertIn(
                    "after a successful `tradecraft_propose_trade`, stop the current invocation",
                    instructions,
                )
                self.assertIn("Respond exactly once and stop", instructions)
                self.assertIn("single JSON decision", instructions)
                self.assertIn("The injected events", instructions)
                self.assertIn("`tradecraft_send_message` is a normal negotiation action", instructions)
                self.assertIn("end your reply without calling any tool", instructions)
                tools = (player.workspace / "TOOLS.md").read_text(encoding="utf-8")
                self.assertIn("four `tradecraft_*` tools", tools)

    def test_seed_override_wins_over_configured_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self._settings(root)
            profile_root = root / "isolated-profile"
            overridden = load_settings(
                root / "runner.json",
                seed_override=97,
                base_port_override=30789,
                profile_root_override=profile_root,
                max_rounds_override=20,
            )

            self.assertEqual(settings.seed, 11)
            self.assertEqual(overridden.seed, 97)
            self.assertEqual(overridden.base_port, 30789)
            self.assertEqual(overridden.profile_root, profile_root.resolve())
            self.assertEqual(overridden.max_rounds, 20)

    def test_prepare_profiles_assigns_and_isolates_per_player_api_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            key_envs = (
                "TEST_TRADECRAFT_P0_KEY",
                "TEST_TRADECRAFT_P1_KEY",
                "TEST_TRADECRAFT_P2_KEY",
            )
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
                api_key_envs=key_envs,
                config_overlay={
                    "models": {
                        "providers": {
                            "routeapi": {
                                "api": "openai-completions",
                                "baseUrl": "https://example.invalid/v1",
                                "models": [{"id": "test-model"}],
                            },
                        },
                    },
                },
            )
            runner = GameRunner(settings)

            for player, key_env in zip(runner.players, key_envs):
                config = json.loads(player.config_path.read_text(encoding="utf-8"))
                provider = config["models"]["providers"]["routeapi"]
                self.assertEqual(provider["apiKey"], f"${{{key_env}}}")

            fake_keys = {key_env: f"secret-{index}" for index, key_env in enumerate(key_envs)}
            with patch.dict("os.environ", fake_keys):
                player_env = runner._player_env(runner.players[0])
            self.assertEqual(player_env[key_envs[0]], fake_keys[key_envs[0]])
            self.assertNotIn(key_envs[1], player_env)
            self.assertNotIn(key_envs[2], player_env)

    def test_progress_key_tracks_board_changes(self):
        state = self._play_state()
        before = _progress_key(state)
        state["players"] = [
            {"buildings": ["v0"], "roads": [], "resources": {"wood": 1}},
            {"buildings": [], "roads": [], "resources": {}},
            {"buildings": [], "roads": [], "resources": {}},
        ]
        self.assertNotEqual(before, _progress_key(state))

    def test_agent_failure_classification_separates_retryable_and_fatal_errors(self):
        cases = [
            ("FailoverError: API rate limit reached", AgentFailureKind.RATE_LIMIT),
            ("HTTP 503 Service Unavailable", AgentFailureKind.SERVER_ERROR),
            ("fetch failed: ECONNRESET", AgentFailureKind.TRANSPORT),
            ("transcript compaction failed: terminated", AgentFailureKind.TRANSPORT),
            ("agent subprocess timeout", AgentFailureKind.TIMEOUT),
            ("invalid API key", AgentFailureKind.AUTHENTICATION),
            ("Thinking level low is not supported", AgentFailureKind.CONFIGURATION),
        ]
        for detail, expected in cases:
            with self.subTest(detail=detail):
                record = {
                    "error": detail if expected == AgentFailureKind.TIMEOUT else "",
                    "stderr": detail,
                    "stdout": "",
                    "returncode": 1,
                }
                self.assertEqual(_classify_agent_failure(record), expected)

    def test_retryable_agent_failure_uses_backoff_then_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
                agent_retry_attempts=2,
                agent_retry_base_seconds=4,
                agent_retry_max_seconds=30,
            )
            runner = GameRunner(settings)
            settings.match_dir.mkdir(parents=True)
            failed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="GatewayClientRequestError: API rate limit reached",
            )
            succeeded = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="{}", stderr=""
            )

            with (
                patch.object(runner, "_openclaw_command", side_effect=lambda *args: ["node", *args]),
                patch.object(runner, "_read_state", return_value=self._play_state()),
                patch("game_runner.subprocess.run", side_effect=[failed, succeeded]) as run,
                patch("game_runner.random.uniform", return_value=3.0),
                patch("game_runner.time.sleep") as sleep,
            ):
                attempts = runner._invoke_openclaw_agent(0)

            self.assertEqual(attempts, 2)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(3.0)
            command = run.call_args_list[0].args[0]
            wake_message = (settings.match_dir / "wake-p0.txt").read_text(encoding="utf-8")
            self.assertIn("Read the game state and any step instruction", wake_message)
            self.assertIn("a successful trade proposal", wake_message)
            self.assertIn("stop immediately", wake_message)
            self.assertIn("tradecraft_send_message is available for concrete negotiation", wake_message)
            self.assertIn("do not call any tool — simply end your reply to end the turn", wake_message)
            records = [
                json.loads(line)
                for line in (settings.match_dir / "agent-p0.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[0]["failure_class"], "rate_limit")
            self.assertTrue(records[0]["retryable"])
            self.assertEqual(records[1]["returncode"], 0)

    def test_retry_delay_is_exponential_and_capped(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                agent_retry_base_seconds=5,
                agent_retry_max_seconds=60,
            )
            runner = GameRunner(settings)
            with patch("game_runner.random.uniform", side_effect=lambda _low, high: high):
                delays = [runner._agent_retry_delay(number) for number in range(1, 7)]
            self.assertEqual(delays, [5, 10, 20, 40, 60, 60])

    def test_rate_limit_fallback_waits_beyond_regular_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
                agent_retry_attempts=0,
                rate_limit_retry_seconds=120,
                rate_limit_max_wait_seconds=300,
            )
            runner = GameRunner(settings)
            settings.match_dir.mkdir(parents=True)
            failed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="API rate limit reached"
            )
            succeeded = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="{}", stderr=""
            )

            with (
                patch.object(runner, "_openclaw_command", side_effect=lambda *args: ["node", *args]),
                patch.object(runner, "_read_state", return_value=self._play_state()),
                patch("game_runner.subprocess.run", side_effect=[failed, succeeded]) as run,
                patch("game_runner.time.monotonic", return_value=100.0),
                patch("game_runner.random.uniform", return_value=120.0),
                patch("game_runner.time.sleep") as sleep,
            ):
                attempts = runner._invoke_openclaw_agent(0)

            self.assertEqual(attempts, 2)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(120.0)
            events = [
                json.loads(line)
                for line in settings.runner_event_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["type"] for event in events],
                ["rate_limit_fallback_started", "rate_limit_wait_scheduled"],
            )

    def test_rate_limit_fallback_stops_after_wait_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
                agent_retry_attempts=0,
                agent_retry_base_seconds=5,
                rate_limit_retry_seconds=120,
                rate_limit_max_wait_seconds=60,
            )
            runner = GameRunner(settings)
            settings.match_dir.mkdir(parents=True)
            failed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="API rate limit reached"
            )

            with (
                patch.object(runner, "_openclaw_command", side_effect=lambda *args: ["node", *args]),
                patch.object(runner, "_read_state", return_value=self._play_state()),
                patch("game_runner.subprocess.run", return_value=failed) as run,
                patch("game_runner.time.monotonic", side_effect=[100.0, 161.0]),
                patch("game_runner.random.uniform", return_value=120.0),
                patch("game_runner.time.sleep") as sleep,
            ):
                with self.assertRaisesRegex(RuntimeError, "exhausted 2 attempts"):
                    runner._invoke_openclaw_agent(0)

            self.assertEqual(run.call_count, 2)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [60.0, 5])

    def test_configuration_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
            )
            runner = GameRunner(settings)
            settings.match_dir.mkdir(parents=True)
            failed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Thinking level low is not supported for this model",
            )

            with (
                patch.object(runner, "_openclaw_command", side_effect=lambda *args: ["node", *args]),
                patch.object(runner, "_read_state", return_value=self._play_state()),
                patch("game_runner.subprocess.run", return_value=failed) as run,
                patch("game_runner.time.sleep") as sleep,
            ):
                with self.assertRaisesRegex(RuntimeError, "configuration"):
                    runner._invoke_openclaw_agent(0)

            self.assertEqual(run.call_count, 1)
            sleep.assert_not_called()

    def test_timeout_does_not_retry_when_state_advances_during_backoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="routeapi/test-model",
                agent_retry_attempts=2,
                agent_retry_base_seconds=2,
                agent_retry_max_seconds=10,
            )
            runner = GameRunner(settings)
            settings.match_dir.mkdir(parents=True)
            baseline = self._play_state()
            advanced = self._play_state(advanced=True)

            with (
                patch.object(runner, "_openclaw_command", side_effect=lambda *args: ["node", *args]),
                patch.object(
                    runner,
                    "_read_state",
                    side_effect=[baseline, baseline, advanced],
                ),
                patch(
                    "game_runner.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="openclaw agent", timeout=630),
                ) as run,
                patch("game_runner.random.uniform", return_value=1.5),
                patch("game_runner.time.sleep") as sleep,
            ):
                attempts = runner._invoke_openclaw_agent(0)

            self.assertEqual(attempts, 1)
            self.assertEqual(run.call_count, 1)
            sleep.assert_called_once_with(1.5)
            events = [
                json.loads(line)
                for line in settings.runner_event_path.read_text(encoding="utf-8").splitlines()
            ]
            recovered = [
                event for event in events
                if event["type"] == "agent_failure_after_progress"
            ]
            self.assertEqual(recovered[0]["review_stage"], "after_backoff")
            self.assertEqual(recovered[0]["failure_class"], "timeout")

    def test_disconnect_game_over_is_not_mistaken_for_action_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = GameRunner(self._settings(Path(temporary)))
            baseline = self._play_state()
            disconnected = {**baseline, "game_over": True}
            with patch.object(runner, "_read_state", return_value=disconnected):
                recovered = runner._recover_if_game_progressed(
                    _gameplay_progress_key(baseline),
                    player_id=0,
                    attempt=1,
                    failure_kind=AgentFailureKind.TRANSPORT,
                    review_stage="immediate",
                )
            self.assertFalse(recovered)

    def test_messages_are_injected_once_into_every_other_player_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary), players=4),
                mode="openclaw",
            )
            runner = GameRunner(settings)
            runner.session_keys = {
                player_id: f"agent:main:tradecraft-test-p{player_id}"
                for player_id in range(4)
            }
            state = {"messages": [{
                "id": 7,
                "from": 0,
                "recipients": [1, 2, 3],
                "content": "Trade wood for stone?",
                "round": 2,
            }]}

            with patch.object(
                runner,
                "_gateway_call",
                return_value={"ok": True, "messageId": "gateway-message"},
            ) as gateway_call:
                runner._deliver_pending_messages(state)
                runner._deliver_pending_messages(state)

            self.assertEqual(gateway_call.call_count, 3)
            recipients = [call.args[0].player_id for call in gateway_call.call_args_list]
            self.assertEqual(recipients, [1, 2, 3])
            for call in gateway_call.call_args_list:
                self.assertEqual(call.args[1], "chat.inject")
                self.assertEqual(call.args[2]["message"], "Trade wood for stone?")
                self.assertEqual(
                    call.args[2]["label"],
                    "TradeCraft message #7 from P0",
                )
            self.assertEqual(runner.delivered_messages, {(7, 1), (7, 2), (7, 3)})

    def test_openclaw_match_rejects_model_without_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                self._settings(Path(temporary)),
                mode="openclaw",
                model="glm-5.2",
            )

            with self.assertRaisesRegex(RuntimeError, "provider/model"):
                GameRunner(settings).run()

            self.assertFalse(settings.match_dir.exists())

    def test_mock_match_reaches_game_over(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._settings(Path(temporary))
            final_state = GameRunner(settings).run()

            self.assertTrue(final_state["game_over"])
            self.assertEqual(final_state["round"], 12)
            self.assertEqual(final_state["max_rounds"], 12)
            self.assertEqual(len(final_state["dice_rolls"]), 2)
            self.assertEqual(len(final_state["scores"]), 3)
            self.assertTrue(settings.engine_event_path.exists())
            self.assertTrue(settings.runner_event_path.exists())

    def test_four_player_mock_match_reaches_game_over(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._settings(Path(temporary), players=4)
            final_state = GameRunner(settings).run()

            self.assertTrue(final_state["game_over"])
            self.assertEqual(len(final_state["scores"]), 4)


if __name__ == "__main__":
    unittest.main()
