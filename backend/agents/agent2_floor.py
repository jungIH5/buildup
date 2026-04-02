"""
agent2_floor.py — Agent 2: 도면 분석 + Dead Zone 생성

2단계 구조:
  전반부: OpenCV polygon + OCR 축척 + Claude Vision 설비 감지
  후반부: px→mm + Shapely Dead Zone + NetworkX reference_point 계산
"""

from __future__ import annotations
import base64
import json
import math
import re
from typing import Optional
import cv2
import networkx as nx
import numpy as np
import anthropic
from shapely.geometry import Point as SPoint, Polygon as SPolygon
from core.schemas import (
    BrandStandards, FloorAnalysis, Equipment, ReferencePoint, ConfidenceLevel
)
from core.geometry_utils import px_to_mm

# ─────────────────────────────────────────
# PDF 벡터 직접 추출 (CAD PDF 전용)
# ─────────────────────────────────────────

def extract_from_pdf_vectors(pdf_bytes: bytes) -> dict | None:
    """
    fitz.get_drawings()로 CAD PDF 벡터 패스를 직접 파싱.
    성공 시 dict 반환, 벡터 추출 불가 시 None.

    반환 키:
      room_polygon_mm  : [(x,y), ...] 실제 mm 좌표
      scale_ratio      : int (예: 50)
      scale_mm_per_pt  : float (1pt = N mm 실제)
      equipment_raw    : [{"type": str, "center_pt": (x,y), "bbox_pt": (x0,y0,x1,y1)}]
    """
    try:
        import fitz
    except ImportError:
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)
    except Exception:
        return None

    # ── 1. Scale 텍스트 추출 ──────────────────────────
    scale_ratio: int | None = None
    for block in page.get_text("blocks"):
        m = re.search(r"1\s*[:/]\s*(\d+)", block[4])
        if m:
            scale_ratio = int(m.group(1))
            break

    if not scale_ratio:
        doc.close()
        return None  # scale 모르면 mm 변환 불가

    scale_mm_per_pt: float = (25.4 / 72) * scale_ratio

    def pt2mm(v: float) -> float:
        return v * scale_mm_per_pt

    # ── 2. 방 외곽 = 두꺼운 검정 선 + 최대 면적 ────────
    drawings = page.get_drawings()

    def is_dark(color) -> bool:
        return color is not None and all(c < 0.3 for c in color)

    room_drawing = None
    best_area = 0.0
    for d in drawings:
        sw = d.get("width") or 0
        if sw < 1.5:          # 얇은 격자·치수선 제외
            continue
        if not is_dark(d.get("color")):
            continue
        area = d["rect"].get_area()
        if area > best_area:
            best_area = area
            room_drawing = d

    if room_drawing is None:
        doc.close()
        return None

    # 방 외곽 폴리곤 — items에서 꼭짓점 추출, 없으면 rect 사용
    room_pts: list[tuple[float, float]] = []
    for item in room_drawing.get("items", []):
        if item[0] == "l":          # ('l', Point_start, Point_end)
            p = item[1]
            room_pts.append((p.x, p.y))
        elif item[0] == "re":       # ('re', Rect, ...)
            r2 = item[1]
            room_pts += [(r2.x0, r2.y0), (r2.x1, r2.y0), (r2.x1, r2.y1), (r2.x0, r2.y1)]

    # 중복 제거 + 순서 유지
    seen: set = set()
    unique_pts: list[tuple[float, float]] = []
    for p in room_pts:
        key = (round(p[0], 1), round(p[1], 1))
        if key not in seen:
            seen.add(key)
            unique_pts.append(p)

    # 꼭짓점이 3개 미만이면 rect 폴백
    if len(unique_pts) < 3:
        r = room_drawing["rect"]
        unique_pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]

    room_polygon_mm = [(pt2mm(x), pt2mm(y)) for x, y in unique_pts]

    # ── 3. 설비 감지 — 색상·크기 기반 휴리스틱 ──────────
    EQUIPMENT_COLOR_MAP = {
        "sprinkler":       lambda c: c is not None and c[0] > 0.6 and c[1] < 0.3 and c[2] < 0.3,
        "fire_extinguisher": lambda c: c is not None and c[0] > 0.6 and c[1] < 0.3 and c[2] < 0.3,
        "exit":            lambda c: c is not None and c[2] > 0.6 and c[0] < 0.3,
    }

    equipment_raw: list[dict] = []
    for d in drawings:
        r2 = d["rect"]
        area_pt2 = r2.get_area()
        if area_pt2 > best_area * 0.05:   # 방 외곽의 5% 이상이면 설비 아님
            continue
        if area_pt2 < 1.0:                # 너무 작으면 제외
            continue
        color = d.get("color")
        eq_type = None
        for etype, check in EQUIPMENT_COLOR_MAP.items():
            if check(color):
                eq_type = etype
                break
        if eq_type:
            cx_pt = (r2.x0 + r2.x1) / 2
            cy_pt = (r2.y0 + r2.y1) / 2
            equipment_raw.append({
                "type": eq_type,
                "center_mm": (pt2mm(cx_pt), pt2mm(cy_pt)),
                "bbox_mm": (pt2mm(r2.x0), pt2mm(r2.y0), pt2mm(r2.x1), pt2mm(r2.y1)),
            })

    doc.close()
    return {
        "room_polygon_mm": room_polygon_mm,
        "scale_ratio": scale_ratio,
        "scale_mm_per_pt": scale_mm_per_pt,
        "equipment_raw": equipment_raw,
    }


