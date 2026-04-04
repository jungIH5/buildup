import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import {
  Upload, Layout, AlertCircle, ArrowRight, Settings,
  Package, Layers, Box, RotateCcw, Trash2, PlusSquare,
  Undo2, RefreshCw, Send, ChevronDown, ChevronUp,
} from 'lucide-react';
import './globals.css';

import ThreeViewer from './components/ThreeViewer';
import type { Wall } from './components/ThreeViewer';
import FloorView2D from './components/FloorView2D';

interface Placement {
  object_type: string;
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number];
  height_mm: number;
  reference_point: string;
  placed_because: string;
}

interface FailedItem   { object_type: string; reason: string; }
interface Violation    { severity: string; object_type: string; rule: string; detail: string; }
interface DetectedEquip { equipment_type: string; position_mm: [number, number]; size_mm?: [number, number]; }
interface BrandStandards { clearspace_mm: number; main_corridor_min_mm: number; source: string; [key: string]: unknown; }
interface Summary { total_placed: number; total_failed: number; [key: string]: unknown; }

interface PipelineResult {
  placed: Placement[];
  failed: FailedItem[];
  violations: Violation[];
  glb_blocked: boolean;
  disclaimer_items: string[];
  summary: Summary;
  brand_standards: BrandStandards;
  room_polygon_mm?: [number, number][];
  equipment_detected?: DetectedEquip[];
  image_size_px?: [number, number];
  room_bbox_px?: [number, number, number, number];
  floor_plan_png?: string;
  scale_mm_per_px?: number;
  scale_confidence?: string;
  _cache?: { floor: unknown; standards: unknown; constraints: unknown; emergency_exits: unknown };
}

const OBJECT_NAMES: Record<string, string> = {
  character_bbox:  '캐릭터 조형물',
  shelf_rental:    '렌탈 선반',
  photo_zone:      '포토존',
  banner_stand:    '배너 스탠드',
  product_display: '상품 진열대',
};
const OBJECT_COLORS: Record<string, string> = {
  character_bbox:  '#6366f1',
  photo_zone:      '#10b981',
  shelf_rental:    '#ec4899',
  banner_stand:    '#f59e0b',
  product_display: '#3b82f6',
};
const WALL_PRESETS = [
  { label: '1m', length: 1000 },
  { label: '2m', length: 2000 },
  { label: '3m', length: 3000 },
];

let wallIdCounter = 0;
const newWallId = () => `wall_${++wallIdCounter}`;

