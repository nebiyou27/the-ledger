'use client'

import { useMemo, useState } from 'react'
import { Card } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/status-badge'
import { ComplianceRowView } from '@/types/loan'
const asOfOptions = ['Decision Time', '2026-03-18', '2026-03-19', '2026-03-20']

export function ComplianceAuditClient({ rows }: { rows: ComplianceRowView[] }) {
  const [applicationId, setApplicationId] = useState(rows[0]?.applicationId ?? '')
  const [asOf, setAsOf] = useState(asOfOptions[0])

  const selected = useMemo(
    () => rows.find((row) => row.applicationId === applicationId) ?? rows[0],
    [applicationId, rows]
  )

  if (!selected) {
    return null
  }

  return (
    <div className="space-y-6">
      <Card
        title="Compliance Audit View"
        eyebrow="Policy control"
        actions={<span className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white">As of {asOf}</span>}
      >
        <div className="grid gap-4 md:grid-cols-3">
          <label className="block">
            <div className="mb-2 text-sm font-medium text-slate-700">Application</div>
            <select
              value={applicationId}
              onChange={(event) => setApplicationId(event.target.value)}
              className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
            >
              {rows.map((row) => (
                <option key={row.applicationId} value={row.applicationId}>
                  {row.applicationId} - {row.businessName}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <div className="mb-2 text-sm font-medium text-slate-700">As of</div>
            <select
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
            >
              {asOfOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>

          <div className="rounded-2xl bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Regulation Version</div>
            <div className="mt-2 text-3xl font-semibold text-slate-900">{selected.regulationVersion}</div>
          </div>
        </div>
      </Card>

      <Card title={`Compliance at Time of Decision: ${selected.businessName}`} eyebrow={selected.applicationId}>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-2xl bg-emerald-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">Passed Rules</div>
            <div className="mt-2 text-3xl font-semibold text-emerald-950">{selected.passedRules.length}</div>
          </div>
          <div className="rounded-2xl bg-rose-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-rose-700">Failed Rules</div>
            <div className="mt-2 text-3xl font-semibold text-rose-950">{selected.failedRules.length}</div>
          </div>
          <div className="rounded-2xl bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">As of Snapshot</div>
            <div className="mt-2 text-lg font-semibold text-slate-900">{asOf}</div>
          </div>
        </div>

        <div className="mt-6 space-y-3">
          {selected.passedRules.map((rule) => (
            <div key={rule.ruleId} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div>
                <div className="font-semibold text-slate-900">{rule.label}</div>
                <div className="mt-1 text-sm text-slate-500">{rule.note}</div>
              </div>
              <StatusBadge value="Approved" kind="application" />
            </div>
          ))}
          {selected.failedRules.map((rule) => (
            <div key={rule.ruleId} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-rose-50 p-4">
              <div>
                <div className="font-semibold text-slate-900">{rule.label}</div>
                <div className="mt-1 text-sm text-slate-500">{rule.note}</div>
              </div>
              <StatusBadge value="Declined" kind="application" />
            </div>
          ))}
        </div>
        <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">{selected.notes}</div>
      </Card>
    </div>
  )
}
