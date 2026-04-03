"""
generate_sample_dxf.py — 샘플 DXF 도면 생성기

실행: python generate_sample_dxf.py
출력: sample_floor_Lshape.dxf  (20m × 15m L자형, SCALE 1:50)
       sample_floor_rect.dxf   (12m × 9m 직사각형, SCALE 1:100)
"""

import ezdxf
from ezdxf.enums import TextEntityAlignment


def _add_dim_text(msp, text: str, x: float, y: float, height: float = 200):
    """치수 텍스트 삽입 (실제 mm 값 레이블)"""
    msp.add_text(
        text,
        dxfattribs={
            "insert": (x, y),
            "height": height,
            "layer": "DIMENSIONS",
            "color": 1,  # 빨간색
        },
    )


def _add_equipment(msp, eq_type: str, x: float, y: float, radius: float = 150):
    """설비 심볼 (원 + 텍스트)"""
    msp.add_circle(
        center=(x, y),
        radius=radius,
        dxfattribs={"layer": "EQUIPMENT", "color": 1},
    )
    msp.add_text(
        eq_type,
        dxfattribs={
            "insert": (x - radius * 0.8, y - radius * 0.4),
            "height": radius * 0.7,
            "layer": "EQUIPMENT",
            "color": 1,
        },
    )