const App: React.FC = () => {
  const [brandManual, setBrandManual]   = useState<File | null>(null);
  const [floorPlan, setFloorPlan]       = useState<File | null>(null);
  const [floorPlanUrl, setFloorPlanUrl] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult]             = useState<PipelineResult | null>(null);
  const [uploadOpen, setUploadOpen]     = useState(true);

  // requirements
  const [reqText, setReqText]           = useState('');
  // 누적 요구사항 목록 — 새 요구사항이 기존 것을 덮어쓰지 않음
  const [appliedReqs, setAppliedReqs]   = useState<string[]>([]);

  // layout cache (Agent 1·2 결과 — user_requirements 제외)
  const layoutCache = useRef<PipelineResult['_cache'] | null>(null);

  // local editable state
  const [localPlaced, setLocalPlaced]               = useState<Placement[]>([]);
  const [walls, setWalls]                           = useState<Wall[]>([]);
  const [selectedObjectIndex, setSelectedObjectIndex] = useState<number | null>(null);
  const [selectedWallId, setSelectedWallId]         = useState<string | null>(null);
  const [viewMode, setViewMode]                     = useState<'3d' | '2d'>('3d');

  // ── Undo ──
  type Snapshot = { placed: Placement[]; walls: Wall[] };
  const undoStack = useRef<Snapshot[]>([]);

  const pushHistory = (placed: Placement[], ws: Wall[]) => {
    undoStack.current = [
      ...undoStack.current.slice(-49),
      { placed: placed.map(p => ({ ...p, position_mm: [...p.position_mm] as [number, number] })), walls: ws.map(w => ({ ...w })) },
    ];
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        if (!undoStack.current.length) return;
        const snap = undoStack.current[undoStack.current.length - 1];
        undoStack.current = undoStack.current.slice(0, -1);
        setLocalPlaced(snap.placed);
        setWalls(snap.walls);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (result) { setLocalPlaced(result.placed); undoStack.current = []; }
  }, [result]);

  // ── 전체 파이프라인 실행 ──
  const handleProcess = async () => {
    if (!floorPlan && !brandManual) return;
    setIsProcessing(true);
    const allReqs = [...appliedReqs, reqText.trim()].filter(Boolean).join('\n');
    setAppliedReqs([]);
    setReqText('');
    const formData = new FormData();
    if (brandManual) formData.append('brand_manual', brandManual);
    if (floorPlan)   formData.append('floor_plan', floorPlan);
    if (allReqs)     formData.append('user_requirements', allReqs);
    try {
      const res = await axios.post('http://localhost:8000/api/pipeline/run', formData);
      setResult(res.data);
      layoutCache.current = res.data._cache ?? null;
      setWalls([]);
      setSelectedObjectIndex(null);
      setSelectedWallId(null);
      if (res.data.floor_plan_png)
        setFloorPlanUrl(`data:image/png;base64,${res.data.floor_plan_png}`);
      setUploadOpen(false);
    } catch (err) {
      console.error(err);
      alert('분석 중 오류가 발생했습니다. 백엔드 서버를 확인하세요.');
    } finally {
      setIsProcessing(false);
    }
  };

  // ── Agent 3만 재실행 (누적 requirements 사용) ──
  const handleRegenerate = async (fullReqs?: string | null) => {
    if (!layoutCache.current) { handleProcess(); return; }
    setIsProcessing(true);
    try {
      const payload = {
        ...layoutCache.current,
        user_requirements: fullReqs !== undefined ? fullReqs : appliedReqs.join('\n') || null,
        // 현재 배치된 오브젝트를 전달 — 백엔드에서 위치를 보존하고 신규만 추가 배치
        existing_placed: localPlaced.length > 0 ? localPlaced : null,
      };
      const res = await axios.post('http://localhost:8000/api/pipeline/layout_only', payload);
      // 백엔드가 기존 + 신규를 합쳐 반환하므로 그대로 교체
      setResult(prev => prev ? {
        ...prev,
        placed: res.data.placed,
        failed: res.data.failed,
        violations: res.data.violations,
        glb_blocked: res.data.glb_blocked,
        disclaimer_items: res.data.disclaimer_items,
        summary: res.data.summary,
      } : prev);
      setSelectedObjectIndex(null);
      setSelectedWallId(null);
      undoStack.current = [];
    } catch (err) {
      console.error(err);
      alert('재생성 중 오류가 발생했습니다.');
    } finally {
      setIsProcessing(false);
    }
  };

  // ── Object move ──
  const handleObjectMove = (index: number, x: number, z: number) => {
    setLocalPlaced(prev => {
      pushHistory(prev, walls);
      return prev.map((p, i) => i === index ? { ...p, position_mm: [x, z] as [number, number] } : p);
    });
  };

  // ── Wall callbacks ──
  const handleWallMove   = (id: string, x: number, z: number) => setWalls(prev => { pushHistory(localPlaced, prev); return prev.map(w => w.id === id ? { ...w, x, z } : w); });
  const handleWallRotate = (id: string) => setWalls(prev => { pushHistory(localPlaced, prev); return prev.map(w => w.id === id ? { ...w, rotation: (w.rotation + 90) % 360 } : w); });
  const handleWallDelete = (id: string) => setWalls(prev => { pushHistory(localPlaced, prev); if (selectedWallId === id) setSelectedWallId(null); return prev.filter(w => w.id !== id); });

  const handleAddWall = (length: number) => {
    const poly = result?.room_polygon_mm ?? [];
    const cx = poly.length ? poly.reduce((s, p) => s + p[0], 0) / poly.length : 0;
    const cz = poly.length ? poly.reduce((s, p) => s + p[1], 0) / poly.length : 0;
    const count = walls.length;
    const id = newWallId();
    setWalls(prev => {
      pushHistory(localPlaced, prev);
      return [...prev, {
        id, x: cx + (count % 3 - 1) * 700, z: cz + Math.floor(count / 3) * 700,
        rotation: 0, length, height: 2500, thickness: 100,
      }];
    });
    setSelectedWallId(id);
  };

  const canAnalyze = (!!floorPlan || !!brandManual) && !isProcessing;

  return (
    <div className="flex flex-col min-h-screen" style={{ background: 'var(--bg-base, #0d1117)' }}>
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <div className="bg-primary p-2 rounded-xl shadow-lg ring-2 ring-primary/20">
            <Box size={24} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight hero-gradient">BuildUp</h1>
            <p className="text-text-muted text-xs">AI 브랜드 메뉴얼 기반 자동 배치 솔루션</p>
          </div>
        </div>
        {result && (
          <div className="flex items-center gap-2">
            <span className="bg-accent/10 text-accent px-3 py-1 rounded-full text-xs font-bold border border-accent/20">
              성공 {result.summary.total_placed}
            </span>
            {result.summary.total_failed > 0 && (
              <span className="bg-red-500/10 text-red-400 px-3 py-1 rounded-full text-xs font-bold border border-red-500/20">
                미배치 {result.summary.total_failed}
              </span>
            )}
          </div>
        )}
      </header>

      {/* ── Body: LEFT controls | RIGHT viewer ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ════ LEFT PANEL ════ */}
        <aside className="w-80 shrink-0 flex flex-col border-r border-border overflow-y-auto">

          {/* 파일 업로드 섹션 (접기 가능) */}
          <div className="border-b border-border">
            <button
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/5 transition-colors"
              onClick={() => setUploadOpen(v => !v)}
            >
              <span className="text-sm font-bold flex items-center gap-2">
                <Upload size={14} className="text-primary" /> 파일 업로드
                {(brandManual || floorPlan) && (
                  <span className="w-2 h-2 rounded-full bg-accent ml-1" />
                )}
              </span>
              {uploadOpen ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
            </button>

            {uploadOpen && (
              <div className="px-4 pb-4 space-y-3">
                {/* 브랜드 메뉴얼 */}
                <label className="block">
                  <div className="text-xs text-text-muted mb-1 flex items-center gap-1">
                    <Layout size={11} className="text-primary" /> 브랜드 메뉴얼 (PDF)
                  </div>
                  <div className="border border-dashed border-border rounded-xl p-3 cursor-pointer hover:border-primary/50 transition-colors group">
                    <input type="file" className="hidden" accept=".pdf"
                      onChange={e => setBrandManual(e.target.files?.[0] || null)} />
                    <div className="flex items-center gap-2">
                      <Upload size={14} className="text-text-muted group-hover:text-primary transition-colors shrink-0" />
                      <span className="text-xs text-text-muted truncate">
                        {brandManual ? brandManual.name : '파일 선택 또는 드래그'}
                      </span>
                    </div>
                  </div>
                </label>

                {/* 도면 */}
                <label className="block">
                  <div className="text-xs text-text-muted mb-1 flex items-center gap-1">
                    <Layers size={11} className="text-accent" /> 도면 파일 (이미지·PDF·DXF·DWG)
                  </div>
                  <div className="border border-dashed border-border rounded-xl p-3 cursor-pointer hover:border-accent/50 transition-colors group">
                    <input type="file" className="hidden" accept="image/*,.pdf,.dxf,.dwg"
                      onChange={e => setFloorPlan(e.target.files?.[0] || null)} />
                    <div className="flex items-center gap-2">
                      <Upload size={14} className="text-text-muted group-hover:text-accent transition-colors shrink-0" />
                      <span className="text-xs text-text-muted truncate">
                        {floorPlan ? (
                          <span className="flex items-center gap-1">
                            {floorPlan.name}
                            {(floorPlan.name.endsWith('.dxf') || floorPlan.name.endsWith('.dwg')) && (
                              <span className="bg-accent/20 text-accent text-[9px] px-1 rounded font-bold">CAD</span>
                            )}
                          </span>
                        ) : '파일 선택 또는 드래그'}
                      </span>
                    </div>
                  </div>
                </label>
              </div>
            )}
          </div>

          {/* 요구사항 입력 */}
          <div className="px-4 py-3 border-b border-border">
            <div className="text-xs text-text-muted mb-2 flex items-center gap-1">
              <Send size={11} className="text-accent" /> 배치 요구사항
              <span className="ml-1 text-[10px] opacity-60">(선택)</span>
            </div>

            {/* 누적 요구사항 칩 */}
            {appliedReqs.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {appliedReqs.map((req, idx) => (
                  <span key={idx}
                    className="flex items-center gap-1 bg-accent/10 border border-accent/30
                               text-accent text-[10px] px-2 py-0.5 rounded-full max-w-full">
                    <span className="truncate max-w-[160px]" title={req}>{req}</span>
                    <button
                      onClick={() => {
                        const next = appliedReqs.filter((_, i) => i !== idx);
                        setAppliedReqs(next);
                        handleRegenerate(next.join('\n') || null);
                      }}
                      className="shrink-0 text-accent/60 hover:text-accent leading-none ml-0.5">×</button>
                  </span>
                ))}
              </div>
            )}

            <textarea
              className="w-full bg-black/20 border border-border rounded-xl p-2.5 text-xs text-text-main
                         placeholder-text-muted resize-none focus:outline-none focus:border-accent/60 transition-colors"
              rows={3}
              placeholder={"예) 상품진열대를 벽 쪽에 4개, 중앙에 2개 배치해주세요.\n포토존은 입구 정면에 놓아주세요."}
              value={reqText}
              onChange={e => setReqText(e.target.value)}
            />
            {/* 분석하기 / 요구사항 적용 버튼 */}
            {result ? (
              <div className="flex gap-2 mt-2">
                <button
                  onClick={() => {
                    const newReq = reqText.trim();
                    const newApplied = newReq ? [...appliedReqs, newReq] : appliedReqs;
                    if (newReq) setAppliedReqs(newApplied);
                    setReqText('');
                    handleRegenerate(newApplied.join('\n') || null);
                  }}
                  disabled={isProcessing || (!reqText.trim() && appliedReqs.length === 0)}
                  className="flex-1 flex items-center justify-center gap-1 py-2 rounded-xl
                             bg-accent/20 border border-accent/40 text-accent text-xs font-bold
                             hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <Send size={12} />
                  {isProcessing ? '적용 중...' : '요구사항 적용'}
                </button>
                <button
                  onClick={() => handleRegenerate(appliedReqs.join('\n') || null)}
                  disabled={isProcessing}
                  title="요구사항 유지하고 배치만 다시 생성"
                  className="px-3 py-2 rounded-xl border border-border text-text-muted text-xs
                             hover:bg-white/10 hover:text-white disabled:opacity-40 transition-colors"
                >
                  <RefreshCw size={12} className={isProcessing ? 'animate-spin' : ''} />
                </button>
              </div>
            ) : (
              <button
                onClick={handleProcess}
                disabled={!canAnalyze}
                className="w-full mt-2 flex items-center justify-center gap-1.5 py-2.5 rounded-xl
                           bg-primary text-white text-sm font-bold
                           hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {isProcessing ? (
                  <><RefreshCw size={14} className="animate-spin" /> AI 분석 중...</>
                ) : (
                  <><ArrowRight size={14} /> 분석 시작하기</>
                )}
              </button>
            )}
          </div>

          {/* 분석 완료 후 패널들 */}
          {result && (
            <>
              {/* 가벽 설치 */}
              <div className="px-4 py-3 border-b border-border">
                <h4 className="text-xs font-bold mb-2 flex items-center gap-1.5">
                  <PlusSquare size={13} className="text-accent" /> 가벽 설치
                </h4>
                <div className="flex gap-1.5 mb-2">
                  {WALL_PRESETS.map(({ label, length }) => (
                    <button key={label} onClick={() => handleAddWall(length)}
                      className="flex-1 py-1.5 rounded-lg border border-accent/40 text-accent text-xs font-bold
                                 hover:bg-accent/10 transition-colors">
                      + {label}
                    </button>
                  ))}
                </div>
                {walls.length > 0 && (
                  <div className="space-y-1.5">
                    {walls.map(wall => {
                      const isSel = selectedWallId === wall.id;
                      return (
                        <div key={wall.id}
                          onClick={() => { setSelectedWallId(p => p === wall.id ? null : wall.id); setSelectedObjectIndex(null); }}
                          className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg border cursor-pointer transition-all text-xs ${
                            isSel ? 'border-yellow-400/60 bg-yellow-400/10' : 'border-white/5 bg-black/20 hover:bg-white/5'}`}>
                          <span className="font-bold">{(wall.length / 1000).toFixed(0)}m벽 · {wall.rotation}°</span>
                          <div className="flex gap-1">
                            <button onClick={e => { e.stopPropagation(); handleWallRotate(wall.id); }}
                              className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white">
                              <RotateCcw size={11} />
                            </button>
                            <button onClick={e => { e.stopPropagation(); handleWallDelete(wall.id); }}
                              className="p-1 rounded hover:bg-red-500/20 text-text-muted hover:text-red-400">
                              <Trash2 size={11} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* 배치된 오브젝트 */}
              <div className="px-4 py-3 border-b border-border">
                <h4 className="text-xs font-bold mb-2 flex items-center gap-1.5">
                  <Package size={13} className="text-primary" /> 배치된 오브젝트
                  <span className="ml-auto text-[10px] text-text-muted">클릭·드래그</span>
                </h4>
                <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
                  {localPlaced.map((p, i) => {
                    const isSel = selectedObjectIndex === i;
                    const color = OBJECT_COLORS[p.object_type] ?? '#ec4899';
                    const name  = OBJECT_NAMES[p.object_type]  ?? p.object_type;
                    return (
                      <button key={i}
                        onClick={() => { setSelectedObjectIndex(prev => prev === i ? null : i); setSelectedWallId(null); }}
                        className={`w-full text-left px-2.5 py-2 rounded-lg border transition-all ${
                          isSel ? 'border-yellow-400/60 bg-yellow-400/10' : 'border-white/5 bg-black/20 hover:bg-white/5'}`}>
                        <div className="flex items-center gap-1.5">
                          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: color }} />
                          <span className="text-xs font-bold text-text-main">{name}</span>
                          {isSel && <span className="ml-auto text-yellow-400 text-[10px]">선택됨</span>}
                        </div>
                        <div className="text-[10px] text-text-muted mt-0.5 pl-4">
                          {p.reference_point} · {p.bbox_mm[0]}×{p.bbox_mm[1]} (H{p.height_mm})
                        </div>
                        {isSel && (
                          <div className="text-[10px] text-text-muted mt-1 pl-4 leading-relaxed border-t border-white/5 pt-1">
                            {p.placed_because}
                          </div>
                        )}
                      </button>
                    );
                  })}
                  {localPlaced.length === 0 && <p className="text-xs text-text-muted italic">배치된 오브젝트 없음</p>}
                </div>
              </div>

              {/* 미배치 리포트 */}
              {result.failed.length > 0 && (
                <div className="px-4 py-3 border-b border-border">
                  <h4 className="text-xs font-bold mb-2 flex items-center gap-1.5 text-red-400">
                    <AlertCircle size={13} /> 미배치 ({result.failed.length})
                  </h4>
                  <div className="space-y-1.5">
                    {result.failed.map((f, i) => (
                      <div key={i} className="px-2.5 py-2 rounded-lg bg-red-500/5 border border-red-500/10">
                        <div className="text-xs font-bold text-text-main">{OBJECT_NAMES[f.object_type] ?? f.object_type}</div>
                        <div className="text-[10px] text-text-muted mt-0.5 leading-relaxed">{f.reason}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 브랜드 기준 */}
              <div className="px-4 py-3">
                <h4 className="text-xs font-bold mb-2 flex items-center gap-1.5">
                  <Settings size={13} className="text-primary" /> 브랜드 추출 기준
                </h4>
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-white/5 px-2.5 py-2 rounded-lg">
                    <div className="text-[9px] uppercase tracking-wider text-text-muted">Clearspace</div>
                    <div className="text-xs font-bold">{result.brand_standards.clearspace_mm}mm</div>
                  </div>
                  <div className="bg-white/5 px-2.5 py-2 rounded-lg">
                    <div className="text-[9px] uppercase tracking-wider text-text-muted">복도 최소폭</div>
                    <div className="text-xs font-bold">{result.brand_standards.main_corridor_min_mm}mm</div>
                  </div>
                  <div className="bg-white/5 px-2.5 py-2 rounded-lg col-span-2">
                    <div className="text-[9px] uppercase tracking-wider text-text-muted">추출 출처</div>
                    <div className="text-xs font-bold">{result.brand_standards.source}</div>
                  </div>
                  {result.scale_mm_per_px !== undefined && (
                    <div className={`bg-white/5 px-2.5 py-2 rounded-lg col-span-2 border ${
                      result.scale_confidence === 'high' ? 'border-green-500/30' :
                      result.scale_confidence === 'medium' ? 'border-yellow-500/30' : 'border-red-500/30'
                    }`}>
                      <div className="text-[9px] uppercase tracking-wider text-text-muted">도면 스케일 감지</div>
                      <div className="text-xs font-bold">
                        {result.scale_mm_per_px.toFixed(2)}mm/px
                        <span className={`ml-1.5 text-[10px] ${
                          result.scale_confidence === 'high' ? 'text-green-400' :
                          result.scale_confidence === 'medium' ? 'text-yellow-400' : 'text-red-400'
                        }`}>
                          {result.scale_confidence === 'high' ? '✓ 정확' :
                           result.scale_confidence === 'medium' ? '△ 보통' : '✗ 실패(기본값)'}
                        </span>
                      </div>
                      {result.room_polygon_mm && result.room_polygon_mm.length > 0 && (() => {
                        const xs = result.room_polygon_mm!.map(p => p[0]);
                        const ys = result.room_polygon_mm!.map(p => p[1]);
                        const w = Math.round(Math.max(...xs) - Math.min(...xs));
                        const h = Math.round(Math.max(...ys) - Math.min(...ys));
                        return <div className="text-[10px] text-text-muted mt-0.5">방 {w}×{h}mm</div>;
                      })()}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </aside>

        {/* ════ RIGHT PANEL: 3D Viewer ════ */}
        <main className="flex-1 flex flex-col min-w-0">
          {/* Viewer toolbar */}
          <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
            <h2 className="text-sm font-bold flex items-center gap-2">
              <Package size={15} className="text-primary" /> 배치 레이아웃
            </h2>
            <div className="flex items-center gap-2">
              {result && (
                <>
                  <button onClick={() => { if (undoStack.current.length === 0) return; const s = undoStack.current[undoStack.current.length-1]; undoStack.current = undoStack.current.slice(0,-1); setLocalPlaced(s.placed); setWalls(s.walls); }}
                    title="실행취소 (Ctrl+Z)"
                    className="flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[11px] font-bold text-text-muted
                               hover:text-white hover:bg-white/10 transition-colors">
                    <Undo2 size={12} /> 되돌리기
                  </button>
                </>
              )}
              <div className="flex rounded-lg border border-border overflow-hidden text-[11px] font-bold">
                <button onClick={() => setViewMode('3d')}
                  className={`px-2.5 py-1 transition-colors ${viewMode === '3d' ? 'bg-primary text-white' : 'text-text-muted hover:bg-white/5'}`}>
                  3D
                </button>
                <button onClick={() => setViewMode('2d')}
                  className={`px-2.5 py-1 transition-colors ${viewMode === '2d' ? 'bg-primary text-white' : 'text-text-muted hover:bg-white/5'}`}>
                  2D
                </button>
              </div>
            </div>
          </div>

          {/* Viewer canvas */}
          <div className="flex-1 bg-[#0a0f1d] relative overflow-hidden">
            {result ? (
              viewMode === '3d' ? (
                <ThreeViewer
                  roomPolygon={result.room_polygon_mm || []}
                  placedObjects={localPlaced}
                  detectedObjects={result.equipment_detected || []}
                  walls={walls}
                  floorPlanUrl={floorPlanUrl ?? undefined}
                  roomBboxPx={result.room_bbox_px}
                  imageSizePx={result.image_size_px}
                  selectedIndex={selectedObjectIndex}
                  selectedWallId={selectedWallId}
                  onObjectClick={idx => { setSelectedObjectIndex(p => p === idx ? null : idx); setSelectedWallId(null); }}
                  onWallClick={id => { setSelectedWallId(p => p === id ? null : id); setSelectedObjectIndex(null); }}
                  onObjectMove={handleObjectMove}
                  onWallMove={handleWallMove}
                />
              ) : (
                <FloorView2D
                  roomPolygon={result.room_polygon_mm || []}
                  placedObjects={localPlaced}
                  detectedObjects={result.equipment_detected || []}
                  walls={walls}
                  selectedIndex={selectedObjectIndex}
                  onObjectClick={idx => { setSelectedObjectIndex(p => p === idx ? null : idx); setSelectedWallId(null); }}
                />
              )
            ) : (
              /* 분석 전 빈 화면 */
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 select-none">
                <div className="w-24 h-24 rounded-3xl bg-white/5 border border-border flex items-center justify-center">
                  <Box size={40} className="text-white/20" />
                </div>
                <div className="text-center">
                  <p className="text-text-muted text-sm font-medium">
                    {isProcessing ? 'AI가 공간을 분석 중입니다...' : '좌측에서 파일을 업로드하고 분석을 시작하세요'}
                  </p>
                  {isProcessing && (
                    <div className="mt-3 flex items-center justify-center gap-2 text-accent text-xs">
                      <RefreshCw size={13} className="animate-spin" />
                      Agent 1→2→3 파이프라인 실행 중
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* 드래그 힌트 */}
            {result && viewMode === '3d' && (
              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 bg-black/60 backdrop-blur-sm
                              text-[10px] text-text-muted px-3 py-1 rounded-full border border-white/10 pointer-events-none">
                오브젝트·가벽 드래그로 이동 · 배경 드래그로 시점 변경
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
};

export default App;
