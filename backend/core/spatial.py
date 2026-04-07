"""
spatial.py — Shapely 기반 공간 분석 및 배치 계산

주요 기능:
1. Dead Zone 생성 (설비 주변 + 비상로 + 벽 이격)
2. 오브젝트 bbox 폴리곤 생성
3. 충돌 감지
4. 코드 레벨 위치 조정 (step_mm 단위 슬라이딩)
"""

from __future__ import annotations
import math
import networkx as nx
from shapely.geometry import Polygon, Point, box
from shapely.affinity import rotate
from core.schemas import (
    FloorAnalysis, PlacementIntent, PlacedObject,
    BrandStandards, LayoutResult, Violation, ViolationSeverity,
)
from core.violations import (
    check_dead_zone_intrusion,
    check_emergency_path,
    aggregate_violations,
)
from core.geometry_utils import DEFAULT_FURNITURE_HEIGHTS

# 그리드 스캔 파라미터
GRID_STEP_MM: float = 200.0        # x-y 그리드 간격 (방 전체를 균일하게 탐색)
CORRIDOR_GRID_MM: float = 150.0    # 통로 체크 격자 크기

# 사람 1명 통과 최소 통행폭 (브랜드 clearspace와 별개)
MIN_ACCESS_GAP_MM: float = 600.0

# 오브젝트 타입별 중요도 (낮을수록 먼저 배치)
OBJECT_PRIORITY: dict[str, int] = {
    "character_bbox": 1,    # 캐릭터 조형물 — 브랜드 상징, 최우선
    "photo_zone": 2,        # 포토존 — 방문객 체험 핵심
    "banner_stand": 3,      # 배너 스탠드 — 시각적 홍보
    "product_display": 4,   # 상품 진열대
    "shelf_rental": 5,      # 렌탈 선반
}

# 접근성 체크 면제 오브젝트 — 주변 오브젝트에 둘러싸여도 무방한 타입
# (관람/포토 목적으로 구석/코너 배치가 자연스러운 오브젝트)
ACCESSIBILITY_EXEMPT: frozenset[str] = frozenset({
    "character_bbox",  # 등신대는 동선 외 공간에 배치해도 됨
})


def _build_walkability_graph(
    room_poly: Polygon,
    obstacles: list[Polygon],
    grid_mm: float = CORRIDOR_GRID_MM,
) -> tuple[nx.Graph, set[tuple[int, int]]]:
    """
    방 내부 격자 그래프 생성 (장애물 노드 제외).
    반환: (Graph, node_set)
    """
    G: nx.Graph = nx.Graph()
    node_set: set[tuple[int, int]] = set()
    step = int(grid_mm)
    minx, miny, maxx, maxy = room_poly.bounds

    for x in range(int(minx), int(maxx) + step, step):
        for y in range(int(miny), int(maxy) + step, step):
            pt = Point(x, y)
            if room_poly.contains(pt) and not any(obs.contains(pt) for obs in obstacles):
                G.add_node((x, y))
                node_set.add((x, y))

    for (x, y) in list(G.nodes()):
        for dx, dy in [(step, 0), (0, step)]:
            nb = (x + dx, y + dy)
            if nb in node_set:
                G.add_edge((x, y), nb, weight=math.sqrt(dx ** 2 + dy ** 2))

    return G, node_set


def _corridor_ok(
    G_base: nx.Graph,
    new_obj_poly: Polygon,
    entrance_pos: tuple[float, float],
    placed_polys: list[Polygon] | None = None,
    min_reachable_fraction: float = 0.5,
) -> bool:
    """
    새 오브젝트 + 이미 배치된 오브젝트들을 포함해 입구에서 방의 50% 이상
    노드에 도달 가능한지 확인. 통로가 막히면 False.
    """
    if not G_base.nodes():
        return True

    # 새 오브젝트 + 기존 배치 오브젝트가 차지하는 노드 제거
    blocked: set = {n for n in G_base.nodes() if new_obj_poly.contains(Point(n[0], n[1]))}
    if placed_polys:
        for poly in placed_polys:
            blocked |= {n for n in G_base.nodes() if poly.contains(Point(n[0], n[1]))}

    if not blocked:
        return True

    G_temp = G_base.copy()
    G_temp.remove_nodes_from(blocked)

    if not G_temp.nodes():
        return False

    entrance_node = min(
        G_temp.nodes(),
        key=lambda n: (n[0] - entrance_pos[0]) ** 2 + (n[1] - entrance_pos[1]) ** 2,
    )
    component = nx.node_connected_component(G_temp, entrance_node)
    return len(component) / len(G_temp.nodes()) >= min_reachable_fraction


