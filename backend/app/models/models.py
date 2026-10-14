"""Database models for soccer schedule application"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
import enum


class ScrapeStatus(str, enum.Enum):
    """Scrape status enumeration"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"


class GameStatus(str, enum.Enum):
    """Game status enumeration"""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"


class Event(Base):
    """Tournament/Event model"""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    gotsport_event_id: Mapped[str] = mapped_column(String(50), index=True)
    gotsport_group_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(50), default="gotsport", nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    last_imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    divisions: Mapped[list["Division"]] = relationship(
        "Division", back_populates="event", cascade="all, delete-orphan"
    )
    scrape_logs: Mapped[list["ScrapeLog"]] = relationship(
        "ScrapeLog", back_populates="event", cascade="all, delete-orphan"
    )
    browser_imports: Mapped[list["BrowserImport"]] = relationship(
        "BrowserImport", back_populates="event", cascade="all, delete-orphan"
    )


class Division(Base):
    """Age/Gender division within an event"""
    __tablename__ = "divisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gotsport_division_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional user-facing label; provider name/ID remains in ``name``.
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    age_group: Mapped[Optional[str]] = mapped_column(String(50))
    gender: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="divisions")
    teams: Mapped[list["Team"]] = relationship(
        "Team", back_populates="division", cascade="all, delete-orphan"
    )
    games: Mapped[list["Game"]] = relationship(
        "Game", back_populates="division", cascade="all, delete-orphan"
    )
    bracket_standings: Mapped[list["BracketStanding"]] = relationship(
        "BracketStanding", back_populates="division", cascade="all, delete-orphan"
    )


class Team(Base):
    """Team model"""
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    division_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gotsport_team_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    club: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    division: Mapped["Division"] = relationship("Division", back_populates="teams")
    home_games: Mapped[list["Game"]] = relationship(
        "Game",
        foreign_keys="[Game.home_team_id]",
        back_populates="home_team",
    )
    away_games: Mapped[list["Game"]] = relationship(
        "Game",
        foreign_keys="[Game.away_team_id]",
        back_populates="away_team",
    )


class FavoriteTeam(Base):
    """A shared favorite identified by its division and canonical team name."""
    __tablename__ = "favorite_teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    division_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    division: Mapped["Division"] = relationship("Division")

    __table_args__ = (
        Index("ix_favorite_teams_division_name", "division_id", "normalized_name", unique=True),
    )


class Game(Base):
    """Game/Match model"""
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    division_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gotsport_game_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    game_number: Mapped[Optional[str]] = mapped_column(String(50))
    
    home_team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    away_team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    
    # For cases where team isn't in our database yet
    home_team_name: Mapped[Optional[str]] = mapped_column(String(255))
    away_team_name: Mapped[Optional[str]] = mapped_column(String(255))
    
    game_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    game_time: Mapped[Optional[str]] = mapped_column(String(20))
    field_name: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    field_location: Mapped[Optional[str]] = mapped_column(String(255))
    
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    
    status: Mapped[str] = mapped_column(
        SQLEnum(GameStatus), default=GameStatus.SCHEDULED, nullable=False
    )
    
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    division: Mapped["Division"] = relationship("Division", back_populates="games")
    home_team: Mapped[Optional["Team"]] = relationship(
        "Team",
        foreign_keys=[home_team_id],
        back_populates="home_games",
    )
    away_team: Mapped[Optional["Team"]] = relationship(
        "Team",
        foreign_keys=[away_team_id],
        back_populates="away_games",
    )
    
    # Composite indexes for performance
    __table_args__ = (
        Index('ix_games_division_gotsport', 'division_id', 'gotsport_game_id'),
        Index('ix_games_division_teams_datetime', 
              'division_id', 'home_team_name', 'away_team_name', 'game_date', 'game_time'),
        Index('ix_games_datetime', 'game_date', 'game_time'),
        Index('ix_games_field_date', 'field_name', 'game_date'),
    )


class BracketStanding(Base):
    """Bracket standings within a division"""
    __tablename__ = "bracket_standings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    division_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("divisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bracket_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Standings data
    played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    goal_difference: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    division: Mapped["Division"] = relationship("Division", back_populates="bracket_standings")
    
    # Unique constraint for team in bracket
    __table_args__ = (
        Index('ix_bracket_division_bracket_team', 'division_id', 'bracket_name', 'team_name', unique=True),
    )


class ScrapeLog(Base):
    """Log of scraping operations"""
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        SQLEnum(ScrapeStatus), default=ScrapeStatus.PENDING, nullable=False
    )
    scrape_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    scrape_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    games_scraped: Mapped[Optional[int]] = mapped_column(Integer)
    games_updated: Mapped[Optional[int]] = mapped_column(Integer)
    games_created: Mapped[Optional[int]] = mapped_column(Integer)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="scrape_logs")


class BrowserImport(Base):
    """History of explicit, authenticated browser-assisted imports."""
    __tablename__ = "browser_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="gotsport")
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    games_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    games_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    games_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    event: Mapped["Event"] = relationship("Event", back_populates="browser_imports")
