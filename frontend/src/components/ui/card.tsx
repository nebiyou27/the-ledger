import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Card({
  title,
  eyebrow,
  children,
  className,
  actions
}: {
  title: string
  eyebrow?: string
  children: ReactNode
  className?: string
  actions?: ReactNode
}) {
  return (
    <section className={cn('rounded-3xl border border-slate-200 bg-white p-6 shadow-sm shadow-slate-200/40', className)}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          {eyebrow ? <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{eyebrow}</div> : null}
          <h2 className="mt-1 text-lg font-semibold text-slate-900">{title}</h2>
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}
