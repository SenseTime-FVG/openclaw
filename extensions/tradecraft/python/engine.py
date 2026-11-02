#!/usr/bin/env python3
"""
TradeCraft Game Engine — Catan-style hex map resource game.

Runs as a Unix socket daemon supporting 3-4 players. Each player session
connects via Unix socket and takes turns. The engine manages map topology,
resource production (dice), building, trading, and victory conditions.

Usage:
  python engine.py --socket /tmp/tradecraft.sock --players 3 [--seed 42]
    parser.add_argument("--players", type=int, default=3, help="Number of players (3 or 4)")
"""

import argparse
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from map_generator import (
    generate_map, GameMap, HexTile, Vertex, Edge,
    TOTAL_ROUNDS, ACTIONS_PER_ROUND, ACTION_LIMIT, MAX_SETTLEMENTS, MAX_CITIES, MAX_ROADS,
    WAKE_ACTION_LIMIT, WAKE_TRADE_LIMIT, WAKE_CHAT_LIMIT,
    MIN_SETTLEMENT_DISTANCE, RESOURCES, DESERT,
    HEX_ROWS, CUBE_DIRS, BANK_RATE_DEFAULT, BANK_RATE_PORT,
)

from i18n import make_translator


TRADE_MASTER_REQUIRED_TRADES = 8
RESOURCE_TYCOON_REQUIRED_RESOURCES = 20
INITIAL_SETTLEMENTS = 2  # initial snake-draft settlement+road pairs per player


# ── Game State ─────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    player_id: int
    resources: dict[str, int] = field(default_factory=lambda: {r: 0 for r in RESOURCES})
    buildings: dict[str, int] = field(default_factory=dict)  # vertex_id → level (1-3)
    roads: set[str] = field(default_factory=set)  # set of edge_ids
    projects: list[dict] = field(default_factory=list)
    trades_completed: int = 0
    # Per-player placement bookkeeping: the settlement just placed whose
    # mandatory road is still pending. ``None`` means no pending placement.
    placement_vertex: Optional[str] = None
    placement_pairs: int = 0  # completed settlement+road pairs during initial placement
    total_actions: int = 0
    illegal_actions: int = 0
    # Snapshot of ``illegal_actions`` taken at the start of each wake (set by
    # ``_advance``). ``illegal_actions - illegal_baseline`` is this wake's
    # illegal/over-action usage; the engine reads it only to pick the tiered
    # over-action message -- the single ``illegal_actions`` counter stays the
    # one and only input to the scheduler's forfeit judgement.
    illegal_baseline: int = 0
    # Diagnostic-only subset of ``illegal_actions``: calls issued *after* this
    # wake's one scheduled action was already completed (the engine replies
    # ``action_complete`` / ``action_near_budget`` / ``action_over_budget``
    # plus ``turn_over``). Recorded so "spinning on a finished turn" can be
    # told apart from genuine rule rejections; never judged on its own (adding
    # it to ``illegal_actions`` would double-count).
    noop_actions: int = 0
    forfeits: int = 0
    # Responder-wake accounting (chat reply / trade response). Those wakes are
    # driven from the engine's responder threads rather than the scheduler, so
    # they keep their own reverse-accounting counters here; the runner merges
    # them into ``health.json`` so the reward's forfeit_ratio covers them too.
    responder_wakes: int = 0
    responder_forfeits: int = 0
    # True once this player completed their one scheduled action this wake.
    # Reset by ``_advance`` (the scheduler's per-wake boundary signal). A
    # further tool call while True is rejected as an over-action so the agent
    # learns to end its loop by calling no tool at all.
    acted_this_wake: bool = False
    # Per-wake quotas, reset by ``_advance``. Build and claim_project share
    # ``wake_actions_used`` so they are mutually exclusive; trade and chat get
    # their own small allowance to bound free negotiation.
    wake_actions_used: int = 0
    wake_trades_used: int = 0
    wake_chats_used: int = 0


@dataclass
class TradeProposal:
    proposer: int
    responder: int
    offer: dict[str, int]
    request: dict[str, int]


@dataclass
class ChatRequest:
    """A directed private message awaiting its target's single-turn reply.

    ``send_message`` is blocking: the initiator's socket call is held until the
    target's reply is parsed and returned as the ``send_message`` tool result.
    """
    sender: int
    target: int
    content: str
    message_id: int


@dataclass
class PlayerMessage:
    message_id: int
    sender: int
    recipients: list[int]
    content: str
    round_num: int
    kind: str = "system"


@dataclass(frozen=True)
class ActionRecord:
    action_id: int
    round_num: int
    phase: str
    actor: int
    action: str
    details: dict


@dataclass
class GameState:
    players: list[PlayerState]
    game_map: GameMap
    num_players: int = 3
    round_num: int = 1
    pending_chat: Optional[ChatRequest] = None
    game_over: bool = False
    winner: Optional[int] = None
    phase: str = "placement"
    # Production rolls for the current round, one per player, in roll order:
    # [{"player_id": 0, "roll": 8}, ...]. Each player rolls once, at their first
    # wake of the round, and every roll settles for the whole table.
    dice_this_round: list[dict] = field(default_factory=list)
    rolled_this_round: set = field(default_factory=set)
    # Resources as of each player's last wake. The scheduler uses the delta to
    # show "what changed since your last wake" at the top of the user prompt.
    resource_snapshots: dict = field(default_factory=dict)
    # Robber: the hex that produces nothing. Starts on the centre desert; moved
    # by whichever player the scheduler grants this round's robber opportunity.
    robber_hex: str = ""
    messages: list[PlayerMessage] = field(default_factory=list)
    action_history: list[ActionRecord] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    event_pointers: dict = field(default_factory=dict)
    # Per-round score snapshots (round_num -> per-player scores), recorded by
    # the scheduler once each complete round finishes. Long-round matches that
    # stall mid-round fall back to the last completed round's snapshot.
    round_scores: dict[int, list[int]] = field(default_factory=dict)
    # Team mode (optional): ``teams`` is a list of roster groups (each a list of
    # player ids), e.g. [[0, 3], [1, 2]]; ``team_of`` maps player_id -> team idx.
    # ``None``/empty means free-for-all (every player competes alone).
    teams: Optional[list[list[int]]] = None
    team_of: dict[int, int] = field(default_factory=dict)


# ── Engine ─────────────────────────────────────────────────────────────────

