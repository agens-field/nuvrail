import { HashRouter, NavLink, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import PendingView from './views/PendingView'
import AuditView from './views/AuditView'
import AgentsView from './views/AgentsView'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 2000,
    },
  },
})

const NAV_LINK_BASE =
  'px-4 py-2 text-sm font-medium rounded-md transition-colors'
const NAV_LINK_ACTIVE = 'bg-indigo-600 text-white'
const NAV_LINK_INACTIVE = 'text-slate-400 hover:text-slate-100'

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <div className="min-h-screen bg-slate-900 text-slate-100">
          {/* Top nav */}
          <header className="border-b border-slate-700 bg-slate-900 sticky top-0 z-10">
            <div className="max-w-4xl mx-auto px-4 py-3 flex items-center gap-2">
              <span className="font-bold text-indigo-400 mr-4 text-lg tracking-tight">
                Nuvrail
              </span>
              <nav className="flex gap-1">
                <NavLink
                  to="/"
                  end
                  className={({ isActive }) =>
                    `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                  }
                >
                  Pending
                </NavLink>
                <NavLink
                  to="/audit"
                  className={({ isActive }) =>
                    `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                  }
                >
                  Audit Log
                </NavLink>
                <NavLink
                  to="/agents"
                  className={({ isActive }) =>
                    `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                  }
                >
                  Agents
                </NavLink>
              </nav>
            </div>
          </header>

          {/* Page content */}
          <main className="max-w-4xl mx-auto px-4 py-6">
            <Routes>
              <Route path="/" element={<PendingView />} />
              <Route path="/audit" element={<AuditView />} />
              <Route path="/agents" element={<AgentsView />} />
            </Routes>
          </main>
        </div>
      </HashRouter>
      <Toaster position="bottom-right" theme="dark" richColors />
    </QueryClientProvider>
  )
}
