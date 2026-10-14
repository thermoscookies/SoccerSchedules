"""Parse GotSport's printable schedule PDF into SoccerSchedules game records."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

try:
    from pypdf import PdfReader
except ImportError:  # The text parser remains testable in minimal utility images.
    PdfReader = None
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Division, Event, Game
from app.services.scrape_service import ScrapeService


DATE_RE = re.compile(
    r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})$"
)
ROW_RE = re.compile(r"^\s*(?P<number>\d{3,})\s+(?P<body>.*)$")
TIME_RE = re.compile(r"(?P<time>\d{1,2}:\d{2}\s*(?:AM|PM)(?:\s+[A-Z]{2,4})?)", re.I)
SCORE_RE = re.compile(r"(?P<home>\d+)\s*-\s*(?P<away>\d+)")


@dataclass
class PdfImportResult:
    games: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def critical_errors(self) -> list[str]:
        return [warning for warning in self.warnings if warning.startswith("CRITICAL:")]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -")


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%B %d, %Y").replace(tzinfo=ZoneInfo("America/Los_Angeles"))


def _extract_columns(body: str) -> tuple[Optional[str], str, str, str, str]:
    """Extract time, home, away, and location from one layout-preserved row."""
    time_match = TIME_RE.search(body)
    kickoff = time_match.group("time") if time_match else None
    remainder = body[time_match.end():] if time_match else body
    score_match = SCORE_RE.search(remainder)
    if score_match:
        home = _clean(remainder[:score_match.start()])
        score = score_match.group(0)
        after = remainder[score_match.end():]
    else:
        # Unplayed rows use a standalone dash between the teams.
        dash = re.search(r"\s+-\s+", remainder)
        if not dash:
            return kickoff, "", "", "", ""
        home = _clean(remainder[:dash.start()])
        score = ""
        after = remainder[dash.end():]

    # GotSport's printable layout separates away team and location with a
    # wide gap; team names themselves use ordinary single spaces.
    parts = re.split(r"\s{2,}", after.strip(), maxsplit=1)
    away = _clean(parts[0])
    location = _clean(parts[1]) if len(parts) == 2 else ""
    field_name = ""
    field_match = re.search(r"\s+-\s+(Field\b.*|#\s*\d+.*)$", location, re.I)
    if field_match:
        field_name = _clean(field_match.group(1))
        location = _clean(location[:field_match.start()])
    return kickoff, home, away, location, field_name


def _parse_row(number: str, body: str, date: Optional[datetime], division_name: str, warnings: list[str]) -> Optional[dict[str, Any]]:
    if date is None:
        warnings.append(f"CRITICAL: game {number} has no preceding date heading")
        return None
    kickoff, home, away, location, field_name = _extract_columns(body)
    if not home or not away:
        warnings.append(f"CRITICAL: game {number} could not identify both teams")
        return None

    score_match = SCORE_RE.search(body)
    game: dict[str, Any] = {
        "division_name": division_name,
        "game_number": number,
        "home_team_name": home,
        "away_team_name": away,
        "game_date": date,
        "game_time": kickoff,
        "field_location": location or None,
        "field_name": field_name or None,
        # The printable schedule exposes GotSport's match number, which is the
        # stable source key available to the existing game upsert logic.
        "gotsport_game_id": number,
    }
    if score_match:
        game["home_score"] = int(score_match.group("home"))
        game["away_score"] = int(score_match.group("away"))
        game["status"] = "COMPLETED"
    else:
        game["status"] = "SCHEDULED"
    return game


def parse_schedule_text(text_pages: Iterable[str], division_name: str = "Central Coast GU14") -> PdfImportResult:
    """Parse layout-preserved page text from a GotSport printable schedule."""
    result = PdfImportResult()
    pages = list(text_pages)
    if not pages:
        result.warnings.append("CRITICAL: PDF contains no pages")
        return result

    current_date: Optional[datetime] = None
    pending: Optional[tuple[str, list[str]]] = None

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        number, lines = pending
        # Layout extraction keeps useful column gaps on the first line. Wrapped
        # lines are retained as a conservative continuation of the row.
        body = " ".join(line.strip() for line in lines if line.strip())
        game = _parse_row(number, body, current_date, division_name, result.warnings)
        if game:
            result.games.append(game)
        pending = None

    for text in pages:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or re.fullmatch(r"\d+\s+of\s+\d+", line):
                continue
            date_match = DATE_RE.match(line)
            if date_match:
                flush()
                current_date = _parse_date(
                    f"{date_match.group('month')} {date_match.group('day')}, {date_match.group('year')}"
                )
                continue
            row_match = ROW_RE.match(raw_line)
            if row_match:
                flush()
                pending = (row_match.group("number"), [row_match.group("body")])
            elif pending and not line.startswith(("#", "Time", "Home Team", "Central Coast")):
                pending[1].append(raw_line)
        flush()

    if not result.games:
        result.warnings.append("CRITICAL: no game rows parsed")
    return result


def parse_schedule_pdf(path: str | Path, division_name: str = "Central Coast GU14") -> PdfImportResult:
    """Parse all game rows from a GotSport printable schedule PDF."""
    if PdfReader is None:
        raise RuntimeError("pypdf is required for direct PDF parsing; use parse_schedule_text with rendered pages")
    reader = PdfReader(str(path))
    return parse_schedule_text(
        (page.extract_text(extraction_mode="layout") or "" for page in reader.pages),
        division_name,
    )


async def import_schedule_pdf(
    db: AsyncSession,
    event: Event,
    division: Division,
    path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Parse and optionally persist a PDF using the normal game upsert path."""
    parsed = parse_schedule_pdf(path, division.name)
    return await import_parsed_games(db, event, division, parsed, dry_run=dry_run)


async def import_parsed_games(
    db: AsyncSession,
    event: Event,
    division: Division,
    parsed: PdfImportResult,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Plan or persist already-rendered PDF games using normal upsert logic."""
    report: dict[str, Any] = {
        "parsed": len(parsed.games),
        "warnings": parsed.warnings,
        "critical_errors": parsed.critical_errors,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
    }
    if parsed.critical_errors:
        return report

    existing_result = await db.execute(select(Game).where(Game.division_id == division.id))
    existing = {
        str(game.gotsport_game_id or game.game_number): game
        for game in existing_result.scalars().all()
        if game.gotsport_game_id or game.game_number
    }
    for item in parsed.games:
        current = existing.get(item["gotsport_game_id"])
        if current is None:
            report["created"] += 1
        else:
            changed = any(
                value is not None and value != getattr(current, field)
                for field, value in item.items()
                if field not in {"division_name", "gotsport_game_id"}
            )
            report["updated" if changed else "unchanged"] += 1

    if not dry_run:
        stats = await ScrapeService(db)._store_games(event, {division.name: division}, parsed.games)
        report["created"] = stats["created"]
        report["updated"] = stats["updated"]
    return report
