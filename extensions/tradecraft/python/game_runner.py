#!/usr/bin/env python3
"""Run a complete TradeCraft match with mock or OpenClaw-backed players."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


TRADECRAFT_TOOLS = [
    "tradecraft_view_game_state",
    "tradecraft_build",
    "tradecraft_propose_trade",
    "tradecraft_respond_trade",
    "tradecraft_send_message",
    "tradecraft_claim_project",
]


class AgentFailureKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    TRANSPORT = "transport"
    SERVER_ERROR = "server_error"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


RETRYABLE_AGENT_FAILURES = {
    AgentFailureKind.TIMEOUT,
    AgentFailureKind.RATE_LIMIT,
    AgentFailureKind.OVERLOADED,
    AgentFailureKind.TRANSPORT,
    AgentFailureKind.SERVER_ERROR,
}


@dataclass(frozen=True)
class PlayerRuntime:
    player_id: int
    port: int
    gateway_token: str
    state_dir: Path
    config_path: Path
    workspace: Path


@dataclass(frozen=True)
class RunnerSettings:
    mode: str
    players: int
    seed: int
    game_id: str
    repo_root: Path
    extension_root: Path
    runtime_root: Path
    model: str
    api_key_env: str
    api_key_envs: tuple[str, ...]
    agent_id: str
    thinking: str
    agent_timeout_seconds: int
    agent_retry_attempts: int
    agent_retry_base_seconds: float
    agent_retry_max_seconds: float
    rate_limit_retry_seconds: float
    rate_limit_max_wait_seconds: float
    gateway_startup_seconds: int
    max_agent_calls_without_progress: int
    max_agent_calls: int
    base_port: int
    port_step: int
    config_overlay: dict

    def api_key_env_for_player(self, player_id: int) -> str:
        if self.api_key_envs:
            return self.api_key_envs[player_id]
        return self.api_key_env

    @property
    def match_dir(self) -> Path:
        return self.runtime_root / "matches" / self.game_id

    @property
    def state_path(self) -> Path:
        return self.match_dir / "state.json"

    @property
    def engine_event_path(self) -> Path:
        return self.match_dir / "engine-events.jsonl"

    @property
    def runner_event_path(self) -> Path:
        return self.match_dir / "runner-events.jsonl"

    @property
    def socket_path(self) -> Path:
        digest = hashlib.sha256(str(self.match_dir).encode("utf-8")).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"tradecraft-{digest}.sock"

    @property
    def session_key(self) -> str:
        return f"agent:{self.agent_id}:tradecraft-{self.game_id}"


class ManagedProcess:
    def __init__(self, name: str, process: subprocess.Popen, log_handle):
        self.name = name
        self.process = process
        self.log_handle = log_handle

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_handle.close()


class JsonLineClient:
    def __init__(self, socket_path: Path, player_id: int):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(20)
        self.socket.connect(str(socket_path))
        self.reader = self.socket.makefile("r", encoding="utf-8")
        registration = self.request("register_player", {"player_id": player_id})
        if not registration.get("ok"):
            raise RuntimeError(f"Player {player_id} registration failed: {registration}")

    def request(self, action: str, params: dict) -> dict:
        message = json.dumps({"action": action, "params": params}) + "\n"
        self.socket.sendall(message.encode("utf-8"))
        line = self.reader.readline()
        if not line:
            raise RuntimeError("TradeCraft engine closed the socket")
        return json.loads(line)

    def close(self):
        try:
            self.reader.close()
        finally:
            self.socket.close()


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _output_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _agent_failure_detail(record: dict) -> str:
    return (
        str(record.get("error", "")).strip()
        or str(record.get("stderr", "")).strip()
        or str(record.get("stdout", "")).strip()
        or "unknown agent error"
    )


def _classify_agent_failure(record: dict) -> AgentFailureKind:
    """Classify runner-visible failures after OpenClaw's own provider handling."""
    text = "\n".join(
        str(record.get(key, "")) for key in ("error", "stderr", "stdout")
    ).lower()

    # Permanent failures take precedence over broad strings such as "rate limit"
    # so retries do not burn time on credentials, billing, or invalid config.
    if any(marker in text for marker in (
        "invalid api key", "incorrect api key", "authentication failed",
        "unauthorized", "forbidden", "status code 401", "status code 403",
        "http 401", "http 403",
    )):
        return AgentFailureKind.AUTHENTICATION
    if any(marker in text for marker in (
        "insufficient_quota", "quota exceeded", "billing", "credit balance",
        "weekly rate limit", "monthly rate limit",
    )):
        return AgentFailureKind.QUOTA
    if any(marker in text for marker in (
        "thinking level", "is not supported", "unknown model", "model not found",
        "invalid request", "bad request", "context length", "maximum context",
        "status code 400", "status code 404", "http 400", "http 404",
    )):
        return AgentFailureKind.CONFIGURATION

    if record.get("error") == "agent subprocess timeout" or any(
        marker in text for marker in (
            "timed out", "timeout", "deadline exceeded", "etimedout",
            "status code 408", "http 408",
        )
    ):
        return AgentFailureKind.TIMEOUT
    if any(marker in text for marker in (
        "rate limit", "too many requests", "status code 429", "http 429",
    )):
        return AgentFailureKind.RATE_LIMIT
    if any(marker in text for marker in (
        "overloaded", "over capacity", "server busy", "temporarily busy",
    )):
        return AgentFailureKind.OVERLOADED
    if re.search(r"(?<!\d)(?:500|502|503|504)(?!\d)", text) or any(
        marker in text for marker in (
            "internal server error", "bad gateway", "service unavailable",
            "gateway timeout",
        )
    ):
        return AgentFailureKind.SERVER_ERROR
    if any(marker in text for marker in (
        "econnreset", "econnrefused", "enotfound", "eai_again", "socket hang up",
        "fetch failed", "network error", "connection reset", "connection closed",
        "connection aborted", "remote disconnected", "broken pipe",
        "incomplete stream", "premature close", "terminated",
    )) or (
        isinstance(record.get("returncode"), int) and record["returncode"] < 0
    ):
        return AgentFailureKind.TRANSPORT
    return AgentFailureKind.UNKNOWN


