/**
 * SetupView signup-mode gating tests (GH #139).
 *
 * The web app fetches GET /api/v1/config before login and renders the account
 * step accordingly:
 *   closed  -> NO submittable signup form; a "signups are closed" notice
 *   invite  -> the form plus a required invite-code field
 *   open    -> the normal form, no invite field
 *
 * The server-side 403 stays the source of truth; this is UX only. We mock
 * getConfig so no network is involved, and assert the rendered surface.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { DeploymentConfig } from '../api/client'

// Mock the client module: getConfig is what SetupView reads on mount.
const getConfig = vi.fn<() => Promise<DeploymentConfig>>()
vi.mock('../api/client', () => ({
  getConfig: () => getConfig(),
  registerUser: vi.fn(),
  setToken: vi.fn(),
  startGmailOAuth: vi.fn(),
  createAgent: vi.fn(),
}))

// Imported after the mock so the mocked client is used.
import SetupView from './SetupView'

function renderSetup() {
  return render(
    <MemoryRouter>
      <SetupView />
    </MemoryRouter>,
  )
}

describe('SetupView signup gating', () => {
  beforeEach(() => {
    getConfig.mockReset()
  })

  it('closed mode hides the signup form and shows an unavailable notice', async () => {
    getConfig.mockResolvedValue({ signup_mode: 'closed' })
    renderSetup()

    await waitFor(() => {
      expect(screen.getByText(/signups are closed/i)).toBeInTheDocument()
    })
    // No submittable account form: the "Create account" submit button is absent.
    expect(screen.queryByRole('button', { name: /create account/i })).not.toBeInTheDocument()
    // Points the user at their administrator rather than dead-ending on submit.
    expect(screen.getByText(/contact your administrator/i)).toBeInTheDocument()
  })

  it('invite mode shows the form with a required invite-code field', async () => {
    getConfig.mockResolvedValue({ signup_mode: 'invite' })
    renderSetup()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument()
    })
    expect(screen.getByText(/invite-only/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/invite code/i)).toBeInTheDocument()
  })

  it('open mode shows the normal form with no invite-code field', async () => {
    getConfig.mockResolvedValue({ signup_mode: 'open' })
    renderSetup()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/invite code/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signups are closed/i)).not.toBeInTheDocument()
  })
})
