"""
violations.py — violation severity 체크

blocking: Dead Zone 침범 / 비상로 미확보 / 복도 900mm 미달 → .glb 출력 차단
warning : 그 외 규정 위반 (경고 후 출력)
"""

from shapely.geometry import Polygon, box
from core.schemas import Violation, ViolationSeverity
from core.geometry_utils import DEFAULTS


def check_dead_zone_intrusion(
    obj_poly: Polygon,
    dead_zones: list[Polygon],
    object_type: str,
) -> list[Violation]:
    violations = []
    for dz in dead_zones:
        if obj_poly.intersects(dz):
            violations.append(Violation(
                severity=ViolationSeverity.BLOCKING,
                object_type=object_type,
                rule="dead_zone_intrusion",
                detail=f"{object_type}이 Dead Zone을 침범합니다.",
            ))
            break
    return violations


def check_corridor_width(
    obj_poly: Polygon,
    room_poly: Polygon,
    all_placed: list[Polygon],
    object_type: str,
    min_width_mm: float = DEFAULTS["main_corridor_min_mm"],
) -> list[Violation]:
    """
    배치 후 남은 통로 최소 폭 검사.
    NetworkX pathfinder의 결과로 대체되지만 여기서 단순 bounding box 체크도 수행.
    """
    violations = []
    # 실제 세부 검사는 pathfinder.py의 NetworkX 검증에서 수행
    # 여기서는 객체 크기가 방 폭의 80% 초과 시 경고
    room_bounds = room_poly.bounds  # minx, miny, maxx, maxy
    room_width = room_bounds[2] - room_bounds[0]
    obj_bounds = obj_poly.bounds
    obj_width = obj_bounds[2] - obj_bounds[0]
    if obj_width > room_width * 0.8:
        violations.append(Violation(
            severity=ViolationSeverity.WARNING,
            object_type=object_type,
            rule="corridor_width_risk",
            detail=f"{object_type} 폭({obj_width:.0f}mm)이 공간 폭의 80%를 초과합니다.",
        ))
    return violations


def check_emergency_path(
    obj_poly: Polygon,
    emergency_exits: list[tuple[float, float]],
    object_type: str,
    min_mm: float = DEFAULTS["emergency_path_min_mm"],
) -> list[Violation]:
    """비상구 주변 이격거리 체크"""
    violations = []
    from shapely.geometry import Point
    for exit_pos in emergency_exits:
        exit_point = Point(exit_pos)
        if obj_poly.distance(exit_point) < min_mm:
            violations.append(Violation(
                severity=ViolationSeverity.BLOCKING,
                object_type=object_type,
                rule="emergency_path_blocked",
                detail=(
                    f"{object_type}이 비상구({exit_pos})로부터 "
                    f"{obj_poly.distance(exit_point):.0f}mm 이내에 있습니다. "
                    f"최소 {min_mm:.0f}mm 이격 필요."
                ),
            ))
    return violations


def aggregate_violations(
    violations: list[Violation],
) -> tuple[list[Violation], bool]:
    """violations 목록에서 blocking 여부 판단"""
    glb_blocked = any(v.severity == ViolationSeverity.BLOCKING for v in violations)
    return violations, glb_blocked
