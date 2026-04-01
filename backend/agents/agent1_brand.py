"""
agent1_brand.py — Agent 1: 브랜드 메뉴얼에서 기준값 추출

입력: 브랜드 메뉴얼 PDF (bytes)
출력: BrandStandards dict (null이면 기본값 merge)
"""

from __future__ import annotations
import json
import anthropic
from core.schemas import BrandStandards, ConfidenceLevel
from core.geometry_utils import DEFAULTS

SYSTEM_PROMPT = """당신은 브랜드 메뉴얼 분석 전문가입니다.
제공된 PDF에서 공간 배치에 필요한 기준값만 추출하세요.
반드시 JSON 형식으로만 응답하고, 추측은 하지 마세요.
모르는 값은 반드시 null로 표기하세요."""

EXTRACTION_PROMPT = """다음 브랜드 메뉴얼 PDF를 분석하여 아래 JSON 스키마에 맞게 기준값을 추출하세요.

**출력 JSON 스키마:**
```json
{
  "clearspace_mm": <숫자 또는 null>,
  "logo_clearspace_mm": <숫자 또는 null>,
  "character_orientation": <문자열 또는 null>,
  "prohibited_material": [<문자열 목록>],
  "relationships": {},
  "main_corridor_min_mm": <숫자 또는 null>,
  "emergency_path_min_mm": <숫자 또는 null>,
  "wall_clearance_mm": <숫자 또는 null>,
  "confidence": "high" | "medium" | "low",
  "source": "메뉴얼 추출"
}
```

규칙:
- 추측 금지 — 명확히 기재된 값만 추출
- 단위가 cm/m면 mm로 변환
- "넉넉하게", "적당히" 같은 표현은 null
- confidence: 명확한 수치 → high, 서술형 → medium, 불명확 → low
"""


async def run_agent1(pdf_bytes: bytes, client: anthropic.AsyncAnthropic) -> BrandStandards:
    """
    브랜드 메뉴얼 PDF → BrandStandards
    실패 또는 null 값은 DEFAULTS로 merge
    """
    import base64

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw_text = response.content[0].text.strip()

    # JSON 파싱
    try:
        # 마크다운 코드블록 제거
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        data: dict = json.loads(raw_text)
    except (json.JSONDecodeError, IndexError):
        # 파싱 실패 → 완전 기본값으로 fallback
        return BrandStandards(**DEFAULTS, source="기본값", confidence=ConfidenceLevel.LOW)

    # null 값 → DEFAULTS merge
    merged = {**DEFAULTS}
    for key, val in data.items():
        if val is not None:
            merged[key] = val

    merged.setdefault("source", "메뉴얼 추출")
    merged.setdefault("confidence", "medium")

    try:
        return BrandStandards(**merged)
    except Exception:
        return BrandStandards(**DEFAULTS, source="기본값", confidence=ConfidenceLevel.LOW)
