import {
  AgentPerformanceRecord,
  EventThroughputSnapshot,
  LoanApplication,
  ManualReviewBacklogSnapshot,
  ProjectionLagSnapshot,
  ReplayProgressSnapshot,
  ComplianceRowView,
  ReviewQueueItem,
  TimelineEvent,
  StreamSizeSnapshot
} from '@/types/loan'

export const ledgerApiBaseUrl = process.env.NEXT_PUBLIC_LEDGER_API_BASE_URL?.replace(/\/$/, '')
const ledgerApiInternalBaseUrl = process.env.LEDGER_API_INTERNAL_URL?.replace(/\/$/, '') ?? ledgerApiBaseUrl
const apiKey = process.env.NEXT_PUBLIC_LEDGER_API_KEY?.trim()

function resolveBaseUrl() {
  const baseUrl = typeof window === 'undefined' ? ledgerApiInternalBaseUrl : ledgerApiBaseUrl
  if (!baseUrl) {
    throw new Error('NEXT_PUBLIC_LEDGER_API_BASE_URL is not configured.')
  }
  return baseUrl
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = resolveBaseUrl()
  const response = await fetch(`${baseUrl}${path}`, {
    cache: 'no-store',
    ...init,
    headers: {
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      ...(init?.headers ?? {})
    }
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail ?? payload?.message ?? `Request failed with status ${response.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return (await response.json()) as T
}

export async function listApplications() {
  return fetchJson<LoanApplication[]>('/applications')
}

export async function getApplicationById(id: string) {
  try {
    return await fetchJson<LoanApplication>(`/applications/${id}`)
  } catch (error) {
    if (error instanceof Error && error.message.includes('status 404')) {
      return null
    }
    throw error
  }
}

export async function listTimelineEvents() {
  return fetchJson<TimelineEvent[]>('/timeline')
}

export async function listReviewQueue() {
  return fetchJson<ReviewQueueItem[]>('/review-queue')
}

export async function listManualReviewBacklogMetrics() {
  return fetchJson<ManualReviewBacklogSnapshot>('/review-queue/metrics')
}

export async function listComplianceRows() {
  return fetchJson<ComplianceRowView[]>('/compliance')
}

export async function listAgentPerformance() {
  return fetchJson<AgentPerformanceRecord[]>('/agents')
}

export async function listProjectionLag() {
  return fetchJson<ProjectionLagSnapshot>('/projections/lag')
}

export async function getReplayProgress() {
  return fetchJson<ReplayProgressSnapshot>('/replay/progress')
}

export async function listEventThroughput() {
  return fetchJson<EventThroughputSnapshot>('/metrics/events')
}

export async function listStreamSizes() {
  return fetchJson<StreamSizeSnapshot>('/metrics/streams')
}

export async function refreshProjections() {
  const response = await fetch(`${resolveBaseUrl()}/refresh`, {
    method: 'POST',
    cache: 'no-store',
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `Request failed with status ${response.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }

  return payload
}

export async function submitIntakeApplication(formData: FormData) {
  const response = await fetch(`${resolveBaseUrl()}/applications/intake`, {
    method: 'POST',
    cache: 'no-store',
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
    body: formData
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.message ?? `Request failed with status ${response.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }

  return payload as {
    ok: boolean
    application_id: string
    company_id: string
    detail: unknown
    documents: Array<{
      document_id: string
      filename: string
      file_path: string
      download_url?: string | null
      file_size_bytes: number
      file_hash: string
      document_type: string
      document_format: string
      fiscal_year: number | null
    }>
    pipeline_result: number
  }
}

export const dataSourceNote = ledgerApiBaseUrl
  ? `Connected to ${ledgerApiBaseUrl}`
  : 'Set NEXT_PUBLIC_LEDGER_API_BASE_URL to connect the Python backend.'
