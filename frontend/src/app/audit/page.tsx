import { AuditTrailClient } from '@/components/audit/audit-trail-client'
import { listTimelineEvents } from '@/lib/ledger-api'

export default async function AuditPage() {
  const events = await listTimelineEvents()
  return <AuditTrailClient events={events} />
}
