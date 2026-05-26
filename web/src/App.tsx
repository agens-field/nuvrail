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
import ThemeToggle, { useTheme } from './components/ThemeToggle'
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
  'px-3 py-1.5 text-sm font-medium rounded-md transition-colors'
const NAV_LINK_ACTIVE = 'bg-accent text-white dark:text-[#111c27]'
const NAV_LINK_INACTIVE = 'text-fg-2 hover:text-fg hover:bg-surface-hi'

function AuthGuard({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const publicRoutes = ['/login', '/reset-request', '/reset']
    const isPublic = publicRoutes.some((r) => location.pathname === r)
    if (!isPublic && !getToken()) {
      navigate('/login', { replace: true })
    }
    if (getToken() && !isPublic) {
      void setupPushNotifications()
    }
  }, [location.pathname, navigate])

  return <>{children}</>
}

function Logo() {
  const { dark } = useTheme()
  return (
    <a href="#/" className="flex items-center gap-2 mr-2 shrink-0">
      <img
        src={dark ? '/logo-white.png' : '/logo-dark.png'}
        alt="Nuvrail"
        className="h-7 w-auto"
      />
      <span className="font-display font-black text-fg tracking-tight text-base leading-none">
        NUVRAIL
      </span>
    </a>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <AuthGuard>
          <div className="min-h-screen bg-bg text-fg">
            {/* Top nav */}
            <header className="border-b border-edge bg-surface sticky top-0 z-10">
              <div className="max-w-4xl mx-auto px-4 py-2.5 flex items-center gap-1">
                <Logo />
                <nav className="flex gap-0.5 flex-1 overflow-x-auto">
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
                </nav>
                <ThemeToggle />
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
                <Route path="/login" element={<LoginView />} />
                <Route path="/account" element={<AccountView />} />
                <Route path="/reset-request" element={<ResetRequestView />} />
                <Route path="/reset" element={<ResetView />} />
                <Route path="/setup" element={<SetupView />} />
              </Routes>
            </main>
          </div>
        </AuthGuard>
      </HashRouter>
      <Toaster position="bottom-right" richColors />
    </QueryClientProvider>
  )
}
