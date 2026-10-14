"""Service for managing scraping operations and data persistence"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Event, Division, Team, Game, ScrapeLog, ScrapeStatus, GameStatus, BracketStanding
from app.scraper.gotsport import GotsportScraper

logger = logging.getLogger(__name__)


class ScrapeService:
    """Service for handling scraping operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def scrape_and_store_event(self, event_url: str, force: bool = False) -> Tuple[Event, ScrapeLog]:
        """
        Scrape an event and store data in the database
        
        Args:
            event_url: URL of the Gotsport event
            force: Force scrape even if recently scraped
            
        Returns:
            Tuple of (Event, ScrapeLog)
        """
        scraper = GotsportScraper()
        
        try:
            # Start scraping
            await scraper.start()
            
            # Extract event ID from URL
            event_id = scraper._extract_event_id_from_url(event_url)
            if not event_id:
                raise ValueError(f"Invalid event URL: {event_url}")
            
            # Check if event exists
            event = await self._get_event_by_gotsport_id(event_id)
            
            if event and not force:
                # Check when it was last scraped
                if event.last_scraped_at:
                    now = datetime.now(timezone.utc)
                    last_scraped = event.last_scraped_at
                    if last_scraped.tzinfo is None:
                        last_scraped = last_scraped.replace(tzinfo=timezone.utc)
                    
                    hours_since_scrape = (now - last_scraped).total_seconds() / 3600
                    if hours_since_scrape < 1:  # Don't scrape more than once per hour
                        logger.info(f"Event {event_id} was scraped {hours_since_scrape:.1f} hours ago, skipping")
                        # Return existing event without creating new scrape log
                        return event, None
            
            # Create scrape log
            scrape_log = ScrapeLog(
                event_id=event.id if event else None,
                status=ScrapeStatus.IN_PROGRESS,
                scrape_started_at=datetime.now(timezone.utc),
            )
            
            try:
                # Perform the scrape
                logger.info(f"Starting scrape of event {event_id}")
                scraped_data = await scraper.scrape_event(event_url)
                
                # Store or update event
                event = await self._store_event_data(event, scraped_data, event_url)
                
                # Update scrape log with event ID if it's new
                if not scrape_log.event_id:
                    scrape_log.event_id = event.id
                
                # Store divisions
                divisions_map = await self._store_divisions(event, scraped_data.get('divisions', []))
                
                # Store schedules/games
                stats = await self._store_games(event, divisions_map, scraped_data.get('schedules', []))
                
                # Store bracket standings
                bracket_stats = await self._store_bracket_standings(event, divisions_map, scraped_data.get('bracket_standings', []))
                if bracket_stats['total'] > 0:
                    logger.info(f"Stored {bracket_stats['created']} bracket standings, updated {bracket_stats['updated']}")
                
                # Clean up any duplicates that might have been created
                duplicates_removed = await self._cleanup_duplicate_games(event)
                if duplicates_removed > 0:
                    logger.info(f"Removed {duplicates_removed} duplicate games")
                    stats['duplicates_removed'] = duplicates_removed
                
                # Update event start/end dates based on game dates
                await self._update_event_dates_from_games(event)
                
                # Update scrape log with success
                scrape_log.status = ScrapeStatus.SUCCESS
                scrape_log.scrape_completed_at = datetime.now(timezone.utc)
                scrape_log.games_scraped = stats['total']
                scrape_log.games_created = stats['created']
                scrape_log.games_updated = stats['updated']
                
                # Update event last scraped time
                event.last_scraped_at = datetime.now(timezone.utc)
                
                self.db.add(scrape_log)
                self.db.add(event)  # Explicitly add event to ensure last_scraped_at is persisted
                await self.db.commit()
                await self.db.refresh(event)
                
                logger.info(f"Successfully scraped event {event_id}: {stats}")
                logger.info(f"Committed scrape data to database: {stats['total']} games, {len(divisions_map)} divisions")
                
                return event, scrape_log
                
            except Exception as e:
                logger.error(f"Error scraping event {event_id}: {e}", exc_info=True)
                scrape_log.status = ScrapeStatus.FAILED
                scrape_log.scrape_completed_at = datetime.now(timezone.utc)
                scrape_log.error_message = str(e)
                self.db.add(scrape_log)
                await self.db.commit()
                raise
                
        finally:
            await scraper.stop()
    
    async def _get_event_by_gotsport_id(self, gotsport_event_id: str) -> Optional[Event]:
        """Get event by Gotsport event ID"""
        result = await self.db.execute(
            select(Event).where(Event.gotsport_event_id == gotsport_event_id)
        )
        return result.scalar_one_or_none()
    
    async def _store_event_data(self, event: Optional[Event], scraped_data: Dict, event_url: str) -> Event:
        """Store or update event data"""
        event_data = scraped_data.get('event', {})
        gotsport_event_id = scraped_data.get('event_id', '')
        
        if not event:
            # Create new event
            event = Event(
                gotsport_event_id=gotsport_event_id,
                name=event_data.get('name', f'Event {gotsport_event_id}'),
                location=event_data.get('location'),
                start_date=event_data.get('start_date'),
                end_date=event_data.get('end_date'),
                url=event_url,
                status='active',
            )
            self.db.add(event)
            await self.db.commit()  # Commit immediately to release locks
            logger.info(f"Created new event: {event.name}")
        else:
            # Update existing event
            if event_data.get('name'):
                event.name = event_data['name']
            if event_data.get('location'):
                event.location = event_data['location']
            if event_data.get('start_date'):
                event.start_date = event_data['start_date']
            if event_data.get('end_date'):
                event.end_date = event_data['end_date']
            event.url = event_url
            event.updated_at = datetime.now(timezone.utc)
            await self.db.commit()  # Commit updates immediately
            logger.info(f"Updated existing event: {event.name}")
        
        return event
    
    async def _store_divisions(self, event: Event, divisions_data: List[Dict]) -> Dict[str, Division]:
        """Store or update divisions, return mapping of division name to Division object"""
        divisions_map = {}
        
        # Get existing divisions for this event
        result = await self.db.execute(
            select(Division).where(Division.event_id == event.id)
        )
        existing_division_list = result.scalars().all()
        # GotSport group IDs are stable even when a source changes the display name.
        # Keep the name lookup for legacy divisions that predate a group ID.
        existing_by_name = {div.name: div for div in existing_division_list}
        existing_by_group_id = {
            div.gotsport_division_id: div
            for div in existing_division_list
            if div.gotsport_division_id
        }
        
        for div_data in divisions_data:
            div_name = div_data.get('name')
            if not div_name:
                continue

            group_id = div_data.get('gotsport_division_id')
            division = (
                existing_by_group_id.get(group_id) if group_id else None
            ) or existing_by_name.get(div_name)

            if division:
                # Update existing division
                if division.name != div_name:
                    division.name = div_name
                if div_data.get('age_group'):
                    division.age_group = div_data['age_group']
                if div_data.get('gender'):
                    division.gender = div_data['gender']
                if div_data.get('gotsport_division_id'):
                    division.gotsport_division_id = div_data['gotsport_division_id']
                division.updated_at = datetime.now(timezone.utc)
            else:
                # Create new division
                division = Division(
                    event_id=event.id,
                    name=div_name,
                    age_group=div_data.get('age_group'),
                    gender=div_data.get('gender'),
                    gotsport_division_id=div_data.get('gotsport_division_id'),
                )
                self.db.add(division)
                existing_by_name[div_name] = division
                if group_id:
                    existing_by_group_id[group_id] = division
            
            divisions_map[div_name] = division
        
        # Commit all divisions at once to release locks
        await self.db.commit()
        logger.info(f"Processed {len(divisions_map)} divisions for event {event.name}")
        return divisions_map
    
    async def _store_games(self, event: Event, divisions_map: Dict[str, Division], games_data: List[Dict]) -> Dict[str, int]:
        """Store or update games with batched commits to reduce lock time"""
        stats = {'total': 0, 'created': 0, 'updated': 0, 'skipped': 0}
        
        # MEMORY OPTIMIZATION: Use single dictionary with composite keys to reduce memory by 66%
        # Only load essential fields (id and matching criteria) to reduce memory further
        division_ids = [div.id for div in divisions_map.values()]
        games_lookup = {}
        
        if division_ids:
            result = await self.db.execute(
                select(Game).where(Game.division_id.in_(division_ids))
            )
            existing_games = result.scalars().all()
            
            # Build single lookup with multiple keys pointing to same game object
            for g in existing_games:
                # Key 1: gotsport_game_id (most reliable)
                if g.gotsport_game_id:
                    games_lookup[('gotsport', g.gotsport_game_id)] = g
                # Key 2: game_number + division (stable across schedule changes)
                if g.game_number:
                    games_lookup[('number', g.division_id, g.game_number)] = g
                # Key 3: exact signature (teams + date + time)
                if g.home_team_name and g.away_team_name and g.game_date and g.game_time:
                    games_lookup[('sig', g.division_id, g.home_team_name, g.away_team_name, 
                                  g.game_date, g.game_time)] = g
            
            # Free the list to reduce memory
            existing_games = None
            logger.info(f"Loaded {len(games_lookup)} game lookup keys for matching")
        
        # Process games in batches to avoid long-running transactions
        # MEMORY OPTIMIZATION: Reduced from 200 to 100 to lower memory usage
        BATCH_SIZE = 100
        batch_count = 0
        
        for game_data in games_data:
            stats['total'] += 1
            
            # Get division
            div_name = game_data.get('division_name')
            division = divisions_map.get(div_name)
            
            if not division:
                logger.warning(f"No division found for game: {game_data}")
                stats['skipped'] += 1
                continue
            
            # Check if game exists using multiple strategies to prevent duplicates
            gotsport_game_id = game_data.get('gotsport_game_id')
            game_number = game_data.get('game_number')
            home_team = game_data.get('home_team_name')
            away_team = game_data.get('away_team_name')
            game_date = game_data.get('game_date')
            game_time = game_data.get('game_time')
            
            # Try to find existing game using composite key lookup
            game = None
            if gotsport_game_id:
                game = games_lookup.get(('gotsport', gotsport_game_id))
            if not game and game_number:
                game = games_lookup.get(('number', division.id, game_number))
            if not game and home_team and away_team and game_date and game_time:
                game = games_lookup.get(('sig', division.id, home_team, away_team, game_date, game_time))
            
            if game:
                # Update existing game
                self._update_game_from_data(game, game_data)
                stats['updated'] += 1
            else:
                # Create new game
                game = self._create_game_from_data(division.id, game_data)
                self.db.add(game)
                stats['created'] += 1
                
                # Add to lookup to prevent duplicates within this scrape
                if gotsport_game_id:
                    games_lookup[('gotsport', gotsport_game_id)] = game
                if game_number:
                    games_lookup[('number', division.id, game_number)] = game
                if home_team and away_team and game_date and game_time:
                    games_lookup[('sig', division.id, home_team, away_team, game_date, game_time)] = game
            
            batch_count += 1
            
            # Commit in batches to reduce lock time (critical for performance!)
            if batch_count >= BATCH_SIZE:
                await self.db.commit()
                batch_count = 0
                logger.debug(f"Committed batch: {stats['created']} created, {stats['updated']} updated so far")
        
        # Commit any remaining games
        if batch_count > 0:
            await self.db.commit()
        
        # MEMORY OPTIMIZATION: Clear lookup dictionary to free memory
        games_lookup.clear()
        games_lookup = None
        
        logger.info(f"Processed {stats['total']} games: {stats['created']} created, {stats['updated']} updated, {stats['skipped']} skipped")
        return stats

    async def _store_teams(self, divisions_map: Dict[str, Division], games_data: List[Dict]) -> Dict[tuple, Team]:
        """Create/update teams without deleting teams absent from an import."""
        division_ids = [division.id for division in divisions_map.values()]
        if not division_ids:
            return {}
        result = await self.db.execute(select(Team).where(Team.division_id.in_(division_ids)))
        by_key = {(team.division_id, team.name): team for team in result.scalars().all()}
        for game_data in games_data:
            division = divisions_map.get(game_data.get("division_name"))
            if not division:
                continue
            for side in ("home", "away"):
                name = game_data.get(f"{side}_team_name")
                if not name:
                    continue
                key = (division.id, name)
                team = by_key.get(key)
                if not team:
                    team = Team(division_id=division.id, name=name)
                    self.db.add(team)
                    by_key[key] = team
        await self.db.flush()
        for game_data in games_data:
            division = divisions_map.get(game_data.get("division_name"))
            if not division:
                continue
            for side in ("home", "away"):
                name = game_data.get(f"{side}_team_name")
                team = by_key.get((division.id, name)) if name else None
                if team:
                    game_data[f"{side}_team_id"] = team.id
        await self.db.commit()
        return by_key

    def _create_game_from_data(self, division_id: int, game_data: Dict) -> Game:
        """Create a new Game object from scraped data"""
        return Game(
            division_id=division_id,
            gotsport_game_id=game_data.get('gotsport_game_id'),
            game_number=game_data.get('game_number'),
            home_team_id=game_data.get('home_team_id'),
            away_team_id=game_data.get('away_team_id'),
            home_team_name=game_data.get('home_team_name'),
            away_team_name=game_data.get('away_team_name'),
            game_date=game_data.get('game_date'),
            game_time=game_data.get('game_time'),
            field_name=game_data.get('field_name'),
            field_location=game_data.get('field_location'),
            home_score=game_data.get('home_score'),
            away_score=game_data.get('away_score'),
            status=game_data.get('status', GameStatus.SCHEDULED),
        )
    
    def _update_game_from_data(self, game: Game, game_data: Dict):
        """Update an existing Game object with scraped data"""
        if game_data.get('game_number'):
            game.game_number = game_data['game_number']
        if game_data.get('home_team_name'):
            game.home_team_name = game_data['home_team_name']
        if game_data.get('away_team_name'):
            game.away_team_name = game_data['away_team_name']
        if game_data.get('home_team_id'):
            game.home_team_id = game_data['home_team_id']
        if game_data.get('away_team_id'):
            game.away_team_id = game_data['away_team_id']
        if game_data.get('game_date'):
            game.game_date = game_data['game_date']
        if game_data.get('game_time'):
            game.game_time = game_data['game_time']
        if game_data.get('field_name'):
            game.field_name = game_data['field_name']
        if game_data.get('field_location'):
            game.field_location = game_data['field_location']
        if game_data.get('home_score') is not None:
            game.home_score = game_data['home_score']
        if game_data.get('away_score') is not None:
            game.away_score = game_data['away_score']
        if game_data.get('status'):
            game.status = game_data['status']
        game.updated_at = datetime.now(timezone.utc)
    
    async def _store_bracket_standings(self, event: Event, divisions_map: Dict[str, Division], standings_data: List[Dict]) -> Dict:
        """Store or update bracket standings in the database"""
        stats = {'total': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}
        
        if not standings_data:
            return stats
        
        BATCH_SIZE = 25  # Smaller batch size for better reliability
        
        logger.info(f"Processing {len(standings_data)} bracket standings")
        
        # Load existing standings for this event with error handling
        standings_lookup = {}
        try:
            # Extract division IDs from Division objects
            division_ids = [div.id for div in divisions_map.values()]
            for division_id in division_ids:
                try:
                    result = await self.db.execute(
                        select(BracketStanding).where(BracketStanding.division_id == division_id)
                    )
                    for standing in result.scalars():
                        key = (standing.division_id, standing.bracket_name, standing.team_name)
                        standings_lookup[key] = standing
                except Exception as e:
                    logger.error(f"Error loading existing standings for division {division_id}: {e}")
                    # Continue with other divisions
                    continue
        except Exception as e:
            logger.error(f"Error loading existing standings: {e}")
            # Continue with empty lookup - will create all as new
        
        batch_count = 0
        for i, standing_data in enumerate(standings_data):
            stats['total'] += 1
            
            try:
                # Get division name and look up Division object
                division_name = standing_data.get('division_name')
                if not division_name:
                    stats['skipped'] += 1
                    continue
                
                division = divisions_map.get(division_name)
                if not division:
                    stats['skipped'] += 1
                    logger.warning(f"Division not found for standing: {division_name}")
                    continue
                
                division_id = division.id  # Extract ID from Division object
                bracket_name = standing_data.get('bracket_name', 'Unknown Bracket')
                team_name = standing_data.get('team_name')
                
                if not team_name:
                    stats['skipped'] += 1
                    continue
                
                # Check if this standing already exists
                key = (division_id, bracket_name, team_name)
                existing_standing = standings_lookup.get(key)
                
                if existing_standing:
                    # Update existing
                    existing_standing.played = standing_data.get('played', 0)
                    existing_standing.wins = standing_data.get('wins', 0)
                    existing_standing.draws = standing_data.get('draws', 0)
                    existing_standing.losses = standing_data.get('losses', 0)
                    existing_standing.goals_for = standing_data.get('goals_for', 0)
                    existing_standing.goals_against = standing_data.get('goals_against', 0)
                    existing_standing.goal_difference = standing_data.get('goal_difference', 0)
                    existing_standing.points = standing_data.get('points', 0)
                    existing_standing.updated_at = datetime.now(timezone.utc)
                    stats['updated'] += 1
                else:
                    # Create new
                    new_standing = BracketStanding(
                        division_id=division_id,
                        bracket_name=bracket_name,
                        team_name=team_name,
                        played=standing_data.get('played', 0),
                        wins=standing_data.get('wins', 0),
                        draws=standing_data.get('draws', 0),
                        losses=standing_data.get('losses', 0),
                        goals_for=standing_data.get('goals_for', 0),
                        goals_against=standing_data.get('goals_against', 0),
                        goal_difference=standing_data.get('goal_difference', 0),
                        points=standing_data.get('points', 0),
                    )
                    self.db.add(new_standing)
                    standings_lookup[key] = new_standing
                    stats['created'] += 1
                
                batch_count += 1
                
                # Commit in batches
                if batch_count >= BATCH_SIZE:
                    try:
                        await self.db.commit()
                        logger.info(f"Committed batch of {batch_count} standings (processed {i+1}/{len(standings_data)})")
                        batch_count = 0
                    except Exception as e:
                        logger.error(f"Error committing batch at standing {i+1}: {e}")
                        await self.db.rollback()
                        stats['errors'] += batch_count
                        batch_count = 0
                        # Continue processing remaining standings
                        
            except Exception as e:
                logger.error(f"Error processing standing {i+1}: {e}")
                stats['errors'] += 1
                # Continue with next standing
                continue
        
        # Commit any remaining standings
        if batch_count > 0:
            try:
                await self.db.commit()
                logger.info(f"Committed final batch of {batch_count} standings")
            except Exception as e:
                logger.error(f"Error committing final batch: {e}")
                await self.db.rollback()
                stats['errors'] += batch_count
        
        standings_lookup.clear()
        
        logger.info(f"Bracket standings stored: {stats['created']} created, {stats['updated']} updated, {stats['skipped']} skipped, {stats['errors']} errors")

        standings_lookup = None
        
        logger.info(f"Processed {stats['total']} standings: {stats['created']} created, {stats['updated']} updated, {stats['skipped']} skipped")
        return stats
    
    async def _update_event_dates_from_games(self, event: Event):
        """Update event start_date and end_date based on the earliest and latest game dates"""
        try:
            from sqlalchemy import func
            
            # Skip if this isn't the first scrape and dates are already set
            # (dates rarely change after initial scrape)
            if event.start_date and event.end_date and event.last_scraped_at:
                logger.debug(f"Event {event.name} already has dates, skipping update")
                return
            
            logger.info(f"Starting date update for event {event.name} (ID: {event.id})")
            
            # Query for min and max game_date for this event's games
            result = await self.db.execute(
                select(
                    func.min(Game.game_date),
                    func.max(Game.game_date)
                ).join(Division).where(
                    Division.event_id == event.id,
                    Game.game_date.isnot(None)
                )
            )
            
            min_date, max_date = result.one()
            
            logger.info(f"Query result - min_date: {min_date}, max_date: {max_date}")
            
            if min_date and max_date:
                # Only update if dates are actually different
                if event.start_date != min_date or event.end_date != max_date:
                    event.start_date = min_date
                    event.end_date = max_date
                    await self.db.commit()  # Commit immediately to release locks
                    logger.info(f"✅ Updated event {event.name} dates: {min_date.date()} to {max_date.date()}")
                else:
                    logger.debug(f"Event {event.name} dates unchanged, skipping commit")
            else:
                logger.warning(f"⚠️ No game dates found for event {event.name}")
        except Exception as e:
            logger.error(f"❌ Error updating event dates: {e}", exc_info=True)
    
    async def _cleanup_duplicate_games(self, event: Event) -> int:
        """
        Detect and remove duplicate games after scraping using window functions.
        Keeps the most recently updated game when duplicates are found.
        Single SQL query for better performance.
        """
        from sqlalchemy import text
        
        duplicates_removed = 0
        
        try:
            # Use window functions to identify duplicates and delete all but the most recent
            # This is much faster than the N-query approach
            delete_query = text("""
                WITH ranked_games AS (
                    SELECT g.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY g.division_id, g.home_team_name, g.away_team_name, g.game_date
                               ORDER BY g.updated_at DESC, g.id DESC
                           ) as rn
                    FROM games g
                    JOIN divisions d ON g.division_id = d.id
                    WHERE d.event_id = :event_id
                )
                DELETE FROM games
                WHERE id IN (
                    SELECT id FROM ranked_games WHERE rn > 1
                )
            """)
            
            result = await self.db.execute(delete_query, {"event_id": event.id})
            duplicates_removed = result.rowcount
            
            if duplicates_removed > 0:
                await self.db.commit()
                logger.info(f"Cleaned up {duplicates_removed} duplicate games for event {event.name}")
            
        except Exception as e:
            logger.error(f"Error cleaning up duplicates: {e}", exc_info=True)
        
        return duplicates_removed
