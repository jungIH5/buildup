# 작업 내역 (일별)

## 2026-04-01 — 프로젝트 초기 구성

- **Initial commit**: 전체 프로젝트 뼈대 생성 (48개 파일, 6,500+ 라인)
  - Backend: FastAPI 서버, 3개 AI Agent (`agent1_brand`, `agent2_floor`, `agent3_layout`), 핵심 유틸 (`spatial`, `pathfinder`, `violations`, `schemas`)
  - Frontend: React 18 + TypeScript + Three.js 3D 뷰어
  - 인프라: Docker Compose 구성
  - 기획서 PDF 포함

---

## 2026-04-02 — 기능 개선 및 수정 (2회 커밋)

### 오전 — 개선 및 수정
- 모든 Agent 로직 대폭 개선 (~1,200 라인 추가)
- Frontend `App.tsx` 전면 개편 (+449 라인)
- `FloorView2D.tsx` 신규 2D 뷰어 컴포넌트 추가 (224 라인)
- `ThreeViewer.tsx` 3D 뷰어 대규모 리팩토링
- 테스트용 PDF 파일 추가 (`test_brand.pdf`, `test_floor.pdf`)

### 오후 — 4월2일 개선
- Agent 1, 3 로직 추가 개선
- `spatial.py` 공간 계산 강화
- `pipeline.py` 라우터 확장 (+68 라인)
- 샘플 도면 생성 스크립트 `gen_small_floor.py` 신규 추가
- SVG 샘플 도면 `sample_floor_plan.svg` 추가

---

## 2026-04-03 — 도면 파싱 강화 및 문서화 (3회 커밋)

### 오전 (14시 중간확인)
- `App.tsx` 전면 리팩토링 (984→472줄, 약 40% 경량화)
- Agent 1, 2, 3 버그 수정 및 개선
- `spatial.py` 공간 처리 개선

### 오후 — md 추가
- `PROJECT.md` 문서 작성 (303 라인) — 프로젝트 개요, 아키텍처, Agent 파이프라인 문서화

### 저녁 — 마지막 커밋
- `agent2_floor.py` 대규모 개선 (+333 라인)
- DXF 도면 파일 파싱 지원 추가
- 샘플 DXF 파일 생성 스크립트 `generate_sample_dxf.py` 신규 추가
- L자형/직사각형 샘플 DXF 도면 (`sample_floor_Lshape.dxf`, `sample_floor_rect.dxf`) 추가
- `requirements.txt` 의존성 추가

---

## 요약

| 날짜 | 주요 작업 |
|------|-----------|
| 4/1 | 프로젝트 초기 구성 (전체 뼈대) |
| 4/2 | 기능 완성도 향상, 2D 뷰어 추가, 샘플 도면 생성 |
| 4/3 | 코드 정리(경량화), DXF 지원 확장, 문서화 |
