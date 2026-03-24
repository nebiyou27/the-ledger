import {
  agentPerformance,
  applications,
  complianceRows,
  eventThroughputSnapshot,
  manualReviewBacklogSnapshot,
  projectionLagSnapshot,
  replayProgressSnapshot,
  streamSizeSnapshot,
  reviewQueue,
  timelineEvents
} from '@/data/mock-data'
import {
  EventThroughputSnapshot,
  LoanApplication,
  ManualReviewBacklogSnapshot,
  ProjectionLagSnapshot,
  ReplayProgressSnapshot,
  ReviewQueueItem,
  StreamSizeSnapshot
} from '@/types/loan'

const baseUrl = process.env.NEXT_PUBLIC_LEDGER_API_BASE_URL?.replace(/\/$/, '')
const apiKey = process.env.NEXT_PUBLIC_LEDGER_API_KEY?.trim()

async function loadOrMock<T>(path: string, fallback: T): Promise<T> {
  if (!baseUrl) {
    return fallback
  }

  try {
    const response = await fetch(`${baseUrl}${path}`, {
      cache: 'no-store',
      headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined
    })

    if (!response.ok) {
      return fallback
    }

    return (await response.json()) as T
  } catch {
    return fallback
  }
}

export async function listApplications() {
  return loadOrMock<LoanApplication[]>('/applications', applications)
}

export async function getApplicationById(id: string) {
  if (!baseUrl) {
    return applications.find((application) => application.id === id) ?? null
  }

  return loadOrMock<LoanApplication | null>(`/applications/${id}`, null)
}

export async function listTimelineEvents() {
  return loadOrMock('/timeline', timelineEvents)
}

export async function listReviewQueue() {
  return loadOrMock<ReviewQueueItem[]>('/review-queue', reviewQueue)
}

export async function listManualReviewBacklogMetrics() {
  return loadOrMock<ManualReviewBacklogSnapshot>('/review-queue/metrics', manualReviewBacklogSnapshot)
}

export async function listComplianceRows() {
  return loadOrMock('/compliance', complianceRows)
}

export async function listAgentPerformance() {
  return loadOrMock('/agents', agentPerformance)
}

export async function listProjectionLag() {
  return loadOrMock<ProjectionLagSnapshot>('/projections/lag', projectionLagSnapshot)
}

export async function getReplayProgress() {
  return loadOrMock<ReplayProgressSnapshot>('/replay/progress', replayProgressSnapshot)
}

export async function listEventThroughput() {
  return loadOrMock<EventThroughputSnapshot>('/metrics/events', eventThroughputSnapshot)
}

export async function listStreamSizes() {
  return loadOrMock<StreamSizeSnapshot>('/metrics/streams', streamSizeSnapshot)
}

export async function refreshProjections() {
  if (!baseUrl) {
    return { ok: true, mock: true as const }
  }

  const response = await fetch(`${baseUrl}/refresh`, {
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
  if (!baseUrl) {
    throw new Error('Mock data mode is active. Set NEXT_PUBLIC_LEDGER_API_BASE_URL to submit a real application.')
  }

  const response = await fetch(`${baseUrl}/applications/intake`, {
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
      file_size_bytes: number
      file_hash: string
      document_type: string
      document_format: string
      fiscal_year: number | null
    }>
    pipeline_result: number
  }
}

export const dataSourceNote = baseUrl
  ? `Connected to ${baseUrl}`
  : 'Mock data mode: set NEXT_PUBLIC_LEDGER_API_BASE_URL to connect the Python backend.'
