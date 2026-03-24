import { ProjectionLagDashboard } from '@/components/projections/projection-lag-dashboard'
import { listProjectionLag } from '@/lib/ledger-api'

export default async function ProjectionsPage() {
  const snapshot = await listProjectionLag()
  return <ProjectionLagDashboard snapshot={snapshot} />
}