def load_settings(config_path: Path, mode_override: Optional[str] = None,
                  game_id_override: Optional[str] = None,
                  seed_override: Optional[int] = None) -> RunnerSettings:
    config_path = config_path.resolve()
    config = _read_json(config_path)
    repo_root = Path(__file__).resolve().parents[3]
    extension_root = Path(__file__).resolve().parents[1]
    mode = mode_override or config.get("mode", "mock")
    if mode not in ("mock", "openclaw"):
        raise ValueError("mode must be 'mock' or 'openclaw'")

    players = config.get("players", 3)
    if players not in (3, 4):
        raise ValueError("players must be 3 or 4")
    openclaw = config.get("openclaw", {})
    runtime_root = _resolve_path(
        config.get("runtimeRoot", str(repo_root / "runs" / "tradecraft")),
        config_path.parent,
    )
    game_id = game_id_override or config.get("gameId")
    if not game_id:
        game_id = f"{time.strftime('game-%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    game_id = str(game_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", game_id):
        raise ValueError("gameId must contain only letters, numbers, dot, underscore, or dash")

    api_key_env = str(openclaw.get("apiKeyEnv", "")).strip()
    if api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
        raise ValueError("openclaw.apiKeyEnv must be a valid environment variable name")
    raw_api_key_envs = openclaw.get("apiKeyEnvs", [])
    if not isinstance(raw_api_key_envs, list):
        raise ValueError("openclaw.apiKeyEnvs must be an array")
    api_key_envs = tuple(str(value).strip() for value in raw_api_key_envs)
    if api_key_env and api_key_envs:
        raise ValueError("set only one of openclaw.apiKeyEnv or openclaw.apiKeyEnvs")
    if api_key_envs and len(api_key_envs) != players:
        raise ValueError("openclaw.apiKeyEnvs must contain one entry per player")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
           for value in api_key_envs):
        raise ValueError(
            "every openclaw.apiKeyEnvs entry must be a valid environment variable name"
        )
    if len(set(api_key_envs)) != len(api_key_envs):
        raise ValueError("openclaw.apiKeyEnvs entries must be unique")
    base_port = int(openclaw.get("basePort", 18789))
    port_step = int(openclaw.get("portStep", 20))
    if base_port < 1 or port_step < 1 or base_port + (players - 1) * port_step > 65535:
        raise ValueError("OpenClaw gateway ports must be distinct and within 1-65535")
    agent_retry_attempts = int(openclaw.get("agentRetryAttempts", 3))
    agent_retry_base_seconds = float(openclaw.get("agentRetryBaseSeconds", 5))
    agent_retry_max_seconds = float(openclaw.get("agentRetryMaxSeconds", 60))
    rate_limit_retry_seconds = float(openclaw.get("rateLimitRetrySeconds", 120))
    rate_limit_max_wait_seconds = float(openclaw.get("rateLimitMaxWaitSeconds", 3600))
    if not 0 <= agent_retry_attempts <= 10:
        raise ValueError("openclaw.agentRetryAttempts must be between 0 and 10")
    if not math.isfinite(agent_retry_base_seconds) or agent_retry_base_seconds <= 0:
        raise ValueError("openclaw.agentRetryBaseSeconds must be a positive number")
    if (
        not math.isfinite(agent_retry_max_seconds)
        or agent_retry_max_seconds < agent_retry_base_seconds
    ):
        raise ValueError(
            "openclaw.agentRetryMaxSeconds must be greater than or equal to "
            "agentRetryBaseSeconds"
        )
    if not math.isfinite(rate_limit_retry_seconds) or rate_limit_retry_seconds <= 0:
        raise ValueError("openclaw.rateLimitRetrySeconds must be a positive number")
    if not math.isfinite(rate_limit_max_wait_seconds) or rate_limit_max_wait_seconds < 0:
        raise ValueError("openclaw.rateLimitMaxWaitSeconds must be zero or a positive number")

    return RunnerSettings(
        mode=mode,
        players=players,
        seed=int(config.get("seed", 42) if seed_override is None else seed_override),
        game_id=game_id,
        repo_root=repo_root,
        extension_root=extension_root,
        runtime_root=runtime_root,
        model=str(openclaw.get("model", "")).strip(),
        api_key_env=api_key_env,
        api_key_envs=api_key_envs,
        agent_id=str(openclaw.get("agentId", "main")),
        thinking=str(openclaw.get("thinking", "low")),
        agent_timeout_seconds=int(openclaw.get("agentTimeoutSeconds", 600)),
        agent_retry_attempts=agent_retry_attempts,
        agent_retry_base_seconds=agent_retry_base_seconds,
        agent_retry_max_seconds=agent_retry_max_seconds,
        rate_limit_retry_seconds=rate_limit_retry_seconds,
        rate_limit_max_wait_seconds=rate_limit_max_wait_seconds,
        gateway_startup_seconds=int(openclaw.get("gatewayStartupSeconds", 90)),
        max_agent_calls_without_progress=int(config.get("maxAgentCallsWithoutProgress", 3)),
        max_agent_calls=int(config.get("maxAgentCalls", 200)),
        base_port=base_port,
        port_step=port_step,
        config_overlay=dict(openclaw.get("configOverlay", {})),
    )


