import React, { useMemo, useRef, useState, useCallback } from 'react';
import type { Wall } from './ThreeViewer';

interface PlacedObject {
  object_type: string;
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number];
  height_mm: number;
  reference_point: string;
  placed_because: string;
}

interface DetectedObject {
  equipment_type: string;
  position_mm: [number, number];
  size_mm?: [number, number];
}

interface UserMarking { equipment_type: string; position_mm: [number, number]; }

export interface ZoneDefinition {
  name: string;
  polygon_mm: [number, number][];
  label: string;
  allowed_objects: string[] | null;
  source: string;
}

export type EditMode = 'view' | 'mark_exit' | 'mark_sprinkler' | 'draw_zone';

interface FloorView2DProps {
  roomPolygon: [number, number][];
  placedObjects: PlacedObject[];
  detectedObjects?: DetectedObject[];
  walls?: Wall[];
  selectedIndices?: number[];
  collidingIndices?: Set<number>;
  onObjectClick?: (index: number | null, shiftKey?: boolean) => void;
  onObjectRotate?: (index: number, deltaDeg: number) => void;
  userMarkings?: UserMarking[];
  onMapClick?: (x: number, y: number) => void;
  deadZones?: [number, number][][];
  zones?: ZoneDefinition[];
  editMode?: EditMode;
  selectedZoneName?: string | null;
  onZoneSelect?: (name: string | null) => void;
  onZoneDrag?: (name: string, newPolygon: [number, number][]) => void;
  onZoneDraw?: (polygon_mm: [number, number][]) => void;
  draggingObjectType?: string | null;
  onObjectDrop?: (objectType: string, x_mm: number, y_mm: number) => void;
}

// 오브젝트 크기 (mm) — 백엔드 FURNITURE_SIZES와 동일
const FURNITURE_SIZES_MM: Record<string, [number, number]> = {
  character_bbox:  [800, 800],
  shelf_rental:    [600, 400],
  photo_zone:      [1500, 1200],
  banner_stand:    [600, 200],
  product_display: [900, 600],
};

const OBJECT_COLORS: Record<string, string> = {
  character_bbox:  '#6366f1',
  photo_zone:      '#10b981',
  shelf_rental:    '#ec4899',
  banner_stand:    '#f59e0b',
  product_display: '#3b82f6',
};

const OBJECT_NAMES: Record<string, string> = {
  character_bbox:  '캐릭터',
  shelf_rental:    '선반',
  photo_zone:      '포토존',
  banner_stand:    '배너',
  product_display: '진열대',
};

const ZONE_STYLES: Record<string, { fill: string; stroke: string }> = {
  entrance_zone: { fill: 'rgba(34,197,94,0.12)',  stroke: '#22c55e' },
  mid_zone:      { fill: 'rgba(99,102,241,0.12)', stroke: '#6366f1' },
  deep_zone:     { fill: 'rgba(245,158,11,0.12)', stroke: '#f59e0b' },
  custom:        { fill: 'rgba(100,116,139,0.12)', stroke: '#64748b' },
};
const PHOTO_ZONE_STYLE    = { fill: 'rgba(236,72,153,0.22)', stroke: '#ec4899' };
const DEAD_ZONE_USR_STYLE = { fill: 'rgba(239,68,68,0.25)',  stroke: '#ef4444' };

function getZoneType(zone: ZoneDefinition): 'none' | 'photo' | 'dead' {
  if (zone.allowed_objects === null) return 'none';
  if (zone.allowed_objects.length === 0) return 'dead';
  if (zone.allowed_objects.includes('photo_zone')) return 'photo';
  return 'none';
}

function getZoneStyle(zone: ZoneDefinition): { fill: string; stroke: string } {
  const t = getZoneType(zone);
  if (t === 'photo') return PHOTO_ZONE_STYLE;
  if (t === 'dead')  return DEAD_ZONE_USR_STYLE;
  return ZONE_STYLES[zone.label] ?? ZONE_STYLES.custom;
}

