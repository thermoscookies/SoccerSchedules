"""Explicit, authenticated imports initiated from a user's GotSport browser page."""
import re
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import BrowserImport, Event
from app.scraper.gotsport import GotsportCaptchaError, GotsportScraper
from app.services.scrape_service import ScrapeService

router = APIRouter()


class BrowserImportRequest(BaseModel):
    source_url: HttpUrl
    html: str = Field(..., min_length=256, max_length=settings.BROWSER_IMPORT_MAX_HTML_BYTES)


class BrowserImportResponse(BaseModel):
    event_id: int
    division_id: int
    provider: str
    games_seen: int
    games_created: int
    games_updated: int
    completed_results: int
    import_id: int


def _authorize(request: Request, token: Optional[str] = Header(None, alias="X-SoccerSchedules-Import-Token")) -> None:
    configured = settings.BROWSER_IMPORT_TOKEN
    if not configured or not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid browser import token")
    origin = request.headers.get("origin")
    if origin and origin not in settings.CORS_ORIGINS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed")


def _source_parts(source_url: str) -> tuple[str, str]:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname != "system.gotsport.com":
        raise HTTPException(status_code=422, detail="Only HTTPS system.gotsport.com URLs are accepted")
    match = re.search(r"/org_event/events/(\d+)", parsed.path)
    group = re.search(r"(?:^|&)group=(\d+)(?:&|$)", parsed.query)
    if not match or not group:
        raise HTTPException(status_code=422, detail="Source URL must identify a GotSport event and group")
    return match.group(1), group.group(1)


def _event_name(html: str, event_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(".navbar-brand-event") or soup.select_one("title")
    return node.get_text(" ", strip=True) if node else f"GotSport Event {event_id}"


@router.post("", response_model=BrowserImportResponse)
async def import_browser_html(
    payload: BrowserImportRequest = Body(...),
    request: Request = None,
    _authorized: None = Depends(_authorize),
    db: AsyncSession = Depends(get_db),
):
    event_id, group_id = _source_parts(str(payload.source_url))
    html = payload.html
    scraper = GotsportScraper()
    if scraper._is_captcha_interstitial(str(payload.source_url), html):
        raise HTTPException(status_code=422, detail="CAPTCHA/interstitial HTML cannot be imported")
    if not re.search(rf"/org_event/events/{re.escape(event_id)}(?:[\"'/?>])", html):
        raise HTTPException(status_code=422, detail="HTML belongs to a different GotSport event")
    if group_id not in html:
        raise HTTPException(status_code=422, detail="HTML does not identify the requested GotSport group")

    division_data = scraper._direct_group_division(str(payload.source_url))
    if not division_data:
        raise HTTPException(status_code=422, detail="Could not identify the GotSport group")
    try:
        games = await scraper._scrape_division_schedule(
            None, division_data["schedule_url"], division_data, html_content=html
        )
        standings = await scraper._scrape_bracket_standings(
            None, division_data["schedule_url"], division_data, html_content=html
        )
    except GotsportCaptchaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not games:
        raise HTTPException(status_code=422, detail="HTML contains no schedule games")
    if any(not game.get("game_number") or not game.get("home_team_name") or not game.get("away_team_name") for game in games):
        raise HTTPException(status_code=422, detail="HTML contains incomplete game rows")
    numbers = [game["game_number"] for game in games]
    if len(numbers) != len(set(numbers)):
        raise HTTPException(status_code=422, detail="HTML contains duplicate game numbers")

    result = await db.execute(select(Event).where(Event.gotsport_event_id == event_id, Event.gotsport_group_id == group_id))
    event = result.scalar_one_or_none()
    if not event:
        event = Event(
            gotsport_event_id=event_id,
            gotsport_group_id=group_id,
            name=division_data.get("name") or _event_name(html, event_id),
            url=str(payload.source_url),
            source_url=str(payload.source_url),
            provider="gotsport",
            status="active",
        )
        db.add(event)
        await db.flush()
    elif event.provider != "gotsport":
        raise HTTPException(status_code=409, detail="Event is owned by another provider")
    event.url = str(payload.source_url)
    event.gotsport_group_id = group_id
    event.source_url = str(payload.source_url)
    event.provider = "gotsport"

    service = ScrapeService(db)
    divisions = await service._store_divisions(event, [division_data])
    await service._store_teams(divisions, games)
    game_stats = await service._store_games(event, divisions, games)
    await service._store_bracket_standings(event, divisions, standings)
    now = datetime.now(timezone.utc)
    event.last_imported_at = now
    import_row = BrowserImport(
        event_id=event.id,
        provider="gotsport",
        source_url=str(payload.source_url),
        status="success",
        imported_at=now,
        games_seen=game_stats["total"],
        games_created=game_stats["created"],
        games_updated=game_stats["updated"],
        completed_results=sum(1 for game in games if game.get("home_score") is not None and game.get("away_score") is not None),
    )
    db.add(import_row)
    await db.commit()
    await db.refresh(import_row)
    return BrowserImportResponse(
        event_id=event.id,
        division_id=next(iter(divisions.values())).id,
        provider="gotsport",
        games_seen=game_stats["total"],
        games_created=game_stats["created"],
        games_updated=game_stats["updated"],
        completed_results=import_row.completed_results,
        import_id=import_row.id,
    )
