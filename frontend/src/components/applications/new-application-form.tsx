'use client'

import { useState } from 'react'
import { Card } from '@/components/ui/card'

const syntheticDocs = [
  'financial_statements.xlsx',
  'income_statement_2024.pdf',
  'balance_sheet_2024.pdf',
  'bank_statements.pdf'
]

export function NewApplicationForm() {
  const [documents, setDocuments] = useState<string[]>([])
  const [submitted, setSubmitted] = useState(false)

  return (
    <div className="space-y-6">
      <Card title="New Loan Application" eyebrow="Intake form">
        <form className="grid gap-4 lg:grid-cols-2">
          {[
            'Company ID',
            'Business Name',
            'Loan Type',
            'Requested Amount',
            'Loan Purpose',
            'Annual Revenue',
            'Years in Business',
            'Industry',
            'Collateral Type'
          ].map((label) => (
            <label key={label} className={label === 'Loan Purpose' ? 'lg:col-span-2' : ''}>
              <div className="mb-2 text-sm font-medium text-slate-700">{label}</div>
              <input
                type="text"
                placeholder={label}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
              />
            </label>
          ))}
        </form>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <Card title="Document Upload" eyebrow="Supporting evidence">
          <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-slate-50/80 p-8 text-center">
            <div className="text-base font-semibold text-slate-900">Drop documents here</div>
            <p className="mt-2 text-sm text-slate-500">PDF, CSV, and spreadsheet uploads are supported in the real workflow.</p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
              <label className="cursor-pointer rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
                Upload Files
                <input
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(event) => setDocuments(Array.from(event.target.files ?? []).map((file) => file.name))}
                />
              </label>
              <button
                type="button"
                onClick={() => setDocuments(syntheticDocs)}
                className="rounded-full bg-teal-600 px-4 py-2 text-sm font-semibold text-white"
              >
                Load Synthetic Sample Documents
              </button>
            </div>
            <div className="mt-6 grid gap-2 text-left text-sm text-slate-600">
              {documents.length ? (
                documents.map((document) => (
                  <div key={document} className="rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200">
                    {document}
                  </div>
                ))
              ) : (
                <div className="text-slate-400">No documents loaded yet.</div>
              )}
            </div>
          </div>
        </Card>

        <Card title="Submission" eyebrow="Demo action">
          <p className="text-sm leading-6 text-slate-600">
            The create action is intentionally lightweight here so the frontend stays ready for the Python backend call.
          </p>
          <button
            type="button"
            onClick={() => setSubmitted(true)}
            className="mt-6 w-full rounded-2xl bg-teal-600 px-4 py-3 text-sm font-semibold text-white"
          >
            Create Application
          </button>
          {submitted ? (
            <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
              Demo submission captured. Wire this button to the Python command API or FastAPI wrapper later.
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  )
}
