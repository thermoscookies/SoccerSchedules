import pytest

from app.importers.gotsport_pdf import parse_schedule_text
from app.models.models import Division, Event, Game
from app.services.scrape_service import ScrapeService


def test_pdf_parser_handles_scores_unplayed_rows_and_fields(monkeypatch):
    result = parse_schedule_text(["Central Coast\nSaturday, September 12, 2026\n"
        "# Time Home Team Results Away Team Location\n"
        "234 9:00AM PDT Central Cal Aztecs GU14 0 - 2 City SC Kings County "
            "  Kaiser Permanente Sports Village - Field 13\n"
        "Saturday, October 18, 2026\n"
        "235 City SC Kings County - Ventura County Fusion"])

    assert result.critical_errors == []
    assert len(result.games) == 2
    completed, scheduled = result.games
    assert completed["game_number"] == "234"
    assert completed["home_score"] == 0
    assert completed["away_score"] == 2
    assert completed["status"] == "COMPLETED"
    assert completed["field_name"] == "Field 13"
    assert scheduled["game_number"] == "235"
    assert scheduled["status"] == "SCHEDULED"
    assert "home_score" not in scheduled


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _Session:
    def __init__(self, games):
        self.games = games
        self.added = []

    async def execute(self, _statement):
        return _Result(self.games)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.games.extend(self.added)
        self.added.clear()


@pytest.mark.asyncio
async def test_existing_game_is_updated_by_number_without_blank_overwrite(monkeypatch):
    event = Event(id=1, gotsport_event_id="51974", name="DPL", url="pdf")
    division = Division(id=10, event_id=1, name="Central Coast GU14")
    existing = Game(
        id=20, division_id=10, gotsport_game_id="234", game_number="234",
        home_team_name="Old Home", away_team_name="Old Away",
        game_time="8:00AM", field_location="Old Venue",
    )
    session = _Session([existing])
    result = parse_schedule_text(["Saturday, September 12, 2026\n"
        "234 9:00AM PDT Central Cal Aztecs GU14 0 - 2 City SC Kings County "
        "Kaiser Permanente Sports Village - Field 13\n"
        "235 City SC Kings County - Ventura County Fusion"])
    stats = await ScrapeService(session)._store_games(event, {division.name: division}, result.games)

    assert stats["created"] == 1
    assert stats["updated"] == 1
    assert existing.home_score == 0
    assert existing.away_score == 2
