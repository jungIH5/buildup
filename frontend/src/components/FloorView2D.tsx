import React, { useMemo } from 'react';
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

interface FloorView2DProps {
  roomPolygon: [number, number][];
  placedObjects: PlacedObject[];
  detectedObjects?: DetectedObject[];
  walls?: Wall[];
  selectedIndex?: number | null;
  onObjectClick?: (index: number | null) => void;
}

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

const PADDING = 40;
const SVG_W   = 800;
const SVG_H   = 600;

const FloorView2D: React.FC<FloorView2DProps> = ({
  roomPolygon, placedObjects, detectedObjects = [], walls = [],
  selectedIndex, onObjectClick,
}) => {
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

  const roomPoints = roomPolygon.map(([x, y]) => toSVG(x, y).join(',')).join(' ');

  return (
    <div className="w-full h-full flex items-center justify-center bg-[#0a0f1d]">
      <svg
        viewBox={`0 0 ${SVG_W} ${SVG_H}`}
        width="100%"
        height="100%"
        style={{ maxHeight: '100%', userSelect: 'none' }}
        onClick={() => onObjectClick?.(null)}
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
                <rect x={cx - w/2} y={cy - d/2} width={w} height={d}
                  fill="#22c55e" fillOpacity={0.4} stroke="#4ade80" strokeWidth="2" rx="2" />
                <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle"
                  fontSize="9" fill="#4ade80" fontWeight="bold">EXIT</text>
              </g>
            );
          }
          return (
            <circle key={`eq-${i}`} cx={cx} cy={cy} r={8} fill="#64748b" stroke="#94a3b8" strokeWidth="1.5" />
          );
        })}

        {/* AI 배치 오브젝트 */}
        {placedObjects.map((obj, i) => {
          const [cx, cy] = toSVG(obj.position_mm[0], obj.position_mm[1]);
          const w  = obj.bbox_mm[0] * scale;
          const d  = obj.bbox_mm[1] * scale;
          const isSelected = selectedIndex === i;
          const color = OBJECT_COLORS[obj.object_type] ?? '#ec4899';
          const name  = OBJECT_NAMES[obj.object_type]  ?? obj.object_type;

          return (
            <g
              key={`obj-${i}`}
              transform={`translate(${cx}, ${cy}) rotate(${obj.rotation_deg})`}
              onClick={(e) => { e.stopPropagation(); onObjectClick?.(selectedIndex === i ? null : i); }}
              style={{ cursor: 'pointer' }}
            >
              <rect
                x={-w / 2} y={-d / 2} width={w} height={d}
                fill={color}
                fillOpacity={isSelected ? 0.95 : 0.7}
                stroke={isSelected ? '#ffd700' : '#ffffff'}
                strokeWidth={isSelected ? 2.5 : 1}
                rx="3"
              />
              {/* 선택 시 외곽 강조 */}
              {isSelected && (
                <rect
                  x={-w/2 - 3} y={-d/2 - 3} width={w + 6} height={d + 6}
                  fill="none" stroke="#ffd700" strokeWidth="1.5" strokeDasharray="4 2" rx="4"
                />
              )}
              {/* 오브젝트 이름 */}
              <text
                x={0} y={0}
                textAnchor="middle" dominantBaseline="middle"
                fontSize={Math.max(8, Math.min(w, d) * 0.18)}
                fill="#ffffff"
                fontWeight="bold"
                style={{ pointerEvents: 'none' }}
              >
                {name}
              </text>
              {/* 번호 */}
              <text
                x={w / 2 - 4} y={-d / 2 + 8}
                textAnchor="end"
                fontSize="7"
                fill="rgba(255,255,255,0.6)"
                style={{ pointerEvents: 'none' }}
              >
                {i + 1}
              </text>
            </g>
          );
        })}

        {/* 가벽 */}
        {walls.map((wall) => {
          const [cx, cy] = toSVG(wall.x, wall.z);
          const w = wall.length * scale;
          const d = wall.thickness * scale;
          return (
            <g
              key={wall.id}
              transform={`translate(${cx}, ${cy}) rotate(${wall.rotation})`}
            >
              <rect
                x={-w / 2} y={-d / 2} width={w} height={Math.max(d, 3)}
                fill="#94a3b8"
                fillOpacity={0.85}
                stroke="#e2e8f0"
                strokeWidth="1.5"
                rx="1"
              />
            </g>
          );
        })}

        {/* 범례 */}
        <g transform={`translate(${SVG_W - 130}, 10)`}>
          <rect width="125" height={32 + detectedObjects.length > 0 ? 52 : 32}
            fill="rgba(0,0,0,0.5)" rx="6" />
          <circle cx="14" cy="14" r="7" fill="#ef4444" />
          <text x="26" y="18" fontSize="9" fill="#cbd5e1">스프링클러</text>
          <rect x="7" y="26" width="14" height="10" fill="#22c55e" fillOpacity={0.7} rx="1" />
          <text x="26" y="34" fontSize="9" fill="#cbd5e1">비상구</text>
        </g>

        {/* 스케일 바 (500mm) */}
        <g transform={`translate(${PADDING}, ${SVG_H - 20})`}>
          <line x1="0" y1="0" x2={scale * 500} y2="0" stroke="#64748b" strokeWidth="2" />
          <line x1="0" y1="-4" x2="0" y2="4" stroke="#64748b" strokeWidth="1.5" />
          <line x1={scale * 500} y1="-4" x2={scale * 500} y2="4" stroke="#64748b" strokeWidth="1.5" />
          <text x={scale * 250} y="-6" textAnchor="middle" fontSize="9" fill="#64748b">500mm</text>
        </g>
      </svg>
    </div>
  );
};

export default FloorView2D;
