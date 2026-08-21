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
    TOTAL_ROUNDS, ACTIONS_PER_ROUND, MAX_SETTLEMENTS, MAX_CITIES, MAX_ROADS,
    MIN_SETTLEMENT_DISTANCE, RESOURCES, GOLD_RESOURCE, DESERT,
    HEX_ROWS, CUBE_DIRS,
)


# ── Game State ─────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    player_id: int
    resources: dict[str, int] = field(default_factory=lambda: {r: 0 for r in RESOURCES + [GOLD_RESOURCE]})
    buildings: dict[str, int] = field(default_factory=dict)  # vertex_id → level (1-3)
    roads: set[str] = field(default_factory=set)  # set of edge_ids
    projects: list[dict] = field(default_factory=list)
    trades_completed: int = 0
    actions_used: int = 0


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


@dataclass
class GameState:
    players: list[PlayerState]
    game_map: GameMap
    num_players: int = 3
    round_num: int = 1
    current_player: int = 0
    pending_trade: Optional[TradeProposal] = None
    game_over: bool = False
    winner: Optional[int] = None
    phase: str = "placement"
    placement_round: int = 0
    placement_done: bool = False
    placement_vertex: Optional[str] = None
    current_dice_roll: int = 0
    messages: list[PlayerMessage] = field(default_factory=list)


# ── Engine ─────────────────────────────────────────────────────────────────

