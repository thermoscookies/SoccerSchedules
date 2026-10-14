"""Main API router combining all endpoint routers"""
from fastapi import APIRouter

from app.api.v1.endpoints import events, schedules, scraping, browser_import

api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(schedules.router, prefix="/schedules", tags=["schedules"])
api_router.include_router(scraping.router, prefix="/scraping", tags=["scraping"])
api_router.include_router(browser_import.router, prefix="/browser-imports", tags=["browser-imports"])


@api_router.get("/")
async def api_root():
    """API v1 root endpoint"""
    return {
        "message": "Soccer Schedules API v1",
        "endpoints": {
            "events": "/api/v1/events",
            "schedules": "/api/v1/schedules",
            "scraping": "/api/v1/scraping",
        }
    }
