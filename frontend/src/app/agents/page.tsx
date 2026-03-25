import Link from 'next/link'
import { Card } from '@/components/ui/card'
import { listAgentPerformance } from '@/lib/ledger-api'
import { formatPercent } from '@/lib/utils'

export default async function AgentsPage() {
  const agentPerformance = await listAgentPerformance()
  const hasAgentPerformance = agentPerformance.length > 0

  return (
    <div className="space-y-6">
      {hasAgentPerformance ? (
        <div className="grid gap-4 xl:grid-cols-4">
          {agentPerformance.map((agent) => (
            <div key={agent.agentName} className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="text-sm font-medium text-slate-500">{agent.agentName}</div>
              <div className="mt-2 text-2xl font-semibold text-slate-900">{agent.modelVersion}</div>
              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-slate-400">Decisions</div>
                  <div className="font-semibold text-slate-900">{agent.decisions}</div>
                </div>
                <div>
                  <div className="text-slate-400">Overrides</div>
                  <div className="font-semibold text-slate-900">{agent.overrides}</div>
                </div>
                <div>
                  <div className="text-slate-400">Avg Confidence</div>
                  <div className="font-semibold text-slate-900">{formatPercent(agent.averageConfidence * 100)}</div>
                </div>
                <div>
                  <div className="text-slate-400">Referral Rate</div>
                  <div className="font-semibold text-slate-900">{formatPercent(agent.referralRate * 100)}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Card title="Agent Performance Snapshot" eyebrow="Operational metrics">
          <div className="rounded-3xl border border-dashed border-slate-200 bg-slate-50 px-5 py-8 text-sm leading-6 text-slate-600">
            No agent performance data has been written yet. This dashboard fills after the backend records agent session and decision events.
            <div className="mt-4 flex flex-wrap gap-3">
              <Link href="/applications/new" className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
                Submit an application
              </Link>
              <span className="rounded-full bg-white px-4 py-2 text-sm text-slate-500 ring-1 ring-slate-200">
                Agent metrics come from `/agents`
              </span>
            </div>
          </div>
        </Card>
      )}

      <Card title="Decision Mix by Agent" eyebrow="Quick charts">
        {hasAgentPerformance ? (
          <div className="space-y-4">
            {agentPerformance.map((agent) => {
              const total = agent.approved + agent.declined + agent.humanReview
              const approvedShare = (agent.approved / total) * 100
              const declinedShare = (agent.declined / total) * 100
              const reviewShare = (agent.humanReview / total) * 100

              return (
                <div key={agent.agentName} className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-semibold text-slate-800">{agent.agentName}</span>
                    <span className="text-slate-500">{formatPercent(agent.averageConfidence * 100)} average confidence</span>
                  </div>
                  <div className="overflow-hidden rounded-full bg-slate-100">
                    <div className="flex h-3">
                      <div className="bg-emerald-500" style={{ width: `${approvedShare}%` }} />
                      <div className="bg-rose-500" style={{ width: `${declinedShare}%` }} />
                      <div className="bg-amber-500" style={{ width: `${reviewShare}%` }} />
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs text-slate-500">
                    <span>Approved {formatPercent(approvedShare)}</span>
                    <span>Declined {formatPercent(declinedShare)}</span>
                    <span>Human Review {formatPercent(reviewShare)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="rounded-3xl border border-dashed border-slate-200 bg-slate-50 px-5 py-8 text-sm text-slate-600">
            There are no agent outcomes to chart yet.
          </div>
        )}
      </Card>

      <Card title="Agent Performance Dashboard" eyebrow="Operational metrics">
        <div className="overflow-hidden rounded-3xl border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-left">
            <thead className="bg-slate-50">
              <tr className="text-xs uppercase tracking-[0.18em] text-slate-500">
                <th className="px-4 py-3 font-semibold">Agent</th>
                <th className="px-4 py-3 font-semibold">Model Version</th>
                <th className="px-4 py-3 font-semibold">Decision Counts</th>
                <th className="px-4 py-3 font-semibold">Override Rate</th>
                <th className="px-4 py-3 font-semibold">Average Confidence</th>
                <th className="px-4 py-3 font-semibold">Referral Rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {hasAgentPerformance ? (
                agentPerformance.map((agent) => (
                  <tr key={agent.agentName}>
                    <td className="px-4 py-4 font-semibold text-slate-900">{agent.agentName}</td>
                    <td className="px-4 py-4 text-sm text-slate-600">{agent.modelVersion}</td>
                    <td className="px-4 py-4 text-sm text-slate-700">
                      A {agent.approved} / D {agent.declined} / R {agent.humanReview}
                    </td>
                    <td className="px-4 py-4 text-sm text-slate-700">{formatPercent((agent.overrides / agent.decisions) * 100)}</td>
                    <td className="px-4 py-4 text-sm text-slate-700">{formatPercent(agent.averageConfidence * 100)}</td>
                    <td className="px-4 py-4 text-sm text-slate-700">{formatPercent(agent.referralRate * 100)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={6}>
                    No agent performance records are available yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
