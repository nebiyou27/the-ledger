import { cn } from '@/lib/utils'
import { ApplicationStatus, StageState } from '@/types/loan'

const statusStyles: Record<ApplicationStatus, string> = {
  Approved: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  Declined: 'bg-rose-50 text-rose-700 ring-rose-200',
  'Human Review': 'bg-amber-50 text-amber-800 ring-amber-200',
  'In Progress': 'bg-sky-50 text-sky-700 ring-sky-200'
}

const stageStyles: Record<StageState, string> = {
  complete: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  'in-progress': 'bg-sky-50 text-sky-700 ring-sky-200',
  blocked: 'bg-rose-50 text-rose-700 ring-rose-200',
  pending: 'bg-slate-100 text-slate-600 ring-slate-200'
}

export function StatusBadge({
  value,
  kind = 'application'
}: {
  value: ApplicationStatus | StageState
  kind?: 'application' | 'stage'
}) {
  const className = kind === 'application' ? statusStyles[value as ApplicationStatus] : stageStyles[value as StageState]

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ring-1 ring-inset',
        className
      )}
    >
      {value.replace('-', ' ')}
    </span>
  )
}
