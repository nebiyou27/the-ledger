'use client'

import { useMemo, useState } from 'react'
import { Card } from '@/components/ui/card'
import { Timeline } from '@/components/ui/timeline'
import { TimelineDomain, TimelineEvent } from '@/types/loan'

const filters: Array<TimelineDomain | 'all'> = ['all', 'loan', 'credit', 'fraud', 'compliance', 'agent session']

export function AuditTrailClient({ events }: { events: TimelineEvent[] }) {
  const [selectedDomain, setSelectedDomain] = useState<TimelineDomain | 'all'>('all')

  const filteredEvents = useMemo(() => {
    if (selectedDomain === 'all') {
      return events
    }

    return events.filter((event) => event.domain === selectedDomain)
  }, [events, selectedDomain])

  return (
    <div className="space-y-6">
      <Card title="Event Timeline / Audit Trail" eyebrow="System of record">
        <div className="flex flex-wrap gap-2">
          {filters.map((filter) => (
            <button
              key={filter}
              type="button"
              onClick={() => setSelectedDomain(filter)}
              className={
                filter === selectedDomain
                  ? 'rounded-full bg-teal-600 px-3 py-1 text-sm font-semibold text-white'
                  : 'rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700 ring-1 ring-slate-200'
              }
            >
              {filter}
            </button>
          ))}
        </div>
        <div className="mt-4 text-sm text-slate-500">{filteredEvents.length} events shown</div>
      </Card>

      <Card title="Vertical Timeline" eyebrow="Event stream">
        <Timeline events={filteredEvents} />
      </Card>
    </div>
  )
}
