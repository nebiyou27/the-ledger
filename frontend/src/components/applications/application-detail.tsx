import Link from 'next/link'
import { Card } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/status-badge'
import { Timeline } from '@/components/ui/timeline'
import { ledgerApiBaseUrl } from '@/lib/ledger-api'
import { formatCurrency, formatDateTime, formatPercent } from '@/lib/utils'
import { LoanApplication } from '@/types/loan'

export function ApplicationDetail({ application }: { application: LoanApplication }) {
  return (
    <div className="space-y-6">
      <Card
        title={application.businessName}
        eyebrow={application.id}
        actions={<StatusBadge value={application.status} kind="application" />}
      >
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="rounded-2xl bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Requested Amount</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{formatCurrency(application.requestedAmount)}</div>
          </div>
          <div className="rounded-2xl bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Current Status</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{application.currentStatus}</div>
          </div>
          <div className="rounded-2xl bg-slate-50 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Final Recommendation</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">
              {application.finalRecommendation}{' '}
              <span className="text-base font-medium text-slate-500">
                {application.confidenceScore ? formatPercent(application.confidenceScore) : 'Pending'}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[
            ['Application ID', application.id],
            ['Business Name', application.businessName],
            ['Loan Type', application.loanType],
            ['Last Updated', formatDateTime(application.lastUpdated)]
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{label}</div>
              <div className="mt-2 text-sm font-medium text-slate-900">{value}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1fr_0.9fr]">
        <Card title="Pipeline Stages" eyebrow="Decision flow">
          <div className="space-y-3">
            {application.pipeline.map((stage, index) => (
              <div key={stage.name} className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-4">
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white font-semibold text-slate-800 ring-1 ring-slate-200">
                    {index + 1}
                  </div>
                  <div>
                    <div className="font-semibold text-slate-900">{stage.name}</div>
                    <div className="text-sm text-slate-500">{stage.owner}</div>
                  </div>
                </div>
                <div className="text-right">
                  <StatusBadge value={stage.state} kind="stage" />
                  {stage.completedAt ? <div className="mt-2 text-xs text-slate-500">{formatDateTime(stage.completedAt)}</div> : null}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Extracted Facts" eyebrow="Structured outputs">
          <div className="grid gap-4 sm:grid-cols-2">
            {[
              ['Revenue', application.extractedFacts.revenue],
              ['EBITDA', application.extractedFacts.ebitda],
              ['Debt', application.extractedFacts.debt],
              ['Cash Flow', application.extractedFacts.cashFlow]
            ].map(([label, value]) => (
              <div key={label} className="rounded-2xl bg-slate-50 p-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{label}</div>
                <div className="mt-2 text-lg font-semibold text-slate-900">{value}</div>
              </div>
            ))}
            <div className="sm:col-span-2 rounded-2xl bg-amber-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Flags</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {application.extractedFacts.flags.map((flag) => (
                  <span key={flag} className="rounded-full bg-white px-3 py-1 text-sm text-amber-900 ring-1 ring-amber-200">
                    {flag}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <Card title="Decision Result Panel" eyebrow="Recommendation">
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-2xl bg-slate-50 p-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Outcome</div>
                <div className="mt-1 text-2xl font-semibold text-slate-900">{application.finalRecommendation}</div>
              </div>
              <StatusBadge value={application.status} kind="application" />
            </div>
            <div className="rounded-2xl bg-slate-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Confidence Score</div>
              <div className="mt-2 text-3xl font-semibold text-slate-900">
                {application.confidenceScore ? formatPercent(application.confidenceScore) : 'Pending'}
              </div>
            </div>
            <div>
              <div className="text-sm font-semibold text-slate-900">Reasons</div>
              <ul className="mt-2 space-y-2 text-sm text-slate-600">
                {application.decisionReasons.map((reason) => (
                  <li key={reason} className="rounded-2xl bg-slate-50 px-4 py-3">
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              {[
                ['Credit', application.analyses.credit.verdict, application.analyses.credit.notes],
                ['Fraud', application.analyses.fraud.verdict, application.analyses.fraud.notes],
                ['Compliance', application.analyses.compliance.verdict, application.analyses.compliance.notes]
              ].map(([label, verdict, notes]) => (
                <div key={label} className="rounded-2xl border border-slate-200 p-4">
                  <div className="text-sm font-semibold text-slate-900">{label}</div>
                  <div className="mt-1 text-sm font-medium text-teal-700">{verdict}</div>
                  <div className="mt-2 text-sm text-slate-500">{notes}</div>
                </div>
              ))}
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">{application.policyNotes}</div>
          </div>
        </Card>

        <Card title="Recent Event Timeline" eyebrow="Audit trail">
          <Timeline events={application.timeline} />
          <div className="mt-4">
            <Link href="/audit" className="text-sm font-semibold text-teal-700 hover:text-teal-900">
              Open full event timeline
            </Link>
          </div>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Compliance Snapshot" eyebrow="Rule at time of decision">
          <div className="space-y-3">
            {application.complianceResults.length ? (
              application.complianceResults.map((rule) => (
                <div key={rule.ruleId} className="rounded-2xl border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold text-slate-900">{rule.label}</div>
                      <div className="mt-1 text-sm text-slate-500">{rule.note}</div>
                    </div>
                    <StatusBadge value={rule.status === 'passed' ? 'Approved' : 'Declined'} kind="application" />
                  </div>
                  <div className="mt-3 text-xs text-slate-500">Regulation version {rule.regulationVersion}</div>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                No compliance findings yet.
              </div>
            )}
          </div>
        </Card>

        <Card title="Document Package" eyebrow="Uploaded evidence">
          <div className="space-y-3">
            {application.documents.map((document) => (
              <div key={document.name} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div>
                  <div className="font-semibold text-slate-900">{document.name}</div>
                  <div className="mt-1 text-sm text-slate-500">
                    {document.type} - {document.size}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {document.downloadUrl && ledgerApiBaseUrl ? (
                    <a
                      href={`${ledgerApiBaseUrl}${document.downloadUrl}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm font-semibold text-teal-700 hover:text-teal-900"
                    >
                      Open file
                    </a>
                  ) : null}
                  <StatusBadge
                    value={document.status === 'verified' ? 'Approved' : document.status === 'extracted' ? 'Human Review' : 'In Progress'}
                    kind="application"
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
