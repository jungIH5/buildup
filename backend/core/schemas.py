"""
schemas.py — Pydantic v2 스키마 (Circuit Breaker 역할)

- Agent 출력마다 Pydantic 검증 수행
- 범위 벗어나거나 좌표가 포함된 경우 즉시 차단
- 실패 시 LLM 재호출 (최대 3회)
"""

from __future__ import annotations
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────
# Agent 1 출력 스키마
# ─────────────────────────────────────────

class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    USER_INPUT = "user_input"


class BrandStandards(BaseModel):
    """Agent 1이 브랜드 메뉴얼에서 추출하는 기준값"""

    clearspace_mm: float = Field(default=500, ge=300, le=5000)
    logo_clearspace_mm: Optional[float] = Field(default=None, ge=100, le=3000)
    character_orientation: Optional[str] = None          # 예: "entrance_facing"
    prohibited_material: list[str] = Field(default_factory=list)
    relationships: dict = Field(default_factory=dict)

    # 방어값 (null이면 DEFAULTS에서 merge됨)
    main_corridor_min_mm: float = Field(default=900, ge=600, le=3000)
    emergency_path_min_mm: float = Field(default=1200, ge=900, le=3000)
    wall_clearance_mm: float = Field(default=300, ge=100, le=1000)

    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    source: Literal["메뉴얼 추출", "기본값", "사용자 입력"] = "기본값"


# ─────────────────────────────────────────
# Agent 2 출력 스키마
# ─────────────────────────────────────────

class Equipment(BaseModel):
    """감지된 설비 및 가구 (도면에 이미 존재하는 요소)"""
    equipment_type: str
    position_px: tuple[float, float]   # 도면 이미지 픽셀 중심 좌표
    position_mm: Optional[tuple[float, float]] = None  # mm 단위 실제 중심 좌표
    bbox_px: Optional[tuple[float, float, float, float]] = None  # [x1, y1, x2, y2]
    size_mm: Optional[tuple[float, float]] = None  # [width, depth] (실제 크기)
    confidence: ConfidenceLevel
    source: Literal["auto_detected", "user_marked", "unknown"]


class ReferencePoint(BaseModel):
    """기준점 — Agent 3이 이것만 참조하여 배치 의도 결정"""
    name: str                          # 예: "entrance", "north_wall_mid"
    position_mm: tuple[float, float]   # mm 단위 실제 좌표
    facing: Optional[str] = None       # 예: "south", "inward"


class FloorAnalysis(BaseModel):
    """Agent 2 전체 출력"""
    room_polygon_mm: list[tuple[float, float]]  # 방 외곽선 (mm)
    dead_zones_mm: list[list[tuple[float, float]]]  # Dead Zone 폴리곤 목록
    reference_points: list[ReferencePoint]
    eligible_objects: list[str]         # 배치 가능 오브젝트 코드명 목록
    scale_mm_per_px: float
    scale_confidence: ConfidenceLevel
    equipment_detected: list[Equipment]
    disclaimer_items: list[str] = Field(default_factory=list)  # "모르겠음" 항목


# ─────────────────────────────────────────
# Agent 3 출력 스키마 — 좌표 금지!
# ─────────────────────────────────────────

class PlacementIntent(BaseModel):
    """
    Agent 3이 결정하는 배치 '의도'.
    절대 좌표나 mm 값을 포함해서는 안 된다.
    Shapely 계산은 이 의도를 받아 코드에서만 수행한다.
    """
    object_type: str                   # Supabase furniture_standards 코드명
    reference_point: str               # ReferencePoint.name 참조
    direction: str                     # 예: "wall_facing", "inward", "center"
    priority: int = Field(ge=1, le=10) # 낮을수록 먼저 배치 시도
    placed_because: str                # 배치 근거 (리포트용)

    @field_validator("reference_point", "direction", mode="before")
    @classmethod
    def no_numbers(cls, v: str) -> str:
        """숫자(좌표/mm 값)가 들어오면 Circuit Breaker 차단"""
        import re
        if re.search(r"\b\d+(\.\d+)?(mm|px|m)?\b", v):
            raise ValueError(
                f"Agent 3 출력에 숫자/단위가 포함됨 (좌표 금지): '{v}'"
            )
        return v


class LayoutPlan(BaseModel):
    """Agent 3 전체 출력"""
    placements: list[PlacementIntent]

    @model_validator(mode="after")
    def check_no_coordinates(self) -> "LayoutPlan":
        import re, json
        raw = json.dumps([p.model_dump() for p in self.placements])
        if re.search(r'"x"\s*:', raw) or re.search(r'"y"\s*:', raw):
            raise ValueError("Agent 3 출력에 x/y 좌표 키가 감지됨 — Circuit Breaker 차단")
        return self


# ─────────────────────────────────────────
# 배치 결과 스키마 (Shapely 계산 후)
# ─────────────────────────────────────────

class ViolationSeverity(str, Enum):
    BLOCKING = "blocking"   # .glb 출력 차단
    WARNING = "warning"     # 경고와 함께 출력
    INFO = "info"


class Violation(BaseModel):
    severity: ViolationSeverity
    object_type: Optional[str]
    rule: str
    detail: str


class PlacedObject(BaseModel):
    object_type: str
    position_mm: tuple[float, float]
    rotation_deg: float = 0.0
    bbox_mm: tuple[float, float]       # width, height
    reference_point: str
    placed_because: str


class LayoutResult(BaseModel):
    """최종 배치 결과 — 성공 + 실패 분리"""
    placed: list[PlacedObject]         # 성공한 오브젝트
    failed: list[dict]                 # 실패한 오브젝트 + 실패 이유
    violations: list[Violation]
    glb_blocked: bool                  # blocking violation 존재 여부
    disclaimer_items: list[str] = Field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return any(v.severity == ViolationSeverity.BLOCKING for v in self.violations)
