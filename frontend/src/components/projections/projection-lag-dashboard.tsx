'use client'

import { useState } from 'react'
import { Card } from '@/components/ui/card'
import { KpiCard } from '@/components/ui/kpi-card'
import { dataSourceNote, listEventThroughput, listProjectionLag, refreshProjections } from '@/lib/ledger-api'
import { formatDuration } from '@/lib/utils'
import { EventThroughputSnapshot, ProjectionLagSnapshot } from '@/types/loan'

const projectionMeta: Record<
  string,
  {
    label: string
    owner: string
    targetMillis: number
    summary: string
  }
> = {
  application_summary: {
    label: 'Application Summary',
    owner: 'Underwriting',
    targetMillis: 500,
    summary: 'Primary loan decision read model'
  },
  agent_session_failures: {
    label: 'Agent Session Failures',
    owner: 'Platform',
    targetMillis: 2000,
    summary: 'Exception stream for long-running agent work'
  },
  agent_performance: {
    label: 'Agent Performance',
    owner: 'Operations',
    targetMillis: 2000,
    summary: 'Agent scorecards and throughput metrics'
  },
  compliance_audit: {
    label: 'Compliance Audit',
    owner: 'Compliance',
    targetMillis: 2000,
    summary: 'Audit evidence and rule outcomes'
  },
  manual_reviews: {
    label: 'Manual Reviews',
    owner: 'Review Team',
    targetMillis: 1000,
    summary: 'Human review queue and analyst assignments'
  }
}

function getStatus(positionsBehind: number, millis: number, targetMillis: number) {
  if (positionsBehind === 0 && millis <= targetMillis) {
    return {
      label: 'Healthy',
      className: 'bg-emerald-50 text-emerald-700 ring-emerald-200'
    }
  }

  if (positionsBehind <= 2 && millis <= targetMillis * 2) {
    return {
      label: 'Watch',
      className: 'bg-amber-50 text-amber-800 ring-amber-200'
    }
  }

  return {
    label: 'Lagging',
    className: 'bg-rose-50 text-rose-700 ring-rose-200'
  }
}

function formatProjectionName(name: string) {
  return projectionMeta[name]?.label ?? name.replaceAll('_', ' ')
}

