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
"""

PLACEMENT_PROMPT_TEMPLATE = """다음 정보를 바탕으로 오브젝트 배치 의도를 결정하세요.

공간 제약:
{constraints}

배치 가능 오브젝트:
{eligible_objects}

브랜드 기준:
{brand_standards}

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

FEEDBACK_TEMPLATE = """
이전 배치 시도에서 실패한 오브젝트가 있습니다:
{failed_objects}

이미 배치 성공한 오브젝트:
{placed_objects}

실패한 오브젝트에 대해 다른 reference_point나 direction을 시도하세요.
이미 성공한 오브젝트는 다시 포함하지 마세요.
"""


async def run_agent3(
    floor: FloorAnalysis,
    standards: BrandStandards,
    constraints: dict,
    furniture_sizes: dict[str, tuple[float, float]],
    client: anthropic.AsyncAnthropic,
    emergency_exits: list[tuple[float, float]] | None = None,
) -> LayoutResult:
    """
    Agent 3 메인 진입점.
    - LLM 배치 의도 결정 → Shapely 계산
    - 실패 오브젝트는 Agent 3 재호출 (최대 2회)
    - 최종 결과: 성공 + 실패 분리된 LayoutResult
    """
    all_placed: list = []
    all_placed_polys: list[Polygon] = []  # 라운드 간 충돌 체크용
    all_failed: list = []
    all_violations: list = []
    remaining_objects = list(floor.eligible_objects)

    for attempt in range(MAX_RETRIES + 1):
        # 프롬프트 구성
        feedback_section = ""
        if attempt > 0 and all_failed:
            feedback_section = FEEDBACK_TEMPLATE.format(
                failed_objects=json.dumps(all_failed, ensure_ascii=False, indent=2),
                placed_objects=json.dumps(
                    [{"object_type": p.object_type, "reference_point": p.reference_point}
                     for p in all_placed],
                    ensure_ascii=False, indent=2,
                ),
            )

        prompt = PLACEMENT_PROMPT_TEMPLATE.format(
            constraints=json.dumps(constraints, ensure_ascii=False, indent=2),
            eligible_objects=json.dumps(remaining_objects, ensure_ascii=False),
            brand_standards=json.dumps({
                "clearspace_mm": standards.clearspace_mm,
                "character_orientation": standards.character_orientation,
                "prohibited_material": standards.prohibited_material,
                "main_corridor_min_mm": standards.main_corridor_min_mm,
                "wall_clearance_mm": standards.wall_clearance_mm,
            }, ensure_ascii=False, indent=2),
            feedback_section=feedback_section,
        )

        # LLM 호출 + Circuit Breaker 검증
        layout_plan = await _call_with_circuit_breaker(prompt, client)
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
    max_retries: int = 3,
) -> LayoutPlan | None:
    """
    LLM 호출 + Pydantic Circuit Breaker.
    최대 3회 재시도 후 None 반환.
    """
    for i in range(max_retries):
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
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
