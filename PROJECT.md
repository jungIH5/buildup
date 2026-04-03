# BuildUp — AI 브랜드 메뉴얼 기반 자동 배치 솔루션

## 프로젝트 개요

브랜드 메뉴얼 PDF와 공간 도면을 입력하면, 3개의 AI Agent가 순차적으로 실행되어 내부 구조물(조형물, 진열대, 포토존 등)의 최적 배치를 자동으로 계산하고 3D/2D로 시각화하는 서비스.

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| Backend | FastAPI, Python 3.11 |
| AI | Claude Haiku 4.5 (Anthropic API) |
| 공간 계산 | Shapely, NetworkX, OpenCV, PyMuPDF |
| Frontend | React 18 + TypeScript, Vite, Three.js |
| 스타일 | Tailwind CSS |
| 인프라 | Docker Compose |
| DB (예정) | Supabase |

---

## 시스템 아키텍처

```
[사용자]
  ├── 브랜드 메뉴얼 PDF (선택)
  └── 도면 파일 (이미지/PDF) (선택)
        ↓
[Backend: FastAPI]
  ├── Agent 1: 브랜드 메뉴얼 분석
  ├── Agent 2: 도면 분석 + Dead Zone 생성
  └── Agent 3: 배치 의도 결정 → Shapely 계산
        ↓
[Frontend: React + Three.js]
  ├── 3D 뷰어 (Three.js)
  └── 2D 뷰어 (Canvas)
```

---

## Agent 파이프라인 상세

### Agent 1 — 브랜드 메뉴얼 분석 (`agent1_brand.py`)

브랜드 메뉴얼 PDF에서 공간 배치에 필요한 기준값을 추출한다.

**입력:** 브랜드 메뉴얼 PDF (bytes)  
**출력:** `BrandStandards` 스키마

**처리 방식:**
- 4MB 이하 PDF: Anthropic PDF Document API로 직접 전송
- 4MB 초과 PDF: PyMuPDF(fitz)로 텍스트 추출 후 텍스트 메시지로 전송
- 파싱 실패 또는 null 값은 기본값(DEFAULTS)으로 merge

**추출 항목:**
| 필드 | 설명 | 기본값 |
|------|------|--------|
| `clearspace_mm` | 오브젝트 간격 기준 | 500mm |
| `main_corridor_min_mm` | 주 통로 최소폭 | 900mm |
| `emergency_path_min_mm` | 비상 통로 최소폭 | 1200mm |
| `wall_clearance_mm` | 벽 이격 거리 | 300mm |
| `furniture_heights_mm` | 오브젝트별 높이 (dict) | 기본값 사용 |
| `character_orientation` | 캐릭터 방향 기준 | null |
| `prohibited_material` | 금지 재료 목록 | [] |

---

### Agent 2 — 도면 분석 (`agent2_floor.py`)

도면에서 방 외곽선, 설비 위치, 배치 기준점을 추출하고 Dead Zone을 생성한다.

**입력:** 도면 이미지/PDF, BrandStandards  
**출력:** `FloorAnalysis` 스키마 + 제약 조건 dict

**처리 경로 (우선순위):**

1. **경로 A — PDF 벡터 직접 추출** (CAD PDF 전용)
   - `fitz.get_drawings()`로 벡터 패스 파싱
   - 축척 텍스트 "1:XX" 자동 인식
   - 두꺼운 검정 선에서 방 외곽 폴리곤 추출
   - 색상 기반 설비 감지 (빨간색=스프링클러/소화기, 파란색=비상구)

2. **경로 B — Vision + OpenCV 폴백** (이미지 입력)
   - Claude Haiku Vision으로 설비 및 축척 감지
   - 축척 바 실측값 → scale_mm_per_px 계산
   - OpenCV Canny 엣지 → 방 외곽 폴리곤 추출

