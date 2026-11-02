#!/usr/bin/env python3
"""TradeCraft Map Generator — cube-coordinate hexagonal grid."""

import random
from dataclasses import dataclass, field
from typing import Optional

TOTAL_ROUNDS = 12
ACTIONS_PER_ROUND = 1
ACTION_LIMIT = 3  # legacy knob, unused by pacing

# Per-wake quotas. One wake may contain at most this many of each kind:
# build and claim_project share one slot (mutual exclusion), while the free
# negotiation steps get a small allowance so a wake cannot loop on them.
WAKE_ACTION_LIMIT = 1
WAKE_TRADE_LIMIT = 3
WAKE_CHAT_LIMIT = 3
MAX_SETTLEMENTS = 5
MAX_CITIES = 3
MAX_ROADS = 12
MIN_SETTLEMENT_DISTANCE = 2

# Catan base-game resource set. Five resources, no "wildcard" resource: the
# only source of flexibility is the bank/port ratio trade, which always costs
# more than a player-to-player swap (that asymmetry is what creates bargaining).
RESOURCES = ["wood", "brick", "sheep", "wheat", "ore"]
DESERT = "desert"
# Base Catan terrain mix: 4 forest, 3 brick, 4 pasture, 4 field, 3 mountain,
# 1 desert = 19 hexes. The desert is pinned to the centre hex.
RESOURCE_POOL = (["wood"]*4 + ["brick"]*3 + ["sheep"]*4
                 + ["wheat"]*4 + ["ore"]*3)
# Base Catan number tokens: one each of 2 and 12, two each of 3..6 and 8..11
# = 18 tokens for the 18 non-desert hexes. 7 is deliberately absent.
DICE_DISTRIBUTION = [(2,1),(3,2),(4,2),(5,2),(6,2),(8,2),(9,2),(10,2),(11,2),(12,1)]
HEX_ROWS = [3, 4, 5, 4, 3]
RED_NUMBERS = {6, 8}  # Catan "red number" tokens: may not sit adjacent

# Centre hex (row, col) is always desert and hosts the robber at match start.
CENTER_CELL = (2, 2)
# The six corner hexes of the radius-2 board. Each has exactly two vertices
# that touch no other hex — those twelve vertices are the generic ports.
CORNER_CELLS = [(0, 0), (0, 2), (2, 0), (2, 4), (4, 0), (4, 2)]

# Bank trade ratios: 4:1 normally, 3:1 when the trader owns a port vertex.
BANK_RATE_DEFAULT = 4
BANK_RATE_PORT = 3

# 6 uniform neighbor directions in cube coords
CUBE_DIRS = [(1,-1,0),(1,0,-1),(0,1,-1),(-1,1,0),(-1,0,1),(0,-1,1)]
# 6 corner offsets from hex center to vertex (in units of 1/3)
CORNER_OFFSETS = [(2,-1,-1),(1,1,-2),(-1,2,-1),(-2,1,1),(-1,-1,2),(1,-2,1)]


def _board_to_cube(r, c):
    """Map a local row/column to the centered radius-2 board."""
    radius = len(HEX_ROWS) // 2
    x = c - min(r, radius)
    z = r - radius
    return (x, -x - z, z)


@dataclass
class HexTile:
    id: str; row: int; col: int; resource: str; number: Optional[int] = None
    cube: tuple[int,int,int] = (0,0,0)
    @property
    def is_desert(self): return self.resource == DESERT


@dataclass
class Vertex:
    id: str; hexes: list[str] = field(default_factory=list)
    adjacent_vertices: list[str] = field(default_factory=list)
    pos: tuple[int,int,int] = (0,0,0)


@dataclass
class Edge:
    id: str; v1: str; v2: str
    adjacent_edges: list[str] = field(default_factory=list)


@dataclass
class GameMap:
    hexes: list[HexTile]; vertices: list[Vertex]; edges: list[Edge]
    seed: int; dice_sequence: list[int] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    robber_start: str = ""
    def hex_by_id(self, hid): return next(h for h in self.hexes if h.id == hid)
    def vertex_by_id(self, vid): return next(v for v in self.vertices if v.id == vid)
    def edge_by_id(self, eid): return next(e for e in self.edges if e.id == eid)


def cell_to_hex_id(r: int, c: int) -> str:
    """Row-major hex id for a (row, col) board cell."""
    idx = 0
    for row, count in enumerate(HEX_ROWS):
        if row == r:
            return f"h{idx + c}"
        idx += count
    raise ValueError(f"cell out of board: {(r, c)}")


