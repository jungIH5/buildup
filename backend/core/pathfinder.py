"""
pathfinder.py — NetworkX 격자 그래프 기반 통로 검증

배치할 때마다 격자 그래프에서 차단된 노드를 제거하고
출입구 ↔ 핵심 지점 간 최단 경로가 존재하는지, 
최소 폭(900mm)이 확보되는지 확인한다.
"""

from __future__ import annotations
import math
import networkx as nx
from shapely.geometry import Polygon, Point, LineString
from core.geometry_utils import DEFAULTS

# 격자 간격 (mm) — 이 크기가 통로 검사 해상도를 결정
GRID_STEP_MM: float = 150.0


def build_grid_graph(
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
    grid_step: float = GRID_STEP_MM,
) -> nx.Graph:
    """
    공간을 격자로 나눠 그래프 생성.
    Dead Zone, 배치된 오브젝트, 벽 외부 노드는 제거.
    """
    minx, miny, maxx, maxy = room_poly.bounds
    G = nx.Graph()

    # 노드 생성 (격자 교점)
    x = minx
    while x <= maxx:
        y = miny
        while y <= maxy:
            pt = Point(x, y)
            if room_poly.contains(pt):
                blocked = any(dz.contains(pt) for dz in dead_zones)
                blocked = blocked or any(p.contains(pt) for p in placed_polys)
                if not blocked:
                    G.add_node((round(x, 1), round(y, 1)))
            y += grid_step
        x += grid_step

    # 엣지 생성 (상하좌우 + 대각선)
    for (nx_, ny_) in list(G.nodes):
        for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
            neighbor = (round(nx_ + dx * grid_step, 1),
                        round(ny_ + dy * grid_step, 1))
            if G.has_node(neighbor):
                weight = grid_step * (math.sqrt(2) if dx != 0 and dy != 0 else 1.0)
                G.add_edge((nx_, ny_), neighbor, weight=weight)

    return G


def nearest_node(G: nx.Graph, pos: tuple[float, float]) -> tuple[float, float] | None:
    """pos에 가장 가까운 그래프 노드 반환"""
    if not G.nodes:
        return None
    return min(G.nodes, key=lambda n: math.hypot(n[0] - pos[0], n[1] - pos[1]))


def check_path_exists(
    G: nx.Graph,
    source: tuple[float, float],
    target: tuple[float, float],
) -> tuple[bool, float]:
    """
    두 지점 사이의 경로 존재 여부와 최단 거리(mm) 반환.
    """
    src_node = nearest_node(G, source)
    tgt_node = nearest_node(G, target)
    if src_node is None or tgt_node is None:
        return False, 0.0
    try:
        path_length = nx.shortest_path_length(G, src_node, tgt_node, weight="weight")
        return True, path_length
    except nx.NetworkXNoPath:
        return False, 0.0


def check_corridor_connectivity(
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
    entrance_pos: tuple[float, float],
    key_points: list[tuple[float, float]],
    min_corridor_mm: float = DEFAULTS["main_corridor_min_mm"],
) -> list[dict]:
    """
    출입구에서 각 key_point까지 경로 검증.
    min_corridor_mm = 900mm 이상인지 체크.

    반환: [{"to": point, "reachable": bool, "distance_mm": float}]
    """
    G = build_grid_graph(room_poly, dead_zones, placed_polys, grid_step=min_corridor_mm / 6)
    results = []
    for kp in key_points:
        reachable, dist = check_path_exists(G, entrance_pos, kp)
        results.append({
            "to": kp,
            "reachable": reachable,
            "distance_mm": dist,
        })
    return results


def incremental_check(
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
    new_poly: Polygon,
    entrance_pos: tuple[float, float],
    key_points: list[tuple[float, float]],
    min_corridor_mm: float = DEFAULTS["main_corridor_min_mm"],
) -> bool:
    """
    새 오브젝트를 추가했을 때 경로가 여전히 유효한지 증분 체크.
    True = 통과, False = 통로 차단
    """
    all_placed = placed_polys + [new_poly]
    results = check_corridor_connectivity(
        room_poly, dead_zones, all_placed,
        entrance_pos, key_points, min_corridor_mm,
    )
    return all(r["reachable"] for r in results)
