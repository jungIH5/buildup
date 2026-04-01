"""
spatial.py — Shapely 기반 공간 분석 및 배치 계산

주요 기능:
1. Dead Zone 생성 (설비 주변 + 비상로 + 벽 이격)
2. 오브젝트 bbox 폴리곤 생성
3. 충돌 감지
4. 코드 레벨 위치 조정 (step_mm 단위 슬라이딩)
"""

from __future__ import annotations
from shapely.geometry import Polygon, box
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

# 코드 레벨 위치 조정 파라미터 (Sprint 5에서 조정 예정)
STEP_MM: float = 50.0    # 한 번 밀 때 이동 거리
MAX_STEPS: int = 20       # 최대 시도 횟수


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


def try_place_object(
    cx: float, cy: float,
    width_mm: float, height_mm: float,
    rotation_deg: float,
    room_poly: Polygon,
    dead_zones: list[Polygon],
    placed_polys: list[Polygon],
) -> tuple[Polygon | None, float, float]:
    """
    주어진 위치에 오브젝트 배치 시도.
    충돌 시 STEP_MM 씩 밀어서 MAX_STEPS 회까지 재시도.
    성공하면 (polygon, cx, cy), 실패하면 (None, cx, cy).
    """
    directions = [(1, 0), (0, 1), (-1, 0), (0, -1),
                  (1, 1), (-1, 1), (1, -1), (-1, -1)]

    for step in range(MAX_STEPS + 1):
        for dx_sign, dy_sign in directions:
            if step == 0:
                test_cx, test_cy = cx, cy
            else:
                test_cx = cx + dx_sign * STEP_MM * step
                test_cy = cy + dy_sign * STEP_MM * step

            obj_poly = make_object_polygon(test_cx, test_cy, width_mm, height_mm, rotation_deg)

            # 룸 외곽 이탈 체크
            if not room_poly.contains(obj_poly):
                continue

            # Dead Zone 충돌 체크
            if any(obj_poly.intersects(dz) for dz in dead_zones):
                continue

            # 기존 배치 오브젝트 충돌 체크
            if any(obj_poly.intersects(p) for p in placed_polys):
                continue

            return obj_poly, test_cx, test_cy

        if step == 0:
            continue  # step 0은 방향 없이 한 번만

    return None, cx, cy  # 모든 시도 실패


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

        width_mm, height_mm = furniture_sizes[intent.object_type]

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

        # 배치 시도 (코드 레벨 위치 조정 포함)
        obj_poly, final_cx, final_cy = try_place_object(
            cx, cy, width_mm, height_mm, rotation_deg,
            room_poly, dead_zones, placed_polys,
        )

        if obj_poly is None:
            # 완전 실패 → failed 목록에 기록
            failed_objects.append({
                "object_type": intent.object_type,
                "reason": f"'{intent.reference_point}' 기준점 주변 {MAX_STEPS * STEP_MM:.0f}mm 내에 배치 가능 공간 없음",
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
            bbox_mm=(width_mm, height_mm),
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
