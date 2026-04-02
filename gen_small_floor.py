import os
from PIL import Image, ImageDraw, ImageFont

font_paths = [
    'C:/Windows/Fonts/malgun.ttf',
    'C:/Windows/Fonts/NanumGothic.ttf',
    'C:/Windows/Fonts/gulim.ttc',
]
def load(size):
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

# 실제 공간: 5,800 x 5,600mm = 32.48㎡ = 약 9.8평
# 축척 1:50, 1px = 10mm
SCALE = 0.1
OX, OY = 130, 90
W, H = 950, 820

img = Image.new('RGB', (W, H), '#ffffff')
d = ImageDraw.Draw(img)

f8  = load(8);  f9  = load(9);  f10 = load(10)
f11 = load(11); f12 = load(12); f14 = load(14)
f18 = load(18)

def mmx(v): return int(OX + v * SCALE)
def mmy(v): return int(OY + v * SCALE)

RW = 5800
RD = 5600
WT = 150

# 외벽
d.rectangle([mmx(0), mmy(0), mmx(RW), mmy(RD)], fill='#cccccc', outline='#222222', width=2)
# 내부
d.rectangle([mmx(WT), mmy(WT), mmx(RW-WT), mmy(RD-WT)], fill='#f8f8f4')

# 출입구 (남쪽 중앙 900mm)
door_x = (RW - 900) // 2
d.rectangle([mmx(door_x), mmy(RD-WT), mmx(door_x+900), mmy(RD)], fill='#f8f8f4')
d.line([mmx(door_x), mmy(RD-WT), mmx(door_x), mmy(RD-WT-25)], fill='#444444', width=2)
d.arc([mmx(door_x)-1, mmy(RD-WT-50), mmx(door_x+50), mmy(RD-WT)+1], start=0, end=90, fill='#666666', width=1)
# 출입구 치수
d.line([mmx(door_x), mmy(RD+18), mmx(door_x+900), mmy(RD+18)], fill='#27ae60', width=2)
d.line([mmx(door_x), mmy(RD+12), mmx(door_x), mmy(RD+24)], fill='#27ae60', width=2)
d.line([mmx(door_x+900), mmy(RD+12), mmx(door_x+900), mmy(RD+24)], fill='#27ae60', width=2)
d.text((mmx(door_x+450)-32, mmy(RD+27)), '출입구 W=900mm', font=f10, fill='#27ae60')

