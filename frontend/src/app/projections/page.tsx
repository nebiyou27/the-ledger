import { ProjectionLagDashboard } from '@/components/projections/projection-lag-dashboard'
import { getReplayProgress, listEventThroughput, listProjectionLag, listStreamSizes } from '@/lib/ledger-api'

export default async function ProjectionsPage() {
  const snapshot = await listProjectionLag()
  const throughput = await listEventThroughput()
  const streamSizes = await listStreamSizes()
  const replayProgress = await getReplayProgress()
  return <ProjectionLagDashboard snapshot={snapshot} throughput={throughput} streamSizes={streamSizes} replayProgress={replayProgress} />
}
