import { notFound } from 'next/navigation'
import { ApplicationDetail } from '@/components/applications/application-detail'
import { getApplicationById } from '@/lib/ledger-api'

export default async function ApplicationDetailPage({
  params
}: {
  params: Promise<{ applicationId: string }>
}) {
  const { applicationId } = await params
  const application = await getApplicationById(applicationId)

  if (!application) {
    notFound()
  }

  return <ApplicationDetail application={application} />
}
