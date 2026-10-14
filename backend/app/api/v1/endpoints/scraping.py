"""Scraping control API endpoints"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Event, ScrapeLog
from app.schemas.schemas import (
    ScrapeRequest,
    ScrapeResponse,
    ScrapeLogResponse,
    SchedulerStatus,
)
from app.services.scrape_service import ScrapeService
from app.scheduler import get_scheduler

router = APIRouter()


@router.post("/trigger", response_model=ScrapeResponse)
async def trigger_scrape(
    scrape_request: ScrapeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a scrape for an event"""
    if not settings.SCRAPING_ENABLED:
        raise HTTPException(status_code=403, detail="Scraping is disabled by server configuration")
    # Verify event exists
    result = await db.execute(
        select(Event).where(Event.id == scrape_request.event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {scrape_request.event_id} not found")
    
    # Trigger scrape in background
    scheduler = await get_scheduler()
    background_tasks.add_task(
        scheduler.trigger_manual_scrape,
        scrape_request.event_id,
        scrape_request.force
    )
    
    return ScrapeResponse(
        message=f"Scrape triggered for event {event.name}",
        status="pending",
    )


@router.get("/logs/{event_id}", response_model=List[ScrapeLogResponse])
async def get_scrape_logs(
    event_id: int,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Get scrape logs for an event"""
    result = await db.execute(
        select(ScrapeLog)
        .where(ScrapeLog.event_id == event_id)
        .order_by(ScrapeLog.scrape_started_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    
    return [ScrapeLogResponse(**log.__dict__) for log in logs]


@router.get("/status", response_model=List[SchedulerStatus])
async def get_scheduler_status(
    db: AsyncSession = Depends(get_db),
):
    """Get scheduler status for all active events"""
    # Get all active events
    result = await db.execute(
        select(Event).where(Event.status == 'active')
    )
    events = result.scalars().all()
    
    scheduler = await get_scheduler()
    
    status_list = []
    for event in events:
        interval = scheduler._get_scrape_interval(event)
        next_scrape_hours = scheduler.get_hours_until_next_scrape(event)
        
        status_list.append(SchedulerStatus(
            event_id=event.id,
            event_name=event.name,
            last_scraped=event.last_scraped_at,
            next_scrape_in_hours=next_scrape_hours or 0,
            scrape_interval_hours=interval,
        ))
    
    return status_list