**공통 처리:**
- 사용자 마킹 설비 병합
- **NetworkX 격자 그래프**로 입구에서 각 기준점까지 보행 거리 계산
- 거리 비율로 `zone_label` 할당:
  - 0~33%: `entrance_zone`
  - 33~67%: `mid_zone`
  - 67~100%: `deep_zone`

**생성 기준점 (`ReferencePoint`):**
- `center` — 방 중심
- `north_wall_mid`, `south_wall_mid`, `east_wall_mid`, `west_wall_mid` — 각 벽 중앙
- `entrance` — 비상구 감지 시 그 위치, 없으면 남쪽 벽 기본값

**Dead Zone 생성:**
- 비상구: `emergency_path_min_mm` 반경 사각형 + 내측 통로 확보 구역
- 기타 설비: `wall_clearance_mm` 반경 사각형

---

### Agent 3 — 배치 결정 (`agent3_layout.py`)

LLM이 배치 "의도"(방향과 기준점)를 결정하고, Shapely가 실제 좌표를 계산한다.

**핵심 설계 원칙: 좌표/mm 값 LLM 출력 금지**
- Agent 3은 `reference_point` 이름과 `direction`만 출력
- 실제 좌표 계산은 `spatial.py`(Shapely)에서만 수행
- Pydantic `field_validator`로 숫자 패턴 감지 시 즉시 차단 (Circuit Breaker)

**`PlacementIntent` 출력 필드:**
| 필드 | 예시 | 설명 |
|------|------|------|
| `object_type` | `character_bbox` | 배치할 오브젝트 코드명 |
| `reference_point` | `north_wall_mid` | 기준점 이름 |
| `direction` | `entrance_facing` | 방향 |
| `priority` | 1~10 | 낮을수록 먼저 배치 |
| `placed_because` | "동선 확보 위해..." | 배치 근거 |

**재시도 로직:**
- 배치 실패 오브젝트는 Agent 3 재호출 (최대 2회)
- 재호출 시 실패 오브젝트 + 성공 오브젝트 피드백 포함
- Circuit Breaker: JSON 검증 실패 시 최대 2회 재시도 후 포기

**사용자 요구사항 처리:**
- 한국어/영어 수량 패턴 파싱 (예: "상품진열대 8개" → `product_display` 8개)
- 요구사항은 기본 오브젝트 수량에 추가되는 방식 (덮어쓰기 아님)

---

## Shapely 배치 계산 (`spatial.py`)

Agent 3의 의도를 받아 실제 좌표를 계산한다.

**`try_place_object` 알고리즘:**
1. 기준점 위치에서 배치 시도
2. 충돌 시 8방향으로 50mm씩 밀어서 최대 30번 재시도
3. 유효한 후보 위치 최대 16개 수집
4. **분산 점수** 기준 최적 위치 선택 (기존 오브젝트와 가장 멀리 떨어진 곳)

**검증 체크:**
- 방 외곽 내 포함 여부
- Dead Zone 충돌 여부
- 기존 오브젝트 충돌 여부
- 접근성 체크: 4방향 중 최소 1면 이상 600mm 통행 공간 확보
- NetworkX 통로 연결성: 입구에서 방의 50% 이상 도달 가능한지 확인

**위반(Violation) 등급:**
- `BLOCKING`: GLB 출력 차단, 배치 실패 처리
- `WARNING`: 경고와 함께 배치 허용
- `INFO`: 정보 기록만

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 헬스 체크 |
| POST | `/api/upload/...` | 파일 업로드 |
| POST | `/api/pipeline/run` | 전체 파이프라인 실행 (Agent 1→2→3) |
| POST | `/api/pipeline/layout_only` | Agent 3만 재실행 (캐시 활용) |
| POST | `/api/pipeline/agent2/review` | Agent 2 결과만 반환 (사용자 확인용) |
| POST | `/api/export/...` | 결과 내보내기 |

### `POST /api/pipeline/run` 요청 (multipart/form-data)