def _min_placed_distance(cx: float, cy: float, placed_polys: list[Polygon]) -> float:
    """기존 배치 오브젝트 중심까지의 최소 거리 (분산 점수)"""
    if not placed_polys:
        return float('inf')
    return min(
        math.sqrt(
            (cx - (p.bounds[0] + p.bounds[2]) / 2) ** 2 +
            (cy - (p.bounds[1] + p.bounds[3]) / 2) ** 2
        )
        for p in placed_polys
    )


def _score_position(
    x: float, y: float,
    ref_cx: float, ref_cy: float,
    placed_polys: list[Polygon],
) -> float:
    """
    배치 점수 = 분산 가중치(70%) + 기준점 근접 가중치(30%).
    첫 오브젝트(placed_polys 없음): 기준점에 가장 가까운 위치 우선.
    """
    ref_dist = math.sqrt((x - ref_cx) ** 2 + (y - ref_cy) ** 2)
    disp = _min_placed_distance(x, y, placed_polys)

    if disp == float('inf'):
        # 첫 오브젝트: 기준점에 가장 가까운 곳
        return -ref_dist

    # 기준점 근접 보너스 (5,000mm 거리에서 37% 수준으로 감소)
    ref_bonus = math.exp(-ref_dist / 5000.0)
    return disp * (0.7 + 0.3 * ref_bonus)


def build_dead_zones(
    floor: FloorAnalysis,
    standards: BrandStandards,
) -> list[Polygon]:
    """
    Dead Zone 폴리곤 목록 생성:
    - 감지된 설비 주변 clearspace
    - 비상구 주변 emergency_path_min_mm
    - 벽 이격 (wall_clearance_mm) → room_polygon 에서 inward buffer로 처리
    """
    dead_zones: list[Polygon] = []

    # Agent 2의 dead_zones_mm 만 사용 (equipment_detected는 이미 포함됨)
    for dz_coords in floor.dead_zones_mm:
        if len(dz_coords) >= 3:
            dead_zones.append(Polygon(dz_coords))

    return dead_zones


def get_reference_position(
    floor: FloorAnalysis,
    ref_name: str,
) -> tuple[float, float]:
    """reference_point 이름 → mm 좌표 변환"""
    for rp in floor.reference_points:
        if rp.name == ref_name:
            return rp.position_mm
    raise ValueError(f"reference_point '{ref_name}'을 찾을 수 없습니다.")


def make_object_polygon(
    cx: float, cy: float,
    width_mm: float, height_mm: float,
    rotation_deg: float = 0.0,
) -> Polygon:
    """중심점 기준 bbox 폴리곤 생성 (회전 포함)"""
    half_w = width_mm / 2
    half_h = height_mm / 2
    rect = box(cx - half_w, cy - half_h, cx + half_w, cy + half_h)
    if rotation_deg != 0.0:
        rect = rotate(rect, rotation_deg, origin=(cx, cy))
    return rect


def _is_accessible(
    obj_poly: Polygon,
    placed_polys: list[Polygon],
    room_poly: Polygon,
    clearspace_mm: float,
) -> bool:
    """
    오브젝트가 사람이 접근 가능한지 확인.
    - 4개 면 중 최소 1개 이상이 clearspace_mm 만큼 열려 있어야 함
    - 열린 면: 해당 방향으로 clearspace_mm 확장했을 때 다른 오브젝트나 벽과 겹치지 않는 영역
    """
    minx, miny, maxx, maxy = obj_poly.bounds
    half_cs = clearspace_mm

    # 4방향 접근 통로 후보 (오브젝트 옆에 clearspace 폭의 복도)
    side_rects = [
        box(minx, miny - half_cs, maxx, miny),           # 남쪽
        box(minx, maxy, maxx, maxy + half_cs),            # 북쪽
        box(minx - half_cs, miny, minx, maxy),            # 서쪽
        box(maxx, miny, maxx + half_cs, maxy),            # 동쪽
    ]

    obstacles = placed_polys  # 이미 배치된 오브젝트
    for side in side_rects:
        # 방 안에 있고, 다른 오브젝트와 겹치지 않으면 접근 가능
        if not room_poly.contains(side):
            continue
        if any(side.intersects(p) for p in obstacles):
            continue
        return True  # 최소 한 면은 열려 있음

    return False


