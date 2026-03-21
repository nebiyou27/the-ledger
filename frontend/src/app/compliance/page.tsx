import { ComplianceAuditClient } from '@/components/compliance/compliance-audit-client'
import { listComplianceRows } from '@/lib/ledger-api'

export default async function CompliancePage() {
  const rows = await listComplianceRows()
  return <ComplianceAuditClient rows={rows} />
}
