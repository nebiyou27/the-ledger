import { formatDateTime } from '@/lib/utils'
import { TimelineEvent } from '@/types/loan'

function DomainPill({ value }: { value: TimelineEvent['domain'] }) {
  return (
    <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold capitalize text-slate-600 ring-1 ring-slate-200">
      {value}
    </span>
  )
}

export function Timeline({
  events
}: {
  events: TimelineEvent[]
}) {
  return (
    <div className="space-y-4">
      {events.map((event) => (
        <div key={`${event.streamId}-${event.version}`} className="relative pl-6">
          <div className="absolute left-0 top-2 h-3 w-3 rounded-full bg-teal-600 ring-4 ring-teal-100" />
          <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-slate-900">{event.eventName}</div>
                <div className="mt-1 text-xs text-slate-500">{formatDateTime(event.timestamp)}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <DomainPill value={event.domain} />
                {event.sessionId ? (
                  <span className="rounded-full bg-white px-2.5 py-1 ring-1 ring-slate-200">{event.sessionId}</span>
                ) : null}
              </div>
            </div>
            <div className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
              <div>
                <span className="font-semibold text-slate-500">Stream</span> {event.streamId}
              </div>
              <div>
                <span className="font-semibold text-slate-500">Version</span> {event.version}
              </div>
              <div className="sm:col-span-1">
                <span className="font-semibold text-slate-500">Payload</span> {event.payloadPreview}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