class TradeCraftEngine:
    """Game engine for TradeCraft, supporting 3-4 players."""

    def __init__(self, seed: int = 42, num_players: int = 3):
        if num_players not in (3, 4):
            raise ValueError(f"num_players must be 3 or 4, got {num_players}")
        self.num_players = num_players
        self.game_map = generate_map(seed)
        self.state = GameState(
            players=[PlayerState(i) for i in range(num_players)],
            game_map=self.game_map,
            num_players=num_players,
        )
        self._rng = random.Random(seed)
        self._dice_idx = 0

    def handle_action(self, action: str, params: dict, player_id: int) -> dict:
        if player_id < 0 or player_id >= self.state.num_players:
            return {"ok": False, "error": f"Invalid player: P{player_id}."}

        # Viewing the final state remains available after the game ends.
        if action == "view_game_state":
            return {"ok": True, "result": self._view_game_state(player_id, include_map=True)}
        if self.state.game_over:
            return {"ok": False, "error": "Game is already over."}

        player = self.state.players[player_id]

        # Universal tools
        if action == "respond_trade":
            return self._with_action_state(
                self._respond_trade(player, params, player_id), player_id
            )

        # Turn checks for placement phase
        if self.state.phase == "placement":
            return self._with_action_state(
                self._handle_placement(action, params, player_id, player), player_id
            )

        # Turn checks for play phase
        if action in ("build", "propose_trade", "send_message", "claim_project"):
            if player_id != self.state.current_player:
                return {"ok": False, "error": f"Not your turn. Current: P{self.state.current_player}."}
            if player.actions_used >= ACTIONS_PER_ROUND:
                return {"ok": False, "error": "All turn actions are used; resolve the pending trade."}

        if action == "build":
            result = self._build(player, params, player_id)
        elif action == "propose_trade":
            result = self._propose_trade(player, params, player_id)
        elif action == "send_message":
            result = self._send_message(player, params, player_id)
        elif action == "claim_project":
            result = self._claim_project(player, params, player_id)
        else:
            return {"ok": False, "error": f"Unknown action: {action}"}
        return self._with_action_state(result, player_id)

    def _with_action_state(self, result: dict, player_id: int) -> dict:
        """Attach one post-action dynamic snapshot without repeating the static map."""
        if result.get("ok") and isinstance(result.get("result"), dict):
            result["result"]["state"] = self._view_game_state(
                player_id, include_map=False
            )
        return result

    # ── Placement Phase ────────────────────────────────────────────────

    def _placement_player(self) -> int:
        """Return the player expected by the current snake-draft step."""
        player_count = self.state.num_players
        if self.state.placement_round < player_count:
            return self.state.placement_round
        return 2 * player_count - 1 - self.state.placement_round

    def _handle_placement(self, action: str, params: dict, player_id: int, player: PlayerState) -> dict:
        """Initial placement: snake draft."""
        expected = self._placement_player()

        if player_id != expected:
            return {"ok": False, "error": f"Placement round {self.state.placement_round}: waiting for P{expected}."}

        if action != "build":
            return {"ok": False, "error": "During placement, use build action."}

        btype = params.get("type", "")
        if btype == "settlement":
            if self.state.placement_done:
                return {"ok": False, "error": "Already placed settlement. Now place a road (build type=road)."}
            vid = params.get("vertex_id", "")
            result = self._build_settlement(player, vid, placement=True)
            if result["ok"]:
                self.state.placement_done = True
                self.state.placement_vertex = vid
                vertex = self.game_map.vertex_by_id(vid)
                self._grant_initial_resources(player, vertex)
                result["result"]["phase"] = self.state.phase
            return result
        elif btype == "road":
            if not self.state.placement_done:
                return {"ok": False, "error": "Place settlement first, then road."}
            result = self._build_road(player, params.get("edge_id", ""))
            if result["ok"]:
                # Both settlement and road placed → advance
                self.state.placement_round += 1
                self.state.placement_done = False
                self.state.placement_vertex = None
                if self.state.placement_round >= 2 * self.state.num_players:
                    self.state.phase = "play"
                    self.state.current_player = 0
                    self.state.round_num = 1
                    # Roll first dice and distribute resources
                    self._start_round()
                else:
                    self.state.current_player = self._placement_player()
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

    def _start_round(self):
        roll = self._roll_dice()
        self._produce_resources(roll)
        self.state.current_dice_roll = roll
        for p in self.state.players:
            p.actions_used = 0
        self.state.current_player = 0
        return roll

    # ── Build Actions ──────────────────────────────────────────────────

    def _build(self, player: PlayerState, params: dict, player_id: int) -> dict:
        """Handle build action: road, settlement, or upgrade."""
        btype = params.get("type", "")

        if btype == "road":
            return self._build_road(player, params.get("edge_id", ""))
        elif btype == "settlement":
            result = self._build_settlement(player, params.get("vertex_id", ""))
            if result["ok"]:
                self._consume_action(player)
            return result
        elif btype == "upgrade":
            result = self._upgrade_building(player, params.get("vertex_id", ""),
                                            params.get("to_level", 0))
            if result["ok"]:
                self._consume_action(player)
            return result
        else:
            return {"ok": False, "error": f"Unknown build type: {btype}"}

    def _build_road(self, player: PlayerState, edge_id: str) -> dict:
        """Place a road on an edge."""
        if edge_id not in {e.id for e in self.game_map.edges}:
            return {"ok": False, "error": f"Invalid edge: {edge_id}"}

        if any(edge_id in p.roads for p in self.state.players):
            return {"ok": False, "error": "Road already exists here."}

        # Check max roads
        if len(player.roads) >= MAX_ROADS:
            return {"ok": False, "error": f"Max {MAX_ROADS} roads reached."}

        edge = self.game_map.edge_by_id(edge_id)

        # Road must connect to existing road or building
        if self.state.phase == "placement":
            # During placement the road belongs to the settlement placed in
            # this snake-draft step, not an older part of the player's network.
            placed_vertex = self.state.placement_vertex
            has_adj = placed_vertex is not None and placed_vertex in (edge.v1, edge.v2)
            if not has_adj:
                return {"ok": False, "error": "Road must connect to your settlement."}
        else:
            has_adj = (
                edge.v1 in player.buildings or edge.v2 in player.buildings or
                any(eid in player.roads for eid in edge.adjacent_edges)
            )
            if not has_adj:
                return {"ok": False, "error": "Road must connect to your existing road or building."}

        # Pay cost (free during placement)
        if self.state.phase == "play":
            cost = {"wood": 1, "stone": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": f"Cannot afford road. Cost: {cost}, Have: {player.resources}"}
            self._deduct_cost(player.resources, cost)

        player.roads.add(edge_id)
        if self.state.phase == "play":
            self._consume_action(player)
        return {"ok": True, "result": {"built": "road", "edge_id": edge_id}}

    def _build_settlement(self, player: PlayerState, vertex_id: str, placement: bool = False) -> dict:
        """Place a settlement on a vertex."""
        if vertex_id not in {v.id for v in self.game_map.vertices}:
            return {"ok": False, "error": f"Invalid vertex: {vertex_id}"}

        if self._vertex_owners(vertex_id):
            return {"ok": False, "error": "A building already exists here."}

        # Check max settlements
        if len(player.buildings) >= MAX_SETTLEMENTS:
            return {"ok": False, "error": f"Max {MAX_SETTLEMENTS} settlements reached."}

        # Distance applies during both initial placement and normal play.
        if not self._check_distance(vertex_id):
            return {"ok": False, "error": "Too close to other buildings (min 2 edges)."}

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
                return {"ok": False, "error": "Settlement must connect to a road."}

        # Pay cost
        if not placement:
            cost = {"wood": 1, "stone": 1, "iron": 1, "silk": 1}
            if not self._can_afford(player.resources, cost):
                return {"ok": False, "error": f"Cannot afford settlement. Cost: {cost}"}
            self._deduct_cost(player.resources, cost)

        player.buildings[vertex_id] = 1
        return {"ok": True, "result": {"built": "settlement", "vertex_id": vertex_id,
                                        "level": 1}}

    def _upgrade_building(self, player: PlayerState, vertex_id: str, to_level: int) -> dict:
        """Upgrade a settlement to city (L1→L2) or city to metropolis (L2→L3)."""
        if vertex_id not in player.buildings:
            return {"ok": False, "error": "No building at this vertex."}

        current_level = player.buildings[vertex_id]
        if to_level != current_level + 1:
            return {"ok": False, "error": f"Can only upgrade from {current_level} to {current_level+1}."}
        if to_level > 3:
            return {"ok": False, "error": "Max level is 3."}
        if current_level == 1:
            city_count = sum(1 for level in player.buildings.values() if level >= 2)
            if city_count >= MAX_CITIES:
                return {"ok": False, "error": f"Max {MAX_CITIES} cities reached."}

        if current_level == 1:
            cost = {"crystal": 2, "iron": 1}
        else:
            cost = {"crystal": 3, "iron": 2, "gold": 1}

        if not self._can_afford(player.resources, cost):
            return {"ok": False, "error": f"Cannot afford upgrade. Cost: {cost}"}
        self._deduct_cost(player.resources, cost)

        player.buildings[vertex_id] = to_level
        return {"ok": True, "result": {"built": "upgrade", "vertex_id": vertex_id,
                                        "level": to_level}}

    def _check_distance(self, vertex_id: str) -> bool:
        """Check distance rule: no other building within 2 edges."""
        if self._vertex_owners(vertex_id):
            return False

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
                                return False
            fringe = new_fringe
        return True

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
            return {"ok": False, "error": "Trade negotiation already in progress."}

        for r, n in offer.items():
            if player.resources.get(r, 0) < n:
                return {"ok": False, "error": f"Insufficient {r}: have {player.resources.get(r,0)}, offering {n}."}

        target_id = params.get("target", (player_id + 1) % self.state.num_players)
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return {"ok": False, "error": "Trade target must be an integer player id."}
        if target_id < 0 or target_id >= self.state.num_players or target_id == player_id:
            return {"ok": False, "error": f"Invalid trade target: P{target_id}."}
        opponent = self.state.players[target_id]
        for r, n in request.items():
            if opponent.resources.get(r, 0) < n:
                return {"ok": False, "error": f"Opponent has insufficient {r}."}

        self.state.pending_trade = TradeProposal(
            proposer=player_id, responder=target_id,
            offer=dict(offer), request=dict(request))

        self._consume_action(player)
        return {"ok": True, "result": {
            "trade_proposed": {"proposer": player_id, "offer": offer, "request": request}}}

    def _respond_trade(self, player: PlayerState, params: dict, player_id: int) -> dict:
        action = params.get("action", "")
        message = params.get("message", "")
        pt = self.state.pending_trade
        if pt is None:
            return {"ok": False, "error": "No pending trade."}

        # Only proposer and responder can act on the trade
        if player_id not in (pt.proposer, pt.responder):
            return {"ok": False, "error": "You are not involved in this trade."}
        if not isinstance(message, str) or not message.strip():
            return {"ok": False, "error": "Trade response message required."}

        other_id = pt.responder if player_id == pt.proposer else pt.proposer

        if action == "accept":
            if pt.proposer == player_id:
                return {"ok": False, "error": "Cannot accept your own proposal."}
            result = self._execute_trade(player_id)
        elif action == "reject":
            self.state.pending_trade = None
            result = {"ok": True, "result": {"trade_rejected": True}}
        elif action == "counter":
            # Reject current, create new proposal with swapped roles
            offer = params.get("offer", {})
            request = params.get("request", {})
            validation_error = self._validate_trade_maps(offer, request)
            if validation_error:
                return {"ok": False, "error": validation_error}
            for r, n in offer.items():
                if player.resources.get(r, 0) < n:
                    return {"ok": False, "error": f"Insufficient {r}."}
            other_id = pt.proposer if player_id == pt.responder else pt.responder
            other = self.state.players[other_id]
            for r, n in request.items():
                if other.resources.get(r, 0) < n:
                    return {"ok": False, "error": f"Opponent has insufficient {r}."}
            self.state.pending_trade = TradeProposal(
                proposer=player_id, responder=other_id,
                offer=dict(offer), request=dict(request))
            result = {"ok": True, "result": {
                "trade_countered": {"offer": offer, "request": request}}}
        else:
            return {"ok": False, "error": f"Unknown trade response: {action}"}

        if result.get("ok"):
            feedback = self._record_message(player_id, [other_id], message)
            result["result"]["response_message"] = self._message_view(feedback)
        self._advance_turn_if_ready()
        return result

    def _execute_trade(self, player_id: int) -> dict:
        pt = self.state.pending_trade
        giver = self.state.players[pt.proposer]
        receiver = self.state.players[pt.responder]

        # Re-validate
        for r, n in pt.offer.items():
            if giver.resources.get(r, 0) < n:
                self.state.pending_trade = None
                return {"ok": False, "error": f"Proposer no longer has enough {r}."}
        for r, n in pt.request.items():
            if receiver.resources.get(r, 0) < n:
                self.state.pending_trade = None
                return {"ok": False, "error": f"Responder no longer has enough {r}."}

        for r, n in pt.offer.items():
            giver.resources[r] -= n
            receiver.resources[r] = receiver.resources.get(r, 0) + n
        for r, n in pt.request.items():
            receiver.resources[r] -= n
            giver.resources[r] = giver.resources.get(r, 0) + n

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
            return {"ok": False, "error": "Message content required."}
        message = self._record_message(
            player_id,
            [pid for pid in range(self.state.num_players) if pid != player_id],
            content,
        )
        self._consume_action(player)
        return {"ok": True, "result": {
            "message_sent": self._message_view(message)}}

    def _claim_project(self, player: PlayerState, params: dict, player_id: int) -> dict:
        pid = params.get("project_id", "")
        projects = [
            {"id": "trade_master", "name": "Trade Master", "condition": lambda p: p.trades_completed >= 5, "points": 3},
            {"id": "build_master", "name": "Building Master", "condition": lambda p: len(p.buildings) >= 5, "points": 3},
            {"id": "tech_pioneer", "name": "Tech Pioneer", "condition": lambda p: sum(1 for l in p.buildings.values() if l >= 3) >= 2, "points": 5},
            {"id": "resource_tycoon", "name": "Resource Tycoon", "condition": lambda p: sum(p.resources.values()) >= 12, "points": 3},
            {"id": "road_king", "name": "Road King", "condition": lambda p: self._longest_road(p) >= 6, "points": 4},
            {"id": "hex_lord", "name": "Hex Lord", "condition": lambda p: self._hex_diversity(p) >= 4, "points": 4},
        ]

        project = next((pr for pr in projects if pr["id"] == pid), None)
        if not project:
            return {"ok": False, "error": f"Unknown project: {pid}"}

        # Check already claimed
        for pstate in self.state.players:
            for pr in pstate.projects:
                if pr["id"] == pid:
                    return {"ok": False, "error": "Project already claimed."}

        if not project["condition"](player):
            return {"ok": False, "error": f"Conditions not met for '{project['name']}'."}

        player.projects.append({"id": pid, "name": project["name"], "points": project["points"]})
        self._consume_action(player)
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

    def _hex_diversity(self, player: PlayerState) -> int:
        """Count distinct resource types of hexes adjacent to player's buildings."""
        types = set()
        for vid in player.buildings:
            v = self.game_map.vertex_by_id(vid)
            for hid in v.hexes:
                h = self.game_map.hex_by_id(hid)
                if h.resource != DESERT:
                    types.add(h.resource)
        return len(types)

    # ── Turn Management ────────────────────────────────────────────────

    def _consume_action(self, player: PlayerState):
        player.actions_used += 1
        if player.actions_used >= ACTIONS_PER_ROUND:
            self._end_turn(player.player_id)

    def _advance_turn_if_ready(self):
        # The last player can finish their actions while a trade is pending.
        # Release that deferred round transition once negotiation resolves.
        player = self.state.players[self.state.current_player]
        if (player.actions_used >= ACTIONS_PER_ROUND
                and self.state.pending_trade is None):
            self._end_turn(player.player_id)

    def _end_turn(self, player_id: int):
        N = self.state.num_players; next_player = (player_id + 1) % N
        if next_player == 0:
            # Dice production and final scoring must use the inventories after
            # the last player's pending trade has been resolved.
            if self.state.pending_trade is not None:
                return
            self.state.round_num += 1
            if self.state.round_num > TOTAL_ROUNDS:
                self._end_game()
                return
            self._start_round()
        else:
            for p in self.state.players:
                p.actions_used = 0
            self.state.current_player = next_player

    def _end_game(self):
        self.state.game_over = True
        scores = self._calculate_scores()
        max_score = max(scores)
        winners = [i for i, s in enumerate(scores) if s == max_score]
        self.state.winner = winners[0] if len(winners) == 1 else None

    def _calculate_scores(self) -> list[int]:
        scores = []
        for p in self.state.players:
            building_score = sum(l for l in p.buildings.values())
            project_score = sum(pr["points"] for pr in p.projects)
            resource_bonus = sum(p.resources.values()) // 2
            scores.append(building_score + project_score + resource_bonus)
        return scores

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
        if self.state.phase == "placement" and player_id == self.state.current_player:
            if not self.state.placement_done:
                for v in self.game_map.vertices:
                    if not self._vertex_owners(v.id) and self._check_distance(v.id):
                        settlement_ids.append(v.id)
            else:
                placed_vertex = self.state.placement_vertex
                for e in self.game_map.edges:
                    occupied = any(e.id in p.roads for p in self.state.players)
                    if not occupied and placed_vertex in (e.v1, e.v2):
                        road_ids.append(e.id)
        elif player_id == self.state.current_player:
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
                "actions_used": p.actions_used,
                "is_current": p.player_id == self.state.current_player,
            })

        pending_trade_view = None
        if self.state.pending_trade:
            pt = self.state.pending_trade
            pending_trade_view = {
                "proposer": pt.proposer, "responder": pt.responder,
                "offer": dict(pt.offer), "request": dict(pt.request),
            }

        projects_view = [
            {"id": "trade_master", "name": "Trade Master", "description": "Complete 5+ trades", "points": 3,
             "claimed_by": self._project_claimed_by("trade_master")},
            {"id": "build_master", "name": "Building Master", "description": "Own 5+ buildings", "points": 3,
             "claimed_by": self._project_claimed_by("build_master")},
            {"id": "tech_pioneer", "name": "Tech Pioneer", "description": "Own 2+ L3 buildings", "points": 5,
             "claimed_by": self._project_claimed_by("tech_pioneer")},
            {"id": "resource_tycoon", "name": "Resource Tycoon", "description": "Hold 12+ resources", "points": 3,
             "claimed_by": self._project_claimed_by("resource_tycoon")},
            {"id": "road_king", "name": "Road King", "description": "Longest road ≥ 6", "points": 4,
             "claimed_by": self._project_claimed_by("road_king")},
            {"id": "hex_lord", "name": "Hex Lord", "description": "Buildings on 4+ resource types", "points": 4,
             "claimed_by": self._project_claimed_by("hex_lord")},
        ]

        scores = self._calculate_scores() if self.state.game_over else None

        view = {
            "round": self.state.round_num,
            "max_rounds": TOTAL_ROUNDS,
            "dice_roll": self.state.current_dice_roll,
            "phase": self.state.phase,
            "placement_round": self.state.placement_round,
            "placement_done": self.state.placement_done,
            "current_player": self.state.current_player,
            "current_actions_used": self.state.players[self.state.current_player].actions_used,
            "actions_used": self.state.players[player_id].actions_used,
            "actions_per_round": ACTIONS_PER_ROUND,
            "players": players_view,
        }
        if include_map:
            view["map"] = self._compact_map_view()
        view.update({
            "projects": projects_view,
            "pending_trade": pending_trade_view,
            "messages": [self._message_view(message) for message in self.state.messages],
            "expansion_targets": self._expansion_targets_view(player_id),
            "game_over": self.state.game_over,
            "winner": self.state.winner,
            "scores": scores,
            "my_player_id": player_id,
        })
        return view

    def _project_claimed_by(self, pid: str) -> Optional[int]:
        for p in self.state.players:
            for pr in p.projects:
                if pr["id"] == pid:
                    return p.player_id
        return None


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


def run_server(socket_path: str, seed: int = 42, num_players: int = 3,
               state_path: Optional[str] = None, event_log_path: Optional[str] = None):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    engine = TradeCraftEngine(seed=seed, num_players=num_players)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chmod(socket_path, 0o660)
    server.listen(num_players)

    conn_by_pid: dict[int, socket.socket] = {}
    pid_by_conn: dict[int, int] = {}
    sequence = 0

    _write_snapshot(state_path, engine, sequence, [])
    _append_event(event_log_path, {
        "type": "server_started",
        "seed": seed,
        "players": num_players,
        "socket_path": socket_path,
    })

    print(f"[TradeCraft] Listening on {socket_path} "
          f"(seed={seed}, players={num_players})", flush=True)

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

        while not engine.state.game_over:
            readable, _, _ = select.select(all_conns, [], [], None)

            for conn in readable:
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
                    "current_player": engine.state.current_player,
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
    parser.add_argument("--state-file", help="Write the latest game state as atomic JSON")
    parser.add_argument("--event-log", help="Append engine lifecycle and action events as JSONL")
    args = parser.parse_args()
    run_server(args.socket, args.seed, args.players, args.state_file, args.event_log)
