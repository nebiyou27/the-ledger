'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ReactNode } from 'react'
import { cn } from '@/lib/utils'

const navItems = [
  { href: '/applications', label: 'Applications Dashboard' },
  { href: '/applications/new', label: 'New Loan Application' },
  { href: '/review', label: 'Human Review Queue' },
  { href: '/audit', label: 'Event Timeline' },
  { href: '/compliance', label: 'Compliance Audit' },
  { href: '/agents', label: 'Agent Performance' }
]

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(15,118,110,0.10),_transparent_28%),linear-gradient(180deg,_#f8fafc_0%,_#eef4f8_100%)] text-slate-900">
      <div className="grid min-h-screen lg:grid-cols-[290px_1fr]">
        <aside className="border-b border-slate-200 bg-slate-950/95 px-6 py-8 text-slate-100 shadow-2xl shadow-slate-900/20 lg:border-b-0 lg:border-r">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-teal-500/15 text-lg font-bold text-teal-300 ring-1 ring-teal-500/30">
              TL
            </div>
            <div>
              <div className="text-sm font-semibold uppercase tracking-[0.22em] text-slate-400">The Ledger</div>
              <div className="text-lg font-semibold">Loan Decisioning</div>
            </div>
          </div>

          <p className="mt-5 max-w-sm text-sm leading-6 text-slate-300">
            Enterprise lending operations dashboard for underwriting, compliance, and human review.
          </p>

          <nav className="mt-8 space-y-2">
            {navItems.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    'flex items-center justify-between rounded-2xl px-4 py-3 text-sm transition',
                    active ? 'bg-teal-500 text-white shadow-lg shadow-teal-950/30' : 'text-slate-300 hover:bg-white/5 hover:text-white'
                  )}
                >
                  <span>{item.label}</span>
                  <span className={cn('text-xs', active ? 'text-teal-50' : 'text-slate-500')}>Open</span>
                </Link>
              )
            })}
          </nav>
        </aside>

        <main className="min-w-0">
          <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/90 backdrop-blur">
            <div className="flex items-center justify-between gap-4 px-6 py-4 lg:px-10">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.24em] text-teal-700">Internal lending platform</div>
                <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-slate-900">
                  Demo-ready operations console
                </h1>
              </div>
            </div>
          </header>

          <div className="px-4 py-6 sm:px-6 lg:px-10 lg:py-8">{children}</div>
        </main>
      </div>
    </div>
  )
}