const PADDING = 40;
const SVG_W   = 800;
const SVG_H   = 600;
const MIN_ZONE_MM = 300; // 최소 zone 크기 (mm)

const FloorView2D: React.FC<FloorView2DProps> = ({
  roomPolygon, placedObjects, detectedObjects = [], walls = [],
  selectedIndices = [], collidingIndices = new Set(),
  onObjectClick, onObjectRotate,
  userMarkings = [], onMapClick,
  deadZones = [], zones = [],
  editMode = 'view',
  selectedZoneName,
  onZoneSelect,
  onZoneDrag,
  onZoneDraw,
  draggingObjectType,
  onObjectDrop,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);

  // 그리기 상태
  const [drawRect, setDrawRect] = useState<{ start: [number, number]; end: [number, number] } | null>(null);

  // 드롭 프리뷰 위치 (SVG 좌표)
  const [dropPreview, setDropPreview] = useState<[number, number] | null>(null);

  // zone 드래그 상태
  const [zoneDrag, setZoneDrag] = useState<{
    name: string;
    polygon: [number, number][];
    svgStart: [number, number];
    svgOffset: [number, number];
  } | null>(null);

  const { scale, minX, minY, toSVG } = useMemo(() => {
    if (roomPolygon.length === 0) {
      return { scale: 1, minX: 0, minY: 0, toSVG: (x: number, y: number) => [x, y] as [number, number] };
    }
    const xs = roomPolygon.map(p => p[0]);
    const ys = roomPolygon.map(p => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const scaleX = (SVG_W - PADDING * 2) / (maxX - minX);
    const scaleY = (SVG_H - PADDING * 2) / (maxY - minY);
    const scale  = Math.min(scaleX, scaleY);
    const toSVG  = (x: number, y: number): [number, number] => [
      (x - minX) * scale + PADDING,
      (y - minY) * scale + PADDING,
    ];
    return { scale, minX, minY, toSVG };
  }, [roomPolygon]);

  const toMM = useCallback((svgX: number, svgY: number): [number, number] => [
    (svgX - PADDING) / scale + minX,
    (svgY - PADDING) / scale + minY,
  ], [scale, minX, minY]);

  const getSVGCoords = useCallback((e: React.MouseEvent | React.DragEvent): [number, number] => {
    const rect = svgRef.current!.getBoundingClientRect();
    return [
      ((e.clientX - rect.left) / rect.width)  * SVG_W,
      ((e.clientY - rect.top)  / rect.height) * SVG_H,
    ];
  }, []);

  // ── 드래그앤드롭 핸들러 ──────────────────────────
  const handleDragOver = (e: React.DragEvent<SVGSVGElement>) => {
    if (!onObjectDrop) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    const pos = getSVGCoords(e);
    setDropPreview(pos);
  };

  const handleDragLeave = () => setDropPreview(null);

  const handleDrop = (e: React.DragEvent<SVGSVGElement>) => {
    e.preventDefault();
    setDropPreview(null);
    const objectType = e.dataTransfer.getData('application/x-object-type');
    if (!objectType || !onObjectDrop) return;
    const pos = getSVGCoords(e);
    const [mmX, mmY] = toMM(pos[0], pos[1]);
    onObjectDrop(objectType, mmX, mmY);
  };

  // ── SVG 마우스 핸들러 ──────────────────────────
  const handleSVGMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (editMode === 'draw_zone') {
      e.preventDefault();
      const pos = getSVGCoords(e);
      setDrawRect({ start: pos, end: pos });
    }
  };

  const handleSVGMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const pos = getSVGCoords(e);
    if (drawRect) {
      setDrawRect(prev => prev ? { ...prev, end: pos } : null);
    }
    if (zoneDrag) {
      setZoneDrag(prev => prev ? {
        ...prev,
        svgOffset: [pos[0] - prev.svgStart[0], pos[1] - prev.svgStart[1]],
      } : null);
    }
  };

  const handleSVGMouseUp = (e: React.MouseEvent<SVGSVGElement>) => {
    const pos = getSVGCoords(e);

    if (drawRect) {
      const [mmX1, mmY1] = toMM(drawRect.start[0], drawRect.start[1]);
      const [mmX2, mmY2] = toMM(pos[0], pos[1]);
      const w = Math.abs(mmX2 - mmX1), h = Math.abs(mmY2 - mmY1);
      if (w > MIN_ZONE_MM && h > MIN_ZONE_MM) {
        const x1 = Math.min(mmX1, mmX2), y1 = Math.min(mmY1, mmY2);
        const x2 = Math.max(mmX1, mmX2), y2 = Math.max(mmY1, mmY2);
        onZoneDraw?.([[x1,y1],[x2,y1],[x2,y2],[x1,y2],[x1,y1]]);
      }
      setDrawRect(null);
      return;
    }

    if (zoneDrag) {
      const [ox, oy] = zoneDrag.svgOffset;
      const moved = Math.abs(ox) > 3 || Math.abs(oy) > 3;
      if (moved) {
        const dxMM = ox / scale;
        const dyMM = oy / scale;
        const newPoly = zoneDrag.polygon.map(([x, y]) => [x + dxMM, y + dyMM] as [number, number]);
        onZoneDrag?.(zoneDrag.name, newPoly);
      } else {
        // 클릭으로 처리
        onZoneSelect?.(zoneDrag.name === selectedZoneName ? null : zoneDrag.name);
      }
      setZoneDrag(null);
      return;
    }
  };

  const handleSVGClick = (e: React.MouseEvent<SVGSVGElement>) => {
    // 마킹 모드: 지도 클릭 → 마커 추가
    if ((editMode === 'mark_exit' || editMode === 'mark_sprinkler') && onMapClick) {
      const pos = getSVGCoords(e);
      const [mmX, mmY] = toMM(pos[0], pos[1]);
      onMapClick(mmX, mmY);
      return;
    }
    // view 모드: 빈 곳 클릭 → 선택 해제
    if (editMode === 'view') {
      onObjectClick?.(null);
      onZoneSelect?.(null);
    }
  };

  const roomPoints = roomPolygon.map(([x, y]) => toSVG(x, y).join(',')).join(' ');

  const cursor =
    draggingObjectType           ? 'copy' :
    editMode === 'draw_zone'     ? 'crosshair' :
    editMode === 'mark_exit'     ? 'cell' :
    editMode === 'mark_sprinkler'? 'cell' : 'default';

  return (
    <div className="w-full h-full flex items-center justify-center bg-[#0a0f1d]">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${SVG_W} ${SVG_H}`}
        width="100%"
        height="100%"
        style={{ maxHeight: '100%', userSelect: 'none', cursor }}
        onMouseDown={handleSVGMouseDown}
        onMouseMove={handleSVGMouseMove}
        onMouseUp={handleSVGMouseUp}
        onClick={handleSVGClick}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {/* 배경 격자 */}
        <defs>
          <pattern id="grid2d" width={scale * 500} height={scale * 500} patternUnits="userSpaceOnUse"
            x={PADDING - minX * scale} y={PADDING - minY * scale}>
            <path d={`M ${scale * 500} 0 L 0 0 0 ${scale * 500}`} fill="none" stroke="#1e293b" strokeWidth="0.5"/>
          </pattern>
        </defs>
        <rect width={SVG_W} height={SVG_H} fill="url(#grid2d)" />

        {/* 방 외곽 */}
        <polygon points={roomPoints} fill="#1e3a5f" fillOpacity={0.5} stroke="#6366f1" strokeWidth="2" />

        {/* Zone 폴리곤 */}
        {zones.map((zone) => {
          const zoneType   = getZoneType(zone);
          const isSelected = zone.name === selectedZoneName;
          const isDragging = zoneDrag?.name === zone.name;
          const style      = getZoneStyle(zone);

          // 드래그 중이면 offset 적용
          const poly = isDragging && zoneDrag
            ? zone.polygon_mm.map(([x, y]) => {
                const [sx, sy] = toSVG(x, y);
                return `${sx + zoneDrag.svgOffset[0]},${sy + zoneDrag.svgOffset[1]}`;
              }).join(' ')
            : zone.polygon_mm.map(([x, y]) => toSVG(x, y).join(',')).join(' ');

          // 라벨 위치
          const xs = zone.polygon_mm.map(p => p[0]);
          const ys = zone.polygon_mm.map(p => p[1]);
          const cxMM = (Math.min(...xs) + Math.max(...xs)) / 2;
          const cyMM = (Math.min(...ys) + Math.max(...ys)) / 2;
          const [lx, ly] = isDragging && zoneDrag
            ? [toSVG(cxMM, cyMM)[0] + zoneDrag.svgOffset[0], toSVG(cxMM, cyMM)[1] + zoneDrag.svgOffset[1]]
            : toSVG(cxMM, cyMM);

          return (
            <g key={zone.name}
              style={{ cursor: editMode === 'view' ? (isDragging ? 'grabbing' : 'grab') : 'default' }}
              onMouseDown={(e) => {
                if (editMode !== 'view') return;
                e.stopPropagation();
                const pos = getSVGCoords(e);
                setZoneDrag({ name: zone.name, polygon: zone.polygon_mm, svgStart: pos, svgOffset: [0, 0] });
              }}
            >
              <polygon points={poly}
                fill={style.fill}
                stroke={isSelected ? '#ffffff' : style.stroke}
                strokeWidth={isSelected ? 2 : 1.5}
                strokeDasharray={zone.source === 'user_defined' ? '0' : '6 3'}
              />
              <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle"
                fontSize="9" fill={isSelected ? '#ffffff' : style.stroke}
                fontWeight={isSelected ? 'bold' : 'normal'}
                style={{ pointerEvents: 'none' }}>
                {zoneType === 'photo' ? '📸 포토존 전용' : zoneType === 'dead' ? '🚫 배치불가' : zone.label.replace('_zone', '')}
              </text>
            </g>
          );
        })}

        {/* Dead Zone */}
        {deadZones.map((poly, i) => {
          const pts = poly.map(([x, y]) => toSVG(x, y).join(',')).join(' ');
          return (
            <polygon key={`dz-${i}`} points={pts}
              fill="rgba(239,68,68,0.12)" stroke="#ef4444"
              strokeWidth="1" strokeDasharray="4 2"
            />
          );
        })}

        {/* 감지된 설비 */}
        {detectedObjects.map((obj, i) => {
          if (!obj.position_mm) return null;
          const [cx, cy] = toSVG(obj.position_mm[0], obj.position_mm[1]);
          const type = obj.equipment_type;
          if (type === 'sprinkler') {
            return (
              <g key={`eq-${i}`}>
                <circle cx={cx} cy={cy} r={10} fill="#ef4444" stroke="#ff0000" strokeWidth="2" />
                <circle cx={cx} cy={cy} r={4}  fill="#ffffff" />
              </g>
            );
          }
          if (type === 'exit' || type === 'emergency_exit') {
            const w = (obj.size_mm ? obj.size_mm[0] : 350) * scale;
            const d = (obj.size_mm ? obj.size_mm[1] : 350) * scale;
            return (
              <g key={`eq-${i}`}>
                <rect x={cx-w/2} y={cy-d/2} width={w} height={d}
                  fill="#22c55e" fillOpacity={0.4} stroke="#4ade80" strokeWidth="2" rx="2" />
                <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle"
                  fontSize="9" fill="#4ade80" fontWeight="bold">EXIT</text>
              </g>
            );
          }
          return <circle key={`eq-${i}`} cx={cx} cy={cy} r={8} fill="#64748b" stroke="#94a3b8" strokeWidth="1.5" />;
        })}

        {/* 사람 스케일 레퍼런스 */}
        {(() => {
          const xs = roomPolygon.map(p => p[0]);
          const ys = roomPolygon.map(p => p[1]);
          const rcx = (Math.min(...xs) + Math.max(...xs)) / 2;
          const entrance = detectedObjects.find(o => o.equipment_type === 'exit' || o.equipment_type === 'emergency_exit');
          const [px, py] = entrance?.position_mm ?? [rcx, Math.max(...ys) + 500];
          const [svgX, svgY] = toSVG(px, py);
          const bodyW = 450 * scale, bodyH = 1500 * scale, headR = 150 * scale;
          return (
            <g key="person-ref" opacity={0.6}>
              <rect x={svgX-bodyW/2} y={svgY-bodyH/2} width={bodyW} height={bodyH}
                fill="#e2e8f0" fillOpacity={0.2} stroke="#94a3b8" strokeWidth="1.5" strokeDasharray="4 2" rx="3" />
              <circle cx={svgX} cy={svgY-bodyH/2-headR} r={headR}
                fill="#e2e8f0" fillOpacity={0.2} stroke="#94a3b8" strokeWidth="1.5" strokeDasharray="4 2" />
              <text x={svgX+bodyW/2+6} y={svgY-bodyH/2-headR*2} fontSize="9" fill="#64748b">↕ 180cm</text>
            </g>
          );
        })()}

        {/* AI 배치 오브젝트 */}
        {placedObjects.map((obj, i) => {
          const [cx, cy] = toSVG(obj.position_mm[0], obj.position_mm[1]);
          const w = obj.bbox_mm[0] * scale;
          const d = obj.bbox_mm[1] * scale;
          const isSelected  = selectedIndices.includes(i);
          const isColliding = collidingIndices.has(i);
          const color = isColliding ? '#ef4444' : (OBJECT_COLORS[obj.object_type] ?? '#ec4899');
          const name  = OBJECT_NAMES[obj.object_type] ?? obj.object_type;
          return (
            <g key={`obj-${i}`}
              transform={`translate(${cx},${cy}) rotate(${obj.rotation_deg})`}
              onClick={(e) => { e.stopPropagation(); onObjectClick?.(selectedIndices.includes(i) && !e.shiftKey ? null : i, e.shiftKey); }}
              onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); if (selectedIndices.includes(i)) onObjectRotate?.(i, 45); }}
              style={{ cursor: 'pointer' }}>
              <rect x={-w/2} y={-d/2} width={w} height={d}
                fill={color} fillOpacity={isSelected ? 0.95 : 0.7}
                stroke={isSelected ? '#ffd700' : isColliding ? '#ff0000' : '#ffffff'}
                strokeWidth={isSelected || isColliding ? 2.5 : 1} rx="3" />
              {isColliding && !isSelected && (
                <rect x={-w/2-3} y={-d/2-3} width={w+6} height={d+6}
                  fill="none" stroke="#ff0000" strokeWidth="1.5" strokeDasharray="4 2" rx="4" />
              )}
              {isSelected && (
                <rect x={-w/2-3} y={-d/2-3} width={w+6} height={d+6}
                  fill="none" stroke="#ffd700" strokeWidth="1.5" strokeDasharray="4 2" rx="4" />
              )}
              <text x={0} y={0} textAnchor="middle" dominantBaseline="middle"
                fontSize={Math.max(8, Math.min(w,d)*0.18)} fill="#ffffff" fontWeight="bold"
                style={{ pointerEvents: 'none' }}>{name}</text>
              <text x={w/2-4} y={-d/2+8} textAnchor="end" fontSize="7"
                fill="rgba(255,255,255,0.6)" style={{ pointerEvents: 'none' }}>{i+1}</text>
            </g>
          );
        })}

        {/* 가벽 */}
        {walls.map((wall) => {
          const [cx, cy] = toSVG(wall.x, wall.z);
          const w = wall.length * scale;
          const d = wall.thickness * scale;
          return (
            <g key={wall.id} transform={`translate(${cx},${cy}) rotate(${wall.rotation})`}>
              <rect x={-w/2} y={-d/2} width={w} height={Math.max(d,3)}
                fill="#94a3b8" fillOpacity={0.85} stroke="#e2e8f0" strokeWidth="1.5" rx="1" />
            </g>
          );
        })}

        {/* 사용자 수동 마킹 */}
        {userMarkings.map((m, i) => {
          const [cx, cy] = toSVG(m.position_mm[0], m.position_mm[1]);
          const color = m.equipment_type === 'exit' ? '#22c55e' : '#ef4444';
          const label = m.equipment_type === 'exit' ? 'EXIT' : 'SPR';
          return (
            <g key={`marking-${i}`}>
              <circle cx={cx} cy={cy} r={10} fill={color} fillOpacity={0.25} stroke={color} strokeWidth="2" strokeDasharray="3 2" />
              <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fontSize="7" fill={color} fontWeight="bold"
                style={{ pointerEvents: 'none' }}>{label}</text>
            </g>
          );
        })}

        {/* 영역 그리기 프리뷰 */}
        {drawRect && (() => {
          const x = Math.min(drawRect.start[0], drawRect.end[0]);
          const y = Math.min(drawRect.start[1], drawRect.end[1]);
          const w = Math.abs(drawRect.end[0] - drawRect.start[0]);
          const h = Math.abs(drawRect.end[1] - drawRect.start[1]);
          return (
            <rect x={x} y={y} width={w} height={h}
              fill="rgba(236,72,153,0.15)" stroke="#ec4899"
              strokeWidth="1.5" strokeDasharray="6 3"
              style={{ pointerEvents: 'none' }} />
          );
        })()}

        {/* 드롭 프리뷰 */}
        {dropPreview && draggingObjectType && (() => {
          const [sz_w, sz_h] = FURNITURE_SIZES_MM[draggingObjectType] ?? [600, 400];
          const pw = sz_w * scale;
          const ph = sz_h * scale;
          const color = OBJECT_COLORS[draggingObjectType] ?? '#ec4899';
          const name  = OBJECT_NAMES[draggingObjectType] ?? draggingObjectType;
          return (
            <g style={{ pointerEvents: 'none' }}>
              <rect
                x={dropPreview[0] - pw / 2} y={dropPreview[1] - ph / 2}
                width={pw} height={ph}
                fill={color} fillOpacity={0.4}
                stroke={color} strokeWidth="2" strokeDasharray="6 3" rx="3"
              />
              <text x={dropPreview[0]} y={dropPreview[1]}
                textAnchor="middle" dominantBaseline="middle"
                fontSize={Math.max(8, Math.min(pw, ph) * 0.18)} fill="#ffffff" fontWeight="bold">
                {name}
              </text>
            </g>
          );
        })()}

        {/* 범례 */}
        <g transform={`translate(${SVG_W - 130}, 10)`}>
          <rect width="125" height="42" fill="rgba(0,0,0,0.55)" rx="6" />
          <circle cx="14" cy="14" r="7" fill="#ef4444" />
          <text x="26" y="18" fontSize="9" fill="#cbd5e1">스프링클러 (감지)</text>
          <rect x="7" y="26" width="14" height="10" fill="#22c55e" fillOpacity={0.7} rx="1" />
          <text x="26" y="34" fontSize="9" fill="#cbd5e1">비상구 (감지)</text>
        </g>

        {/* 스케일 바 */}
        <g transform={`translate(${PADDING}, ${SVG_H - 20})`}>
          <line x1="0" y1="0" x2={scale*500} y2="0" stroke="#64748b" strokeWidth="2" />
          <line x1="0" y1="-4" x2="0" y2="4" stroke="#64748b" strokeWidth="1.5" />
          <line x1={scale*500} y1="-4" x2={scale*500} y2="4" stroke="#64748b" strokeWidth="1.5" />
          <text x={scale*250} y="-6" textAnchor="middle" fontSize="9" fill="#64748b">500mm</text>
        </g>
      </svg>
    </div>
  );
};

export default FloorView2D;
