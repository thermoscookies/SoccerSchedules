"""Focused browser-import validation and idempotency tests."""
from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.browser_import import _source_parts
from app.models.models import Division, Event, Game
from app.scraper.gotsport import GotsportCaptchaError, GotsportScraper
from app.services.scrape_service import ScrapeService


def test_source_identifies_event_and_group():
    assert _source_parts("https://system.gotsport.com/org_event/events/48745/schedules?date=All&group=522955") == ("48745", "522955")


def test_source_rejects_non_gotsport_url():
    with pytest.raises(Exception):
        _source_parts("https://example.test/events/48745?group=522955")


@pytest.mark.asyncio
async def test_captcha_html_is_rejected_before_parser_data():
    with pytest.raises(GotsportCaptchaError):
        await GotsportScraper()._scrape_division_schedule(
            None,
            "https://system.gotsport.com/org_event/events/48745/schedules?group=522955",
            {"name": "La Esperanza GU15", "gotsport_division_id": "522955"},
            html_content="<html><title>Please verify CAPTCHA</title><form action='/verify_captchas'></form></html>",
        )


@pytest.mark.asyncio
async def test_existing_event_game_import_is_idempotent_and_preserves_absent_games():
    # Reuse the lightweight session used by the scraper regression suite.
    from test_scraper_regressions import _MemorySession

    event = Event(id=2, gotsport_event_id="48745", name="La Esperanza GU15", url="source")
    division = Division(id=22, event_id=2, name="Group 522955", gotsport_division_id="522955")
    existing = Game(id=1, division_id=22, game_number="KEEP", home_team_name="Existing", away_team_name="Other")
    session = _MemorySession([event, division, existing])
    service = ScrapeService(session)
    data = [{"division_name":"Group 522955", "game_number":"NEW", "home_team_name":"La Esperanza GU15", "away_team_name":"Opponent", "status":"scheduled"}]
    first = await service._store_games(event, {division.name: division}, data)
    second = await service._store_games(event, {division.name: division}, data)
    assert first["created"] == 1
    assert second["created"] == 0
    assert len([game for game in session.items if isinstance(game, Game)]) == 2
    assert any(game.game_number == "KEEP" for game in session.items if isinstance(game, Game))