# ─────────────────────────────────────────
# 전반부: 도면 이미지 분석
# ─────────────────────────────────────────

VISION_SYSTEM = """당신은 건축 도면 분석 전문가입니다.
제공된 도면 이미지에서 설비와 공간 정보를 감지합니다.
반드시 JSON만 출력하고 추측은 하지 마세요."""

VISION_PROMPT = """이 도면 이미지를 분석하여 아래 JSON을 출력하세요.

```json
{
  "room_bbox_px": [x1, y1, x2, y2],
  "scale_ratio": <"SCALE 1:XX" 또는 "1/XX"에서 XX 숫자, 없으면 null>,
  "equipment": [
    {
      "equipment_type": "sprinkler" | "fire_extinguisher" | "distribution_panel" | "exit" | "bed" | "desk" | "sofa" | "table" | "chair",
      "position_px": [center_x, center_y],
      "bbox_px": [x1, y1, x2, y2],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "scale_indicator": {
    "found": true | false,
    "real_length_mm": <축척 바의 실제 길이(mm), 없으면 null>,
    "pixel_length": <축척 바의 픽셀 길이, 없으면 null>,
    "confidence": "high" | "medium" | "low"
  },
  "room_shape": "rectangle" | "irregular" | "unknown",
  "disclaimers": ["감지하지 못한 항목"]
}
```

규칙:
- room_bbox_px: 방의 내벽 경계를 픽셀 좌표로 표현 (도면 용지 여백 제외)
- scale_ratio: 예) "SCALE 1:50" → 50, "1/100" → 100
- 추측 금지, 확인된 값만 기재
"""


async def analyze_floor_image(
    image_bytes: bytes,
    client: anthropic.AsyncAnthropic,
) -> dict:
    """Claude Vision으로 설비 및 축척 감지"""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    ext = _detect_mime(image_bytes)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=VISION_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": ext, "data": b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )

    import logging
    u = response.usage
    logging.info(f"[Agent2/Vision] input={u.input_tokens} output={u.output_tokens}")

    raw = response.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"equipment": [], "scale_indicator": {"found": False}, "disclaimers": ["Claude Vision 파싱 실패"]}


def extract_room_polygon_opencv(img_gray: np.ndarray) -> list[tuple[float, float]]:
    """
    OpenCV 폴백: 이미지 면적의 15% 이상인 가장 큰 닫힌 윤곽을 방 외곽으로 사용.
    CAD 도면은 흰 배경 + 검은 벽선이므로 Canny 엣지 기반으로 처리.
    """
    if img_gray is None:
        return []

    img_area = img_gray.shape[0] * img_gray.shape[1]

    # Canny 엣지 → 닫힌 윤곽 탐색
    edges = cv2.Canny(img_gray, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    # 이미지 면적의 15% 이상인 윤곽만 후보로
    candidates = [c for c in contours if cv2.contourArea(c) >= img_area * 0.15]
    if not candidates:
        # 15% 기준 완화: 가장 큰 것 하나
        candidates = [max(contours, key=cv2.contourArea)]

    largest = max(candidates, key=cv2.contourArea)
    epsilon = 0.02 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)
    return [(float(pt[0][0]), float(pt[0][1])) for pt in approx]


