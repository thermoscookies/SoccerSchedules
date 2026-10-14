"""Pydantic schemas for API request/response validation"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl


# ============================================================================
# Event Schemas
# ============================================================================

class EventBase(BaseModel):
    """Base event schema"""
    gotsport_event_id: str = Field(..., description="Gotsport event ID")
    name: str = Field(..., description="Event name")
    location: Optional[str] = Field(None, description="Event location")
    start_date: Optional[datetime] = Field(None, description="Event start date")
    end_date: Optional[datetime] = Field(None, description="Event end date")
    url: str = Field(..., description="Event URL")
    status: str = Field(default="active", description="Event status")
    provider: str = Field(default="gotsport", description="Data provider")
    source_url: Optional[str] = Field(None, description="Canonical provider source URL")
    last_imported_at: Optional[datetime] = None


class EventCreate(EventBase):
    """Schema for creating an event"""
    gotsport_group_id: Optional[str] = Field(None, description="Optional GotSport group ID to track directly")
    group_url: Optional[str] = Field(None, description="Optional GotSport schedule or results URL for one group")


class EventUpdate(BaseModel):
    """Schema for updating an event"""
    name: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    url: Optional[str] = None
    gotsport_group_id: Optional[str] = Field(None, description="Optional GotSport group ID to track directly")
    group_url: Optional[str] = Field(None, description="Optional GotSport schedule or results URL for one group")
    status: Optional[str] = None


class EventResponse(EventBase):
    """Schema for event response"""
    id: int
    last_scraped_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EventWithStats(EventResponse):
    """Event response with statistics"""
    total_divisions: int = 0
    total_teams: int = 0
    total_games: int = 0
    next_scrape_in_hours: Optional[float] = None


# ============================================================================
# Division Schemas
# ============================================================================

class DivisionBase(BaseModel):
    """Base division schema"""
    name: str = Field(..., description="Division name")
    age_group: Optional[str] = Field(None, description="Age group (e.g., U12)")
    gender: Optional[str] = Field(None, description="Gender (Male/Female/Coed)")
    gotsport_division_id: Optional[str] = Field(None, description="Gotsport division ID")


class DivisionCreate(DivisionBase):
    """Schema for creating a division"""
    event_id: int


class DivisionResponse(DivisionBase):
    """Schema for division response"""
    id: int
    event_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DivisionWithCounts(DivisionResponse):
    """Division response with counts"""
    team_count: int = 0
    game_count: int = 0


# ============================================================================
# Team Schemas
# ============================================================================

class TeamBase(BaseModel):
    """Base team schema"""
    name: str = Field(..., description="Team name")
    club: Optional[str] = Field(None, description="Club name")
    gotsport_team_id: Optional[str] = Field(None, description="Gotsport team ID")


class TeamCreate(TeamBase):
    """Schema for creating a team"""
    division_id: int


class TeamResponse(TeamBase):
    """Schema for team response"""
    id: int
    division_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Game Schemas
# ============================================================================

class GameBase(BaseModel):
    """Base game schema"""
    game_number: Optional[str] = Field(None, description="Game number")
    home_team_name: Optional[str] = Field(None, description="Home team name")
    away_team_name: Optional[str] = Field(None, description="Away team name")
    game_date: Optional[datetime] = Field(None, description="Game date and time")
    game_time: Optional[str] = Field(None, description="Game time string")
    field_name: Optional[str] = Field(None, description="Field name")
    field_location: Optional[str] = Field(None, description="Field location")
    home_score: Optional[int] = Field(None, description="Home team score")
    away_score: Optional[int] = Field(None, description="Away team score")
    status: str = Field(default="scheduled", description="Game status")
    notes: Optional[str] = Field(None, description="Additional notes")


class GameCreate(GameBase):
    """Schema for creating a game"""
    division_id: int
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    gotsport_game_id: Optional[str] = None


class GameUpdate(BaseModel):
    """Schema for updating a game"""
    game_date: Optional[datetime] = None
    game_time: Optional[str] = None
    field_name: Optional[str] = None
    field_location: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class GameResponse(GameBase):
    """Schema for game response"""
    id: int
    division_id: int
    home_team_id: Optional[int]
    away_team_id: Optional[int]
    gotsport_game_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GameDetailResponse(GameResponse):
    """Detailed game response with related data"""
    division_name: Optional[str] = None
    event_name: Optional[str] = None
    event_id: Optional[int] = None


# ============================================================================
# ScrapeLog Schemas
# ============================================================================

class ScrapeLogBase(BaseModel):
    """Base scrape log schema"""
    status: str = Field(..., description="Scrape status")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    games_scraped: Optional[int] = Field(None, description="Number of games scraped")
    games_updated: Optional[int] = Field(None, description="Number of games updated")
    games_created: Optional[int] = Field(None, description="Number of games created")


class ScrapeLogResponse(ScrapeLogBase):
    """Schema for scrape log response"""
    id: int
    event_id: int
    scrape_started_at: datetime
    scrape_completed_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================================
# Bracket Standings Schemas
# ============================================================================

class BracketStandingBase(BaseModel):
    """Base bracket standing schema"""
    bracket_name: str = Field(..., description="Bracket name (e.g., 'Bracket A')")
    team_name: str = Field(..., description="Team name")
    played: int = Field(default=0, description="Games played")
    wins: int = Field(default=0, description="Wins")
    draws: int = Field(default=0, description="Draws")
    losses: int = Field(default=0, description="Losses")
    goals_for: int = Field(default=0, description="Goals scored")
    goals_against: int = Field(default=0, description="Goals conceded")
    goal_difference: int = Field(default=0, description="Goal difference")
    points: int = Field(default=0, description="Points")


class BracketStandingResponse(BracketStandingBase):
    """Schema for bracket standing response"""
    id: int
    division_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SeedingTeam(BaseModel):
    """Team in seeding list"""
    team_name: str
    bracket_name: str
    points: int
    goal_difference: int
    goals_for: int
    goals_against: int
    played: int
    wins: int
    draws: int
    losses: int
    rank: int
    is_bracket_winner: bool


class SeedingResponse(BaseModel):
    """Response schema for seeding calculation"""
    division_id: int
    division_name: str
    bracket_winners: List[SeedingTeam]
    top_remaining: List[SeedingTeam]


# ============================================================================
# Scraping Operation Schemas
# ============================================================================

class ScrapeRequest(BaseModel):
    """Request schema for triggering a scrape"""
    event_id: int = Field(..., description="Event ID to scrape")
    force: bool = Field(default=False, description="Force scrape even if recently scraped")


class ScrapeResponse(BaseModel):
    """Response schema for scrape operations"""
    message: str
    scrape_log_id: Optional[int] = None
    status: str


# ============================================================================
# Schedule Query Schemas
# ============================================================================

class ScheduleFilters(BaseModel):
    """Filters for schedule queries"""
    event_id: int
    division_id: Optional[int] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    field_name: Optional[str] = None
    team_name: Optional[str] = None
    status: Optional[str] = None


class ScheduleResponse(BaseModel):
    """Response schema for schedule queries"""
    event: EventResponse
    divisions: List[DivisionResponse]
    games: List[GameDetailResponse]
    total_games: int
    events: Optional[List[EventResponse]] = None


# ============================================================================
# Health Check Schemas
# ============================================================================

class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SchedulerStatus(BaseModel):
    """Scheduler status response"""
    event_id: int
    event_name: str
    last_scraped: Optional[datetime]
    next_scrape_in_hours: float
    scrape_interval_hours: int
