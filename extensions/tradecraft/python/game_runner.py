#!/usr/bin/env python3
"""Run a complete TradeCraft match with mock or OpenClaw-backed players."""

from __future__ import annotations

import argparse
import copy
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

from map_generator import ACTIONS_PER_ROUND


TRADECRAFT_TOOLS = [
    "tradecraft_build",
    "tradecraft_propose_trade",
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


class AgentNoReply(RuntimeError):
    """Agent was woken but returned NO_REPLY (declined to act).

    Treated by the scheduler as a turn forfeit so a silent player never blocks
    the flow; the forfeit feeds the turn-health statistics.
    """


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
    max_rounds: int
    limit: int
    game_id: str
    repo_root: Path
    extension_root: Path
    runtime_root: Path
    profile_root: Path
    model: str
    api_key_env: str
    api_key_envs: tuple[str, ...]
    openclaw_bin: str
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
    role_sandbox_urls: dict
    teams: Optional[list[list[int]]]
    map_seed: Optional[int]

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


class ControllerClient(JsonLineClient):
    """Scheduler-side connection for the controller actions (get_view/advance)."""

    def __init__(self, socket_path: Path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(20)
        self.socket.connect(str(socket_path))
        self.reader = self.socket.makefile("r", encoding="utf-8")
        registration = self.request("register_controller", {})
        if not registration.get("ok"):
            raise RuntimeError(f"Controller registration failed: {registration}")

    def get_view(self, player_id: int) -> dict:
        response = self.request("get_view", {"player_id": player_id})
        if not response.get("ok"):
            raise RuntimeError(f"get_view({player_id}) failed: {response}")
        return response["result"]

    def advance(self, player_id: int) -> dict:
        response = self.request("advance", {"player_id": player_id})
        if not response.get("ok"):
            raise RuntimeError(f"advance({player_id}) failed: {response}")
        return response["result"]

    def forfeit(self, player_id: int) -> dict:
        response = self.request("forfeit_player", {"player_id": player_id})
        if not response.get("ok"):
            raise RuntimeError(f"forfeit({player_id}) failed: {response}")
        return response["result"]

    def start_round(self) -> dict:
        response = self.request("start_round", {})
        if not response.get("ok"):
            raise RuntimeError(f"start_round failed: {response}")
        return response["result"]

    def end_placement(self) -> dict:
        response = self.request("end_placement", {})
        if not response.get("ok"):
            raise RuntimeError(f"end_placement failed: {response}")
        return response["result"]

    def snapshot_round_scores(self) -> dict:
        response = self.request("snapshot_round_scores", {})
        if not response.get("ok"):
            raise RuntimeError(f"snapshot_round_scores failed: {response}")
        return response["result"]

    def settle(self) -> dict:
        response = self.request("end_game", {})
        if not response.get("ok"):
            raise RuntimeError(f"end_game failed: {response}")
        return response["result"]

    def resolve_trade(self, decision: str, reason: str) -> dict:
        # An ``ok: False`` here is a normal in-game rejection (e.g. the
        # proposer no longer holds the offered resources at execution time),
        # not an engine fault. Return the raw response so the scheduler can
        # treat it as a forfeit instead of crashing the whole match.
        return self.request("resolve_trade", {"decision": decision, "reason": reason})


def _map_topology_json(settings: RunnerSettings) -> str:
    """Render the fixed hex map (hexes/vertices/edges) as compact JSON.

    The runner derives this independently of the engine from the same seed via
    ``map_generator``, so both sides always agree on ids and adjacency.
    """
    from map_generator import generate_map, compact_map_view
    game_map = generate_map(settings.seed, total_rounds=settings.max_rounds,
                            map_seed=settings.map_seed)
    return json.dumps(compact_map_view(game_map), separators=(",", ":"))


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


def _compression_url(base_url: str) -> str:
    """Derive a gateway's context-compression endpoint from its chat base URL."""
    if base_url.endswith("/v1"):
        return base_url[:-len("/v1")] + "/compression/v1"
    return base_url.rstrip("/") + "/compression/v1"


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
                  seed_override: Optional[int] = None,
                  base_port_override: Optional[int] = None,
                  profile_root_override: Optional[Path] = None,
                  max_rounds_override: Optional[int] = None,
                  limit_override: Optional[int] = None) -> RunnerSettings:
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
    max_rounds = int(
        config.get("maxRounds", 12)
        if max_rounds_override is None
        else max_rounds_override
    )
    if max_rounds < 1:
        raise ValueError("maxRounds must be positive")
    limit = int(
        config.get("limit", 3)
        if limit_override is None
        else limit_override
    )
    if limit < 1:
        raise ValueError("limit must be positive")
    raw_teams = config.get("teams")
    teams = None
    if raw_teams:
        if not isinstance(raw_teams, list):
            raise ValueError("teams must be a list of player-id groups")
        teams = [[int(pid) for pid in group] for group in raw_teams]
    raw_map_seed = config.get("map_seed")
    map_seed = None
    if raw_map_seed is not None:
        if not isinstance(raw_map_seed, int) or isinstance(raw_map_seed, bool):
            raise ValueError("map_seed must be an integer")
        map_seed = int(raw_map_seed)
    openclaw = config.get("openclaw", {})
    runtime_root = _resolve_path(
        config.get("runtimeRoot", str(repo_root / "runs" / "tradecraft")),
        config_path.parent,
    )
    profile_root = (
        profile_root_override.expanduser().resolve()
        if profile_root_override is not None
        else runtime_root / "profiles"
    )
    game_id = game_id_override or config.get("gameId")
    if not game_id:
        game_id = f"{time.strftime('game-%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    game_id = str(game_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", game_id):
        raise ValueError("gameId must contain only letters, numbers, dot, underscore, or dash")

    openclaw_bin = str(openclaw.get("bin", "")).strip()
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
    base_port = int(
        openclaw.get("basePort", 18789)
        if base_port_override is None
        else base_port_override
    )
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
        max_rounds=max_rounds,
        limit=limit,
        game_id=game_id,
        repo_root=repo_root,
        extension_root=extension_root,
        runtime_root=runtime_root,
        profile_root=profile_root,
        model=str(openclaw.get("model", "")).strip(),
        api_key_env=api_key_env,
        api_key_envs=api_key_envs,
        openclaw_bin=openclaw_bin,
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
        role_sandbox_urls=dict(openclaw.get("roleSandboxUrls", {})),
        teams=teams,
        map_seed=map_seed,
    )