class TradeCraftEngine:
    """Game engine for TradeCraft, supporting 3-4 players."""

    def __init__(self, seed: int = 42, num_players: int = 3,
                 max_rounds: int = TOTAL_ROUNDS, limit: int = ACTION_LIMIT,
                 teams: Optional[list[list[int]]] = None,
                 map_seed: Optional[int] = None,
                 language: str = "en",
                 responder_config: Optional[dict] = None,
                 trade_timeout: float = 120.0,
                 max_illegal_per_wake: int = 3,
                 wake_action_limit: int = WAKE_ACTION_LIMIT,
                 wake_trade_limit: int = WAKE_TRADE_LIMIT,
                 wake_chat_limit: int = WAKE_CHAT_LIMIT):
        if num_players not in (3, 4):
            raise ValueError(f"num_players must be 3 or 4, got {num_players}")
        if max_rounds < 1:
            raise ValueError(f"max_rounds must be positive, got {max_rounds}")
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        if teams is not None:
            teams = [list(group) for group in teams]
            team_of = {}
            seen = set()
            for team_idx, group in enumerate(teams):
                for pid in group:
                    if not (isinstance(pid, int) and not isinstance(pid, bool)
                            and 0 <= pid < num_players):
                        raise ValueError(f"team roster pid {pid!r} out of range for {num_players} players")
                    if pid in seen:
                        raise ValueError(f"player {pid} appears in more than one team")
                    seen.add(pid)
                    team_of[pid] = team_idx
            if len(seen) != num_players:
                raise ValueError(
                    f"teams must cover every player exactly once; got rosters for {sorted(seen)}"
                )
        else:
            team_of = {}

        self.num_players = num_players
        self.max_rounds = max_rounds
        self.limit = limit
        self.wake_action_limit = wake_action_limit
        self.wake_trade_limit = wake_trade_limit
        self.wake_chat_limit = wake_chat_limit
        # Per-wake illegal/over-action budget; mirrors the scheduler's
        # ``maxIllegalActionsPerWake`` (passed via --max-illegal-per-wake) so the
        # tiered over-action message can never disagree with the forfeit rule.
        self.max_illegal_per_wake = max(0, int(max_illegal_per_wake))
        self.game_map = generate_map(seed, total_rounds=max_rounds, map_seed=map_seed,
                                     num_players=num_players)
        self.state = GameState(
            players=[PlayerState(i) for i in range(num_players)],
            game_map=self.game_map,
            num_players=num_players,
            teams=teams,
            team_of=team_of,
            robber_hex=self.game_map.robber_start,
        )
        self._rng = random.Random(seed)
        self._dice_idx = 0
        self._t = make_translator(language)
        # Blocking-trade support: a threading.Lock guarding state mutations shared
        # between the socket loop and the trade responder thread, plus the
        # responder-wake config (openclaw invocation) supplied by the scheduler.
        self._lock = threading.Lock()
        self.responder_config = responder_config
        self.trade_timeout = trade_timeout

    def handle_action(self, action: str, params: dict, player_id: int) -> dict:
        result = self._handle_action_inner(action, params, player_id)
        return self._account_action(action, result, player_id)

    def _account_action(self, action: str, result: dict, player_id: int) -> dict:
        """Tally per-player action health (total/illegal) for every dispatched action.

        Pure accounting: the engine records how many actions a player attempted
        and how many were rejected (``ok:false``). It no longer interprets
        consecutive failures or pushes the turn forward — the scheduler owns
        pacing, forfeit, and advance decisions.
        """
        if action == "view_game_state":
            return result
        if not (isinstance(player_id, int) and not isinstance(player_id, bool)
                and 0 <= player_id < self.state.num_players):
            return result
        player = self.state.players[player_id]
        player.total_actions += 1
        if not result.get("ok"):
            player.illegal_actions += 1
        return result

    def _over_action_error(self, player: PlayerState, base_key: str) -> str:
        """Tiered message for an over-action call, keyed off this wake's usage.

        ``used`` counts the calls that hit the per-wake budget (rule rejections
        *and* over-actions) off the same single ``illegal_actions`` counter the
        scheduler reads, so the wording can never disagree with the forfeit
        judgement. The current call is included because ``_account_action`` has
        not tallied it yet when this message is built:

          * ``used > budget``  -> ``action_over_budget`` (this call triggers the
            forfeit; the provisional charge is no longer released);
          * ``used == budget`` -> ``action_near_budget`` (last allowed call);
          * otherwise          -> the plain ``base_key`` (normal end-of-turn cue).
        """
        used = player.illegal_actions - player.illegal_baseline + 1
        budget = self.max_illegal_per_wake
        if used > budget:
            return self._t("action_over_budget", budget=budget, used=used)
        if used == budget:
            return self._t("action_near_budget", budget=budget, used=used)
        return self._t(base_key)

    def _all_quotas_used(self, player: PlayerState) -> bool:
        """True when this wake has spent every per-wake allowance."""
        return (player.wake_actions_used >= self.wake_action_limit
                and player.wake_trades_used >= self.wake_trade_limit
                and player.wake_chats_used >= self.wake_chat_limit)

    def _quota_exhausted_result(self, player: PlayerState, key: str, limit: int) -> dict:
        """Reject a call whose per-wake quota is spent (diagnostic, no advance)."""
        return {
            "ok": False,
            "error": self._t(key, limit=limit),
            "turn_over": self._all_quotas_used(player),
        }

    def _attach_all_quotas_cue(self, player: PlayerState, result: dict) -> None:
        """Add the end-of-turn cue once every per-wake allowance is spent."""
        if isinstance(result.get("result"), dict) and self._all_quotas_used(player):
            result["result"]["turn_over"] = True
            result["result"].setdefault("turn_instruction", self._t("turn_complete"))

    def _begin_responder_wake(self, player_id: int) -> int:
        """Pre-charge one responder wake (chat reply / trade response).

        Mirrors the scheduler's reverse accounting: the wake counts as one
        advance plus one provisional forfeit. ``_end_responder_wake`` releases
        the provisional charge only when the responder finished legally and
        stayed within ``max_illegal_per_wake``; timeouts, subprocess errors,
        unparsable output and missing replies keep it.

        Returns this player's ``illegal_actions`` before the wake, for the
        per-wake delta check.
        """
        with self._lock:
            player = self.state.players[player_id]
            player.responder_wakes += 1
            player.responder_forfeits += 1
            return int(player.illegal_actions)

    def _end_responder_wake(self, player_id: int, illegal_before: int, ok: bool) -> None:
        """Release the provisional responder forfeit for a wake that ended right."""
        with self._lock:
            player = self.state.players[player_id]
            over_budget = (
                int(player.illegal_actions) - int(illegal_before)
            ) > self.max_illegal_per_wake
            if ok and not over_budget and player.responder_forfeits > 0:
                player.responder_forfeits -= 1

    def forfeit_player(self, player_id: int) -> dict:
        """Record a scheduler-driven forfeit; no turn advance happens here.

        The scheduler decides when to forfeit a stuck/crashed/silent player and
        calls this to count it toward turn-health stats. Trades are resolved
        inside the proposer's own call, so there is never a trade left waiting
        on the forfeiting player. Advancing to the next actor is entirely the
        scheduler's job after this returns.
        """
        if self.state.game_over:
            return {"ok": False, "error": self._t("game_over")}
        self.state.players[player_id].forfeits += 1
        if (self.state.pending_chat is not None
                and player_id == self.state.pending_chat.target):
            return self.resolve_chat("[forfeited]")
        return {"ok": True, "result": {"forfeited": True, "phase": self.state.phase}}

    def _handle_action_inner(self, action: str, params: dict, player_id: int) -> dict:
        if player_id < 0 or player_id >= self.state.num_players:
            return {"ok": False, "error": self._t("invalid_player", pid=player_id)}

        # Viewing the final state remains available after the game ends.
        if action == "view_game_state":
            return {"ok": True, "result": self._view_game_state(player_id, include_map=True)}
        if self.state.game_over:
            return {"ok": False, "error": self._t("game_over")}
        if self.state.phase == "play" and self.state.round_num > self.max_rounds:
            # Passive settlement: the final round has completed but the match has
            # not been settled. The scheduler calls ``settle`` explicitly after it
            # has stopped invoking players.
            return {"ok": False, "error": self._t("await_settlement")}

        # Capture ownership before dispatch because the action can advance the
        # turn or round before its result is returned.
        action_round = self.state.round_num
        action_phase = self.state.phase
        player = self.state.players[player_id]

        # Turn checks for placement phase
        if self.state.phase == "placement":
            return self._complete_action(
                self._handle_placement(action, params, player_id, player),
                action,
                params,
                player_id,
                action_round,
                action_phase,
            )

        # Turn checks for play phase
        # (No current-player or actions-remaining gate here — the scheduler owns
        # pacing. The engine only validates the board-level legality of the move.
        # The one rule the engine now owns is "one action per wake": once a player
        # has completed their scheduled action, any further tool call is rejected.)

        # Private negotiation and trade proposals are *free* steps: neither
        # consumes the wake's action nor earns the end-of-turn cue by itself,
        # but both are quota-limited so one wake cannot loop on them. Both stay
        # available after the scheduled action has already been committed.
        if action == "send_message":
            if player.wake_chats_used >= self.wake_chat_limit:
                player.noop_actions += 1
                return self._quota_exhausted_result(
                    player, "chat_quota_exhausted", self.wake_chat_limit,
                )
            result = self._complete_action(
                self._send_message(player, params, player_id),
                action,
                params,
                player_id,
                action_round,
                action_phase,
                consumes=False,
            )
            if result.get("ok"):
                player.wake_chats_used += 1
                self._attach_all_quotas_cue(player, result)
            return result

        if action == "propose_trade" and player.wake_trades_used >= self.wake_trade_limit:
            player.noop_actions += 1
            return self._quota_exhausted_result(
                player, "trade_quota_exhausted", self.wake_trade_limit,
            )

        if player.acted_this_wake and action != "propose_trade":
            # Over-action: the wake's single scheduled action is already done.
            # Trade proposals are exempt (a free step), so they reach the quota
            # check above even after that action was committed. Count the call
            # in the diagnostic noop subset; the rule itself runs off the single
            # illegal_actions tally (design §7.4.1).
            player.noop_actions += 1
            return {
                "ok": False,
                "error": self._over_action_error(player, "action_complete"),
                "turn_over": True,
            }

        if action in ("build", "claim_project"):
            if player.wake_actions_used >= self.wake_action_limit:
                player.noop_actions += 1
                return {
                    "ok": False,
                    "error": self._over_action_error(player, "action_complete"),
                    "turn_over": True,
                }

        if action == "build":
            result = self._build(player, params, player_id)
        elif action == "propose_trade":
            result, proposal, details = self._prepare_trade(params, player_id)
            if proposal is not None:
                result = self._resolve_trade_blocking(proposal)
        elif action == "claim_project":
            result = self._claim_project(player, params, player_id)
        else:
            return {
                "ok": False,
                "error": self._t("unknown_action", action=action),
            }
        if result.get("ok"):
            if action in ("build", "claim_project"):
                player.acted_this_wake = True
                player.wake_actions_used += 1
            elif action == "propose_trade":
                player.wake_trades_used += 1
        return self._complete_action(
            result,
            action,
            params,
            player_id,
            action_round,
            action_phase,
        )

    def _complete_action(
        self,
        result: dict,
        action: str,
        params: dict,
        player_id: int,
        round_num: int,
        phase: str,
        consumes: bool = True,
    ) -> dict:
        """Record a committed action before attaching its post-action state."""
        if result.get("ok"):
            self._record_action(
                action,
                params,
                result,
                player_id,
                round_num,
                phase,
            )
            # Proactive end-of-turn cue, only for actions that actually consume
            # the wake ("one action per wake"): tell the player to summarize and
            # stop now instead of only rejecting a redundant next tool call.
            # Free steps (private chat) and failed calls never carry it.
            player = self.state.players[player_id]
            if consumes and isinstance(result.get("result"), dict):
                if player.wake_actions_used >= self.wake_action_limit:
                    result["result"]["turn_over"] = True
                    result["result"]["turn_instruction"] = self._t("turn_complete")
            self._attach_all_quotas_cue(player, result)
        return self._with_action_state(result, player_id)

    def _record_action(
        self,
        action: str,
        params: dict,
        result: dict,
        player_id: int,
        round_num: int,
        phase: str,
    ) -> None:
        """Store the normalized public facts of one successful game action."""
        body = result["result"]
        details = {}
        if action == "build":
            details["type"] = body["built"]
            if "edge_id" in body:
                details["edge_id"] = body["edge_id"]
            if "vertex_id" in body:
                details["vertex_id"] = body["vertex_id"]
            if "level" in body:
                details["level"] = body["level"]
        elif action == "propose_trade":
            details = {
                "target": params.get(
                    "target", (player_id + 1) % self.state.num_players
                ),
                "offer": dict(params["offer"]),
                "request": dict(params["request"]),
            }
        elif action == "respond_trade":
            feedback = body["response_message"]
            details = {
                "response": params["action"],
                "other_player": feedback["recipients"][0],
                "feedback_message_id": feedback["id"],
            }
            if body.get("trade_accepted"):
                details["offer"] = dict(body["trade_accepted"]["offer"])
                details["request"] = dict(body["trade_accepted"]["request"])
        elif action == "send_message":
            message = body["message_sent"]
            details = {
                "message_id": message["id"],
                "recipients": list(message["recipients"]),
                "content": message["content"],
            }
        elif action == "claim_project":
            details = {"project_id": body["claimed"]}

        self.state.action_history.append(ActionRecord(
            action_id=len(self.state.action_history) + 1,
            round_num=round_num,
            phase=phase,
            actor=player_id,
            action=action,
            details=details,
        ))
        self._append_state_event(action, body, details, player_id, round_num, phase)

    def _push_event(
        self, event_type: str, actor, round_num: int, phase: str, details: dict
    ) -> None:
        """Append one state-affecting event to the global event log."""
        self.state.events.append({
            "seq": len(self.state.events),
            "round": round_num,
            "phase": phase,
            "type": event_type,
            "actor": actor,
            "details": details,
        })

    def _append_state_event(
        self, action: str, body: dict, details: dict, player_id: int,
        round_num: int, phase: str,
    ) -> None:
        """Record only state-affecting actions into the event log.

        Chat, trade proposals, and unsettled reject responses do not change map
        ownership or resources, so they are intentionally excluded.
        """
        if action == "build" or action == "claim_project":
            self._push_event(action, player_id, round_num, phase, dict(details))
        elif action == "respond_trade" and body.get("trade_accepted"):
            trade = body["trade_accepted"]
            feedback = body["response_message"]
            self._push_event("trade", player_id, round_num, phase, {
                "offer": dict(trade["offer"]),
                "request": dict(trade["request"]),
                "proposer": feedback["recipients"][0],
                "responder": player_id,
            })

    def _with_action_state(self, result: dict, player_id: int) -> dict:
        """Attach one post-action dynamic snapshot without repeating the static map."""
        if result.get("ok") and isinstance(result.get("result"), dict):
            result["result"]["state"] = self._view_game_state(
                player_id, include_map=False
            )
        return result

    # ── Placement Phase ────────────────────────────────────────────────

    def _handle_placement(self, action: str, params: dict, player_id: int, player: PlayerState) -> dict:
        """Initial placement. Board-level pairing only: settlement then its road.

        No snake-draft order or "whose step" check — the scheduler decides who is
        woken and how many times. The engine only enforces that, once a player
        places a settlement, their next placement build must be its adjacent road
        (and vice-versa), tracked per player via ``placement_vertex``.
        """
        if action != "build":
            return {
                "ok": False,
                "error": self._t("placement_only_build"),
            }

        if player.placement_pairs >= INITIAL_SETTLEMENTS:
            # Over-action during placement: this player's initial placement is
            # already done. Same class of defect as the play-phase over-action,
            # so count it in the diagnostic noop subset and escalate on the same
            # budget -- spinning must not be penalty-free (design §7.4.1).
            player.noop_actions += 1
            return {
                "ok": False,
                "error": self._over_action_error(player, "placement_complete"),
                "turn_over": True,
            }

        btype = params.get("type", "")
        if btype == "settlement":
            if player.placement_vertex is not None:
                return {
                    "ok": False,
                    "error": self._t("settlement_awaiting_road"),
                }
            vid = params.get("vertex_id", "")
            result = self._build_settlement(player, vid, placement=True)
            if result["ok"]:
                player.placement_vertex = vid
                vertex = self.game_map.vertex_by_id(vid)
                self._grant_initial_resources(player, vertex)
                result["result"]["phase"] = self.state.phase
            return result
        elif btype == "road":
            if player.placement_vertex is None:
                return {"ok": False, "error": self._t("place_settlement_first")}
            result = self._build_road(player, params.get("edge_id", ""))
            if result["ok"]:
                # Settlement+road pair complete; clear the per-player pending marker.
                player.placement_vertex = None
                player.placement_pairs += 1
                result["result"]["phase"] = self.state.phase
            return result
        else:
            return {"ok": False, "error": self._t("placement_use_build")}

    def _grant_initial_resources(self, player: PlayerState, vertex: Vertex):
        """Grant resources from hexes adjacent to a newly placed settlement."""
        for hid in vertex.hexes:
            h = self.game_map.hex_by_id(hid)
            if h.resource != DESERT:
                player.resources[h.resource] = player.resources.get(h.resource, 0) + 1

    # ── Dice & Production ──────────────────────────────────────────────

    def _roll_dice(self) -> int:
        """Roll the dice for this round. Uses pre-determined sequence."""
        if self._dice_idx >= len(self.game_map.dice_sequence):
            return self._rng.randint(2, 12)
        roll = self.game_map.dice_sequence[self._dice_idx]
        self._dice_idx += 1
        return roll

    def _produce_resources(self, dice_roll: int):
        """Distribute resources based on dice roll."""
        for h in self.game_map.hexes:
            if h.number == dice_roll and h.resource != DESERT:
                # The robber blocks production on whatever hex it occupies.
                if h.id == self.state.robber_hex:
                    continue
                # Find vertices with buildings adjacent to this hex
                for v in self.game_map.vertices:
                    if h.id in v.hexes:
                        for pid, player in enumerate(self.state.players):
                            if v.id in player.buildings:
                                level = player.buildings[v.id]
                                amount = level  # L1=1, L2=2, L3=3
                                player.resources[h.resource] = player.resources.get(h.resource, 0) + amount

    def start_round(self) -> dict:
        """Open a new round; players roll at their own first wake.

        The engine does not roll here: production is per player
        (``roll_for_player``), so opening a round only resets the per-round
        dice bookkeeping. The scheduler calls this exactly once per round.
        """
        self.state.round_num += 1
        self.state.dice_this_round = []
        self.state.rolled_this_round = set()
        return {"round": self.state.round_num, "dice_this_round": []}

    def roll_for_player(self, player_id: int) -> dict:
        """Roll once for ``player_id`` and settle it for the whole table.

        Called by the scheduler at the player's first wake of the round. The
        roller has no privilege: every player whose buildings touch a hex with
        the rolled number gains resources at building level, unless the robber
        sits on that hex. A 7 produces nothing and grants the robber move to the
        roller, as in Catan.
        """
        if self.state.phase != "play":
            return {"ok": False,
                    "error": self._t("dice_only_in_play", phase=self.state.phase)}
        if player_id in self.state.rolled_this_round:
            return {"ok": False, "error": self._t("already_rolled", pid=player_id)}
        before = [dict(p.resources) for p in self.state.players]
        roll = self._roll_dice()
        self.state.rolled_this_round.add(player_id)
        if roll != 7:
            # A 7 has no hex on the board, so this is explicit rather than implied.
            self._produce_resources(roll)
        self.state.dice_this_round.append({"player_id": player_id, "roll": roll})
        deltas = []
        for p, prev in zip(self.state.players, before):
            changed = {
                r: p.resources.get(r, 0) - prev.get(r, 0)
                for r in set(p.resources) | set(prev)
                if p.resources.get(r, 0) != prev.get(r, 0)
            }
            deltas.append({"player_id": p.player_id, "resources": changed})
        robber_triggered = roll == 7
        self._push_event("production", player_id, self.state.round_num, "play", {
            "roller": player_id,
            "roll": roll,
            "deltas": deltas,
            "robber_hex": self.state.robber_hex,
            "robber_triggered": robber_triggered,
        })
        return {"ok": True, "result": {
            "player_id": player_id,
            "roll": roll,
            "deltas": deltas,
            "robber_hex": self.state.robber_hex,
            "robber_triggered": robber_triggered,
        }}

    def move_robber(self, player_id: int, hex_id: str) -> dict:
        """Move the robber to ``hex_id`` on behalf of ``player_id``.

        Called by the scheduler once it has parsed the robber target out of the
        robber-owner's end-of-turn prose. The engine owns the position state and
        the legality checks; the scheduler owns who is allowed to move it.
        """
        current_hex = self.state.robber_hex
        requested = hex_id.strip() if isinstance(hex_id, str) else ""

        def _reject(key: str, **fmt) -> dict:
            # A rejected move leaves the robber where it is. Record it anyway so
            # the owner sees the outcome on its next wake instead of silence.
            self._push_event(
                "robber_move_rejected", player_id,
                self.state.round_num, self.state.phase,
                {"requested_hex": requested, "reason": key,
                 "robber_hex": current_hex, "unchanged": True},
            )
            return {"ok": False, "error": self._t(key, **fmt)}

        if not requested:
            return _reject("robber_needs_hex")
        if requested not in {h.id for h in self.game_map.hexes}:
            return _reject("invalid_hex", hex_id=requested)
        if requested == current_hex:
            return _reject("robber_same_hex", hex_id=requested)
        previous = current_hex
        hex_id = requested
        self.state.robber_hex = hex_id
        self._push_event("robber_moved", player_id, self.state.round_num, self.state.phase, {
            "from": previous,
            "to": hex_id,
            "resource": self.game_map.hex_by_id(hex_id).resource,
        })
        return {"ok": True, "result": {"robber_moved": {"from": previous, "to": hex_id}}}

    def end_placement(self) -> dict:
        """Transition placement → play. Scheduler-triggered once all players
        have completed their initial settlement+road placements, in whatever
        order the scheduler chose."""
        if self.state.phase != "placement":
            return {"ok": False, "error": self._t("not_placement_phase", phase=self.state.phase)}
        self.state.phase = "play"
        self.state.round_num = 0
        return {"ok": True, "result": {"phase": "play"}}

    # ── Build Actions ──────────────────────────────────────────────────

    def _build(self, player: PlayerState, params: dict, player_id: int) -> dict:
        """Handle build action: road, settlement, or upgrade."""
        btype = params.get("type", "")

        if btype == "road":
            return self._build_road(player, params.get("edge_id", ""))
        elif btype == "settlement":
            return self._build_settlement(player, params.get("vertex_id", ""))
        elif btype == "upgrade":
            return self._upgrade_building(player, params.get("vertex_id", ""),
                                          params.get("to_level", 0))
        else:
            return {"ok": False, "error": (
                self._t("unknown_build_type", default=f"Unknown build type: {btype!r}. Use type=road, type=settlement, or type=upgrade.", btype=btype)
            )}

    def _build_road(self, player: PlayerState, edge_id: str) -> dict:
        """Place a road on an edge."""
        if not isinstance(edge_id, str) or not edge_id.strip():
            return {"ok": False, "error": self._t("road_needs_edge_id")}
        if edge_id not in {e.id for e in self.game_map.edges}:
            return {"ok": False, "error": self._t("invalid_edge", edge_id=edge_id)}

        if any(edge_id in p.roads for p in self.state.players):
            owner = next((p.player_id for p in self.state.players if edge_id in p.roads), None)
            return {"ok": False, "error": self._t("road_exists_owned", owner=owner)}

        # Check max roads
        if len(player.roads) >= MAX_ROADS:
            return {"ok": False, "error": self._t("max_roads", n=MAX_ROADS)}

        edge = self.game_map.edge_by_id(edge_id)

        # Road must connect to existing road or building
        if self.state.phase == "placement":
            # During placement the road must connect to THIS player's pending
            # settlement (their ``placement_vertex``), not older network pieces.
            placed_vertex = player.placement_vertex
            has_adj = placed_vertex is not None and placed_vertex in (edge.v1, edge.v2)
            if not has_adj:
                return {"ok": False, "error": self._t("road_connect_settlement")}
        else:
            has_adj = (
                edge.v1 in player.buildings or edge.v2 in player.buildings or
                any(eid in player.roads for eid in edge.adjacent_edges)
            )
            if not has_adj:
                return {"ok": False, "error": self._t("road_connect_road")}

        # Pay cost (free during placement)
        if self.state.phase == "play":
            cost = {"wood": 1, "brick": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": self._t("cannot_afford_road", cost=cost, have=player.resources)}
            self._deduct_cost(player.resources, cost)

        player.roads.add(edge_id)
        return {"ok": True, "result": {"built": "road", "edge_id": edge_id}}

    def _build_settlement(self, player: PlayerState, vertex_id: str, placement: bool = False) -> dict:
        """Place a settlement on a vertex."""
        if not isinstance(vertex_id, str) or not vertex_id.strip():
            return {"ok": False, "error": self._t("settlement_needs_vertex")}
        if vertex_id not in {v.id for v in self.game_map.vertices}:
            return {"ok": False, "error": self._t("invalid_vertex", vertex_id=vertex_id)}

        if self._vertex_owners(vertex_id):
            pid, level = self._vertex_owners(vertex_id)[0]
            return {"ok": False, "error": self._t("building_exists", level=level, pid=pid)}

        # Check max settlements
        if len(player.buildings) >= MAX_SETTLEMENTS:
            return {"ok": False, "error": self._t("max_settlements", n=MAX_SETTLEMENTS)}

        # Distance applies during both initial placement and normal play.
        conflict = self._distance_conflict(vertex_id)
        if conflict is not None:
            cpid, cvid = conflict
            return {"ok": False, "error": self._t(
                "too_close", pid=cpid, vid=cvid, dist=MIN_SETTLEMENT_DISTANCE
            )}

        # Road connectivity (not checked during initial placement)
        if not placement:
            connected = False
            vertex = self.game_map.vertex_by_id(vertex_id)
            for e in self.game_map.edges:
                if e.v1 == vertex_id or e.v2 == vertex_id:
                    if e.id in player.roads:
                        connected = True
                        break
            if not connected:
                return {"ok": False, "error": self._t("settlement_connect_road")}

        # Pay cost
        if not placement:
            cost = {"wood": 1, "brick": 1, "sheep": 1, "wheat": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": self._t("cannot_afford_settlement", cost=cost, have=player.resources)}
            self._deduct_cost(player.resources, cost)

        player.buildings[vertex_id] = 1
        return {"ok": True, "result": {"built": "settlement", "vertex_id": vertex_id,
                                        "level": 1}}

    def _upgrade_building(self, player: PlayerState, vertex_id: str, to_level: int) -> dict:
        """Upgrade a settlement to a city (L1→L2). Level 2 is the cap."""
        if not isinstance(vertex_id, str) or not vertex_id.strip():
            return {"ok": False, "error": self._t("upgrade_needs_vertex")}
        if vertex_id not in player.buildings:
            return {"ok": False, "error": self._t("no_building_here", vertex_id=vertex_id)}

        current_level = player.buildings[vertex_id]
        if to_level != current_level + 1:
            return {"ok": False, "error": self._t("upgrade_step", cur=current_level, nxt=current_level+1)}
        if to_level > 2:
            return {"ok": False, "error": self._t("max_level_2")}
        if current_level == 1:
            city_count = sum(1 for level in player.buildings.values() if level >= 2)
            if city_count >= MAX_CITIES:
                return {"ok": False, "error": self._t("max_cities", n=MAX_CITIES)}

        # Catan city: 2 wheat + 3 ore. Level 2 is the cap, so there is no
        # higher tier to price.
        cost = {"wheat": 2, "ore": 3}

        if not self._can_afford(player.resources, cost):
            return {"ok": False, "error": self._t("cannot_afford_upgrade", cost=cost, have=player.resources)}
        self._deduct_cost(player.resources, cost)

        player.buildings[vertex_id] = to_level
        return {"ok": True, "result": {"built": "upgrade", "vertex_id": vertex_id,
                                        "level": to_level}}

    def _distance_conflict(self, vertex_id: str):
        """Return ``(player_id, vertex_id)`` of a building too close, else None."""
        owners = self._vertex_owners(vertex_id)
        if owners:
            return (owners[0][0], vertex_id)

        visited = {vertex_id}
        fringe = {vertex_id}
        # Distances below the configured minimum are forbidden. With the
        # default of 2, adjacent vertices are rejected and distance 2 is legal.
        for _ in range(1, MIN_SETTLEMENT_DISTANCE):
            new_fringe = set()
            for vid in fringe:
                v = self.game_map.vertex_by_id(vid)
                for av in v.adjacent_vertices:
                    if av not in visited:
                        visited.add(av)
                        new_fringe.add(av)
                        # Check if any player has a building here
                        for p in self.state.players:
                            if av in p.buildings:
                                return (p.player_id, av)
            fringe = new_fringe
        return None

    def _check_distance(self, vertex_id: str) -> bool:
        """Check distance rule: no other building within 2 edges."""
        return self._distance_conflict(vertex_id) is None

    def _vertex_owners(self, vertex_id: str) -> list[tuple[int, int]]:
        """Return [(player_id, level)] for buildings at this vertex."""
        result = []
        for pid, p in enumerate(self.state.players):
            if vertex_id in p.buildings:
                result.append((pid, p.buildings[vertex_id]))
        return result

    # ── Trade ──────────────────────────────────────────────────────────

    def _prepare_trade(self, params: dict, player_id: int):
        """Validate a proposal; return ``(result, proposal, details)``.

        No game state is written. The returned proposal is a local value: the
        caller wakes the responder and settles it immediately, so there is no
        "pending trade" to persist, expose in views, or clean up on failure.
        """
        player = self.state.players[player_id]
        # Normalize first: an explicit 0 (or an omitted key) means "this resource
        # is not part of the trade", so a two-resource swap stays expressible
        # without padding all six resource keys with zeros.
        offer, offer_error = self._normalize_trade_map(params.get("offer", {}), "offer")
        if offer_error:
            return {"ok": False, "error": offer_error}, None, {}
        request, request_error = self._normalize_trade_map(params.get("request", {}), "request")
        if request_error:
            return {"ok": False, "error": request_error}, None, {}

        for r, n in offer.items():
            if player.resources.get(r, 0) < n:
                return {"ok": False, "error": self._t(
                    "insufficient_resource", r=r,
                    have=player.resources.get(r, 0), n=n)}, None, {}

        target_id = params.get("target", (player_id + 1) % self.state.num_players)
        details = {"target": target_id, "offer": dict(offer), "request": dict(request)}

        # ``target="system"`` is the bank/port exchange: an unconditional
        # conversion at a fixed ratio, no responder wake.
        if isinstance(target_id, str) and target_id.strip().lower() == "system":
            return self._bank_trade(player, offer, request, player_id), None, details

        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return {"ok": False, "error": self._t("trade_target_int")}, None, details
        if target_id < 0 or target_id >= self.state.num_players or target_id == player_id:
            return {"ok": False,
                    "error": self._t("invalid_trade_target", pid=target_id)}, None, details
        opponent = self.state.players[target_id]
        for r, n in request.items():
            if opponent.resources.get(r, 0) < n:
                return {"ok": False, "error": self._t(
                    "opponent_insufficient", r=r,
                    have=opponent.resources.get(r, 0), n=n)}, None, details

        proposal = TradeProposal(
            proposer=player_id, responder=target_id,
            offer=dict(offer), request=dict(request))
        return {"ok": True, "result": {"trade_proposed": {
            "proposer": player_id, "responder": target_id,
            "offer": offer, "request": request}}}, proposal, details

    def handle_propose_trade(self, params: dict, player_id: int) -> dict:
        """Validate, wake the responder, settle — one synchronous call.

        Called by the socket server OUTSIDE the state lock: validation and
        application each take the lock, the responder wake does not. Every
        responder failure collapses to a rejection, so the trade can never
        outlive this call.
        """
        player = self.state.players[player_id]
        if self.state.phase == "play" and player.wake_trades_used >= self.wake_trade_limit:
            player.noop_actions += 1
            blocked = self._quota_exhausted_result(
                player, "trade_quota_exhausted", self.wake_trade_limit,
            )
            return self._account_action("propose_trade", blocked, player_id)
        with self._lock:
            result, proposal, details = self._prepare_trade(params, player_id)
        if proposal is not None:
            result = self._resolve_trade_blocking(proposal)
        result = self._account_action("propose_trade", result, player_id)
        if result.get("ok"):
            with self._lock:
                player.wake_trades_used += 1
                self._record_action(
                    "propose_trade", details, result, player_id,
                    self.state.round_num, self.state.phase,
                )
            self._attach_all_quotas_cue(player, result)
        return result

    def _resolve_trade_blocking(self, proposal: TradeProposal) -> dict:
        """Wake the responder and apply its decision before returning.

        Missing responder config, subprocess errors, unparsable output and
        timeouts all become a rejection. The proposal is a local value, so there
        is nothing to unwind afterwards.
        """
        # Without a responder (mock/tests) the decision comes from the engine
        # attribute, defaulting to a rejection.
        decision = str(getattr(self, "mock_trade_decision", "reject"))
        reason = str(getattr(self, "mock_trade_reason", "(system) no responder configured"))
        if self.responder_config:
            try:
                decision, reason = self._run_trade_responder(proposal)
            except Exception as exc:  # noqa: BLE001
                decision = "reject"
                reason = f"(system) responder failed: {type(exc).__name__}: {exc}"
        if decision not in ("accept", "reject"):
            decision, reason = "reject", reason or "(system) invalid responder decision"
        with self._lock:
            return self._apply_trade(proposal, decision, reason)

    def _port_rate(self, player: PlayerState) -> int:
        """Bank ratio for this player: 3:1 with a port building, else 4:1."""
        ports = set(self.game_map.ports)
        if any(vid in ports for vid in player.buildings):
            return BANK_RATE_PORT
        return BANK_RATE_DEFAULT

    def _bank_trade(self, player: PlayerState, offer: dict, request: dict,
                    player_id: int) -> dict:
        """Execute a bank/port exchange: ``rate`` of one resource for 1 of another.

        Unconditional in the sense that no counterparty has to agree — but the
        offer must match the player's current ratio exactly, and must be a
        single resource type (Catan's bank takes one kind at a time).
        """
        rate = self._port_rate(player)
        if len(offer) != 1:
            return {"ok": False, "error": self._t("bank_offer_single", rate=rate)}
        give_resource, give_amount = next(iter(offer.items()))
        if give_amount != rate:
            return {"ok": False, "error": self._t(
                "bank_offer_rate", rate=rate, got=give_amount, r=give_resource
            )}
        if len(request) != 1:
            return {"ok": False, "error": self._t("bank_request_single")}
        want_resource, want_amount = next(iter(request.items()))
        if want_amount != 1:
            return {"ok": False, "error": self._t("bank_request_one", got=want_amount)}
        if want_resource == give_resource:
            return {"ok": False, "error": self._t("bank_same_resource", r=give_resource)}
        if player.resources.get(give_resource, 0) < give_amount:
            return {"ok": False, "error": self._t(
                "insufficient_resource", r=give_resource,
                have=player.resources.get(give_resource, 0), n=give_amount)}

        player.resources[give_resource] -= give_amount
        player.resources[want_resource] = player.resources.get(want_resource, 0) + want_amount
        self._push_event("bank_trade", player_id, self.state.round_num, self.state.phase, {
            "give": {give_resource: give_amount},
            "get": {want_resource: want_amount},
            "rate": rate,
        })
        return {"ok": True, "result": {
            "bank_trade": {"gave": {give_resource: give_amount},
                           "got": {want_resource: want_amount},
                           "rate": rate}}}

    def _apply_trade(self, pt: TradeProposal, decision: str, reason: str) -> dict:
        """Apply the responder's decision to a resolved proposal (lock held)."""
        action_round = self.state.round_num
        action_phase = self.state.phase
        responder = pt.responder
        if decision == "accept":
            result = self._execute_trade(pt)
        else:
            result = {"ok": True, "result": {"trade_rejected": True}}
        if result.get("ok"):
            feedback = self._record_message(responder, [pt.proposer], reason, kind="trade")
            result["result"]["response_message"] = self._message_view(feedback)
            self._record_action(
                "respond_trade",
                {"action": decision, "message": reason},
                result,
                responder,
                action_round,
                action_phase,
            )
        return self._with_action_state(result, pt.proposer)

    def _execute_trade(self, pt: TradeProposal) -> dict:
        giver = self.state.players[pt.proposer]
        receiver = self.state.players[pt.responder]

        # Re-validate against the state at decision time.
        for r, n in pt.offer.items():
            if giver.resources.get(r, 0) < n:
                return {"ok": False, "error": (
                    f"Proposer no longer has enough {r}: have {giver.resources.get(r, 0)}, need {n}."
                )}
        for r, n in pt.request.items():
            if receiver.resources.get(r, 0) < n:
                return {"ok": False, "error": (
                    f"Responder no longer has enough {r}: have {receiver.resources.get(r, 0)}, need {n}."
                )}

        for r, n in pt.offer.items():
            giver.resources[r] -= n
            receiver.resources[r] = receiver.resources.get(r, 0) + n
        for r, n in pt.request.items():
            receiver.resources[r] -= n
            giver.resources[r] = giver.resources.get(r, 0) + n

        # ``trades_completed`` feeds the ``trade_master`` project. In team mode it
        # must only count cross-team trades; same-team resource swaps are legal
        # cooperation but would otherwise let teammates farm the achievement.
        if self._cross_team(pt.proposer, pt.responder):
            giver.trades_completed += 1
            receiver.trades_completed += 1

        return {"ok": True, "result": {
            "trade_accepted": {"offer": pt.offer, "request": pt.request}}}

    # ── Trade responder (synchronous) ──────────────────────────────────

    def _build_trade_responder_message(self, pt: TradeProposal) -> str:
        """Compose the responder's single-turn decision prompt for ``pt``."""
        view = self._get_view(pt.responder)
        state = view.get("state", {})
        events = view.get("events", [])
        return (
            self._t("trade_responder")
            + "\n\nOffer from P" + str(pt.proposer)
            + ": give " + json.dumps(dict(pt.offer), ensure_ascii=False)
            + ", receive " + json.dumps(dict(pt.request), ensure_ascii=False)
            + "\n\nCurrent game state (JSON):\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
            + "\n\nEvents since your last turn (JSON):\n"
            + json.dumps(events, ensure_ascii=False, indent=2)
        )

    @staticmethod
    def _extract_trade_decision(stdout: str):
        """Parse the responder's ``{decision, reason}`` from ``--json`` stdout.

        The OpenClaw ``--json`` CLI prints an envelope whose
        ``result.payloads[].text`` carry the assistant's visible reply; the
        decision JSON sits (typically in a fenced json block) inside that text.
        Any parse failure is treated as a rejection.
        """
        decision = "reject"
        reason = "(system) 无法从响应方输出解析出交易决策，按拒绝处理"
        text = ""
        if stdout:
            try:
                data = json.loads(stdout)
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict):
                result = data.get("result")
                payloads = result.get("payloads") if isinstance(result, dict) else None
                if isinstance(payloads, list):
                    text = "\n".join(
                        p.get("text", "")
                        for p in payloads
                        if isinstance(p, dict) and isinstance(p.get("text"), str)
                    ).strip()
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
            d = str(obj.get("decision", "")).strip().lower()
            if d not in ("accept", "reject"):
                continue
            decision = d
            reason = str(obj.get("reason", "")).strip() or "(system) 响应方未提供决策理由"
            break
        return decision, reason

    @staticmethod
    def _extract_chat_reply(stdout: str):
        """Parse the chat responder's ``{reply, reason}`` from ``--json`` stdout."""
        text = ""
        if stdout:
            try:
                data = json.loads(stdout)
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict):
                result = data.get("result")
                payloads = result.get("payloads") if isinstance(result, dict) else None
                if isinstance(payloads, list):
                    text = "\n".join(
                        p.get("text", "")
                        for p in payloads
                        if isinstance(p, dict) and isinstance(p.get("text"), str)
                    ).strip()
        return text

    def _run_trade_responder(self, pt: TradeProposal):
        """Wake the responder (single-turn OpenClaw); return (decision, reason).

        Runs without the state lock. Only setup errors raise; timeouts and
        unparsable output fall back to a rejection via _extract_trade_decision.
        """
        responder_id = pt.responder
        illegal_before = self._begin_responder_wake(responder_id)
        message = self._build_trade_responder_message(pt)

        cfg = self.responder_config
        responder_cfg = cfg["players"][str(responder_id)]
        message_dir = cfg.get("message_dir") or tempfile.gettempdir()
        msg_file = os.path.join(message_dir, f"traderespond-p{responder_id}.txt")
        os.makedirs(os.path.dirname(msg_file) or ".", exist_ok=True)
        with open(msg_file, "w", encoding="utf-8") as fh:
            fh.write(message)

        command = (
            list(cfg["command"])
            + [
                "agent",
                "--agent", str(responder_cfg.get("agent_id", cfg.get("agent_id", "main"))),
                "--session-key", responder_cfg["session_key"],
                "--message-file", msg_file,
                "--thinking", str(cfg.get("thinking", "low")),
                "--timeout", str(cfg.get("timeout", 300)),
                "--json",
            ]
        )
        selected_api_env = responder_cfg.get("api_key_env", "")
        env = os.environ.copy()
        env["OPENCLAW_STATE_DIR"] = cfg["state_dir"]
        env["OPENCLAW_CONFIG_PATH"] = cfg["config_path"]
        for _other_pid, other in cfg["players"].items():
            oenv = other.get("api_key_env", "")
            if oenv and oenv != selected_api_env:
                env.pop(oenv, None)

        t0 = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                command,
                cwd=cfg.get("cwd"),
                env=env,
                capture_output=True,
                text=True,
                timeout=int(cfg.get("timeout", 300)) + 30,
            )
            stdout = proc.stdout or ""
            print(f"[TradeCraft] trade-responder p{responder_id}: rc={proc.returncode} "
                  f"elapsed={time.time()-t0:.1f}s stderr={proc.stderr[:200]!r}", flush=True)
        except subprocess.TimeoutExpired:
            stdout = ""
            timed_out = True
            print(f"[TradeCraft] trade-responder p{responder_id}: TIMEOUT after {time.time()-t0:.1f}s", flush=True)

        decision, reason = self._extract_trade_decision(stdout)
        print(f"[TradeCraft] trade-responder p{responder_id}: decision={decision} reason={reason[:80]!r}", flush=True)
        # Same reverse-accounting spec as the scheduler's scheduled wakes: the
        # provisional forfeit stays unless this wake produced a parseable
        # decision and stayed within the per-wake illegal budget.
        parsed = bool(reason) and not str(reason).startswith("(system)")
        self._end_responder_wake(responder_id, illegal_before, (not timed_out) and parsed)
        return decision, reason

    def resolve_chat(self, reply: str) -> dict:
        """Deliver the target's reply to the pending chat and clear it."""
        pc = self.state.pending_chat
        if pc is None:
            return {"ok": False, "error": "No pending chat."}
        reply_text = (reply or "").strip() or "[no reply]"
        source = self.state.messages[pc.message_id - 1] if 0 < pc.message_id <= len(self.state.messages) else None
        feedback = self._record_message(pc.target, [pc.sender], reply_text, kind="chat-reply")
        self.state.pending_chat = None
        result = {
            "reply": {
                "from": pc.target,
                "content": reply_text,
                "message": self._message_view(feedback),
            }
        }
        if source is not None:
            result["message_sent"] = self._message_view(source)
        return {"ok": True, "result": result}

    def _build_chat_responder_message(self, target_id: int) -> str:
        """Compose the target's single-turn reply prompt for a pending chat."""
        view = self._get_view(target_id)
        state = view.get("state", {})
        events = view.get("events", [])
        pc = self.state.pending_chat
        sender = pc.sender if pc is not None else None
        directive = (
            "You received a private message and the sender is waiting for your reply. "
            "Do NOT call any tool to respond - just answer in your own words. Your visible "
            "reply is delivered verbatim to the sender; your thinking is never shared."
        )
        return (
            directive
            + "\n\nFrom player: P" + str(sender)
            + "\n\nCurrent game state (JSON):\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
            + "\n\nEvents since your last turn (JSON):\n"
            + json.dumps(events, ensure_ascii=False, indent=2)
        )

    def _run_chat_responder(self, initiator_id: int) -> dict:
        """Wake the chat target (single-turn OpenClaw) and resolve the chat."""
        with self._lock:
            pending = self.state.pending_chat
            if pending is None:
                return {"ok": False, "error": "No pending chat."}
            target_id = pending.target
            message = self._build_chat_responder_message(target_id)
        illegal_before = self._begin_responder_wake(target_id)
        cfg = self.responder_config
        responder_cfg = cfg["players"][str(target_id)]
        message_dir = cfg.get("message_dir") or tempfile.gettempdir()
        msg_file = os.path.join(message_dir, "chatrespond-p" + str(target_id) + ".txt")
        os.makedirs(os.path.dirname(msg_file) or ".", exist_ok=True)
        with open(msg_file, "w", encoding="utf-8") as fh:
            fh.write(message)
        command = (
            list(cfg["command"])
            + [
                "agent",
                "--agent", str(responder_cfg.get("agent_id", cfg.get("agent_id", "main"))),
                "--session-key", responder_cfg["session_key"],
                "--message-file", msg_file,
                "--thinking", str(cfg.get("thinking", "low")),
                "--timeout", str(cfg.get("timeout", 300)),
                "--json",
            ]
        )
        selected_api_env = responder_cfg.get("api_key_env", "")
        env = os.environ.copy()
        env["OPENCLAW_STATE_DIR"] = cfg["state_dir"]
        env["OPENCLAW_CONFIG_PATH"] = cfg["config_path"]
        for _other_pid, other in cfg["players"].items():
            oenv = other.get("api_key_env", "")
            if oenv and oenv != selected_api_env:
                env.pop(oenv, None)
        t0 = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                command, cwd=cfg.get("cwd"), env=env,
                capture_output=True, text=True,
                timeout=int(cfg.get("timeout", 300)) + 30,
            )
            stdout = proc.stdout or ""
            print("[TradeCraft] chat-responder p" + str(target_id) + ": rc=" + str(proc.returncode)
                  + " elapsed=" + ("%.1f" % (time.time()-t0)) + "s stderr=" + repr(proc.stderr[:200]), flush=True)
        except subprocess.TimeoutExpired:
            stdout = ""
            timed_out = True
            print("[TradeCraft] chat-responder p" + str(target_id) + ": TIMEOUT after " + ("%.1f" % (time.time()-t0)) + "s", flush=True)
        if timed_out:
            reply = "[timed out]"
        else:
            reply = self._extract_chat_reply(stdout) or "[no reply]"
        print("[TradeCraft] chat-responder p" + str(target_id) + ": reply=" + repr(reply[:80]), flush=True)
        # Reverse accounting, same spec as the scheduler: keep the provisional
        # forfeit unless the responder actually produced a reply and stayed
        # within the per-wake illegal budget.
        replied = (not timed_out) and reply not in ("[timed out]", "[no reply]", "[agent error]")
        self._end_responder_wake(target_id, illegal_before, replied)
        with self._lock:
            return self.resolve_chat(reply)

    def wait_for_chat_resolution(self, initiator_id: int) -> dict:
        """Spawn the chat responder thread and block until the reply arrives."""
        if not self.responder_config:
            return {"ok": True, "result": {"reply": {"from": None, "content": "", "blocking_disabled": True}}}
        holder: dict = {}
        done = threading.Event()

        def _run() -> None:
            try:
                holder["result"] = self._run_chat_responder(initiator_id)
            except Exception as exc:  # noqa: BLE001
                holder["result"] = {
                    "ok": True,
                    "result": {"reply": {"from": None, "content": "[agent error]"}},
                }
            finally:
                done.set()

        threading.Thread(target=_run, name="chat-responder", daemon=True).start()
        if not done.wait(timeout=self.trade_timeout):
            with self._lock:
                self.state.pending_chat = None
            return {"ok": True, "result": {"reply": {"from": None, "content": "[timed out]"}}}
        return holder.get("result", {"ok": True, "result": {"reply": {"from": None, "content": "[no reply]"}}})

    # ── Messages & Projects ────────────────────────────────────────────

    @staticmethod
    def _message_view(message: PlayerMessage) -> dict:
        return {
            "id": message.message_id,
            "from": message.sender,
            "recipients": list(message.recipients),
            "content": message.content,
            "round": message.round_num,
            "kind": message.kind,
        }

    def _record_message(
        self, player_id: int, recipients: list[int], content: str, kind: str = "system"
    ) -> PlayerMessage:
        message = PlayerMessage(
            message_id=len(self.state.messages) + 1,
            sender=player_id,
            recipients=recipients,
            content=content.strip(),
            round_num=self.state.round_num,
            kind=kind,
        )
        self.state.messages.append(message)
        return message

    def _send_message(self, player: PlayerState, params: dict, player_id: int) -> dict:
        content = params.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return {"ok": False, "error": self._t("message_required")}
        target_id = params.get("target")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return {"ok": False, "error": self._t("trade_target_int")}
        if target_id < 0 or target_id >= self.state.num_players or target_id == player_id:
            return {"ok": False, "error": self._t("invalid_trade_target", pid=target_id)}
        message = self._record_message(player_id, [target_id], content, kind="chat")
        self.state.pending_chat = ChatRequest(
            sender=player_id, target=target_id,
            content=content.strip(), message_id=message.message_id)
        return {"ok": True, "result": {
            "message_sent": self._message_view(message), "target": target_id}}

    def _project_defs(self) -> list:
        """Single source of truth for the five public projects.

        Used by both ``_claim_project`` (validation + error text) and
        ``_view_game_state`` (``eligible`` / ``progress``), so the state can
        never advertise a condition the engine does not enforce.
        """
        def level2(p) -> int:
            return sum(1 for lvl in p.buildings.values() if lvl >= 2)

        return [
            {"id": "trade_master", "name": "Trade Master", "points": 1,
             "description": f"Complete {TRADE_MASTER_REQUIRED_TRADES}+ trades",
             "condition": lambda p: p.trades_completed >= TRADE_MASTER_REQUIRED_TRADES,
             "requirement": f"complete {TRADE_MASTER_REQUIRED_TRADES}+ trades",
             "progress": lambda p: f"{p.trades_completed} trades completed",
             "progress_compact": lambda p: f"{p.trades_completed}/{TRADE_MASTER_REQUIRED_TRADES}"},
            {"id": "build_master", "name": "Building Master", "points": 2,
             "description": "Own 5+ buildings",
             "condition": lambda p: len(p.buildings) >= 5,
             "requirement": "own 5+ buildings",
             "progress": lambda p: f"{len(p.buildings)} buildings owned",
             "progress_compact": lambda p: f"{len(p.buildings)}/5"},
            {"id": "tech_pioneer", "name": "Tech Pioneer", "points": 2,
             "description": "Own 2+ level-2 buildings",
             "condition": lambda p: level2(p) >= 2,
             "requirement": "own 2+ level-2 buildings",
             "progress": lambda p: f"{level2(p)} level-2 buildings owned",
             "progress_compact": lambda p: f"{level2(p)}/2"},
            {"id": "resource_tycoon", "name": "Resource Tycoon", "points": 1,
             "description": f"Hold {RESOURCE_TYCOON_REQUIRED_RESOURCES}+ resources",
             "condition": lambda p: sum(p.resources.values()) >= RESOURCE_TYCOON_REQUIRED_RESOURCES,
             "requirement": f"hold {RESOURCE_TYCOON_REQUIRED_RESOURCES}+ resources",
             "progress": lambda p: f"{sum(p.resources.values())} resources held",
             "progress_compact": lambda p: f"{sum(p.resources.values())}/{RESOURCE_TYCOON_REQUIRED_RESOURCES}"},
            {"id": "road_king", "name": "Road King", "points": 2,
             "description": "Longest road ≥ 6",
             "condition": lambda p: self._longest_road(p) >= 6,
             "requirement": "have a longest road of 6+ segments",
             "progress": lambda p: f"longest road is {self._longest_road(p)} segments",
             "progress_compact": lambda p: f"{self._longest_road(p)}/6"},
        ]

    def _claim_project(self, player: PlayerState, params: dict, player_id: int) -> dict:
        pid = params.get("project_id", "")
        projects = self._project_defs()

        project = next((pr for pr in projects if pr["id"] == pid), None)
        if not project:
            return {"ok": False, "error": self._t("unknown_project", pid=pid)}

        # Check already claimed
        for pstate in self.state.players:
            for pr in pstate.projects:
                if pr["id"] == pid:
                    return {"ok": False, "error": self._t("project_claimed", pid=pstate.player_id)}

        if not project["condition"](player):
            return {"ok": False, "error": self._t(
                "conditions_not_met",
                name=project["name"],
                req=project["requirement"],
                progress=project["progress"](player),
            )}

        player.projects.append({"id": pid, "name": project["name"], "points": project["points"]})
        return {"ok": True, "result": {"claimed": pid}}

    def _longest_road(self, player: PlayerState) -> int:
        """Compute the longest continuous road for a player."""
        if not player.roads:
            return 0
        # Build an edge-labelled graph so the score counts road segments, not
        # visited vertices, and still handles branches and cycles correctly.
        graph = defaultdict(list)
        for eid in player.roads:
            e = self.game_map.edge_by_id(eid)
            graph[e.v1].append((eid, e.v2))
            graph[e.v2].append((eid, e.v1))

        # DFS to find longest path
        best = 0
        def dfs(node, used_edges):
            nonlocal best
            best = max(best, len(used_edges))
            for eid, nb in graph[node]:
                if eid not in used_edges:
                    dfs(nb, used_edges | {eid})

        for start in graph:
            dfs(start, set())
        return best

    # ── Turn Management ────────────────────────────────────────────────
    #
    # The engine deliberately has NO end_turn / consume_action / advance logic.
    # Who acts, in what order, and for how many actions is the scheduler's sole
    # responsibility. The engine only exposes start_round() (dice production)
    # and end_placement() (phase switch) as scheduler-driven hooks, plus
    # settle() for final scoring.

    def _end_game(self):
        self.state.game_over = True
        scores = self._calculate_scores()
        max_score = max(scores)
        winners = [i for i, s in enumerate(scores) if s == max_score]
        self.state.winner = winners[0] if len(winners) == 1 else None

    def _team_scores(self, scores: list[int]) -> Optional[list[float]]:
        """Merge per-player scores into per-team scores; None when team mode off.

        Uses the team **mean** (not the sum) so a team's score keeps the same
        magnitude as a single player's — the settlement normalization shares
        ``max_settlement_score`` between solo and team modes with no extra logic.
        """
        if not self.state.teams or scores is None:
            return None
        return [
            sum(scores[pid] for pid in team) / len(team)
            for team in self.state.teams
        ]

    def _team_winner(self, team_scores: list[float]) -> Optional[int]:
        """Winning team index from mean team scores (None on a tie)."""
        best = max(team_scores)
        winners = [i for i, s in enumerate(team_scores) if s == best]
        return winners[0] if len(winners) == 1 else None

    def _settlement_result(self) -> dict:
        """The settled-state dict shared by settle() and snapshot fallback."""
        scores = self._calculate_scores()
        result: dict = {
            "game_over": True,
            "winner": self.state.winner,
            "scores": scores,
            "legality": self._legality_view(),
        }
        if self.state.teams:
            team_scores = self._team_scores(scores)
            result["teams"] = [list(t) for t in self.state.teams]
            result["team_scores"] = team_scores
            result["team_winner"] = self._team_winner(team_scores)
        return result

    def settle(self) -> dict:
        """Explicitly settle a finished match. Scheduler-triggered.

        The scheduler decides when the match is over (it owns round pacing) and
        calls this to compute final scores. Idempotent. Defensive guards only:
        cannot settle before play, before all rounds are played, or with a
        pending trade unresolved.
        """
        if self.state.game_over:
            return {"ok": True, "result": self._settlement_result()}
        if self.state.phase != "play":
            return {"ok": False, "error": self._t("cannot_settle_before_play", phase=self.state.phase)}
        if self.state.round_num < self.max_rounds:
            return {
                "ok": False,
                "error": self._t(
                    "game_not_finished",
                    round_num=self.state.round_num,
                    max_rounds=self.max_rounds,
                ),
            }
        self._end_game()
        return {"ok": True, "result": self._settlement_result()}

    def _cross_team(self, a: int, b: int) -> bool:
        """True when ``a`` and ``b`` belong to different teams (or team mode off)."""
        if not self.state.teams:
            return True
        return self.state.team_of.get(a) != self.state.team_of.get(b)

    def _calculate_scores(self) -> list[int]:
        scores = []
        for p in self.state.players:
            building_score = sum(l for l in p.buildings.values())
            project_score = sum(pr["points"] for pr in p.projects)
            scores.append(building_score + project_score)
        return scores

    def snapshot_round_scores(self) -> dict:
        """Record this round's per-player score snapshot. Scheduler-triggered.

        Called by the scheduler exactly once per complete round (after every
        player has taken their scheduled actions). Long-round matches that stall
        mid-round later settle against the last completed round's snapshot.
        """
        scores = self._calculate_scores()
        self.state.round_scores[self.state.round_num] = scores
        return {"ok": True, "result": {"round": self.state.round_num, "scores": scores}}

    def _latest_completed_round_scores(self) -> Optional[list[int]]:
        """Scores from the most recently completed round, or None if none yet."""
        if not self.state.round_scores:
            return None
        last = max(self.state.round_scores)
        return list(self.state.round_scores[last])

    @staticmethod
    def _winner_from_scores(scores: Optional[list[int]]) -> Optional[int]:
        if not scores:
            return None
        best = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        return winners[0] if len(winners) == 1 else None

    def _legality_view(self) -> dict:
        """Per-role action-legality counters (whole-table, route 2)."""
        return {
            "total_actions": [int(p.total_actions) for p in self.state.players],
            "illegal_actions": [int(p.illegal_actions) for p in self.state.players],
            "noop_actions": [int(p.noop_actions) for p in self.state.players],
            "forfeits": [int(p.forfeits) for p in self.state.players],
            "responder_wakes": [int(p.responder_wakes) for p in self.state.players],
            "responder_forfeits": [int(p.responder_forfeits) for p in self.state.players],
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _can_afford(self, resources: dict, cost: dict) -> bool:
        return all(resources.get(resource, 0) >= amount
                   for resource, amount in cost.items())

    def _deduct_cost(self, resources: dict, cost: dict):
        for resource, amount in cost.items():
            resources[resource] = resources.get(resource, 0) - amount

    @staticmethod
    def _normalize_trade_map(resource_map: object, label: str):
        """Normalize one side of a trade into a compact, strictly positive map.

        Zero and ``None`` amounts are dropped rather than rejected: tool schemas
        let models pad a resource map with zeros, and "0 wood" means exactly the
        same thing as omitting ``wood``. Negative or non-integer amounts stay
        errors, and a side that ends up empty is rejected.
        """
        if not isinstance(resource_map, dict):
            return None, f"{label} must be an object mapping resource names to amounts."
        valid_resources = set(RESOURCES)
        cleaned: dict = {}
        for resource, amount in resource_map.items():
            if resource not in valid_resources:
                return None, f"Unknown resource in {label}: {resource}."
            if amount is None:
                continue
            if isinstance(amount, bool) or not isinstance(amount, int):
                return None, f"{label}.{resource} must be an integer."
            if amount == 0:
                continue
            if amount < 0:
                return None, f"{label}.{resource} must not be negative."
            cleaned[resource] = amount
        if not cleaned:
            return None, f"Must specify at least one positive amount in {label}."
        return cleaned, None

    def _validate_trade_maps(self, offer: object, request: object) -> Optional[str]:
        """Validate engine-bound resource maps independently of tool schemas."""
        _offer, offer_error = self._normalize_trade_map(offer, "offer")
        if offer_error:
            return offer_error
        _request, request_error = self._normalize_trade_map(request, "request")
        return request_error

    # ── State Serialization ────────────────────────────────────────────

    def _compact_map_view(self) -> dict:
        return {
            "hexes": {
                h.id: [h.resource, h.number]
                for h in self.game_map.hexes
            },
            "vertices": {
                v.id: list(v.hexes)
                for v in self.game_map.vertices
            },
            "edges": {
                e.id: [e.v1, e.v2]
                for e in self.game_map.edges
            },
            "ports": list(self.game_map.ports),
        }

    def _expansion_targets_view(self, player_id: int) -> dict:
        settlement_ids = []
        road_ids = []
        player = self.state.players[player_id]

        # These are topology-only expansion targets. Never filter them by
        # affordability: agents need unaffordable targets for resource planning.
        # The engine reports each player's own legal targets regardless of whose
        # turn it is — pacing/selection is the scheduler's job.
        if self.state.phase == "placement":
            if player.placement_vertex is None:
                for v in self.game_map.vertices:
                    if not self._vertex_owners(v.id) and self._check_distance(v.id):
                        settlement_ids.append(v.id)
            else:
                placed_vertex = player.placement_vertex
                for e in self.game_map.edges:
                    occupied = any(e.id in p.roads for p in self.state.players)
                    if not occupied and placed_vertex in (e.v1, e.v2):
                        road_ids.append(e.id)
        else:
            for v in self.game_map.vertices:
                if v.id not in player.buildings:
                    owners = [pid for pid, _ in self._vertex_owners(v.id)]
                    if player_id not in owners and self._check_distance(v.id):
                        connected = any(
                            e.id in player.roads
                            for e in self.game_map.edges
                            if e.v1 == v.id or e.v2 == v.id
                        )
                        if connected:
                            settlement_ids.append(v.id)
            for e in self.game_map.edges:
                if not any(e.id in p.roads for p in self.state.players):
                    connected = (
                        e.v1 in player.buildings or e.v2 in player.buildings or
                        any(eid in player.roads for eid in e.adjacent_edges)
                    )
                    if connected:
                        road_ids.append(e.id)

        hex_by_id = {h.id: h for h in self.game_map.hexes}
        vertex_by_id = {v.id: v for v in self.game_map.vertices}
        edge_by_id = {e.id: e for e in self.game_map.edges}
        settlements = {
            vertex_id: [
                [hex_by_id[hex_id].resource, hex_by_id[hex_id].number]
                for hex_id in vertex_by_id[vertex_id].hexes
            ]
            for vertex_id in settlement_ids
        }
        roads = {
            edge_id: [edge_by_id[edge_id].v1, edge_by_id[edge_id].v2]
            for edge_id in road_ids
        }
        return {"settlements": settlements, "roads": roads}

    # ── Per-wake recommendation shortlist ──────────────────────────────

    RECOMMENDATION_LIMIT = 8

    def _vertices_near_opponents(self, player_id: int, hops: int = 3) -> set:
        """Vertices within ``hops`` edges of any opponent building."""
        vertex_by_id = {v.id: v for v in self.game_map.vertices}
        starts = [
            vid
            for p in self.state.players
            if p.player_id != player_id
            for vid in p.buildings
        ]
        near: set = set()
        for start in starts:
            seen = {start}
            frontier = {start}
            for _ in range(hops):
                step = set()
                for vid in frontier:
                    for adjacent in vertex_by_id[vid].adjacent_vertices:
                        if adjacent not in seen:
                            seen.add(adjacent)
                            step.add(adjacent)
                near |= step
                frontier = step
        return near

    def _recommendations(self, player_id: int) -> list:
        """Rank this player's rule-legal moves for the current wake.

        Candidates come from ``_expansion_targets_view`` — so occupancy, the
        minimum settlement distance (own *and* opponent buildings) and the
        own-network connectivity rule are all enforced there, once — plus
        upgrades on the player's own buildings. Nothing here relaxes a rule;
        this only ranks what is already legal.

        Score = resource variety + production pips + own-network connectivity,
        with a small bonus for staying away from opponent buildings. Ties break
        on (kind, id) so the same board always yields the same list.
        """
        player = self.state.players[player_id]
        targets = self._expansion_targets_view(player_id)
        hex_by_id = {h.id: h for h in self.game_map.hexes}
        vertex_by_id = {v.id: v for v in self.game_map.vertices}
        edge_by_id = {e.id: e for e in self.game_map.edges}
        near_opponent = self._vertices_near_opponents(player_id)
        edges_by_vertex: dict = {}
        for edge in self.game_map.edges:
            edges_by_vertex.setdefault(edge.v1, []).append(edge.id)
            edges_by_vertex.setdefault(edge.v2, []).append(edge.id)

        def pips(vertex_id: str) -> int:
            return sum(
                hex_by_id[hex_id].number or 0
                for hex_id in vertex_by_id[vertex_id].hexes
            )

        def variety(vertex_id: str) -> int:
            return len({
                hex_by_id[hex_id].resource
                for hex_id in vertex_by_id[vertex_id].hexes
                if hex_by_id[hex_id].resource not in (None, "desert")
            })

        def resource_label(vertex_id: str) -> str:
            parts = []
            for hex_id in vertex_by_id[vertex_id].hexes:
                tile = hex_by_id[hex_id]
                if tile.resource in (None, "desert"):
                    continue
                parts.append(f"{tile.resource}{tile.number if tile.number is not None else ''}")
            return ", ".join(parts)

        candidates: list = []

        for vertex_id in targets["settlements"]:
            connectivity = sum(
                1 for edge_id in edges_by_vertex.get(vertex_id, [])
                if edge_id in player.roads
            ) + sum(
                1 for adjacent in vertex_by_id[vertex_id].adjacent_vertices
                if adjacent in player.buildings
            )
            score = (
                3.0 * variety(vertex_id)
                + pips(vertex_id) / 12.0
                + 2.0 * connectivity
                + (0.0 if vertex_id in near_opponent else 1.0)
            )
            candidates.append({
                "kind": "settlement",
                "id": vertex_id,
                "score": score,
                "resources": resource_label(vertex_id),
            })

        for edge_id in targets["roads"]:
            edge = edge_by_id[edge_id]
            opens = sum(
                1 for endpoint in (edge.v1, edge.v2)
                if endpoint in targets["settlements"]
            )
            far = 0.0 if (edge.v1 in near_opponent and edge.v2 in near_opponent) else 0.5
            score = 2.0 * opens + (pips(edge.v1) + pips(edge.v2)) / 24.0 + far
            candidates.append({
                "kind": "road",
                "id": edge_id,
                "score": score,
                "v1": edge.v1,
                "v2": edge.v2,
            })

        cities = sum(1 for level in player.buildings.values() if level >= 2)
        if self.state.phase != "placement":
            for vertex_id, level in player.buildings.items():
                if level >= 2:
                    continue
                if level == 1 and cities >= MAX_CITIES:
                    continue
                to_level = 2
                cost = {"wheat": 2, "ore": 3}
                cost_label = ", ".join(f"{k}{v}" for k, v in cost.items())
                candidates.append({
                    "kind": "upgrade",
                    "id": f"{vertex_id}->{to_level}",
                    # No affordability term: the model must judge that itself.
                    "score": 2.0,
                    "vertex": vertex_id,
                    "level": to_level,
                    "cost": cost_label,
                })

        candidates.sort(key=lambda item: (-item["score"], item["kind"], item["id"]))
        return candidates[: self.RECOMMENDATION_LIMIT]

    def _recommendation_block(self, player_id: int) -> str:
        """The short, re-computed-every-wake recommendation list (localized)."""
        items = self._recommendations(player_id)
        if not items:
            return ""
        targets = self._expansion_targets_view(player_id)
        total = len(targets["settlements"]) + len(targets["roads"])
        lines = []
        for item in items:
            if item["kind"] == "settlement":
                line = self._t("rec_settlement", vid=item["id"], resources=item["resources"])
            elif item["kind"] == "road":
                line = self._t("rec_road", eid=item["id"], v1=item["v1"], v2=item["v2"])
            else:
                line = self._t(
                    "rec_upgrade",
                    vid=item["vertex"],
                    level=item["level"],
                    cost=item["cost"],
                )
            lines.append(f"- {line}")
        return (
            "\n\n## "
            + self._t("rec_title", pid=player_id, n=self.RECOMMENDATION_LIMIT)
            + "\n"
            + "\n".join(lines)
            + "\n"
            + self._t("rec_footer", total=total)
        )

    @staticmethod
    def _action_view(record: ActionRecord) -> dict:
        return {
            "action_id": record.action_id,
            "round_num": record.round_num,
            "phase": record.phase,
            "actor": record.actor,
            "action": record.action,
            "details": dict(record.details),
        }

    def _recent_actions_view(self) -> dict:
        if self.state.phase == "placement":
            current = [
                record for record in self.state.action_history
                if record.phase == "placement"
            ]
            previous = []
        else:
            current = [
                record for record in self.state.action_history
                if record.phase == "play"
                and record.round_num == self.state.round_num
            ]
            previous = [
                record for record in self.state.action_history
                if record.phase == "play"
                and record.round_num == self.state.round_num - 1
            ]
        return {
            "previous_round": [self._action_view(record) for record in previous],
            "current_round": [self._action_view(record) for record in current],
        }

    def _view_game_state(self, player_id: int, include_map: bool = True,
                         include_legality: bool = False) -> dict:
        players_view = []
        for p in self.state.players:
            buildings_view = []
            for vid, level in p.buildings.items():
                v = self.game_map.vertex_by_id(vid)
                buildings_view.append({
                    "vertex_id": vid, "level": level,
                    "hexes": list(v.hexes),
                })
            players_view.append({
                "player_id": p.player_id,
                "resources": dict(p.resources),
                "buildings": buildings_view,
                "roads": sorted(p.roads),
                "longest_road": self._longest_road(p),
                "projects": [dict(pr) for pr in p.projects],
                "trades_completed": p.trades_completed,
                "placement_vertex": p.placement_vertex,
            })

        # Projects carry their own eligibility + progress so the model does not
        # have to recompute five conditions from raw counters. ``eligible`` is
        # only ever true for a claim the engine would actually accept.
        me = self.state.players[player_id]
        projects_view = []
        for project in self._project_defs():
            claimed_by = self._project_claimed_by(project["id"])
            projects_view.append({
                "id": project["id"],
                "name": project["name"],
                "description": project["description"],
                "points": project["points"],
                "claimed_by": claimed_by,
                "eligible": claimed_by is None and bool(project["condition"](me)),
                "progress": project["progress_compact"](me),
            })

        scores = self._calculate_scores() if self.state.game_over else self._latest_completed_round_scores()
        teams = [list(t) for t in self.state.teams] if self.state.teams else None
        team_scores = self._team_scores(scores) if self.state.teams else None
        team_winner = self._team_winner(team_scores) if team_scores else None
        my_bank_rate = self._port_rate(me)

        view = {
            "round": self.state.round_num,
            "max_rounds": self.max_rounds,
            "dice_this_round": [dict(entry) for entry in self.state.dice_this_round],
            "rolled_this_round": sorted(self.state.rolled_this_round),
            # Robber position is always public. Its hex produces nothing.
            "robber_hex": self.state.robber_hex,
            "robber_on_desert": bool(
                self.state.robber_hex
                and self.game_map.hex_by_id(self.state.robber_hex).resource == DESERT
            ),
            # Bank ratio this player currently gets (3 with a port building).
            # ``my_bank_rate`` is the explicit per-player spelling; ``bank_rate``
            # is kept for existing consumers.
            "bank_rate": my_bank_rate,
            "my_bank_rate": my_bank_rate,
            "port_vertices": list(self.game_map.ports),
            "scoring": {
                "buildings": "Sum of building levels",
                "projects": "Sum of claimed project points",
            },
            "phase": self.state.phase,
            "players": players_view,
        }
        if include_map:
            view["map"] = self._compact_map_view()
        view.update({
            "projects": projects_view,
            "pending_chat": self._pending_chat_view(),
            "messages": [self._message_view(message) for message in self.state.messages],
            "recent_actions": self._recent_actions_view(),
            "expansion_targets": self._expansion_targets_view(player_id),
            "game_over": self.state.game_over,
            "winner": self.state.winner if self.state.game_over else self._winner_from_scores(scores),
            "scores": scores,
            "teams": teams,
            "team_scores": team_scores,
            "team_winner": team_winner,
            "my_player_id": player_id,
        })
        # What changed since this player's last wake (production, trades, ...),
        # so the scheduler can put recent resource changes at the top of the
        # prompt instead of making the model diff the event log.
        current = self.state.players[player_id].resources
        snapshot = self.state.resource_snapshots.get(player_id) or {}
        view["resource_delta_since_last_wake"] = {
            r: current.get(r, 0) - snapshot.get(r, 0)
            for r in set(current) | set(snapshot)
            if current.get(r, 0) != snapshot.get(r, 0)
        }
        # ``legality`` is a scheduler/audit side channel and is deliberately NOT
        # agent-facing: the model learns about rule violations from tool errors,
        # not from counters (README "Error feedback contract"). Only the
        # state.json snapshot opts in via ``include_legality``.
        if include_legality:
            view["legality"] = self._legality_view()
        return view

    def _project_claimed_by(self, pid: str) -> Optional[int]:
        for p in self.state.players:
            for pr in p.projects:
                if pr["id"] == pid:
                    return p.player_id
        return None

    def _pending_chat_view(self) -> Optional[dict]:
        pc = self.state.pending_chat
        if pc is None:
            return None
        return {"sender": pc.sender, "target": pc.target, "message_id": pc.message_id}

    def _step_instruction(self, player_id: int) -> str:
        """Rule-level guidance for THIS player, with no turn ordering whatsoever.

        The engine only knows board rules and the per-player placement pairing
        and pending-trade state. All turn/order/wake phrasing — "it's your turn",
        "wait for others" — is the scheduler's job; the engine returns only the
        rule-level "if you act, this is what the rule expects of you" guidance.
        """
        if self.state.phase == "placement":
            return self._placement_instruction(player_id) + self._recommendation_block(player_id)
        return self._round_notice() + self._recommendation_block(player_id)

    def _round_notice(self) -> str:
        """This round's production rolls plus the robber's public position.

        Each player rolls once per round and every roll settles for the whole
        table, so the engine states the rolls (with their rollers) once per view
        instead of leaving the model to reconstruct them from the event log. The
        robber line is emitted only while it sits off the desert (on the desert
        it blocks nothing).
        """
        parts = []
        if self.state.dice_this_round:
            entries = "; ".join(
                self._t(
                    "dice_roll_entry_seven" if entry["roll"] == 7 else "dice_roll_entry",
                    pid=entry["player_id"], roll=entry["roll"],
                )
                for entry in self.state.dice_this_round
            )
            parts.append(self._t("dice_notice_round", rolls=entries))
        elif self.state.phase == "play":
            parts.append(self._t("dice_none_yet"))
        if self.state.robber_hex:
            hex_obj = self.game_map.hex_by_id(self.state.robber_hex)
            if hex_obj.resource != DESERT:
                parts.append(self._t(
                    "robber_notice", hex=hex_obj.id, resource=hex_obj.resource,
                ))
        if not parts:
            return ""
        return "\n\n## " + "\n".join(parts)

    def _placement_instruction(self, player_id: int) -> str:
        """Per-player placement pairing: settlement first, then its adjacent road.

        No snake-draft order/step wording — only the rule that a player's next
        placement build must complete the current settlement+road pair.
        """
        if self.state.phase != "placement":
            return ""
        player = self.state.players[player_id]
        key = (
            "placement_need_settlement"
            if player.placement_vertex is None
            else "placement_need_road"
        )
        return (
            "\n\n## "
            + self._t("placement_title", pid=player_id)
            + "\n"
            + self._t("placement_intro", action=self._t(key))
        )

    def _get_view(self, player_id: int) -> dict:
        """Backend view for the scheduler: dynamic state + incremental events.

        Returns the latest ownership/runtime state (no static map topology, no
        chat, no recent-actions), the events appended since this player's
        pointer, and this player's engine-side step instruction. The scheduler
        advances the pointer after the player's turn / trade response, which
        naturally skips the events it participated in.
        """
        pointer = self.state.event_pointers.get(player_id, 0)
        state = self._view_game_state(player_id, include_map=False)
        state.pop("map", None)
        state.pop("recent_actions", None)
        state.pop("messages", None)
        return {
            "state": state,
            "events": list(self.state.events[pointer:]),
            "pointer": pointer,
            "instruction": self._step_instruction(player_id),
        }

    def _advance(self, player_id: int) -> dict:
        """Move a player's read pointer past every event seen this wake.

        Clears the per-wake action flag so the player can act again on the next
        wake, and re-baselines ``illegal_actions`` so the next wake's usage is
        ``illegal_actions - illegal_baseline``.
        """
        self.state.event_pointers[player_id] = len(self.state.events)
        player = self.state.players[player_id]
        player.acted_this_wake = False
        player.wake_actions_used = 0
        player.wake_trades_used = 0
        player.wake_chats_used = 0
        player.illegal_baseline = player.illegal_actions
        self.state.resource_snapshots[player_id] = dict(player.resources)
        return {"player_id": player_id, "pointer": len(self.state.events)}


# ── Unix Socket Server ─────────────────────────────────────────────────────

def recv_message(conn: socket.socket) -> Optional[dict]:
    buf = b""
    while True:
        try:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
            if b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                return json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        except Exception:
            return None


def send_message(conn: socket.socket, msg: dict):
    data = json.dumps(msg) + "\n"
    conn.sendall(data.encode("utf-8"))


def _write_snapshot(path: Optional[str], engine: TradeCraftEngine, sequence: int,
                    connected_players: list[int]):
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    state = engine._view_game_state(0, include_legality=True)
    state["_meta"] = {
        "sequence": sequence,
        "updated_at": time.time(),
        "connected_players": sorted(connected_players),
        "required_players": engine.num_players,
    }
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _append_event(path: Optional[str], event: dict):
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = {"timestamp": time.time(), **event}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _handle_controller(conn: socket.socket, engine: TradeCraftEngine,
                     msg: Optional[dict] = None):
    """Serve one scheduler/controller request: get_view, advance, or end_game.

    Returns ``None`` when the controller disconnected so the caller can drop it.
    ``msg`` may be supplied by a caller that already read the request, so the
    socket read happens outside the state lock.
    """
    if msg is None:
        msg = recv_message(conn)
        if msg is None:
            return None
    action = msg.get("action", "")
    params = msg.get("params", {}) if isinstance(msg.get("params"), dict) else {}
    pid = params.get("player_id")
    valid_pid = (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and 0 <= pid < engine.num_players
    )
    if action == "get_view":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": engine._t("invalid_player_id", pid=pid)})
            return True
        send_message(conn, {"ok": True, "result": engine._get_view(pid)})
    elif action == "start_round":
        # Scheduler-driven round reset (no rolls: each player rolls at their wake).
        send_message(conn, {"ok": True, "result": engine.start_round()})
    elif action == "roll_for_player":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": engine._t("invalid_player_id", pid=pid)})
            return True
        send_message(conn, engine.roll_for_player(pid))
    elif action == "end_placement":
        # Scheduler-driven transition from placement to play.
        send_message(conn, engine.end_placement())
    elif action == "snapshot_round_scores":
        # Scheduler-driven per-round score snapshot (one per complete round).
        send_message(conn, engine.snapshot_round_scores())
    elif action == "move_robber":
        # Scheduler parses the robber target out of the owner's end-of-turn
        # prose and asks the engine to apply it.
        hex_id = params.get("hex_id", "")
        if not valid_pid:
            send_message(conn, {"ok": False, "error": engine._t("invalid_player_id", pid=pid)})
            return True
        result = engine.move_robber(pid, hex_id)
        send_message(conn, result)
        return True
    elif action == "advance":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": engine._t("invalid_player_id", pid=pid)})
            return True
        send_message(conn, {"ok": True, "result": engine._advance(pid)})
    elif action == "forfeit_player":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": engine._t("invalid_player_id", pid=pid)})
            return True
        send_message(conn, engine.forfeit_player(pid))
    elif action == "end_game":
        send_message(conn, engine.settle())
    else:
        send_message(conn, {
            "ok": False,
            "error": engine._t("unknown_controller_action", action=action),
        })
    return True


def run_server(socket_path: str, seed: int = 42, num_players: int = 3,
               state_path: Optional[str] = None, event_log_path: Optional[str] = None,
               max_rounds: int = TOTAL_ROUNDS, limit: int = ACTION_LIMIT,
               teams: Optional[list[list[int]]] = None,
               map_seed: Optional[int] = None,
               language: str = "en",
               responder_config: Optional[dict] = None,
               trade_timeout: float = 120.0,
               max_illegal_per_wake: int = 3):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    engine = TradeCraftEngine(
        seed=seed,
        num_players=num_players,
        max_rounds=max_rounds,
        limit=limit,
        teams=teams,
        map_seed=map_seed,
        language=language,
        responder_config=responder_config,
        trade_timeout=trade_timeout,
        max_illegal_per_wake=max_illegal_per_wake,
    )
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chmod(socket_path, 0o660)
    server.listen(num_players + 1)

    conn_by_pid: dict[int, socket.socket] = {}
    pid_by_conn: dict[int, int] = {}
    sequence = 0

    _write_snapshot(state_path, engine, sequence, [])
    _append_event(event_log_path, {
        "type": "server_started",
        "seed": seed,
        "players": num_players,
        "max_rounds": max_rounds,
        "socket_path": socket_path,
    })

    print(f"[TradeCraft] Listening on {socket_path} "
          f"(seed={seed}, players={num_players}, max_rounds={max_rounds})", flush=True)

    try:
        import select
        while len(conn_by_pid) < engine.num_players:
            readable, _, _ = select.select([server], [], [], None)
            if server in readable:
                conn, _ = server.accept()
                registration = recv_message(conn)
                params = registration.get("params", {}) if registration else {}
                pid = params.get("player_id") if registration else None
                valid_registration = (
                    registration is not None
                    and registration.get("action") == "register_player"
                    and isinstance(pid, int)
                    and not isinstance(pid, bool)
                    and 0 <= pid < engine.num_players
                    and pid not in conn_by_pid
                )
                if not valid_registration:
                    send_message(conn, {
                        "ok": False,
                        "error": engine._t("invalid_registration"),
                    })
                    conn.close()
                    continue
                conn_by_pid[pid] = conn
                pid_by_conn[id(conn)] = pid
                send_message(conn, {"ok": True, "result": {"registered_player": pid}})
                _write_snapshot(state_path, engine, sequence, list(conn_by_pid))
                _append_event(event_log_path, {
                    "type": "player_connected",
                    "player_id": pid,
                    "connected_players": sorted(conn_by_pid),
                })
                print(f"[TradeCraft] Player {pid} connected.", flush=True)

        print(f"[TradeCraft] All {engine.num_players} players connected. Game starting.",
              flush=True)
        # ── Concurrent service: one thread per connection ─────────────────
        # A blocking trade/chat responder wait must occupy only the initiator's
        # own thread, never the shared I/O loop; otherwise one player's
        # propose_trade/send_message would freeze every other player's socket
        # (and the controller's) for the duration of the wait, orphaning their
        # in-flight tool calls. The plugin client keeps at most one request in
        # flight per connection (it serializes on a per-client tail), so a
        # per-connection thread keeps FIFO response pairing valid without any
        # protocol change.
        state_lock = engine._lock          # engine state + snapshot/event writes
        conn_lock = threading.Lock()       # conn_by_pid
        sequence_box = [0]

        def _persist_action(action, params, result, pid):
            with state_lock:
                sequence_box[0] += 1
                seq = sequence_box[0]
                with conn_lock:
                    connected = list(conn_by_pid)
                _write_snapshot(state_path, engine, seq, connected)
                _append_event(event_log_path, {
                    "type": "action",
                    "sequence": seq,
                    "player_id": pid,
                    "action": action,
                    "params": params,
                    "ok": result.get("ok", False),
                    "error": result.get("error"),
                    "phase": engine.state.phase,
                    "round": engine.state.round_num,
                    "game_over": engine.state.game_over,
                })

        def _serve_player(conn, pid):
            try:
                while not engine.state.game_over:
                    msg = recv_message(conn)
                    if msg is None:
                        print(f"[TradeCraft] Player {pid} disconnected.", flush=True)
                        with state_lock:
                            engine.state.game_over = True
                            sequence_box[0] += 1
                            with conn_lock:
                                connected = list(conn_by_pid)
                            _write_snapshot(state_path, engine, sequence_box[0], connected)
                            _append_event(event_log_path, {
                                "type": "player_disconnected",
                                "player_id": pid,
                                "sequence": sequence_box[0],
                            })
                        break
                    if "error" in msg:
                        send_message(conn, {"ok": False, "error": msg["error"]})
                        continue

                    action = msg.get("action", "")
                    params = msg.get("params", {})
                    if action == "propose_trade":
                        # Trade resolution is one synchronous call: validation,
                        # responder wake and settlement all belong to it, and no
                        # state survives it. It manages its own lock scope so the
                        # responder wake never holds the shared state lock.
                        try:
                            result = engine.handle_propose_trade(params, pid)
                        except Exception as exc:
                            result = {"ok": False, "error": engine._t("internal_error", exc=exc)}
                    else:
                        # Validate + commit under the lock (fast, no blocking I/O).
                        with state_lock:
                            try:
                                result = engine.handle_action(action, params, pid)
                            except Exception as exc:
                                result = {"ok": False, "error": engine._t("internal_error", exc=exc)}
                            blocking = (
                                action == "send_message"
                                and result.get("ok")
                                and engine.responder_config
                            )
                        # Blocking chat wait runs on THIS player's thread only,
                        # and never under the lock.
                        if blocking:
                            result = engine.wait_for_chat_resolution(pid)
                    _persist_action(action, params, result, pid)
                    send_message(conn, result)
            except Exception as exc:  # noqa: BLE001
                print(f"[TradeCraft] Player {pid} thread error: {exc!r}", flush=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        def _serve_controller(conn):
            try:
                while not engine.state.game_over:
                    msg = recv_message(conn)
                    if msg is None:
                        with state_lock:
                            _append_event(event_log_path, {"type": "controller_disconnected"})
                        return
                    # Controller actions can mutate game state; run them under
                    # the same lock as player actions, but never while blocking
                    # on a socket read.
                    with state_lock:
                        _handle_controller(conn, engine, msg)
                        sequence_box[0] += 1
                        with conn_lock:
                            connected = list(conn_by_pid)
                        _write_snapshot(state_path, engine, sequence_box[0], connected)
            except Exception as exc:  # noqa: BLE001
                print(f"[TradeCraft] Controller thread error: {exc!r}", flush=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        for _conn in list(conn_by_pid.values()):
            _pid = pid_by_conn.get(id(_conn))
            if _pid is None:
                continue
            threading.Thread(target=_serve_player, args=(_conn, _pid),
                             name=f"tradecraft-player-{_pid}", daemon=True).start()

        controller_conn = None
        while not engine.state.game_over:
            readable, _, _ = select.select([server], [], [], 1.0)
            if server not in readable:
                continue
            client, _ = server.accept()
            registration = recv_message(client)
            if (registration is not None
                    and registration.get("action") == "register_controller"
                    and controller_conn is None):
                controller_conn = client
                send_message(client, {
                    "ok": True,
                    "result": {"registered_controller": True},
                })
                with state_lock:
                    _append_event(event_log_path, {"type": "controller_connected"})
                print("[TradeCraft] Controller connected.", flush=True)
                threading.Thread(target=_serve_controller, args=(client,),
                                 name="tradecraft-controller", daemon=True).start()
            else:
                send_message(client, {
                    "ok": False,
                    "error": engine._t("controller_connected"),
                })
                client.close()

        # The response to the final action already contains the final state.
        # Do not send unsolicited messages: the client protocol is FIFO and
        # has no request ids, so a broadcast could be mistaken for a response.
        _write_snapshot(state_path, engine, sequence_box[0], list(conn_by_pid))
        _append_event(event_log_path, {
            "type": "game_over",
            "sequence": sequence_box[0],
            "winner": engine.state.winner,
            "scores": engine._calculate_scores(),
        })

    except KeyboardInterrupt:
        pass
    finally:
        for c in conn_by_pid.values():
            try: c.close()
            except: pass
        server.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        print("[TradeCraft] Shutdown.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeCraft Game Engine")
    parser.add_argument("--socket", default="/tmp/tradecraft.sock")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--players", type=int, default=3, help="Number of players (3 or 4)")
    parser.add_argument("--max-rounds", type=int, default=TOTAL_ROUNDS)
    parser.add_argument("--limit", type=int, default=ACTION_LIMIT)
    parser.add_argument("--teams", help="Optional team rosters as JSON, e.g. '[[0,3],[1,2]]'")
    parser.add_argument("--map-seed", type=int, default=None,
                        help="Optional independent map-layout seed (None falls back to --seed)")
    parser.add_argument("--state-file", help="Write the latest game state as atomic JSON")
    parser.add_argument("--event-log", help="Append engine lifecycle and action events as JSONL")
    parser.add_argument("--language", default="en", help="Agent-facing message language (en/zh/fr/es/ru/ar)")
    parser.add_argument("--responder-config", help="JSON file with the blocking-trade responder-wake config")
    parser.add_argument("--trade-timeout", type=float, default=120.0,
                        help="Max seconds to wait for a trade responder (fallback: auto-reject)")
    parser.add_argument("--max-illegal-per-wake", type=int, default=3,
                        help="Per-wake illegal/over-action budget; above it the over-action hint "
                             "escalates to action_over_budget (must match the scheduler's "
                             "maxIllegalActionsPerWake)")
    args = parser.parse_args()
    teams = json.loads(args.teams) if args.teams else None
    responder_config = None
    if args.responder_config:
        with open(args.responder_config, encoding="utf-8") as f:
            responder_config = json.load(f)
    run_server(
        args.socket,
        args.seed,
        args.players,
        args.state_file,
        args.event_log,
        args.max_rounds,
        args.limit,
        teams,
        args.map_seed,
        args.language,
        responder_config,
        args.trade_timeout,
        args.max_illegal_per_wake,
    )