def try_place_object(
    cx: float, cy: float,
    width_mm: float, height_mm: float,
    rotation_deg: float,
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
    corridor_graph: nx.Graph | None = None,
    entrance_pos: tuple[float, float] | None = None,
    clearspace_mm: float = MIN_ACCESS_GAP_MM,
    check_access: bool = True,
) -> tuple[Polygon | None, float, float]:
    """
    방 전체를 x-y 그리드 순서로 스캔하여 제약 조건을 만족하는 후보를 수집.
    (cx, cy)는 Agent 3이 지정한 기준점 — 분산 점수 계산 시 근접 가중치에 활용.
    스캔 순서: x=minx→maxx, y=miny→maxy (GRID_STEP_MM 간격).
    점수 = 기존 오브젝트와의 거리(분산) * 기준점 근접 보너스.
    최종 corridor 체크는 상위 후보에만 적용해 성능 확보.
    """
    access_gap = max(clearspace_mm, MIN_ACCESS_GAP_MM)

    minx, miny, maxx, maxy = room_poly.bounds
    half_w = width_mm / 2
    half_h = height_mm / 2

    candidates: list[tuple[float, Polygon, float, float]] = []  # (score, poly, x, y)

    # (0,0) 에 해당하는 room 기준 왼쪽-아래 모서리부터 x→y 순으로 스캔
    test_x = minx + half_w
    while test_x <= maxx - half_w:
        test_y = miny + half_h
        while test_y <= maxy - half_h:
            obj_poly = make_object_polygon(test_x, test_y, width_mm, height_mm, rotation_deg)

            # 제약 조건 검사 (순서: 빠른 것 먼저)
            if not room_poly.contains(obj_poly):
                test_y += GRID_STEP_MM
                continue
            if any(obj_poly.intersects(dz) for dz in dead_zones):
                test_y += GRID_STEP_MM
                continue
            if any(obj_poly.intersects(p) for p in placed_polys):
                test_y += GRID_STEP_MM
                continue
            if check_access and not _is_accessible(obj_poly, placed_polys, room_poly, access_gap):
                test_y += GRID_STEP_MM
                continue

            score = _score_position(test_x, test_y, cx, cy, placed_polys)
            candidates.append((score, obj_poly, test_x, test_y))

            test_y += GRID_STEP_MM
        test_x += GRID_STEP_MM

    if not candidates:
        return None, cx, cy

    # 분산+기준점 점수 기준 내림차순 정렬
    candidates.sort(key=lambda c: -c[0])

    # 상위 후보에 대해서만 통로 연결성 체크 (비용이 큰 검사)
    # check_access=False 면 접근성 면제 오브젝트 → 첫 후보 바로 반환
    if not check_access:
        best = candidates[0]
        return best[1], best[2], best[3]

    if corridor_graph is not None and entrance_pos is not None:
        for score, obj_poly, bx, by in candidates[:10]:
            if _corridor_ok(corridor_graph, obj_poly, entrance_pos, placed_polys):
                return obj_poly, bx, by
        return None, cx, cy

    best = candidates[0]
    return best[1], best[2], best[3]


