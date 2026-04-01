import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { 
  Upload, 
  MapPin, 
  Eye, 
  Layout, 
  CheckCircle2, 
  AlertCircle, 
  ArrowRight, 
  ChevronLeft,
  Settings,
  Package,
  Layers,
  Box
} from 'lucide-react';
import './globals.css';

// Types
type Step = 'upload' | 'mark' | 'review' | 'result';

interface Equipment {
  id: string;
  type: string;
  x: number;
  y: number;
  source: 'auto' | 'user';
}

interface Placement {
  object_type: string;
  position_mm: [number, number];
  rotation_deg: number;
  bbox_mm: [number, number]; // Added this
  reference_point: string;
  placed_because: string;
}

interface PipelineResult {
  placed: Placement[];
  failed: any[];
  violations: any[];
  glb_blocked: boolean;
  disclaimer_items: string[];
  summary: any;
  brand_standards: any;
  room_polygon_mm?: [number, number][];
  equipment_detected?: any[];
  image_size_px?: [number, number];
  room_bbox_px?: [number, number, number, number];
  floor_plan_png?: string;
}

import ThreeViewer from './components/ThreeViewer';

const App: React.FC = () => {
  const [currentStep, setCurrentStep] = useState<Step>('upload');
  const [brandManual, setBrandManual] = useState<File | null>(null);
  const [floorPlan, setFloorPlan] = useState<File | null>(null);
  const [floorPlanUrl, setFloorPlanUrl] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult] = useState<PipelineResult | null>(null);

  const handleProcess = async () => {
    if (!floorPlan && !brandManual) return;

    setIsProcessing(true);
    const formData = new FormData();
    if (brandManual) {
      formData.append('brand_manual', brandManual);
    }
    if (floorPlan) {
      formData.append('floor_plan', floorPlan);
    }

    try {
      const response = await axios.post('http://localhost:8000/api/pipeline/run', formData);
      setResult(response.data);
      if (floorPlan) {
        setFloorPlanUrl(URL.createObjectURL(floorPlan));
      }
      setCurrentStep('result');
    } catch (error) {
      console.error('Pipeline failed:', error);
      alert('공정 처리 중 오류가 발생했습니다. 백엔드 서버 확인이 필요합니다.');
    } finally {
      setIsProcessing(false);
    }
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
          {(['upload', 'mark', 'review', 'result'] as Step[]).map((step, idx) => (
            <div 
              key={step} 
              className={`step ${currentStep === step ? 'active' : ''} ${idx < ['upload', 'mark', 'review', 'result'].indexOf(currentStep) ? 'done' : ''}`}
            >
              {idx + 1}. {step.charAt(0).toUpperCase() + step.slice(1)}
            </div>
          ))}
        </div>
      </header>

      <main className="grid grid-cols-1 gap-8 fade-in">
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
              <p className="text-text-muted text-sm mb-8">도면 이미지(PNG, JPG) 또는 PDF 파일 (공간 분석용)</p>
              
              <label className="w-full border-2 border-dashed border-border rounded-2xl p-10 cursor-pointer hover:border-accent/50 transition-all group">
                <input type="file" className="hidden" accept="image/*,.pdf" onChange={(e) => setFloorPlan(e.target.files?.[0] || null)} />
                <div className="flex flex-col items-center gap-2">
                  <Upload size={32} className="text-text-muted group-hover:text-accent transition-colors" />
                  <span className="font-medium text-text-muted">{floorPlan ? floorPlan.name : '파일 선택 또는 드래그'}</span>
                </div>
              </label>
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

        {currentStep === 'result' && result && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 glass-card p-6 min-h-[600px] flex flex-col">
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-xl font-bold flex items-center gap-2">
                  <Package className="text-primary" /> 3D 배치 레이아웃
                </h3>
                <div className="flex gap-2">
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
              
              <div id="canvas-container" className="flex-grow flex items-center justify-center border border-border/50 bg-[#0a0f1d] rounded-2xl overflow-hidden relative group">
                <ThreeViewer
                  roomPolygon={result?.room_polygon_mm || []}
                  placedObjects={result?.placed || []}
                  detectedObjects={result?.equipment_detected || []}
                  floorPlanUrl={floorPlanUrl ?? undefined}
                  roomBboxPx={result?.room_bbox_px}
                  imageSizePx={result?.image_size_px}
                />
              </div>
            </div>

            <div className="flex flex-col gap-6">
              {/* Failure Report */}
              <div className="glass-card p-6 border-l-4 border-l-danger">
                <h4 className="font-bold mb-4 flex items-center gap-2 text-danger">
                  <AlertCircle size={20} /> 미배치 오브젝트 리포트
                </h4>
                <div className="space-y-3">
                  {result.failed.length > 0 ? result.failed.map((f, i) => (
                    <div key={i} className="bg-black/20 p-3 rounded-xl border border-white/5">
                      <div className="font-bold text-sm text-text-main mb-1">{f.object_type}</div>
                      <div className="text-xs text-text-muted leading-relaxed">{f.reason}</div>
                    </div>
                  )) : (
                    <div className="text-sm text-text-muted italic">모든 오브젝트가 성공적으로 배치되었습니다.</div>
                  )}
                </div>
              </div>

              {/* Brand Standards Summary */}
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