def compute_scale(
    vision_result: dict,
    image_size_px: tuple[int, int] | None = None,
    page_size_mm: tuple[float, float] | None = None,
) -> tuple[float, ConfidenceLevel]:
    """
    px당 mm 배율 계산. 우선순위:
    1. 축척 바 실측 (real_length_mm / pixel_length)
    2. scale_ratio 텍스트 + 페이지 실제 크기 (mm_per_px × ratio)
    3. fallback 1.0
    """
    conf_map = {"high": ConfidenceLevel.HIGH, "medium": ConfidenceLevel.MEDIUM, "low": ConfidenceLevel.LOW}

    # 1) 축척 바 실측
    si = vision_result.get("scale_indicator", {})
    if si.get("found") and si.get("real_length_mm") and si.get("pixel_length"):
        scale = si["real_length_mm"] / si["pixel_length"]
        return scale, conf_map.get(si.get("confidence", "medium"), ConfidenceLevel.MEDIUM)

    # 2) scale_ratio ("1:50" → 50) + 페이지 실제 크기
    scale_ratio = vision_result.get("scale_ratio")
    if scale_ratio and image_size_px and page_size_mm:
        mm_per_px_on_paper = page_size_mm[0] / image_size_px[0]
        return mm_per_px_on_paper * float(scale_ratio), ConfidenceLevel.MEDIUM

    return 1.0, ConfidenceLevel.LOW


# ─────────────────────────────────────────
# 후반부: Dead Zone + reference_point
# ─────────────────────────────────────────

CONSTRAINT_SYSTEM = """당신은 공간 배치 제약 분석 전문가입니다.
도면 분석 결과를 바탕으로 배치 제약 사항을 자연어로 설명합니다.
좌표나 mm 숫자 값을 Agent 3에게 전달하지 마세요.
"""

CONSTRAINT_PROMPT_TEMPLATE = """다음 공간 분석 결과를 바탕으로 배치 제약 사항을 작성하세요.

공간 정보:
{space_info}

브랜드 기준값:
{brand_info}

출력 형식 (JSON):
```json
{{
  "natural_language_constraints": "배치 시 고려해야 할 제약 사항을 자연어로 서술",
  "eligible_objects": ["배치 가능 오브젝트 코드명 목록"],
  "priority_notes": "우선순위 고려사항"
}}
```

규칙:
- 좌표, 픽셀, mm 숫자 값 절대 포함 금지
- reference_point 이름(entrance, north_wall_mid 등)으로만 위치 표현
- eligible_objects는 Supabase furniture_standards 코드명 사용
"""


async def build_constraints(
    floor_analysis: FloorAnalysis,
    standards: BrandStandards,
    client: anthropic.AsyncAnthropic,
) -> dict:
    """Agent 2 후반부: 자연어 제약 생성 (Agent 3 입력용)"""
    space_info = {
        "reference_points": [rp.model_dump() for rp in floor_analysis.reference_points],
        "eligible_objects": floor_analysis.eligible_objects,
        "equipment_count": len(floor_analysis.equipment_detected),
        "dead_zone_count": len(floor_analysis.dead_zones_mm),
        "disclaimer_count": len(floor_analysis.disclaimer_items),
    }
    brand_info = {
        "clearspace_mm": standards.clearspace_mm,
        "character_orientation": standards.character_orientation,
        "prohibited_material": standards.prohibited_material,
        "main_corridor_min_mm": standards.main_corridor_min_mm,
    }

    prompt = CONSTRAINT_PROMPT_TEMPLATE.format(
        space_info=json.dumps(space_info, ensure_ascii=False, indent=2),
        brand_info=json.dumps(brand_info, ensure_ascii=False, indent=2),
    )

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=CONSTRAINT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    import logging
    u = response.usage
    logging.info(f"[Agent2/Constraints] input={u.input_tokens} output={u.output_tokens}")

    raw = response.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "natural_language_constraints": "자동 제약 생성 실패. 기본 규칙 적용.",
            "eligible_objects": floor_analysis.eligible_objects,
            "priority_notes": "",
        }


# ─────────────────────────────────────────
# Agent 2 메인 진입점
# ─────────────────────────────────────────