def plan_cluster_layout(
    count: int,
    unit_w: float,
    unit_d: float,
    gap_mm: float = 50.0,
) -> list[list[tuple[float, float, float]]]:
    """
    N개의 product_display 배치 형태를 결정.
    반환: 서브클러스터별 [(rel_x, rel_y, rotation_deg), ...] 리스트의 리스트.

    배치 형태:
    - 1-2개: 1행 단열
    - 3-6개: 2행 배치 (앞행 ceil(N/2), 뒷행 나머지)
    - 7개↑: 1행 단열 — try_place_cluster의 wall snap이 벽 따라 일렬 배치.
             (분산 배치는 _split_pd_intents_into_groups에서 reference_point 기준으로만 처리)
    """
    if count <= 2:
        # 1행 단열
        return [[(i * (unit_w + gap_mm), 0.0, 0.0) for i in range(count)]]

    if count <= 6:
        # 최대 2행 배치
        cols = math.ceil(count / 2)
        rows = math.ceil(count / cols)  # 2 이하 보장
        units = []
        for r in range(rows):
            row_count = cols if (r < rows - 1 or count % cols == 0) else count % cols
            row_x_offset = ((cols - row_count) * (unit_w + gap_mm)) / 2  # 행 중앙 정렬
            for c in range(row_count):
                units.append((row_x_offset + c * (unit_w + gap_mm), r * (unit_d + gap_mm), 0.0))
        return [units]

    # 7개 이상: 단열로 벽 따라 배치 (try_place_cluster wall snap이 처리)
    return [[(i * (unit_w + gap_mm), 0.0, 0.0) for i in range(count)]]


def _cluster_bounding_box(
    cx: float, cy: float,
    units: list[tuple[float, float, float]],
    unit_w: float, unit_d: float,
) -> tuple[float, float, float, float]:
    """클러스터 전체의 bounding box 반환 (minx, miny, maxx, maxy). cx/cy는 클러스터 중심."""
    if not units:
        return cx, cy, cx, cy
    xs = [cx + rx - unit_w / 2 for rx, _, _ in units] + \
         [cx + rx + unit_w / 2 for rx, _, _ in units]
    ys = [cy + ry - unit_d / 2 for _, ry, _ in units] + \
         [cy + ry + unit_d / 2 for _, ry, _ in units]
    return min(xs), min(ys), max(xs), max(ys)


def _cluster_center_offset(units: list[tuple[float, float, float]]) -> tuple[float, float]:
    """유닛 상대좌표 리스트의 기하학적 중심 오프셋 반환."""
    if not units:
        return 0.0, 0.0
    cx = sum(rx for rx, _, _ in units) / len(units)
    cy = sum(ry for _, ry, _ in units) / len(units)
    return cx, cy


