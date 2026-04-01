import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

interface PlacedObject {
  object_type: string;
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number];
  reference_point: string;
}

interface DetectedObject {
  equipment_type: string;
  position_mm: [number, number];
  size_mm?: [number, number];
}

interface ThreeViewerProps {
  roomPolygon: [number, number][];
  placedObjects: PlacedObject[];
  detectedObjects?: DetectedObject[];
  floorPlanUrl?: string;
  roomBboxPx?: [number, number, number, number]; // [x1, y1, x2, y2] in image pixels
  imageSizePx?: [number, number];               // [width, height] in pixels
}

const ThreeViewer: React.FC<ThreeViewerProps> = ({ roomPolygon, placedObjects, detectedObjects = [], floorPlanUrl, roomBboxPx, imageSizePx }) => {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const currentMount = mountRef.current;
    if (!currentMount) return;

    // --- Room bounding box ---
    const xs = roomPolygon.length > 0 ? roomPolygon.map(p => p[0]) : [-10000, 10000];
    const ys = roomPolygon.length > 0 ? roomPolygon.map(p => p[1]) : [-10000, 10000];
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const roomW = maxX - minX;
    const roomD = maxY - minY;
    const centerX = (minX + maxX) / 2;
    const centerZ = (minY + maxY) / 2;

    // --- Scene Setup ---
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0f1d);

    const camera = new THREE.PerspectiveCamera(
      60,
      currentMount.clientWidth / currentMount.clientHeight,
      0.1,
      200000
    );
    const camDist = Math.max(roomW, roomD) * 1.2;
    camera.position.set(centerX, camDist, centerZ + camDist * 0.6);
    camera.lookAt(centerX, 0, centerZ);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(currentMount.clientWidth, currentMount.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    currentMount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(centerX, 0, centerZ);

    // --- Lights ---
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.9);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
    dirLight.position.set(centerX, camDist, centerZ);
    scene.add(dirLight);

    const geometries: THREE.BufferGeometry[] = [];
    const materials: THREE.Material[] = [];

    // --- Floor (Room) ---
    if (floorPlanUrl && roomPolygon.length > 0) {
      // 도면 이미지를 바닥 텍스처로 사용
      const planeGeo = new THREE.PlaneGeometry(roomW, roomD);
      const planeMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
      geometries.push(planeGeo);
      materials.push(planeMat);

      new THREE.TextureLoader().load(floorPlanUrl, (texture) => {
        // roomBboxPx + imageSizePx가 있으면 방 영역만 잘라서 표시
        if (roomBboxPx && imageSizePx) {
          const [bx1, by1, bx2, by2] = roomBboxPx;
          const [iw, ih] = imageSizePx;
          texture.repeat.set((bx2 - bx1) / iw, (by2 - by1) / ih);
          // Three.js UV Y축은 하단이 0이므로 반전
          texture.offset.set(bx1 / iw, 1 - by2 / ih);
          texture.wrapS = THREE.ClampToEdgeWrapping;
          texture.wrapT = THREE.ClampToEdgeWrapping;
        }
        planeMat.map = texture;
        planeMat.needsUpdate = true;
      });

      const plane = new THREE.Mesh(planeGeo, planeMat);
      plane.rotation.x = -Math.PI / 2;
      plane.position.set(centerX, 0, centerZ);
      scene.add(plane);

      // 방 외곽선 (도면 위에 겹쳐서 경계 강조)
      const outlineShape = new THREE.Shape();
      outlineShape.moveTo(roomPolygon[0][0], roomPolygon[0][1]);
      for (let i = 1; i < roomPolygon.length; i++) {
        outlineShape.lineTo(roomPolygon[i][0], roomPolygon[i][1]);
      }
      outlineShape.closePath();
      const outlineGeo = new THREE.ShapeGeometry(outlineShape);
      const outlineMat = new THREE.MeshBasicMaterial({ color: 0x6366f1, wireframe: true, opacity: 0.3, transparent: true });
      geometries.push(outlineGeo);
      materials.push(outlineMat);
      const outlineMesh = new THREE.Mesh(outlineGeo, outlineMat);
      outlineMesh.rotation.x = -Math.PI / 2;
      outlineMesh.position.y = 1;
      scene.add(outlineMesh);
    } else {
      // 도면 없으면 기존 어두운 바닥
      const shape = new THREE.Shape();
      if (roomPolygon.length > 3) {
        shape.moveTo(roomPolygon[0][0], roomPolygon[0][1]);
        for (let i = 1; i < roomPolygon.length; i++) {
          shape.lineTo(roomPolygon[i][0], roomPolygon[i][1]);
        }
        shape.closePath();
      } else {
        shape.moveTo(-10000, -10000);
        shape.lineTo(10000, -10000);
        shape.lineTo(10000, 10000);
        shape.lineTo(-10000, 10000);
        shape.closePath();
      }
      const floorGeo = new THREE.ShapeGeometry(shape);
      const floorMat = new THREE.MeshStandardMaterial({ color: 0x111827, side: THREE.DoubleSide, roughness: 0.9 });
      geometries.push(floorGeo);
      materials.push(floorMat);
      const floor = new THREE.Mesh(floorGeo, floorMat);
      floor.rotation.x = -Math.PI / 2;
      scene.add(floor);

      const gridHelper = new THREE.GridHelper(40000, 40, 0x1e293b, 0x0f172a);
      gridHelper.position.y = -2;
      scene.add(gridHelper);
    }

    // --- 1. Render Detected Existing Objects (Gray) ---
    detectedObjects.forEach((obj) => {
      const { equipment_type, position_mm, size_mm } = obj;
      if (!position_mm) return;
      
      const width = size_mm ? size_mm[0] : 1000;
      const depth = size_mm ? size_mm[1] : 1000;
      const height = equipment_type === 'desk' ? 750 : 500; 

      const geometry = new THREE.BoxGeometry(width, height, depth);
      geometries.push(geometry);
      
      const material = new THREE.MeshStandardMaterial({ 
        color: 0x64748b, 
        transparent: true,
        opacity: 0.5,
        roughness: 0.5
      });
      materials.push(material);

      const box = new THREE.Mesh(geometry, material);
      box.position.set(position_mm[0], height/2, position_mm[1]);
      scene.add(box);

      const edges = new THREE.EdgesGeometry(geometry);
      const line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x94a3b8 }));
      box.add(line);
    });

    // --- 2. Render AI Placed Objects (Vibrant Colors) ---
    placedObjects.forEach((obj) => {
      const { object_type, position_mm, rotation_deg, bbox_mm } = obj;
      const [width, depth] = bbox_mm;
      const height = 1500; 

      const geometry = new THREE.BoxGeometry(width, height, depth);
      geometries.push(geometry);

      const color = object_type.includes('character') ? 0x6366f1 : 
                    object_type.includes('photo') ? 0x10b981 : 0xec4899;
      
      const material = new THREE.MeshStandardMaterial({ 
        color: color,
        transparent: true,
        opacity: 0.8,
        roughness: 0.3,
        metalness: 0.5
      });
      materials.push(material);

      const box = new THREE.Mesh(geometry, material);
      box.position.set(position_mm[0], height/2, position_mm[1]);
      box.rotation.y = THREE.MathUtils.degToRad(rotation_deg);
      
      const edges = new THREE.EdgesGeometry(geometry);
      const line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0xffffff }));
      box.add(line);

      scene.add(box);
    });

    const handleResize = () => {
      if (!currentMount) return;
      camera.aspect = currentMount.clientWidth / currentMount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(currentMount.clientWidth, currentMount.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    let animationId = 0;
    const animate = () => {
      animationId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationId);
      currentMount.removeChild(renderer.domElement);
      geometries.forEach(g => g.dispose());
      materials.forEach(m => m.dispose());
      renderer.dispose();
    };
  }, [roomPolygon, placedObjects, detectedObjects, floorPlanUrl, roomBboxPx, imageSizePx]);

  return <div ref={mountRef} className="w-full h-full" />;
};

export default ThreeViewer;
