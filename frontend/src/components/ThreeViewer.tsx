import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export interface PlacedObject {
  object_type: string;
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number];
  height_mm: number;
  reference_point: string;
  placed_because: string;
}

export interface DetectedObject {
  equipment_type: string;
  position_mm: [number, number];
  size_mm?: [number, number];
}

export interface Wall {
  id: string;
  x: number;
  z: number;
  rotation: number; // degrees
  length: number;
  height: number;
  thickness: number;
}

interface ThreeViewerProps {
  roomPolygon: [number, number][];
  placedObjects: PlacedObject[];
  detectedObjects?: DetectedObject[];
  walls?: Wall[];
  floorPlanUrl?: string;
  roomBboxPx?: [number, number, number, number];
  imageSizePx?: [number, number];
  selectedIndices?: number[];
  selectedWallId?: string | null;
  collidingIndices?: Set<number>;
  onObjectClick?: (index: number | null, shiftKey?: boolean) => void;
  onWallClick?: (id: string | null) => void;
  onObjectMove?: (index: number, x: number, z: number) => void;
  onObjectMoveMulti?: (moves: { index: number; x: number; z: number }[]) => void;
  onWallMove?: (id: string, x: number, z: number) => void;
  onObjectRotate?: (index: number, deltaDeg: number) => void;
}

const OBJECT_COLORS: Record<string, number> = {
  character_bbox: 0x6366f1, photo_zone: 0x10b981,
  shelf_rental: 0xec4899, banner_stand: 0xf59e0b, product_display: 0x3b82f6,
};
const SELECTED_COLOR  = 0xffd700;
const COLLIDE_COLOR   = 0xef4444;
const WALL_COLOR      = 0x94a3b8;

