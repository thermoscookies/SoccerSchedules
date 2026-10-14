"""Events API endpoints"""
import asyncio
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Event, Division, Game, BracketStanding
from app.schemas.schemas import EventResponse, EventCreate, EventUpdate, EventWithStats, SeedingResponse, SeedingTeam
from app.services.scrape_service import ScrapeService
from app.scraper.gotsport import build_gotsport_group_url
from app.scheduler import get_scheduler

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=List[EventWithStats])
async def list_events(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all events with statistics"""
    # Get events
    result = await db.execute(
        select(Event)
        .order_by(Event.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    events = result.scalars().all()
    
    if not events:
        return []
    
    # Get counts for all events in a single query (fixes N+1 problem)
    event_ids = [e.id for e in events]
    
    # Count divisions per event
    div_counts = await db.execute(
        select(Division.event_id, func.count(Division.id))
        .where(Division.event_id.in_(event_ids))
        .group_by(Division.event_id)
    )
    div_counts_map = {event_id: count for event_id, count in div_counts}
    
    # Count games per event
    game_counts = await db.execute(
        select(Division.event_id, func.count(Game.id))
        .join(Game)
        .where(Division.event_id.in_(event_ids))
        .group_by(Division.event_id)
    )
    game_counts_map = {event_id: count for event_id, count in game_counts}
    
    # Get scheduler for next scrape times
    scheduler = await get_scheduler()
    
    # Build response with stats
    response = []
    for event in events:
        next_scrape_hours = scheduler.get_hours_until_next_scrape(event)
        
        response.append(EventWithStats(
            **event.__dict__,
            total_divisions=div_counts_map.get(event.id, 0),
            total_teams=0,  # TODO: implement when we track teams properly
            total_games=game_counts_map.get(event.id, 0),
            next_scrape_in_hours=next_scrape_hours,
        ))
    
    return response


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event: EventCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new event (will be scraped automatically)"""
    # Check if event already exists
    result = await db.execute(
        select(Event).where(Event.gotsport_event_id == event.gotsport_event_id)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Event with gotsport_event_id {event.gotsport_event_id} already exists"
        )
    
    # A direct group source avoids relying on event-wide group discovery.
    event_data = event.model_dump()
    group_id = event_data.pop("gotsport_group_id", None)
    group_url = event_data.pop("group_url", None)
    if group_url:
        event_data["url"] = group_url
    elif group_id:
        event_data["url"] = build_gotsport_group_url(event.gotsport_event_id, group_id)

    # Create event
    db_event = Event(**event_data)
    db.add(db_event)
    await db.commit()
    await db.refresh(db_event)
    
    # Data collection is opt-in for self-hosted deployments.
    if settings.SCRAPING_ENABLED:
        scheduler = await get_scheduler()
        asyncio.create_task(scheduler.trigger_manual_scrape(db_event.id, force=True))
    
    return db_event


@router.get("/{event_id}", response_model=EventWithStats)
async def get_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific event by ID"""
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found"
        )
    
    # Count related entities (still better than original N+1 as these are simple aggregates)
    div_count = await db.scalar(
        select(func.count(Division.id)).where(Division.event_id == event.id)
    )
    game_count = await db.scalar(
        select(func.count(Game.id))
        .join(Division)
        .where(Division.event_id == event.id)
    )
    
    scheduler = await get_scheduler()
    next_scrape_hours = scheduler.get_hours_until_next_scrape(event)
    
    return EventWithStats(
        **event.__dict__,
        total_divisions=div_count or 0,
        total_teams=0,
        total_games=game_count or 0,
        next_scrape_in_hours=next_scrape_hours,
    )


@router.patch("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    event_update: EventUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an event"""
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found"
        )
    
    # Update fields
    update_data = event_update.model_dump(exclude_unset=True)
    group_id = update_data.pop("gotsport_group_id", None)
    group_url = update_data.pop("group_url", None)
    if group_url:
        update_data["url"] = group_url
    elif group_id:
        update_data["url"] = build_gotsport_group_url(event.gotsport_event_id, group_id)
    for field, value in update_data.items():
        setattr(event, field, value)
    
    await db.commit()
    await db.refresh(event)
    
    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an event and all related data"""
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found"
        )
    
    await db.delete(event)
    await db.commit()
    
    return None


@router.get("/{event_id}/divisions/{division_id}/seeding", response_model=SeedingResponse)
async def get_division_seeding(
    event_id: int,
    division_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Calculate and return seeding for a division based on bracket standings
    
    Seeding calculation:
    1. Identify bracket winners (highest points in each bracket)
    2. Sort bracket winners by: PTS (desc), GD (desc), GF (desc), GA (asc)
    3. Get remaining teams (non-winners) and sort the same way
    4. Return bracket winners + top 6 remaining teams
    """
    try:
        # Verify event exists
        result = await db.execute(
            select(Event).where(Event.id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event {event_id} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching event {event_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    
    try:
        # Verify division exists and belongs to event
        result = await db.execute(
            select(Division).where(Division.id == division_id, Division.event_id == event_id)
        )
        division = result.scalar_one_or_none()
        if not division:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Division {division_id} not found in event {event_id}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching division {division_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )
    
    try:
        # Get all bracket standings for this division
        result = await db.execute(
            select(BracketStanding)
            .where(BracketStanding.division_id == division_id)
            .order_by(
                BracketStanding.bracket_name,
                BracketStanding.points.desc(),
                BracketStanding.goal_difference.desc(),
                BracketStanding.goals_for.desc(),
                BracketStanding.goals_against.asc()
            )
        )
        standings = result.scalars().all()
        
        if not standings:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No bracket standings found for division {division_id}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching bracket standings for division {division_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error fetching standings: {str(e)}"
        )
    
    try:
        # Deduplicate standings - keep the one with a real bracket name (not "Unknown Bracket")
        # Group by team name and keep the best entry
        team_standings = {}
        for standing in standings:
            team_name = standing.team_name
            if team_name not in team_standings:
                team_standings[team_name] = standing
            else:
                # If current entry has a real bracket name and existing doesn't, replace it
                existing = team_standings[team_name]
                if standing.bracket_name != "Unknown Bracket" and existing.bracket_name == "Unknown Bracket":
                    team_standings[team_name] = standing
                # If both have real bracket names or both are Unknown, keep the one with better stats
                elif standing.points > existing.points or \
                     (standing.points == existing.points and standing.goal_difference > existing.goal_difference):
                    team_standings[team_name] = standing
        
        # Use deduplicated standings
        deduplicated_standings = list(team_standings.values())
        
        # Group standings by bracket and identify winners
        brackets = {}
        for standing in deduplicated_standings:
            if standing.bracket_name not in brackets:
                brackets[standing.bracket_name] = []
            brackets[standing.bracket_name].append(standing)
        
        # Get bracket winners (first team in each bracket - already sorted)
        bracket_winners = []
        remaining_teams = []
        
        for bracket_name, bracket_teams in brackets.items():
            if bracket_teams:
                # Sort teams within this bracket
                bracket_teams.sort(key=lambda t: (
                    -t.points,
                    -t.goal_difference,
                    -t.goals_for,
                    t.goals_against,
                    t.team_name
                ))
                # First team is the winner
                winner = bracket_teams[0]
                bracket_winners.append({
                    'team': winner,
                    'bracket': bracket_name
                })
                # Rest are remaining teams
                for team in bracket_teams[1:]:
                    remaining_teams.append({
                        'team': team,
                        'bracket': bracket_name
                    })
        
        # Sort bracket winners by seeding criteria
        def sort_key(item):
            team = item['team']
            return (
                -team.points,  # Higher points first
                -team.goal_difference,  # Higher GD first
                -team.goals_for,  # Higher GF first
                team.goals_against,  # Lower GA first
                team.team_name  # Alphabetical as final tiebreaker
            )
        
        bracket_winners.sort(key=sort_key)
        remaining_teams.sort(key=sort_key)
        
        # Convert to response format
        winner_responses = []
        for rank, winner_data in enumerate(bracket_winners, start=1):
            team = winner_data['team']
            winner_responses.append(SeedingTeam(
                rank=rank,
                team_name=team.team_name,
                bracket_name=winner_data['bracket'],
                points=team.points,
                goal_difference=team.goal_difference,
                goals_for=team.goals_for,
                goals_against=team.goals_against,
                wins=team.wins,
                draws=team.draws,
                losses=team.losses,
                played=team.played,
                is_bracket_winner=True
            ))
        
        # Take top 6 remaining teams
        remaining_responses = []
        start_rank = len(bracket_winners) + 1
        for idx, remaining_data in enumerate(remaining_teams[:6]):
            team = remaining_data['team']
            remaining_responses.append(SeedingTeam(
                rank=start_rank + idx,
                team_name=team.team_name,
                bracket_name=remaining_data['bracket'],
                points=team.points,
                goal_difference=team.goal_difference,
                goals_for=team.goals_for,
                goals_against=team.goals_against,
                wins=team.wins,
                draws=team.draws,
                losses=team.losses,
                played=team.played,
                is_bracket_winner=False
            ))
        
        return SeedingResponse(
            division_id=division_id,
            division_name=division.name,
            bracket_winners=winner_responses,
            top_remaining=remaining_responses
        )
    except Exception as e:
        logger.error(f"Error processing seeding data for division {division_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing seeding: {str(e)}"
        )
