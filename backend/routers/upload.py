"""upload.py — 파일 업로드 라우터"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_PDF_TYPES = {"application/pdf"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


@router.post("/brand-manual")
async def upload_brand_manual(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_PDF_TYPES:
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드 가능합니다.")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="파일 크기가 20MB를 초과합니다.")
    return JSONResponse({"message": "브랜드 메뉴얼 업로드 완료", "filename": file.filename, "size": len(data)})


@router.post("/floor-plan")
async def upload_floor_plan(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_IMAGE_TYPES | ALLOWED_PDF_TYPES:
        raise HTTPException(status_code=400, detail="PNG/JPG/WebP/PDF 파일만 업로드 가능합니다.")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="파일 크기가 20MB를 초과합니다.")
    return JSONResponse({"message": "도면 업로드 완료", "filename": file.filename, "size": len(data)})
