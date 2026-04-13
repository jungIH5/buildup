"""
intent_parser.py — 배치 요구사항 자연어 → 구조화된 인텐트 변환

Agent 2와 Agent 3 사이에서 실행.
- 자연어에서 오브젝트 타입, 수량, 위치 의도를 추출 (Haiku LLM)
- 입구 기준 상대 방향("오른쪽", "맞은편")을 실제 벽 방향으로 변환
- 최종 결과: ResolvedIntent 목록 → Agent 3에 직접 전달
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# 위치 케이스 타입
# ─────────────────────────────────────────────────────
PositionCase = Literal[
    "entrance_relative",  # 입구 기준 좌/우/맞은편/앞
    "absolute_wall",      # 동/서/남/북 절대 방향
    "zone_based",         # 깊은 곳/입구 가까이 등 zone 명시
    "adjacent",           # A 옆에 B 배치
    "unspecified",        # 위치 미명시 → Agent 3 자유 배치
]


@dataclass
class ResolvedIntent:
    """
    Intent Parser가 출력하는 구조화된 배치 의도.
    target_ref_point가 있으면 Agent 3은 반드시 그 기준점을 사용해야 한다.
    """
    object_type: str
    quantity: int                            # -1 = fill (벽 용량 자동 계산)
    position_case: PositionCase
    target_ref_point: Optional[str] = None   # 해결된 reference_point name
    zone_hint: Optional[str] = None          # entrance_zone / mid_zone / deep_zone
    adjacent_to: Optional[str] = None        # 인접 대상 object_type
    original_text: str = ""                  # 파싱에 사용한 원문 조각


# ─────────────────────────────────────────────────────
# 입구 방향 테이블
# ─────────────────────────────────────────────────────

# 입구가 있는 벽 → 그 입구에 서서 안쪽을 바라볼 때 좌/우/맞은편
# 픽셀 좌표계: N=위(miny), S=아래(maxy), W=왼쪽(minx), E=오른쪽(maxx)
_RELATIVE_TO_WALL: dict[str, dict[str, str]] = {
    "south": {"right": "east",  "left": "west",  "facing": "north", "behind": "south"},
    "north": {"right": "west",  "left": "east",  "facing": "south", "behind": "north"},
    "west":  {"right": "south", "left": "north", "facing": "east",  "behind": "west"},
    "east":  {"right": "north", "left": "south", "facing": "west",  "behind": "east"},
}

# 절대 방향 → reference_point name 탐색 키워드
_WALL_KEYWORDS: dict[str, list[str]] = {
    "north": ["north_wall", "north"],
    "south": ["south_wall", "south"],
    "east":  ["east_wall",  "east"],
    "west":  ["west_wall",  "west"],
}

# zone 힌트 키워드 → zone_label
_ZONE_HINTS: dict[str, str] = {
    "entrance_zone": "entrance_zone",
    "mid_zone":      "mid_zone",
    "deep_zone":     "deep_zone",
}


# ─────────────────────────────────────────────────────
# 내부 유틸리티
# ─────────────────────────────────────────────────────

def _get_room_bounds(room_polygon_mm: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in room_polygon_mm]
    ys = [p[1] for p in room_polygon_mm]
    return min(xs), min(ys), max(xs), max(ys)


def _determine_entrance_side(
    entrance_pos: tuple[float, float],
    room_bounds: tuple[float, float, float, float],
) -> str:
    """
    입구 mm 좌표가 방의 어느 벽에 가장 가까운지 판단.
    반환: "north" | "south" | "east" | "west"
    """
    minx, miny, maxx, maxy = room_bounds
    ex, ey = entrance_pos
    dists = {
        "south": abs(ey - maxy),
        "north": abs(ey - miny),
        "west":  abs(ex - minx),
        "east":  abs(ex - maxx),
    }
    return min(dists, key=lambda k: dists[k])


def _find_ref_by_wall(wall_dir: str, reference_points: list) -> Optional[str]:
    """
    벽 방향 → 해당 벽 reference_point name.
    이름에 키워드가 포함된 첫 번째 기준점 반환.
    """
    keywords = _WALL_KEYWORDS.get(wall_dir, [])
    for kw in keywords:
        for rp in reference_points:
            if kw in rp.name.lower():
                return rp.name
    return None


def _find_ref_by_zone(zone_label: str, reference_points: list) -> Optional[str]:
    """zone_label 매칭 reference_point 이름 반환 (mid 우선)."""
    matched = [rp for rp in reference_points if rp.zone_label == zone_label]
    if not matched:
        return None
    # mid가 포함된 이름 우선
    for rp in matched:
        if "mid" in rp.name.lower():
            return rp.name
    return matched[0].name


# ─────────────────────────────────────────────────────
# LLM 파싱 프롬프트
# ─────────────────────────────────────────────────────

_PARSE_SYSTEM = """당신은 공간 배치 요구사항 파서입니다.
사용자 텍스트에서 각 오브젝트의 배치 의도를 추출해 JSON으로 반환하세요.

오브젝트 타입 코드:
- "character_bbox": 캐릭터 조형물, 등신대, 캐릭터 인형, 캐릭터 피규어
- "photo_zone": 포토존, 포토 존, 사진존
- "banner_stand": 배너 스탠드, 배너, 현수막 스탠드
- "product_display": 상품 진열대, 진열대, 상품진열대, 디스플레이
- "shelf_rental": 렌탈 선반, 선반

수량 규칙:
- 명시 숫자: 그대로 (예: "3개" → 3)
- "최대한", "가득", "채워", "전부", "모두", "빼곡히" 등 → -1