def build_player_runtimes(settings: RunnerSettings) -> list[PlayerRuntime]:
    players = []
    for player_id in range(settings.players):
        root = settings.profile_root / f"player-{player_id}"
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
    map_topology = _map_topology_json(settings)

    for player in players:
        player.workspace.mkdir(parents=True, exist_ok=True)
        player.state_dir.mkdir(parents=True, exist_ok=True)
        team_section = ""
        if settings.teams:
            team = next(
                (group for group in settings.teams if player.player_id in group),
                None,
            )
            if team:
                teammates = [pid for pid in team if pid != player.player_id]
                team_section = (
                    "\n\nYou are on a TEAM with "
                    + ", ".join(f"P{pid}" for pid in teammates)
                    + ". Maximize your team's COMBINED score; final ranking and "
                      "reward are decided by the team total. Cooperate — your "
                      "teammate's score is your score. Trade with your teammate "
                      "all you like (same-team trades still help the team but do "
                      "not count toward the Trade Master project), and negotiate "
                      "with opposing players as opponents."
                )
        instructions = (
            template.replace("{{PLAYER_ID}}", str(player.player_id))
            .replace("{{PLAYER_COUNT}}", str(settings.players))
            .replace("{{TEAM_SECTION}}", team_section)
        )
        agents_md = (
            instructions
            + "\n\n## Map topology (fixed for this match; immutable)\n"
            + "The hex map is fixed. `hexes` maps hex id to [resource, diceNumber]; "
              "`vertices` maps vertex id to adjacent hex ids; `edges` maps edge id to "
              "[vertexA, vertexB]. Desert hexes have `null` dice number and never produce.\n\n"
            + "```json\n" + map_topology + "\n```\n"
        )
        (player.workspace / "AGENTS.md").write_text(agents_md, encoding="utf-8")
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
            "# Tools\n\nUse only the four `tradecraft_*` tools exposed by the game plugin. "
            "To end a turn, do not call any tool — simply end your reply.\n",
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
        if settings.role_sandbox_urls:
            role_url = settings.role_sandbox_urls.get(f"P{player.player_id}")
            if role_url:
                # ``config["models"]`` is a shared reference into config_overlay;
                # deep-copy before mutating so each player's baseUrl stays isolated.
                config = copy.deepcopy(config)
                provider_id = settings.model.split("/", 1)[0]
                provider = (
                    config.get("models", {})
                    .get("providers", {})
                    .get(provider_id)
                )
                if not isinstance(provider, dict):
                    raise ValueError(
                        "openclaw.roleSandboxUrls requires a matching custom provider at "
                        f"openclaw.configOverlay.models.providers.{provider_id}"
                    )
                provider["baseUrl"] = role_url
                # Context compaction is session-scoped too: each in-game gateway
                # must summarise against its own /compression endpoint, otherwise
                # it lands on the leader session's prefix cache and stalls.
                compression = (
                    config.get("models", {})
                    .get("providers", {})
                    .get("context-compression")
                )
                if isinstance(compression, dict):
                    compression["baseUrl"] = _compression_url(role_url)
        _write_json(player.config_path, config)
        os.chmod(player.config_path, 0o600)
    return players


