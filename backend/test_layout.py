import sys
sys.path.insert(0, '.')
from core.schemas import (
    BrandStandards, FloorAnalysis, ReferencePoint, Equipment,
    PlacementIntent, LayoutPlan, ConfidenceLevel
)
from core.spatial import compute_layout

# 테스트 공간: 10m x 8m 방
room = [(0,0),(10000,0),(10000,8000),(0,8000)]

standards = BrandStandards(
    clearspace_mm=500,
    main_corridor_min_mm=900,
    wall_clearance_mm=300,
    source='기본값',
    confidence=ConfidenceLevel.MEDIUM,
)

floor = FloorAnalysis(
    room_polygon_mm=room,
    dead_zones_mm=[],
    reference_points=[
        ReferencePoint(name='entrance', position_mm=(5000, 7500), facing='inward'),
        ReferencePoint(name='north_wall_mid', position_mm=(5000, 500), facing='south'),
        ReferencePoint(name='east_wall_mid', position_mm=(9500, 4000), facing='west'),
    ],
    eligible_objects=['character_bbox', 'shelf_rental', 'photo_zone', 'impossible_object'],
    scale_mm_per_px=1.0,
    scale_confidence=ConfidenceLevel.HIGH,
    equipment_detected=[],
)

placements = [
    PlacementIntent(object_type='character_bbox', reference_point='entrance', direction='inward', priority=1, placed_because='입구 캐릭터 배치'),
    PlacementIntent(object_type='shelf_rental', reference_point='north_wall_mid', direction='wall_facing', priority=2, placed_because='북쪽 벽 선반'),
    PlacementIntent(object_type='photo_zone', reference_point='east_wall_mid', direction='inward', priority=3, placed_because='동쪽 포토존'),
    PlacementIntent(object_type='impossible_object', reference_point='entrance', direction='inward', priority=4, placed_because='테스트용 없는 오브젝트'),
]

furniture_sizes = {
    'character_bbox': (800, 800),
    'shelf_rental': (600, 400),
    'photo_zone': (1500, 1200),
    # impossible_object 없음 -> 실패 처리 확인
}

result = compute_layout(floor, standards, placements, furniture_sizes)

print('=== 배치 결과 ===')
print(f'성공: {len(result.placed)}개')
for p in result.placed:
    print(f'  [OK] {p.object_type} @ {p.position_mm} ({p.reference_point})')

print(f'실패: {len(result.failed)}개')
for f in result.failed:
    print(f'  [FAIL] {f["object_type"]}: {f["reason"]}')

print(f'Violations: {len(result.violations)}개')
print(f'GLB 차단: {result.glb_blocked}')
