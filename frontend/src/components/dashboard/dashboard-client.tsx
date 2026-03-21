'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { KpiCard } from '@/components/ui/kpi-card'
import { StatusBadge } from '@/components/ui/status-badge'
import { Card } from '@/components/ui/card'
import { formatCurrency, formatDateTime, formatPercent } from '@/lib/utils'
import { ApplicationStatus, LoanApplication, RiskTier } from '@/types/loan'

const statusFilters: Array<ApplicationStatus | 'All'> = ['All', 'Approved', 'Declined', 'Human Review', 'In Progress']
const riskFilters: Array<RiskTier | 'All'> = ['All', 'Low', 'Moderate', 'Elevated', 'High']

export function DashboardClient({ applications }: { applications: LoanApplication[] }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<ApplicationStatus | 'All'>('All')
  const [riskTier, setRiskTier] = useState<RiskTier | 'All'>('All')

  const filteredApplications = useMemo(() => {
    const q = query.trim().toLowerCase()
    return applications.filter((application) => {
      const matchesQuery =
        !q ||
        application.id.toLowerCase().includes(q) ||
        application.businessName.toLowerCase().includes(q) ||
        application.companyId.toLowerCase().includes(q)
      const matchesStatus = status === 'All' || application.status === status
      const matchesRisk = riskTier === 'All' || application.riskTier === riskTier
      return matchesQuery && matchesStatus && matchesRisk
    })
  }, [applications, query, riskTier, status])

  const kpis = useMemo(() => {
    const total = filteredApplications.length
    const approved = filteredApplications.filter((app) => app.status === 'Approved').length
    const declined = filteredApplications.filter((app) => app.status === 'Declined').length
    const humanReview = filteredApplications.filter((app) => app.status === 'Human Review').length
    const decisionTimes = filteredApplications
      .filter((app) => app.decisionTimeMinutes > 0)
      .map((app) => app.decisionTimeMinutes)
    const avgDecisionTime = decisionTimes.length
      ? Math.round(decisionTimes.reduce((sum, value) => sum + value, 0) / decisionTimes.length)
      : 0

    return {
      total,
      approved,
      declined,
      humanReview,
      avgDecisionTime
    }
  }, [filteredApplications])

  return (
    <div className="space-y-6">
      <div className="grid gap-4 xl:grid-cols-5">
        <KpiCard label="Total Applications" value={kpis.total.toString()} helperText="Visible in current filter set" />
        <KpiCard label="Approved" value={kpis.approved.toString()} helperText="Auto-decision approvals" />
        <KpiCard label="Declined" value={kpis.declined.toString()} helperText="Policy or risk-based declines" />
        <KpiCard label="Human Review" value={kpis.humanReview.toString()} helperText="Cases requiring analyst attention" />
        <KpiCard label="Average Decision Time" value={`${kpis.avgDecisionTime} min`} helperText="From submission to final decision" />
      </div>

      <Card
        title="Applications Dashboard"
        eyebrow="Decision queue"
        actions={
          <Link href="/applications/new" className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
            New Application
          </Link>
        }
      >
        <div className="grid gap-3 md:grid-cols-[1.3fr_1fr_1fr]">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search application ID, company ID, or business name"
            className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-0 placeholder:text-slate-400 focus:border-teal-400"
          />
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as ApplicationStatus | 'All')}
            className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
          >
            {statusFilters.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select
            value={riskTier}
            onChange={(event) => setRiskTier(event.target.value as RiskTier | 'All')}
            className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
          >
            {riskFilters.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-6 overflow-hidden rounded-3xl border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-left">
            <thead className="bg-slate-50">
              <tr className="text-xs uppercase tracking-[0.18em] text-slate-500">
                <th className="px-4 py-3 font-semibold">Application ID</th>
                <th className="px-4 py-3 font-semibold">Business Name</th>
                <th className="px-4 py-3 font-semibold">Requested Amount</th>
                <th className="px-4 py-3 font-semibold">Loan Type</th>
                <th className="px-4 py-3 font-semibold">Risk Tier</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 font-semibold">Last Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {filteredApplications.map((application) => (
                <tr key={application.id} className="hover:bg-slate-50/90">
                  <td className="px-4 py-4">
                    <Link className="font-semibold text-teal-700 hover:text-teal-900" href={`/applications/${application.id}`}>
                      {application.id}
                    </Link>
                  </td>
                  <td className="px-4 py-4 text-sm text-slate-900">{application.businessName}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{formatCurrency(application.requestedAmount)}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{application.loanType}</td>
                  <td className="px-4 py-4 text-sm text-slate-700">{application.riskTier}</td>
                  <td className="px-4 py-4">
                    <StatusBadge value={application.status} kind="application" />
                  </td>
                  <td className="px-4 py-4 text-sm text-slate-500">{formatDateTime(application.lastUpdated)}</td>
                </tr>
              ))}
              {filteredApplications.length === 0 ? (
                <tr>
                  <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={7}>
                    No applications match the current filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Portfolio Mix" eyebrow="Risk snapshot">
          <div className="space-y-4">
            {applications.map((application) => (
              <div key={application.id} className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-slate-700">{application.businessName}</span>
                  <span className="text-slate-500">{formatPercent(application.confidenceScore)}</span>
                </div>
                <div className="h-2 rounded-full bg-slate-100">
                  <div
                    className="h-2 rounded-full bg-teal-600"
                    style={{ width: `${Math.max(application.confidenceScore, 8)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Demo notes" eyebrow="Integration ready">
          <div className="space-y-3 text-sm leading-6 text-slate-600">
            <p>
              The dashboard is wired to mock data now, but the fetch layer is isolated in <code>src/lib/ledger-api.ts</code>.
            </p>
            <p>
              When the Python backend is ready, point <code>NEXT_PUBLIC_LEDGER_API_BASE_URL</code> at your thin FastAPI or MCP adapter.
            </p>
            <p>The UI components, routes, and data types are already separated so the backend swap should be straightforward.</p>
          </div>
        </Card>
      </div>
    </div>
  )
}
