import { DashboardClient } from '@/components/dashboard/dashboard-client'
import { listApplications } from '@/lib/ledger-api'

export default async function ApplicationsPage() {
  const applications = await listApplications()
  return <DashboardClient applications={applications} />
}
