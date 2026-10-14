'use client';

import { useQuery } from '@tanstack/react-query';
import { schedulesApi, eventsApi, Game } from '@/lib/api';
import { useState, use, useEffect, useMemo } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import AdminModal from '@/components/AdminModal';

type FilterType = 'all' | 'division' | 'team' | 'location' | 'favorites' | 'current' | 'myclub';
type StatusFilter = 'all' | 'upcoming' | 'completed';

export default function EventPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const eventId = parseInt(id);
  const searchParams = useSearchParams();
  const router = useRouter();
  const combinedView = searchParams.get('events') === 'all';
  const [selectedDivision, setSelectedDivision] = useState<number | undefined>();
  const [teamFilter, setTeamFilter] = useState('');
  const [locationFilter, setLocationFilter] = useState('');
  const [filterType, setFilterType] = useState<FilterType>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [selectedEventIds, setSelectedEventIds] = useState<number[]>([]);
  const [eventSelectionInitialized, setEventSelectionInitialized] = useState(false);
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [favoriteTeams, setFavoriteTeams] = useState<string[]>([]);
  const [showFavoritesModal, setShowFavoritesModal] = useState(false);
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [myClubName, setMyClubName] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 100;

  // Fetch all events for the dropdown
  const { data: allEvents } = useQuery({
    queryKey: ['events'],
    queryFn: async () => {
      const response = await eventsApi.getAll();
      return response.data;
    },
  });

  useEffect(() => {
    if (combinedView && allEvents?.length && !eventSelectionInitialized) {
      setSelectedEventIds(allEvents.filter(event => event.status === 'active').map(event => event.id));
      setEventSelectionInitialized(true);
    }
  }, [combinedView, allEvents, eventSelectionInitialized]);

  // Load favorite teams and club name from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem('favoriteTeams');
    if (stored) {
      setFavoriteTeams(JSON.parse(stored));
    }
    const storedClubName = localStorage.getItem('myClubName');
    if (storedClubName) {
      setMyClubName(storedClubName);
    }
  }, []);

  // Listen for storage changes (when club name is updated in admin modal)
  useEffect(() => {
    const handleStorageChange = () => {
      const storedClubName = localStorage.getItem('myClubName');
      setMyClubName(storedClubName || '');
    };
    window.addEventListener('storage', handleStorageChange);
    // Also check when admin modal closes
    const interval = setInterval(handleStorageChange, 500);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      clearInterval(interval);
    };
  }, []);

  // Save favorite teams to localStorage whenever it changes
  useEffect(() => {
    if (favoriteTeams.length > 0) {
      localStorage.setItem('favoriteTeams', JSON.stringify(favoriteTeams));
    }
  }, [favoriteTeams]);

  const toggleFavoriteTeam = (teamName: string) => {
    setFavoriteTeams(prev => {
      if (prev.includes(teamName)) {
        const updated = prev.filter(t => t !== teamName);
        localStorage.setItem('favoriteTeams', JSON.stringify(updated));
        return updated;
      } else {
        const updated = [...prev, teamName];
        localStorage.setItem('favoriteTeams', JSON.stringify(updated));
        return updated;
      }
    });
  };

  const removeFavoriteTeam = (teamName: string) => {
    const updated = favoriteTeams.filter(t => t !== teamName);
    setFavoriteTeams(updated);
    localStorage.setItem('favoriteTeams', JSON.stringify(updated));
  };

  const clearAllFavorites = () => {
    setFavoriteTeams([]);
    localStorage.removeItem('favoriteTeams');
  };

  const { data: schedule, isLoading, error } = useQuery({
    queryKey: ['schedule', combinedView ? 'all' : eventId],
    queryFn: async () => {
      const response = combinedView
        ? await schedulesApi.getAllActive()
        : await schedulesApi.getEventSchedule(eventId, { page: 1, page_size: 500 });
      return response.data;
    },
  });

  // Fetch teams list from backend
  const { data: teamsData } = useQuery({
    queryKey: ['teams', eventId],
    queryFn: async () => {
      const response = await schedulesApi.getTeams(eventId);
      return response.data.teams;
    },
    enabled: !combinedView && filterType === 'team',
  });

  // Fetch locations list from backend
  const { data: locationsData } = useQuery({
    queryKey: ['locations', eventId],
    queryFn: async () => {
      const response = await schedulesApi.getLocations(eventId);
      return response.data.locations;
    },
    enabled: !combinedView && filterType === 'location',
  });

  // PERFORMANCE: Use backend-provided lists instead of computing from all games
  const allTeams = useMemo(() => combinedView
    ? Array.from(new Set((schedule?.games || []).flatMap(game => [game.home_team_name, game.away_team_name]).filter(Boolean) as string[])).sort()
    : (teamsData || []), [combinedView, schedule?.games, teamsData]);
  const allLocations = useMemo(() => combinedView
    ? Array.from(new Set((schedule?.games || []).map(game => game.field_name).filter(Boolean) as string[])).sort()
    : (locationsData || []), [combinedView, schedule?.games, locationsData]);

  // Helper function to parse time strings like "8:00 AM" to comparable numbers
  const parseTime = (timeStr: string | null) => {
    if (!timeStr) return 0;
    const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
    if (!match) return 0;
    let hours = parseInt(match[1]);
    const minutes = parseInt(match[2]);
    const period = match[3].toUpperCase();
    
    // Convert to 24-hour format
    if (period === 'PM' && hours !== 12) hours += 12;
    if (period === 'AM' && hours === 12) hours = 0;
    
    return hours * 60 + minutes; // Return total minutes for comparison
  };

  // Helper function to get current matches (games that have started but likely haven't finished)
  const getCurrentMatches = useMemo(() => {
    return (games: Game[]) => {
      const now = new Date();
      // Get today's date in local timezone (YYYY-MM-DD)
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      const todayStr = `${year}-${month}-${day}`;
      const currentMinutes = now.getHours() * 60 + now.getMinutes();

      // Filter games that are today (compare dates in local timezone)
      const todayGames = games.filter(game => {
        if (!game.game_date) return false;
        const gameDate = new Date(game.game_date);
        const gameYear = gameDate.getFullYear();
        const gameMonth = String(gameDate.getMonth() + 1).padStart(2, '0');
        const gameDay = String(gameDate.getDate()).padStart(2, '0');
        const gameDateStr = `${gameYear}-${gameMonth}-${gameDay}`;
        return gameDateStr === todayStr;
      });

      // Group games by field
      const gamesByField = new Map<string, Game[]>();
      todayGames.forEach(game => {
        const field = game.field_name || 'Unknown Field';
        if (!gamesByField.has(field)) {
          gamesByField.set(field, []);
        }
        gamesByField.get(field)!.push(game);
      });

      // For each field, find the game with the last start time before current time
      const currentGames: Game[] = [];
      gamesByField.forEach((fieldGames, field) => {
        // Sort games by start time
        const sortedGames = fieldGames.sort((a, b) => {
          const timeA = parseTime(a.game_time);
          const timeB = parseTime(b.game_time);
          return timeA - timeB;
        });

        // Find the last game that has started (start time <= current time)
        let lastStartedGame: Game | null = null;
        for (const game of sortedGames) {
          const gameStartMinutes = parseTime(game.game_time);
          if (gameStartMinutes <= currentMinutes) {
            lastStartedGame = game;
          } else {
            break; // Since sorted, no need to check further
          }
        }

        if (lastStartedGame) {
          currentGames.push(lastStartedGame);
        }
      });

      return currentGames;
    };
  }, []); // Empty deps - function logic doesn't change

  // PERFORMANCE: Memoize filtering and sorting to prevent re-computing on every render
  // Note: Pagination handled on backend, but we still filter client-side for current/favorites/myclub
  const filteredGames = useMemo(() => {
    if (!schedule?.games) return [];
    
    return schedule.games
      .filter(game => {
        if (combinedView && selectedEventIds.length > 0 && game.event_id && !selectedEventIds.includes(game.event_id)) return false;
        if (selectedDivision && game.division_id !== selectedDivision) return false;
        if (teamFilter && !(game.home_team_name === teamFilter || game.away_team_name === teamFilter)) return false;
        if (locationFilter && game.field_name !== locationFilter) return false;
        if (statusFilter === 'completed' && game.status !== 'completed') return false;
        if (statusFilter === 'upcoming' && game.status === 'completed') return false;
        if (filterType === 'current') {
          // For current matches, we'll filter separately
          return true;
        }
        if (filterType === 'favorites') {
          return (game.home_team_name && favoriteTeams.includes(game.home_team_name)) ||
                 (game.away_team_name && favoriteTeams.includes(game.away_team_name));
        }
        if (filterType === 'myclub') {
          if (!myClubName) return false;
          return (game.home_team_name && game.home_team_name.includes(myClubName)) ||
                 (game.away_team_name && game.away_team_name.includes(myClubName));
        }
        return true;
      })
      .sort((a, b) => {
        // First sort by date
        const dateA = a.game_date ? new Date(a.game_date).getTime() : 0;
        const dateB = b.game_date ? new Date(b.game_date).getTime() : 0;
        
        if (dateA !== dateB) {
          return dateA - dateB;
        }
        
        // If dates are equal, sort by time
        const timeA = parseTime(a.game_time);
        const timeB = parseTime(b.game_time);
        return timeA - timeB;
      });
  }, [schedule?.games, combinedView, selectedEventIds, selectedDivision, teamFilter, locationFilter, statusFilter, filterType, favoriteTeams, myClubName]);

  // Apply current matches filter if selected
  const displayGames = filterType === 'current' ? getCurrentMatches(filteredGames) : filteredGames;

  // Handle filter type change - reset all filters
  const handleFilterTypeChange = (newFilterType: FilterType) => {
    setFilterType(newFilterType);
    setSelectedDivision(undefined);
    setTeamFilter('');
    setLocationFilter('');
    setShowFilterMenu(false);
    setCurrentPage(1);
  };

  const resetFilters = () => {
    setSelectedDivision(undefined);
    setTeamFilter('');
    setLocationFilter('');
    setFilterType('all');
    setStatusFilter('all');
    if (combinedView) setSelectedEventIds(allEvents?.filter(event => event.status === 'active').map(event => event.id) || []);
    setCurrentPage(1);
  };

  // Handle clicking on a team name in the table
  const handleTeamClick = (teamName: string) => {
    setFilterType('team');
    setTeamFilter(teamName);
    setSelectedDivision(undefined);
    setLocationFilter('');
    setCurrentPage(1);
  };

  // Handle clicking on a location in the table
  const handleLocationClick = (location: string) => {
    setFilterType('location');
    setLocationFilter(location);
    setSelectedDivision(undefined);
    setTeamFilter('');
    setCurrentPage(1);
  };

  // Handle clicking on a division in the table
  const handleDivisionClick = (divisionId: number) => {
    setFilterType('division');
    setSelectedDivision(divisionId);
    setTeamFilter('');
    setLocationFilter('');
    setCurrentPage(1);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-lg text-gray-600">Loading schedule...</div>
      </div>
    );
  }

  if (error || !schedule) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-lg text-red-600">Error loading schedule</div>
      </div>
    );
  }

  const getFilterLabel = () => {
    switch (filterType) {
      case 'all': return 'All Games';
      case 'division': return 'Filter by Division';
      case 'team': return 'Filter by Team';
      case 'location': return 'Filter by Location';
      case 'favorites': return 'Show Favorites';
      case 'current': return 'Current Matches';
      case 'myclub': return 'My Club';
      default: return 'Filter by Division';
    }
  };

  const getViewingText = () => {
    if (filterType === 'location' && locationFilter) {
      return locationFilter;
    }
    if (filterType === 'division' && selectedDivision) {
      const division = schedule?.divisions.find(d => d.id === selectedDivision);
      return division?.name || 'All';
    }
    if (filterType === 'team' && teamFilter) {
      return teamFilter;
    }
    if (filterType === 'myclub') {
      return myClubName || 'My Club (not set)';
    }
    return 'All';
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Admin Modal */}
      <AdminModal isOpen={showAdminModal} onClose={() => setShowAdminModal(false)} currentEventId={eventId} />
      
      {/* Top Navigation Bar */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Left side - Tournament selector and viewing mode */}
            <div className="flex items-center gap-3 sm:gap-6 flex-1 min-w-0">
              <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2 flex-1 min-w-0">
                <span className="text-xs sm:text-sm font-medium text-gray-700 whitespace-nowrap">Tournament:</span>
                <select 
                  className="px-2 sm:px-3 py-1.5 border border-gray-300 rounded-md text-xs sm:text-sm bg-white min-w-0 flex-1"
                  value={combinedView ? 'all' : eventId}
                  onChange={(e) => router.push(e.target.value === 'all' ? `/events/${eventId}?events=all` : `/events/${e.target.value}`)}
                >
                  {allEvents && allEvents.length > 1 && <option value="all">All Events</option>}
                  {allEvents && allEvents.length > 0 ? (
                    allEvents.map(event => (
                      <option key={event.id} value={event.id}>{event.name}</option>
                    ))
                  ) : (
                    <option value={eventId}>{schedule.event.name}</option>
                  )}
                </select>
              </div>
              {schedule.event.last_scraped_at && (
                <div className="hidden lg:block text-xs text-gray-500 whitespace-nowrap">
                  Last Scraped: {new Date(schedule.event.last_scraped_at).toLocaleString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit',
                    hour12: true
                  })}
                </div>
              )}
            </div>
            
            {/* Right side - Admin and Seeding buttons */}
            <div className="flex gap-2">
              <button
                onClick={() => window.location.href = `/events/${eventId}/seeding`}
                className="px-3 sm:px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium text-sm whitespace-nowrap"
              >
                Seeding
              </button>
              <button
                onClick={() => setShowAdminModal(true)}
                className="px-3 sm:px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors font-medium text-sm whitespace-nowrap"
              >
                Admin
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Viewing Info */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-4">
          <div className="flex items-center gap-2 text-sm sm:text-base text-gray-700">
            <span>Viewing: <span className="font-bold">{getViewingText()}</span></span>
            {filterType === 'team' && teamFilter && (
              <button
                onClick={() => toggleFavoriteTeam(teamFilter)}
                className="p-1 hover:bg-gray-100 rounded transition-colors"
                title={favoriteTeams.includes(teamFilter) ? 'Remove from favorites' : 'Add to favorites'}
              >
                <svg 
                  className="w-5 h-5" 
                  fill={favoriteTeams.includes(teamFilter) ? 'currentColor' : 'none'}
                  stroke="currentColor" 
                  viewBox="0 0 24 24"
                >
                  <path 
                    strokeLinecap="round" 
                    strokeLinejoin="round" 
                    strokeWidth={2} 
                    d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" 
                  />
                </svg>
              </button>
            )}
            {filterType === 'favorites' && favoriteTeams.length > 0 && (
              <button
                onClick={() => setShowFavoritesModal(true)}
                className="flex items-center gap-2 px-3 py-1 text-sm text-gray-600 hover:bg-gray-100 rounded transition-colors"
              >
                <svg className="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
                </svg>
                <span>{favoriteTeams.length} favorite team{favoriteTeams.length !== 1 ? 's' : ''}</span>
              </button>
            )}
          </div>
          {filterType === 'current' && (
            <div className="mt-2 text-xs sm:text-sm text-gray-600 flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/>
              </svg>
              <span>Showing the most recent game that has started on each field (games do not extend past midnight)</span>
            </div>
          )}
        </div>
      </div>

      <main className="max-w-7xl mx-auto py-4 sm:py-6 px-4 sm:px-6 lg:px-8">
        {/* Filter Bar */}
        <div className="bg-white rounded-lg shadow mb-4 sm:mb-6 p-3 sm:p-4">
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-4">
            {/* Filter Type Dropdown */}
            <div className="relative flex-1 sm:flex-initial">
              <button
                onClick={() => setShowFilterMenu(!showFilterMenu)}
                className="w-full flex items-center justify-between gap-2 px-3 sm:px-4 py-2 border border-gray-300 rounded-md text-sm bg-white sm:min-w-[200px] hover:bg-gray-50"
              >
                <span className="truncate">{getFilterLabel()}</span>
                <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {showFilterMenu && (
                <div className="absolute top-full left-0 mt-1 w-full bg-white border border-gray-200 rounded-md shadow-lg z-10">
                  <button
                    onClick={() => handleFilterTypeChange('all')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 flex items-center justify-between"
                  >
                    All Games
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('division')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 flex items-center justify-between"
                  >
                    Filter by Division
                    {filterType === 'division' && <span>✓</span>}
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('team')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    Filter by Team
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('location')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    Filter by Location
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('favorites')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    Show Favorites
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('myclub')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    My Club
                  </button>
                  <button
                    onClick={() => handleFilterTypeChange('current')}
                    className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
                  >
                    Current Matches
                  </button>
                </div>
              )}
            </div>

            {/* Division Selector (when filter type is division) */}
            {filterType === 'division' && (
              <select
                className="flex-1 px-3 sm:px-4 py-2 border border-gray-300 rounded-md text-sm bg-white"
                value={selectedDivision || ''}
                onChange={(e) => {
                  setSelectedDivision(e.target.value ? parseInt(e.target.value) : undefined);
                  setCurrentPage(1);
                }}
              >
                <option value="">Select division...</option>
                {schedule.divisions.map((div) => (
                  <option key={div.id} value={div.id}>
                    {div.name}
                  </option>
                ))}
              </select>
            )}

            {/* Team Filter (when filter type is team) */}
            {filterType === 'team' && (
              <select
                className="flex-1 px-3 sm:px-4 py-2 border border-gray-300 rounded-md text-sm bg-white"
                value={teamFilter}
                onChange={(e) => {
                  setTeamFilter(e.target.value);
                  setCurrentPage(1);
                }}
              >
                <option value="">All Teams</option>
                {allTeams.map((team) => (
                  <option key={team} value={team}>
                    {team}
                  </option>
                ))}
              </select>
            )}

            {/* Location Filter (when filter type is location) */}
            {filterType === 'location' && (
              <select
                className="flex-1 px-3 sm:px-4 py-2 border border-gray-300 rounded-md text-sm bg-white"
                value={locationFilter}
                onChange={(e) => {
                  setLocationFilter(e.target.value);
                  setCurrentPage(1);
                }}
              >
                <option value="">All Locations</option>
                {allLocations.map((location) => (
                  <option key={location} value={location}>
                    {location}
                  </option>
                ))}
              </select>
            )}
            {combinedView && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">Events:</span>
                {(allEvents || []).filter(event => event.status === 'active').map(event => (
                  <label key={event.id} className="inline-flex items-center gap-1">
                    <input type="checkbox" checked={selectedEventIds.includes(event.id)} onChange={() => setSelectedEventIds(current => current.includes(event.id) ? current.filter(id => id !== event.id) : [...current, event.id])} />
                    {event.name}
                  </label>
                ))}
              </div>
            )}
            <label className="text-sm whitespace-nowrap">Results:
              <select value={statusFilter} onChange={event => setStatusFilter(event.target.value as StatusFilter)} className="ml-2 px-2 py-2 border border-gray-300 rounded-md bg-white">
                <option value="all">All Games</option>
                <option value="upcoming">Upcoming</option>
                <option value="completed">Completed</option>
              </select>
            </label>
            <button onClick={resetFilters} className="px-3 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50">Reset Filters</button>
          </div>
        </div>

        {/* Pagination Controls - only show for 'all' view */}
        {filterType === 'all' && schedule && schedule.total_games > pageSize && (
          <div className="bg-white rounded-lg shadow p-4 mb-6 flex items-center justify-between">
            <div className="text-sm text-gray-600">
              Showing {((currentPage - 1) * pageSize) + 1} - {Math.min(currentPage * pageSize, schedule.total_games)} of {schedule.total_games} games
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="px-3 py-1 border border-gray-300 rounded-md text-sm bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <div className="flex items-center gap-1">
                {Array.from({ length: Math.ceil(schedule.total_games / pageSize) }, (_, i) => i + 1)
                  .filter(page => {
                    // Show first, last, current, and adjacent pages
                    const totalPages = Math.ceil(schedule.total_games / pageSize);
                    return page === 1 || page === totalPages || Math.abs(page - currentPage) <= 1;
                  })
                  .map((page, idx, arr) => (
                    <div key={page} className="flex items-center gap-1">
                      {idx > 0 && arr[idx - 1] !== page - 1 && <span className="text-gray-400">...</span>}
                      <button
                        onClick={() => setCurrentPage(page)}
                        className={`px-3 py-1 border rounded-md text-sm ${
                          currentPage === page
                            ? 'bg-blue-600 text-white border-blue-600'
                            : 'bg-white border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        {page}
                      </button>
                    </div>
                  ))}
              </div>
              <button
                onClick={() => setCurrentPage(p => Math.min(Math.ceil(schedule.total_games / pageSize), p + 1))}
                disabled={currentPage >= Math.ceil(schedule.total_games / pageSize)}
                className="px-3 py-1 border border-gray-300 rounded-md text-sm bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}

        {/* Games Table - Desktop */}
        <div className="hidden md:block bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Time
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Home Team
                </th>
                <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Results
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Away Team
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Location
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Division
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Event
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {displayGames.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-6 py-12 text-center text-gray-500">
                    {filterType === 'current' 
                      ? 'No current matches at this time.'
                      : 'No matches found. Contact an admin to upload schedules.'}
                  </td>
                </tr>
              ) : (
                displayGames.map((game) => (
                  <tr key={game.id} className={game.status === 'completed' ? 'hover:bg-gray-50' : 'bg-gray-50/50 hover:bg-gray-100'}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                      <div>{game.game_date ? new Date(game.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}</div>
                      <div className="text-gray-600">{game.game_time || 'TBD'}</div>
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <button
                        onClick={() => game.home_team_name && handleTeamClick(game.home_team_name)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left"
                        disabled={!game.home_team_name}
                      >
                        {game.home_team_name || 'TBD'}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-center">
                      {game.home_score !== null && game.away_score !== null ? (
                        <span className="font-extrabold text-emerald-700" aria-label="Completed score">{game.home_score} - {game.away_score}</span>
                      ) : (
                        <span className="text-gray-500 italic">Scheduled</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <button
                        onClick={() => game.away_team_name && handleTeamClick(game.away_team_name)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left"
                        disabled={!game.away_team_name}
                      >
                        {game.away_team_name || 'TBD'}
                      </button>
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <button
                        onClick={() => game.field_name && handleLocationClick(game.field_name)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left"
                        disabled={!game.field_name}
                      >
                        {game.field_name || 'TBD'}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      <button
                        onClick={() => game.division_id && handleDivisionClick(game.division_id)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left"
                        disabled={!game.division_id}
                      >
                        {game.division_name}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                      {game.event_name || schedule.event.name}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <span className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${
                        game.status === 'completed' 
                          ? 'bg-green-100 text-green-800' 
                          : game.status === 'in_progress'
                          ? 'bg-blue-100 text-blue-800'
                          : 'bg-gray-100 text-gray-800'
                      }`}>
                        {game.status || 'scheduled'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Games Cards - Mobile */}
        <div className="md:hidden space-y-2">
          {displayGames.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-6 text-center text-gray-500">
              {filterType === 'current' 
                ? 'No current matches at this time.'
                : 'No matches found. Contact an admin to upload schedules.'}
            </div>
          ) : (
            displayGames.map((game) => (
              <div key={game.id} className="bg-white rounded-lg shadow p-3">
                {/* Top Row: Time, Field/Division, Status */}
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="text-xs">
                    <div className="font-semibold text-gray-900">
                      {game.game_date ? new Date(game.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'TBD'}
                    </div>
                    <div className="text-gray-600">{game.game_time || 'TBD'}</div>
                  </div>
                  
                  <div className="flex-1 text-xs text-gray-600 space-y-0.5">
                    {game.field_name && (
                      <button
                        onClick={() => game.field_name && handleLocationClick(game.field_name)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left block truncate"
                      >
                        {game.field_name}
                      </button>
                    )}
                    {game.division_name && (
                      <button
                        onClick={() => game.division_id && handleDivisionClick(game.division_id)}
                        className="text-blue-600 hover:text-blue-800 hover:underline text-left block truncate"
                      >
                        {game.division_name}
                      </button>
                    )}
                  </div>
                  
                  <span className={`px-2 py-1 text-xs font-semibold rounded-full whitespace-nowrap ${
                    game.status === 'completed' 
                      ? 'bg-green-100 text-green-800' 
                      : game.status === 'in_progress'
                      ? 'bg-blue-100 text-blue-800'
                      : 'bg-gray-100 text-gray-800'
                  }`}>
                    {game.status || 'scheduled'}
                  </span>
                </div>

                {/* Teams and Score */}
                <div className="space-y-1 mb-2">
                  <div className="flex items-center justify-between">
                    <button
                      onClick={() => game.home_team_name && handleTeamClick(game.home_team_name)}
                      className="text-blue-600 hover:text-blue-800 text-left font-medium flex-1 text-sm"
                      disabled={!game.home_team_name}
                    >
                      {game.home_team_name || 'TBD'}
                    </button>
                    <span className={`font-extrabold text-base mx-2 ${game.home_score !== null && game.away_score !== null && game.home_score > game.away_score ? 'text-emerald-300' : 'text-gray-100'}`}>
                      {game.home_score !== null ? game.home_score : '-'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <button
                      onClick={() => game.away_team_name && handleTeamClick(game.away_team_name)}
                      className="text-blue-600 hover:text-blue-800 text-left font-medium flex-1 text-sm"
                      disabled={!game.away_team_name}
                    >
                      {game.away_team_name || 'TBD'}
                    </button>
                    <span className={`font-extrabold text-base mx-2 ${game.home_score !== null && game.away_score !== null && game.away_score > game.home_score ? 'text-emerald-300' : 'text-gray-100'}`}>
                      {game.away_score !== null ? game.away_score : '-'}
                    </span>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </main>

      {/* Favorites Modal */}
      {showFavoritesModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowFavoritesModal(false)}>
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold">My Favorite Teams - {schedule?.event.name}</h2>
                <button
                  onClick={() => setShowFavoritesModal(false)}
                  className="text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              
              <div className="space-y-3">
                {favoriteTeams.map((team) => (
                  <div key={team} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                    <span className="text-lg">{team}</span>
                    <button
                      onClick={() => removeFavoriteTeam(team)}
                      className="text-gray-400 hover:text-red-600 transition-colors"
                      title="Remove from favorites"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