def build_player_runtimes(settings: RunnerSettings) -> list[PlayerRuntime]:
    players = []
    for player_id in range(settings.players):
        root = settings.runtime_root / "profiles" / f"player-{player_id}"
        players.append(PlayerRuntime(
            player_id=player_id,
            port=settings.base_port + player_id * settings.port_step,
            gateway_token=secrets.token_urlsafe(32),
            state_dir=root / "state",
            config_path=root / "openclaw.json",
            workspace=root / "workspace",
        ))
    return players


def prepare_profiles(settings: RunnerSettings) -> list[PlayerRuntime]:
    template_path = settings.extension_root / "prompts" / "player.md"
    template = template_path.read_text(encoding="utf-8")
    players = build_player_runtimes(settings)

    for player in players:
        player.workspace.mkdir(parents=True, exist_ok=True)
        player.state_dir.mkdir(parents=True, exist_ok=True)
        instructions = (
            template.replace("{{PLAYER_ID}}", str(player.player_id))
            .replace("{{PLAYER_COUNT}}", str(settings.players))
        )
        (player.workspace / "AGENTS.md").write_text(instructions, encoding="utf-8")
        (player.workspace / "SOUL.md").write_text(
            f"# Persona\n\nYou are TradeCraft player P{player.player_id}. "
            "Be strategic, concise, and decisive.\n",
            encoding="utf-8",
        )
        (player.workspace / "IDENTITY.md").write_text(
            f"# Identity\n\nName: TradeCraft P{player.player_id}\n",
            encoding="utf-8",
        )
        (player.workspace / "USER.md").write_text(
            "# Operator\n\nThe match runner is the operator. Follow its turn prompts.\n",
            encoding="utf-8",
        )
        (player.workspace / "TOOLS.md").write_text(
            "# Tools\n\nUse only the six `tradecraft_*` tools exposed by the game plugin.\n",
            encoding="utf-8",
        )
        (player.workspace / "HEARTBEAT.md").write_text("", encoding="utf-8")

        defaults = {
            "workspace": str(player.workspace),
            "timeoutSeconds": settings.agent_timeout_seconds,
            "thinkingDefault": settings.thinking,
            "skipBootstrap": True,
        }
        if settings.model:
            defaults["model"] = {"primary": settings.model}
            defaults["models"] = {settings.model: {}}

        config = {
            "gateway": {
                "mode": "local",
                "port": player.port,
                "bind": "loopback",
                "auth": {"mode": "token", "token": player.gateway_token},
            },
            "agents": {
                "defaults": defaults,
                "list": [{
                    "id": settings.agent_id,
                    "name": f"TradeCraft P{player.player_id}",
                    "workspace": str(player.workspace),
                    "tools": {"allow": TRADECRAFT_TOOLS},
                }],
            },
            "plugins": {
                "enabled": True,
                "allow": ["tradecraft"],
                "entries": {
                    "tradecraft": {
                        "enabled": True,
                        "config": {
                            "socketPath": str(settings.socket_path),
                            "playerId": player.player_id,
                        },
                    },
                },
            },
        }
        config = _deep_merge(config, settings.config_overlay)
        config["gateway"]["auth"] = {
            "mode": "token",
            "token": player.gateway_token,
        }
        player_api_key_env = settings.api_key_env_for_player(player.player_id)
        if settings.api_key_envs and player_api_key_env:
            provider_id = settings.model.split("/", 1)[0]
            provider = (
                config.get("models", {})
                .get("providers", {})
                .get(provider_id)
            )
            if not isinstance(provider, dict):
                raise ValueError(
                    "openclaw.apiKeyEnvs requires a matching custom provider at "
                    f"openclaw.configOverlay.models.providers.{provider_id}"
                )
            provider["apiKey"] = f"${{{player_api_key_env}}}"
        _write_json(player.config_path, config)
        os.chmod(player.config_path, 0o600)
    return players