def _build_topology(hex_cubes: dict[tuple, int]):
    """Build vertex and edge topology from hex cube coordinates."""
    pos_to_vid = {}
    vid_to_hexes = {}

    for (hx, hy, hz), hidx in hex_cubes.items():
        for c, (cx, cy, cz) in enumerate(CORNER_OFFSETS):
            pos = (3*hx + cx, 3*hy + cy, 3*hz + cz)
            if pos not in pos_to_vid:
                vid = f"v{len(pos_to_vid)}"
                pos_to_vid[pos] = vid
                vid_to_hexes[vid] = []
            hid = f"h{hidx}"
            if hid not in vid_to_hexes[pos_to_vid[pos]]:
                vid_to_hexes[pos_to_vid[pos]].append(hid)

    for vid in vid_to_hexes:
        vid_to_hexes[vid] = sorted(vid_to_hexes[vid],
                                   key=lambda x: int(x[1:]))

    edge_set = {}
    for (hx, hy, hz), hidx in hex_cubes.items():
        for c in range(6):
            c2 = (c + 1) % 6
            cx1,cy1,cz1 = CORNER_OFFSETS[c]
            cx2,cy2,cz2 = CORNER_OFFSETS[c2]
            v1_pos = (3*hx+cx1, 3*hy+cy1, 3*hz+cz1)
            v2_pos = (3*hx+cx2, 3*hy+cy2, 3*hz+cz2)
            v1 = pos_to_vid[v1_pos]
            v2 = pos_to_vid[v2_pos]
            ek = tuple(sorted([v1, v2]))
            if ek not in edge_set:
                edge_set[ek] = f"e{len(edge_set)}"

    vertices = []
    for pos, vid in sorted(pos_to_vid.items(), key=lambda x: int(x[1][1:])):
        vertices.append(Vertex(id=vid, hexes=vid_to_hexes[vid], pos=pos))

    edges = []
    for (v1, v2), eid in sorted(edge_set.items(), key=lambda x: int(x[1][1:])):
        edges.append(Edge(id=eid, v1=v1, v2=v2))

    edge_map = {}
    for e in edges:
        edge_map.setdefault(e.v1, []).append(e.id)
        edge_map.setdefault(e.v2, []).append(e.id)

    for e in edges:
        adj = set()
        for a in edge_map.get(e.v1, []):
            if a != e.id: adj.add(a)
        for a in edge_map.get(e.v2, []):
            if a != e.id: adj.add(a)
        e.adjacent_edges = sorted(adj)

    for v in vertices:
        adj = set()
        for eid in edge_map.get(v.id, []):
            e_obj = edges[int(eid[1:])]
            nb = e_obj.v1 if e_obj.v2 == v.id else e_obj.v2
            adj.add(nb)
        v.adjacent_vertices = sorted(adj)

    return vertices, edges


def _build_hex_adjacency() -> dict[int, list[int]]:
    """hex idx -> sorted list of adjacent hex idx (radius-2 board)."""
    cubes = []
    for r, count in enumerate(HEX_ROWS):
        for c in range(count):
            cubes.append(_board_to_cube(r, c))
    cube_to_idx = {cube: i for i, cube in enumerate(cubes)}
    adj = {i: [] for i in range(len(cubes))}
    for i, cube in enumerate(cubes):
        for d in CUBE_DIRS:
            nb = (cube[0]+d[0], cube[1]+d[1], cube[2]+d[2])
            if nb in cube_to_idx:
                adj[i].append(cube_to_idx[nb])
    for i in adj:
        adj[i].sort()
    return adj


_HEX_ADJACENCY = None
def _hex_adjacency() -> dict[int, list[int]]:
    global _HEX_ADJACENCY
    if _HEX_ADJACENCY is None:
        _HEX_ADJACENCY = _build_hex_adjacency()
    return _HEX_ADJACENCY


def _assign_anticlustered(items: list, positions: list[int],
                          conflict, rng: random.Random,
                          max_retries: int = 200) -> Optional[dict[int, object]]:
    """Greedy (MRV) placement with retry so adjacent positions never conflict.

    ``positions`` are the target hex indices; ``items`` the values to place
    (one per position). ``conflict(a, b)`` decides whether two values may not
    sit on adjacent hexes. Returns ``{position: item}`` or None if no conflict-
    free assignment was found within ``max_retries`` random restarts.
    """
    adj = _hex_adjacency()
    order = sorted(positions, key=lambda p: -len(adj[p]))  # most constrained first
    for _ in range(max_retries):
        remaining = list(items)
        rng.shuffle(remaining)
        assignment = {}
        ok = True
        for pos in order:
            # forbidden values: those conflicting with any already-placed neighbor
            forbidden = set()
            for nb in adj[pos]:
                placed = assignment.get(nb)
                if placed is None:
                    continue
                for it in remaining:
                    if conflict(it, placed):
                        forbidden.add(it)
            candidates = [it for it in remaining if it not in forbidden]
            if not candidates:
                ok = False
                break
            # MRV: prefer the value with fewest remaining copies (use rare first)
            chosen = min(candidates, key=lambda it: remaining.count(it))
            assignment[pos] = chosen
            remaining.remove(chosen)
        if ok and len(assignment) == len(positions):
            return assignment
    return None