def _gameplay_progress_key(state: dict) -> tuple:
    """Cheap fingerprint of mutable board facts (no turn/order state).

    The scheduler itself owns turn order, so game progress is measured by
    objective board changes — ownership, roads, resources, round, phase — plus
    the pending-trade facts. Used only to detect whether a failed/flaky agent
    invocation nonetheless advanced the board (e.g. a last tool call landed in
    the Gateway after the CLI returned a nonzero exit).
    """
    trade = state.get("pending_trade")
    trade_key = None
    if trade:
        trade_key = (
            trade.get("proposer"),
            trade.get("responder"),
            json.dumps(trade.get("offer", {}), sort_keys=True),
            json.dumps(trade.get("request", {}), sort_keys=True),
        )
    players_key = tuple(
        (len(p.get("buildings", [])), len(p.get("roads", [])),
         json.dumps(p.get("resources", {}), sort_keys=True))
        for p in state.get("players", [])
    )
    return (
        state.get("phase"),
        state.get("round"),
        players_key,
        trade_key,
    )


def _progress_key(state: dict) -> tuple:
    return (*_gameplay_progress_key(state), state.get("game_over"))


def _extract_agent_reply_text(stdout: str) -> str:
    """Extract the agent's final reply text from ``--json`` CLI stdout.

    The OpenClaw agent CLI prints a JSON envelope whose ``result.payloads``
    carry the assistant's visible reply text. Returns ``""`` when the envelope
    is missing, malformed, or has no text payload.
    """
    if not stdout:
        return ""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return ""
    result = data.get("result") if isinstance(data, dict) else None
    payloads = result.get("payloads") if isinstance(result, dict) else None
    if not isinstance(payloads, list):
        return ""
    return "\n".join(
        p.get("text", "")
        for p in payloads
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    ).strip()


def _is_no_reply(stdout: str) -> bool:
    """True when the agent declined to act via the platform NO_REPLY sentinel."""
    text = _extract_agent_reply_text(stdout)
    return bool(text) and text.upper() == "NO_REPLY"


