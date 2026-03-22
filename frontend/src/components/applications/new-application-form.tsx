'use client'

import { useRouter } from 'next/navigation'
import { ChangeEvent, FormEvent, useState } from 'react'
import { Card } from '@/components/ui/card'
import { dataSourceNote, submitIntakeApplication } from '@/lib/ledger-api'

type FileSlot = 'application_proposal' | 'income_statement' | 'balance_sheet' | 'bank_statements'

const loanPurposeOptions = [
  ['working_capital', 'Working Capital'],
  ['equipment_financing', 'Equipment Financing'],
  ['real_estate', 'Real Estate'],
  ['expansion', 'Expansion'],
  ['refinancing', 'Refinancing'],
  ['acquisition', 'Acquisition'],
  ['bridge', 'Bridge']
] as const

const requiredSlots: Array<{ key: FileSlot; label: string; helper: string }> = [
  { key: 'application_proposal', label: 'Application Proposal', helper: 'Pitch deck, proposal, or intake PDF' },
  { key: 'income_statement', label: 'Income Statement', helper: 'PDF, XLSX, or CSV' },
  { key: 'balance_sheet', label: 'Balance Sheet', helper: 'PDF, XLSX, or CSV' }
]

const optionalSlots: Array<{ key: FileSlot; label: string; helper: string }> = [
  { key: 'bank_statements', label: 'Bank Statements', helper: 'Optional supporting evidence' }
]

const defaultApplicationId = ''

