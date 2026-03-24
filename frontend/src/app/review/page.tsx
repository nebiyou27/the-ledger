import Link from 'next/link'
import { KpiCard } from '@/components/ui/kpi-card'
import { Card } from '@/components/ui/card'
import { listManualReviewBacklogMetrics, listReviewQueue } from '@/lib/ledger-api'
import { formatDateTime, formatPercent } from '@/lib/utils'

function formatMinutes(value: number) {
  return value > 0 ? `${Math.max(1, Math.round(value / 60000))} min` : '0 min'
}

export default async function ReviewPage() {
  const [reviewQueue, backlog] = await Promise.all([listReviewQueue(), listManualReviewBacklogMetrics()])

  return (
    <div className="space-y-6">
      <Card title="Backlog Pressure" eyebrow="Operational signal" className="bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 text-white shadow-slate-900/20">
        <div className="mb-5 max-w-2xl text-sm leading-6 text-slate-300">
          These are the numbers that tell you whether the queue is getting risky, not just whether it is busy.
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Stale Reviews</div>
            <div className="mt-2 text-4xl font-semibold tracking-tight text-white">{backlog.staleCount}</div>
            <div className="mt-2 text-sm text-slate-300">Open items older than 24 hours</div>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Average Age</div>
            <div className="mt-2 text-4xl font-semibold tracking-tight text-white">{formatMinutes(backlog.averagePendingAgeMillis)}</div>
            <div className="mt-2 text-sm text-slate-300">Mean age across the open queue</div>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Oldest Pending</div>
            <div className="mt-2 text-4xl font-semibold tracking-tight text-white">{formatMinutes(backlog.oldestPendingAgeMillis)}</div>
            <div className="mt-2 text-sm text-slate-300">Longest-waiting review in the queue</div>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-4">
        <KpiCard label="Backlog" value={backlog.backlogCount.toString()} helperText="Open reviews waiting for analyst action" />
        <KpiCard label="Assigned" value={backlog.assignedCount.toString()} helperText="Queued reviews already owned by an analyst" />
        <KpiCard label="Unassigned" value={backlog.unassignedCount.toString()} helperText="Needs a reviewer assignment" />
        <KpiCard label="Resolved" value={backlog.resolvedCount.toString()} helperText="Closed items retained in the projection" />
      </div>

      <Card title="Human Review Queue" eyebrow="Manual intervention">
        <div className="mb-4 text-sm text-slate-500">
          Backlog is sourced from <code>/review-queue/metrics</code> and the queue rows come from <code>/review-queue</code>.
        </div>
        <div className="overflow-hidden rounded-3xl border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-left">
            <thead className="bg-slate-50">
              <tr className="text-xs uppercase tracking-[0.18em] text-slate-500">
                <th className="px-4 py-3 font-semibold">Application ID</th>
                <th className="px-4 py-3 font-semibold">Business Name</th>
                <th className="px-4 py-3 font-semibold">Reason</th>
                <th className="px-4 py-3 font-semibold">Confidence</th>
                <th className="px-4 py-3 font-semibold">Assigned Reviewer</th>
                <th className="px-4 py-3 font-semibold">Last Updated</th>
                <th className="px-4 py-3 font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {reviewQueue.map((application) => (
                <tr key={application.applicationId} className="hover:bg-slate-50/80">
                  <td className="px-4 py-4 font-semibold text-teal-700">
                    <Link href={`/applications/${application.applicationId}`}>{application.applicationId}</Link>
                  </td>
                  <td className="px-4 py-4 text-sm text-slate-900">{application.businessName}</td>
                  <td className="px-4 py-4 text-sm text-slate-600">{application.reason}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{formatPercent(application.confidence)}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{application.assignedReviewer}</td>
                  <td className="px-4 py-4 text-sm text-slate-500">{formatDateTime(application.lastUpdated)}</td>
                  <td className="px-4 py-4">
                    <div className="flex flex-wrap gap-2">
                      <Link href={`/applications/${application.applicationId}`} className="rounded-full bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white">
                        Open Review
                      </Link>
                      <button className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
                        Approve
                      </button>
                      <button className="rounded-full bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 ring-1 ring-rose-200">
                        Decline
                      </button>
                      <button className="rounded-full bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 ring-1 ring-amber-200">
                        Request More Info
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 text-sm text-slate-500">
          Review queue is now sourced from the Python projection when the backend API is available.
        </div>
      </Card>
    </div>
  )
}
