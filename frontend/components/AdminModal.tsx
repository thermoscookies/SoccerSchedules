'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient, useQueries } from '@tanstack/react-query';
import { eventsApi, scrapingApi, favoritesApi, Event, FavoriteTeam, Division } from '@/lib/api';
import { format } from 'date-fns';

interface ScrapeLog {
  id: number;
  event_id: number;
  status: string;
  scrape_started_at: string;
  scrape_completed_at: string | null;
  error_message: string | null;
  games_scraped: number | null;
  games_updated: number | null;
  games_created: number | null;
}

interface AdminModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentEventId?: number;
}

export default function AdminModal({ isOpen, onClose, currentEventId }: AdminModalProps) {
  const [newEventId, setNewEventId] = useState('');
  const [newEventName, setNewEventName] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [viewingLogsForEvent, setViewingLogsForEvent] = useState<number | null>(null);
  const [clubName, setClubName] = useState('');
  const [editingEventId, setEditingEventId] = useState<number | null>(null);
  const [editingEventName, setEditingEventName] = useState('');
  const [editingDivisionId, setEditingDivisionId] = useState<number | null>(null);
  const [editingDivisionName, setEditingDivisionName] = useState('');
  const [favoriteSearch, setFavoriteSearch] = useState('');
  const queryClient = useQueryClient();

  // Load club name from localStorage on mount
  useState(() => {
    const stored = localStorage.getItem('myClubName');
    if (stored) {
      setClubName(stored);
    }
  });

  const { data: events, isLoading } = useQuery({
    queryKey: ['events'],
    queryFn: async () => {
      const response = await eventsApi.getAll();
      return response.data;
    },
    enabled: isOpen,
  });

  const { data: scrapeLogs, isLoading: logsLoading } = useQuery({
    queryKey: ['scrapeLogs', viewingLogsForEvent],
    queryFn: async () => {
      if (!viewingLogsForEvent) return [];
      const response = await scrapingApi.getLogs(viewingLogsForEvent, 20);
      return response.data as ScrapeLog[];
    },
    enabled: viewingLogsForEvent !== null,
    refetchInterval: viewingLogsForEvent !== null ? 3000 : false, // Refresh every 3s when viewing logs
  });

  const { data: favoriteOptions = [] } = useQuery({
    queryKey: ['favorites'],
    queryFn: async () => (await favoritesApi.list()).data,
    enabled: isOpen,
  });
  const divisionQueries = useQueries({
    queries: (events || []).map(event => ({
      queryKey: ['divisions', event.id],
      queryFn: async () => (await eventsApi.getDivisions(event.id)).data,
      enabled: isOpen,
    })),
  });
  const divisions = divisionQueries.flatMap(query => query.data || []);
  const favoriteMutation = useMutation({
    mutationFn: async (item: FavoriteTeam) => item.is_favorite && item.id != null
      ? favoritesApi.remove(item.id)
      : favoritesApi.add(item.division_id, item.team_name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['favorites'] });
      setSuccess('Favorite teams updated');
      setTimeout(() => setSuccess(''), 2500);
    },
    onError: (err: any) => setError(err.response?.data?.detail || 'Failed to update favorite team'),
  });

  const deleteEventMutation = useMutation({
    mutationFn: (eventId: number) => eventsApi.delete(eventId),
    onSuccess: (_, deletedEventId) => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      queryClient.invalidateQueries({ queryKey: ['schedule', deletedEventId] });
      
      // If we deleted the currently viewed event, redirect
      if (currentEventId === deletedEventId) {
        // Get remaining events after this one is removed
        const remainingEvents = events?.filter(e => e.id !== deletedEventId) || [];
        
        if (remainingEvents.length > 0) {
          // Redirect to the first available event
          window.location.href = `/events/${remainingEvents[0].id}`;
        } else {
          // No events left, go to home page
          window.location.href = '/';
        }
      } else {
        setSuccess('Event deleted successfully');
        setTimeout(() => setSuccess(''), 3000);
      }
    },
    onError: (error: any) => {
      setError(error.response?.data?.detail || 'Failed to delete event');
      setTimeout(() => setError(''), 5000);
    },
  });

  const reScrapeEventMutation = useMutation({
    mutationFn: (eventId: number) => scrapingApi.trigger(eventId, true),
    onSuccess: (_, eventId) => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      queryClient.invalidateQueries({ queryKey: ['schedule', eventId] });
      setSuccess('Re-scrape started! This may take a few minutes.');
      setTimeout(() => setSuccess(''), 5000);
    },
    onError: (error: any) => {
      setError(error.response?.data?.detail || 'Failed to start re-scrape');
      setTimeout(() => setError(''), 5000);
    },
  });

  const addEventMutation = useMutation({
    mutationFn: async (data: { gotsport_event_id: string; name: string }) => {
      const url = `https://system.gotsport.com/org_event/events/${data.gotsport_event_id}`;
      const response = await eventsApi.create({
        gotsport_event_id: data.gotsport_event_id,
        name: data.name,
        url,
        status: 'active',
      });
      return response.data;
    },
    onSuccess: async (newEvent) => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      setSuccess(`Event added successfully! Scraping started for "${newEvent.name}"`);
      setNewEventId('');
      setNewEventName('');
      setShowAddForm(false);
      setTimeout(() => setSuccess(''), 5000);
    },
    onError: (error: any) => {
      setError(error.response?.data?.detail || 'Failed to add event');
      setTimeout(() => setError(''), 5000);
    },
  });

  const renameEventMutation = useMutation({
    mutationFn: async ({ id, name }: { id: number; name: string }) => (await eventsApi.update(id, { name })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      queryClient.invalidateQueries({ queryKey: ['schedule'] });
      setEditingEventId(null);
      setSuccess('Event name saved successfully');
      setTimeout(() => setSuccess(''), 3000);
    },
    onError: (err: any) => setError(err.response?.data?.detail || 'Failed to save event name'),
  });
  const renameDivisionMutation = useMutation({
    mutationFn: async ({ division, displayName }: { division: Division; displayName: string }) =>
      (await eventsApi.updateDivision(division.event_id, division.id, { display_name: displayName.trim() || null })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['divisions'] });
      queryClient.invalidateQueries({ queryKey: ['schedule'] });
      queryClient.invalidateQueries({ queryKey: ['favorites'] });
      setEditingDivisionId(null);
      setSuccess('Division name saved successfully');
      setTimeout(() => setSuccess(''), 3000);
    },
    onError: (err: any) => setError(err.response?.data?.detail || 'Failed to save division name'),
  });

  const handleDeleteEvent = (event: Event) => {
    if (window.confirm(`Are you sure you want to delete "${event.name}"? This will remove all games and divisions for this event.`)) {
      deleteEventMutation.mutate(event.id);
    }
  };

  const handleReScrapeEvent = (event: Event) => {
    if (window.confirm(`Re-scrape "${event.name}"? This will update all games and divisions with the latest data from Gotsport.`)) {
      reScrapeEventMutation.mutate(event.id);
    }
  };

  const handleAddEvent = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');

    if (!newEventId.trim()) {
      setError('Event ID is required');
      return;
    }
    if (!newEventName.trim()) {
      setError('Event name is required');
      return;
    }

    addEventMutation.mutate({
      gotsport_event_id: newEventId.trim(),
      name: newEventName.trim(),
    });
  };

  const handleSaveClubName = () => {
    localStorage.setItem('myClubName', clubName.trim());
    setSuccess('Club name saved successfully!');
    setTimeout(() => setSuccess(''), 3000);
  };

  const handleClearClubName = () => {
    setClubName('');
    localStorage.removeItem('myClubName');
    setSuccess('Club name cleared!');
    setTimeout(() => setSuccess(''), 3000);
  };

  if (!isOpen) return null;

  // If viewing logs, show logs modal instead
  if (viewingLogsForEvent !== null) {
    const event = events?.find(e => e.id === viewingLogsForEvent);
    
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-2 sm:p-4">
        <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between p-3 sm:p-6 border-b border-gray-200">
            <div className="min-w-0 flex-1 pr-2">
              <h2 className="text-lg sm:text-2xl font-bold text-gray-900 truncate">Scrape Logs</h2>
              {event && <p className="text-xs sm:text-sm text-gray-600 mt-1 truncate">{event.name}</p>}
            </div>
            <button
              onClick={() => setViewingLogsForEvent(null)}
              className="text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0 p-1"
              aria-label="Close"
            >
              <svg className="w-5 h-5 sm:w-6 sm:h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-3 sm:p-6">
            {logsLoading ? (
              <div className="text-center py-8 text-gray-500">Loading logs...</div>
            ) : !scrapeLogs || scrapeLogs.length === 0 ? (
              <div className="text-center py-8 text-gray-500">
                No scrape logs found. Logs will appear here after the first scrape.
              </div>
            ) : (
              <div className="space-y-2 sm:space-y-3">
                {scrapeLogs.map((log) => (
                  <div
                    key={log.id}
                    className={`p-3 sm:p-4 border rounded-lg ${
                      log.status === 'success'
                        ? 'bg-green-50 border-green-200'
                        : log.status === 'failed'
                        ? 'bg-red-50 border-red-200'
                        : log.status === 'in_progress'
                        ? 'bg-blue-50 border-blue-200'
                        : 'bg-gray-50 border-gray-200'
                    }`}
                  >
                    <div className="flex flex-col sm:flex-row items-start sm:justify-between gap-2 sm:gap-4 mb-2">
                      <div>
                        <span
                          className={`inline-block px-2 py-1 rounded text-xs font-semibold uppercase ${
                            log.status === 'success'
                              ? 'bg-green-200 text-green-800'
                              : log.status === 'failed'
                              ? 'bg-red-200 text-red-800'
                              : log.status === 'in_progress'
                              ? 'bg-blue-200 text-blue-800'
                              : 'bg-gray-200 text-gray-800'
                          }`}
                        >
                          {log.status}
                        </span>
                      </div>
                      <div className="text-xs sm:text-sm text-gray-600 whitespace-nowrap">
                        {format(new Date(log.scrape_started_at), 'MMM d, yyyy h:mm:ss a')}
                      </div>
                    </div>
                    
                    {log.status === 'success' && (
                      <div className="text-sm text-gray-700 space-y-1">
                        {log.games_scraped !== null && (
                          <p><span className="font-medium">Total games:</span> {log.games_scraped}</p>
                        )}
                        {log.games_created !== null && (
                          <p><span className="font-medium">Created:</span> {log.games_created}</p>
                        )}
                        {log.games_updated !== null && (
                          <p><span className="font-medium">Updated:</span> {log.games_updated}</p>
                        )}
                        {log.scrape_completed_at && (
                          <p className="text-xs text-gray-500">
                            Duration: {
                              Math.round(
                                (new Date(log.scrape_completed_at).getTime() - 
                                 new Date(log.scrape_started_at).getTime()) / 1000
                              )
                            }s
                          </p>
                        )}
                      </div>
                    )}
                    
                    {log.status === 'failed' && log.error_message && (
                      <div className="mt-2 text-sm text-red-700">
                        <span className="font-medium">Error:</span> {log.error_message}
                      </div>
                    )}
                    
                    {log.status === 'in_progress' && (
                      <div className="mt-2 text-sm text-blue-700 flex items-center gap-2">
                        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                        Scraping in progress...
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="p-3 sm:p-6 border-t border-gray-200 bg-gray-50">
            <button
              onClick={() => setViewingLogsForEvent(null)}
              className="w-full px-4 sm:px-6 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors font-medium text-sm sm:text-base"
            >
              Back to Admin Panel
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-2 sm:p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[95vh] sm:max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-3 sm:p-6 border-b border-gray-200">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg sm:text-2xl font-bold text-gray-900">Admin Panel</h2>
          <a href="/userscripts/gotsport.user.js" className="text-xs sm:text-sm text-blue-600 hover:underline" target="_blank" rel="noreferrer">
            Install GotSport Userscript
          </a>
        </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors p-1"
            aria-label="Close"
          >
            <svg className="w-5 h-5 sm:w-6 sm:h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-3 sm:p-6">
          {/* Status Messages */}
          {error && (
            <div className="mb-3 sm:mb-4 p-3 sm:p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
              {error}
            </div>
          )}
          {success && (
            <div className="mb-3 sm:mb-4 p-3 sm:p-4 bg-green-50 border border-green-200 text-green-700 rounded-lg text-sm">
              {success}
            </div>
          )}

          {/* Club Settings Section */}
          <div className="mb-4 sm:mb-6 p-3 sm:p-4 bg-blue-50 rounded-lg border border-blue-200">
            <h3 className="text-base sm:text-lg font-semibold text-gray-900 mb-2 sm:mb-3">My Club Settings</h3>
            <p className="text-xs sm:text-sm text-gray-600 mb-2 sm:mb-3">
              Set your club name to quickly filter games for your teams using the "My Club" filter.
            </p>
            <div className="flex flex-col gap-2 sm:gap-3">
              <input
                type="text"
                value={clubName}
                onChange={(e) => setClubName(e.target.value)}
                placeholder="e.g., Reel Stream Media Group"
                className="w-full px-3 py-2 border border-gray-500 rounded-lg bg-gray-900 text-gray-100 placeholder:text-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm sm:text-base"
              />
              <div className="flex gap-2">
                <button
                  onClick={handleSaveClubName}
                  disabled={!clubName.trim()}
                  className="flex-1 sm:flex-initial px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium disabled:bg-gray-400 disabled:cursor-not-allowed whitespace-nowrap text-sm sm:text-base"
                >
                  Save
                </button>
                {clubName && (
                  <button
                    onClick={handleClearClubName}
                    className="flex-1 sm:flex-initial px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors font-medium whitespace-nowrap text-sm sm:text-base"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Add Event Section */}
          <div className="mb-4 sm:mb-6 p-3 sm:p-4 bg-gray-800 rounded-lg border border-gray-600">
            <h3 className="text-base sm:text-lg font-semibold text-gray-100 mb-2">Favorite Teams</h3>
            <p className="text-xs sm:text-sm text-gray-300 mb-3">Favorites are shared across all visitors and devices. Teams are scoped to their event and division.</p>
            <input aria-label="Search favorite teams" value={favoriteSearch} onChange={e => setFavoriteSearch(e.target.value)} placeholder="Search teams, events, or divisions" className="w-full px-3 py-2 mb-3 rounded-lg border border-gray-500 bg-gray-900 text-gray-100 placeholder:text-gray-400" />
            <div className="max-h-64 overflow-y-auto space-y-2">
              {favoriteOptions.filter(item => `${item.team_name} ${item.event_name} ${item.division_name}`.toLowerCase().includes(favoriteSearch.toLowerCase())).map(item => (
                <div key={`${item.division_id}:${item.team_name}`} className="flex items-center justify-between gap-2 rounded border border-gray-700 px-3 py-2">
                  <div className="min-w-0"><div className="truncate text-sm text-gray-100">{item.team_name}</div><div className="truncate text-xs text-gray-400">{item.event_name} · {item.division_name}</div></div>
                  <button type="button" onClick={() => favoriteMutation.mutate(item)} disabled={favoriteMutation.isPending} aria-label={`${item.is_favorite ? 'Remove' : 'Add'} ${item.team_name} favorite`} className={`shrink-0 text-xl ${item.is_favorite ? 'text-amber-400' : 'text-gray-400 hover:text-amber-300'}`}>{item.is_favorite ? '★' : '☆'}</button>
                </div>
              ))}
              {favoriteOptions.length === 0 && <div className="text-sm text-gray-400">No imported teams are available yet.</div>}
            </div>
          </div>

          {/* Add Event Section */}
          <div className="mb-4 sm:mb-6">
            <button
              onClick={() => setShowAddForm(!showAddForm)}
              className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium text-sm sm:text-base"
            >
              {showAddForm ? 'Cancel' : '+ Add New Event'}
            </button>

            {showAddForm && (
              <form onSubmit={handleAddEvent} className="mt-3 sm:mt-4 p-3 sm:p-4 bg-gray-50 rounded-lg border border-gray-200">
                <div className="space-y-3 sm:space-y-4">
                  <div>
                    <label htmlFor="eventId" className="block text-xs sm:text-sm font-medium text-gray-700 mb-1">
                      Gotsport Event ID *
                    </label>
                    <input
                      type="text"
                      id="eventId"
                      value={newEventId}
                      onChange={(e) => setNewEventId(e.target.value)}
                      placeholder="e.g., 39474"
                      className="w-full px-3 py-2 border border-gray-500 rounded-lg bg-gray-900 text-gray-100 placeholder:text-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm sm:text-base"
                      required
                    />
                    <p className="mt-1 text-xs text-gray-500 break-all">
                      Find this in the Gotsport URL: https://system.gotsport.com/org_event/events/<strong>39474</strong>
                    </p>
                  </div>
                  <div>
                    <label htmlFor="eventName" className="block text-xs sm:text-sm font-medium text-gray-700 mb-1">
                      Event Name *
                    </label>
                    <input
                      type="text"
                      id="eventName"
                      value={newEventName}
                      onChange={(e) => setNewEventName(e.target.value)}
                      placeholder="e.g., 2025 U.S. Futsal Northeast..."
                      className="w-full px-3 py-2 border border-gray-500 rounded-lg bg-gray-900 text-gray-100 placeholder:text-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm sm:text-base"
                      required
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={addEventMutation.isPending}
                    className="w-full px-4 sm:px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors font-medium disabled:bg-gray-400 disabled:cursor-not-allowed text-sm sm:text-base"
                  >
                    {addEventMutation.isPending ? 'Adding...' : 'Add Event & Start Scraping'}
                  </button>
                </div>
              </form>
            )}
          </div>

          {/* Events List */}
          <div>
            <h3 className="text-base sm:text-lg font-semibold text-gray-900 mb-3 sm:mb-4">Active Events</h3>
            
            {isLoading ? (
              <div className="text-center py-8 text-gray-500">Loading events...</div>
            ) : !events || events.length === 0 ? (
              <div className="text-center py-8 text-gray-500">No events found</div>
            ) : (
              <div className="space-y-2 sm:space-y-3">
                {events.map((event) => (
                  <div
                    key={event.id}
                    className="p-3 sm:p-4 bg-white border border-gray-200 rounded-lg hover:border-gray-300 transition-colors"
                  >
                    <div className="flex flex-col gap-3">
                      <div className="flex-1 min-w-0">
                        {editingEventId === event.id ? (
                          <div className="flex flex-wrap gap-2 mb-2">
                            <input aria-label="Event display name" value={editingEventName} onChange={e => setEditingEventName(e.target.value)} className="min-w-[16rem] flex-1 px-3 py-2 rounded border border-gray-500 bg-gray-900 text-gray-100" />
                            <button onClick={() => renameEventMutation.mutate({ id: event.id, name: editingEventName.trim() })} disabled={!editingEventName.trim() || renameEventMutation.isPending} className="px-3 py-2 rounded bg-blue-600 text-white text-xs">Save</button>
                            <button onClick={() => setEditingEventId(null)} className="px-3 py-2 rounded bg-gray-600 text-white text-xs">Cancel</button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2 mb-1">
                            <h4 className="font-semibold text-gray-900 text-sm sm:text-base break-words">{event.name}</h4>
                            <button onClick={() => { setEditingEventId(event.id); setEditingEventName(event.name); }} className="text-xs text-blue-600 hover:underline">Rename</button>
                          </div>
                        )}
                        <div className="text-xs sm:text-sm text-gray-500 space-y-1">
                          <p>
                            <span className="font-medium">Event ID:</span> {event.gotsport_event_id}
                          </p>
                          {event.source_url && (
                            <p className="truncate">
                              <span className="font-medium">Source:</span>{' '}
                              <a href={event.source_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{event.source_url}</a>
                            </p>
                          )}
                          {event.total_games !== undefined && (
                            <p>
                              <span className="font-medium">Stats:</span> {event.total_divisions || 0} divisions, {event.total_games} games
                            </p>
                          )}
                          {event.last_scraped_at && (
                            <p>
                              <span className="font-medium">Last scraped:</span>{' '}
                              {format(new Date(event.last_scraped_at), 'MMM d, yyyy h:mm a')}
                            </p>
                          )}
                          {event.last_imported_at && (
                            <p>
                              <span className="font-medium">Last browser import:</span>{' '}
                              {format(new Date(event.last_imported_at), 'MMM d, yyyy h:mm a')}
                            </p>
                          )}
                          {event.next_scrape_in_hours !== undefined && event.next_scrape_in_hours > 0 && (
                            <p>
                              <span className="font-medium">Next scrape:</span> in {event.next_scrape_in_hours.toFixed(1)} hours
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
                        <button
                          onClick={() => setViewingLogsForEvent(event.id)}
                          className="px-3 sm:px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors font-medium text-xs sm:text-sm"
                        >
                          View Logs
                        </button>
                        <button
                          onClick={() => handleReScrapeEvent(event)}
                          disabled={reScrapeEventMutation.isPending}
                          className="px-3 sm:px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium disabled:bg-gray-400 disabled:cursor-not-allowed text-xs sm:text-sm"
                        >
                          {reScrapeEventMutation.isPending ? 'Scraping...' : 'Re-Scrape'}
                        </button>
                        <button
                          onClick={() => handleDeleteEvent(event)}
                          disabled={deleteEventMutation.isPending}
                          className="px-3 sm:px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors font-medium disabled:bg-gray-400 disabled:cursor-not-allowed text-xs sm:text-sm"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Division display names */}
        <div className="mt-6 rounded-lg border border-gray-700 bg-gray-900 p-4">
          <h3 className="text-base sm:text-lg font-semibold text-gray-100 mb-2">Division Display Names</h3>
          <p className="text-xs sm:text-sm text-gray-300 mb-3">These labels are local display names. Provider group IDs and imported games are unchanged.</p>
          <div className="space-y-2">
            {divisions.map((division) => (
              <div key={division.id} className="flex flex-wrap items-center gap-2 rounded border border-gray-700 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-gray-100 break-words">{division.display_name || division.name}</div>
                  <div className="text-xs text-gray-400">Group {division.gotsport_division_id || 'not set'}</div>
                </div>
                {editingDivisionId === division.id ? (
                  <>
                    <input aria-label={`Division display name for ${division.name}`} value={editingDivisionName} onChange={e => setEditingDivisionName(e.target.value)} className="min-w-[14rem] flex-1 px-3 py-2 rounded border border-gray-500 bg-gray-800 text-gray-100" />
                    <button onClick={() => renameDivisionMutation.mutate({ division, displayName: editingDivisionName })} disabled={renameDivisionMutation.isPending} className="px-3 py-2 rounded bg-blue-600 text-white text-xs">Save</button>
                    <button onClick={() => setEditingDivisionId(null)} className="px-3 py-2 rounded bg-gray-600 text-white text-xs">Cancel</button>
                  </>
                ) : (
                  <button onClick={() => { setEditingDivisionId(division.id); setEditingDivisionName(division.display_name || division.name); }} className="text-xs text-blue-300 hover:underline">Edit</button>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="p-3 sm:p-6 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            className="w-full px-4 sm:px-6 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors font-medium text-sm sm:text-base"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
