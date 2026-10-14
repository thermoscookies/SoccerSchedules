"""Gotsport scraper using Playwright for headless browser automation"""
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs, parse_qsl, urlencode, urlunparse
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Response
from bs4 import BeautifulSoup
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class GotsportCaptchaError(RuntimeError):
    """Raised when GotSport returns its CAPTCHA/interstitial page."""


def build_gotsport_group_url(event_id: str, group_id: str) -> str:
    """Build the canonical GotSport results URL for one event group."""
    return f"https://system.gotsport.com/org_event/events/{event_id}/results?group={group_id}"


class GotsportScraper:
    """Scraper for Gotsport tournament websites"""
    
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._playwright = None
        self.api_responses: Dict[str, Any] = {}
        
    async def __aenter__(self):
        """Async context manager entry"""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.stop()
    
    async def start(self):
        """Start the browser instance"""
        self._playwright = await async_playwright().start()
        # Resource limits for a headless browser in a small self-hosted deployment.
        self.browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--disable-plugins',
                '--single-process',  # Use single process to reduce memory
                '--no-zygote',  # Don't use zygote process
                '--disable-background-networking',
                '--disable-default-apps',
                '--disable-sync',
            ]
        )
        # Keep one ordinary browser context for every page in this scrape.  This
        # preserves consent/session state without persisting cookies between runs.
        self.context = await self.browser.new_context()
        logger.info("Playwright browser started with memory optimizations")
    
    async def stop(self):
        """Stop the browser instance"""
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Playwright browser stopped")
    
    def _extract_event_id_from_url(self, url: str) -> Optional[str]:
        """Extract event ID from Gotsport URL"""
        # Example: https://system.gotsport.com/org_event/events/39474
        match = re.search(r'/events/(\d+)', url)
        if match:
            return match.group(1)
        return None
    
    async def _handle_response(self, response: Response):
        """Intercept and store API responses"""
        url = response.url
        
        # Look for Gotsport-specific API endpoints (not cookies/ads)
        # Focus on system.gotsport.com and gotss domains
        if 'gotsport.com' in url or 'gotss' in url:
            # Look for API-like patterns
            if any(pattern in url for pattern in ['/api/', '/data/', '.json', 'schedule', 'event', 'division', 'game']):
                print(f"[SCRAPER] Potential Gotsport API endpoint detected: {url}")
                logger.info(f"Potential API endpoint detected: {url}")
                try:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        print(f"[SCRAPER] Response status 200, content-type: {content_type}")
                        logger.info(f"Response status 200, content-type: {content_type}")
                        if 'application/json' in content_type:
                            data = await response.json()
                            self.api_responses[url] = data
                            print(f"[SCRAPER] Successfully intercepted and parsed JSON from: {url}")
                            logger.info(f"Successfully intercepted and parsed JSON from: {url}")
                            logger.debug(f"Data type: {type(data)}, keys/length: {list(data.keys()) if isinstance(data, dict) else len(data) if isinstance(data, list) else 'unknown'}")
                        else:
                            logger.debug(f"Skipping non-JSON response from: {url}")
                    else:
                        logger.debug(f"Non-200 status ({response.status}) from: {url}")
                except Exception as e:
                    logger.warning(f"Could not parse response from {url}: {e}")
    
    async def scrape_event(self, event_url: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        Scrape event data from Gotsport with retry logic
        
        Args:
            event_url: URL of the event to scrape
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dict containing event details, divisions, and schedule data
        """
        if not self.browser:
            await self.start()
        
        event_id = self._extract_event_id_from_url(event_url)
        if not event_id:
            raise ValueError(f"Could not extract event ID from URL: {event_url}")
        
        print(f"[SCRAPER] Starting scrape of event {event_id} from {event_url}")
        logger.info(f"Scraping event {event_id} from {event_url}")
        
        # Try multiple times with exponential backoff
        for attempt in range(max_retries):
            try:
                result = await self._attempt_scrape(event_url, event_id)
                print(f"[SCRAPER] Scrape completed: {len(result.get('schedules', []))} games, {len(result.get('divisions', []))} divisions")
                return result
            except GotsportCaptchaError:
                # Retrying a verification page is both unlikely to help and can make the
                # source site treat this application more aggressively.  Let persistence
                # record one failed scrape instead.
                logger.error("GotSport CAPTCHA/interstitial detected; not retrying")
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5  # 5, 10, 15 seconds
                    print(f"[SCRAPER] Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    logger.warning(f"Scrape attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[SCRAPER] All {max_retries} attempts failed for event {event_id}")
                    logger.error(f"All {max_retries} scrape attempts failed for event {event_id}")
                    raise
    
    async def _attempt_scrape(self, event_url: str, event_id: str) -> Dict[str, Any]:
        """Single scrape attempt"""
        # Clear previous API responses to free memory
        self.api_responses = {}
        
        # All pages for this scrape share the same browser context.
        if self.context:
            page = await self.context.new_page()
        else:
            page = await self.browser.new_page()
        
        # Request normal HTML content.
        await page.set_extra_http_headers({
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        })
        
        # Set up response interception
        page.on('response', self._handle_response)
        
        try:
            logger.info(f"Loading page: {event_url}")
            
            # Navigate to the event page with longer timeout and domcontentloaded
            # domcontentloaded is faster than networkidle and more reliable
            navigation = await page.goto(
                event_url,
                wait_until='domcontentloaded',
                timeout=180000  # 180 seconds (3 minutes) - some pages are very slow
            )

            content = await page.content()
            self._log_navigation(event_url, page.url, navigation, captcha=False)
            if self._is_captcha_interstitial(page.url, content):
                self._log_navigation(event_url, page.url, navigation, captcha=True)
                raise GotsportCaptchaError("GotSport returned a CAPTCHA verification page; no schedule data was imported")
            
            logger.info("Page loaded, waiting for content...")
            
            # Wait for the page to settle
            await asyncio.sleep(5)
            
            # Try to wait for specific content that indicates the page loaded
            try:
                await page.wait_for_selector('body', timeout=10000)
                logger.info("Body element found")
            except Exception as e:
                logger.warning(f"Could not find body selector: {e}")
            
            # Extract divisions and their schedule URLs from the main event page
            print("[SCRAPER] Extracting divisions from main event page...")
            direct_division = self._direct_group_division(event_url)
            if direct_division:
                divisions_data = [direct_division]
            else:
                divisions_data = await self._extract_divisions_from_event_page(page, event_id, event_url)
            print(f"[SCRAPER] Found {len(divisions_data)} divisions")
            
            # Extract event name from the page
            event_name = None
            print("[SCRAPER] Starting event name extraction...")
            logger.info("Starting event name extraction...")
            try:
                # Wait a bit more to ensure the page is fully loaded
                await asyncio.sleep(2)
                
                # Get page content
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # First try: Look for widget-title which has the full event name
                widget_title = soup.find('div', class_='widget-title')
                if widget_title:
                    event_name = widget_title.get_text(strip=True)
                    print(f"[SCRAPER] Found event name from widget-title: {event_name}")
                    logger.info(f"Found event name from widget-title: {event_name}")
                
                # Second try: Look for navbar-brand-event
                if not event_name:
                    name_elem = soup.find('a', class_='navbar-brand-event')
                    if name_elem:
                        event_name = name_elem.get_text(strip=True)
                        print(f"[SCRAPER] Found event name from navbar-brand-event: {event_name}")
                        logger.info(f"Found event name from navbar-brand-event: {event_name}")
                
                # Third try: Look for navbar-brand spans
                if not event_name:
                    name_elem = soup.find('span', class_=lambda x: x and 'navbar-brand' in str(x))
                    if name_elem:
                        event_name = name_elem.get_text(strip=True)
                        print(f"[SCRAPER] Found event name from navbar-brand span: {event_name}")
                        logger.info(f"Found event name from navbar-brand span: {event_name}")
                
                # Fallback to page title
                if not event_name:
                    title = await page.title()
                    if title and title != 'GotSport':
                        event_name = title.replace(' - GotSport', '').strip()
                        print(f"[SCRAPER] Event name from page title: {event_name}")
                        logger.info(f"Event name from page title: {event_name}")
                
            except Exception as e:
                print(f"[SCRAPER] Error extracting event name: {e}")
                logger.error(f"Error extracting event name: {e}", exc_info=True)
            
            # Extract tournament dates from the HTML
            start_date = None
            end_date = None
            try:
                content = await page.content()
                date_soup = BeautifulSoup(content, 'html.parser')
                
                # Method 1: Look for date range text patterns like "Feb 13-15, 2026"
                text = date_soup.get_text()
                import re
                date_patterns = [
                    r'(\w+\s+\d+)\s*-\s*(\d+),?\s+(\d{4})',  # "Feb 13-15, 2026"
                    r'(\w+\s+\d+),?\s+(\d{4})\s*-\s*(\w+\s+\d+),?\s+(\d{4})',  # "Feb 13, 2026 - Feb 15, 2026"
                ]
                for pattern in date_patterns:
                    match = re.search(pattern, text)
                    if match:
                        groups = match.groups()
                        if len(groups) == 3:  # "Feb 13-15, 2026"
                            month_day_start, day_end, year = groups
                            try:
                                # Parse start date
                                start_date = self._parse_date(f"{month_day_start}, {year}")
                                # Parse end date (use same month)
                                month_parts = month_day_start.split()
                                if len(month_parts) == 2:
                                    month = month_parts[0]
                                    end_date = self._parse_date(f"{month} {day_end}, {year}")
                                print(f"[SCRAPER] Extracted dates from pattern: {start_date} to {end_date}")
                                logger.info(f"Extracted tournament dates: {start_date} to {end_date}")
                                break
                            except Exception as e:
                                print(f"[SCRAPER] Error parsing extracted dates: {e}")
                                logger.warning(f"Error parsing extracted dates: {e}")
                        elif len(groups) == 4:  # "Feb 13, 2026 - Feb 15, 2026"
                            start_str, start_year, end_str, end_year = groups
                            try:
                                start_date = self._parse_date(f"{start_str}, {start_year}")
                                end_date = self._parse_date(f"{end_str}, {end_year}")
                                print(f"[SCRAPER] Extracted dates from pattern: {start_date} to {end_date}")
                                logger.info(f"Extracted tournament dates: {start_date} to {end_date}")
                                break
                            except Exception as e:
                                print(f"[SCRAPER] Error parsing extracted dates: {e}")
                                logger.warning(f"Error parsing extracted dates: {e}")
                
                # Method 2: Look for meta tags or structured data
                if not start_date:
                    meta_start = date_soup.find('meta', {'property': 'event:start_date'})
                    meta_end = date_soup.find('meta', {'property': 'event:end_date'})
                    if meta_start:
                        start_date = self._parse_date(meta_start.get('content'))
                    if meta_end:
                        end_date = self._parse_date(meta_end.get('content'))
                
            except Exception as e:
                print(f"[SCRAPER] Error extracting tournament dates: {e}")
                logger.warning(f"Error extracting tournament dates: {e}")
            
            # Now scrape each division's schedule page
            all_schedules = []
            all_bracket_standings = []
            for division in divisions_data:
                if division.get('schedule_url'):
                    print(f"[SCRAPER] Scraping schedule for division: {division['name']}")
                    try:
                        schedule_response = await page.goto(
                            division['schedule_url'],
                            wait_until='domcontentloaded',
                            timeout=180000,
                        )
                        schedule_content = await page.content()
                        self._log_navigation(
                            division['schedule_url'], page.url, schedule_response,
                            captcha=self._is_captcha_interstitial(page.url, schedule_content),
                        )
                        if self._is_captcha_interstitial(page.url, schedule_content):
                            raise GotsportCaptchaError(
                                "GotSport returned a CAPTCHA verification page for group "
                                f"{division.get('gotsport_division_id', 'unknown')}; no schedule data was imported"
                            )
                        schedule_games = await self._scrape_division_schedule(
                            page, division['schedule_url'], division, html_content=schedule_content
                        )
                        print(f"[SCRAPER] Found {len(schedule_games)} games in {division['name']}")
                        all_schedules.extend(schedule_games)
                        
                        # Also scrape bracket standings from the same page
                        bracket_standings = await self._scrape_bracket_standings(
                            page, division['schedule_url'], division, html_content=schedule_content
                        )
                        if bracket_standings:
                            print(f"[SCRAPER] Found {len(bracket_standings)} bracket standings in {division['name']}")
                            all_bracket_standings.extend(bracket_standings)
                    except GotsportCaptchaError:
                        # A verification page is not an empty division.  Propagate it so
                        # the service records a failed scrape without advancing the event.
                        raise
                    except Exception as e:
                        print(f"[SCRAPER] Error scraping division {division['name']}: {e}")
                        logger.warning(f"Error scraping division {division['name']}: {e}")
            event_data = {
                'event_id': event_id,
                'event': {
                    'gotsport_event_id': event_id,
                    'url': event_url,
                    'name': event_name or f"Event {event_id}",
                    'start_date': start_date,
                    'end_date': end_date
                },
                'divisions': divisions_data,
                'schedules': all_schedules,
                'bracket_standings': all_bracket_standings,
            }
            
            logger.info(f"Scrape successful: {len(all_schedules)} games found")
            return event_data
            
        finally:
            await page.close()

    @staticmethod
    def _is_captcha_interstitial(url: str, content: str) -> bool:
        """Return whether a GotSport response is a verification/interstitial page."""
        candidate = f"{url}\n{content}".lower()
        return "verify_captchas" in candidate or (
            "captcha" in candidate and "gotsport" in candidate and "verify" in candidate
        )

    @staticmethod
    def _log_navigation(requested_url: str, final_url: str, response: Optional[Response], captcha: bool) -> None:
        """Emit navigation diagnostics without recording cookies or headers."""
        status = response.status if response else None
        redirects = []
        request = response.request if response else None
        while request and request.redirected_from:
            request = request.redirected_from
            redirects.append(request.url)
        logger.info(
            "GotSport navigation %s",
            json.dumps({
                "requested_url": requested_url,
                "final_url": final_url,
                "status": status,
                "redirects": list(reversed(redirects)),
                "captcha": captcha,
            }, sort_keys=True),
        )

    @staticmethod
    def _direct_group_division(event_url: str) -> Optional[Dict[str, str]]:
        """Represent an explicitly configured GotSport group as one division source."""
        match = re.search(r'[?&]group=(\d+)', event_url)
        if not match:
            return None
        group_id = match.group(1)
        schedule_url = GotsportScraper._schedule_url_for_group(event_url, group_id)
        return {
            'name': f'Group {group_id}',
            'gotsport_division_id': group_id,
            'schedule_url': schedule_url,
        }

    @staticmethod
    def _schedule_url_for_group(event_url: str, group_id: str) -> str:
        """Canonicalize a group source to the all-date schedule page."""
        parsed = urlparse(event_url)
        schedule_path = parsed.path.replace('/results', '/schedules', 1)
        query = {'date': 'All', 'group': group_id}
        query.update({key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                      if key not in {'date', 'group'}})
        return urlunparse(parsed._replace(path=schedule_path, query=urlencode(query)))
    
    async def _extract_from_api_responses(self, event_id: str) -> Dict[str, Any]:
        """Extract data from intercepted API responses"""
        result = {
            'event_id': event_id,
            'event': {},
            'divisions': [],
            'schedules': [],
        }
        
        print(f"[SCRAPER] Processing {len(self.api_responses)} intercepted API responses")
        logger.info(f"Processing {len(self.api_responses)} intercepted API responses")
        
        # Look through all intercepted responses for relevant data
        for url, data in self.api_responses.items():
            print(f"[SCRAPER] Processing API response from: {url}")
            logger.info(f"Processing API response from: {url}")
            logger.debug(f"Data type: {type(data)}")
            
            # Try to identify the type of data
            if isinstance(data, dict):
                logger.debug(f"Dict keys: {list(data.keys())[:20]}")  # Limit to first 20 keys
                print(f"[SCRAPER] Dict with keys: {list(data.keys())[:10]}")
                
                # Event details
                if 'name' in data and 'start_date' in data:
                    logger.info("Found event data in response")
                    result['event'] = self._normalize_event_data(data)
                
                # Schedule data - check multiple possible keys
                schedule_keys = ['games', 'schedule', 'schedules', 'matches']
                for key in schedule_keys:
                    if key in data:
                        games = data[key]
                        print(f"[SCRAPER] Found {len(games) if isinstance(games, list) else 'unknown'} items under '{key}' key")
                        logger.info(f"Found {len(games) if isinstance(games, list) else 'unknown'} items under '{key}' key")
                        result['schedules'].extend(self._normalize_schedule_data(games))
                
                # Divisions
                if 'divisions' in data:
                    print(f"[SCRAPER] Found {len(data['divisions'])} divisions in response")
                    logger.info(f"Found {len(data['divisions'])} divisions in response")
                    result['divisions'].extend(self._normalize_divisions_data(data['divisions']))
            
            elif isinstance(data, list):
                logger.debug(f"List with {len(data)} items")
                print(f"[SCRAPER] List with {len(data)} items")
                # Could be a list of games or divisions
                if data and isinstance(data[0], dict):
                    first_item_keys = list(data[0].keys())
                    logger.debug(f"First item keys: {first_item_keys[:10]}")
                    
                    if 'game_number' in data[0] or 'home_team' in data[0] or 'homeTeam' in data[0]:
                        print(f"[SCRAPER] Found list of {len(data)} games")
                        logger.info(f"Found list of {len(data)} games")
                        result['schedules'].extend(self._normalize_schedule_data(data))
                    elif 'division_name' in data[0] or 'age_group' in data[0]:
                        print(f"[SCRAPER] Found list of {len(data)} divisions")
                        logger.info(f"Found list of {len(data)} divisions")
                        result['divisions'].extend(self._normalize_divisions_data(data))
        
        print(f"[SCRAPER] API extraction complete: {len(result['schedules'])} games, {len(result['divisions'])} divisions")
        logger.info(f"API extraction complete: {len(result['schedules'])} games, {len(result['divisions'])} divisions")
        return result
    
    async def _extract_from_dom(self, page: Page, event_id: str, event_url: str) -> Dict[str, Any]:
        """Extract data by parsing the DOM (fallback method)"""
        logger.info("Starting DOM extraction")
        
        result = {
            'event_id': event_id,
            'event': {'gotsport_event_id': event_id, 'url': event_url},
            'divisions': [],
            'schedules': [],
        }
        
        # Get page content
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Try to extract event name
        title_elem = soup.find('h1') or soup.find('title')
        if title_elem:
            result['event']['name'] = title_elem.get_text(strip=True)
            logger.info(f"Found event name: {result['event']['name']}")
        
        # Look for embedded JSON data in script tags
        script_tags = soup.find_all('script')
        logger.info(f"Found {len(script_tags)} script tags")
        
        json_found = 0
        for idx, script in enumerate(script_tags):
            script_content = script.string
            if not script_content:
                continue
                
            # Check for relevant keywords
            keywords = ['schedule', 'event', 'games', 'Game', 'Schedule', 'division']
            if any(keyword in script_content for keyword in keywords):
                logger.debug(f"Script tag {idx} contains relevant keywords")
                
                # Try multiple JSON extraction patterns
                patterns = [
                    r'(?:var|const|let)\s+\w+\s*=\s*(\{[^;]+\});',  # Variable assignment
                    r'(?:var|const|let)\s+\w+\s*=\s*(\[[^\]]+\]);',  # Array assignment
                    r'window\.\w+\s*=\s*(\{[^;]+\});',  # Window property
                    r'window\.\w+\s*=\s*(\[[^\]]+\]);',  # Window array
                ]
                
                for pattern in patterns:
                    json_matches = re.findall(pattern, script_content, re.DOTALL)
                    for json_str in json_matches:
                        try:
                            data = json.loads(json_str)
                            json_found += 1
                            logger.debug(f"Parsed JSON object from script, type: {type(data)}")
                            
                            if isinstance(data, dict):
                                logger.debug(f"JSON keys: {list(data.keys())[:10]}")
                                if 'games' in data or 'schedule' in data:
                                    games = data.get('games') or data.get('schedule', [])
                                    logger.info(f"Found {len(games)} games in script JSON")
                                    result['schedules'].extend(self._normalize_schedule_data(games))
                                if 'divisions' in data:
                                    logger.info(f"Found {len(data['divisions'])} divisions in script JSON")
                                    result['divisions'].extend(self._normalize_divisions_data(data['divisions']))
                            elif isinstance(data, list) and data:
                                logger.debug(f"JSON list with {len(data)} items")
                                if isinstance(data[0], dict):
                                    first_keys = list(data[0].keys())[:10]
                                    logger.debug(f"First item keys: {first_keys}")
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.debug(f"Error parsing JSON: {e}")
        
        logger.info(f"Parsed {json_found} JSON objects from script tags")
        
        # Try to scrape schedule table if present
        tables = soup.find_all('table')
        logger.info(f"Found {len(tables)} tables on page")
        
        for idx, table in enumerate(tables):
            # Check if table looks like a schedule
            table_classes = ' '.join(table.get('class', [])).lower()
            table_id = (table.get('id') or '').lower()
            
            if any(keyword in table_classes or keyword in table_id 
                   for keyword in ['schedule', 'game', 'match']):
                logger.info(f"Table {idx} appears to be a schedule table")
                table_games = self._scrape_schedule_table(table)
                if table_games:
                    logger.info(f"Extracted {len(table_games)} games from table")
                    result['schedules'].extend(table_games)
        
        # Try to find division links/tabs
        division_elements = soup.find_all(['a', 'button', 'div'], 
                                         class_=re.compile(r'division|bracket|pool', re.I))
        logger.info(f"Found {len(division_elements)} potential division elements")
        
        for elem in division_elements:
            div_name = elem.get_text(strip=True)
            if div_name and len(div_name) < 100:  # Reasonable division name length
                result['divisions'].append({
                    'name': div_name,
                    'gotsport_division_id': None,
                })
        
        logger.info(f"DOM extraction complete: {len(result['schedules'])} games, {len(result['divisions'])} divisions")
        return result
    
    def _scrape_schedule_table(self, table) -> List[Dict[str, Any]]:
        """Scrape schedule data from an HTML table"""
        games = []
        rows = table.find_all('tr')[1:]  # Skip header row
        
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 4:  # Minimum columns for a valid game row
                game = {}
                
                # This is a generic parser - actual structure may vary
                # Typically: Date, Time, Field, Division, Home, Away, Score
                for i, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    if i == 0:
                        game['game_date'] = text
                    elif i == 1:
                        game['game_time'] = text
                    elif 'field' in cell.get('class', [''])[0].lower():
                        game['field_name'] = text
                    elif 'home' in cell.get('class', [''])[0].lower():
                        game['home_team_name'] = text
                    elif 'away' in cell.get('class', [''])[0].lower():
                        game['away_team_name'] = text
                
                if game:
                    games.append(game)
        
        return games
    
    def _normalize_event_data(self, data: Dict) -> Dict[str, Any]:
        """Normalize event data to standard format"""
        return {
            'gotsport_event_id': str(data.get('event_id') or data.get('id', '')),
            'name': data.get('name', ''),
            'location': data.get('location') or data.get('city'),
            'start_date': self._parse_date(data.get('start_date')),
            'end_date': self._parse_date(data.get('end_date')),
            'url': data.get('url', ''),
        }
    
    def _normalize_divisions_data(self, divisions: List[Dict]) -> List[Dict[str, Any]]:
        """Normalize division data to standard format"""
        result = []
        for div in divisions:
            if isinstance(div, dict):
                result.append({
                    'gotsport_division_id': str(div.get('division_id') or div.get('id', '')),
                    'name': div.get('name') or div.get('division_name', ''),
                    'age_group': div.get('age_group'),
                    'gender': div.get('gender'),
                })
        return result
    
    def _normalize_schedule_data(self, games: List[Dict]) -> List[Dict[str, Any]]:
        """Normalize schedule/game data to standard format"""
        result = []
        for game in games:
            if isinstance(game, dict):
                normalized = {
                    'gotsport_game_id': str(game.get('game_id') or game.get('id', '')),
                    'game_number': game.get('game_number'),
                    'division_name': game.get('division_name') or game.get('division'),
                    'home_team_name': game.get('home_team') or game.get('home'),
                    'away_team_name': game.get('away_team') or game.get('away'),
                    'game_date': self._parse_date(game.get('date') or game.get('game_date')),
                    'game_time': game.get('time') or game.get('game_time'),
                    'field_name': game.get('field') or game.get('field_name'),
                    'field_location': game.get('field_location') or game.get('location'),
                    'home_score': game.get('home_score'),
                    'away_score': game.get('away_score'),
                    'status': game.get('status', 'scheduled'),
                }
                result.append(normalized)
        return result
    
    async def _extract_divisions_from_event_page(self, page: Page, event_id: str, event_url: str) -> List[Dict[str, Any]]:
        """Extract division names and schedule URLs from the main event page"""
        divisions_dict = {}  # Use dict to avoid duplicates, keyed by group_id
        
        # Get page content
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Look for all elements that might contain schedule links
        # Check both href attributes and onclick handlers
        all_elements = soup.find_all(['a', 'button', 'div', 'span'])
        
        for elem in all_elements:
            # Check href attribute
            href = elem.get('href', '')
            onclick = elem.get('onclick', '')
            
            # Look for schedule URLs in either href or onclick
            url_to_check = href if href else onclick
            
            if ('schedules' in url_to_check or 'results' in url_to_check) and 'group=' in url_to_check:
                # Extract group ID
                group_match = re.search(r'group=(\d+)', url_to_check)
                if group_match:
                    group_id = group_match.group(1)
                    
                    # Skip if we already have this division
                    if group_id in divisions_dict:
                        continue
                    
                    # Build full URL
                    if href and href.startswith('http'):
                        schedule_url = href
                    elif href and href.startswith('/'):
                        schedule_url = f"https://system.gotsport.com{href}"
                    else:
                        schedule_url = f"https://system.gotsport.com/org_event/events/{event_id}/schedules?group={group_id}"
                    schedule_url = self._schedule_url_for_group(schedule_url, group_id)
                    
                    # Try to find division name from parent structure
                    # Strategy: Find the panel, then look for age in panel-heading, division in panel-body
                    age_group = None
                    division_qualifier = None
                    
                    # First get division qualifier from the immediate row (look for <b> tag)
                    current = elem
                    for _ in range(8):
                        current = current.parent
                        if not current:
                            break
                        
                        if current.name in ['tr', 'div', 'td']:
                            # Look for <b> tag in this row (division names are often in bold)
                            bold_tags = current.find_all('b')
                            if bold_tags:
                                division_qualifier = bold_tags[0].get_text(strip=True)
                                break
                            
                            # Fallback: get row text
                            row_text = current.get_text(separator=' ', strip=True)
                            # Remove button/navigation text
                            for btn in ['Schedule', 'Standings', 'Bracket', 'View', 'Results']:
                                row_text = row_text.replace(btn, '')
                            row_text = ' '.join(row_text.split()).strip()
                            
                            # Look for division qualifiers
                            if row_text and len(row_text) < 100:
                                if re.search(r'(Championship|Elite|Superior|Premier|Flight|Black|Orange|White|Red|Blue|Green|\d+v\d+)', row_text, re.IGNORECASE):
                                    division_qualifier = row_text
                                    break
                    
                    # Now find age group by looking up to panel level and finding panel-heading
                    current = elem
                    for _ in range(15):
                        current = current.parent
                        if not current:
                            break
                        
                        # Check if this is a panel container (class contains 'panel')
                        class_attr = current.get('class', [])
                        class_str = ' '.join(class_attr) if isinstance(class_attr, list) else str(class_attr)
                        
                        if 'panel' in class_str and 'panel-body' not in class_str:
                            # Found a panel, now look for panel-heading
                            panel_heading = current.find('div', class_=lambda x: x and 'panel-heading' in (x if isinstance(x, str) else ' '.join(x)))
                            if not panel_heading:
                                panel_heading = current.find('div', class_=lambda x: x and 'panel-title' in (x if isinstance(x, str) else ' '.join(x)))
                            
                            if panel_heading:
                                heading_text = panel_heading.get_text(separator=' ', strip=True)
                                # Remove button text
                                for btn in ['Schedule', 'Standings', 'Bracket', 'View', 'Results', 'Calendar']:
                                    heading_text = heading_text.replace(btn, '')
                                heading_text = ' '.join(heading_text.split()).strip()
                                
                                # Look for age group pattern in heading
                                age_match = re.search(r'\b(U\d{1,2}|\d{1,2}U)\b', heading_text, re.IGNORECASE)
                                if age_match:
                                    potential_age = age_match.group(1).upper()
                                    # Normalize format (9U -> U9, 10U -> U10)
                                    if re.match(r'^\d+U$', potential_age):
                                        age_group = 'U' + potential_age[:-1]
                                    else:
                                        age_group = potential_age
                                    break
                    
                    # Combine age group and division qualifier
                    if age_group and division_qualifier:
                        # Check if the division qualifier already contains the age group
                        if age_group.upper() in division_qualifier.upper():
                            text = division_qualifier
                        else:
                            text = f"{age_group} {division_qualifier}"
                    elif division_qualifier:
                        text = division_qualifier
                    elif age_group:
                        text = age_group
                    else:
                        text = None
                    
                    # Clean up the text
                    if text:
                        text = ' '.join(text.split())
                    
                    # If still no good name, use a default
                    if not text:
                        text = f"Division {group_id}"
                    
                    divisions_dict[group_id] = {
                        'name': text,
                        'gotsport_division_id': group_id,
                        'schedule_url': schedule_url,
                    }
                    
                    print(f"[SCRAPER] Found division: {text} (group={group_id})")
        
        return list(divisions_dict.values())
    
    async def _scrape_division_schedule(
        self,
        page: Page,
        schedule_url: str,
        division: Dict,
        html_content: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Parse games from HTML already retrieved by the Playwright page."""
        games = []
        
        try:
            content = html_content
            if content is None and page is not None:
                content = await page.content()
            if content is None:
                raise ValueError(f"No Playwright HTML available for {schedule_url}")
            if self._is_captcha_interstitial(schedule_url, content):
                raise GotsportCaptchaError(
                    "GotSport returned a CAPTCHA verification page for group "
                    f"{division.get('gotsport_division_id', 'unknown')}; no schedule data was imported"
                )
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Extract age group and gender from division name itself only
            # Don't scan the page content as it may pick up team names
            original_name = division['name']
            
            # Look for age groups in the division name (U8, U10, U12, etc.)
            age_match = re.search(r'\b([UO]\d{1,2})\b', original_name, re.IGNORECASE)
            if age_match:
                division['age_group'] = age_match.group(1).upper()
                print(f"[SCRAPER] Detected age group from division name: {division['age_group']}")
            
            # Look for gender indicators in the division name
            gender_match = re.search(r'\b(Boys?|Girls?|Men|Women|Male|Female)\b', original_name, re.IGNORECASE)
            if gender_match:
                gender_text = gender_match.group(1).lower()
                if 'boy' in gender_text:
                    division['gender'] = 'Boys'
                elif 'girl' in gender_text:
                    division['gender'] = 'Girls'
                elif 'men' in gender_text:
                    division['gender'] = 'Men'
                elif 'women' in gender_text:
                    division['gender'] = 'Women'
                print(f"[SCRAPER] Detected gender from division name: {division['gender']}")
            
            # Keep the original division name as-is - don't add prefixes
            print(f"[SCRAPER] Division name: {division['name']}")
            
            # Look for schedule table
            tables = soup.find_all('table')
            
            for table in tables:
                # Check if this looks like a schedule table (not standings)
                headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
                
                # Only process tables that are actual game schedules, not standings
                # Schedule tables have "match #" or "game" and teams columns
                if not any(keyword in ' '.join(headers) for keyword in ['match #', 'match', 'game #']):
                    continue
                    
                # Skip standings tables (they have mp/w/l/d columns)
                if any(keyword in ' '.join(headers) for keyword in ['mp', 'pts', 'gd', 'standings']):
                    continue
                
                print(f"[SCRAPER] Found schedule table with headers: {headers[:7]}")
                
                # Process table rows
                rows = table.find_all('tr')[1:]  # Skip header
                print(f"[SCRAPER] Table has {len(rows)} data rows")
                
                for row_idx, row in enumerate(rows):
                        cells = row.find_all(['td', 'th'])
                        cell_texts = [cell.get_text(strip=True) for cell in cells]
                        
                        # Debug: print first few rows
                        if row_idx < 3:
                            print(f"[SCRAPER] Row {row_idx}: {cell_texts}")
                        
                        if len(cells) >= 3:  # Need at least time and teams
                            game_data = {
                                'division_name': division['name'],
                                'gotsport_division_id': division['gotsport_division_id'],
                            }
                            
                            # If we recognize the exact header structure, use positional parsing
                            if 'match #' in headers or 'match' in headers:
                                print(f"[SCRAPER] Using positional parsing for match table (row {row_idx})")
                                # Gotsport typical format: ['match #', 'time', 'home team', 'results', 'away team', ...]
                                time_idx = next((i for i, h in enumerate(headers) if 'time' in h), None)
                                home_idx = next((i for i, h in enumerate(headers) if 'home' in h), None)
                                away_idx = next((i for i, h in enumerate(headers) if 'away' in h), None)
                                match_idx = next((i for i, h in enumerate(headers) if 'match' in h or 'game' in h), None)
                                field_idx = next((i for i, h in enumerate(headers) if 'field' in h.lower() or 'location' in h.lower()), None)
                                results_idx = next((i for i, h in enumerate(headers) if 'result' in h.lower() or 'score' in h.lower() or h.lower() in ['r', 'res']), None)
                                
                                if row_idx < 2:
                                    print(f"[SCRAPER] Indices - match:{match_idx}, time:{time_idx}, home:{home_idx}, away:{away_idx}, field:{field_idx}, results:{results_idx}")
                                
                                if match_idx is not None and match_idx < len(cell_texts):
                                    game_data['game_number'] = cell_texts[match_idx]
                                
                                if time_idx is not None and time_idx < len(cell_texts):
                                    time_text = cell_texts[time_idx]
                                    # Parse date/time - may be combined like "Feb 14, 20259:10 PM EST"
                                    # Fix common typo where space is missing: "20259:10" -> "2025 9:10"
                                    time_text = re.sub(r'(\d{4})(\d{1,2}:)', r'\1 \2', time_text)
                                    
                                    # Try to split date and time
                                    date_match = re.search(r'([A-Za-z]+\s+\d{1,2},\s+\d{4})', time_text)
                                    time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', time_text, re.IGNORECASE)
                                    
                                    if date_match:
                                        # Parse date string to datetime at noon in Eastern timezone
                                        # Using noon avoids date boundary issues when converting to UTC
                                        date_str = date_match.group(1)
                                        try:
                                            from datetime import datetime as dt
                                            # Parse to naive datetime at noon
                                            parsed_dt = dt.strptime(date_str, '%b %d, %Y').replace(hour=12, minute=0, second=0)
                                            # Make it timezone-aware in Eastern Time
                                            eastern = ZoneInfo('America/New_York')
                                            game_data['game_date'] = parsed_dt.replace(tzinfo=eastern)
                                        except ValueError:
                                            # If parsing fails, store as string for now
                                            game_data['game_date'] = date_str
                                    if time_match:
                                        game_data['game_time'] = time_match.group(1)
                                    
                                    # If we couldn't parse, store the whole thing
                                    if not date_match and not time_match:
                                        game_data['game_time'] = time_text
                                
                                if home_idx is not None and home_idx < len(cell_texts):
                                    game_data['home_team_name'] = cell_texts[home_idx]
                                
                                if away_idx is not None and away_idx < len(cell_texts):
                                    game_data['away_team_name'] = cell_texts[away_idx]
                                
                                if field_idx is not None and field_idx < len(cell_texts):
                                    game_data['field_name'] = cell_texts[field_idx]
                                else:
                                    # Field might not have a header, look for it in remaining cells
                                    for i, text in enumerate(cell_texts):
                                        if i not in [match_idx, time_idx, home_idx, away_idx, results_idx] and text:
                                            # Check if it looks like a field name (contains "court", "field", or similar)
                                            if any(word in text.lower() for word in ['court', 'field', 'pitch']):
                                                game_data['field_name'] = text
                                                break
                                
                                # Parse results/scores if available
                                if results_idx is not None and results_idx < len(cell_texts):
                                    results_text = cell_texts[results_idx].strip()
                                    # Common score patterns: "3-2", "3 - 2", "3:2", "3 to 2"
                                    score_match = re.match(r'(\d+)\s*[-:to]\s*(\d+)', results_text, re.IGNORECASE)
                                    if score_match:
                                        game_data['home_score'] = int(score_match.group(1))
                                        game_data['away_score'] = int(score_match.group(2))
                                        game_data['status'] = 'completed'
                                        if row_idx < 2:
                                            print(f"[SCRAPER] Parsed score: {game_data['home_score']}-{game_data['away_score']}")
                                    elif results_text and results_text not in ['', '-', 'vs', 'TBD']:
                                        # Try to parse other formats
                                        if row_idx < 2:
                                            print(f"[SCRAPER] Unparsed results format: '{results_text}'")
                            else:
                                # Fallback: intelligent parsing based on content
                                for i, cell_text in enumerate(cell_texts):
                                    # Date patterns
                                    if re.match(r'\d{1,2}/\d{1,2}', cell_text):
                                        game_data['game_date'] = cell_text
                                    # Time patterns
                                    elif re.match(r'\d{1,2}:\d{2}', cell_text):
                                        game_data['game_time'] = cell_text
                                    # Field patterns
                                    elif 'field' in cell_text.lower() or (cell_text.isdigit() and len(cell_text) <= 3):
                                        if 'field_name' not in game_data:
                                            game_data['field_name'] = cell_text
                                    # Game number patterns
                                    elif re.match(r'^[A-Z]?\d+$', cell_text) and len(cell_text) <= 4:
                                        if 'game_number' not in game_data:
                                            game_data['game_number'] = cell_text
                                    # Teams - longer text that's not a common keyword
                                    elif len(cell_text) > 3 and not any(x in cell_text.lower() for x in ['field', 'final', 'score', 'result', 'vs']):
                                        if 'home_team_name' not in game_data:
                                            game_data['home_team_name'] = cell_text
                                        elif 'away_team_name' not in game_data:
                                            game_data['away_team_name'] = cell_text
                            
                            # Only add if we have minimum required data (at least one team)
                            if row_idx < 2:  # Debug first 2 rows
                                print(f"[SCRAPER] Game data extracted: {game_data}")
                            
                            if game_data.get('home_team_name') or game_data.get('away_team_name'):
                                games.append(game_data)
                                if row_idx < 2:  # Debug
                                    print(f"[SCRAPER] Game added: {game_data.get('game_number', 'N/A')} - {game_data.get('home_team_name', '?')} vs {game_data.get('away_team_name', '?')}")
                            else:
                                if row_idx < 2:  # Debug
                                    print(f"[SCRAPER] Game skipped - no team names found")
            
        except Exception as e:
            logger.error(f"Error scraping division schedule from {schedule_url}: {e}")
            raise
        
        return games
    
    async def _scrape_bracket_standings(
        self,
        page: Page,
        schedule_url: str,
        division: Dict,
        html_content: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scrape bracket standings from a division page.
        Looks for standings tables with columns like: Team, MP, W, D, L, GF, GA, GD, PTS
        """
        standings = []
        
        try:
            print(f"[SCRAPER] Parsing bracket standings from: {schedule_url}")
            if html_content is None and page is not None:
                html_content = await page.content()
            if html_content is None:
                raise ValueError(f"No Playwright HTML available for {schedule_url}")
            if self._is_captcha_interstitial(schedule_url, html_content):
                raise GotsportCaptchaError(
                    "GotSport returned a CAPTCHA verification page for group "
                    f"{division.get('gotsport_division_id', 'unknown')}; no standings were imported"
                )
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Find all tables
            tables = soup.find_all('table')
            unknown_bracket_index = 0
            
            for table in tables:
                headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
                
                # Only process standings tables (they have mp/w/l/d/pts columns)
                if not any(keyword in ' '.join(headers) for keyword in ['pts', 'points', 'mp', 'gp']):
                    continue
                
                # Skip schedule tables
                if any(keyword in ' '.join(headers) for keyword in ['match #', 'game #', 'time', 'location']):
                    continue
                
                print(f"[SCRAPER] Found standings table with headers: {headers}")
                
                # Try to find the bracket name from the panel structure
                bracket_name = "Unknown Bracket"
                
                # First try to find the panel-collapse ancestor (GotSport uses Bootstrap panels)
                panel_collapse = table.find_parent(class_='panel-collapse')
                if panel_collapse and panel_collapse.get('id'):
                    # Extract the panel ID (e.g., 'collapse-648115')
                    panel_id = panel_collapse.get('id')
                    # Find the corresponding panel-heading by ID pattern
                    bracket_id = panel_id.replace('collapse-', 'bracket-')
                    panel_heading = soup.find('div', id=bracket_id)
                    if panel_heading:
                        # Get the text from the anchor tag in panel-title
                        panel_title = panel_heading.find(class_='panel-title')
                        if panel_title:
                            link = panel_title.find('a')
                            if link:
                                bracket_name = link.get_text(strip=True)
                
                # Fallback: Look for a panel heading/title in the closest panel
                if bracket_name == "Unknown Bracket":
                    panel = table.find_parent(class_='panel')
                    if panel:
                        panel_heading = panel.find(class_='panel-heading')
                        if panel_heading:
                            link = panel_heading.find('a')
                            if link and link.get_text(strip=True):
                                bracket_name = link.get_text(strip=True)
                            else:
                                heading_text = panel_heading.get_text(strip=True)
                                if heading_text:
                                    bracket_name = heading_text

                # Fallback: find the closest previous bracket heading element
                if bracket_name == "Unknown Bracket":
                    prev_heading = table.find_previous(
                        lambda tag: tag.name in ['div', 'a']
                        and ((tag.get('id') or '').startswith('bracket-')
                             or 'panel-heading' in (tag.get('class') or []))
                    )
                    if prev_heading:
                        link = prev_heading.find('a') if prev_heading.name == 'div' else prev_heading
                        heading_text = link.get_text(strip=True) if link else ''
                        if heading_text:
                            bracket_name = heading_text

                # Fallback: table caption text
                if bracket_name == "Unknown Bracket":
                    caption = table.find('caption')
                    if caption:
                        caption_text = caption.get_text(strip=True)
                        if caption_text:
                            bracket_name = caption_text

                # Fallback: Look for headings
                if bracket_name == "Unknown Bracket":
                    parent = table.find_parent()
                    if parent:
                        # Look for a heading before the table
                        for sibling in parent.find_all_previous(['h1', 'h2', 'h3', 'h4', 'h5', 'strong', 'div']):
                            text = sibling.get_text(strip=True)
                            if not text:
                                continue
                            if sibling.name == 'div':
                                class_list = sibling.get('class') or []
                                if 'panel-title' in class_list or 'panel-heading' in class_list:
                                    bracket_name = text
                                    break
                            if 'bracket' in text.lower() or 'group' in text.lower() or 'pool' in text.lower():
                                bracket_name = text
                                break

                # Final fallback: avoid returning "Unknown Bracket" labels
                if bracket_name == "Unknown Bracket":
                    unknown_bracket_index += 1
                    bracket_name = f"Bracket {unknown_bracket_index}"
                
                print(f"[SCRAPER] Bracket name: {bracket_name}")
                
                # Map column names to indices
                col_map = {}
                for idx, header in enumerate(headers):
                    header = header.lower()
                    if 'team' in header or 'club' in header:
                        col_map['team'] = idx
                    elif header in ['mp', 'gp', 'p', 'played', 'games']:
                        col_map['played'] = idx
                    elif header in ['w', 'win', 'wins']:
                        col_map['wins'] = idx
                    elif header in ['d', 'draw', 'draws', 't', 'tie', 'ties']:
                        col_map['draws'] = idx
                    elif header in ['l', 'loss', 'losses']:
                        col_map['losses'] = idx
                    elif header in ['gf', 'f', 'gs', 'goals for', 'scored']:
                        col_map['gf'] = idx
                    elif header in ['ga', 'a', 'gc', 'goals against', 'conceded']:
                        col_map['ga'] = idx
                    elif header in ['gd', '+/-', 'diff', 'goal difference']:
                        col_map['gd'] = idx
                    elif header in ['pts', 'pt', 'points']:
                        col_map['pts'] = idx
                
                if 'team' not in col_map or 'pts' not in col_map:
                    print(f"[SCRAPER] Skipping table - missing required columns (team or pts)")
                    continue
                
                # Process table rows
                rows = table.find_all('tr')[1:]  # Skip header
                print(f"[SCRAPER] Table has {len(rows)} data rows")
                
                for row_idx, row in enumerate(rows):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < len(headers):
                        continue
                    
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    
                    try:
                        standing_data = {
                            'division_name': division['name'],
                            'gotsport_division_id': division.get('gotsport_division_id'),
                            'bracket_name': bracket_name,
                            'team_name': cell_texts[col_map['team']],
                            'played': int(cell_texts[col_map.get('played', 0)]) if col_map.get('played') and cell_texts[col_map.get('played', 0)].isdigit() else 0,
                            'wins': int(cell_texts[col_map.get('wins', 0)]) if col_map.get('wins') and cell_texts[col_map.get('wins', 0)].isdigit() else 0,
                            'draws': int(cell_texts[col_map.get('draws', 0)]) if col_map.get('draws') and cell_texts[col_map.get('draws', 0)].isdigit() else 0,
                            'losses': int(cell_texts[col_map.get('losses', 0)]) if col_map.get('losses') and cell_texts[col_map.get('losses', 0)].isdigit() else 0,
                            'goals_for': int(cell_texts[col_map.get('gf', 0)]) if col_map.get('gf') and cell_texts[col_map.get('gf', 0)].isdigit() else 0,
                            'goals_against': int(cell_texts[col_map.get('ga', 0)]) if col_map.get('ga') and cell_texts[col_map.get('ga', 0)].isdigit() else 0,
                            'goal_difference': int(cell_texts[col_map.get('gd', 0)]) if col_map.get('gd') and cell_texts[col_map.get('gd', 0)].replace('-', '').isdigit() else 0,
                            'points': int(cell_texts[col_map['pts']]) if cell_texts[col_map['pts']].isdigit() else 0,
                        }
                        
                        if standing_data['team_name']:
                            standings.append(standing_data)
                            if row_idx < 2:  # Debug
                                print(f"[SCRAPER] Standing added: {standing_data['team_name']} - {standing_data['points']} pts")
                    
                    except Exception as e:
                        print(f"[SCRAPER] Error parsing standing row: {e}")
                        logger.warning(f"Error parsing standing row: {e}")
        
        except Exception as e:
            logger.warning(f"Error scraping bracket standings from {schedule_url}: {e}")
        
        return standings
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string to datetime object"""
        if not date_str:
            return None
        
        try:
            # Try ISO format first
            if 'T' in str(date_str):
                return datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            # Try common date formats
            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S']:
                try:
                    return datetime.strptime(str(date_str), fmt)
                except ValueError:
                    continue
        except Exception as e:
            logger.warning(f"Could not parse date: {date_str} - {e}")
        
        return None