export function ProjectionLagDashboard({
  snapshot: initialSnapshot,
  throughput: initialThroughput
}: {
  snapshot: ProjectionLagSnapshot
  throughput: EventThroughputSnapshot
}) {
  const [snapshot, setSnapshot] = useState(initialSnapshot)
  const [throughput, setThroughput] = useState(initialThroughput)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null)

  const rows = Object.entries(snapshot)
    .map(([name, lag]) => {
      const meta = projectionMeta[name] ?? {
        label: formatProjectionName(name),
        owner: 'Unassigned',
        targetMillis: 1000,
        summary: 'Projection lag snapshot'
      }
      const status = getStatus(lag.positionsBehind, lag.millis, meta.targetMillis)

      return {
        name,
        meta,
        lag,
        status
      }
    })
    .sort((left, right) => right.lag.millis - left.lag.millis)

  const totalLaggedPositions = rows.reduce((sum, row) => sum + row.lag.positionsBehind, 0)
  const laggingProjections = rows.filter((row) => row.lag.positionsBehind > 0 || row.lag.millis > row.meta.targetMillis).length
  const healthyProjections = rows.length - laggingProjections
  const worstLag = rows[0]?.lag.millis ?? 0
  const peakBucket =
    throughput.buckets.reduce(
      (winner, bucket) => (bucket.events > winner.events ? bucket : winner),
      throughput.buckets[0] ?? {
        label: throughput.peakBucketLabel,
        events: throughput.peakBucketEvents,
        startAt: throughput.windowStartAt,
        endAt: throughput.windowStartAt
      }
    )

  async function handleRefresh() {
    setIsRefreshing(true)
    setRefreshError(null)

    try {
      await refreshProjections()
      const nextSnapshot = await listProjectionLag()
      const nextThroughput = await listEventThroughput()
      setSnapshot(nextSnapshot)
      setThroughput(nextThroughput)
      setLastRefreshedAt(new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' }))
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : 'Refresh failed')
    } finally {
      setIsRefreshing(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 xl:grid-cols-4">
        <KpiCard label="Events in Window" value={throughput.totalEvents.toString()} helperText={`Last ${throughput.windowMinutes} minutes of activity`} />
        <KpiCard label="Event Throughput" value={`${throughput.eventsPerMinute.toFixed(1)} / min`} helperText="Average append rate over the observed window" />
        <KpiCard label="Peak Burst" value={`${peakBucket.events} events`} helperText={`${throughput.bucketMinutes}-minute bucket at ${throughput.peakBucketLabel}`} />
        <KpiCard label="Projections Tracked" value={rows.length.toString()} helperText="Read models included in the runtime snapshot" />
      </div>

      <div className="grid gap-4 xl:grid-cols-4">
        <KpiCard label="Healthy Projections" value={healthyProjections.toString()} helperText="Within position and latency targets" />
        <KpiCard label="Lagging Projections" value={laggingProjections.toString()} helperText="Need attention or a sync cycle" />
        <KpiCard label="Total Positions Behind" value={totalLaggedPositions.toString()} helperText={`Worst lag ${formatDuration(worstLag)}`} />
        <KpiCard
          label="Peak Event Hour"
          value={`${throughput.eventsPerHour.toFixed(0)} / hr`}
          helperText={`Latest event ${throughput.latestEventAt ? new Date(throughput.latestEventAt).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'unknown'}`}
        />
      </div>

      <Card
        title="Projection Lag Dashboard"
        eyebrow="Read model health"
        actions={
          <div className="flex flex-col items-end gap-2 sm:flex-row sm:items-center">
            <button
              type="button"
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {isRefreshing ? 'Refreshing...' : 'Refresh projections'}
            </button>
            <div className="rounded-full bg-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              {dataSourceNote}
            </div>
            {lastRefreshedAt ? <div className="text-xs text-slate-500">Last refreshed at {lastRefreshedAt}</div> : null}
          </div>
        }
      >
        {refreshError ? (
          <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {refreshError}
          </div>
        ) : null}
        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-4">
            {rows.map((row) => (
              <div key={row.name} className="rounded-3xl border border-slate-200 bg-slate-50/70 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-base font-semibold text-slate-900">{row.meta.label}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {row.meta.summary} - Owner {row.meta.owner}
                    </div>
                  </div>
                  <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${row.status.className}`}>
                    {row.status.label}
                  </span>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  <div className="rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Positions Behind</div>
                    <div className="mt-1 text-2xl font-semibold text-slate-900">{row.lag.positionsBehind}</div>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Lag</div>
                    <div className="mt-1 text-2xl font-semibold text-slate-900">{formatDuration(row.lag.millis)}</div>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Target</div>
                    <div className="mt-1 text-2xl font-semibold text-slate-900">{formatDuration(row.meta.targetMillis)}</div>
                  </div>
                </div>

                <div className="mt-4">
                  <div className="flex items-center justify-between text-xs text-slate-500">
                    <span>Relative lag</span>
                    <span>
                      {row.lag.millis > 0 ? Math.min(100, Math.round((row.lag.millis / Math.max(row.meta.targetMillis, row.lag.millis)) * 100)) : 0}%
                    </span>
                  </div>
                  <div className="mt-2 h-2 rounded-full bg-white ring-1 ring-slate-200">
                    <div
                      className={`h-2 rounded-full ${
                        row.status.label === 'Healthy'
                          ? 'bg-emerald-500'
                          : row.status.label === 'Watch'
                            ? 'bg-amber-500'
                            : 'bg-rose-500'
                      }`}
                      style={{
                        width: `${row.lag.millis > 0 ? Math.min(100, Math.round((row.lag.millis / Math.max(row.meta.targetMillis, row.lag.millis)) * 100)) : 0}%`
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="space-y-4">
            <Card title="Event Throughput" eyebrow="Append velocity">
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-2xl bg-slate-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Window</div>
                    <div className="mt-1 text-lg font-semibold text-slate-900">{throughput.windowMinutes} min</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Peak Bucket</div>
                    <div className="mt-1 text-lg font-semibold text-slate-900">{throughput.peakBucketEvents} events</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Latest Event</div>
                    <div className="mt-1 text-lg font-semibold text-slate-900">
                      {throughput.latestEventAt ? new Date(throughput.latestEventAt).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'Unknown'}
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs text-slate-500">
                    <span>5-minute buckets</span>
                    <span>{throughput.totalEvents} events total</span>
                  </div>
                  <div className="flex h-32 items-end gap-2 rounded-3xl border border-slate-200 bg-white px-4 py-3">
                    {throughput.buckets.map((bucket) => {
                      const maxEvents = Math.max(1, throughput.peakBucketEvents)
                      const height = Math.max(8, Math.round((bucket.events / maxEvents) * 100))
                      return (
                        <div key={`${bucket.label}-${bucket.startAt}`} className="flex flex-1 flex-col items-center gap-2">
                          <div className="flex h-full w-full items-end">
                            <div
                              className="w-full rounded-t-2xl bg-gradient-to-t from-teal-600 to-cyan-400 shadow-sm shadow-teal-200/60"
                              style={{ height: `${height}%` }}
                              title={`${bucket.label}: ${bucket.events} events`}
                            />
                          </div>
                          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">{bucket.label}</div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            </Card>

            <Card title="Operational Notes" eyebrow="How to read this" className="bg-slate-950 text-slate-100 shadow-slate-900/20">
              <div className="space-y-3 text-sm leading-6 text-slate-300">
                <p>Throughput measures how many events arrived during the most recent observed window.</p>
                <p>Positions behind measures how many events a projection still needs to process.</p>
                <p>Latency is derived from the newest event timestamp versus the last processed event timestamp.</p>
                <p>If you connect the Python backend, this panel reads live lag from <code>/projections/lag</code> and throughput from <code>/metrics/events</code>.</p>
              </div>
            </Card>

            <Card title="Lag Summary" eyebrow="Current snapshot">
              <div className="space-y-3">
                {rows.map((row) => (
                  <div key={`${row.name}-summary`} className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900">{row.meta.label}</div>
                      <div className="text-xs text-slate-500">{row.meta.owner}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-semibold text-slate-900">{formatDuration(row.lag.millis)}</div>
                      <div className="text-xs text-slate-500">{row.lag.positionsBehind} behind</div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      </Card>
    </div>
  )
}
