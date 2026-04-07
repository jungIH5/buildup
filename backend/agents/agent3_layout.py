"""
agent3_layout.py — Agent 3: 배치 결정

- 좌표/mm 값 출력 금지 (Pydantic Circuit Breaker)
- 재호출 최대 2회 (placed_objects 피드백 포함)
- 배치 계산은 Shapely(spatial.py)에서만 수행
"""

from __future__ import annotations
import json
import anthropic
from pydantic import ValidationError
from core.schemas import (
    FloorAnalysis, BrandStandards, LayoutPlan,
    LayoutResult,
)
from core.spatial import compute_layout, make_object_polygon
from shapely.geometry import Polygon

MAX_RETRIES = 2

SYSTEM_PROMPT = """당신은 공간 배치 전문가입니다.
브랜드 메뉴얼 기준과 공간 제약을 고려하여 오브젝트 배치 의도를 결정합니다.

절대 규칙:
1. 좌표(x, y), mm 숫자 값, 픽셀 값을 출력하지 마세요.
2. reference_point 이름(예: entrance, north_wall_mid)으로만 위치를 지시하세요.
3. 반드시 JSON 형식으로만 응답하세요.

위치 결정 원칙:
4. 사용자가 위치를 지정한 오브젝트 → 그 위치와 가장 일치하는 reference_point를 선택하세요.
   분산 규칙 면제 — 여러 개가 같은 reference_point에 몰려도 됩니다.
5. 사용자가 위치를 지정하지 않은 오브젝트 → entrance_zone·mid_zone·deep_zone을 균형 있게 사용하세요.
   동일 reference_point에 같은 타입 최대 2개.

product_display(상품진열대) 클러스터 규칙:
6. product_display 1-6개: 동일한 reference_point 1개에 묶어서 배치하세요.
7. product_display 7개 이상: 서로 다른 zone의 reference_point 2개에 나누어 배치하세요.
   예) 7개 → mid_zone 기준점에 4개 + deep_zone 기준점에 3개
8. 같은 그룹 내 모든 product_display는 반드시 동일한 reference_point를 사용하세요.
   (배치 엔진이 그룹 단위로 클러스터 계산하므로, 기준점이 다르면 별도 그룹으로 처리됩니다)
"""

PLACEMENT_PROMPT_TEMPLATE = """다음 정보를 바탕으로 오브젝트 배치 의도를 결정하세요.

{user_requirements_section}
공간 기준점 (zone_label로 위치 특성 파악):
{reference_points}

공간 제약:
{constraints}

배치 가능 오브젝트 (수량 포함):
{eligible_objects}

브랜드 기준:
{brand_standards}

{relationships_section}

배치 원칙:
- 사용자 요구사항에서 위치가 명시된 오브젝트: 해당 위치의 reference_point 우선 선택 (분산 규칙 적용 안 함)
- 위치가 명시되지 않은 오브젝트: entrance_zone·mid_zone·deep_zone 균형 있게 분산, 동일 reference_point에 같은 타입 최대 2개
- 동선이 확보되도록 오브젝트 간 여백 고려

{feedback_section}

출력 JSON 스키마 (좌표/mm 절대 금지):
```json
{{
  "placements": [
    {{
      "object_type": "<코드명>",
      "reference_point": "<기준점 이름>",
      "direction": "<방향 (예: entrance_facing, wall_facing, inward)>",
      "priority": <1~10 숫자, 낮을수록 먼저 배치>,
      "placed_because": "<배치 근거 한 문장>"
    }}
  ]
}}
```
"""

USER_REQUIREMENTS_TEMPLATE = """★ 사용자 요구사항 (최우선 반영):
{requirements}

위 요구사항을 반드시 우선적으로 반영하세요.
- 수량이 명시된 경우: 해당 수량만큼 같은 object_type을 여러 번 출력하세요.
- 위치가 명시된 경우: 가장 적합한 reference_point와 direction을 선택하세요.
- 요구사항에 없는 오브젝트는 브랜드 기준과 공간 제약에 따라 자유롭게 배치하세요.

"""

FEEDBACK_TEMPLATE = """
이전 배치 시도에서 실패한 오브젝트가 있습니다:
{failed_objects}

이미 배치 성공한 오브젝트:
{placed_objects}

대안 기준점 (실패한 오브젝트에 시도 가능):
{alternative_refs}

실패한 오브젝트에 대해 대안 기준점이나 다른 direction을 시도하세요.
이미 성공한 오브젝트는 다시 포함하지 마세요.
"""