const ThreeViewer: React.FC<ThreeViewerProps> = ({
  roomPolygon, placedObjects, detectedObjects = [], walls = [],
  floorPlanUrl, roomBboxPx, imageSizePx,
  selectedIndices = [], selectedWallId, collidingIndices = new Set(),
  onObjectClick, onWallClick, onObjectMove, onObjectMoveMulti, onWallMove, onObjectRotate,
}) => {
  const mountRef      = useRef<HTMLDivElement>(null);
  const placedMeshes  = useRef<THREE.Mesh[]>([]);
  const wallMeshes    = useRef<THREE.Mesh[]>([]);
  const personGroup   = useRef<THREE.Group | null>(null);
  const personPos     = useRef<{ x: number; z: number } | null>(null);
  // 카메라 상태 ref — 씬 재빌드 시 복원
  const cameraState   = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null);

  // Callback refs (always current in event closures)
  const cbObjectClick    = useRef(onObjectClick);
  const cbWallClick      = useRef(onWallClick);
  const cbObjectMove      = useRef(onObjectMove);
  const cbObjectMoveMulti = useRef(onObjectMoveMulti);
  const cbWallMove       = useRef(onWallMove);
  const cbObjectRotate    = useRef(onObjectRotate);
  const selectedIndicesRef = useRef(selectedIndices);
  const selectedWallIdRef  = useRef(selectedWallId);
  useEffect(() => { cbObjectClick.current      = onObjectClick;   }, [onObjectClick]);
  useEffect(() => { cbWallClick.current        = onWallClick;     }, [onWallClick]);
  useEffect(() => { cbObjectMove.current       = onObjectMove;       }, [onObjectMove]);
  useEffect(() => { cbObjectMoveMulti.current  = onObjectMoveMulti;  }, [onObjectMoveMulti]);
  useEffect(() => { cbWallMove.current         = onWallMove;      }, [onWallMove]);
  useEffect(() => { cbObjectRotate.current     = onObjectRotate;  }, [onObjectRotate]);
  useEffect(() => { selectedIndicesRef.current = selectedIndices; }, [selectedIndices]);
  useEffect(() => { selectedWallIdRef.current  = selectedWallId;  }, [selectedWallId]);

  // ── Highlight: placed objects ──
  useEffect(() => {
    placedMeshes.current.forEach((mesh, i) => {
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const isSel = selectedIndices.includes(i);
      const isCol = collidingIndices.has(i);
      mat.color.setHex(isSel ? SELECTED_COLOR : isCol ? COLLIDE_COLOR : (OBJECT_COLORS[placedObjects[i]?.object_type] ?? 0xec4899));
      mat.emissive.setHex(isSel ? 0x554400 : isCol ? 0x550000 : 0x000000);
      mat.needsUpdate = true;
    });
  }, [selectedIndices, collidingIndices, placedObjects]);

  // ── Highlight: walls ──
  useEffect(() => {
    wallMeshes.current.forEach((mesh) => {
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.color.setHex(mesh.userData.wallId === selectedWallId ? SELECTED_COLOR : WALL_COLOR);
      mat.needsUpdate = true;
    });
  }, [selectedWallId]);

  // ── Main scene ──
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    placedMeshes.current = [];
    wallMeshes.current   = [];

    const xs = roomPolygon.length > 0 ? roomPolygon.map(p => p[0]) : [-10000, 10000];
    const ys = roomPolygon.length > 0 ? roomPolygon.map(p => p[1]) : [-10000, 10000];
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const roomW = maxX - minX, roomD = maxY - minY;
    const cx = (minX + maxX) / 2, cz = (minY + maxY) / 2;

    const scene    = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0f1d);
    const camera   = new THREE.PerspectiveCamera(60, mount.clientWidth / mount.clientHeight, 0.1, 200000);
    const camDist  = Math.max(roomW, roomD) * 1.2;
    camera.position.set(cx, camDist, cz + camDist * 0.6);
    camera.lookAt(cx, 0, cz);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(cx, 0, cz);

    // 이전 씬 재빌드 시 카메라 상태 복원
    if (cameraState.current) {
      camera.position.copy(cameraState.current.pos);
      controls.target.copy(cameraState.current.target);
    }

    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const dir = new THREE.DirectionalLight(0xffffff, 1);
    dir.position.set(cx, camDist, cz);
    scene.add(dir);

    const geos: THREE.BufferGeometry[] = [];
    const mats: THREE.Material[]       = [];

    // ── Floor ──
    if (floorPlanUrl && roomPolygon.length > 0) {
      const pg = new THREE.PlaneGeometry(roomW, roomD);
      const pm = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
      geos.push(pg); mats.push(pm);
      new THREE.TextureLoader().load(floorPlanUrl, (tex) => {
        if (roomBboxPx && imageSizePx) {
          const [bx1,by1,bx2,by2] = roomBboxPx, [iw,ih] = imageSizePx;
          tex.repeat.set((bx2-bx1)/iw, (by2-by1)/ih);
          tex.offset.set(bx1/iw, 1-by2/ih);
          tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
        }
        pm.map = tex; pm.needsUpdate = true;
      });
      const plane = new THREE.Mesh(pg, pm);
      plane.rotation.x = -Math.PI / 2;
      plane.position.set(cx, 0, cz);
      scene.add(plane);
    } else {
      const shape = new THREE.Shape();
      if (roomPolygon.length > 3) {
        shape.moveTo(roomPolygon[0][0], roomPolygon[0][1]);
        roomPolygon.slice(1).forEach(([x,y]) => shape.lineTo(x, y));
        shape.closePath();
      } else {
        shape.moveTo(-10000,-10000); shape.lineTo(10000,-10000);
        shape.lineTo(10000,10000);  shape.lineTo(-10000,10000); shape.closePath();
      }
      const fg = new THREE.ShapeGeometry(shape);
      const fm = new THREE.MeshStandardMaterial({ color: 0x111827, side: THREE.DoubleSide });
      geos.push(fg); mats.push(fm);
      const fl = new THREE.Mesh(fg, fm); fl.rotation.x = -Math.PI / 2; scene.add(fl);
      scene.add(new THREE.GridHelper(40000, 40, 0x1e293b, 0x0f172a));
    }

    // 방 외곽선
    if (roomPolygon.length >= 3) {
      const pts = [...roomPolygon.map(([x,y]) => new THREE.Vector3(x,2,y)), new THREE.Vector3(roomPolygon[0][0],2,roomPolygon[0][1])];
      const lg = new THREE.BufferGeometry().setFromPoints(pts);
      const lm = new THREE.LineBasicMaterial({ color: 0x6366f1, opacity: 0.6, transparent: true });
      geos.push(lg); mats.push(lm);
      scene.add(new THREE.Line(lg, lm));
    }

    // ── 설비 ──
    detectedObjects.forEach((obj) => {
      if (!obj.position_mm) return;
      const [px,pz] = obj.position_mm;
      const type = obj.equipment_type;
      if (type === 'sprinkler') {
        const cg = new THREE.CylinderGeometry(90,90,30,32);
        const cm = new THREE.MeshStandardMaterial({ color: 0xef4444, roughness: 0.4, metalness: 0.3 });
        geos.push(cg); mats.push(cm);
        const cyl = new THREE.Mesh(cg, cm); cyl.position.set(px,15,pz); scene.add(cyl);
        const tg = new THREE.TorusGeometry(95,8,8,32);
        const tm = new THREE.MeshStandardMaterial({ color: 0xff0000 });
        geos.push(tg); mats.push(tm);
        const tor = new THREE.Mesh(tg, tm); tor.rotation.x = Math.PI/2; tor.position.set(px,20,pz); scene.add(tor);
      } else if (type === 'exit' || type === 'emergency_exit') {
        const w = obj.size_mm?.[0] ?? 350, d = obj.size_mm?.[1] ?? 350;
        const eg = new THREE.BoxGeometry(w,60,d);
        const em = new THREE.MeshStandardMaterial({ color: 0x22c55e, transparent: true, opacity: 0.9 });
        geos.push(eg); mats.push(em);
        const ex = new THREE.Mesh(eg, em); ex.position.set(px,30,pz);
        ex.add(new THREE.LineSegments(new THREE.EdgesGeometry(eg), new THREE.LineBasicMaterial({ color: 0x4ade80 })));
        scene.add(ex);
      } else {
        const bg = new THREE.BoxGeometry(300,200,300);
        const bm = new THREE.MeshStandardMaterial({ color: 0x64748b, transparent: true, opacity: 0.5 });
        geos.push(bg); mats.push(bm);
        const bx = new THREE.Mesh(bg, bm); bx.position.set(px,100,pz); scene.add(bx);
      }
    });

    // ── 사람 모형 (스케일 레퍼런스, 드래그 가능) ──
    {
      // 이전 드래그 위치가 있으면 유지, 없으면 입구 기준 초기 배치
      let personX: number, personZ: number;
      if (personPos.current) {
        personX = personPos.current.x;
        personZ = personPos.current.z;
      } else {
        personX = cx; personZ = maxY - 1200;
        const entrance = detectedObjects.find(o =>
          o.equipment_type === 'exit' || o.equipment_type === 'emergency_exit'
        );
        if (entrance?.position_mm) {
          const [ex, ez] = entrance.position_mm;
          const dx = cx - ex, dz = cz - ez;
          const dist = Math.sqrt(dx * dx + dz * dz) || 1;
          personX = ex + (dx / dist) * 1200;
          personZ = ez + (dz / dist) * 1200;
        }
        personPos.current = { x: personX, z: personZ };
      }

      const BODY_H = 1500, HEAD_R = 150, BODY_W = 450, BODY_D = 300;
      const personColor = 0xe2e8f0;

      const group = new THREE.Group();
      group.position.set(personX, 0, personZ);
      group.userData = { type: 'person' };

      // 몸통
      const bodyGeo = new THREE.BoxGeometry(BODY_W, BODY_H, BODY_D);
      const bodyMat = new THREE.MeshStandardMaterial({
        color: personColor, transparent: true, opacity: 0.55, roughness: 0.5,
      });
      geos.push(bodyGeo); mats.push(bodyMat);
      const bodyMesh = new THREE.Mesh(bodyGeo, bodyMat);
      bodyMesh.position.set(0, BODY_H / 2, 0);
      bodyMesh.userData = { type: 'person' };
      bodyMesh.add(new THREE.LineSegments(
        new THREE.EdgesGeometry(bodyGeo),
        new THREE.LineBasicMaterial({ color: 0x94a3b8, opacity: 0.8, transparent: true })
      ));
      group.add(bodyMesh);

      // 머리
      const headGeo = new THREE.SphereGeometry(HEAD_R, 16, 16);
      const headMat = new THREE.MeshStandardMaterial({
        color: personColor, transparent: true, opacity: 0.55, roughness: 0.5,
      });
      geos.push(headGeo); mats.push(headMat);
      const headMesh = new THREE.Mesh(headGeo, headMat);
      headMesh.position.set(0, BODY_H + HEAD_R, 0);
      headMesh.userData = { type: 'person' };
      group.add(headMesh);

      // 키 표시선
      const lineGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(BODY_W, 0, 0),
        new THREE.Vector3(BODY_W, BODY_H + HEAD_R * 2, 0),
      ]);
      const lineMat = new THREE.LineBasicMaterial({ color: 0x94a3b8, opacity: 0.6, transparent: true });
      geos.push(lineGeo); mats.push(lineMat);
      group.add(new THREE.Line(lineGeo, lineMat));

      scene.add(group);
      personGroup.current = group;
    }

    // ── AI 배치 오브젝트 ──
    placedObjects.forEach((obj, i) => {
      const [w,d] = obj.bbox_mm, h = obj.height_mm ?? 1500;
      const geo = new THREE.BoxGeometry(w,h,d);
      const isSel = selectedIndices.includes(i);
      const isCol = collidingIndices.has(i);
      const mat = new THREE.MeshStandardMaterial({
        color: isSel ? SELECTED_COLOR : isCol ? COLLIDE_COLOR : (OBJECT_COLORS[obj.object_type] ?? 0xec4899),
        emissive: isSel ? 0x554400 : isCol ? 0x550000 : 0x000000,
        transparent: true, opacity: 0.85, roughness: 0.3, metalness: 0.4,
      });
      geos.push(geo); mats.push(mat);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(obj.position_mm[0], h/2, obj.position_mm[1]);
      mesh.rotation.y = THREE.MathUtils.degToRad(obj.rotation_deg);
      mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0xffffff })));
      mesh.userData = { type: 'placed', placedIndex: i };
      scene.add(mesh);
      placedMeshes.current.push(mesh);
    });

    // ── 가벽 ──
    walls.forEach((wall) => {
      const geo = new THREE.BoxGeometry(wall.length, wall.height, wall.thickness);
      const isSel = wall.id === selectedWallId;
      const mat = new THREE.MeshStandardMaterial({
        color: isSel ? SELECTED_COLOR : WALL_COLOR,
        roughness: 0.8, transparent: true, opacity: 0.9,
      });
      geos.push(geo); mats.push(mat);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(wall.x, wall.height/2, wall.z);
      mesh.rotation.y = THREE.MathUtils.degToRad(wall.rotation);
      mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0xe2e8f0 })));
      mesh.userData = { type: 'wall', wallId: wall.id };
      scene.add(mesh);
      wallMeshes.current.push(mesh);
    });

    // ── 드래그 + 클릭 ──
    const raycaster    = new THREE.Raycaster();
    const mouse        = new THREE.Vector2();
    const floorPlane   = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
    const hitPoint     = new THREE.Vector3();

    let isDragging    = false;
    let dragType: 'placed' | 'wall' | 'person' | null = null;
    let dragMesh: THREE.Mesh | null = null;       // placed/wall
    let dragPersonGrp: THREE.Group | null = null; // person
    let dragIndex     = -1;
    let dragWallId    = '';
    let dragOffset    = { x: 0, z: 0 };
    let downPos       = { x: 0, y: 0 };

    const getMouseNDC = (e: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
      mouse.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;
    };

    // 클릭/드래그 가능한 mesh 탐색 (person 포함)
    const findDraggable = (e: PointerEvent): THREE.Object3D | null => {
      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      const targets = [
        ...placedMeshes.current,
        ...wallMeshes.current,
        ...(personGroup.current ? personGroup.current.children.filter(c => c instanceof THREE.Mesh) as THREE.Mesh[] : []),
      ];
      const hits = raycaster.intersectObjects(targets, true);
      if (!hits.length) return null;
      let obj: THREE.Object3D | null = hits[0].object;
      while (obj && !obj.userData.type) obj = obj.parent;
      return obj ?? null;
    };

    const onPointerDown = (e: PointerEvent) => {
      downPos = { x: e.clientX, y: e.clientY };
      const found = findDraggable(e);
      if (!found) return;

      // 가벽은 선택된 것만 드래그
      if (found.userData.type === 'wall' && found.userData.wallId !== selectedWallIdRef.current) return;

      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      raycaster.ray.intersectPlane(floorPlane, hitPoint);

      isDragging = true;
      dragType   = found.userData.type;
      controls.enabled = false;
      renderer.domElement.style.cursor = 'grabbing';

      if (dragType === 'person' && personGroup.current) {
        dragPersonGrp = personGroup.current;
        dragOffset = { x: hitPoint.x - dragPersonGrp.position.x, z: hitPoint.z - dragPersonGrp.position.z };
      } else {
        dragMesh = found as THREE.Mesh;
        if (dragType === 'placed') dragIndex  = found.userData.placedIndex;
        if (dragType === 'wall')   dragWallId = found.userData.wallId;
        dragOffset = { x: hitPoint.x - dragMesh.position.x, z: hitPoint.z - dragMesh.position.z };
        // delta 추적 초기값 설정
        prevDragX = dragMesh.position.x;
        prevDragZ = dragMesh.position.z;
      }
      e.stopPropagation();
    };

    // 드래그 중 다중 선택 mesh 추적용
    let prevDragX = 0, prevDragZ = 0;

    const onPointerMove = (e: PointerEvent) => {
      if (!isDragging) return;
      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      raycaster.ray.intersectPlane(floorPlane, hitPoint);

      if (dragType === 'person' && dragPersonGrp) {
        dragPersonGrp.position.x = hitPoint.x - dragOffset.x;
        dragPersonGrp.position.z = hitPoint.z - dragOffset.z;
      } else if (dragMesh) {
        const newX = hitPoint.x - dragOffset.x;
        const newZ = hitPoint.z - dragOffset.z;
        const ddx = newX - prevDragX;
        const ddz = newZ - prevDragZ;
        prevDragX = newX;
        prevDragZ = newZ;

        const indices = selectedIndicesRef.current;
        if (indices.length > 1 && indices.includes(dragIndex)) {
          // 다중 선택: 드래그 mesh 포함 선택된 모든 mesh를 delta만큼 같이 이동
          indices.forEach(idx => {
            const m = placedMeshes.current[idx];
            if (m) { m.position.x += ddx; m.position.z += ddz; }
          });
        } else {
          dragMesh.position.x = newX;
          dragMesh.position.z = newZ;
        }
      }
    };

    const onPointerUp = (e: PointerEvent) => {
      const dx = Math.abs(e.clientX - downPos.x);
      const dy = Math.abs(e.clientY - downPos.y);
      const wasDrag = dx > 5 || dy > 5;

      if (isDragging && wasDrag) {
        if (dragType === 'person' && dragPersonGrp) {
          // 사람 위치 ref에 저장 — 씬 재빌드 시 유지
          personPos.current = { x: dragPersonGrp.position.x, z: dragPersonGrp.position.z };
        } else if (dragMesh) {
          if (dragType === 'placed') {
            const indices = selectedIndicesRef.current;
            if (indices.length > 1 && indices.includes(dragIndex)) {
              // 다중 선택: 한 번에 emit → App에서 pushHistory 1회만 실행
              const moves = indices.map(idx => {
                const m = placedMeshes.current[idx];
                return { index: idx, x: m?.position.x ?? 0, z: m?.position.z ?? 0 };
              });
              cbObjectMoveMulti.current?.(moves);
            } else {
              cbObjectMove.current?.(dragIndex, dragMesh.position.x, dragMesh.position.z);
            }
          }
          if (dragType === 'wall') cbWallMove.current?.(dragWallId, dragMesh.position.x, dragMesh.position.z);
        }
      } else if (!wasDrag) {
        const found = findDraggable(e);
        if (!found) {
          cbObjectClick.current?.(null);
          cbWallClick.current?.(null);
        } else if (found.userData.type === 'placed') {
          cbObjectClick.current?.(found.userData.placedIndex, e.shiftKey);
        } else if (found.userData.type === 'wall') {
          cbWallClick.current?.(found.userData.wallId);
        }
        // person 클릭은 별도 동작 없음
      }

      isDragging = false; dragMesh = null; dragPersonGrp = null; dragType = null;
      controls.enabled = true;
      renderer.domElement.style.cursor = 'pointer';
    };

    // ── 우클릭 회전 (45°) ──
    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      const indices = selectedIndicesRef.current;
      if (indices.length > 0) {
        indices.forEach(idx => cbObjectRotate.current?.(idx, 45));
      }
    };

    renderer.domElement.style.cursor = 'pointer';
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup',   onPointerUp);
    renderer.domElement.addEventListener('contextmenu', onContextMenu);

    // ── Resize ──
    const onResize = () => {
      if (!mount) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener('resize', onResize);

    // ── Animate ──
    let animId = 0;
    const animate = () => {
      animId = requestAnimationFrame(animate);
      controls.update();
      // 카메라 상태 지속 저장 — 씬 재빌드 시 복원용
      cameraState.current = {
        pos: camera.position.clone(),
        target: controls.target.clone(),
      };
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup',   onPointerUp);
      renderer.domElement.removeEventListener('contextmenu', onContextMenu);
      cancelAnimationFrame(animId);
      mount.removeChild(renderer.domElement);
      geos.forEach(g => g.dispose());
      mats.forEach(m => m.dispose());
      renderer.dispose();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomPolygon, placedObjects, detectedObjects, walls, floorPlanUrl, roomBboxPx, imageSizePx]);

  return <div ref={mountRef} className="w-full h-full" style={{ cursor: 'pointer' }} />;
};

export default ThreeViewer;
