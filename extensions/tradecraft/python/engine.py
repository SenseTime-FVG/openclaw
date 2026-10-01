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
import socket
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from map_generator import (
    generate_map, GameMap, HexTile, Vertex, Edge,
    TOTAL_ROUNDS, ACTIONS_PER_ROUND, ACTION_LIMIT, MAX_SETTLEMENTS, MAX_CITIES, MAX_ROADS,
    MIN_SETTLEMENT_DISTANCE, RESOURCES, GOLD_RESOURCE, DESERT,
    HEX_ROWS, CUBE_DIRS,
)


TRADE_MASTER_REQUIRED_TRADES = 8
RESOURCE_TYCOON_REQUIRED_RESOURCES = 20
RESOURCES_PER_FINAL_POINT = 4


# ── Game State ─────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    player_id: int
    resources: dict[str, int] = field(default_factory=lambda: {r: 0 for r in RESOURCES + [GOLD_RESOURCE]})
    buildings: dict[str, int] = field(default_factory=dict)  # vertex_id → level (1-3)
    roads: set[str] = field(default_factory=set)  # set of edge_ids
    projects: list[dict] = field(default_factory=list)
    trades_completed: int = 0
    # Per-player placement bookkeeping: the settlement just placed whose
    # mandatory road is still pending. ``None`` means no pending placement.
    placement_vertex: Optional[str] = None
    total_actions: int = 0
    illegal_actions: int = 0
    forfeits: int = 0
    # True once this player completed their one scheduled action this wake.
    # Reset by ``_advance`` (the scheduler's per-wake boundary signal). A
    # further tool call while True is rejected as an over-action so the agent
    # learns to end its loop by calling no tool at all.
    acted_this_wake: bool = False


@dataclass
class TradeProposal:
    proposer: int
    responder: int
    offer: dict[str, int]
    request: dict[str, int]


