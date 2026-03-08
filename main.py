"""
AdaptAble FastAPI Backend
=========================
Production-grade backend for the AdaptAble inclusive learning platform.
Serves a Next.js frontend with Supabase auth, lesson processing,
Azure TTS, and Hugging Face integrations.

FIXED: Added /api prefix to all routes for frontend compatibility
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routers import auth, student, teacher, admin, translate

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("adaptable")


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AdaptAble backend starting up…")
    yield
    logger.info("AdaptAble backend shutting down.")


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="AdaptAble API",
    description="Backend for AdaptAble – AI-powered accessible education for Nigerian students.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
# FIXED: Added /api prefix to match frontend expectations
app.include_router(auth.router,    prefix="/api/auth",    tags=["Auth"])
app.include_router(student.router, prefix="/api/student", tags=["Student"])
app.include_router(teacher.router, prefix="/api/teacher", tags=["Teacher"])
app.include_router(admin.router,   prefix="/api/admin",   tags=["Admin"])
app.include_router(translate.router, prefix="/api/translate", tags=["Translation"])

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health_check():
    """Render health-check probe."""
    return {"status": "ok", "service": "adaptable-backend", "version": "1.0.0"}


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Root"])
async def root():
    """API root - returns basic info."""
    return {
        "service": "AdaptAble API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})