async def run_agent2(
    image_bytes: bytes,
    standards: BrandStandards,
    user_marked_equipment: list[dict] | None,
    client: anthropic.AsyncAnthropic,
    page_size_mm: tuple[float, float] | None = None,
    pdf_bytes: bytes | None = None,
) -> tuple[FloorAnalysis, dict, dict]:
    """
    반환: (FloorAnalysis, constraints_dict, image_meta)

    PDF 입력 시: 벡터 직접 추출 → Vision/OpenCV 스킵
    이미지 입력 시: Vision(Haiku) → OpenCV 폴백
    """
    import logging

    all_equipment: list[Equipment] = []
    room_polygon_mm: list[tuple[float, float]] = []
    scale_mm_per_px: float = 1.0
    scale_confidence = ConfidenceLevel.LOW
    disclaimers: list[str] = []
    image_size_px: tuple[int, int] | None = None
    room_bbox_px = None

    # ── 경로 A: PDF 벡터 직접 추출 ────────────────────
    vec = extract_from_pdf_vectors(pdf_bytes) if pdf_bytes else None

    if vec:
        logging.info(f"[Agent2/Vector] 성공 — scale=1:{vec['scale_ratio']}, "
                     f"polygon={len(vec['room_polygon_mm'])}pts, "
                     f"equipment={len(vec['equipment_raw'])}개")

        room_polygon_mm = vec["room_polygon_mm"]
        scale_mm_per_pt = vec["scale_mm_per_pt"]
        scale_confidence = ConfidenceLevel.HIGH
        # scale_mm_per_px는 이미지 좌표계 변환용 (렌더링 2x 기준)
        scale_mm_per_px = scale_mm_per_pt * (72 / 25.4) / 2  # pt→px (2x render)

        for eq in vec["equipment_raw"]:
            cx_mm, cy_mm = eq["center_mm"]
            # position_px는 렌더링 이미지 기준 역산 (참조용으로만 저장)
            cx_px = cx_mm / scale_mm_per_px
            cy_px = cy_mm / scale_mm_per_px
            all_equipment.append(Equipment(
                equipment_type=eq["type"],
                position_px=(cx_px, cy_px),
                position_mm=(cx_mm, cy_mm),
                confidence=ConfidenceLevel.HIGH,
                source="auto_detected",
            ))

        # 이미지 크기는 렌더링된 png에서 추출 (3D 뷰용)
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img_gray = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)
        if img_gray is not None:
            image_size_px = (img_gray.shape[1], img_gray.shape[0])
            # room_bbox_px: mm→px 역산
            xs = [p[0] for p in room_polygon_mm]
            ys = [p[1] for p in room_polygon_mm]
            room_bbox_px = [
                min(xs) / scale_mm_per_px, min(ys) / scale_mm_per_px,
                max(xs) / scale_mm_per_px, max(ys) / scale_mm_per_px,
            ]

    else:
        # ── 경로 B: 이미지 Vision + OpenCV 폴백 ──────────
        logging.info("[Agent2/Vision] 벡터 추출 실패 또는 이미지 입력 — Vision 경로 사용")

        np_arr = np.frombuffer(image_bytes, np.uint8)
        img_gray = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)
        image_size_px = (img_gray.shape[1], img_gray.shape[0]) if img_gray is not None else None

        vision_result = await analyze_floor_image(image_bytes, client)
        logging.info(f"[Agent2/Vision] room_bbox_px={vision_result.get('room_bbox_px')}, "
                     f"scale_ratio={vision_result.get('scale_ratio')}")

        scale_mm_per_px, scale_confidence = compute_scale(vision_result, image_size_px, page_size_mm)

        vision_bbox = vision_result.get("room_bbox_px")
        if vision_bbox and len(vision_bbox) == 4:
            x1, y1, x2, y2 = vision_bbox
            room_polygon_px = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            room_bbox_px = vision_bbox
        else:
            room_polygon_px = extract_room_polygon_opencv(img_gray)
            room_bbox_px = None

        room_polygon_mm = [
            (px_to_mm(x, scale_mm_per_px), px_to_mm(y, scale_mm_per_px))
            for x, y in room_polygon_px
        ] if room_polygon_px else [(0, 0), (6000, 0), (6000, 6000), (0, 6000)]

        for eq in vision_result.get("equipment", []):
            bbox = eq.get("bbox_px")
            size_mm = None
            if bbox:
                w_px = abs(bbox[2] - bbox[0])
                d_px = abs(bbox[3] - bbox[1])
                size_mm = (px_to_mm(w_px, scale_mm_per_px), px_to_mm(d_px, scale_mm_per_px))
            pos_px = eq["position_px"]
            pos_mm = (px_to_mm(pos_px[0], scale_mm_per_px), px_to_mm(pos_px[1], scale_mm_per_px))
            all_equipment.append(Equipment(
                equipment_type=eq["equipment_type"],
                position_px=tuple(pos_px),
                position_mm=pos_mm,
                bbox_px=tuple(bbox) if bbox else None,
                size_mm=size_mm,
                confidence=ConfidenceLevel(eq.get("confidence", "low")),
                source="auto_detected",
            ))
        disclaimers = vision_result.get("disclaimers", [])

    # ── 공통: 사용자 마킹 병합 ──────────────────────────
    if user_marked_equipment:
        for eq in user_marked_equipment:
            pos_px = eq["position_px"]
            pos_mm = (px_to_mm(pos_px[0], scale_mm_per_px), px_to_mm(pos_px[1], scale_mm_per_px))
            all_equipment.append(Equipment(
                equipment_type=eq["equipment_type"],
                position_px=tuple(pos_px),
                position_mm=pos_mm,
                confidence=ConfidenceLevel.USER_INPUT,
                source="user_marked",
            ))

    # ── 공통: reference_point, dead_zone 생성 ──────────
    reference_points = _generate_reference_points(room_polygon_mm, all_equipment)

    # NetworkX 보행 거리 계산 → zone_label 할당
    entrance_pos = next(
        (rp.position_mm for rp in reference_points if rp.name == "entrance"),
        None,
    )
    if entrance_pos:
        reference_points = _assign_zone_labels(reference_points, room_polygon_mm, entrance_pos)

    dead_zones_mm = _generate_dead_zones(all_equipment, standards)
    eligible_objects = ["character_bbox", "shelf_rental", "photo_zone", "banner_stand", "product_display"]

    floor = FloorAnalysis(
        room_polygon_mm=room_polygon_mm,
        dead_zones_mm=dead_zones_mm,
        reference_points=reference_points,
        eligible_objects=eligible_objects,
        scale_mm_per_px=scale_mm_per_px,
        scale_confidence=scale_confidence,
        equipment_detected=all_equipment,
        disclaimer_items=disclaimers,
    )

    constraints = await build_constraints(floor, standards, client)

    image_meta = {"image_size_px": image_size_px, "room_bbox_px": room_bbox_px}
    return floor, constraints, image_meta


