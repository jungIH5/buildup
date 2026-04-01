"""
geometry_utils.py — 단위 변환 상수 및 유틸리티
MM_TO_UNIT = 1  →  1 unit = 1mm (프로젝트 전체 기준)
"""

MM_TO_UNIT: float = 1.0   # 절대 변경 금지. 바꾸려면 이 상수 하나만 수정.

# 하드코딩 방어값 (브랜드 메뉴얼에서 못 읽으면 이 값으로 fallback)
DEFAULTS = {
    "main_corridor_min_mm": 900,
    "emergency_path_min_mm": 1200,
    "wall_clearance_mm": 300,
    "clearspace_mm": 500,
}


def mm_to_unit(mm: float) -> float:
    """mm 값을 내부 unit 값으로 변환"""
    return mm * MM_TO_UNIT


def px_to_mm(px: float, scale_mm_per_px: float) -> float:
    """픽셀 → mm 변환 (scale_mm_per_px = 도면 1px당 실제 mm)"""
    return px * scale_mm_per_px


def px_to_unit(px: float, scale_mm_per_px: float) -> float:
    """픽셀 → 내부 unit 변환"""
    return mm_to_unit(px_to_mm(px, scale_mm_per_px))
