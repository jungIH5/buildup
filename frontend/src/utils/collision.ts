import type { Wall } from '../components/ThreeViewer';

interface PlacedObject {
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number];
}

type Vec2 = [number, number];

// 회전된 직사각형의 4개 코너 계산 (mm 좌표)
function getObjectCorners(obj: PlacedObject): Vec2[] {
  const [cx, cy] = obj.position_mm;
  const [w, d] = obj.bbox_mm;
  const angle = (obj.rotation_deg * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);

  const local: Vec2[] = [
    [-w / 2, -d / 2],
    [ w / 2, -d / 2],
    [ w / 2,  d / 2],
    [-w / 2,  d / 2],
  ];

  return local.map(([lx, ly]): Vec2 => [
    cx + lx * cos - ly * sin,
    cy + lx * sin + ly * cos,
  ]);
}

// 가벽의 4개 코너 계산 (ThreeViewer Wall: x/z 좌표, mm 단위)
function getWallCorners(wall: Wall): Vec2[] {
  const cx = wall.x;
  const cy = wall.z;
  const w = wall.length;
  const d = wall.thickness;
  const angle = (wall.rotation * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);

  const local: Vec2[] = [
    [-w / 2, -d / 2],
    [ w / 2, -d / 2],
    [ w / 2,  d / 2],
    [-w / 2,  d / 2],
  ];

  return local.map(([lx, ly]): Vec2 => [
    cx + lx * cos - ly * sin,
    cy + lx * sin + ly * cos,
  ]);
}

// 폴리곤의 분리축(SAT 투영축) 생성
function getAxes(corners: Vec2[]): Vec2[] {
  const axes: Vec2[] = [];
  for (let i = 0; i < corners.length; i++) {
    const a = corners[i];
    const b = corners[(i + 1) % corners.length];
    const edge: Vec2 = [b[0] - a[0], b[1] - a[1]];
    // 법선 벡터 (수직)
    const len = Math.sqrt(edge[0] ** 2 + edge[1] ** 2) || 1;
    axes.push([-edge[1] / len, edge[0] / len]);
  }
  return axes;
}

// 폴리곤을 축에 투영
function project(corners: Vec2[], axis: Vec2): { min: number; max: number } {
  const dots = corners.map(([x, y]) => x * axis[0] + y * axis[1]);
  return { min: Math.min(...dots), max: Math.max(...dots) };
}

// SAT 기반 두 볼록 다각형 충돌 검사
function satOverlap(cornersA: Vec2[], cornersB: Vec2[]): boolean {
  const axes = [...getAxes(cornersA), ...getAxes(cornersB)];
  for (const axis of axes) {
    const a = project(cornersA, axis);
    const b = project(cornersB, axis);
    if (a.max < b.min || b.max < a.min) return false; // 분리축 발견 = 충돌 없음
  }
  return true; // 모든 축에서 겹침 = 충돌
}

/**
 * 배치된 오브젝트들 간, 그리고 가벽과의 충돌을 검사합니다.
 * @returns 충돌 중인 오브젝트 인덱스 Set
 */
export function checkCollisions(
  placed: PlacedObject[],
  walls: Wall[],
): Set<number> {
  const colliding = new Set<number>();

  const placedCorners = placed.map(getObjectCorners);
  const wallCorners   = walls.map(getWallCorners);

  // 오브젝트 ↔ 오브젝트
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      if (satOverlap(placedCorners[i], placedCorners[j])) {
        colliding.add(i);
        colliding.add(j);
      }
    }
  }

  // 오브젝트 ↔ 가벽
  for (let i = 0; i < placed.length; i++) {
    for (const wc of wallCorners) {
      if (satOverlap(placedCorners[i], wc)) {
        colliding.add(i);
      }
    }
  }

  return colliding;
}