| 필드 | 타입 | 설명 |
|------|------|------|
| `brand_manual` | File (PDF) | 브랜드 메뉴얼 (선택) |
| `floor_plan` | File (이미지/PDF) | 도면 (선택) |
| `user_markings` | JSON 문자열 | 사용자 직접 마킹한 설비 위치 |
| `user_requirements` | 문자열 | 배치 요구사항 자유 텍스트 |

도면 없이 실행 시 기본 10m × 8m 샘플 공간으로 대체됨.

---

## 배치 가능 오브젝트

| 코드명 | 한국어명 | 크기 (가로×깊이) | 기본 높이 |
|--------|---------|----------------|---------|
| `character_bbox` | 캐릭터 조형물 | 800 × 800mm | - |
| `shelf_rental` | 렌탈 선반 | 600 × 400mm | - |
| `photo_zone` | 포토존 | 1500 × 1200mm | - |
| `banner_stand` | 배너 스탠드 | 600 × 200mm | - |
| `product_display` | 상품 진열대 | 900 × 600mm | - |

※ 높이는 브랜드 메뉴얼 추출값 우선, 없으면 `geometry_utils.py`의 기본값 사용

---

## 프론트엔드 (`App.tsx`)

**주요 기능:**
- 브랜드 메뉴얼(PDF) + 도면(이미지/PDF) 업로드
- 배치 요구사항 자유 텍스트 입력 (누적 칩 방식)
- **3D 뷰어** (Three.js): 방 외곽, 오브젝트, 가벽 드래그 이동
- **2D 뷰어** (Canvas): 평면도 오버레이
- **가벽 설치**: 1m/2m/3m 프리셋, 90° 회전, 삭제
- **Undo (Ctrl+Z)**: 오브젝트·가벽 이동 50단계 되돌리기
- **Agent 3 재실행**: Agent 1·2 결과 캐시 활용, 파일 재업로드 없이 배치만 재생성
- 배치 결과 패널: 성공/실패/위반 목록, 브랜드 추출 기준값

---

## 환경 설정

`.env` 파일 (`.env.example` 참고):

```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...
APP_ENV=development
CORS_ORIGINS=http://localhost:5173
```

---

## 실행 방법

### Docker (권장)

```bash
# 1. .env 파일 설정
cp .env.example .env
# .env 파일에 API 키 입력

# 2. 빌드 및 실행
docker-compose up --build

# 3. 접속
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### 로컬 실행

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

---

## 디렉토리 구조

```
buildup/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── main.py                  # FastAPI 진입점
│   ├── requirements.txt
│   ├── agents/
│   │   ├── agent1_brand.py      # 브랜드 메뉴얼 분석
│   │   ├── agent2_floor.py      # 도면 분석 + Dead Zone
│   │   └── agent3_layout.py     # 배치 의도 결정
│   ├── core/
│   │   ├── schemas.py           # Pydantic 스키마 + Circuit Breaker
│   │   ├── spatial.py           # Shapely 배치 계산
│   │   ├── violations.py        # 위반 체크
│   │   ├── geometry_utils.py    # 단위 변환, 기본값
│   │   └── pathfinder.py
│   └── routers/
│       ├── pipeline.py          # 파이프라인 API
│       ├── upload.py
│       └── export.py
└── frontend/
    ├── Dockerfile
    ├── src/
    │   ├── App.tsx              # 메인 컴포넌트
    │   └── components/
    │       ├── ThreeViewer.tsx  # 3D 뷰어
    │       └── FloorView2D.tsx  # 2D 뷰어
    └── package.json
```

---

## 향후 계획 (TODO)

- [ ] Supabase `furniture_standards` 테이블 연동 (현재 하드코딩)
- [ ] GLB 파일 내보내기 (trimesh 사용 예정)
- [ ] Agent 2 사용자 확인 단계 UI 구현
- [ ] 설비 수동 마킹 기능 (도면 위 클릭)
