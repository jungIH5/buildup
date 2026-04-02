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

# 코드 레벨 위치 조정 파라미터
STEP_MM: float = 50.0    # 한 번 밀 때 이동 거리
MAX_STEPS: int = 30       # 최대 시도 횟수 (수량 많을 때 더 넓은 범위 탐색)
CORRIDOR_GRID_MM: float = 150.0   # 통로 체크 격자 크기

# 사람 1명 통과 최소 통행폭 (브랜드 clearspace와 별개)
MIN_ACCESS_GAP_MM: float = 600.0


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
    주어진 위치에 오브젝트 배치 시도.
    충돌 시 STEP_MM 씩 밀어서 MAX_STEPS 회까지 재시도.
    - clearspace_mm: 접근성 체크에 쓸 최소 통행폭 (브랜드 clearspace가 아닌 사람 통행 기준)
    - 오브젝트 간 직접 충돌(0mm)은 항상 체크
    - 접근성: 최소 1개 면 이상 MIN_ACCESS_GAP_MM 이상 열려있어야 함
    성공하면 (polygon, cx, cy), 실패하면 (None, cx, cy).
    """
    directions = [(1, 0), (0, 1), (-1, 0), (0, -1),
                  (1, 1), (-1, 1), (1, -1), (-1, -1)]

    access_gap = max(clearspace_mm, MIN_ACCESS_GAP_MM)

    for step in range(MAX_STEPS + 1):
        for dx_sign, dy_sign in directions:
            if step == 0:
                test_cx, test_cy = cx, cy
            else:
                test_cx = cx + dx_sign * STEP_MM * step
                test_cy = cy + dy_sign * STEP_MM * step

            obj_poly = make_object_polygon(test_cx, test_cy, width_mm, height_mm, rotation_deg)

            if not room_poly.contains(obj_poly):
                continue
            if any(obj_poly.intersects(dz) for dz in dead_zones):
                continue
            # 오브젝트 직접 충돌 체크 (0mm 이격)
            if any(obj_poly.intersects(p) for p in placed_polys):
                continue

            # 접근성 체크: 최소 1개 면이 access_gap 이상 열려야 함
            if not _is_accessible(obj_poly, placed_polys, room_poly, access_gap):
                continue

            # NetworkX 통로 연결성 체크 (이미 배치된 오브젝트 포함)
            if corridor_graph is not None and entrance_pos is not None:
                if not _corridor_ok(corridor_graph, obj_poly, entrance_pos, placed_polys):
                    continue

            return obj_poly, test_cx, test_cy

        if step == 0:
            continue

    return None, cx, cy


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

    # priority 순 정렬
    sorted_placements = sorted(placements, key=lambda p: p.priority)

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
                "reason": f"'{intent.reference_point}' 기준점 주변 {MAX_STEPS * STEP_MM:.0f}mm 내에 배치 가능 공간 없음 (통로 포함)",
                "reference_point": intent.reference_point,
                "tried_steps": MAX_STEPS,
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