# 창문 북쪽 (W=1200mm 중앙)
win_x = (RW - 1200) // 2
d.rectangle([mmx(win_x), mmy(0), mmx(win_x+1200), mmy(WT)], fill='#aaddff', outline='#2980b9', width=1)
d.line([mmx(win_x), mmy(WT//2), mmx(win_x+1200), mmy(WT//2)], fill='#2980b9', width=1)
d.text((mmx(win_x+600)-26, mmy(0)-14), 'W=1,200mm', font=f9, fill='#2980b9')

# 창문 서쪽 (W=800mm)
win_y2 = (RD - 800) // 2
d.rectangle([mmx(0), mmy(win_y2), mmx(WT), mmy(win_y2+800)], fill='#aaddff', outline='#2980b9', width=1)
d.line([mmx(WT//2), mmy(win_y2), mmx(WT//2), mmy(win_y2+800)], fill='#2980b9', width=1)
d.text((mmx(0)-52, mmy(win_y2+400)-6), 'W=800', font=f8, fill='#2980b9')

# 비상구 동쪽 (W=800mm)
ex_y = (RD - 800) // 2
d.rectangle([mmx(RW-WT), mmy(ex_y), mmx(RW), mmy(ex_y+800)], fill='#f8f8f4')
d.rectangle([mmx(RW-WT+8), mmy(ex_y+8), mmx(RW-8), mmy(ex_y+792)], fill='#22c55e')
d.text((mmx(RW)+5, mmy(ex_y+340)), '비상구', font=f10, fill='#16a34a')
d.text((mmx(RW)+5, mmy(ex_y+360)), 'W=800mm', font=f9, fill='#16a34a')
d.line([mmx(RW+40), mmy(ex_y), mmx(RW+40), mmy(ex_y+800)], fill='#16a34a', width=1)
d.line([mmx(RW), mmy(ex_y), mmx(RW+44), mmy(ex_y)], fill='#16a34a', width=1)
d.line([mmx(RW), mmy(ex_y+800), mmx(RW+44), mmy(ex_y+800)], fill='#16a34a', width=1)
d.text((mmx(RW+42), mmy(ex_y+380)), '800', font=f8, fill='#16a34a')

# 스프링클러 4개 (2x2)
sps = [(1650, 1500), (4150, 1500), (1650, 4000), (4150, 4000)]
for sx, sy in sps:
    cx, cy = mmx(sx), mmy(sy)
    d.ellipse([cx-8, cy-8, cx+8, cy+8], outline='#dc2626', width=2)
    d.ellipse([cx-2, cy-2, cx+2, cy+2], fill='#dc2626')
    for dx2, dy2, ex2, ey2 in [(-8,0,-4,0),(4,0,8,0),(0,-8,0,-4),(0,4,0,8)]:
        d.line([cx+dx2, cy+dy2, cx+ex2, cy+ey2], fill='#dc2626', width=1)

# 스프링클러 간격 치수
d.line([mmx(1650), mmy(75), mmx(4150), mmy(75)], fill='#aaaaaa', width=1)
d.text((mmx(2900)-18, mmy(63)), '2,500mm', font=f8, fill='#888888')
d.line([mmx(680), mmy(1500), mmx(680), mmy(4000)], fill='#aaaaaa', width=1)
d.text((mmx(680)-48, mmy(2750)-6), '2,500mm', font=f8, fill='#888888')

# 치수선 — 가로
dy_dim = RD + 68
d.line([mmx(0), mmy(dy_dim), mmx(RW), mmy(dy_dim)], fill='#dc2626', width=2)
d.polygon([mmx(0), mmy(dy_dim)-3, mmx(0), mmy(dy_dim)+3, mmx(0)-7, mmy(dy_dim)], fill='#dc2626')
d.polygon([mmx(RW), mmy(dy_dim)-3, mmx(RW), mmy(dy_dim)+3, mmx(RW)+7, mmy(dy_dim)], fill='#dc2626')
d.line([mmx(0), mmy(RD), mmx(0), mmy(dy_dim+4)], fill='#dc2626', width=1)
d.line([mmx(RW), mmy(RD), mmx(RW), mmy(dy_dim+4)], fill='#dc2626', width=1)
mid = mmx(RW//2)
d.rectangle([mid-52, mmy(dy_dim)-7, mid+72, mmy(dy_dim)+8], fill='#ffffff')
d.text((mid-50, mmy(dy_dim)-6), '5,800mm  (전체 폭)', font=f11, fill='#dc2626')

# 치수선 — 세로
dx_dim = -68
d.line([mmx(dx_dim), mmy(0), mmx(dx_dim), mmy(RD)], fill='#dc2626', width=2)
d.polygon([mmx(dx_dim)-3, mmy(0), mmx(dx_dim)+3, mmy(0), mmx(dx_dim), mmy(0)-7], fill='#dc2626')
d.polygon([mmx(dx_dim)-3, mmy(RD), mmx(dx_dim)+3, mmy(RD), mmx(dx_dim), mmy(RD)+7], fill='#dc2626')
d.line([mmx(dx_dim-4), mmy(0), mmx(0), mmy(0)], fill='#dc2626', width=1)
d.line([mmx(dx_dim-4), mmy(RD), mmx(0), mmy(RD)], fill='#dc2626', width=1)
tmp = Image.new('RGBA', (128, 14), (255, 255, 255, 0))
td = ImageDraw.Draw(tmp)
td.text((0, 0), '5,600mm  (전체 깊이)', font=f11, fill='#dc2626')
tmp2 = tmp.rotate(90, expand=True)
img.paste(tmp2, (mmx(dx_dim)-18, mmy(RD//2)-64), tmp2)

# 벽 두께 표기
d.line([mmx(0), mmy(WT+8), mmx(WT), mmy(WT+8)], fill='#6366f1', width=1)
d.text((mmx(WT)+4, mmy(WT+2)), '벽체 t=150mm', font=f8, fill='#6366f1')

# 천장고 노트
nx1, ny1 = mmx(3100), mmy(3350)
nx2, ny2 = mmx(5550), mmy(4050)
d.rectangle([nx1, ny1, nx2, ny2], fill='#fffde7', outline='#f59e0b', width=1)
d.text((nx1+8, ny1+8), '※ 천장고 (Ceiling Height)', font=f10, fill='#d97706')
d.text((nx1+8, ny1+26), '   마감 천장  H = 2,700mm', font=f10, fill='#555555')
d.text((nx1+8, ny1+44), '   구조체 기준 H = 3,000mm', font=f10, fill='#555555')
d.text((nx1+8, ny1+62), '   (소규모 점포 기준)', font=f9, fill='#999999')
d.text((nx1+8, ny1+80), '   바닥면적: 약 32.5㎡ (9.8평)', font=f10, fill='#d97706')
d.text((nx1+8, ny1+98), '   유효 내부폭: 5,500 x 5,300mm', font=f9, fill='#999999')

# 공간 레이블
d.text((mmx(1600), mmy(2200)), 'OPEN FLOOR', font=f18, fill='#d1d5db')
d.text((mmx(1780), mmy(2440)), '소형 팝업스토어 B동', font=f12, fill='#d1d5db')

# 방위
d.polygon([mmx(RW)+55, mmy(55), mmx(RW)+47, mmy(83), mmx(RW)+55, mmy(75), mmx(RW)+63, mmy(83)], fill='#334155')
d.text((mmx(RW)+50, mmy(87)), 'N', font=f12, fill='#334155')

# 타이틀 블록
d.rectangle([0, H-110, W, H], fill='#1e293b')
d.line([0, H-110, W, H-110], fill='#475569', width=2)

d.text((20, H-102), 'PROJECT', font=f8, fill='#94a3b8')
d.text((20, H-88), '소형 팝업스토어 B동 평면도', font=f14, fill='#ffffff')
d.text((20, H-66), 'BuildUp AI Sample  |  도면번호: FP-002', font=f9, fill='#94a3b8')
d.text((20, H-48), '바닥면적: 32.5㎡ (9.8평)  |  5,800x5,600mm  |  벽체두께: 150mm  |  마감천장고: 2,700mm', font=f8, fill='#64748b')

d.text((390, H-102), 'SCALE', font=f8, fill='#94a3b8')
d.text((390, H-88), '1 : 50', font=f14, fill='#ffffff')
d.rectangle([390, H-64, 550, H-54], fill='#475569')
d.rectangle([390, H-64, 470, H-54], fill='#ef4444')
d.text((386, H-46), '0', font=f8, fill='#94a3b8')
d.text((428, H-46), '2,000', font=f8, fill='#94a3b8')
d.text((536, H-46), '4,000mm', font=f8, fill='#94a3b8')

d.text((630, H-102), '범례 / LEGEND', font=f8, fill='#94a3b8')
lcx, lcy = 640, H-86
d.ellipse([lcx-6, lcy-6, lcx+6, lcy+6], outline='#dc2626', width=2)
d.ellipse([lcx-2, lcy-2, lcx+2, lcy+2], fill='#dc2626')
d.text((652, H-92), '스프링클러 @2,500mm', font=f9, fill='#e2e8f0')
d.rectangle([630, H-74, 646, H-64], fill='#aaddff', outline='#2980b9')
d.text((652, H-74), '창문  N:W=1,200 / W:W=800', font=f9, fill='#e2e8f0')
d.rectangle([630, H-56, 646, H-46], fill='#22c55e')
d.text((652, H-56), '비상구  W=800mm', font=f9, fill='#e2e8f0')

out = r'C:/Users/804/Documents/카카오톡 받은 파일/demo_brands2/sample_floor_plan_small.png'
img.save(out, 'PNG')
print('saved:', out)
