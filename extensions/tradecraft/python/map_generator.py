#!/usr/bin/env python3
"""TradeCraft Map Generator — cube-coordinate hexagonal grid."""

import random
from dataclasses import dataclass, field
from typing import Optional

TOTAL_ROUNDS = 10
ACTIONS_PER_ROUND = 2
MAX_SETTLEMENTS = 5
MAX_CITIES = 3
MAX_ROADS = 12
MIN_SETTLEMENT_DISTANCE = 2

RESOURCES = ["wood", "stone", "iron", "crystal", "silk"]
GOLD_RESOURCE = "gold"
DESERT = "desert"
RESOURCE_POOL = (["wood"]*3 + ["stone"]*3 + ["iron"]*3
                 + ["crystal"]*3 + ["silk"]*3 + ["gold"]*2 + ["desert"]*2)
DICE_DISTRIBUTION = [(2,1),(3,2),(4,2),(5,2),(6,2),(8,2),(9,2),(10,2),(11,1),(12,1)]
HEX_ROWS = [3, 4, 5, 4, 3]

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
    def hex_by_id(self, hid): return next(h for h in self.hexes if h.id == hid)
    def vertex_by_id(self, vid): return next(v for v in self.vertices if v.id == vid)
    def edge_by_id(self, eid): return next(e for e in self.edges if e.id == eid)


def _build_topology(hex_cubes: dict[tuple, int]):
    """Build vertex and edge topology from hex cube coordinates."""
    # Vertices: each hex has 6 corners. Dedup by (vx, vy, vz) integer triple.
    pos_to_vid = {}  # (vx,vy,vz) → vertex_id
    vid_to_hexes = {}  # vertex_id → sorted hex_ids

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

    # Sort vertex hex lists
    for vid in vid_to_hexes:
        vid_to_hexes[vid] = sorted(vid_to_hexes[vid],
                                   key=lambda x: int(x[1:]))

    # Edges: consecutive corners of each hex
    edge_set = {}  # (v1, v2) sorted → edge_id
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

    # Build vertices and edges
    vertices = []
    for pos, vid in sorted(pos_to_vid.items(), key=lambda x: int(x[1][1:])):
        vertices.append(Vertex(id=vid, hexes=vid_to_hexes[vid], pos=pos))

    edges = []
    for (v1, v2), eid in sorted(edge_set.items(), key=lambda x: int(x[1][1:])):
        edges.append(Edge(id=eid, v1=v1, v2=v2))

    # Compute adjacency
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


def generate_map(seed=42):
    rng = random.Random(seed)

    resources = list(RESOURCE_POOL); rng.shuffle(resources)
    numbers = []; [numbers.extend([num]*c) for num, c in DICE_DISTRIBUTION]
    rng.shuffle(numbers)

    # Build hexes
    hexes = []; hex_cubes = {}; hidx = num_idx = 0
    for r, count in enumerate(HEX_ROWS):
        for c in range(count):
            res = resources[hidx]
            num = numbers[num_idx] if res != DESERT else None
            if res != DESERT: num_idx += 1
            cube = _board_to_cube(r, c)
            hexes.append(HexTile(id=f"h{hidx}", row=r, col=c, resource=res,
                                 number=num, cube=cube))
            hex_cubes[cube] = hidx
            hidx += 1

    # Build topology
    vertices, edges = _build_topology(hex_cubes)

    dice_seq = [rng.randint(1, 6) + rng.randint(1, 6)
                for _ in range(TOTAL_ROUNDS)]

    return GameMap(hexes=hexes, vertices=vertices, edges=edges, seed=seed,
                   dice_sequence=dice_seq)


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