def try_place_cluster(
    ref_cx: float, ref_cy: float,
    units: list[tuple[float, float, float]],
    unit_w: float, unit_d: float,
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
    corridor_graph: nx.Graph | None = None,
    entrance_pos: tuple[float, float] | None = None,
    clearspace_mm: float = MIN_ACCESS_GAP_MM,
    gap_mm: float = 50.0,
) -> tuple[list[tuple[Polygon, float, float]] | None, float, float]:
    """
    클러스터 전체를 하나의 단위로 배치.
    반환: ([(poly, cx, cy), ...] | None, cluster_cx, cluster_cy)

    전략:
    1. 벽 인접(wall_snap): 4방향 벽에 클러스터 뒷면을 붙여 시도
    2. 실패 시 기준점 주변 그리드 스캔 (중앙 배치)

    접근성 보장:
    - 2행 배치면 앞면(y=miny 방향) clearspace_mm 통로 필수
    - 1행 배치면 앞·뒷면 중 하나 clearspace_mm 통로 필수
    """
    minx_r, miny_r, maxx_r, maxy_r = room_poly.bounds

    # 클러스터 상대좌표의 기하 중심을 기준점에 맞추기 위한 오프셋
    oc_x, oc_y = _cluster_center_offset(units)

    def _build_polys(cluster_cx: float, cluster_cy: float):
        polys = []
        for rx, ry, rot in units:
            ux = cluster_cx + rx - oc_x
            uy = cluster_cy + ry - oc_y
            polys.append((make_object_polygon(ux, uy, unit_w, unit_d, rot), ux, uy))
        return polys

    def _is_valid(cluster_cx: float, cluster_cy: float) -> bool:
        built = _build_polys(cluster_cx, cluster_cy)
        all_polys_for_cluster = [p for p, _, _ in built]

        # 1) 모든 유닛이 방 안에 있고, dead_zone/placed_poly와 충돌 없음
        for poly, ux, uy in built:
            if not room_poly.contains(poly):
                return False
            if any(poly.intersects(dz) for dz in dead_zones):
                return False
            if any(poly.intersects(pp) for pp in placed_polys):
                return False

        # 2) 클러스터 전체 bounding box 앞면(miny 방향) 통로 확보
        bminx, bminy, bmaxx, bmaxy = (
            min(p.bounds[0] for p in all_polys_for_cluster),
            min(p.bounds[1] for p in all_polys_for_cluster),
            max(p.bounds[2] for p in all_polys_for_cluster),
            max(p.bounds[3] for p in all_polys_for_cluster),
        )
        aisle = box(bminx, bminy - clearspace_mm, bmaxx, bminy)
        aisle_ok = room_poly.contains(aisle) and \
                   not any(aisle.intersects(pp) for pp in placed_polys) and \
                   not any(aisle.intersects(dz) for dz in dead_zones)

        if not aisle_ok:
            # 뒷면 통로로 대안 시도
            aisle_back = box(bminx, bmaxy, bmaxx, bmaxy + clearspace_mm)
            aisle_ok = room_poly.contains(aisle_back) and \
                       not any(aisle_back.intersects(pp) for pp in placed_polys) and \
                       not any(aisle_back.intersects(dz) for dz in dead_zones)

        if not aisle_ok:
            return False

        # 3) corridor 연결성 체크 (비용이 크므로 마지막에)
        if corridor_graph is not None and entrance_pos is not None:
            combined = all_polys_for_cluster[0]
            for p in all_polys_for_cluster[1:]:
                combined = combined.union(p)
            if not _corridor_ok(corridor_graph, combined, entrance_pos, placed_polys):
                return False

        return True

    # ── 전략 1: 벽 인접 (wall_snap) — 항상 1행 단열로 시도 ─────────────────
    # 벽에 붙을 때는 반드시 일렬 정렬 → 안쪽 유닛이 갇히는 현상 방지
    n_units = len(units)
    single_row_h_units = [(i * (unit_w + gap_mm), 0.0, 0.0) for i in range(n_units)]  # 가로 단열
    single_row_v_units = [(0.0, i * (unit_d + gap_mm), 0.0) for i in range(n_units)]  # 세로 단열

    def _bbox(us: list[tuple[float, float, float]]) -> tuple[float, float]:
        """유닛 리스트의 (폭, 높이) 반환"""
        if not us:
            return unit_w, unit_d
        w = max(rx for rx, _, _ in us) - min(rx for rx, _, _ in us) + unit_w
        h = max(ry for _, ry, _ in us) - min(ry for _, ry, _ in us) + unit_d
        return w, h

    def _try_wall_snap(wall_units: list[tuple[float, float, float]]) -> tuple | None:
        """주어진 유닛 배치로 4방향 벽 snap 시도. 성공하면 (built, cx, cy) 반환."""
        oc_wx, oc_wy = _cluster_center_offset(wall_units)
        wbbox_w, wbbox_h = _bbox(wall_units)

        def _build_wall_polys(ccx: float, ccy: float):
            return [
                (make_object_polygon(ccx + rx - oc_wx, ccy + ry - oc_wy, unit_w, unit_d, rot),
                 ccx + rx - oc_wx, ccy + ry - oc_wy)
                for rx, ry, rot in wall_units
            ]

        def _wall_valid(ccx: float, ccy: float) -> bool:
            built = _build_wall_polys(ccx, ccy)
            for poly, _, _ in built:
                if not room_poly.contains(poly): return False
                if any(poly.intersects(dz) for dz in dead_zones): return False
                if any(poly.intersects(pp) for pp in placed_polys): return False
            all_p = [p for p, _, _ in built]
            bminx = min(p.bounds[0] for p in all_p)
            bminy = min(p.bounds[1] for p in all_p)
            bmaxx = max(p.bounds[2] for p in all_p)
            bmaxy = max(p.bounds[3] for p in all_p)
            aisle_f = box(bminx, bminy - clearspace_mm, bmaxx, bminy)
            aisle_b = box(bminx, bmaxy, bmaxx, bmaxy + clearspace_mm)
            aisle_l = box(bminx - clearspace_mm, bminy, bminx, bmaxy)
            aisle_r = box(bmaxx, bminy, bmaxx + clearspace_mm, bmaxy)
            for aisle in (aisle_f, aisle_b, aisle_l, aisle_r):
                if room_poly.contains(aisle) \
                   and not any(aisle.intersects(pp) for pp in placed_polys) \
                   and not any(aisle.intersects(dz) for dz in dead_zones):
                    return True
            return False

        candidates = [
            (ref_cx, miny_r + wbbox_h / 2 + gap_mm),   # 북벽
            (ref_cx, maxy_r - wbbox_h / 2 - gap_mm),   # 남벽
            (minx_r + wbbox_w / 2 + gap_mm, ref_cy),    # 서벽
            (maxx_r - wbbox_w / 2 - gap_mm, ref_cy),    # 동벽
        ]
        for wcx, wcy in candidates:
            if _wall_valid(wcx, wcy):
                return _build_wall_polys(wcx, wcy), wcx, wcy
        return None

    # 가로 단열 먼저, 실패 시 세로 단열 시도
    for row_units in (single_row_h_units, single_row_v_units):
        result = _try_wall_snap(row_units)
        if result is not None:
            return result[0], result[1], result[2]

    # 단열 실패 시 기존 격자 형태로 벽 snap 재시도
    dummy = _build_polys(ref_cx, ref_cy)
    if dummy:
        all_dummy = [p for p, _, _ in dummy]
        cluster_h = max(p.bounds[3] for p in all_dummy) - min(p.bounds[1] for p in all_dummy)
        cluster_w = max(p.bounds[2] for p in all_dummy) - min(p.bounds[0] for p in all_dummy)
    else:
        cluster_h = unit_d
        cluster_w = unit_w

    for wcx, wcy in [
        (ref_cx, miny_r + cluster_h / 2 + gap_mm),
        (ref_cx, maxy_r - cluster_h / 2 - gap_mm),
        (minx_r + cluster_w / 2 + gap_mm, ref_cy),
        (maxx_r - cluster_w / 2 - gap_mm, ref_cy),
    ]:
        if _is_valid(wcx, wcy):
            return _build_polys(wcx, wcy), wcx, wcy

    # ── 전략 2: 기준점 주변 그리드 스캔 ────────────────────────
    step = GRID_STEP_MM
    half_w = cluster_w / 2
    half_h = cluster_h / 2

    candidates: list[tuple[float, float, float]] = []
    scan_x = minx_r + half_w
    while scan_x <= maxx_r - half_w:
        scan_y = miny_r + half_h
        while scan_y <= maxy_r - half_h:
            if _is_valid(scan_x, scan_y):
                dist = math.sqrt((scan_x - ref_cx) ** 2 + (scan_y - ref_cy) ** 2)
                disp = _min_placed_distance(scan_x, scan_y, placed_polys)
                score = disp * 0.7 - dist * 0.3
                candidates.append((score, scan_x, scan_y))
            scan_y += step
        scan_x += step

    if not candidates:
        return None, ref_cx, ref_cy

    candidates.sort(key=lambda c: -c[0])
    best_score, best_cx, best_cy = candidates[0]
    built = _build_polys(best_cx, best_cy)
    return built, best_cx, best_cy