def _gameplay_progress_key(state: dict) -> tuple:
    trade = state.get("pending_trade")
    trade_key = None
    if trade:
        trade_key = (
            trade.get("proposer"),
            trade.get("responder"),
            json.dumps(trade.get("offer", {}), sort_keys=True),
            json.dumps(trade.get("request", {}), sort_keys=True),
        )
    return (
        state.get("phase"),
        state.get("placement_round"),
        state.get("placement_done"),
        state.get("round"),
        state.get("current_player"),
        state.get("current_actions_used"),
        trade_key,
    )


def _progress_key(state: dict) -> tuple:
    return (*_gameplay_progress_key(state), state.get("game_over"))


class GameRunner:
    def __init__(self, settings: RunnerSettings):
        self.settings = settings
        self.players = prepare_profiles(settings)
        self.processes: list[ManagedProcess] = []
        self.mock_clients: list[JsonLineClient] = []
        self.session_keys: dict[int, str] = {}
        self.delivered_messages: set[tuple[int, int]] = set()

    def _initialize_match(self):
        match_dir = self.settings.match_dir
        if match_dir.exists() and any(match_dir.iterdir()):
            raise RuntimeError(
                f"Match artifact directory already exists: {match_dir}; choose a new --game-id"
            )
        match_dir.mkdir(parents=True, exist_ok=True)
        _write_json(match_dir / "resolved-config.json", {
            "mode": self.settings.mode,
            "players": self.settings.players,
            "seed": self.settings.seed,
            "game_id": self.settings.game_id,
            "model": self.settings.model,
            "api_key_env": self.settings.api_key_env,
            "api_key_envs": list(self.settings.api_key_envs),
            "agent_retry_attempts": self.settings.agent_retry_attempts,
            "agent_retry_base_seconds": self.settings.agent_retry_base_seconds,
            "agent_retry_max_seconds": self.settings.agent_retry_max_seconds,
            "rate_limit_retry_seconds": self.settings.rate_limit_retry_seconds,
            "rate_limit_max_wait_seconds": self.settings.rate_limit_max_wait_seconds,
            "socket_path": str(self.settings.socket_path),
        })

    def _record(self, event_type: str, **fields):
        self.settings.match_dir.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": time.time(), "type": event_type, **fields}
        with self.settings.runner_event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _start_process(self, name: str, command: list[str], log_path: Path,
                       env: Optional[dict] = None) -> ManagedProcess:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=self.settings.repo_root,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        managed = ManagedProcess(name, process, log_handle)
        self.processes.append(managed)
        self._record("process_started", name=name, pid=process.pid, command=command)
        return managed

    def _start_engine(self) -> ManagedProcess:
        command = [
            sys.executable,
            str(self.settings.extension_root / "python" / "engine.py"),
            "--socket", str(self.settings.socket_path),
            "--players", str(self.settings.players),
            "--seed", str(self.settings.seed),
            "--state-file", str(self.settings.state_path),
            "--event-log", str(self.settings.engine_event_path),
        ]
        return self._start_process(
            "engine", command, self.settings.match_dir / "engine.log", os.environ.copy()
        )

    def _wait_for_engine(self, engine: ManagedProcess):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if engine.process.poll() is not None:
                raise RuntimeError(
                    f"Engine exited with code {engine.process.returncode}; "
                    f"see {self.settings.match_dir / 'engine.log'}"
                )
            if self.settings.socket_path.exists() and self.settings.state_path.exists():
                return
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for the TradeCraft engine")

    def _player_env(self, player: PlayerRuntime) -> dict:
        env = os.environ.copy()
        if self.settings.api_key_envs:
            selected = self.settings.api_key_env_for_player(player.player_id)
            for key_env in self.settings.api_key_envs:
                if key_env != selected:
                    env.pop(key_env, None)
        env["OPENCLAW_STATE_DIR"] = str(player.state_dir)
        env["OPENCLAW_CONFIG_PATH"] = str(player.config_path)
        return env

    def _find_node(self) -> str:
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node was not found in PATH; activate the TradeCraft Conda environment")
        return node

    def _gateway_call(self, player: PlayerRuntime, method: str, params: dict) -> dict:
        node = self._find_node()
        launcher = str(self.settings.repo_root / "openclaw.mjs")
        command = [
            node,
            launcher,
            "gateway",
            "call",
            method,
            "--params",
            json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "--timeout",
            "20000",
            "--json",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.settings.repo_root,
                env=self._player_env(player),
                capture_output=True,
                text=True,
                timeout=25,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Gateway P{player.player_id} {method} timed out"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown gateway error"
            raise RuntimeError(
                f"Gateway P{player.player_id} {method} failed: {detail[-2000:]}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Gateway P{player.player_id} {method} returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is False:
            raise RuntimeError(
                f"Gateway P{player.player_id} {method} rejected the request: {payload}"
            )
        return payload

    def _create_player_sessions(self):
        for player in self.players:
            result = self._gateway_call(player, "sessions.create", {
                "key": self.settings.session_key,
                "agentId": self.settings.agent_id,
                "label": f"TradeCraft {self.settings.game_id} P{player.player_id}",
            })
            session_key = result.get("key")
            if not isinstance(session_key, str) or not session_key:
                raise RuntimeError(
                    f"Gateway P{player.player_id} did not return a session key"
                )
            self.session_keys[player.player_id] = session_key
            self._record(
                "session_ready",
                player_id=player.player_id,
                session_key=session_key,
            )

    def _deliver_pending_messages(self, state: dict):
        if self.settings.mode != "openclaw":
            return
        for message in state.get("messages", []):
            message_id = int(message["id"])
            sender = int(message["from"])
            content = str(message["content"])
            for recipient in message["recipients"]:
                recipient_id = int(recipient)
                delivery_key = (message_id, recipient_id)
                if delivery_key in self.delivered_messages:
                    continue
                session_key = self.session_keys.get(recipient_id)
                if not session_key:
                    raise RuntimeError(
                        f"Player {recipient_id} session is unavailable for message {message_id}"
                    )
                result = self._gateway_call(
                    self.players[recipient_id],
                    "chat.inject",
                    {
                        "sessionKey": session_key,
                        "agentId": self.settings.agent_id,
                        "label": f"TradeCraft message #{message_id} from P{sender}",
                        "message": content,
                    },
                )
                self.delivered_messages.add(delivery_key)
                self._record(
                    "message_delivered",
                    message_id=message_id,
                    sender=sender,
                    recipient=recipient_id,
                    session_key=session_key,
                    gateway_message_id=result.get("messageId"),
                )

    def _verify_session_delivery(self):
        content = "P0 broadcast delivery probe. This is not a game action."
        recipients = list(range(1, self.settings.players))
        self._deliver_pending_messages({"messages": [{
            "id": 0,
            "from": 0,
            "recipients": recipients,
            "content": content,
            "round": 0,
        }]})
        label = "TradeCraft message #0 from P0"
        for recipient in recipients:
            history = self._gateway_call(self.players[recipient], "chat.history", {
                "sessionKey": self.session_keys[recipient],
                "agentId": self.settings.agent_id,
                "limit": 10,
            })
            serialized = json.dumps(history, ensure_ascii=False)
            if label not in serialized or content not in serialized:
                raise RuntimeError(
                    f"Player {recipient} session history does not contain the delivery probe"
                )
        self._record(
            "session_delivery_verified",
            sender=0,
            recipients=recipients,
        )

    def _wait_for_port(self, managed: ManagedProcess, port: int):
        deadline = time.monotonic() + self.settings.gateway_startup_seconds
        while time.monotonic() < deadline:
            if managed.process.poll() is not None:
                raise RuntimeError(
                    f"{managed.name} exited with code {managed.process.returncode}; "
                    f"see {self.settings.match_dir / (managed.name + '.log')}"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError(f"Timed out waiting for {managed.name} on port {port}")

    def _start_gateways(self):
        node = self._find_node()
        launcher = str(self.settings.repo_root / "openclaw.mjs")
        for player in self.players:
            try:
                with socket.create_connection(("127.0.0.1", player.port), timeout=0.2):
                    raise RuntimeError(f"Gateway port {player.port} is already in use")
            except ConnectionRefusedError:
                pass
            except TimeoutError:
                pass
            command = [node, launcher, "gateway", "--port", str(player.port)]
            managed = self._start_process(
                f"gateway-p{player.player_id}",
                command,
                self.settings.match_dir / f"gateway-p{player.player_id}.log",
                self._player_env(player),
            )
            self._wait_for_port(managed, player.port)

    def _read_state(self) -> dict:
        return _read_json(self.settings.state_path)

    def _wait_for_players(self):
        deadline = time.monotonic() + self.settings.gateway_startup_seconds
        while time.monotonic() < deadline:
            state = self._read_state()
            connected = state.get("_meta", {}).get("connected_players", [])
            if connected == list(range(self.settings.players)):
                return
            time.sleep(0.1)
        raise TimeoutError("Not all TradeCraft players registered with the engine")

    def _scheduled_player(self, state: dict) -> int:
        trade = state.get("pending_trade")
        if trade:
            return int(trade["responder"])
        return int(state["current_player"])

    def _run_mock_turn(self, player_id: int):
        client = self.mock_clients[player_id]
        for _ in range(8):
            response = client.request("view_game_state", {})
            state = response["result"]
            if state["game_over"]:
                return
            trade = state.get("pending_trade")
            if trade and trade["responder"] == player_id:
                client.request("respond_trade", {
                    "action": "reject",
                    "message": "Mock responder declines this offer.",
                })
                continue
            if state["current_player"] != player_id:
                return
            if state["phase"] == "placement":
                targets = state["expansion_targets"]
                settlements = targets["settlements"]
                roads = targets["roads"]
                if settlements:
                    result = client.request("build", {
                        "type": "settlement",
                        "vertex_id": next(iter(settlements)),
                    })
                elif roads:
                    result = client.request("build", {
                        "type": "road",
                        "edge_id": next(iter(roads)),
                    })
                else:
                    raise RuntimeError(f"Mock player {player_id} has no legal placement")
            else:
                result = client.request("send_message", {
                    "content": f"P{player_id} mock action",
                })
            if not result.get("ok"):
                raise RuntimeError(f"Mock player {player_id} action failed: {result}")
            result_state = result.get("result", {}).get("state", {})
            if result_state.get("game_over"):
                return
        raise RuntimeError(f"Mock player {player_id} did not finish its turn")

    def _run_mock(self):
        for player_id in range(self.settings.players):
            self.mock_clients.append(JsonLineClient(self.settings.socket_path, player_id))
        self._wait_for_players()
        self._run_turn_loop(self._run_mock_turn)

    def _agent_retry_delay(self, retry_number: int) -> float:
        ceiling = min(
            self.settings.agent_retry_max_seconds,
            self.settings.agent_retry_base_seconds * (2 ** (retry_number - 1)),
        )
        # Equal jitter avoids synchronized retries while preserving a meaningful
        # backoff floor for rate-limited providers.
        return random.uniform(ceiling / 2, ceiling)

    def _rate_limit_retry_delay(self) -> float:
        ceiling = self.settings.rate_limit_retry_seconds
        return random.uniform(ceiling * 0.8, ceiling)

    def _recover_if_game_progressed(
        self,
        baseline_key: tuple,
        player_id: int,
        attempt: int,
        failure_kind: AgentFailureKind,
        review_stage: str,
    ) -> bool:
        state = self._read_state()
        if _gameplay_progress_key(state) == baseline_key:
            return False
        self._record(
            "agent_failure_after_progress",
            player_id=player_id,
            attempt=attempt,
            failure_class=failure_kind.value,
            review_stage=review_stage,
            progress_key=_progress_key(state),
        )
        return True

    def _invoke_openclaw_agent(self, player_id: int) -> int:
        player = self.players[player_id]
        node = self._find_node()
        launcher = str(self.settings.repo_root / "openclaw.mjs")
        message = (
            f"You are scheduled now as TradeCraft P{player_id}. Read the game state and use "
            "the TradeCraft tools; do not merely explain a move. If you are the pending trade "
            "responder, respond exactly once with a concise message explaining your opinion to "
            "the proposer, then stop without continuing into normal turn actions. Otherwise, "
            "complete your placement step or remaining normal-turn actions. Exception: after a "
            "successful trade proposal, stop immediately and return control to the runner. Do "
            "not call another tool or send a follow-up message."
        )
        command = [
            node,
            launcher,
            "agent",
            "--agent", self.settings.agent_id,
            "--session-key", self.settings.session_key,
            "--message", message,
            "--thinking", self.settings.thinking,
            "--timeout", str(self.settings.agent_timeout_seconds),
            "--json",
        ]
        log_path = self.settings.match_dir / f"agent-p{player_id}.jsonl"
        baseline_key = _gameplay_progress_key(self._read_state())
        max_attempts = self.settings.agent_retry_attempts + 1
        rate_limit_deadline = None
        attempt = 0

        while True:
            attempt += 1
            started = time.time()
            try:
                result = subprocess.run(
                    command,
                    cwd=self.settings.repo_root,
                    env=self._player_env(player),
                    capture_output=True,
                    text=True,
                    timeout=self.settings.agent_timeout_seconds + 30,
                )
                record = {
                    "timestamp": started,
                    "player_id": player_id,
                    "attempt": attempt,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            except subprocess.TimeoutExpired as exc:
                record = {
                    "timestamp": started,
                    "player_id": player_id,
                    "attempt": attempt,
                    "returncode": None,
                    "stdout": _output_text(exc.stdout),
                    "stderr": _output_text(exc.stderr),
                    "error": "agent subprocess timeout",
                }

            if record["returncode"] == 0:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                return attempt

            failure_kind = _classify_agent_failure(record)
            retryable = failure_kind in RETRYABLE_AGENT_FAILURES
            record["failure_class"] = failure_kind.value
            record["retryable"] = retryable
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

            if self._recover_if_game_progressed(
                baseline_key,
                player_id,
                attempt,
                failure_kind,
                "immediate",
            ):
                return attempt

            if not retryable:
                detail = _agent_failure_detail(record)
                raise RuntimeError(
                    f"OpenClaw agent P{player_id} failed "
                    f"[{failure_kind.value}] (exit {record['returncode']}): {detail[-2000:]}"
                )

            if attempt < max_attempts:
                delay = self._agent_retry_delay(attempt)
                self._record(
                    "agent_retry_scheduled",
                    player_id=player_id,
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    failure_class=failure_kind.value,
                    delay_seconds=delay,
                )
                time.sleep(delay)
                if self._recover_if_game_progressed(
                    baseline_key,
                    player_id,
                    attempt,
                    failure_kind,
                    "after_backoff",
                ):
                    return attempt
                continue

            if (
                failure_kind == AgentFailureKind.RATE_LIMIT
                and self.settings.rate_limit_max_wait_seconds > 0
            ):
                now = time.monotonic()
                if rate_limit_deadline is None:
                    rate_limit_deadline = now + self.settings.rate_limit_max_wait_seconds
                    self._record(
                        "rate_limit_fallback_started",
                        player_id=player_id,
                        attempt=attempt,
                        max_wait_seconds=self.settings.rate_limit_max_wait_seconds,
                    )
                remaining = rate_limit_deadline - now
                if remaining > 0:
                    delay = min(self._rate_limit_retry_delay(), remaining)
                    self._record(
                        "rate_limit_wait_scheduled",
                        player_id=player_id,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        delay_seconds=delay,
                        remaining_wait_seconds=remaining,
                    )
                    time.sleep(delay)
                    if self._recover_if_game_progressed(
                        baseline_key,
                        player_id,
                        attempt,
                        failure_kind,
                        "after_rate_limit_wait",
                    ):
                        return attempt
                    continue

            # A failed CLI can finish its last tool call in the Gateway just
            # after the subprocess exits. One final observation window avoids
            # aborting a game that has already advanced.
            review_delay = min(
                self.settings.agent_retry_base_seconds,
                self.settings.agent_retry_max_seconds,
            )
            self._record(
                "agent_retry_exhausted",
                player_id=player_id,
                attempt=attempt,
                failure_class=failure_kind.value,
                state_review_delay_seconds=review_delay,
            )
            time.sleep(review_delay)
            if self._recover_if_game_progressed(
                baseline_key,
                player_id,
                attempt,
                failure_kind,
                "final_review",
            ):
                return attempt

            detail = _agent_failure_detail(record)
            raise RuntimeError(
                f"OpenClaw agent P{player_id} exhausted {attempt} attempts "
                f"[{failure_kind.value}]: {detail[-2000:]}"
            )

    def _run_turn_loop(self, invoke_player):
        total_calls = 0
        stalled_calls = 0
        previous_key = None
        while True:
            state = self._read_state()
            if state.get("game_over"):
                self._record(
                    "match_complete",
                    winner=state.get("winner"),
                    scores=state.get("scores"),
                    total_agent_calls=total_calls,
                )
                return
            if total_calls >= self.settings.max_agent_calls:
                raise RuntimeError("Maximum total agent calls exceeded")

            before_key = _progress_key(state)
            player_id = self._scheduled_player(state)
            self._record(
                "agent_scheduled",
                player_id=player_id,
                phase=state.get("phase"),
                round=state.get("round"),
                progress_key=before_key,
            )
            attempts = invoke_player(player_id)
            total_calls += attempts if isinstance(attempts, int) and attempts > 0 else 1
            after = self._read_state()
            self._deliver_pending_messages(after)
            after_key = _progress_key(after)

            if after_key == before_key:
                stalled_calls = stalled_calls + 1 if previous_key == before_key else 1
                previous_key = before_key
                self._record("agent_no_progress", player_id=player_id, attempts=stalled_calls)
                if stalled_calls >= self.settings.max_agent_calls_without_progress:
                    raise RuntimeError(
                        f"Player {player_id} made no game progress after {stalled_calls} calls"
                    )
            else:
                stalled_calls = 0
                previous_key = after_key

    def run(self) -> dict:
        if self.settings.mode == "openclaw":
            if not self.settings.model:
                raise RuntimeError(
                    "openclaw.model is empty; set it after choosing the LLM provider"
                )
            if "/" not in self.settings.model:
                raise RuntimeError(
                    "openclaw.model must use provider/model format "
                    f"(received {self.settings.model!r})"
                )
            required_key_envs = self.settings.api_key_envs
            if not required_key_envs and self.settings.api_key_env:
                required_key_envs = (self.settings.api_key_env,)
            missing_key_envs = [
                key_env for key_env in required_key_envs if not os.environ.get(key_env)
            ]
            if missing_key_envs:
                raise RuntimeError(
                    "Required API key environment variables are empty: "
                    + ", ".join(missing_key_envs)
                )

        self._initialize_match()
        engine = self._start_engine()
        try:
            self._wait_for_engine(engine)
            if self.settings.mode == "mock":
                self._run_mock()
            else:
                self._start_gateways()
                self._wait_for_players()
                self._create_player_sessions()
                self._run_turn_loop(self._invoke_openclaw_agent)
            return self._read_state()
        finally:
            for client in self.mock_clients:
                try:
                    client.close()
                except OSError:
                    pass
            for managed in reversed(self.processes):
                managed.stop()
            try:
                self.settings.socket_path.unlink()
            except FileNotFoundError:
                pass

    def run_infrastructure(self, hold: bool = False) -> dict:
        """Start the engine and gateways without making an LLM request."""
        self._initialize_match()
        engine = self._start_engine()
        try:
            self._wait_for_engine(engine)
            self._start_gateways()
            self._wait_for_players()
            self._create_player_sessions()
            if not hold:
                self._verify_session_delivery()
            state = self._read_state()
            self._record(
                "infrastructure_ready",
                connected_players=state.get("_meta", {}).get("connected_players", []),
            )
            if hold:
                print(
                    f"TradeCraft infrastructure is ready at {self.settings.socket_path}. "
                    "Press Ctrl-C to stop.",
                    flush=True,
                )
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
            return state
        finally:
            for managed in reversed(self.processes):
                managed.stop()
            try:
                self.settings.socket_path.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    default_config = Path(__file__).resolve().parents[1] / "game_runner.example.json"
    parser = argparse.ArgumentParser(description="Run a TradeCraft LLM match")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--mode", choices=("mock", "openclaw"))
    parser.add_argument("--game-id")
    parser.add_argument(
        "--seed",
        type=int,
        help="Override the configured map and dice seed for this match",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate isolated OpenClaw player profiles without starting a match",
    )
    infrastructure = parser.add_mutually_exclusive_group()
    infrastructure.add_argument(
        "--check-infrastructure",
        action="store_true",
        help="Start engine and gateways, verify player registration, then stop",
    )
    infrastructure.add_argument(
        "--serve-infrastructure",
        action="store_true",
        help="Start engine and gateways without calling an LLM; stop with Ctrl-C",
    )
    args = parser.parse_args()

    try:
        settings = load_settings(args.config, args.mode, args.game_id, args.seed)
        if args.prepare_only:
            players = prepare_profiles(settings)
            print(f"Prepared {len(players)} player profiles under {settings.runtime_root / 'profiles'}")
            for player in players:
                print(f"P{player.player_id}: {player.config_path}")
            return 0

        runner = GameRunner(settings)
        if args.check_infrastructure or args.serve_infrastructure:
            state = runner.run_infrastructure(hold=args.serve_infrastructure)
            print(json.dumps({
                "game_id": settings.game_id,
                "connected_players": state.get("_meta", {}).get("connected_players", []),
                "artifacts": str(settings.match_dir),
            }, ensure_ascii=False, indent=2))
            return 0

        final_state = runner.run()
        print(json.dumps({
            "game_id": settings.game_id,
            "winner": final_state.get("winner"),
            "scores": final_state.get("scores"),
            "artifacts": str(settings.match_dir),
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"TradeCraft runner failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