position_case 분류:
- "entrance_relative": 입구 기준 방향 언급 (입구 오른쪽/왼쪽/맞은편/앞/뒤/건너편)
- "absolute_wall": 절대 벽 방향 (동/서/남/북/왼쪽 벽/오른쪽 벽/위쪽 벽/아래쪽 벽)
- "zone_based": zone 명시 (깊은 곳=deep_zone, 입구 가까이=entrance_zone, 중간=mid_zone)
- "adjacent": 다른 오브젝트 옆에 배치 ("~ 옆에", "~ 근처에")
- "unspecified": 위치 언급 없음

relative_direction (entrance_relative일 때):
- "right": 오른쪽
- "left": 왼쪽
- "facing": 맞은편, 건너편, 정면
- "behind": 입구 쪽, 뒤쪽 (입구 바로 옆)

absolute_direction (absolute_wall일 때):
- "north", "south", "east", "west"
- 왼쪽 벽→"west", 오른쪽 벽→"east", 위쪽 벽→"north", 아래쪽 벽→"south"

zone_label (zone_based일 때):
- "entrance_zone", "mid_zone", "deep_zone"

반드시 JSON만 반환. 설명 텍스트 없이 JSON 오브젝트만."""

_PARSE_USER_TEMPLATE = """다음 배치 요구사항을 파싱하세요:

{requirements}

반환 형식:
{{
  "intents": [
    {{
      "object_type": "<코드명>",
      "quantity": <숫자 또는 -1>,
      "position_case": "<케이스>",
      "relative_direction": "<right|left|facing|behind 또는 null>",
      "absolute_direction": "<north|south|east|west 또는 null>",
      "zone_label": "<zone 또는 null>",
      "adjacent_to": "<object_type 또는 null>",
      "original_text": "<원문 조각>"
    }}
  ]
}}"""


async def _parse_with_llm(
    user_requirements: str,
    client,
) -> list[dict]:
    """Haiku로 자연어 파싱 → raw intent dict 목록."""
    prompt = _PARSE_USER_TEMPLATE.format(requirements=user_requirements.strip())
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_PARSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            text = m.group(1) if m else text
        data = json.loads(text)
        return data.get("intents", [])
    except Exception as e:
        logger.warning(f"[IntentParser] LLM 파싱 실패: {e}")
        return []


# ─────────────────────────────────────────────────────
# 메인 함수
# ─────────────────────────────────────────────────────

async def parse_intents(
    user_requirements: str,
    floor,           # FloorAnalysis
    client,
) -> list[ResolvedIntent]:
    """
    자연어 요구사항 → ResolvedIntent 목록.

    1. Haiku로 구조화된 intent 추출
    2. 입구 위치 기반으로 상대 방향 → 실제 벽 변환
    3. 벽 방향 → reference_point name 매핑
    """
    if not user_requirements or not user_requirements.strip():
        return []

    raw_intents = await _parse_with_llm(user_requirements, client)
    if not raw_intents:
        return []

    # 입구 위치 분석
    entrance_rp = next(
        (rp for rp in floor.reference_points if rp.name == "entrance"), None
    )
    room_bounds = _get_room_bounds(floor.room_polygon_mm)
    entrance_side: Optional[str] = None
    if entrance_rp:
        entrance_side = _determine_entrance_side(entrance_rp.position_mm, room_bounds)
        logger.info(f"[IntentParser] 입구 벽 방향: {entrance_side}")

    resolved: list[ResolvedIntent] = []

    for raw in raw_intents:
        obj_type = raw.get("object_type", "")
        quantity = int(raw.get("quantity", 1))
        position_case: PositionCase = raw.get("position_case", "unspecified")
        original_text = raw.get("original_text", "")

        target_ref: Optional[str] = None
        zone_hint: Optional[str] = None
        adjacent_to: Optional[str] = None

        if position_case == "entrance_relative":
            relative_dir = raw.get("relative_direction")
            if relative_dir and entrance_side:
                wall_dir = _RELATIVE_TO_WALL[entrance_side].get(relative_dir)
                if wall_dir:
                    target_ref = _find_ref_by_wall(wall_dir, floor.reference_points)
                    logger.info(
                        f"[IntentParser] '{original_text}' → "
                        f"입구:{entrance_side}, 방향:{relative_dir} → 벽:{wall_dir} → ref:{target_ref}"
                    )

        elif position_case == "absolute_wall":
            abs_dir = raw.get("absolute_direction")
            if abs_dir:
                target_ref = _find_ref_by_wall(abs_dir, floor.reference_points)
                logger.info(
                    f"[IntentParser] '{original_text}' → 절대:{abs_dir} → ref:{target_ref}"
                )

        elif position_case == "zone_based":
            zone_label = raw.get("zone_label")
            if zone_label:
                zone_hint = _ZONE_HINTS.get(zone_label, zone_label)
                target_ref = _find_ref_by_zone(zone_hint, floor.reference_points)
                logger.info(
                    f"[IntentParser] '{original_text}' → zone:{zone_hint} → ref:{target_ref}"
                )

        elif position_case == "adjacent":
            adjacent_to = raw.get("adjacent_to")

        resolved.append(ResolvedIntent(
            object_type=obj_type,
            quantity=quantity,
            position_case=position_case,
            target_ref_point=target_ref,
            zone_hint=zone_hint,
            adjacent_to=adjacent_to,
            original_text=original_text,
        ))

    logger.info(f"[IntentParser] 파싱 완료: {len(resolved)}개 인텐트")
    return resolved
