import Link from 'next/link'
import { Card } from '@/components/ui/card'
import { listReviewQueue } from '@/lib/ledger-api'
import { formatDateTime, formatPercent } from '@/lib/utils'

export default async function ReviewPage() {
  const reviewQueue = await listReviewQueue()

  return (
    <Card title="Human Review Queue" eyebrow="Manual intervention">
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
  )
}