def generate_map(seed=42, total_rounds=TOTAL_ROUNDS, map_seed=None, num_players=4):
    """Generate a balanced map. ``map_seed`` (optional) independently controls
    the resource/number layout; ``seed`` controls the dice sequence (and is the
    fallback map seed when ``map_seed`` is None, keeping old behavior intact).

    Balance (b-mode) is always applied:
      * the centre hex is always desert (robber's starting hex);
      * resource anti-clustering — same resource never on adjacent hexes;
      * number balancing — red numbers (6, 8) never adjacent.
    """
    map_rng = random.Random(seed if map_seed is None else map_seed)
    dice_rng = random.Random(seed)

    adj = _hex_adjacency()
    positions = list(adj.keys())  # 0..18
    center_idx = int(cell_to_hex_id(*CENTER_CELL)[1:])
    resource_positions = [p for p in positions if p != center_idx]

    # 1) resources: anti-cluster same resources; the centre stays desert so it
    # never carries a number token and never produces.
    res_ass = _assign_anticlustered(list(RESOURCE_POOL), resource_positions,
                                    lambda a, b: a == b, map_rng)
    if res_ass is None:
        # fallback: plain shuffle (should be vanishingly rare)
        res = list(RESOURCE_POOL)
        map_rng.shuffle(res)
        res_ass = {p: res[i] for i, p in enumerate(resource_positions)}
    res_ass[center_idx] = DESERT

    # 2) numbers: red numbers (6,8) never adjacent
    numbers = []
    for num, cnt in DICE_DISTRIBUTION:
        numbers.extend([num]*cnt)
    non_desert = [p for p in resource_positions]
    num_ass = _assign_anticlustered(numbers, non_desert,
                                    lambda a, b: a in RED_NUMBERS and b in RED_NUMBERS,
                                    map_rng)
    if num_ass is None:
        nums = list(numbers)
        map_rng.shuffle(nums)
        num_ass = {p: nums[i] for i, p in enumerate(non_desert)}

    # Build hexes in row-major order
    hexes = []
    hex_cubes = {}
    hidx = 0
    for r, count in enumerate(HEX_ROWS):
        for c in range(count):
            cube = _board_to_cube(r, c)
            res = res_ass[hidx]
            num = num_ass.get(hidx) if res != DESERT else None
            hexes.append(HexTile(id=f"h{hidx}", row=r, col=c, resource=res,
                                 number=num, cube=cube))
            hex_cubes[cube] = hidx
            hidx += 1

    vertices, edges = _build_topology(hex_cubes)

    # Ports: the two single-hex vertices of each of the six corner hexes.
    ports: list[str] = []
    for r, c in CORNER_CELLS:
        hid = cell_to_hex_id(r, c)
        vs = [v.id for v in vertices if hid in v.hexes and len(v.hexes) == 1]
        ports.extend(sorted(vs, key=lambda x: int(x[1:])))

    # One production roll per player per round: each player rolls once at their
    # first wake of the round, and every roll settles for the whole table.
    dice_seq = [dice_rng.randint(1, 6) + dice_rng.randint(1, 6)
                for _ in range(total_rounds * num_players)]

    return GameMap(hexes=hexes, vertices=vertices, edges=edges, seed=seed,
                   dice_sequence=dice_seq, ports=ports,
                   robber_start=cell_to_hex_id(*CENTER_CELL))


def compact_map_view(game_map: GameMap) -> dict:
    """Serialize the static map topology to its compact dictionary form."""
    return {
        "hexes": {h.id: [h.resource, h.number] for h in game_map.hexes},
        "vertices": {v.id: list(v.hexes) for v in game_map.vertices},
        "edges": {e.id: [e.v1, e.v2] for e in game_map.edges},
        "ports": list(game_map.ports),
    }


if __name__ == "__main__":
    m = generate_map(42)
    print(f"Hexes: {len(m.hexes)}, Vertices: {len(m.vertices)}, Edges: {len(m.edges)}")
    from collections import Counter
    cnt = Counter(len(v.hexes) for v in m.vertices)
    print(f"By hex-count: {dict(cnt)}")
    deg = Counter(len(v.adjacent_vertices) for v in m.vertices)
    print(f"By degree: {dict(deg)}")
    for v in m.vertices:
        for av in v.adjacent_vertices:
            nb = m.vertex_by_id(av)
            assert v.id in nb.adjacent_vertices
    print("✅ Topology valid")

    # balance checks
    adj = _hex_adjacency()
    # same resource adjacent?
    bad_res = 0
    for i in adj:
        for j in adj[i]:
            if j < i:
                continue
            if m.hexes[i].resource == m.hexes[j].resource:
                bad_res += 1
    # red numbers adjacent?
    bad_red = 0
    for i in adj:
        for j in adj[i]:
            if j < i:
                continue
            ni, nj = m.hexes[i].number, m.hexes[j].number
            if ni in RED_NUMBERS and nj in RED_NUMBERS:
                bad_red += 1
    print(f"resource-adjacency violations: {bad_res}, red-adjacency violations: {bad_red}")