export function NewApplicationForm() {
  const router = useRouter()
  const [companyId, setCompanyId] = useState('COMP-001')
  const [applicationId, setApplicationId] = useState(defaultApplicationId)
  const [requestedAmount, setRequestedAmount] = useState('250000')
  const [loanPurpose, setLoanPurpose] = useState('working_capital')
  const [urls, setUrls] = useState<Record<FileSlot, string>>({
    application_proposal: '',
    income_statement: '',
    balance_sheet: '',
    bank_statements: ''
  })
  const [files, setFiles] = useState<Record<FileSlot, File | null>>({
    application_proposal: null,
    income_statement: null,
    balance_sheet: null,
    bank_statements: null
  })
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success' | 'error'>('idle')
  const [message, setMessage] = useState<string>('')

  const connected = dataSourceNote.startsWith('Connected')

  function handleFileChange(slot: FileSlot, event: ChangeEvent<HTMLInputElement>) {
    setFiles((current) => ({
      ...current,
      [slot]: event.target.files?.[0] ?? null
    }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage('')

    if (!connected) {
      setStatus('error')
      setMessage('Connect the frontend to the Python backend first, then upload files.')
      return
    }

    for (const slot of requiredSlots) {
      if (!files[slot.key] && !urls[slot.key].trim()) {
        setStatus('error')
        setMessage(`Please upload or paste a URL for ${slot.label.toLowerCase()} before submitting.`)
        return
      }
    }

    const formData = new FormData()
    formData.append('company_id', companyId.trim())
    if (applicationId.trim()) {
      formData.append('application_id', applicationId.trim())
    }
    formData.append('requested_amount', requestedAmount.trim() || '250000')
    formData.append('loan_purpose', loanPurpose)

    requiredSlots.forEach((slot) => {
      const file = files[slot.key]
      if (file) {
        formData.append(slot.key, file)
      }
      if (urls[slot.key].trim()) {
        formData.append(`${slot.key}_url`, urls[slot.key].trim())
      }
    })
    optionalSlots.forEach((slot) => {
      const file = files[slot.key]
      if (file) {
        formData.append(slot.key, file)
      }
      if (urls[slot.key].trim()) {
        formData.append(`${slot.key}_url`, urls[slot.key].trim())
      }
    })

    setStatus('submitting')
    try {
      const result = await submitIntakeApplication(formData)
      setStatus('success')
      setMessage(`Application ${result.application_id} completed. Opening the full record now.`)
      router.push(`/applications/${result.application_id}`)
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Upload failed.')
    }
  }

  return (
    <div className="space-y-6">
      <Card title="New Loan Application" eyebrow="Browser intake">
        <div className="mb-6 rounded-2xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm text-teal-900">
          {dataSourceNote}
        </div>
        <form className="space-y-6" onSubmit={handleSubmit}>
          <div className="grid gap-4 lg:grid-cols-2">
            <label>
              <div className="mb-2 text-sm font-medium text-slate-700">Company ID</div>
              <input
                value={companyId}
                onChange={(event) => setCompanyId(event.target.value)}
                placeholder="COMP-001"
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
              />
            </label>
            <label>
              <div className="mb-2 text-sm font-medium text-slate-700">Application ID</div>
              <input
                value={applicationId}
                onChange={(event) => setApplicationId(event.target.value)}
                placeholder="Leave blank to auto-generate"
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
              />
            </label>
            <label>
              <div className="mb-2 text-sm font-medium text-slate-700">Requested Amount</div>
              <input
                value={requestedAmount}
                onChange={(event) => setRequestedAmount(event.target.value)}
                inputMode="decimal"
                placeholder="250000"
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
              />
            </label>
            <label>
              <div className="mb-2 text-sm font-medium text-slate-700">Loan Purpose</div>
              <select
                value={loanPurpose}
                onChange={(event) => setLoanPurpose(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
              >
                {loanPurposeOptions.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card title="Required Documents" eyebrow="Upload from your PC">
              <div className="space-y-4">
                {requiredSlots.map((slot) => (
                  <label key={slot.key} className="block rounded-2xl border border-slate-200 bg-slate-50 p-4">
                    <div className="text-sm font-semibold text-slate-900">{slot.label}</div>
                    <div className="mt-1 text-sm text-slate-500">{slot.helper} or paste a remote URL below.</div>
                    <input
                      type="file"
                      accept=".pdf,.xlsx,.xls,.csv"
                      className="mt-3 block w-full text-sm text-slate-600 file:mr-4 file:rounded-full file:border-0 file:bg-slate-900 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white"
                      onChange={(event) => handleFileChange(slot.key, event)}
                    />
                    <input
                      type="url"
                      value={urls[slot.key]}
                      onChange={(event) => setUrls((current) => ({ ...current, [slot.key]: event.target.value }))}
                      placeholder="https://example.com/document.pdf"
                      className="mt-3 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
                    />
                    <div className="mt-2 text-xs text-slate-400">
                      {files[slot.key]?.name ? `Selected: ${files[slot.key]?.name}` : 'No file selected'}
                    </div>
                  </label>
                ))}
              </div>
            </Card>

            <Card title="Optional Documents" eyebrow="Extra evidence">
              <div className="space-y-4">
                {optionalSlots.map((slot) => (
                  <label key={slot.key} className="block rounded-2xl border border-slate-200 bg-white p-4">
                    <div className="text-sm font-semibold text-slate-900">{slot.label}</div>
                    <div className="mt-1 text-sm text-slate-500">{slot.helper} or paste a remote URL.</div>
                    <input
                      type="file"
                      accept=".pdf,.xlsx,.xls,.csv"
                      className="mt-3 block w-full text-sm text-slate-600 file:mr-4 file:rounded-full file:border-0 file:bg-teal-600 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white"
                      onChange={(event) => handleFileChange(slot.key, event)}
                    />
                    <input
                      type="url"
                      value={urls[slot.key]}
                      onChange={(event) => setUrls((current) => ({ ...current, [slot.key]: event.target.value }))}
                      placeholder="https://example.com/bank-statements.pdf"
                      className="mt-3 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none focus:border-teal-400"
                    />
                    <div className="mt-2 text-xs text-slate-400">
                      {files[slot.key]?.name ? `Selected: ${files[slot.key]?.name}` : 'Optional'}
                    </div>
                  </label>
                ))}
                <div className="rounded-2xl bg-slate-50 p-4 text-sm leading-6 text-slate-600">
                  Upload a PDF, XLSX, or CSV from your computer. The backend will save the files, append the
                  application events, and run the credit, fraud, compliance, and decision steps automatically.
                </div>
              </div>
            </Card>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <button
              type="submit"
              disabled={status === 'submitting'}
              className="rounded-2xl bg-teal-600 px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {status === 'submitting' ? 'Uploading and running workflow...' : 'Upload and Run Workflow'}
            </button>
            <div className="text-sm text-slate-500">
              Use a seeded company like <code>COMP-001</code> through <code>COMP-020</code> to line up with the demo data.
            </div>
          </div>

          {message ? (
            <div
              className={`rounded-2xl p-4 text-sm ${
                status === 'error' ? 'border border-rose-200 bg-rose-50 text-rose-800' : 'border border-emerald-200 bg-emerald-50 text-emerald-800'
              }`}
            >
              {message}
            </div>
          ) : null}
        </form>
      </Card>
    </div>
  )
}
