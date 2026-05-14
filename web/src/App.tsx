import { useEffect } from 'react'
import { HashRouter, NavLink, Route, Routes, useNavigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import PendingView from './views/PendingView'
import AuditView from './views/AuditView'
import RulesView from './views/RulesView'
import AgentsView from './views/AgentsView'
import LoginView from './views/LoginView'
import OAuthCallbackView from './views/OAuthCallbackView'
import AccountView from './views/AccountView'
import ResetRequestView from './views/ResetRequestView'
import ResetView from './views/ResetView'
import SetupView from './views/SetupView'
import { getToken, setupPushNotifications } from './api/client'

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

/**
 * AuthGuard — redirects to /login if no token is stored.
 * Renders nothing while redirecting.
 */
function AuthGuard({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const publicRoutes = ['/login', '/setup', '/reset-request', '/reset']
    const isPublic = publicRoutes.some((r) => location.pathname === r)
    if (!isPublic && !getToken()) {
      navigate('/login', { replace: true })
    }
    // After auth, silently attempt to set up push notifications.
    // No-op if permission already granted, browser unsupported, or denied.
    if (getToken() && !isPublic) {
      void setupPushNotifications()
    }
  }, [location.pathname, navigate])

  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <AuthGuard>
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
                    to="/rules"
                    className={({ isActive }) =>
                      `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                    }
                  >
                    Rules
                  </NavLink>
                  <NavLink
                    to="/audit"
                    className={({ isActive }) =>
                      `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                    }
                  >
                    Audit
                  </NavLink>
                  <NavLink
                    to="/agents"
                    className={({ isActive }) =>
                      `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                    }
                  >
                    Agents
                  </NavLink>
                  <NavLink
                    to="/account"
                    className={({ isActive }) =>
                      `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                    }
                  >
                    Account
                  </NavLink>
                  <NavLink
                    to="/setup"
                    className={({ isActive }) =>
                      `${NAV_LINK_BASE} ${isActive ? NAV_LINK_ACTIVE : NAV_LINK_INACTIVE}`
                    }
                  >
                    Setup
                  </NavLink>
                </nav>
              </div>
            </header>

            {/* Page content */}
            <main className="max-w-4xl mx-auto px-4 py-6">
              <Routes>
                <Route path="/" element={<PendingView />} />
                <Route path="/rules" element={<RulesView />} />
                <Route path="/audit" element={<AuditView />} />
                <Route path="/agents" element={<AgentsView />} />
                <Route path="/oauth2/callback" element={<OAuthCallbackView />} />
                <Route path="/setup" element={<SetupView />} />
                <Route path="/login" element={<LoginView />} />
                <Route path="/account" element={<AccountView />} />
                <Route path="/reset-request" element={<ResetRequestView />} />
                <Route path="/reset" element={<ResetView />} />
              </Routes>
            </main>
          </div>
        </AuthGuard>
      </HashRouter>
      <Toaster position="bottom-right" theme="dark" richColors />
    </QueryClientProvider>
  )
}

