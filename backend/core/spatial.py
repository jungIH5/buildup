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
            if not _is_accessible(obj_poly, placed_polys, room_poly, access_gap):
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
    if corridor_graph is not None and entrance_pos is not None:
        for score, obj_poly, bx, by in candidates[:10]:
            if _corridor_ok(corridor_graph, obj_poly, entrance_pos, placed_polys):
                return obj_poly, bx, by
        return None, cx, cy

    best = candidates[0]
    return best[1], best[2], best[3]


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

    for intent in sorted_placements:
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
