const baseUrl = process.env.NEXT_PUBLIC_LEDGER_API_BASE_URL?.replace(/\/$/, '')

export const dataSourceNote = baseUrl
  ? `Connected to ${baseUrl}`
  : 'Set NEXT_PUBLIC_LEDGER_API_BASE_URL to connect the Python backend.'