def _assign_zone_labels(
    reference_points: list[ReferencePoint],
    room_polygon_mm: list[tuple[float, float]],
    entrance_pos: tuple[float, float],
    grid_mm: float = 200.0,
) -> list[ReferencePoint]:
    """
    NetworkX 격자 그래프로 입구→각 기준점 보행 거리 계산.
    거리 비율로 zone_label 할당:
      0~33%  → entrance_zone
      33~67% → mid_zone
      67~100%→ deep_zone
    """
    if not room_polygon_mm or len(room_polygon_mm) < 3:
        return reference_points

    try:
        room_poly = SPolygon(room_polygon_mm)
        minx, miny, maxx, maxy = room_poly.bounds
        step = int(grid_mm)

        # 방 내부 격자 노드 생성
        G: nx.Graph = nx.Graph()
        node_set: set[tuple[int, int]] = set()
        for x in range(int(minx), int(maxx) + step, step):
            for y in range(int(miny), int(maxy) + step, step):
                if room_poly.contains(SPoint(x, y)):
                    G.add_node((x, y))
                    node_set.add((x, y))

        # 4방향 + 대각선 엣지
        for (x, y) in list(G.nodes()):
            for dx, dy in [(step, 0), (0, step), (step, step), (-step, step)]:
                nb = (x + dx, y + dy)
                if nb in node_set:
                    G.add_edge((x, y), nb, weight=math.sqrt(dx ** 2 + dy ** 2))

        if not G.nodes():
            return reference_points

        # 입구에서 가장 가까운 노드
        entrance_node = min(
            G.nodes(),
            key=lambda n: (n[0] - entrance_pos[0]) ** 2 + (n[1] - entrance_pos[1]) ** 2,
        )
        walk_dists: dict = nx.single_source_dijkstra_path_length(G, entrance_node, weight="weight")
        max_dist = max(walk_dists.values()) if walk_dists else 1.0

        updated: list[ReferencePoint] = []
        for rp in reference_points:
            nearest = min(
                G.nodes(),
                key=lambda n: (n[0] - rp.position_mm[0]) ** 2 + (n[1] - rp.position_mm[1]) ** 2,
            )
            dist = walk_dists.get(nearest, max_dist)
            ratio = dist / max_dist if max_dist > 0 else 0.0
            zone = "entrance_zone" if ratio < 0.33 else ("mid_zone" if ratio < 0.67 else "deep_zone")
            updated.append(ReferencePoint(
                name=rp.name,
                position_mm=rp.position_mm,
                facing=rp.facing,
                zone_label=zone,
                walk_distance_mm=round(dist, 1),
            ))
        return updated

    except Exception:
        return reference_points


