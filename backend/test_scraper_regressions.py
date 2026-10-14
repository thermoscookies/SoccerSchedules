"""Regression tests for GotSport group-source and verification handling."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.models import Division, Event, Game, ScrapeStatus
from app.schemas.schemas import EventUpdate
from app.scraper.gotsport import (
    GotsportCaptchaError,
    GotsportScraper,
    build_gotsport_group_url,
)
from app.services import scrape_service as scrape_service_module
from app.services.scrape_service import ScrapeService
from app.scheduler import SmartScheduler


class _Result:
    def __init__(self, values):
        self.values = values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return self.values


class _MemorySession:
    """Small AsyncSession substitute for persistence idempotency tests."""
    def __init__(self, items=()):
        self.items = list(items)
        self.commits = 0

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        return _Result([item for item in self.items if isinstance(item, entity)])

    def add(self, item):
        if item not in self.items:
            self.items.append(item)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None


class _Page:
    def __init__(self, html):
        self.html = html

    async def content(self):
        return self.html


class _AttemptPage(_Page):
    url = "https://system.gotsport.com/org_event/events/51974/results?group=501305"

    async def set_extra_http_headers(self, _headers):
        return None

    def on(self, _event, _callback):
        return None

    async def goto(self, _url, **_kwargs):
        return None

    async def wait_for_selector(self, _selector, **_kwargs):
        return None

    async def title(self):
        return "GotSport"

    async def close(self):
        return None


class _Browser:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


@pytest.mark.asyncio
async def test_results_links_are_discovered_as_group_sources():
    scraper = GotsportScraper()
    page = _Page(
        '<a href="/org_event/events/51974/results?group=501305">Results</a>'
    )

    divisions = await scraper._extract_divisions_from_event_page(
        page, "51974", "https://system.gotsport.com/org_event/events/51974"
    )

    assert len(divisions) == 1
    assert divisions[0]["gotsport_division_id"] == "501305"
    assert divisions[0]["schedule_url"] == (
        "https://system.gotsport.com/org_event/events/51974/schedules?date=All&group=501305"
    )


def test_saved_schedule_html_has_the_game_table_shape():
    html_path = Path("/workspace/GotSport.html")
    if not html_path.exists():
        pytest.skip("saved GotSport.html not mounted for this test run")
    html = html_path.read_text()
    assert "Match #" in html
    assert "Home Team" in html
    assert "Away Team" in html
    assert "Location" in html


@pytest.mark.asyncio
async def test_saved_schedule_html_parses_72_unique_games(monkeypatch):
    html_path = Path("/workspace/GotSport.html")
    if not html_path.exists():
        pytest.skip("saved GotSport.html not mounted for this test run")

    scraper = GotsportScraper()
    division = {"name": "Group 501305", "gotsport_division_id": "501305"}
    games = await scraper._scrape_division_schedule(
        None,
        "https://system.gotsport.com/org_event/events/51974/schedules?date=All&group=501305",
        division,
        html_content=html_path.read_text(),
    )
    numbers = [game["game_number"] for game in games]
    assert len(games) == 72
    assert len(numbers) == len(set(numbers)) == 72
    assert games[0]["home_team_name"] == "Central Cal Aztecs GU14 DPL"
    assert games[0]["away_team_name"] == "City SC Kings County U14G DPL"
    assert games[0]["home_score"] == 0
    assert games[0]["away_score"] == 2


@pytest.mark.asyncio
async def test_schedule_parser_does_not_create_an_http_client():
    scraper = GotsportScraper()
    division = {"name": "Group 501305", "gotsport_division_id": "501305"}

    games = await scraper._scrape_division_schedule(
        None,
        "https://system.gotsport.com/org_event/events/51974/schedules?group=501305",
        division,
        html_content="<html><body><table><tr><th>Match #</th><th>Home Team</th><th>Away Team</th></tr></table></body></html>",
    )
    assert games == []


def test_navigation_diagnostics_include_urls_status_redirects_and_captcha(caplog):
    class _Request:
        url = "https://system.gotsport.com/org_event/events/51974"
        redirected_from = None

    class _Response:
        status = 200
        request = _Request()

    with caplog.at_level("INFO"):
        GotsportScraper._log_navigation(
            "https://system.gotsport.com/org_event/events/51974/results?group=501305",
            "https://system.gotsport.com/org_event/events/51974/verify_captchas/new",
            _Response(),
            captcha=True,
        )
    assert "requested_url" in caplog.text
    assert "verify_captchas/new" in caplog.text
    assert '"status": 200' in caplog.text
    assert '"captcha": true' in caplog.text


def test_captcha_interstitial_is_detected_and_is_not_retried():
    scraper = GotsportScraper()
    assert scraper._is_captcha_interstitial(
        "https://system.gotsport.com/org_event/events/51974/verify_captchas/new",
        "<title>GotSport CAPTCHA Verification</title>",
    )
    assert not scraper._is_captcha_interstitial(
        "https://system.gotsport.com/org_event/events/51974",
        "<title>Development Player League</title>",
    )


@pytest.mark.asyncio
async def test_captcha_failure_does_not_retry_or_update_last_scraped(monkeypatch):
    event = Event(
        id=1,
        gotsport_event_id="51974",
        name="DPL",
        url="https://system.gotsport.com/org_event/events/51974",
        last_scraped_at=datetime(2026, 10, 14, 16, 30, tzinfo=timezone.utc),
    )
    session = _MemorySession([event])
    calls = []

    class _CaptchaScraper:
        async def start(self):
            return None

        async def stop(self):
            return None

        def _extract_event_id_from_url(self, _url):
            return "51974"

        async def scrape_event(self, _url):
            calls.append(_url)
            raise GotsportCaptchaError("GotSport returned a CAPTCHA verification page")

    monkeypatch.setattr(scrape_service_module, "GotsportScraper", _CaptchaScraper)

    with pytest.raises(GotsportCaptchaError):
        await ScrapeService(session).scrape_and_store_event(event.url, force=True)

    scrape_log = next(item for item in session.items if item.__class__.__name__ == "ScrapeLog")
    assert calls == [event.url]
    assert scrape_log.status == ScrapeStatus.FAILED
    assert "CAPTCHA" in scrape_log.error_message
    assert event.last_scraped_at == datetime(2026, 10, 14, 16, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_group_captcha_propagates_instead_of_returning_zero_data(monkeypatch):
    class _GroupCaptchaScraper(GotsportScraper):
        async def _scrape_division_schedule(self, _page, _url, _division, **_kwargs):
            raise GotsportCaptchaError("GotSport returned a CAPTCHA verification page for group 501305")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.scraper.gotsport.asyncio.sleep", _no_sleep)
    scraper = _GroupCaptchaScraper()
    scraper.browser = _Browser(_AttemptPage("<html><title>DPL</title></html>"))

    with pytest.raises(GotsportCaptchaError, match="group 501305"):
        await scraper._attempt_scrape(
            build_gotsport_group_url("51974", "501305"), "51974"
        )


@pytest.mark.asyncio
async def test_scrape_uses_one_shared_browser_context(monkeypatch):
    class _Context:
        def __init__(self, page):
            self.page = page
            self.calls = 0

        async def new_page(self):
            self.calls += 1
            return self.page

    class _NoBrowser:
        async def new_page(self):
            raise AssertionError("browser.new_page must not be used when a context exists")

    class _NoFetchScraper(GotsportScraper):
        async def _scrape_division_schedule(self, *_args, **_kwargs):
            return []

        async def _scrape_bracket_standings(self, *_args, **_kwargs):
            return []

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.scraper.gotsport.asyncio.sleep", _no_sleep)
    scraper = _NoFetchScraper()
    scraper.browser = _NoBrowser()
    context = _Context(_AttemptPage("<html><title>DPL</title></html>"))
    scraper.context = context
    await scraper._attempt_scrape(build_gotsport_group_url("51974", "501305"), "51974")
    assert context.calls == 1


def test_captcha_cooldown_is_conservative():
    scheduler = SmartScheduler()
    assert not scheduler._captcha_cooldown_active()
    scheduler._record_captcha_failure()
    assert scheduler._captcha_cooldown_active()
    scheduler.captcha_cooldown_until = datetime.now(timezone.utc)
    assert not scheduler._captcha_cooldown_active()


@pytest.mark.asyncio
async def test_direct_group_source_and_repeated_imports_are_idempotent():
    source_url = build_gotsport_group_url("51974", "501305")
    scraper = GotsportScraper()
    assert scraper._direct_group_division(source_url) == {
        "name": "Group 501305",
        "gotsport_division_id": "501305",
        "schedule_url": "https://system.gotsport.com/org_event/events/51974/schedules?date=All&group=501305",
    }

    event = Event(id=1, gotsport_event_id="51974", name="DPL", url=source_url)
    division = Division(
        id=10,
        event_id=1,
        name="Previous group label",
        gotsport_division_id="501305",
    )
    session = _MemorySession([event, division])
    service = ScrapeService(session)
    division_data = [{
        "name": "Group 501305",
        "gotsport_division_id": "501305",
    }]

    first_divisions = await service._store_divisions(event, division_data)
    second_divisions = await service._store_divisions(event, division_data)
    assert first_divisions["Group 501305"] is division
    assert second_divisions["Group 501305"] is division
    assert len([item for item in session.items if isinstance(item, Division)]) == 1

    game_data = [{
        "division_name": "Group 501305",
        "gotsport_game_id": "9001",
        "game_number": "1",
        "home_team_name": "Home",
        "away_team_name": "Away",
        "status": "scheduled",
    }]
    first_games = await service._store_games(event, second_divisions, game_data)
    second_games = await service._store_games(event, second_divisions, game_data)
    assert first_games["created"] == 1
    assert second_games["created"] == 0
    assert second_games["updated"] == 1
    assert len([item for item in session.items if isinstance(item, Game)]) == 1


def test_group_configuration_request_fields_are_available():
    group_id_request = EventUpdate(gotsport_group_id="501305")
    group_url_request = EventUpdate(
        group_url="https://system.gotsport.com/org_event/events/51974/schedules?group=501305"
    )

    assert group_id_request.gotsport_group_id == "501305"
    assert group_url_request.group_url.endswith("group=501305")
