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
  selectedIndex?: number | null;
  selectedWallId?: string | null;
  onObjectClick?: (index: number | null) => void;
  onWallClick?: (id: string | null) => void;
  onObjectMove?: (index: number, x: number, z: number) => void;
  onWallMove?: (id: string, x: number, z: number) => void;
  onObjectRotate?: (index: number, deltaDeg: number) => void;
}

const OBJECT_COLORS: Record<string, number> = {
  character_bbox: 0x6366f1, photo_zone: 0x10b981,
  shelf_rental: 0xec4899, banner_stand: 0xf59e0b, product_display: 0x3b82f6,
};
const SELECTED_COLOR = 0xffd700;
const WALL_COLOR     = 0x94a3b8;

const ThreeViewer: React.FC<ThreeViewerProps> = ({
  roomPolygon, placedObjects, detectedObjects = [], walls = [],
  floorPlanUrl, roomBboxPx, imageSizePx,
  selectedIndex, selectedWallId,
  onObjectClick, onWallClick, onObjectMove, onWallMove, onObjectRotate,
}) => {
  const mountRef      = useRef<HTMLDivElement>(null);
  const placedMeshes  = useRef<THREE.Mesh[]>([]);
  const wallMeshes    = useRef<THREE.Mesh[]>([]);

  // Callback refs (always current in event closures)
  const cbObjectClick    = useRef(onObjectClick);
  const cbWallClick      = useRef(onWallClick);
  const cbObjectMove     = useRef(onObjectMove);
  const cbWallMove       = useRef(onWallMove);
  const cbObjectRotate   = useRef(onObjectRotate);
  const selectedIndexRef  = useRef(selectedIndex);
  const selectedWallIdRef = useRef(selectedWallId);
  useEffect(() => { cbObjectClick.current     = onObjectClick;  }, [onObjectClick]);
  useEffect(() => { cbWallClick.current       = onWallClick;    }, [onWallClick]);
  useEffect(() => { cbObjectMove.current      = onObjectMove;   }, [onObjectMove]);
  useEffect(() => { cbWallMove.current        = onWallMove;     }, [onWallMove]);
  useEffect(() => { cbObjectRotate.current    = onObjectRotate; }, [onObjectRotate]);
  useEffect(() => { selectedIndexRef.current  = selectedIndex;  }, [selectedIndex]);
  useEffect(() => { selectedWallIdRef.current = selectedWallId; }, [selectedWallId]);

  // ── Highlight: placed objects ──
  useEffect(() => {
    placedMeshes.current.forEach((mesh, i) => {
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.color.setHex(i === selectedIndex ? SELECTED_COLOR : (OBJECT_COLORS[placedObjects[i]?.object_type] ?? 0xec4899));
      mat.emissive.setHex(i === selectedIndex ? 0x554400 : 0x000000);
      mat.needsUpdate = true;
    });
  }, [selectedIndex, placedObjects]);

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

    // ── AI 배치 오브젝트 ──
    placedObjects.forEach((obj, i) => {
      const [w,d] = obj.bbox_mm, h = obj.height_mm ?? 1500;
      const geo = new THREE.BoxGeometry(w,h,d);
      const isSel = i === selectedIndex;
      const mat = new THREE.MeshStandardMaterial({
        color: isSel ? SELECTED_COLOR : (OBJECT_COLORS[obj.object_type] ?? 0xec4899),
        emissive: isSel ? 0x554400 : 0x000000,
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
    let dragMesh: THREE.Mesh | null = null;
    let dragType: 'placed' | 'wall' | null = null;
    let dragIndex     = -1;
    let dragWallId    = '';
    let dragOffset    = { x: 0, z: 0 };
    let downPos       = { x: 0, y: 0 };

    const getMouseNDC = (e: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
      mouse.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;
    };

    const findDraggable = (e: PointerEvent): THREE.Mesh | null => {
      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      const all = [...placedMeshes.current, ...wallMeshes.current];
      const hits = raycaster.intersectObjects(all, true);
      if (!hits.length) return null;
      let obj: THREE.Object3D | null = hits[0].object;
      while (obj && !obj.userData.type) obj = obj.parent;
      return obj as THREE.Mesh ?? null;
    };

    const onPointerDown = (e: PointerEvent) => {
      downPos = { x: e.clientX, y: e.clientY };
      const mesh = findDraggable(e);
      if (!mesh) return;

      // 가벽은 선택된 것만 드래그 가능 — 겹쳐있을 때 의도치 않은 이동 방지
      if (mesh.userData.type === 'wall' && mesh.userData.wallId !== selectedWallIdRef.current) {
        // 드래그 시작하지 않음 — pointerup에서 클릭으로 처리
        downPos = { x: e.clientX, y: e.clientY };
        return;
      }

      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      raycaster.ray.intersectPlane(floorPlane, hitPoint);

      isDragging = true;
      dragMesh   = mesh;
      dragType   = mesh.userData.type;
      if (dragType === 'placed') dragIndex  = mesh.userData.placedIndex;
      if (dragType === 'wall')   dragWallId = mesh.userData.wallId;
      dragOffset = { x: hitPoint.x - mesh.position.x, z: hitPoint.z - mesh.position.z };
      controls.enabled = false;
      renderer.domElement.style.cursor = 'grabbing';
      e.stopPropagation();
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!isDragging || !dragMesh) return;
      getMouseNDC(e);
      raycaster.setFromCamera(mouse, camera);
      raycaster.ray.intersectPlane(floorPlane, hitPoint);
      dragMesh.position.x = hitPoint.x - dragOffset.x;
      dragMesh.position.z = hitPoint.z - dragOffset.z;
    };

    const onPointerUp = (e: PointerEvent) => {
      const dx = Math.abs(e.clientX - downPos.x);
      const dy = Math.abs(e.clientY - downPos.y);
      const wasDrag = dx > 5 || dy > 5;

      if (isDragging && dragMesh && wasDrag) {
        // 드래그 끝 → 새 위치 emit
        const nx = dragMesh.position.x, nz = dragMesh.position.z;
        if (dragType === 'placed') cbObjectMove.current?.(dragIndex, nx, nz);
        if (dragType === 'wall')   cbWallMove.current?.(dragWallId, nx, nz);
      } else if (!wasDrag) {
        // 클릭
        const mesh = findDraggable(e);
        if (!mesh) {
          cbObjectClick.current?.(null);
          cbWallClick.current?.(null);
        } else if (mesh.userData.type === 'placed') {
          cbObjectClick.current?.(mesh.userData.placedIndex);
        } else if (mesh.userData.type === 'wall') {
          cbWallClick.current?.(mesh.userData.wallId);
        }
      }

      isDragging = false; dragMesh = null; dragType = null;
      controls.enabled = true;
      renderer.domElement.style.cursor = 'pointer';
    };

    // ── 우클릭 회전 (45°) ──
    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      const idx = selectedIndexRef.current;
      if (idx != null && idx >= 0) {
        cbObjectRotate.current?.(idx, 45);
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
    const animate = () => { animId = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); };
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
