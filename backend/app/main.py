"""Main FastAPI application entry point"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router
from app.scheduler import start_scheduler, stop_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Set specific loggers to INFO level
logging.getLogger('app.scraper').setLevel(logging.INFO)
logging.getLogger('app.services').setLevel(logging.INFO)
logging.getLogger('app.scheduler').setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    print("🚀 Starting Soccer Schedules API...")
    if settings.SCHEDULER_ENABLED and settings.SCRAPING_ENABLED:
        await start_scheduler()
        print("✅ Scheduler started")
    else:
        print("⚠️ Scheduler disabled")
    yield
    # Shutdown
    print("👋 Shutting down Soccer Schedules API...")
    if settings.SCHEDULER_ENABLED and settings.SCRAPING_ENABLED:
        stop_scheduler()
        print("✅ Scheduler stopped")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Soccer tournament schedule scraping and display API",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Soccer Schedules API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}
