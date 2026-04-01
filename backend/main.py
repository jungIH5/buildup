"""
main.py — FastAPI 진입점
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import anthropic
import os
from dotenv import load_dotenv

from routers import upload, pipeline, export

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시 Anthropic 클라이언트 초기화
    app.state.anthropic = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    yield
    # 종료 시 정리
    await app.state.anthropic.close()


app = FastAPI(
    title="BuildUp — 도면 기반 자동 배치 서비스",
    description="브랜드 메뉴얼 + 도면 → Agent 1~3 파이프라인으로 내부 구조물 자동 배치",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["Pipeline"])
app.include_router(export.router, prefix="/api/export", tags=["Export"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
