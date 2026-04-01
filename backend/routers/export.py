"""export.py — .glb 내보내기 라우터 (Sprint 2 구현 예정)"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/glb")
async def export_glb():
    """
    배치 결과를 Three.js Whitebox 3D → .glb로 내보내기.
    Sprint 2에서 trimesh 기반 구현 예정.
    """
    raise HTTPException(status_code=501, detail="Sprint 2에서 구현 예정입니다.")
