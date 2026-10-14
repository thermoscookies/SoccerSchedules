"""Smart scheduler for scraping events based on tournament timing"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import engine
from app.models.models import Event
from app.scraper.gotsport import GotsportCaptchaError
from app.services.scrape_service import ScrapeService

logger = logging.getLogger(__name__)


def normalize_to_datetime_utc(date_or_datetime, end_of_day: bool = False) -> datetime:
    """
    Convert a date or datetime object to timezone-aware datetime in UTC.
    
    Args:
        date_or_datetime: date or datetime object to convert
        end_of_day: If True and input is a date, return end of day (23:59:59)
    
    Returns:
        datetime object in UTC timezone
    """
    if isinstance(date_or_datetime, datetime):
        # It's already a datetime
        if date_or_datetime.tzinfo is None:
            return date_or_datetime.replace(tzinfo=timezone.utc)
        return date_or_datetime
    else:
        # It's a date object, convert to datetime
        if end_of_day:
            return datetime.combine(date_or_datetime, datetime.max.time(), tzinfo=timezone.utc)
        else:
            return datetime.combine(date_or_datetime, datetime.min.time(), tzinfo=timezone.utc)


class SmartScheduler:
    """
    Smart scheduler that adjusts scraping frequency based on tournament timing:
    - Daily (24h) by default
    - Hourly (1h) starting the day before first game until tournament ends
    - Back to daily after tournament ends
    """
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.session_maker = async_sessionmaker(engine, expire_on_commit=False)
        self.scheduled_jobs: Dict[int, str] = {}  # event_id -> job_id mapping
        self.captcha_cooldown_until: Optional[datetime] = None
    
    async def start(self):
        """Start the scheduler"""
        logger.info("Starting smart scheduler...")
        self.scheduler.start()
        
        # Schedule the job that checks and updates all event schedules
        self.scheduler.add_job(
            self._check_and_scrape_events,
            trigger=IntervalTrigger(minutes=30),  # Check every 30 minutes
            id='check_events',
            name='Check and scrape events',
            replace_existing=True,
        )
        
        logger.info("Smart scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        logger.info("Stopping smart scheduler...")
        self.scheduler.shutdown()
    
    async def _check_and_scrape_events(self):
        """Check all active events and scrape if needed"""
        logger.info("Checking events for scraping...")

        if self._captcha_cooldown_active():
            logger.warning("Skipping automatic GotSport checks during CAPTCHA cooldown")
            return
        
        async with self.session_maker() as session:
            # Get all active events
            result = await session.execute(
                select(Event).where(Event.status == 'active')
            )
            events = result.scalars().all()
            
            logger.info(f"Found {len(events)} active events to check")
            
            for event in events:
                if self._captcha_cooldown_active():
                    logger.warning("Stopping automatic event checks during CAPTCHA cooldown")
                    break
                try:
                    should_scrape = await self._should_scrape_event(event)
                    
                    if should_scrape:
                        logger.info(f"Scraping event {event.id}: {event.name}")
                        await self._scrape_event(event.id, event.url)
                    else:
                        interval = self._get_scrape_interval(event)
                        logger.debug(f"Skipping event {event.id}, interval: {interval}h")
                        
                except Exception as e:
                    logger.error(f"Error checking event {event.id}: {e}", exc_info=True)

    def _captcha_cooldown_active(self) -> bool:
        return bool(
            self.captcha_cooldown_until
            and datetime.now(timezone.utc) < self.captcha_cooldown_until
        )

    def _record_captcha_failure(self) -> None:
        self.captcha_cooldown_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.CAPTCHA_COOLDOWN_MINUTES
        )
        logger.warning(
            "GotSport CAPTCHA cooldown set for %s",
            self.captcha_cooldown_until.isoformat(),
        )
    
    async def _should_scrape_event(self, event: Event) -> bool:
        """Determine if an event should be scraped now"""
        if not event.last_scraped_at:
            # Never scraped, should scrape
            return True
        
        # Calculate how long since last scrape (make both timezone-aware)
        now = datetime.now(timezone.utc)
        last_scraped = event.last_scraped_at
        if last_scraped.tzinfo is None:
            last_scraped = last_scraped.replace(tzinfo=timezone.utc)
        
        hours_since_scrape = (now - last_scraped).total_seconds() / 3600
        
        # Get the appropriate interval for this event
        interval_hours = self._get_scrape_interval(event)
        
        # Scrape if enough time has passed
        return hours_since_scrape >= interval_hours
    
    def _get_scrape_interval(self, event: Event) -> int:
        """
        Get scrape interval in hours based on tournament timing
        - Daily (24h) by default
        - Hourly (1h) from day before first game until tournament ends
        - Daily (24h) after tournament ends
        """
        if not event.start_date or not event.end_date:
            # No dates available, use default daily interval
            return settings.DEFAULT_SCRAPE_INTERVAL_HOURS
        
        now = datetime.now(timezone.utc)
        
        # Convert dates to datetime objects in UTC using helper
        start_date = normalize_to_datetime_utc(event.start_date)
        end_date = normalize_to_datetime_utc(event.end_date, end_of_day=True)
        
        # Calculate day before tournament starts
        day_before_start = start_date - timedelta(days=1)
        
        # Logic:
        # - Before (day before tournament): daily (24h)
        # - From (day before) through (end date): hourly (1h)
        # - After tournament: daily (24h)
        
        if now < day_before_start:
            # Before tournament window
            logger.debug(f"Event {event.id}: Before tournament window (now: {now}, day_before: {day_before_start})")
            return settings.DEFAULT_SCRAPE_INTERVAL_HOURS
        elif now <= end_date:
            # During tournament window (including day before)
            logger.info(f"Event {event.id}: IN TOURNAMENT WINDOW - using hourly interval (now: {now}, start: {start_date}, end: {end_date})")
            return settings.TOURNAMENT_SCRAPE_INTERVAL_HOURS
        else:
            # After tournament
            logger.debug(f"Event {event.id}: After tournament (now: {now}, end: {end_date})")
            return settings.DEFAULT_SCRAPE_INTERVAL_HOURS
    
    def get_next_scrape_time(self, event: Event) -> Optional[datetime]:
        """Calculate when the event will be scraped next"""
        if not event.last_scraped_at:
            return datetime.now(timezone.utc)  # Scrape ASAP
        
        interval_hours = self._get_scrape_interval(event)
        last_scraped = event.last_scraped_at
        if last_scraped.tzinfo is None:
            last_scraped = last_scraped.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        time_since_last_scrape = (now - last_scraped).total_seconds() / 3600
        
        # If the time since last scrape is greater than the current interval,
        # we need to scrape now (e.g., interval changed from 24h to 1h)
        if time_since_last_scrape >= interval_hours:
            logger.info(f"Event {event.id}: Time since last scrape ({time_since_last_scrape:.1f}h) >= interval ({interval_hours}h), scheduling for now")
            return now
        
        next_scrape = last_scraped + timedelta(hours=interval_hours)
        return next_scrape
    
    def get_hours_until_next_scrape(self, event: Event) -> Optional[float]:
        """Get hours until next scrape for an event"""
        next_scrape = self.get_next_scrape_time(event)
        if not next_scrape:
            return None
        
        now = datetime.now(timezone.utc)
        if next_scrape.tzinfo is None:
            next_scrape = next_scrape.replace(tzinfo=timezone.utc)
        
        hours = (next_scrape - now).total_seconds() / 3600
        return max(0, round(hours, 1))  # Return float with 1 decimal place
    
    async def _scrape_event(self, event_id: int, event_url: str):
        """Scrape a single event"""
        if self._captcha_cooldown_active():
            logger.warning("Skipping event %s during CAPTCHA cooldown", event_id)
            return
        async with self.session_maker() as session:
            try:
                scrape_service = ScrapeService(session)
                await scrape_service.scrape_and_store_event(event_url, force=False)
                logger.info(f"Successfully scraped event {event_id}")
            except GotsportCaptchaError as e:
                self._record_captcha_failure()
                logger.error(f"Error scraping event {event_id}: {e}")
            except Exception as e:
                logger.error(f"Error scraping event {event_id}: {e}", exc_info=True)
    
    async def trigger_manual_scrape(self, event_id: int, force: bool = False) -> bool:
        """Manually trigger a scrape for a specific event"""
        if self._captcha_cooldown_active():
            logger.warning("Skipping manual event %s during CAPTCHA cooldown", event_id)
            return False
        async with self.session_maker() as session:
            result = await session.execute(
                select(Event).where(Event.id == event_id)
            )
            event = result.scalar_one_or_none()
            
            if not event:
                logger.error(f"Event {event_id} not found")
                return False
            
            try:
                scrape_service = ScrapeService(session)
                await scrape_service.scrape_and_store_event(event.url, force=force)
                logger.info(f"Manual scrape completed for event {event_id}")
                return True
            except GotsportCaptchaError as e:
                self._record_captcha_failure()
                logger.error(f"Error in manual scrape for event {event_id}: {e}")
                return False
            except Exception as e:
                logger.error(f"Error in manual scrape for event {event_id}: {e}", exc_info=True)
                return False


# Global scheduler instance
scheduler_instance: Optional[SmartScheduler] = None


async def get_scheduler() -> SmartScheduler:
    """Get the global scheduler instance"""
    global scheduler_instance
    if scheduler_instance is None:
        scheduler_instance = SmartScheduler()
        if settings.SCHEDULER_ENABLED and settings.SCRAPING_ENABLED:
            await scheduler_instance.start()
    return scheduler_instance


async def start_scheduler():
    """Start the global scheduler"""
    await get_scheduler()


def stop_scheduler():
    """Stop the global scheduler"""
    global scheduler_instance
    if scheduler_instance:
        scheduler_instance.stop()
