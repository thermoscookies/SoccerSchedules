"""Schedules API endpoints for querying games"""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.models import Event, Division, Game
from app.schemas.schemas import (
    GameDetailResponse,
    DivisionResponse,
    EventResponse,
    ScheduleResponse,
)

router = APIRouter()


@router.get("/{event_id}/teams")
async def get_event_teams(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get unique list of teams for an event"""
    from sqlalchemy import union
    
    # Get event
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    
    # Get unique home teams
    home_teams = select(Game.home_team_name.label('team_name')).join(Division).where(
        Division.event_id == event_id,
        Game.home_team_name.isnot(None)
    )
    
    # Get unique away teams
    away_teams = select(Game.away_team_name.label('team_name')).join(Division).where(
        Division.event_id == event_id,
        Game.away_team_name.isnot(None)
    )
    
    # Union and get unique sorted list
    teams_query = union(home_teams, away_teams).order_by('team_name')
    teams_result = await db.execute(teams_query)
    teams = [row[0] for row in teams_result]
    
    return {"teams": teams}


@router.get("/{event_id}/locations")
async def get_event_locations(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get unique list of locations/fields for an event"""
    # Get event
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    
    # Get unique field names
    locations_result = await db.execute(
        select(Game.field_name)
        .join(Division)
        .where(
            Division.event_id == event_id,
            Game.field_name.isnot(None)
        )
        .distinct()
        .order_by(Game.field_name)
    )
    locations = [row[0] for row in locations_result]
    
    return {"locations": locations}


@router.get("/{event_id:int}", response_model=ScheduleResponse)
async def get_event_schedule(
    event_id: int,
    division_id: Optional[int] = Query(None, description="Filter by division ID"),
    date_from: Optional[datetime] = Query(None, description="Filter games from this date"),
    date_to: Optional[datetime] = Query(None, description="Filter games until this date"),
    field_name: Optional[str] = Query(None, description="Filter by field name"),
    team_name: Optional[str] = Query(None, description="Filter by team name (home or away)"),
    status: Optional[str] = Query(None, description="Filter by game status"),
    page: Optional[int] = Query(None, ge=1, description="Page number (starts at 1). If not provided, returns all results."),
    page_size: Optional[int] = Query(None, ge=1, le=500, description="Games per page (max 500). If not provided, returns all results."),
    db: AsyncSession = Depends(get_db),
):
    """Get schedule for an event with optional filters"""
    # Get event
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    
    # Get ALL divisions for the event (not just from paginated games)
    divisions_result = await db.execute(
        select(Division).where(Division.event_id == event_id).order_by(Division.name)
    )
    all_divisions = divisions_result.scalars().all()
    
    # Build games query
    query = (
        select(Game, Division)
        .join(Division)
        .where(Division.event_id == event_id)
    )
    
    # Apply filters
    if division_id:
        query = query.where(Game.division_id == division_id)
    
    if date_from:
        query = query.where(Game.game_date >= date_from)
    
    if date_to:
        query = query.where(Game.game_date <= date_to)
    
    if field_name:
        query = query.where(Game.field_name.ilike(f"%{field_name}%"))
    
    if team_name:
        query = query.where(
            or_(
                Game.home_team_name.ilike(f"%{team_name}%"),
                Game.away_team_name.ilike(f"%{team_name}%")
            )
        )
    
    if status:
        query = query.where(Game.status == status)
    
    # Order by date and time
    query = query.order_by(Game.game_date.asc(), Game.game_time.asc())
    
    # Count total games for pagination
    from sqlalchemy import func as sql_func
    count_query = select(sql_func.count()).select_from(query.subquery())
    total_games = await db.scalar(count_query)
    
    # Apply pagination only if page and page_size are provided
    if page is not None and page_size is not None:
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
    
    # Execute query
    games_result = await db.execute(query)
    games_with_divisions = games_result.all()
    
    # Build response
    games_response = []
    for game, division in games_with_divisions:
        game_detail = GameDetailResponse(
            **game.__dict__,
            division_name=division.display_name or division.name,
            event_name=event.name,
                event_id=event.id,
        )
        games_response.append(game_detail)
    
    return ScheduleResponse(
        event=EventResponse(**event.__dict__),
        divisions=[DivisionResponse(**div.__dict__) for div in all_divisions],
        games=games_response,
        total_games=total_games or 0,
    )


@router.get("/all", response_model=ScheduleResponse)
async def get_all_active_schedule(db: AsyncSession = Depends(get_db)):
    """Return all active-event games for the combined schedule view."""
    events_result = await db.execute(select(Event).where(Event.status == "active").order_by(Event.name))
    events = events_result.scalars().all()
    if not events:
        raise HTTPException(status_code=404, detail="No active events found")
    event_ids = [event.id for event in events]
    divisions_result = await db.execute(select(Division).where(Division.event_id.in_(event_ids)).order_by(Division.name))
    divisions = divisions_result.scalars().all()
    query = (select(Game, Division, Event)
             .select_from(Division)
             .join(Game, Game.division_id == Division.id)
             .join(Event, Event.id == Division.event_id)
             .where(Event.id.in_(event_ids))
             .order_by(Game.game_date.asc(), Game.game_time.asc()))
    rows = (await db.execute(query)).all()
    games = [GameDetailResponse(**game.__dict__, division_name=division.display_name or division.name, event_name=event.name, event_id=event.id) for game, division, event in rows]
    return ScheduleResponse(
        event=EventResponse(**events[0].__dict__),
        events=[EventResponse(**event.__dict__) for event in events],
        divisions=[DivisionResponse(**division.__dict__) for division in divisions],
        games=games,
        total_games=len(games),
    )


@router.get("/division/{division_id}/games", response_model=List[GameDetailResponse])
async def get_division_games(
    division_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get all games for a specific division"""
    # Verify division exists
    div_result = await db.execute(
        select(Division).where(Division.id == division_id)
    )
    division = div_result.scalar_one_or_none()
    
    if not division:
        raise HTTPException(status_code=404, detail=f"Division {division_id} not found")
    
    # Get games
    games_result = await db.execute(
        select(Game)
        .where(Game.division_id == division_id)
        .order_by(Game.game_date.asc(), Game.game_time.asc())
    )
    games = games_result.scalars().all()
    
    # Get event for context
    event_result = await db.execute(
        select(Event).where(Event.id == division.event_id)
    )
    event = event_result.scalar_one()
    
    return [
        GameDetailResponse(
            **game.__dict__,
            division_name=division.display_name or division.name,
            event_name=event.name,
        )
        for game in games
    ]
