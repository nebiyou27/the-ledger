import { ProjectionLagDashboard } from '@/components/projections/projection-lag-dashboard'
import { listEventThroughput, listProjectionLag } from '@/lib/ledger-api'

export default async function ProjectionsPage() {
  const snapshot = await listProjectionLag()
  const throughput = await listEventThroughput()
  return <ProjectionLagDashboard snapshot={snapshot} throughput={throughput} />
}
