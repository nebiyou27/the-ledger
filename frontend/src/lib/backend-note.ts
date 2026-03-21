const baseUrl = process.env.NEXT_PUBLIC_LEDGER_API_BASE_URL?.replace(/\/$/, '')

export const dataSourceNote = baseUrl
  ? `Connected to ${baseUrl}`
  : 'Mock data mode: set NEXT_PUBLIC_LEDGER_API_BASE_URL to connect the Python backend.'