def _extract_trade_decision(stdout: str) -> tuple[str, str]:
    """Parse the responder's ``{decision, reason}`` JSON from agent stdout.

    The responder emits its accept/reject decision as a JSON object (typically in
    a fenced `````json````` code block) inside its final message text. Any parse
    failure is treated as a rejection with a system-supplied reason.
    """
    def _reject():
        return "reject", "(system) 无法从响应方输出解析出交易决策，按拒绝处理"

    text = _extract_agent_reply_text(stdout)
    if not text:
        return _reject()

    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    candidates.extend(
        obj for obj in re.findall(r"\{[^{}]*\"decision\"[^{}]*\}", text)
    )
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict) or "decision" not in obj:
            continue
        decision = str(obj.get("decision", "")).strip().lower()
        if decision not in ("accept", "reject"):
            continue
        reason = str(obj.get("reason", "")).strip()
        return decision, reason or "(system) 响应方未提供决策理由"
    return _reject()


class GameRunner:
    def __init__(self, settings: RunnerSettings):
        self.settings = settings
        self.players = prepare_profiles(settings)
        self.processes: list[ManagedProcess] = []
        self.mock_clients: list[JsonLineClient] = []
        self.controller: "ControllerClient | None" = None
        self.session_keys: dict[int, str] = {}
        self.delivered_messages: set[tuple[int, int]] = set()
        self._last_agent_stdout: dict[int, str] = {}

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
            "max_rounds": self.settings.max_rounds,
            "limit": self.settings.limit,
            "game_id": self.settings.game_id,
            "model": self.settings.model,
            "api_key_env": self.settings.api_key_env,
            "api_key_envs": list(self.settings.api_key_envs),
            "agent_retry_attempts": self.settings.agent_retry_attempts,
            "agent_retry_base_seconds": self.settings.agent_retry_base_seconds,
            "agent_retry_max_seconds": self.settings.agent_retry_max_seconds,
            "rate_limit_retry_seconds": self.settings.rate_limit_retry_seconds,
            "rate_limit_max_wait_seconds": self.settings.rate_limit_max_wait_seconds,
            "base_port": self.settings.base_port,
            "profile_root": str(self.settings.profile_root),
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
            "--max-rounds", str(self.settings.max_rounds),
            "--limit", str(self.settings.limit),
            "--state-file", str(self.settings.state_path),
            "--event-log", str(self.settings.engine_event_path),
        ]
        if self.settings.teams:
            command += ["--teams", json.dumps(self.settings.teams)]
        if self.settings.map_seed is not None:
            command += ["--map-seed", str(self.settings.map_seed)]
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

    def _openclaw_command(self, *args: str) -> list[str]:
        """Resolve the OpenClaw CLI launch prefix and append *args.

        Prefers the installed ``openclaw`` CLI (e.g. a sandbox image with a
        global install) and falls back to the repository's ``openclaw.mjs``
        launcher via Node for source-checkout development.
        """
        binary = (
            self.settings.openclaw_bin
            or shutil.which("openclaw")
            or ""
        )
        if binary:
            prefix = [binary]
        else:
            node = shutil.which("node")
            if not node:
                raise RuntimeError(
                    "Neither the openclaw CLI nor node was found in PATH; "
                    "activate the TradeCraft environment"
                )
            prefix = [node, str(self.settings.repo_root / "openclaw.mjs")]
        return [*prefix, *args]

    def _gateway_call(self, player: PlayerRuntime, method: str, params: dict) -> dict:
        command = self._openclaw_command(
            "gateway",
            "call",
            method,
            "--params",
            json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "--timeout",
            "60000",
            "--json",
        )
        try:
            result = subprocess.run(
                command,
                cwd=self.settings.repo_root,
                env=self._player_env(player),
                capture_output=True,
                text=True,
                timeout=90,
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

    def _wait_for_port(self, managed: ManagedProcess, port: int, player: PlayerRuntime | None = None):
        deadline = time.monotonic() + self.settings.gateway_startup_seconds
        while time.monotonic() < deadline:
            if managed.process.poll() is not None:
                log_path = self.settings.match_dir / (managed.name + '.log')
                try:
                    log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                except OSError:
                    log_tail = "(log unavailable)"
                diag = ""
                if player is not None:
                    # Dump the exact config the gateway rejected, straight to stderr
                    # (stderr flows into the harness error, unlike state_dir files).
                    # Print as SINGLE-LINE JSON: multi-line gets interleaved with
                    # ray log lines at every newline, truncating the dump.
                    try:
                        cfg_txt = player.config_path.read_text(encoding="utf-8", errors="replace")
                        try:
                            cfg_compact = json.dumps(
                                json.loads(cfg_txt), ensure_ascii=False, separators=(",", ":")
                            )
                        except json.JSONDecodeError:
                            cfg_compact = cfg_txt.replace("\n", " ")
                        print(f"==GWC==", file=sys.stderr)
                        print(cfg_compact, file=sys.stderr)
                        print(f"==ENDGWC==", file=sys.stderr)
                        diag = f"config:\n{cfg_txt}"
                    except OSError as e:
                        diag = f"(config read failed: {e})"
                    for name in ("logs/config-audit.jsonl", "logs/gateway.log"):
                        p = player.state_dir / name
                        try:
                            txt = p.read_text(encoding="utf-8", errors="replace")
                            print(f"==AUDIT {name}==", file=sys.stderr)
                            print(txt[-4000:].replace("\n", " | "), file=sys.stderr)
                            diag += f"\n{name}:\n{txt[-4000:]}"
                        except OSError:
                            pass
                    # Recursively walk state_dir to capture the real startup-failure
                    # bundle (path is version-dependent).
                    try:
                        for root, _dirs, files in os.walk(str(player.state_dir)):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    txt = open(fp, encoding="utf-8", errors="replace").read()
                                except OSError:
                                    continue
                                if not txt.strip():
                                    continue
                                print(f"==STATE {fp}==", file=sys.stderr)
                                print(txt[-3000:].replace("\n", " | "), file=sys.stderr)
                                diag += f"\n{fp}:\n{txt[-3000:]}"
                    except OSError:
                        pass
                raise RuntimeError(
                    f"{managed.name} exited with code {managed.process.returncode}; "
                    f"log tail:\n{log_tail}\n{diag}"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError(f"Timed out waiting for {managed.name} on port {port}")

    def _start_gateways(self):
        for player in self.players:
            try:
                with socket.create_connection(("127.0.0.1", player.port), timeout=0.2):
                    raise RuntimeError(f"Gateway port {player.port} is already in use")
            except ConnectionRefusedError:
                pass
            except TimeoutError:
                pass
            command = self._openclaw_command("gateway", "run", "--port", str(player.port))
            managed = self._start_process(
                f"gateway-p{player.player_id}",
                command,
                self.settings.match_dir / f"gateway-p{player.player_id}.log",
                self._player_env(player),
            )
            self._wait_for_port(managed, player.port, player)

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

    # ── Scheduler-owned orchestration ────────────────────────────────────
    #
    # The engine is a pure rules referee with no turn/order state. This runner
    # owns the full agent action lifecycle: it decides the wake order (snake
    # draft during placement, round-robin during play), how many actions each
    # player gets, and when to start rounds / end placement / settle.

    @staticmethod
    def _calls(player_id: int, attempts) -> int:
        return attempts if isinstance(attempts, int) and attempts > 0 else 1

    def _drive_step(self, invoke_player, player_id: int, goal, step_label: str) -> int:
        """Wake ``player_id`` until ``goal(state)`` holds, forfeiting on stall.

        The scheduler, not the engine, decides what must happen next. ``goal``
        inspects the objective board state; the player is woken repeatedly until
        the goal is reached (agents may over- or under-act in one wake), or the
        player is forfeited so the match never stalls on one inert agent.
        """
        calls = 0
        limit = self.settings.max_agent_calls_without_progress + 1
        for _ in range(limit):
            if goal(self._read_state()):
                return calls
            try:
                calls += self._calls(player_id, invoke_player(player_id))
            except RuntimeError as exc:
                self._forfeit_player(player_id, exc)
                return calls
            self._after_wake(player_id)
        self._forfeit_player(
            player_id,
            RuntimeError(f"step not reached: {step_label}"),
        )
        return calls
    def _run_mock_turn(self, player_id: int):
        """Deterministic mock: place the engine's recommended next piece or act."""
        client = self.mock_clients[player_id]
        response = client.request("view_game_state", {})
        state = response["result"]
        if state.get("game_over"):
            return
        if state.get("phase") == "placement":
            me = next(p for p in state["players"] if p["player_id"] == player_id)
            if me["placement_vertex"] is None:
                targets = state["expansion_targets"]["settlements"]
                if not targets:
                    raise RuntimeError(f"Mock player {player_id} has no legal settlement")
                result = client.request("build", {
                    "type": "settlement",
                    "vertex_id": next(iter(targets)),
                })
            else:
                targets = state["expansion_targets"]["roads"]
                if not targets:
                    raise RuntimeError(f"Mock player {player_id} has no legal road")
                result = client.request("build", {
                    "type": "road",
                    "edge_id": next(iter(targets)),
                })
        else:
            result = client.request("send_message", {
                "content": f"P{player_id} mock action",
            })
        if not result.get("ok"):
            raise RuntimeError(f"Mock player {player_id} action failed: {result}")

    def _run_mock(self):
        for player_id in range(self.settings.players):
            self.mock_clients.append(JsonLineClient(self.settings.socket_path, player_id))
        self._wait_for_players()
        self.controller = ControllerClient(self.settings.socket_path)
        return self._run_turn_loop(self._run_mock_turn)

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

    def _build_wake_message(self, player_id: int, view) -> str:
        """Compose the per-wake user prompt from the controller's get_view."""
        base = (
            f"You are scheduled now as TradeCraft P{player_id}. Read the game state and any "
            "step instruction, then use the TradeCraft tools; do not merely explain a move. "
            "After you complete your scheduled action — a successful trade proposal, a "
            "placement settlement+road, or any tool call that advances the game — stop "
            "immediately and return control to the runner by ending your reply without "
            "calling any further tool. During normal play, tradecraft_send_message is "
            "available for concrete negotiation such as stating resource needs or "
            "acceptable terms. If no productive legal action or worthwhile negotiation "
            "remains, do not call any tool — simply end your reply to end the turn. "
            "You must always produce an action (a tool call) or an explicit decision — an "
            "empty or silent reply is never acceptable."
        )
        if view is None:
            return base
        state = view.get("state") or {}
        instruction = view.get("instruction") or ""
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        events_json = json.dumps(view.get("events", []), ensure_ascii=False, separators=(",", ":"))
        return (
            base
            + instruction
            + "\n\n## Current game state\n"
            + "```json\n" + state_json + "\n```\n"
            + "## Events since your last turn\n"
            + "```json\n" + events_json + "\n```\n"
        )

    def _invoke_openclaw_agent(self, player_id: int) -> int:
        player = self.players[player_id]
        view = self.controller.get_view(player_id) if self.controller is not None else None
        message = self._build_wake_message(player_id, view)
        message_file = self.settings.match_dir / f"wake-p{player_id}.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        message_file.write_text(message, encoding="utf-8")
        command = self._openclaw_command(
            "agent",
            "--agent", str(self.settings.agent_id),
            "--session-key", self.settings.session_key,
            "--message-file", str(message_file),
            "--thinking", self.settings.thinking,
            "--timeout", str(self.settings.agent_timeout_seconds),
            "--json",
        )
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
                self._last_agent_stdout[player_id] = record["stdout"]
                if _is_no_reply(record["stdout"]):
                    self._record(
                        "agent_no_reply",
                        player_id=player_id,
                        attempt=attempt,
                    )
                    raise AgentNoReply(
                        f"Player {player_id} returned NO_REPLY on attempt {attempt}"
                    )
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
        """Scheduler-owned orchestration state machine.

        Phase 1 — placement: snake draft, each player a settlement→road pair.
        Phase 2 — play: max_rounds rounds, round-robin, ACTIONS_PER_ROUND wakes
        each, with pending-trade responder decisions interleaved between wakes.
        """
        total_calls = self._run_placement(invoke_player)
        total_calls += self._run_play(invoke_player)
        return self._settle_match(total_calls)

    def _run_placement(self, invoke_player) -> int:
        n = self.settings.players
        order = list(range(n)) + list(range(n - 1, -1, -1))  # snake draft
        target = {pid: 0 for pid in range(n)}
        total = 0
        for pid in order:
            target[pid] += 1
            total += self._drive_step(
                invoke_player, pid,
                lambda st, p=pid, t=target[pid]:
                    len(st["players"][p]["buildings"]) >= t,
                f"P{pid} settlement #{target[pid]}",
            )
            total += self._drive_step(
                invoke_player, pid,
                lambda st, p=pid, t=target[pid]:
                    len(st["players"][p]["roads"]) >= t,
                f"P{pid} road #{target[pid]}",
            )
        if self.controller is not None:
            self.controller.end_placement()
        return total

    def _run_play(self, invoke_player) -> int:
        n = self.settings.players
        total = 0
        for _round in range(1, self.settings.max_rounds + 1):
            if self.controller is not None:
                self.controller.start_round()
            for pid in range(n):
                for _ in range(ACTIONS_PER_ROUND):
                    state = self._read_state()
                    if state.get("game_over"):
                        return total
                    self._record(
                        "agent_scheduled",
                        player_id=pid,
                        phase=state.get("phase"),
                        round=state.get("round"),
                    )
                    total += self._resolve_pending_trade(invoke_player)
                    total += self._drive_play_action(invoke_player, pid)
            # One complete round just finished (every player took their
            # ACTIONS_PER_ROUND actions). Record the score snapshot so a
            # long-round match that stalls later can still settle on this
            # round's completed scores instead of the in-flight partial state.
            if self.controller is not None:
                self.controller.snapshot_round_scores()
        return total

    def _drive_play_action(self, invoke_player, player_id: int) -> int:
        try:
            calls = self._calls(player_id, invoke_player(player_id))
        except RuntimeError as exc:
            self._forfeit_player(player_id, exc)
            return 1
        self._after_wake(player_id)
        return calls

    def _after_wake(self, player_id: int):
        """Post-wake bookkeeping: deliver pending chat, advance event pointer."""
        state = self._read_state()
        self._deliver_pending_messages(state)
        if self.controller is not None and not state.get("game_over"):
            self.controller.advance(player_id)

    def _resolve_pending_trade(self, invoke_player) -> int:
        """Awaken the pending trade's responder to decide, then resolve it.

        The responder decision is an extra wake that does not consume the
        responder's action quota. Returns the number of agent calls spent.
        """
        state = self._read_state()
        pending = state.get("pending_trade")
        if not pending:
            return 0
        responder = int(pending["responder"])
        if self.settings.mode == "mock":
            if self.controller is not None:
                self.controller.resolve_trade(
                    "reject", "Mock responder declines this offer."
                )
            return 0
        try:
            calls = self._calls(responder, invoke_player(responder))
        except RuntimeError as exc:
            # forfeit auto-rejects the pending trade on the responder's behalf.
            self._forfeit_player(responder, exc)
            return 1
        self._after_wake(responder)
        self._resolve_trade_response(responder)
        return calls

    def _forfeit_player(self, player_id: int, exc: RuntimeError) -> int:
        """Record a scheduler-driven forfeit after an agent failure.

        The engine's forfeit only (a) counts the forfeit toward turn-health
        stats and (b) rejects a pending trade on this player's behalf. The
        scheduler itself owns advancing to the next actor, so this simply taps
        the engine and moves on. Returns a synthetic call count.
        """
        self._record(
            "agent_forfeited",
            player_id=player_id,
            reason="no_reply" if isinstance(exc, AgentNoReply) else "failure",
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        if self.controller is None:
            raise RuntimeError(
                f"No controller to forfeit player {player_id}: {exc}"
            ) from exc
        result = self.controller.forfeit(player_id)
        print(
            f"[TradeCraft] Forfeited player {player_id} after agent failure "
            f"({type(exc).__name__}: {str(exc)[:200]}) -> {result}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    def _resolve_trade_response(self, player_id: int) -> dict:
        """Parse the responder's JSON decision and submit it to the engine.

        The responder expresses its accept/reject decision as a JSON object in its
        final message text instead of calling a tool. On any parse failure the
        decision is treated as a rejection with a system-supplied reason.

        A rejected resolution (``ok: False``) is a normal in-game outcome — e.g.
        the proposer no longer holds the offered resources at execution time. The
        scheduler treats it as a responder forfeit instead of crashing the match.
        """
        stdout = self._last_agent_stdout.get(player_id, "")
        decision, reason = _extract_trade_decision(stdout)
        result = self.controller.resolve_trade(decision, reason)
        self._record(
            "trade_response",
            player_id=player_id,
            decision=decision,
            reason=reason,
            ok=result.get("ok"),
        )
        if not result.get("ok"):
            self._forfeit_player(
                player_id,
                RuntimeError(
                    f"trade resolution rejected: {result.get('error')}"
                ),
            )
        return result

    def _settle_match(self, total_calls: int) -> dict:
        """Explicitly settle a finished match and return the final state.

        The engine leaves ``game_over`` False after the last round so its socket
        loop keeps serving; the scheduler stops invoking players and calls this
        to compute final scores. After settling, wait for the engine to flush the
        final snapshot so the full settled state (round/max_rounds/winner/scores)
        is available to the caller.
        """
        if self.controller is None:
            raise RuntimeError(
                "No controller connection available to settle the match"
            )
        # A trade proposed on the final action of the last round has no further
        # responder wake to resolve it. The engine rejects settlement while a
        # pending trade is unresolved, so auto-reject any trailing trade before
        # settling (the match is over; the responder can never act again).
        state = self._read_state()
        if state.get("pending_trade") and not state.get("game_over"):
            self.controller.resolve_trade(
                "reject", "Match ended; pending trade auto-rejected."
            )
        final = self.controller.settle()
        self._record(
            "match_complete",
            winner=final.get("winner"),
            scores=final.get("scores"),
            total_agent_calls=total_calls,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = self._read_state()
            if state.get("game_over"):
                return state
            time.sleep(0.05)
        return {
            **self._read_state(),
            "game_over": True,
            "winner": final.get("winner"),
            "scores": final.get("scores"),
        }

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
                final_state = self._run_mock()
            else:
                self._start_gateways()
                self._wait_for_players()
                self._create_player_sessions()
                self.controller = ControllerClient(self.settings.socket_path)
                final_state = self._run_turn_loop(self._invoke_openclaw_agent)
            return final_state if final_state is not None else self._read_state()
        finally:
            for client in self.mock_clients:
                try:
                    client.close()
                except OSError:
                    pass
            if self.controller is not None:
                try:
                    self.controller.close()
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
        "--max-rounds",
        type=int,
        help="Override the configured number of rounds for this match",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Override the unified per-round/per-category action limit (default 3)",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        help="Override the first player Gateway port for this match",
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        help="Override the isolated OpenClaw profile root for this match",
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
        settings = load_settings(
            args.config,
            args.mode,
            args.game_id,
            args.seed,
            args.base_port,
            args.profile_root,
            args.max_rounds,
            args.limit,
        )
        if args.prepare_only:
            players = prepare_profiles(settings)
            print(f"Prepared {len(players)} player profiles under {settings.profile_root}")
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
            "teams": final_state.get("teams"),
            "team_scores": final_state.get("team_scores"),
            "team_winner": final_state.get("team_winner"),
            "legality": final_state.get("legality"),
            "artifacts": str(settings.match_dir),
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"TradeCraft runner failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