def _generate_reference_points(
    room_polygon_mm: list[tuple[float, float]],
    equipment: list[Equipment],
) -> list[ReferencePoint]:
    """방 형태 기반 기준점 생성 (mm 좌표 직접 사용)"""
    if not room_polygon_mm:
        return []

    xs = [p[0] for p in room_polygon_mm]
    ys = [p[1] for p in room_polygon_mm]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2

    margin = min((max_x - min_x), (max_y - min_y)) * 0.05  # 방 단변의 5%
    rps = [
        ReferencePoint(name="center", position_mm=(cx, cy)),
        ReferencePoint(name="north_wall_mid", position_mm=(cx, min_y + margin), facing="south"),
        ReferencePoint(name="south_wall_mid", position_mm=(cx, max_y - margin), facing="north"),
        ReferencePoint(name="east_wall_mid",  position_mm=(max_x - margin, cy), facing="west"),
        ReferencePoint(name="west_wall_mid",  position_mm=(min_x + margin, cy), facing="east"),
    ]

    # 출입구: exit 설비에서 추출, 없으면 남쪽 벽 기본값
    entrance_found = any(eq.equipment_type in ("exit", "emergency_exit") for eq in equipment)
    if not entrance_found:
        rps.append(ReferencePoint(name="entrance", position_mm=(cx, max_y - margin), facing="inward"))
    else:
        for eq in equipment:
            if eq.equipment_type in ("exit", "emergency_exit") and eq.position_mm:
                rps.append(ReferencePoint(name="entrance", position_mm=eq.position_mm, facing="inward"))
                break

    return rps


def _generate_dead_zones(
    equipment: list[Equipment],
    standards: BrandStandards,
) -> list[list[tuple[float, float]]]:
    """
    설비 주변 Dead Zone — position_mm 직접 사용.

    비상구(exit/emergency_exit):
      - 비상구 직접 인접 구역: emergency_path_min_mm(기본 1200mm) 반경 사각형
      - 비상구 앞 동선 확보: 비상구 내측 방향으로 emergency_path_min_mm × (emergency_path_min_mm*2) 직사각형
    기타 설비: wall_clearance_mm 반경 사각형
    """
    dead_zones = []
    for eq in equipment:
        if eq.position_mm is None:
            continue
        mx, my = eq.position_mm

        if eq.equipment_type in ("exit", "emergency_exit"):
            # ① 비상구 직접 인접 사각 Dead Zone (1200mm 반경)
            r = standards.emergency_path_min_mm
            dead_zones.append([
                (mx - r, my - r),
                (mx + r, my - r),
                (mx + r, my + r),
                (mx - r, my + r),
            ])
            # ② 비상구 앞 통로 Dead Zone: 비상구에서 실내 방향으로 추가 1200mm 확보
            # 비상구는 보통 벽 경계에 있으므로 내측(y 감소 방향)으로 복도를 확보
            corridor_depth = standards.emergency_path_min_mm  # 1200mm 추가
            dead_zones.append([
                (mx - r / 2, my - r - corridor_depth),
                (mx + r / 2, my - r - corridor_depth),
                (mx + r / 2, my - r),
                (mx - r / 2, my - r),
            ])
        else:
            radius = standards.wall_clearance_mm
            dead_zones.append([
                (mx - radius, my - radius),
                (mx + radius, my - radius),
                (mx + radius, my + radius),
                (mx - radius, my + radius),
            ])
    return dead_zones


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:4] == b'\x89PNG':
        return "image/png"
    if image_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    if image_bytes[:4] == b'RIFF':
        return "image/webp"
    return "image/png"
