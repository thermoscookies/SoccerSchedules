"""Shared, database-backed favorite team management."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import Division, Event, FavoriteTeam, Game, Team
from app.schemas.schemas import FavoriteTeamCreate, FavoriteTeamResponse

router = APIRouter()


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


async def _team_options(db: AsyncSession):
    divisions = (await db.execute(select(Division, Event).join(Event).order_by(Event.name, Division.name))).all()
    teams = (await db.execute(select(Team))).scalars().all()
    games = (await db.execute(select(Game.division_id, Game.home_team_name, Game.away_team_name))).all()
    names = {}
    for division, event in divisions:
        names[division.id] = { }
    for team in teams:
        names.setdefault(team.division_id, {})[_key(team.name)] = team.name
    for division_id, home, away in games:
        for name in (home, away):
            if name and _key(name):
                names.setdefault(division_id, {}).setdefault(_key(name), name)
    return divisions, names


@router.get("", response_model=List[FavoriteTeamResponse])
async def list_favorites(db: AsyncSession = Depends(get_db)):
    divisions, names = await _team_options(db)
    favorites = (await db.execute(select(FavoriteTeam))).scalars().all()
    favorite_by_key = {(f.division_id, f.normalized_name): f for f in favorites}
    response = []
    for division, event in divisions:
        for normalized, name in sorted(names.get(division.id, {}).items(), key=lambda item: item[1].casefold()):
            favorite = favorite_by_key.get((division.id, normalized))
            response.append(FavoriteTeamResponse(
                id=favorite.id if favorite else None,
                division_id=division.id,
                event_id=event.id,
                event_name=event.name,
                division_name=division.display_name or division.name,
                team_name=name,
                is_favorite=favorite is not None,
            ))
    return response


@router.post("", response_model=FavoriteTeamResponse, status_code=status.HTTP_201_CREATED)
async def add_favorite(payload: FavoriteTeamCreate, db: AsyncSession = Depends(get_db)):
    name = " ".join(payload.team_name.split())
    normalized = _key(name)
    division_result = await db.execute(select(Division, Event).join(Event).where(Division.id == payload.division_id))
    row = division_result.first()
    if not row or not normalized:
        raise HTTPException(status_code=404, detail="Division or team not found")
    division, event = row
    _, names = await _team_options(db)
    canonical = names.get(division.id, {}).get(normalized)
    if not canonical:
        raise HTTPException(status_code=404, detail="Team is not present in this division")
    existing = await db.scalar(select(FavoriteTeam).where(FavoriteTeam.division_id == division.id, FavoriteTeam.normalized_name == normalized))
    if existing:
        return FavoriteTeamResponse(id=existing.id, division_id=division.id, event_id=event.id, event_name=event.name, division_name=division.display_name or division.name, team_name=existing.team_name)
    favorite = FavoriteTeam(division_id=division.id, team_name=canonical, normalized_name=normalized)
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    return FavoriteTeamResponse(id=favorite.id, division_id=division.id, event_id=event.id, event_name=event.name, division_name=division.display_name or division.name, team_name=canonical)


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(favorite_id: int, db: AsyncSession = Depends(get_db)):
    favorite = await db.get(FavoriteTeam, favorite_id)
    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite team not found")
    await db.delete(favorite)
    await db.commit()