RELATIONSHIPS_TEMPLATE = """
브랜드 캐릭터/오브젝트 간 관계 제약 (반드시 준수):
{relationships}
"""

# 오브젝트 타입 → 한국어 별칭 매핑 (AI 파서 프롬프트에 사용)
_OBJECT_ALIASES = {
    "character_bbox":  "캐릭터 조형물, 캐릭터 등신대, 등신대, 캐릭터 인형, 캐릭터 피규어, 캐릭터 모형, 캐릭터",
    "photo_zone":      "포토존, 포토 존, 사진존",
    "banner_stand":    "배너 스탠드, 배너스탠드, 배너, 현수막 스탠드",
    "product_display": "상품 진열대, 진열대, 상품진열대, 디스플레이",
    "shelf_rental":    "렌탈 선반, 렌탈선반, 선반",
}

_QTY_PARSE_SYSTEM = (
    "당신은 공간 배치 요구사항 파서입니다. "
    "사용자 텍스트에서 각 오브젝트의 배치 수량을 추출하세요. "
    "수량이 '최대한', '가득', '전부', '모두', '가능한 만큼', '채워달라', '빼곡히' 등으로 표현된 경우 "
    "해당 오브젝트의 수량을 -1로 반환하세요 (벽 용량 자동 계산 신호). "
    "반드시 JSON만 반환하세요. 설명 텍스트 없이 JSON 오브젝트만."
)

# 벽 용량 자동 계산 신호 값
_FILL_SIGNAL = -1


def _compute_wall_capacity(
    room_polygon_mm: list,
    obj_w: float,
    gap_mm: float = 50.0,
    clearspace_mm: float = 600.0,
) -> int:
    """
    가장 긴 벽 한 면에 단열로 배치 가능한 최대 개수.
    - 브랜드 clearspace를 오브젝트 간격이 아닌 끝단 여유로 적용
    - 실제 배치 시 dead_zone·통로 체크에서 추가 제한될 수 있음
    """
    xs = [p[0] for p in room_polygon_mm]
    ys = [p[1] for p in room_polygon_mm]
    longest = max(max(xs) - min(xs), max(ys) - min(ys))
    # 양쪽 끝 여유 = clearspace_mm, 오브젝트 사이 간격 = gap_mm
    effective = longest - clearspace_mm * 2
    return max(1, int(effective / (obj_w + gap_mm)))