@dataclass
class PlayerMessage:
    message_id: int
    sender: int
    recipients: list[int]
    content: str
    round_num: int


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
    pending_trade: Optional[TradeProposal] = None
    game_over: bool = False
    winner: Optional[int] = None
    phase: str = "placement"
    current_dice_roll: int = 0
    current_dice_rolls: list[int] = field(default_factory=list)
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
                 map_seed: Optional[int] = None):
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
        self.game_map = generate_map(seed, total_rounds=max_rounds, map_seed=map_seed)
        self.state = GameState(
            players=[PlayerState(i) for i in range(num_players)],
            game_map=self.game_map,
            num_players=num_players,
            teams=teams,
            team_of=team_of,
        )
        self._rng = random.Random(seed)
        self._dice_idx = 0

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

    def forfeit_player(self, player_id: int) -> dict:
        """Record a scheduler-driven forfeit; no turn advance happens here.

        The scheduler decides when to forfeit a stuck/crashed/silent player and
        calls this to (a) count it toward turn-health stats and (b) resolve the
        one rule the engine still owns: if the forfeiting player is the responder
        of a pending trade, that trade is rejected on their behalf. Advancing to
        the next actor is entirely the scheduler's job after this returns.
        """
        if self.state.game_over:
            return {"ok": False, "error": "Game is already over."}
        self.state.players[player_id].forfeits += 1
        if (self.state.pending_trade is not None
                and player_id == self.state.pending_trade.responder):
            return self.resolve_trade("reject", "(system) responder forfeited")
        return {"ok": True, "result": {"forfeited": True, "phase": self.state.phase}}

    def _handle_action_inner(self, action: str, params: dict, player_id: int) -> dict:
        if player_id < 0 or player_id >= self.state.num_players:
            return {"ok": False, "error": f"Invalid player: P{player_id}."}

        # Viewing the final state remains available after the game ends.
        if action == "view_game_state":
            return {"ok": True, "result": self._view_game_state(player_id, include_map=True)}
        if self.state.game_over:
            return {"ok": False, "error": "Game is already over."}
        if self.state.phase == "play" and self.state.round_num > self.max_rounds:
            # Passive settlement: the final round has completed but the match has
            # not been settled. The scheduler calls ``settle`` explicitly after it
            # has stopped invoking players.
            return {"ok": False, "error": "Game is over; awaiting settlement. Do not act further."}

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

        if player.acted_this_wake:
            return {
                "ok": False,
                "error": (
                    "Your scheduled action for this turn is already complete. "
                    "Do not call any tool — end your reply now (without a tool "
                    "call) to return control to the scheduler."
                ),
            }

        if action == "build":
            result = self._build(player, params, player_id)
        elif action == "propose_trade":
            result = self._propose_trade(player, params, player_id)
        elif action == "send_message":
            result = self._send_message(player, params, player_id)
        elif action == "claim_project":
            result = self._claim_project(player, params, player_id)
        else:
            return {
                "ok": False,
                "error": f"Unknown action: {action!r}. Use build, propose_trade, send_message, claim_project, or view_game_state.",
            }
        if result.get("ok"):
            player.acted_this_wake = True
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
                "error": "During placement, the only legal action is build (type=settlement or type=road).",
            }

        btype = params.get("type", "")
        if btype == "settlement":
            if player.placement_vertex is not None:
                return {
                    "ok": False,
                    "error": "You already placed a settlement awaiting its road. Place the road (build type=road) first.",
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
                return {"ok": False, "error": "Place settlement first, then road. Start with build type=settlement."}
            result = self._build_road(player, params.get("edge_id", ""))
            if result["ok"]:
                # Settlement+road pair complete; clear the per-player pending marker.
                player.placement_vertex = None
                result["result"]["phase"] = self.state.phase
            return result
        else:
            return {"ok": False, "error": "During placement, use build type=settlement or type=road."}

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
                # Find vertices with buildings adjacent to this hex
                for v in self.game_map.vertices:
                    if h.id in v.hexes:
                        for pid, player in enumerate(self.state.players):
                            if v.id in player.buildings:
                                level = player.buildings[v.id]
                                amount = level  # L1=1, L2=2, L3=3
                                player.resources[h.resource] = player.resources.get(h.resource, 0) + amount

    def start_round(self) -> dict:
        """Run one round's dice production. Scheduler-triggered.

        Advances ``round_num``, rolls the dice, grants resources, and records the
        production event. It does NOT decide who acts or in what order — the
        scheduler drives that and calls this exactly once per round.
        """
        self.state.round_num += 1
        before_snapshot = [dict(p.resources) for p in self.state.players]
        resources_before = [sum(v.values()) for v in before_snapshot]
        rolls = []
        for _ in range(2):
            roll = self._roll_dice()
            rolls.append(roll)
            self._produce_resources(roll)
        for player, before in zip(self.state.players, resources_before):
            if sum(player.resources.values()) == before:
                player.resources[GOLD_RESOURCE] += 1
        self.state.current_dice_rolls = rolls
        self.state.current_dice_roll = rolls[-1]
        # Record the global production event (no single actor) so each player,
        # on waking, sees which resources this round's dice granted to whom.
        deltas = []
        for p, before in zip(self.state.players, before_snapshot):
            changed = {
                r: p.resources.get(r, 0) - before.get(r, 0)
                for r in set(p.resources) | set(before)
                if p.resources.get(r, 0) != before.get(r, 0)
            }
            deltas.append({"player_id": p.player_id, "resources": changed})
        self._push_event("production", None, self.state.round_num, "play", {
            "dice_rolls": list(rolls),
            "deltas": deltas,
        })
        return {"round": self.state.round_num, "dice_rolls": rolls, "deltas": deltas}

    def end_placement(self) -> dict:
        """Transition placement → play. Scheduler-triggered once all players
        have completed their initial settlement+road placements, in whatever
        order the scheduler chose."""
        if self.state.phase != "placement":
            return {"ok": False, "error": f"Not in placement phase (phase={self.state.phase})."}
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
                f"Unknown build type: {btype!r}. Use type=road, type=settlement, or type=upgrade."
            )}

    def _build_road(self, player: PlayerState, edge_id: str) -> dict:
        """Place a road on an edge."""
        if not isinstance(edge_id, str) or not edge_id.strip():
            return {"ok": False, "error": "build type=road requires an edge_id. Pick one from the legal road options in the game state."}
        if edge_id not in {e.id for e in self.game_map.edges}:
            return {"ok": False, "error": f"Invalid edge: {edge_id}. Choose an edge_id from the legal road options."}

        if any(edge_id in p.roads for p in self.state.players):
            owner = next((p.player_id for p in self.state.players if edge_id in p.roads), None)
            return {"ok": False, "error": f"Road already exists here (owned by P{owner}). Pick a different edge."}

        # Check max roads
        if len(player.roads) >= MAX_ROADS:
            return {"ok": False, "error": f"Max {MAX_ROADS} roads reached. You cannot build more roads."}

        edge = self.game_map.edge_by_id(edge_id)

        # Road must connect to existing road or building
        if self.state.phase == "placement":
            # During placement the road must connect to THIS player's pending
            # settlement (their ``placement_vertex``), not older network pieces.
            placed_vertex = player.placement_vertex
            has_adj = placed_vertex is not None and placed_vertex in (edge.v1, edge.v2)
            if not has_adj:
                return {"ok": False, "error": "Road must connect to your newly placed settlement. Pick an adjacent edge."}
        else:
            has_adj = (
                edge.v1 in player.buildings or edge.v2 in player.buildings or
                any(eid in player.roads for eid in edge.adjacent_edges)
            )
            if not has_adj:
                return {"ok": False, "error": "Road must connect to your existing road or building. Pick an adjacent edge."}

        # Pay cost (free during placement)
        if self.state.phase == "play":
            cost = {"wood": 1, "stone": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": f"Cannot afford road. Cost: {cost}, Have: {player.resources}. Trade for or produce the missing resources first."}
            self._deduct_cost(player.resources, cost)

        player.roads.add(edge_id)
        return {"ok": True, "result": {"built": "road", "edge_id": edge_id}}

    def _build_settlement(self, player: PlayerState, vertex_id: str, placement: bool = False) -> dict:
        """Place a settlement on a vertex."""
        if not isinstance(vertex_id, str) or not vertex_id.strip():
            return {"ok": False, "error": "build type=settlement requires a vertex_id. Pick one from the legal settlement options."}
        if vertex_id not in {v.id for v in self.game_map.vertices}:
            return {"ok": False, "error": f"Invalid vertex: {vertex_id}. Choose a vertex_id from the legal settlement options."}

        if self._vertex_owners(vertex_id):
            pid, level = self._vertex_owners(vertex_id)[0]
            return {"ok": False, "error": f"A level-{level} building owned by P{pid} already exists here. Pick a different vertex."}

        # Check max settlements
        if len(player.buildings) >= MAX_SETTLEMENTS:
            return {"ok": False, "error": f"Max {MAX_SETTLEMENTS} settlements reached. You cannot place another settlement."}

        # Distance applies during both initial placement and normal play.
        conflict = self._distance_conflict(vertex_id)
        if conflict is not None:
            cpid, cvid = conflict
            return {"ok": False, "error": (
                f"Too close to P{cpid}'s building at {cvid} "
                f"(min {MIN_SETTLEMENT_DISTANCE} edges between settlements). "
                f"Pick a vertex farther away."
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
                return {"ok": False, "error": "Settlement must connect to one of your roads. Build a road first or pick a connected vertex."}

        # Pay cost
        if not placement:
            cost = {"wood": 1, "stone": 1, "iron": 1, "silk": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": f"Cannot afford settlement. Cost: {cost}, Have: {player.resources}. Trade or produce the missing resources first."}
            self._deduct_cost(player.resources, cost)

        player.buildings[vertex_id] = 1
        return {"ok": True, "result": {"built": "settlement", "vertex_id": vertex_id,
                                        "level": 1}}

    def _upgrade_building(self, player: PlayerState, vertex_id: str, to_level: int) -> dict:
        """Upgrade a settlement to city (L1→L2) or city to metropolis (L2→L3)."""
        if not isinstance(vertex_id, str) or not vertex_id.strip():
            return {"ok": False, "error": "build type=upgrade requires a vertex_id. Pick one of your own buildings."}
        if vertex_id not in player.buildings:
            return {"ok": False, "error": f"No building of yours at this vertex: {vertex_id}. You can only upgrade your own buildings."}

        current_level = player.buildings[vertex_id]
        if to_level != current_level + 1:
            return {"ok": False, "error": f"Can only upgrade from {current_level} to {current_level+1}. Pass to_level={current_level+1}."}
        if to_level > 3:
            return {"ok": False, "error": "Max level is 3. You cannot upgrade beyond a metropolis."}
        if current_level == 1:
            city_count = sum(1 for level in player.buildings.values() if level >= 2)
            if city_count >= MAX_CITIES:
                return {"ok": False, "error": f"Max {MAX_CITIES} cities reached. Upgrade a different settlement or claim a project instead."}

        if current_level == 1:
            cost = {"crystal": 2, "iron": 1}
        else:
            cost = {"crystal": 3, "iron": 2, "gold": 1}

        if not self._can_afford(player.resources, cost):
            return {"ok": False, "error": f"Cannot afford upgrade. Cost: {cost}, Have: {player.resources}. Trade or produce the missing resources first."}
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

    def _propose_trade(self, player: PlayerState, params: dict, player_id: int) -> dict:
        offer = params.get("offer", {})
        request = params.get("request", {})

        validation_error = self._validate_trade_maps(offer, request)
        if validation_error:
            return {"ok": False, "error": validation_error}
        if self.state.pending_trade is not None:
            return {"ok": False, "error": "A trade is already pending. Do not propose another; end your reply and let the pending trade resolve."}

        for r, n in offer.items():
            if player.resources.get(r, 0) < n:
                return {"ok": False, "error": f"Insufficient {r}: have {player.resources.get(r,0)}, offering {n}. Lower the offer amount."}

        target_id = params.get("target", (player_id + 1) % self.state.num_players)
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return {"ok": False, "error": "Trade target must be an integer player id (0-3)."}
        if target_id < 0 or target_id >= self.state.num_players or target_id == player_id:
            return {"ok": False, "error": f"Invalid trade target: P{target_id}. Choose another player (you cannot trade with yourself)."}
        opponent = self.state.players[target_id]
        for r, n in request.items():
            if opponent.resources.get(r, 0) < n:
                return {"ok": False, "error": (
                    f"Opponent has insufficient {r}: have {opponent.resources.get(r, 0)}, "
                    f"requesting {n}."
                )}

        self.state.pending_trade = TradeProposal(
            proposer=player_id, responder=target_id,
            offer=dict(offer), request=dict(request))

        return {"ok": True, "result": {
            "trade_proposed": {"proposer": player_id, "offer": offer, "request": request}}}

    def resolve_trade(self, decision: str, reason: str) -> dict:
        """Resolve the pending trade on behalf of its responder (accept/reject).

        Called by the scheduler after it parses the responder's JSON decision
        from its content. ``reason`` is the decision feedback delivered to the
        proposer; it is never empty (the scheduler supplies a default).
        """
        pt = self.state.pending_trade
        if pt is None:
            return {"ok": False, "error": "No pending trade to resolve."}
        if decision not in ("accept", "reject"):
            return {"ok": False, "error": f"Unknown trade decision: {decision!r}. Use accept or reject."}
        action_round = self.state.round_num
        action_phase = self.state.phase
        responder = pt.responder
        if decision == "accept":
            result = self._execute_trade(responder)
        else:
            self.state.pending_trade = None
            result = {"ok": True, "result": {"trade_rejected": True}}
        if result.get("ok"):
            feedback = self._record_message(responder, [pt.proposer], reason)
            result["result"]["response_message"] = self._message_view(feedback)
            self._record_action(
                "respond_trade",
                {"action": decision, "message": reason},
                result,
                responder,
                action_round,
                action_phase,
            )
        return self._with_action_state(result, responder)

    def _execute_trade(self, player_id: int) -> dict:
        pt = self.state.pending_trade
        giver = self.state.players[pt.proposer]
        receiver = self.state.players[pt.responder]

        # Re-validate
        for r, n in pt.offer.items():
            if giver.resources.get(r, 0) < n:
                self.state.pending_trade = None
                return {"ok": False, "error": (
                    f"Proposer no longer has enough {r}: have {giver.resources.get(r, 0)}, need {n}."
                )}
        for r, n in pt.request.items():
            if receiver.resources.get(r, 0) < n:
                self.state.pending_trade = None
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
        self.state.pending_trade = None

        return {"ok": True, "result": {
            "trade_accepted": {"offer": pt.offer, "request": pt.request}}}

    # ── Messages & Projects ────────────────────────────────────────────

    @staticmethod
    def _message_view(message: PlayerMessage) -> dict:
        return {
            "id": message.message_id,
            "from": message.sender,
            "recipients": list(message.recipients),
            "content": message.content,
            "round": message.round_num,
        }

    def _record_message(
        self, player_id: int, recipients: list[int], content: str
    ) -> PlayerMessage:
        message = PlayerMessage(
            message_id=len(self.state.messages) + 1,
            sender=player_id,
            recipients=recipients,
            content=content.strip(),
            round_num=self.state.round_num,
        )
        self.state.messages.append(message)
        return message

    def _send_message(self, player: PlayerState, params: dict, player_id: int) -> dict:
        content = params.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return {"ok": False, "error": "Message content required. Provide a non-empty negotiation message."}
        message = self._record_message(
            player_id,
            [pid for pid in range(self.state.num_players) if pid != player_id],
            content,
        )
        return {"ok": True, "result": {
            "message_sent": self._message_view(message)}}

    def _claim_project(self, player: PlayerState, params: dict, player_id: int) -> dict:
        pid = params.get("project_id", "")
        projects = [
            {"id": "trade_master", "name": "Trade Master", "points": 1,
             "condition": lambda p: p.trades_completed >= TRADE_MASTER_REQUIRED_TRADES,
             "requirement": f"complete {TRADE_MASTER_REQUIRED_TRADES}+ trades",
             "progress": lambda p: f"{p.trades_completed} trades completed"},
            {"id": "build_master", "name": "Building Master", "points": 2,
             "condition": lambda p: len(p.buildings) >= 5,
             "requirement": "own 5+ buildings",
             "progress": lambda p: f"{len(p.buildings)} buildings owned"},
            {"id": "tech_pioneer", "name": "Tech Pioneer", "points": 2,
             "condition": lambda p: sum(1 for l in p.buildings.values() if l >= 3) >= 2,
             "requirement": "own 2+ level-3 buildings",
             "progress": lambda p: f"{sum(1 for l in p.buildings.values() if l >= 3)} level-3 buildings owned"},
            {"id": "resource_tycoon", "name": "Resource Tycoon", "points": 1,
             "condition": lambda p: sum(p.resources.values()) >= RESOURCE_TYCOON_REQUIRED_RESOURCES,
             "requirement": f"hold {RESOURCE_TYCOON_REQUIRED_RESOURCES}+ resources",
             "progress": lambda p: f"{sum(p.resources.values())} resources held"},
            {"id": "road_king", "name": "Road King", "points": 2,
             "condition": lambda p: self._longest_road(p) >= 6,
             "requirement": "have a longest road of 6+ segments",
             "progress": lambda p: f"longest road is {self._longest_road(p)} segments"},
        ]

        project = next((pr for pr in projects if pr["id"] == pid), None)
        if not project:
            return {"ok": False, "error": f"Unknown project: {pid}. Use one of trade_master, build_master, tech_pioneer, resource_tycoon, road_king."}

        # Check already claimed
        for pstate in self.state.players:
            for pr in pstate.projects:
                if pr["id"] == pid:
                    return {"ok": False, "error": f"Project already claimed by P{pstate.player_id}. Claim a different project."}

        if not project["condition"](player):
            return {"ok": False, "error": (
                f"Conditions not met for '{project['name']}': "
                f"requires {project['requirement']}; "
                f"you currently have {project['progress'](player)}."
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
            return {"ok": False, "error": f"Cannot settle before play (phase={self.state.phase})."}
        if self.state.round_num < self.max_rounds:
            return {
                "ok": False,
                "error": (
                    f"Game is not finished: round {self.state.round_num}/"
                    f"{self.max_rounds}."
                ),
            }
        if self.state.pending_trade is not None:
            return {
                "ok": False,
                "error": "Settlement requires the pending trade to be resolved first.",
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
            resource_bonus = sum(p.resources.values()) // RESOURCES_PER_FINAL_POINT
            scores.append(building_score + project_score + resource_bonus)
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
            "forfeits": [int(p.forfeits) for p in self.state.players],
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _required_gold(self, resources: dict, cost: dict) -> int:
        explicit_gold = cost.get(GOLD_RESOURCE, 0)
        substitution_gold = sum(
            max(0, amount - resources.get(resource, 0))
            for resource, amount in cost.items()
            if resource != GOLD_RESOURCE
        )
        return explicit_gold + substitution_gold

    def _can_afford(self, resources: dict, cost: dict) -> bool:
        return self._required_gold(resources, cost) <= resources.get(GOLD_RESOURCE, 0)

    def _deduct_cost(self, resources: dict, cost: dict):
        gold_spent = self._required_gold(resources, cost)
        for resource, amount in cost.items():
            if resource != GOLD_RESOURCE:
                resources[resource] = max(0, resources.get(resource, 0) - amount)
        resources[GOLD_RESOURCE] = resources.get(GOLD_RESOURCE, 0) - gold_spent

    def _validate_trade_maps(self, offer: object, request: object) -> Optional[str]:
        """Validate engine-bound resource maps independently of tool schemas."""
        valid_resources = set(RESOURCES + [GOLD_RESOURCE])
        for label, resource_map in (("offer", offer), ("request", request)):
            if not isinstance(resource_map, dict) or not resource_map:
                return f"Must specify a non-empty {label}."
            for resource, amount in resource_map.items():
                if resource not in valid_resources:
                    return f"Unknown resource in {label}: {resource}."
                if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                    return f"{label}.{resource} must be a positive integer."
        return None

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

    def _view_game_state(self, player_id: int, include_map: bool = True) -> dict:
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

        pending_trade_view = None
        if self.state.pending_trade:
            pt = self.state.pending_trade
            pending_trade_view = {
                "proposer": pt.proposer, "responder": pt.responder,
                "offer": dict(pt.offer), "request": dict(pt.request),
            }

        projects_view = [
            {"id": "trade_master", "name": "Trade Master", "description": f"Complete {TRADE_MASTER_REQUIRED_TRADES}+ trades", "points": 1,
             "claimed_by": self._project_claimed_by("trade_master")},
            {"id": "build_master", "name": "Building Master", "description": "Own 5+ buildings", "points": 2,
             "claimed_by": self._project_claimed_by("build_master")},
            {"id": "tech_pioneer", "name": "Tech Pioneer", "description": "Own 2+ L3 buildings", "points": 2,
             "claimed_by": self._project_claimed_by("tech_pioneer")},
            {"id": "resource_tycoon", "name": "Resource Tycoon", "description": f"Hold {RESOURCE_TYCOON_REQUIRED_RESOURCES}+ resources", "points": 1,
             "claimed_by": self._project_claimed_by("resource_tycoon")},
            {"id": "road_king", "name": "Road King", "description": "Longest road ≥ 6", "points": 2,
             "claimed_by": self._project_claimed_by("road_king")},
        ]

        scores = self._calculate_scores() if self.state.game_over else self._latest_completed_round_scores()
        teams = [list(t) for t in self.state.teams] if self.state.teams else None
        team_scores = self._team_scores(scores) if self.state.teams else None
        team_winner = self._team_winner(team_scores) if team_scores else None

        view = {
            "round": self.state.round_num,
            "max_rounds": self.max_rounds,
            "dice_roll": self.state.current_dice_roll,
            "dice_rolls": list(self.state.current_dice_rolls),
            "scoring": {
                "buildings": "Sum of building levels",
                "projects": "Sum of claimed project points",
                "remaining_resources": (
                    f"1 point per {RESOURCES_PER_FINAL_POINT} resources, rounded down"
                ),
            },
            "phase": self.state.phase,
            "players": players_view,
        }
        if include_map:
            view["map"] = self._compact_map_view()
        view.update({
            "projects": projects_view,
            "pending_trade": pending_trade_view,
            "messages": [self._message_view(message) for message in self.state.messages],
            "recent_actions": self._recent_actions_view(),
            "expansion_targets": self._expansion_targets_view(player_id),
            "game_over": self.state.game_over,
            "winner": self.state.winner if self.state.game_over else self._winner_from_scores(scores),
            "scores": scores,
            "teams": teams,
            "team_scores": team_scores,
            "team_winner": team_winner,
            "legality": self._legality_view(),
            "my_player_id": player_id,
        })
        return view

    def _project_claimed_by(self, pid: str) -> Optional[int]:
        for p in self.state.players:
            for pr in p.projects:
                if pr["id"] == pid:
                    return p.player_id
        return None

    def _pending_trade_view(self) -> Optional[dict]:
        pt = self.state.pending_trade
        if pt is None:
            return None
        return {
            "proposer": pt.proposer,
            "responder": pt.responder,
            "offer": dict(pt.offer),
            "request": dict(pt.request),
        }

    def _step_instruction(self, player_id: int) -> str:
        """Rule-level guidance for THIS player, with no turn ordering whatsoever.

        The engine only knows board rules and the per-player placement pairing
        and pending-trade state. All turn/order/wake phrasing — "it's your turn",
        "wait for others" — is the scheduler's job; the engine returns only the
        rule-level "if you act, this is what the rule expects of you" guidance.
        """
        if self.state.phase == "placement":
            return self._placement_instruction(player_id)
        pt = self.state.pending_trade
        if pt is not None and pt.responder == player_id:
            return (
                "\n\nYou are the responder to the pending trade. Do NOT call any tool to "
                "respond. End with a single JSON decision in a fenced ```json block, exactly "
                'like {"decision": "accept"|"reject", "reason": "..."} — a short reason is '
                "required. The runner parses this JSON; a missing or malformed decision is "
                "treated as a rejection."
            )
        if pt is not None and pt.proposer == player_id:
            return (
                "\n\n## Trade pending\n"
                "You proposed a trade and are waiting for the responder's decision. "
                "Do not call any tool; end your reply now so the runner continues."
            )
        return ""

    def _placement_instruction(self, player_id: int) -> str:
        """Per-player placement pairing: settlement first, then its adjacent road.

        No snake-draft order/step wording — only the rule that a player's next
        placement build must complete the current settlement+road pair.
        """
        if self.state.phase != "placement":
            return ""
        player = self.state.players[player_id]
        targets = self._expansion_targets_view(player_id)
        if player.placement_vertex is None:
            legal = list((targets.get("settlements") or {}).keys())
            action = (
                'place a settlement now: call tradecraft_build with type="settlement" and a '
                "vertex_id from the legal options. Do NOT place a road first, and do NOT call "
                "tradecraft_build with type=\"upgrade\" — that is rejected during placement."
            )
        else:
            legal = list((targets.get("roads") or {}).keys())
            action = (
                "place the road for the settlement you just placed: call tradecraft_build with "
                'type="road" and an edge_id from the legal options. Do NOT start another '
                "settlement."
            )
        legal_str = ", ".join(legal) if legal else "none listed in state (check expansion_targets)"
        return (
            f"\n\n## Placement for P{player_id}\n"
            f"You are in the placement phase. Right now the rules require you to {action}\n"
            f"Legal options right now: {legal_str}."
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

        Also clears the per-wake action flag so the player can act again on the
        next wake.
        """
        self.state.event_pointers[player_id] = len(self.state.events)
        self.state.players[player_id].acted_this_wake = False
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
    state = engine._view_game_state(0)
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


def _handle_controller(conn: socket.socket, engine: TradeCraftEngine):
    """Serve one scheduler/controller request: get_view, advance, or end_game.

    Returns ``None`` when the controller disconnected so the caller can drop it.
    """
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
            send_message(conn, {"ok": False, "error": f"Invalid player_id: {pid}"})
            return True
        send_message(conn, {"ok": True, "result": engine._get_view(pid)})
    elif action == "start_round":
        # Scheduler-driven dice production for a new round.
        send_message(conn, {"ok": True, "result": engine.start_round()})
    elif action == "end_placement":
        # Scheduler-driven transition from placement to play.
        send_message(conn, engine.end_placement())
    elif action == "snapshot_round_scores":
        # Scheduler-driven per-round score snapshot (one per complete round).
        send_message(conn, engine.snapshot_round_scores())
    elif action == "resolve_trade":
        decision = params.get("decision", "")
        reason = params.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            reason = "(system) 解析未得到有效决策，按拒绝处理"
        send_message(conn, engine.resolve_trade(decision, reason))
    elif action == "advance":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": f"Invalid player_id: {pid}"})
            return True
        send_message(conn, {"ok": True, "result": engine._advance(pid)})
    elif action == "forfeit_player":
        if not valid_pid:
            send_message(conn, {"ok": False, "error": f"Invalid player_id: {pid}"})
            return True
        send_message(conn, engine.forfeit_player(pid))
    elif action == "end_game":
        send_message(conn, engine.settle())
    else:
        send_message(conn, {
            "ok": False,
            "error": f"Unknown controller action: {action}",
        })
    return True


def run_server(socket_path: str, seed: int = 42, num_players: int = 3,
               state_path: Optional[str] = None, event_log_path: Optional[str] = None,
               max_rounds: int = TOTAL_ROUNDS, limit: int = ACTION_LIMIT,
               teams: Optional[list[list[int]]] = None,
               map_seed: Optional[int] = None):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    engine = TradeCraftEngine(
        seed=seed,
        num_players=num_players,
        max_rounds=max_rounds,
        limit=limit,
        teams=teams,
        map_seed=map_seed,
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
                        "error": "Invalid or duplicate player registration.",
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
        all_conns = list(conn_by_pid.values())
        controller_conn = None

        while not engine.state.game_over:
            watch = all_conns + [server] + (
                [controller_conn] if controller_conn is not None else []
            )
            readable, _, _ = select.select(watch, [], [], None)

            for conn in readable:
                if conn is server:
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
                        _append_event(event_log_path, {"type": "controller_connected"})
                        print("[TradeCraft] Controller connected.", flush=True)
                    else:
                        send_message(client, {
                            "ok": False,
                            "error": "Controller already connected or invalid registration.",
                        })
                        client.close()
                    continue

                if controller_conn is not None and conn is controller_conn:
                    if _handle_controller(conn, engine) is None:
                        controller_conn = None
                        _append_event(event_log_path, {"type": "controller_disconnected"})
                    else:
                        # Controller actions can mutate the game state (e.g.
                        # resolve_trade clears the pending trade, end_game
                        # settles). Persist immediately so the scheduler's
                        # next state read reflects the mutation instead of the
                        # stale snapshot left by the last player action.
                        sequence += 1
                        _write_snapshot(state_path, engine, sequence, list(conn_by_pid))
                    continue

                cid = id(conn)
                pid = pid_by_conn.get(cid)
                if pid is None:
                    continue

                msg = recv_message(conn)
                if msg is None:
                    print(f"[TradeCraft] Player {pid} disconnected.", flush=True)
                    engine.state.game_over = True
                    sequence += 1
                    _write_snapshot(state_path, engine, sequence, list(conn_by_pid))
                    _append_event(event_log_path, {
                        "type": "player_disconnected",
                        "player_id": pid,
                        "sequence": sequence,
                    })
                    break

                if "error" in msg:
                    send_message(conn, {"ok": False, "error": msg["error"]})
                    continue

                action = msg.get("action", "")
                params = msg.get("params", {})

                try:
                    result = engine.handle_action(action, params, pid)
                except Exception as exc:
                    result = {"ok": False, "error": f"Internal engine error: {exc}"}
                sequence += 1
                _write_snapshot(state_path, engine, sequence, list(conn_by_pid))
                _append_event(event_log_path, {
                    "type": "action",
                    "sequence": sequence,
                    "player_id": pid,
                    "action": action,
                    "params": params,
                    "ok": result.get("ok", False),
                    "error": result.get("error"),
                    "phase": engine.state.phase,
                    "round": engine.state.round_num,
                    "game_over": engine.state.game_over,
                })
                send_message(conn, result)

        # The response to the final action already contains the final state.
        # Do not send unsolicited messages: the client protocol is FIFO and
        # has no request ids, so a broadcast could be mistaken for a response.
        _write_snapshot(state_path, engine, sequence, list(conn_by_pid))
        _append_event(event_log_path, {
            "type": "game_over",
            "sequence": sequence,
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
    args = parser.parse_args()
    teams = json.loads(args.teams) if args.teams else None
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
    )
