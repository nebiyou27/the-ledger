import { agentPerformance, applications, complianceRows, reviewQueue, timelineEvents } from '@/data/mock-data'
import { LoanApplication } from '@/types/loan'

const baseUrl = process.env.NEXT_PUBLIC_LEDGER_API_BASE_URL?.replace(/\/$/, '')

async function loadOrMock<T>(path: string, fallback: T): Promise<T> {
  if (!baseUrl) {
    return fallback
  }

  try {
    const response = await fetch(`${baseUrl}${path}`, {
      cache: 'no-store'
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
  return loadOrMock('/review-queue', reviewQueue)
}

export async function listComplianceRows() {
  return loadOrMock('/compliance', complianceRows)
}

export async function listAgentPerformance() {
  return loadOrMock('/agents', agentPerformance)
}

export const dataSourceNote = baseUrl
  ? `Connected to ${baseUrl}`
  : 'Mock data mode: set NEXT_PUBLIC_LEDGER_API_BASE_URL to connect the Python backend.'