async def _parse_qty_with_ai(
    user_requirements: str,
    client: anthropic.AsyncAnthropic,
) -> dict[str, int]:
    """
    Claude Haiku로 자연어 요구사항에서 오브젝트별 수량을 추출.
    반환: {"character_bbox": 3, "product_display": 4, ...}
    "최대한/가득" 등 fill 표현은 {"product_display": -1} 형태로 반환 (벽 용량 자동 계산).
    인식 못 한 타입은 포함하지 않음.
    """
    aliases_desc = "\n".join(
        f'- "{k}": {v}로 표현될 수 있음' for k, v in _OBJECT_ALIASES.items()
    )
    prompt = (
        f"다음 요구사항 텍스트에서 각 오브젝트 타입별 배치 수량을 추출하세요.\n\n"
        f"오브젝트 타입과 한국어 표현:\n{aliases_desc}\n\n"
        f"수량 추출 규칙:\n"
        f"- 명시적 숫자: 해당 숫자 그대로\n"
        f"- '최대한', '가득', '전부', '채워달라', '모두', '빼곡히' 등: -1\n\n"
        f"요구사항:\n{user_requirements}\n\n"
        f"결과를 JSON으로만 반환하세요. 예시: {{\"character_bbox\": 3, \"product_display\": -1}}\n"
        f"언급되지 않은 타입은 포함하지 마세요."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_QTY_PARSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            import re as _re
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
            text = m.group(1) if m else text
        result = json.loads(text)
        # 알려진 타입만 필터링. -1(fill signal)과 양수 모두 허용
        return {
            k: int(v) for k, v in result.items()
            if k in _OBJECT_ALIASES and int(v) != 0
        }
    except Exception:
        return {}


async def run_agent3(
    floor: FloorAnalysis,
    standards: BrandStandards,
    constraints: dict,
    furniture_sizes: dict[str, tuple[float, float]],
    client: anthropic.AsyncAnthropic,
    emergency_exits: list[tuple[float, float]] | None = None,
    relationships: list[dict] | None = None,
    user_requirements: str | None = None,
    existing_placed: list | None = None,
) -> LayoutResult:
    """
    Agent 3 메인 진입점.
    - LLM 배치 의도 결정 → Shapely 계산
    - 실패 오브젝트는 Agent 3 재호출 (최대 2회)
    - existing_placed: 이미 배치된 오브젝트 목록 (PlacedObject 또는 dict).
      전달 시 해당 오브젝트의 위치는 보존하고, 신규 추가분만 배치한다.
    - 최종 결과: 성공 + 실패 분리된 LayoutResult
    """
    from core.spatial import make_object_polygon as _make_poly

    # ── 기존 배치 초기화 ──────────────────────────────────────────
    # existing_placed가 있으면 해당 오브젝트들은 이미 배치 완료로 간주.
    # all_placed에 포함시켜 최종 결과에 유지하고,
    # all_placed_polys에 추가해 신규 배치 시 충돌 방지.
    all_placed: list = []
    all_placed_polys: list[Polygon] = []

    existing_counts: dict[str, int] = {}
    if existing_placed:
        for obj in existing_placed:
            if hasattr(obj, "object_type"):
                ot = obj.object_type
                pos = obj.position_mm
                bbox = obj.bbox_mm
                rot = obj.rotation_deg
            else:
                ot = obj["object_type"]
                pos = obj["position_mm"]
                bbox = obj["bbox_mm"]
                rot = obj.get("rotation_deg", 0.0)
            existing_counts[ot] = existing_counts.get(ot, 0) + 1
            all_placed.append(obj)
            all_placed_polys.append(_make_poly(pos[0], pos[1], bbox[0], bbox[1], rot))

    all_failed: list = []
    all_violations: list = []

    # ── 사용자 요구사항 파싱 ──
    # Claude Haiku로 자연어에서 수량을 추출. 정규식 패턴 의존 없이 맥락 기반 인식.
    user_requirements_section = ""

    # 기본 수량: eligible_objects의 각 타입은 기본 1개
    base_counts: dict[str, int] = {}
    for obj in floor.eligible_objects:
        base_counts[obj] = base_counts.get(obj, 0) + 1

    qty_overrides: dict[str, int] = {}
    if user_requirements and user_requirements.strip():
        user_requirements_section = USER_REQUIREMENTS_TEMPLATE.format(
            requirements=user_requirements.strip()
        )
        # AI 파서로 자연어 수량 추출 (등신대, 피규어 등 다양한 표현 처리)
        qty_overrides = await _parse_qty_with_ai(user_requirements.strip(), client)

        # fill 신호(-1) 처리: 벽 용량 자동 계산
        for obj_type, qty in list(qty_overrides.items()):
            if qty == _FILL_SIGNAL and obj_type in furniture_sizes:
                obj_w = furniture_sizes[obj_type][0]
                gap_mm = 50.0
                max_qty = _compute_wall_capacity(
                    floor.room_polygon_mm,
                    obj_w,
                    gap_mm=gap_mm,
                    clearspace_mm=max(standards.clearspace_mm, 600.0),
                )
                qty_overrides[obj_type] = max_qty

    # 최종 수량 결정: 요구사항 수량이 기본보다 크면 그 값으로, 아니면 기본값 유지
    final_counts = dict(base_counts)
    for obj_type, qty in qty_overrides.items():
        if qty > 0:
            final_counts[obj_type] = max(final_counts.get(obj_type, 0), qty)

    # 기존 배치 수량 차감 — 이미 배치된 만큼은 신규 배치 불필요
    new_counts: dict[str, int] = {}
    for obj_type, total in final_counts.items():
        remaining = total - existing_counts.get(obj_type, 0)
        if remaining > 0:
            new_counts[obj_type] = remaining

    expanded_objects: list[str] = []
    for obj_type, count in new_counts.items():
        expanded_objects.extend([obj_type] * count)

    remaining_objects = expanded_objects

    # zone_label 포함 기준점 요약 (Agent 3에게 공간 깊이 정보 전달)
    ref_summary = [
        {
            "name": rp.name,
            "zone_label": rp.zone_label or "unknown",
            "walk_distance_mm": rp.walk_distance_mm,
            "facing": rp.facing,
        }
        for rp in floor.reference_points
    ]

    # relationships 섹션
    relationships_section = ""
    if relationships:
        relationships_section = RELATIONSHIPS_TEMPLATE.format(
            relationships=json.dumps(relationships, ensure_ascii=False, indent=2)
        )

    for attempt in range(MAX_RETRIES + 1):
        # 피드백 섹션
        feedback_section = ""
        if attempt > 0 and all_failed:
            # 대안 기준점: 실패한 오브젝트가 시도한 기준점 외 나머지
            used_refs = {f["reference_point"] for f in all_failed}
            alt_refs = [
                {"name": rp.name, "zone_label": rp.zone_label or "unknown"}
                for rp in floor.reference_points
                if rp.name not in used_refs
            ]
            feedback_section = FEEDBACK_TEMPLATE.format(
                failed_objects=json.dumps(all_failed, ensure_ascii=False, indent=2),
                placed_objects=json.dumps(
                    [{"object_type": p.object_type, "reference_point": p.reference_point}
                     for p in all_placed],
                    ensure_ascii=False, indent=2,
                ),
                alternative_refs=json.dumps(alt_refs, ensure_ascii=False, indent=2),
            )

        prompt = PLACEMENT_PROMPT_TEMPLATE.format(
            user_requirements_section=user_requirements_section,
            reference_points=json.dumps(ref_summary, ensure_ascii=False, indent=2),
            constraints=json.dumps(constraints, ensure_ascii=False, indent=2),
            eligible_objects=json.dumps(remaining_objects, ensure_ascii=False),
            brand_standards=json.dumps({
                "clearspace_mm": standards.clearspace_mm,
                "character_orientation": standards.character_orientation,
                "prohibited_material": standards.prohibited_material,
                "main_corridor_min_mm": standards.main_corridor_min_mm,
                "wall_clearance_mm": standards.wall_clearance_mm,
            }, ensure_ascii=False, indent=2),
            relationships_section=relationships_section,
            feedback_section=feedback_section,
        )

        # LLM 호출 + Circuit Breaker 검증
        layout_plan = await _call_with_circuit_breaker(prompt, client, n_objects=len(remaining_objects))
        if layout_plan is None:
            for obj in remaining_objects:
                all_failed.append({
                    "object_type": obj,
                    "reason": f"Agent 3 Circuit Breaker: {MAX_RETRIES + 1}회 JSON 검증 실패",
                    "reference_point": "unknown",
                })
            break

        # Shapely 배치 계산 — 이전 라운드 배치 폴리곤을 initial로 전달
        result = compute_layout(
            floor=floor,
            standards=standards,
            placements=layout_plan.placements,
            furniture_sizes=furniture_sizes,
            emergency_exits=emergency_exits,
            initial_placed_polys=all_placed_polys,
        )

        all_placed.extend(result.placed)
        all_violations.extend(result.violations)

        # 이번 라운드 성공 오브젝트의 폴리곤 누적
        for p in result.placed:
            all_placed_polys.append(
                make_object_polygon(p.position_mm[0], p.position_mm[1],
                                    p.bbox_mm[0], p.bbox_mm[1], p.rotation_deg)
            )

        # 실패 분류
        newly_failed = result.failed
        if not newly_failed or attempt == MAX_RETRIES:
            all_failed.extend(newly_failed)
            break

        remaining_objects = [f["object_type"] for f in newly_failed]

    glb_blocked = any(
        v.severity.value == "blocking" for v in all_violations
    )

    return LayoutResult(
        placed=all_placed,
        failed=all_failed,
        violations=all_violations,
        glb_blocked=glb_blocked,
        disclaimer_items=floor.disclaimer_items,
    )


async def _call_with_circuit_breaker(
    prompt: str,
    client: anthropic.AsyncAnthropic,
    max_retries: int = 2,
    n_objects: int = 10,
) -> LayoutPlan | None:
    """
    LLM 호출 + Pydantic Circuit Breaker.
    최대 2회 재시도 후 None 반환.
    """
    # 오브젝트 1개당 약 180 토큰 + 기본 여유 400
    output_tokens = max(1500, n_objects * 180 + 400)
    for i in range(max_retries):
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=output_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        import logging
        u = response.usage
        logging.info(f"[Agent3] attempt={i} input={u.input_tokens} output={u.output_tokens}")

        raw = response.content[0].text.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(raw)
            plan = LayoutPlan(**data)
            return plan
        except (json.JSONDecodeError, ValidationError) as e:
            error_msg = str(e)
            # 다음 시도에 에러 피드백 포함 (Circuit Breaker 재시도)
            prompt = f"{prompt}\n\n[이전 응답 오류: {error_msg}]\n다시 시도하세요."

    return None
