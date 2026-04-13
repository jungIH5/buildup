"""export.py — .glb 내보내기 라우터"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter()


class ExportRequest(BaseModel):
    room_polygon_mm: list[list[float]]
    placed_objects: list[dict]
    walls: list[dict] = []


@router.post("/glb")
async def export_glb(body: ExportRequest):
    """
    배치 결과를 3D .glb 파일로 내보내기.
    trimesh로 room floor + placed objects (boxes) + 가벽 생성.
    """
    try:
        import trimesh
        import numpy as np
        from shapely.geometry import Polygon

        meshes = []

        # ── 바닥면 ──
        if len(body.room_polygon_mm) >= 3:
            poly_2d = [(p[0], p[1]) for p in body.room_polygon_mm]
            shapely_poly = Polygon(poly_2d)
            if not shapely_poly.is_valid:
                shapely_poly = shapely_poly.buffer(0)
            floor_mesh = trimesh.creation.extrude_polygon(shapely_poly, height=100.0)
            floor_mesh.apply_translation([0, -100.0, 0])
            floor_mesh.visual.face_colors = [30, 40, 60, 255]
            meshes.append(floor_mesh)

        # ── 배치된 오브젝트 ──
        OBJECT_COLORS: dict[str, list[int]] = {
            "character_bbox":  [99,  102, 241, 210],
            "photo_zone":      [16,  185, 129, 210],
            "shelf_rental":    [236,  72, 153, 210],
            "banner_stand":    [245, 158,  11, 210],
            "product_display": [59,  130, 246, 210],
        }
        for obj in body.placed_objects:
            w = obj.get("bbox_mm", [900, 600])[0]
            d = obj.get("bbox_mm", [900, 600])[1]
            h = obj.get("height_mm", 1500)
            px, pz = obj.get("position_mm", [0, 0])
            rot_deg = obj.get("rotation_deg", 0)

            box = trimesh.creation.box(extents=[w, h, d])
            box.apply_translation([0, h / 2, 0])
            rot_mat = trimesh.transformations.rotation_matrix(
                np.radians(rot_deg), [0, 1, 0]
            )
            box.apply_transform(rot_mat)
            box.apply_translation([px, 0, pz])
            color = OBJECT_COLORS.get(obj.get("object_type", ""), [236, 72, 153, 210])
            box.visual.face_colors = color
            meshes.append(box)

        # ── 가벽 ──
        for wall in body.walls:
            length    = wall.get("length", 2000)
            height    = wall.get("height", 2500)
            thickness = wall.get("thickness", 100)
            wx        = wall.get("x", 0)
            wz        = wall.get("z", 0)
            rot_deg   = wall.get("rotation", 0)

            box = trimesh.creation.box(extents=[length, height, thickness])
            box.apply_translation([0, height / 2, 0])
            rot_mat = trimesh.transformations.rotation_matrix(
                np.radians(rot_deg), [0, 1, 0]
            )
            box.apply_transform(rot_mat)
            box.apply_translation([wx, 0, wz])
            box.visual.face_colors = [148, 163, 184, 230]
            meshes.append(box)

        if not meshes:
            raise HTTPException(status_code=400, detail="내보낼 오브젝트가 없습니다.")

        scene = trimesh.Scene(meshes)
        glb_bytes = scene.export(file_type="glb")

        return Response(
            content=glb_bytes,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": "attachment; filename=buildup_layout.glb"},
        )

    except ImportError:
        raise HTTPException(status_code=500, detail="trimesh 라이브러리가 설치되지 않았습니다.")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"GLB 내보내기 실패: {e}")
