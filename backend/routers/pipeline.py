"""
pipeline.py — Agent 1→2→3 전체 파이프라인 실행 라우터
"""

from __future__ import annotations
import base64
import json
from fastapi import APIRouter, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from agents.agent1_brand import run_agent1
from agents.agent2_floor import run_agent2
from agents.agent3_layout import run_agent3
from core.schemas import LayoutResult

router = APIRouter()

# 임시 furniture_sizes (Sprint 2: Supabase furniture_standards로 교체 예정)
FURNITURE_SIZES: dict[str, tuple[float, float]] = {
    "character_bbox": (800, 800),
    "shelf_rental": (600, 400),
    "photo_zone": (1500, 1200),
    "banner_stand": (600, 200),
    "product_display": (900, 600),
}


class UserMarkingItem(BaseModel):
    equipment_type: str
    position_px: tuple[float, float]


@router.post("/run")
async def run_pipeline(
    request: Request,
    brand_manual: Optional[UploadFile] = File(None, description="브랜드 메뉴얼 PDF (선택사항)"),
    floor_plan: Optional[UploadFile] = File(None, description="도면 이미지/PDF (선택사항)"),
    user_markings: Optional[str] = Form(None, description="사용자 마킹 JSON 문자열"),
):
    """
    Agent 1 → Agent 2 → Agent 3 전체 파이프라인 실행.
    """
    client = request.app.state.anthropic

    try:
        pdf_bytes = await brand_manual.read() if brand_manual else None
        image_bytes = await floor_plan.read() if floor_plan else None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 읽기 실패: {e}")

    # 사용자 마킹 파싱
    markings = None
    if user_markings:
        try:
            markings = json.loads(user_markings)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="user_markings JSON 파싱 실패")

    # Agent 1
    try:
        if pdf_bytes:
            standards = await run_agent1(pdf_bytes, client)
        else:
            from core.schemas import BrandStandards
            standards = BrandStandards()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Agent 1 실패: {e}")

    # Agent 2
    floor_png_b64: str | None = None
    try:
        if floor_plan:
            floor_bytes = image_bytes

            # PDF인 경우 PNG로 변환
            page_size_mm = None
            original_pdf_bytes = None
            if floor_plan.content_type == "application/pdf" or floor_plan.filename.lower().endswith(".pdf"):
                import fitz  # PyMuPDF
                original_pdf_bytes = floor_bytes
                doc = fitz.open(stream=floor_bytes, filetype="pdf")
                page = doc.load_page(0)
                page_size_mm = (page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                floor_bytes = pix.tobytes("png")
                doc.close()

            # 변환된 PNG를 프론트엔드 텍스처용으로 보관
            floor_png_b64 = base64.b64encode(floor_bytes).decode()

            floor, constraints, image_meta = await run_agent2(
                image_bytes=floor_bytes,
                standards=standards,
                user_marked_equipment=markings,
                client=client,
                page_size_mm=page_size_mm,
                pdf_bytes=original_pdf_bytes,
            )
        else:
            # 도면이 없을 때: 10m x 8m 가상 샘플 공간 생성
            from agents.agent2_floor import FloorAnalysis, ReferencePoint, ConfidenceLevel
            room_poly = [(0, 0), (10000, 0), (10000, 8000), (0, 8000)]
            floor = FloorAnalysis(
                room_polygon_mm=room_poly,
                dead_zones_mm=[],
                reference_points=[
                    ReferencePoint(name="center", position_mm=(5000, 4000)),
                    ReferencePoint(name="entrance", position_mm=(5000, 7700), facing="inward"),
                    ReferencePoint(name="north_wall_mid", position_mm=(5000, 300), facing="south"),
                ],
                eligible_objects=["character_bbox", "shelf_rental", "photo_zone"],
                scale_mm_per_px=1.0,
                scale_confidence=ConfidenceLevel.USER_INPUT,
                equipment_detected=[],
                disclaimer_items=[],
            )
            constraints = {
                "natural_language_constraints": "샘플 공간입니다. 브랜드 기준에 따른 최적의 배치를 제안합니다.",
                "eligible_objects": floor.eligible_objects,
                "priority_notes": "창작물을 돋보이게 하는 중앙 집중형 배치 권장"
            }
            image_meta = {"image_size_px": None, "room_bbox_px": None}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Agent 2 실패: {e}")

    # Agent 3
    try:
        # 비상구 좌표 추출 (check_emergency_path 활성화)
        emergency_exits = [
            (eq.position_mm[0], eq.position_mm[1])
            for eq in floor.equipment_detected
            if eq.equipment_type in ("exit", "emergency_exit") and eq.position_mm is not None
        ]

        result: LayoutResult = await run_agent3(
            floor=floor,
            standards=standards,
            constraints=constraints,
            furniture_sizes=FURNITURE_SIZES,
            client=client,
            emergency_exits=emergency_exits if emergency_exits else None,
            relationships=standards.relationships if standards.relationships else None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Agent 3 실패: {e}")

    # 결과 직렬화
    return JSONResponse(content={
        "placed": [p.model_dump() for p in result.placed],
        "failed": result.failed,
        "violations": [v.model_dump() for v in result.violations],
        "glb_blocked": result.glb_blocked,
        "disclaimer_items": result.disclaimer_items,
        "summary": {
            "total_requested": len(floor.eligible_objects),
            "total_placed": len(result.placed),
            "total_failed": len(result.failed),
            "has_blocking_violation": result.glb_blocked,
        },
        "brand_standards": standards.model_dump(),
        "equipment_detected": [eq.model_dump() for eq in floor.equipment_detected],
        "room_polygon_mm": floor.room_polygon_mm,
        "image_size_px": image_meta.get("image_size_px"),
        "room_bbox_px": image_meta.get("room_bbox_px"),
        "floor_plan_png": floor_png_b64,
    })


@router.post("/agent2/review")
async def agent2_review(
    request: Request,
    floor_plan: UploadFile = File(...),
    user_markings: Optional[str] = Form(None),
):
    """
    Agent 2 결과만 반환 (사용자 확인 단계용).
    Dead Zone + reference_points (zone_label 포함) + eligible_objects 시각화 데이터 제공.
    """
    from core.schemas import BrandStandards
    client = request.app.state.anthropic
    image_bytes = await floor_plan.read()
    standards = BrandStandards()

    markings = None
    if user_markings:
        try:
            markings = json.loads(user_markings)
        except json.JSONDecodeError:
            pass

    # PDF이면 PNG 변환
    page_size_mm = None
    original_pdf_bytes = None
    if floor_plan.content_type == "application/pdf" or floor_plan.filename.lower().endswith(".pdf"):
        import fitz
        original_pdf_bytes = image_bytes
        doc = fitz.open(stream=image_bytes, filetype="pdf")
        page = doc.load_page(0)
        page_size_mm = (page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        image_bytes = pix.tobytes("png")
        doc.close()

    floor, constraints, _ = await run_agent2(
        image_bytes=image_bytes,
        standards=standards,
        user_marked_equipment=markings,
        client=client,
        page_size_mm=page_size_mm,
        pdf_bytes=original_pdf_bytes,
    )

    return JSONResponse(content={
        "room_polygon_mm": floor.room_polygon_mm,
        "dead_zones_mm": floor.dead_zones_mm,
        "reference_points": [rp.model_dump() for rp in floor.reference_points],
        "eligible_objects": floor.eligible_objects,
        "scale_mm_per_px": floor.scale_mm_per_px,
        "scale_confidence": floor.scale_confidence.value,
        "equipment_detected": [eq.model_dump() for eq in floor.equipment_detected],
        "disclaimer_items": floor.disclaimer_items,
        "constraints_preview": constraints,
    })