def create_lshape_dxf(output_path: str = "sample_floor_Lshape.dxf"):
    """
    20m × 15m L자형 도면 생성 (실제 크기 mm, SCALE 1:50 표기)

    L자형 외곽 (mm):
      (0,0) → (20000,0) → (20000,8000) → (12000,8000) → (12000,15000) → (0,15000) → (0,0)
    """
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  # mm
    doc.header["$MEASUREMENT"] = 1  # metric

    # 레이어 정의
    doc.layers.add("WALL",       color=7)   # 흰색/검정 — 벽
    doc.layers.add("DIMENSIONS", color=1)   # 빨간색 — 치수
    doc.layers.add("EQUIPMENT",  color=1)   # 빨간색 — 설비
    doc.layers.add("TEXT",       color=3)   # 초록색 — 일반 텍스트
    doc.layers.add("EXIT",       color=3)   # 초록색 — 비상구

    msp = doc.modelspace()

    # ── 방 외곽선 (L자형, 두꺼운 선) ──────────────────────
    wall_pts = [
        (0, 0), (20000, 0), (20000, 8000),
        (12000, 8000), (12000, 15000), (0, 15000), (0, 0),
    ]
    msp.add_lwpolyline(
        wall_pts,
        close=True,
        dxfattribs={"layer": "WALL", "lineweight": 50, "color": 7},
    )

    # ── 치수선 및 mm 레이블 ────────────────────────────────
    # 전체 폭 (하단)
    msp.add_line((0, -500), (20000, -500), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 0), (0, -600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((20000, 0), (20000, -600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "20000mm (전체 폭)", 8000, -900)

    # 상단 폭 (12000)
    msp.add_line((0, 15500), (12000, 15500), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 15000), (0, 15600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((12000, 15000), (12000, 15600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "12000mm", 4000, 15700)

    # 전체 높이 (좌측)
    msp.add_line((-500, 0), (-500, 15000), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 0), (-600, 0), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 15000), (-600, 15000), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "15000mm (전체 높이)", -2800, 6500)

    # 단차 높이 (우측 하단)
    msp.add_line((20500, 0), (20500, 8000), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "8000mm", 20600, 3500)

    # ── 스프링클러 3개 ────────────────────────────────────
    _add_equipment(msp, "SP", 4000,  12000)
    _add_equipment(msp, "SP", 10000, 12000)
    _add_equipment(msp, "SP", 4000,  4000)

    # ── 비상구 (좌하단, 하단 중앙) ───────────────────────
    msp.add_lwpolyline(
        [(0, 1500), (800, 1500), (800, 3000), (0, 3000)],
        close=True,
        dxfattribs={"layer": "EXIT", "color": 3},
    )
    _add_dim_text(msp, "비상구\nW=800mm", -200, 2100, height=150)

    msp.add_lwpolyline(
        [(9000, 0), (11000, 0), (11000, -200), (9000, -200)],
        close=True,
        dxfattribs={"layer": "EXIT", "color": 3},
    )
    _add_dim_text(msp, "출입구 W=2000mm", 9000, -300, height=150)

    # ── 스케일 / 제목 블록 ────────────────────────────────
    msp.add_text("SCALE 1:50", dxfattribs={"insert": (0, -1500), "height": 300, "layer": "TEXT", "color": 3})
    msp.add_text("SIZE 20m x 15m (L자형 244sqm)", dxfattribs={"insert": (0, -2000), "height": 250, "layer": "TEXT", "color": 3})
    msp.add_text("BuildUp AI Sample — FP-003", dxfattribs={"insert": (0, -2500), "height": 200, "layer": "TEXT", "color": 3})

    doc.saveas(output_path)
    print(f"[생성 완료] {output_path}")
    print(f"  방 외곽: L자형 20000×15000mm")
    print(f"  설비: SP×3, 비상구×1, 출입구×1")
    print(f"  SCALE 1:50")


def create_rect_dxf(output_path: str = "sample_floor_rect.dxf"):
    """
    12m × 9m 직사각형 도면 생성 (SCALE 1:100)
    """
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    doc.layers.add("WALL",       color=7)
    doc.layers.add("DIMENSIONS", color=1)
    doc.layers.add("EQUIPMENT",  color=1)
    doc.layers.add("TEXT",       color=3)
    doc.layers.add("EXIT",       color=3)

    msp = doc.modelspace()

    W, H = 12000, 9000

    # 방 외곽
    msp.add_lwpolyline(
        [(0,0), (W,0), (W,H), (0,H), (0,0)],
        close=True,
        dxfattribs={"layer": "WALL", "lineweight": 50, "color": 7},
    )

    # 치수 레이블
    msp.add_line((0, -500), (W, -500), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 0), (0, -600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((W, 0), (W, -600), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "12000mm (전체 폭)", 4000, -900)

    msp.add_line((-500, 0), (-500, H), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, 0), (-600, 0), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    msp.add_line((0, H), (-600, H), dxfattribs={"layer": "DIMENSIONS", "color": 1})
    _add_dim_text(msp, "9000mm (전체 높이)", -2500, 4000)

    # 스프링클러
    for sx, sy in [(3000, 3000), (6000, 3000), (9000, 3000),
                   (3000, 6500), (6000, 6500), (9000, 6500)]:
        _add_equipment(msp, "SP", sx, sy)

    # 출입구 (하단 중앙)
    msp.add_lwpolyline(
        [(5100, 0), (6900, 0), (6900, -200), (5100, -200)],
        close=True,
        dxfattribs={"layer": "EXIT", "color": 3},
    )
    _add_dim_text(msp, "출입구 W=1800mm", 5100, -350, height=150)

    # 비상구 (우측)
    msp.add_lwpolyline(
        [(W, 3500), (W+200, 3500), (W+200, 4500), (W, 4500)],
        close=True,
        dxfattribs={"layer": "EXIT", "color": 3},
    )
    _add_dim_text(msp, "비상구\nW=900mm", W+250, 3900, height=150)

    # 스케일 / 제목 블록
    msp.add_text("SCALE 1:100", dxfattribs={"insert": (0, -1500), "height": 300, "layer": "TEXT", "color": 3})
    msp.add_text("SIZE 12m x 9m (108sqm)", dxfattribs={"insert": (0, -2000), "height": 250, "layer": "TEXT", "color": 3})
    msp.add_text("BuildUp AI Sample — FP-004", dxfattribs={"insert": (0, -2500), "height": 200, "layer": "TEXT", "color": 3})

    doc.saveas(output_path)
    print(f"[생성 완료] {output_path}")
    print(f"  방 외곽: 직사각형 12000×9000mm")
    print(f"  설비: SP×6, 출입구×1, 비상구×1")
    print(f"  SCALE 1:100")


if __name__ == "__main__":
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))
    create_lshape_dxf(os.path.join(out_dir, "sample_floor_Lshape.dxf"))
    create_rect_dxf(os.path.join(out_dir, "sample_floor_rect.dxf"))
    print("\n두 파일 모두 생성되었습니다.")
    print("BuildUp에 업로드하거나 AutoCAD/FreeCAD에서 열 수 있습니다.")
