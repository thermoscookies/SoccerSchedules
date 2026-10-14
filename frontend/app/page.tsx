'use client';

import { useQuery } from '@tanstack/react-query';
import { eventsApi, Event } from '@/lib/api';
import Link from 'next/link';
import { format } from 'date-fns';
import { useState } from 'react';
import AdminModal from '@/components/AdminModal';

export default function Home() {
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [showAdminModal, setShowAdminModal] = useState(false);
  
  const { data: events, isLoading, error } = useQuery({
    queryKey: ['events'],
    queryFn: async () => {
      const response = await eventsApi.getAll();
      return response.data;
    },
  });

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-lg text-gray-600">Loading tournaments...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-lg text-red-600">Error loading tournaments</div>
      </div>
    );
  }

  // If an event is selected or only one event exists, navigate to it
  if (selectedEventId) {
    window.location.href = `/events/${selectedEventId}`;
    return null;
  }

  if (events && events.length > 0) {
    window.location.href = `/events/${events[0].id}?events=all`;
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Admin Modal */}
      <AdminModal isOpen={showAdminModal} onClose={() => setShowAdminModal(false)} />
      
      {/* Top Navigation Bar */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <h1 className="text-xl font-semibold text-gray-900">
                Tournament Schedules
              </h1>
            </div>
            <button
              onClick={() => setShowAdminModal(true)}
              className="px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors font-medium"
            >
              Admin
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        {!events || events.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-12 text-center">
            <h3 className="text-lg font-medium text-gray-900 mb-2">No tournaments available</h3>
            <p className="text-gray-500">
              No tournaments have been scraped yet.
            </p>
          </div>
        ) : (
          <div>
            <h2 className="text-2xl font-bold text-gray-900 mb-6">Select a Tournament</h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {events.map((event) => (
                <EventCard 
                  key={event.id} 
                  event={event} 
                  onSelect={() => setSelectedEventId(event.id)}
                />
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function EventCard({ event, onSelect }: { event: Event; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className="block w-full bg-white overflow-hidden shadow rounded-lg hover:shadow-lg transition-all border-2 border-transparent hover:border-blue-500 text-left"
    >
      <div className="p-4 sm:p-6">
        {/* Event ID Badge */}
        <div className="mb-3">
          <span className="inline-block bg-blue-100 text-blue-800 text-xs font-semibold px-2.5 py-0.5 rounded">
            Event ID: {event.gotsport_event_id}
          </span>
        </div>

        {/* Tournament Name */}
        <h3 className="text-lg sm:text-xl font-bold text-gray-900 mb-3">
          {event.name}
        </h3>
        
        {/* Stats */}
        <div className="flex flex-wrap gap-3 text-sm border-t pt-3 mt-3">
          {event.total_divisions !== undefined && (
            <div className="flex items-center gap-1">
              <span className="font-semibold text-gray-700">{event.total_divisions}</span>
              <span className="text-gray-600">divisions</span>
            </div>
          )}
          {event.total_games !== undefined && (
            <div className="flex items-center gap-1">
              <span className="font-semibold text-gray-700">{event.total_games}</span>
              <span className="text-gray-600">games</span>
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
