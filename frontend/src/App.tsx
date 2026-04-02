import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import {
  Upload,
  Layout,
  AlertCircle,
  ArrowRight,
  ChevronLeft,
  Settings,
  Package,
  Layers,
  Box,
  RotateCcw,
  Trash2,
  PlusSquare,
  Undo2,
  RefreshCw,
} from 'lucide-react';
import './globals.css';

import ThreeViewer from './components/ThreeViewer';
import type { Wall } from './components/ThreeViewer';
import FloorView2D from './components/FloorView2D';

// Types
type Step = 'upload' | 'result';

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
  const [currentStep, setCurrentStep]           = useState<Step>('upload');
  const [brandManual, setBrandManual]           = useState<File | null>(null);
  const [floorPlan, setFloorPlan]               = useState<File | null>(null);
  const [floorPlanUrl, setFloorPlanUrl]         = useState<string | null>(null);
  const [userRequirements, setUserRequirements] = useState('');
  const [isProcessing, setIsProcessing]         = useState(false);
  const [result, setResult]                     = useState<PipelineResult | null>(null);
  const layoutCache = useRef<PipelineResult['_cache'] | null>(null);

  // Local editable state (drag moves update these without re-running pipeline)
  const [localPlaced, setLocalPlaced]           = useState<Placement[]>([]);
  const [walls, setWalls]                       = useState<Wall[]>([]);
  const [selectedObjectIndex, setSelectedObjectIndex] = useState<number | null>(null);
  const [selectedWallId, setSelectedWallId]     = useState<string | null>(null);
  const [viewMode, setViewMode]                 = useState<'3d' | '2d'>('3d');

  // ── Agent 3만 재실행 (Agent 1·2 캐시 재활용) ──
  const handleRegenerate = async () => {
    if (!layoutCache.current) {
      // 캐시 없으면 전체 파이프라인 재실행
      handleProcess();
      return;
    }
    setIsProcessing(true);
    try {
      const response = await axios.post('http://localhost:8000/api/pipeline/layout_only', layoutCache.current);
      setResult(prev => prev ? {
        ...prev,
        placed: response.data.placed,
        failed: response.data.failed,
        violations: response.data.violations,
        glb_blocked: response.data.glb_blocked,
        disclaimer_items: response.data.disclaimer_items,
        summary: response.data.summary,
      } : prev);
      setSelectedObjectIndex(null);
      setSelectedWallId(null);
      undoStack.current = [];
    } catch (error) {
      console.error('Regenerate failed:', error);
      alert('재생성 중 오류가 발생했습니다.');
    } finally {
      setIsProcessing(false);
    }
  };

  // ── Undo stack ──
  type Snapshot = { placed: Placement[]; walls: Wall[] };
  const undoStack = useRef<Snapshot[]>([]);

  const pushHistory = (placed: Placement[], ws: Wall[]) => {
    undoStack.current = [
      ...undoStack.current.slice(-49), // cap at 50
      { placed: placed.map(p => ({ ...p, position_mm: [...p.position_mm] as [number,number] })), walls: ws.map(w => ({ ...w })) },
    ];
  };

  const handleUndo = () => {
    if (undoStack.current.length === 0) return;
    const snap = undoStack.current[undoStack.current.length - 1];
    undoStack.current = undoStack.current.slice(0, -1);
    setLocalPlaced(snap.placed);
    setWalls(snap.walls);
  };

  // Ctrl+Z / Cmd+Z keyboard shortcut
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        // handleUndo only touches refs + setters — safe to call from closure
        if (undoStack.current.length === 0) return;
        const snap = undoStack.current[undoStack.current.length - 1];
        undoStack.current = undoStack.current.slice(0, -1);
        setLocalPlaced(snap.placed);
        setWalls(snap.walls);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Sync localPlaced when result updates
  useEffect(() => {
    if (result) {
      setLocalPlaced(result.placed);
      undoStack.current = [];
    }
  }, [result]);

  const handleProcess = async () => {
    if (!floorPlan && !brandManual) return;
    setIsProcessing(true);
    const formData = new FormData();
    if (brandManual) formData.append('brand_manual', brandManual);
    if (floorPlan)   formData.append('floor_plan', floorPlan);
    if (userRequirements.trim()) formData.append('user_requirements', userRequirements.trim());

    try {
      const response = await axios.post('http://localhost:8000/api/pipeline/run', formData);
      setResult(response.data);
      layoutCache.current = response.data._cache ?? null;
      setWalls([]);
      setSelectedObjectIndex(null);
      setSelectedWallId(null);
      if (response.data.floor_plan_png) {
        setFloorPlanUrl(`data:image/png;base64,${response.data.floor_plan_png}`);
      }
      setCurrentStep('result');
    } catch (error) {
      console.error('Pipeline failed:', error);
      alert('공정 처리 중 오류가 발생했습니다. 백엔드 서버 확인이 필요합니다.');
    } finally {
      setIsProcessing(false);
    }
  };

  // ── Object drag callback ──
  const handleObjectMove = (index: number, x: number, z: number) => {
    setLocalPlaced(prev => {
      pushHistory(prev, walls);
      return prev.map((p, i) => i === index ? { ...p, position_mm: [x, z] as [number, number] } : p);
    });
  };

  // ── Wall callbacks ──
  const handleWallMove = (id: string, x: number, z: number) => {
    setWalls(prev => {
      pushHistory(localPlaced, prev);
      return prev.map(w => w.id === id ? { ...w, x, z } : w);
    });
  };

  const handleWallRotate = (id: string) => {
    setWalls(prev => {
      pushHistory(localPlaced, prev);
      return prev.map(w => w.id === id ? { ...w, rotation: (w.rotation + 90) % 360 } : w);
    });
  };

  const handleWallDelete = (id: string) => {
    setWalls(prev => {
      pushHistory(localPlaced, prev);
      const next = prev.filter(w => w.id !== id);
      if (selectedWallId === id) setSelectedWallId(null);
      return next;
    });
  };

  const handleAddWall = (length: number) => {
    const poly = result?.room_polygon_mm ?? [];
    let cx = 0, cz = 0;
    if (poly.length > 0) {
      cx = poly.reduce((s, p) => s + p[0], 0) / poly.length;
      cz = poly.reduce((s, p) => s + p[1], 0) / poly.length;
    }
    // 기존 가벽 수에 따라 위치를 엇갈려서 겹치지 않게 생성
    const step = 700; // 700mm 간격
    const count = walls.length;
    const col = count % 3;       // 0,1,2 → x축 방향
    const row = Math.floor(count / 3); // 3개마다 z축으로 한 줄 아래
    const offsetX = (col - 1) * step;  // -700, 0, +700
    const offsetZ = row * step;

    const id = newWallId();
    setWalls(prev => {
      pushHistory(localPlaced, prev);
      return [...prev, {
        id,
        x: cx + offsetX,
        z: cz + offsetZ,
        rotation: 0,
        length,
        height: 2500,
        thickness: 100,
      }];
    });
    setSelectedWallId(id);
  };

  return (
    <div className="container mx-auto px-4 py-8 max-w-7xl min-h-screen">
      {/* Header */}
      <header className="flex justify-between items-center mb-12 fade-in">
        <div className="flex items-center gap-3">
          <div className="bg-primary p-3 rounded-2xl shadow-lg ring-4 ring-primary/20">
            <Box size={32} className="text-white" />
          </div>
          <div>
            <h1 className="text-3xl font-bold tracking-tight hero-gradient">BuildUp</h1>
            <p className="text-text-muted text-sm font-medium">AI 브랜드 메뉴얼 기반 자동 배치 솔루션</p>
          </div>
        </div>
        <div className="step-indicator">
          {(['upload', 'result'] as Step[]).map((step, idx) => (
            <div
              key={step}
              className={`step ${currentStep === step ? 'active' : ''} ${idx < ['upload', 'result'].indexOf(currentStep) ? 'done' : ''}`}
            >
              {idx + 1}. {step === 'upload' ? 'Upload' : 'Result'}
            </div>
          ))}
        </div>
      </header>

      <main className="grid grid-cols-1 gap-8 fade-in">
        {/* ── Upload Step ── */}
        {currentStep === 'upload' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="glass-card p-8 flex flex-col items-center text-center">
              <div className="bg-primary/10 p-5 rounded-3xl mb-6">
                <Layout size={48} className="text-primary" />
              </div>
              <h3 className="text-xl font-bold mb-2">브랜드 메뉴얼 업로드</h3>
              <p className="text-text-muted text-sm mb-8">PDF 파일 (Clearspace, 배치 규정 추출)</p>
              <label className="w-full border-2 border-dashed border-border rounded-2xl p-10 cursor-pointer hover:border-primary/50 transition-all group">
                <input type="file" className="hidden" accept=".pdf" onChange={(e) => setBrandManual(e.target.files?.[0] || null)} />
                <div className="flex flex-col items-center gap-2">
                  <Upload size={32} className="text-text-muted group-hover:text-primary transition-colors" />
                  <span className="font-medium text-text-muted">{brandManual ? brandManual.name : '파일 선택 또는 드래그'}</span>
                </div>
              </label>
            </div>

            <div className="glass-card p-8 flex flex-col items-center text-center">
              <div className="bg-accent/10 p-5 rounded-3xl mb-6">
                <Layers size={48} className="text-accent" />
              </div>
              <h3 className="text-xl font-bold mb-2">도면 파일 업로드</h3>
              <p className="text-text-muted text-sm mb-8">도면 이미지(PNG, JPG) 또는 PDF 파일</p>
              <label className="w-full border-2 border-dashed border-border rounded-2xl p-10 cursor-pointer hover:border-accent/50 transition-all group">
                <input type="file" className="hidden" accept="image/*,.pdf" onChange={(e) => setFloorPlan(e.target.files?.[0] || null)} />
                <div className="flex flex-col items-center gap-2">
                  <Upload size={32} className="text-text-muted group-hover:text-accent transition-colors" />
                  <span className="font-medium text-text-muted">{floorPlan ? floorPlan.name : '파일 선택 또는 드래그'}</span>
                </div>
              </label>
            </div>

            {/* 요구사항 입력 */}
            <div className="md:col-span-2 glass-card p-6">
              <h3 className="text-base font-bold mb-2 flex items-center gap-2">
                <Layout size={18} className="text-accent" /> 배치 요구사항 입력
                <span className="ml-2 text-xs text-text-muted font-normal">(선택사항)</span>
              </h3>
              <p className="text-xs text-text-muted mb-3">
                원하는 배치 조건을 자유롭게 입력하세요.<br/>
                예) <span className="text-accent">상품진열대를 8개 배치해주세요. 벽에 4개, 중앙에 4개.</span>
                &nbsp;/&nbsp;
                <span className="text-accent">포토존은 입구 정면에, 캐릭터 조형물은 중앙에 배치해주세요.</span>
              </p>
              <textarea
                className="w-full bg-black/20 border border-border rounded-xl p-3 text-sm text-text-main placeholder-text-muted resize-none focus:outline-none focus:border-accent/60 transition-colors"
                rows={3}
                placeholder="배치 요구사항을 입력하세요..."
                value={userRequirements}
                onChange={(e) => setUserRequirements(e.target.value)}
              />
            </div>

            <div className="md:col-span-2 flex flex-col items-center mt-4 gap-2">
              <button
                className="btn-primary"
                disabled={(!floorPlan && !brandManual) || isProcessing}
                onClick={handleProcess}
              >
                {isProcessing ? 'AI 분석 중...' : floorPlan ? '분석 시작하기' : '샘플 공간으로 분석 (매뉴얼만)'}
                {!isProcessing && <ArrowRight size={20} />}
              </button>
              {!floorPlan && brandManual && !isProcessing && (
                <p className="text-xs text-text-muted mt-2">
                  <span className="text-accent">*</span> 도면이 없어 10m x 8m 샘플 공간에서 분석 결과를 보여드립니다.
                </p>
              )}
            </div>
          </div>
        )}

        {/* ── Result Step ── */}
        {currentStep === 'result' && result && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            {/* 3D / 2D Viewer */}
            <div className="lg:col-span-2 glass-card p-6 min-h-[600px] flex flex-col">
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-xl font-bold flex items-center gap-2">
                  <Package className="text-primary" /> 배치 레이아웃
                </h3>
                <div className="flex gap-2 items-center">
                  <button
                    onClick={handleRegenerate}
                    disabled={isProcessing}
                    title="배치 재생성 (도면·메뉴얼 재분석 없이 배치만 다시 생성)"
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl border border-border text-xs font-bold text-text-muted hover:text-white hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    <RefreshCw size={14} className={isProcessing ? 'animate-spin' : ''} /> 재생성
                  </button>
                  <button
                    onClick={handleUndo}
                    disabled={undoStack.current.length === 0}
                    title="실행취소 (Ctrl+Z)"
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl border border-border text-xs font-bold text-text-muted hover:text-white hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    <Undo2 size={14} /> 되돌리기
                  </button>
                  <div className="flex rounded-xl border border-border overflow-hidden text-xs font-bold">
                    <button
                      onClick={() => setViewMode('3d')}
                      className={`px-3 py-1.5 transition-colors ${viewMode === '3d' ? 'bg-primary text-white' : 'text-text-muted hover:bg-white/5'}`}
                    >
                      3D
                    </button>
                    <button
                      onClick={() => setViewMode('2d')}
                      className={`px-3 py-1.5 transition-colors ${viewMode === '2d' ? 'bg-primary text-white' : 'text-text-muted hover:bg-white/5'}`}
                    >
                      2D
                    </button>
                  </div>
                  <span className="bg-accent/10 text-accent px-3 py-1 rounded-full text-xs font-bold border border-accent/20">
                    성공: {result.summary.total_placed}
                  </span>
                  {result.summary.total_failed > 0 && (
                    <span className="bg-danger/10 text-danger px-3 py-1 rounded-full text-xs font-bold border border-danger/20">
                      실패: {result.summary.total_failed}
                    </span>
                  )}
                </div>
              </div>

              <div id="canvas-container" className="flex-grow flex items-center justify-center border border-border/50 bg-[#0a0f1d] rounded-2xl overflow-hidden relative">
                {viewMode === '3d' ? (
                  <ThreeViewer
                    roomPolygon={result?.room_polygon_mm || []}
                    placedObjects={localPlaced}
                    detectedObjects={result?.equipment_detected || []}
                    walls={walls}
                    floorPlanUrl={floorPlanUrl ?? undefined}
                    roomBboxPx={result?.room_bbox_px}
                    imageSizePx={result?.image_size_px}
                    selectedIndex={selectedObjectIndex}
                    selectedWallId={selectedWallId}
                    onObjectClick={(idx) => {
                      setSelectedObjectIndex(prev => prev === idx ? null : idx);
                      setSelectedWallId(null);
                    }}
                    onWallClick={(id) => {
                      setSelectedWallId(prev => prev === id ? null : id);
                      setSelectedObjectIndex(null);
                    }}
                    onObjectMove={handleObjectMove}
                    onWallMove={handleWallMove}
                  />
                ) : (
                  <FloorView2D
                    roomPolygon={result?.room_polygon_mm || []}
                    placedObjects={localPlaced}
                    detectedObjects={result?.equipment_detected || []}
                    walls={walls}
                    selectedIndex={selectedObjectIndex}
                    onObjectClick={(idx) => {
                      setSelectedObjectIndex(prev => prev === idx ? null : idx);
                      setSelectedWallId(null);
                    }}
                  />
                )}
              </div>

              {/* 3D drag hint */}
              {viewMode === '3d' && (
                <p className="text-xs text-text-muted mt-3 text-center">
                  오브젝트·가벽을 드래그하여 위치 조정 • 배경 드래그로 시점 변경
                </p>
              )}
            </div>

            {/* Right Panel */}
            <div className="flex flex-col gap-6 overflow-y-auto max-h-[90vh]">

              {/* 가벽 설치 */}
              <div className="glass-card p-6">
                <h4 className="font-bold mb-4 flex items-center gap-2">
                  <PlusSquare size={20} className="text-accent" /> 가벽 설치
                </h4>
                <div className="flex gap-2 mb-4">
                  {WALL_PRESETS.map(({ label, length }) => (
                    <button
                      key={label}
                      onClick={() => handleAddWall(length)}
                      className="flex-1 py-2 rounded-xl border border-accent/40 text-accent text-sm font-bold hover:bg-accent/10 transition-colors"
                    >
                      + {label}
                    </button>
                  ))}
                </div>

                {/* Wall list */}
                {walls.length > 0 ? (
                  <div className="space-y-2">
                    {walls.map((wall) => {
                      const isSel = selectedWallId === wall.id;
                      return (
                        <div
                          key={wall.id}
                          onClick={() => {
                            setSelectedWallId(prev => prev === wall.id ? null : wall.id);
                            setSelectedObjectIndex(null);
                          }}
                          className={`flex items-center justify-between p-3 rounded-xl border cursor-pointer transition-all ${
                            isSel ? 'border-yellow-400/60 bg-yellow-400/10' : 'border-white/5 bg-black/20 hover:bg-white/5'
                          }`}
                        >
                          <div>
                            <span className="text-sm font-bold text-text-main">
                              가벽 {(wall.length / 1000).toFixed(0)}m
                            </span>
                            <div className="text-xs text-text-muted">
                              회전 {wall.rotation}° · {wall.thickness}mm 두께
                            </div>
                          </div>
                          <div className="flex gap-1">
                            <button
                              title="90도 회전"
                              onClick={(e) => { e.stopPropagation(); handleWallRotate(wall.id); }}
                              className="p-1.5 rounded-lg hover:bg-white/10 transition-colors text-text-muted hover:text-white"
                            >
                              <RotateCcw size={14} />
                            </button>
                            <button
                              title="삭제"
                              onClick={(e) => { e.stopPropagation(); handleWallDelete(wall.id); }}
                              className="p-1.5 rounded-lg hover:bg-red-500/20 transition-colors text-text-muted hover:text-red-400"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-text-muted italic">
                    버튼을 눌러 가벽을 추가하세요. 3D 뷰에서 드래그로 위치를 조정할 수 있습니다.
                  </p>
                )}
              </div>

              {/* Placed Objects List */}
              <div className="glass-card p-6">
                <h4 className="font-bold mb-4 flex items-center gap-2">
                  <Package size={20} className="text-primary" /> 배치된 오브젝트
                  <span className="ml-auto text-xs text-text-muted font-normal">클릭·드래그 가능</span>
                </h4>
                <div className="space-y-2">
                  {localPlaced.map((p, i) => {
                    const isSelected = selectedObjectIndex === i;
                    const color = OBJECT_COLORS[p.object_type] ?? '#ec4899';
                    const name  = OBJECT_NAMES[p.object_type]  ?? p.object_type;
                    return (
                      <button
                        key={i}
                        onClick={() => {
                          setSelectedObjectIndex(prev => prev === i ? null : i);
                          setSelectedWallId(null);
                        }}
                        className={`w-full text-left p-3 rounded-xl border transition-all ${
                          isSelected
                            ? 'border-yellow-400/60 bg-yellow-400/10'
                            : 'border-white/5 bg-black/20 hover:bg-white/5'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: color }} />
                          <span className="font-bold text-sm text-text-main">{name}</span>
                          {isSelected && <span className="ml-auto text-yellow-400 text-xs">선택됨</span>}
                        </div>
                        <div className="text-xs text-text-muted mt-1 pl-5">
                          기준점: {p.reference_point} · {p.bbox_mm[0]}×{p.bbox_mm[1]}mm (H {p.height_mm}mm)
                        </div>
                        {isSelected && (
                          <div className="text-xs text-text-muted mt-1 pl-5 leading-relaxed">
                            {p.placed_because}
                          </div>
                        )}
                      </button>
                    );
                  })}
                  {localPlaced.length === 0 && (
                    <div className="text-sm text-text-muted italic">배치된 오브젝트가 없습니다.</div>
                  )}
                </div>
              </div>

              {/* Failure Report */}
              {result.failed.length > 0 && (
                <div className="glass-card p-6 border-l-4 border-l-danger">
                  <h4 className="font-bold mb-4 flex items-center gap-2 text-danger">
                    <AlertCircle size={20} /> 미배치 리포트
                  </h4>
                  <div className="space-y-3">
                    {result.failed.map((f, i) => (
                      <div key={i} className="bg-black/20 p-3 rounded-xl border border-white/5">
                        <div className="font-bold text-sm text-text-main mb-1">
                          {OBJECT_NAMES[f.object_type] ?? f.object_type}
                        </div>
                        <div className="text-xs text-text-muted leading-relaxed">{f.reason}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Brand Standards */}
              <div className="glass-card p-6">
                <h4 className="font-bold mb-4 flex items-center gap-2">
                  <Settings size={20} className="text-primary" /> 브랜드 추출 기준
                </h4>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-white/5 p-3 rounded-xl">
                    <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Clearspace</div>
                    <div className="font-bold text-sm">{result.brand_standards.clearspace_mm}mm</div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl">
                    <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">복도 최소폭</div>
                    <div className="font-bold text-sm">{result.brand_standards.main_corridor_min_mm}mm</div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl col-span-2">
                    <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">추출 출처</div>
                    <div className="font-bold text-sm">{result.brand_standards.source}</div>
                  </div>
                </div>
              </div>

              <button className="btn-outline w-full flex items-center justify-center gap-2" onClick={() => setCurrentStep('upload')}>
                <ChevronLeft size={20} /> 처음으로 돌아가기
              </button>
            </div>
          </div>
        )}
      </main>

      <footer className="mt-20 text-center text-text-muted text-sm border-t border-border pt-8 fade-in">
        &copy; 2026 BuildUp AI • 2조 기획서 기반 아키텍처 구현
      </footer>
    </div>
  );
};

export default App;