def _split_pd_intents_into_groups(
    pd_intents: list,
) -> list[list]:
    """
    product_display PlacementIntent 목록을 클러스터 그룹으로 분리.
    - LLM이 서로 다른 reference_point를 지정했으면 그 기준으로 분리
    - 같은 reference_point끼리는 하나의 클러스터로 유지 (강제 분리 없음)
      → 다수 배치 요구 시 벽 한 면에 연속 배치 가능
    """
    from collections import defaultdict
    groups_by_ref: dict[str, list] = defaultdict(list)
    for intent in pd_intents:
        groups_by_ref[intent.reference_point].append(intent)
    return list(groups_by_ref.values())


def compute_layout(
    floor: FloorAnalysis,
    standards: BrandStandards,
    placements: list[PlacementIntent],
    furniture_sizes: dict[str, tuple[float, float]],
    emergency_exits: list[tuple[float, float]] | None = None,
    initial_placed_polys: list[Polygon] | None = None,
) -> LayoutResult:
    """
    Agent 3의 배치 의도(PlacementIntent 목록)를 받아
    Shapely로 실제 좌표 계산 후 LayoutResult 반환.

    - 성공: placed 리스트에 추가
    - 실패: failed 리스트에 추가 + 이유 기록
    - blocking violation 있으면 glb_blocked = True
    """
    room_poly = Polygon(floor.room_polygon_mm)
    dead_zones = build_dead_zones(floor, standards)

    placed_objects: list[PlacedObject] = []
    placed_polys: list[Polygon] = list(initial_placed_polys) if initial_placed_polys else []
    failed_objects: list[dict] = []
    all_violations: list[Violation] = []

    # 입구 위치 + NetworkX 기본 격자 그래프 (Dead Zone만 제외, 오브젝트 제외 전)
    entrance_rp = next((rp for rp in floor.reference_points if rp.name == "entrance"), None)
    entrance_pos: tuple[float, float] | None = entrance_rp.position_mm if entrance_rp else None
    corridor_graph, _ = _build_walkability_graph(room_poly, dead_zones)

    # 오브젝트 중요도(OBJECT_PRIORITY) 우선, 같으면 LLM이 부여한 priority 순
    sorted_placements = sorted(
        placements,
        key=lambda p: (OBJECT_PRIORITY.get(p.object_type, 10), p.priority),
    )

    # ── product_display 클러스터 배치 ──────────────────────────────
    pd_intents = [p for p in sorted_placements if p.object_type == "product_display"]
    other_intents = [p for p in sorted_placements if p.object_type != "product_display"]

    if pd_intents and "product_display" in furniture_sizes:
        pd_w, pd_d = furniture_sizes["product_display"]
        pd_height = (
            standards.furniture_heights_mm.get("product_display")
            or DEFAULT_FURNITURE_HEIGHTS.get("product_display", 1500.0)
        )
        pd_groups = _split_pd_intents_into_groups(pd_intents)

        for group in pd_groups:
            # 그룹 내 첫 번째 인텐트의 reference_point를 클러스터 기준점으로 사용
            try:
                ref_pos = get_reference_position(floor, group[0].reference_point)
            except ValueError as e:
                for intent in group:
                    failed_objects.append({
                        "object_type": "product_display",
                        "reason": str(e),
                        "reference_point": intent.reference_point,
                    })
                continue

            # 클러스터 형태 결정 (서브클러스터 목록 반환)
            sub_clusters = plan_cluster_layout(len(group), pd_w, pd_d)

            intent_idx = 0
            for sub_units in sub_clusters:
                sub_count = len(sub_units)
                sub_group = group[intent_idx: intent_idx + sub_count]
                intent_idx += sub_count

                result_units, cluster_cx, cluster_cy = try_place_cluster(
                    ref_pos[0], ref_pos[1],
                    sub_units, pd_w, pd_d,
                    room_poly, dead_zones, placed_polys,
                    corridor_graph=corridor_graph,
                    entrance_pos=entrance_pos,
                    clearspace_mm=max(standards.clearspace_mm, MIN_ACCESS_GAP_MM),
                )

                if result_units is None:
                    for intent in sub_group:
                        failed_objects.append({
                            "object_type": "product_display",
                            "reason": f"클러스터 배치 실패 — 방 전체 스캔에서 접근성을 만족하는 공간 없음",
                            "reference_point": intent.reference_point,
                        })
                    continue

                for (poly, ux, uy), intent in zip(result_units, sub_group):
                    violations: list[Violation] = []
                    violations += check_dead_zone_intrusion(poly, dead_zones, "product_display")
                    if emergency_exits:
                        violations += check_emergency_path(
                            poly, emergency_exits, "product_display",
                            standards.emergency_path_min_mm,
                        )
                    has_blocking = any(v.severity == ViolationSeverity.BLOCKING for v in violations)
                    if has_blocking:
                        failed_objects.append({
                            "object_type": "product_display",
                            "reason": "; ".join(v.detail for v in violations if v.severity == ViolationSeverity.BLOCKING),
                            "reference_point": intent.reference_point,
                        })
                        continue
                    all_violations.extend(violations)
                    placed_polys.append(poly)
                    placed_objects.append(PlacedObject(
                        object_type="product_display",
                        position_mm=(ux, uy),
                        rotation_deg=0.0,
                        bbox_mm=(pd_w, pd_d),
                        height_mm=pd_height,
                        reference_point=intent.reference_point,
                        placed_because=intent.placed_because,
                    ))

    for intent in other_intents:
        # 오브젝트 크기 조회
        if intent.object_type not in furniture_sizes:
            failed_objects.append({
                "object_type": intent.object_type,
                "reason": f"furniture_standards에 '{intent.object_type}' 크기 정보 없음",
                "reference_point": intent.reference_point,
            })
            continue

        width_mm, depth_mm = furniture_sizes[intent.object_type]
        # 높이: 브랜드 메뉴얼 추출값 → 기본값 순서로 적용
        height_mm = (
            standards.furniture_heights_mm.get(intent.object_type)
            or DEFAULT_FURNITURE_HEIGHTS.get(intent.object_type, 1500.0)
        )

        # 기준점 좌표 조회
        try:
            ref_pos = get_reference_position(floor, intent.reference_point)
        except ValueError as e:
            failed_objects.append({
                "object_type": intent.object_type,
                "reason": str(e),
                "reference_point": intent.reference_point,
            })
            continue

        cx, cy = ref_pos
        rotation_deg = _direction_to_rotation(intent.direction)

        # 배치 시도 (충돌 + 접근성 + NetworkX 통로 연결성 체크)
        # clearspace_mm: 사람 통행 최소폭(600mm) 기준 — 브랜드 clearspace와 별개
        obj_poly, final_cx, final_cy = try_place_object(
            cx, cy, width_mm, depth_mm, rotation_deg,
            room_poly, dead_zones, placed_polys,
            corridor_graph=corridor_graph,
            entrance_pos=entrance_pos,
            check_access=(intent.object_type not in ACCESSIBILITY_EXEMPT),
        )

        if obj_poly is None:
            failed_objects.append({
                "object_type": intent.object_type,
                "reason": f"방 전체 그리드 스캔 ({GRID_STEP_MM:.0f}mm 간격)에서 배치 가능 공간 없음 (충돌·데드존·접근성·통로 제약)",
                "reference_point": intent.reference_point,
            })
            continue

        # Violation 체크 (배치 성공이어도 위반일 수 있음)
        violations: list[Violation] = []
        violations += check_dead_zone_intrusion(obj_poly, dead_zones, intent.object_type)
        if emergency_exits:
            violations += check_emergency_path(
                obj_poly, emergency_exits, intent.object_type,
                standards.emergency_path_min_mm,
            )

        has_blocking = any(v.severity == ViolationSeverity.BLOCKING for v in violations)

        if has_blocking:
            # blocking violation → 배치 실패 처리
            failed_objects.append({
                "object_type": intent.object_type,
                "reason": "; ".join(v.detail for v in violations if v.severity == ViolationSeverity.BLOCKING),
                "reference_point": intent.reference_point,
                "violations": [v.model_dump() for v in violations],
            })
            continue

        all_violations.extend(violations)
        placed_polys.append(obj_poly)
        placed_objects.append(PlacedObject(
            object_type=intent.object_type,
            position_mm=(final_cx, final_cy),
            rotation_deg=rotation_deg,
            bbox_mm=(width_mm, depth_mm),
            height_mm=height_mm,
            reference_point=intent.reference_point,
            placed_because=intent.placed_because,
        ))

    _, glb_blocked = aggregate_violations(all_violations)

    return LayoutResult(
        placed=placed_objects,
        failed=failed_objects,
        violations=all_violations,
        glb_blocked=glb_blocked,
        disclaimer_items=floor.disclaimer_items,
    )


def _direction_to_rotation(direction: str) -> float:
    """방향 문자열 → 회전각(도)"""
    mapping = {
        "north": 0.0, "north_facing": 0.0,
        "east": 90.0, "east_facing": 90.0,
        "south": 180.0, "south_facing": 180.0, "entrance_facing": 180.0,
        "west": 270.0, "west_facing": 270.0,
        "wall_facing": 0.0, "inward": 0.0, "center": 0.0,
    }
    return mapping.get(direction.lower(), 0.0)
