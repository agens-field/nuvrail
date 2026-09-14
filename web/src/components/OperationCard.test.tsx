/**
 * OperationCard "Read full message" tests (GH #152).
 *
 * The approver must be able to READ the full outgoing SMTP body before hitting
 * Approve. The pending-list payload only carries body_preview (kept lean), so
 * the card:
 *   - renders body_preview inline (collapsed) without any network call, and
 *   - fetches the single op on demand (GET /api/v1/operations/{id}) when the
 *     approver clicks "Read full message", then renders the complete body.
 *
 * We mock the api client so no network is involved and assert the rendered
 * surface + that the on-demand fetch fires exactly once and is cached.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Operation } from '../types'

// Mock the client module. OperationCard reads approve/reject (unused here) and
// fetchOperation (the on-demand full-body fetch we exercise).
const fetchOperation = vi.fn<(id: string) => Promise<Operation>>()
vi.mock('../api/client', () => ({
  approveOperation: vi.fn(),
  rejectOperation: vi.fn(),
  fetchOperation: (id: string) => fetchOperation(id),
}))

// Imported after the mock so the mocked client is used.
import OperationCard from './OperationCard'

const FULL_BODY =
  'Hi there,\n\nThis is the complete outgoing message body.\nSecond line for good measure.\n\nRegards,\nThe Agent'

// A pending SMTP send op as it arrives in the list payload: body_preview only,
// no full body (that lives behind the single-op fetch).
function smtpOp(overrides: Partial<Operation> = {}): Operation {
  return {
    id: 'op-152',
    status: 'pending',
    op_type: 'smtp_send',
    protocol: 'smtp',
    description: 'Send email to alice@example.com',
    created_at: Math.floor(Date.now() / 1000) - 60,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    smtp_envelope: {
      from: 'me@example.com',
      to: ['alice@example.com'],
      subject: 'Quarterly update',
      body_preview: 'Hi there, this is the complete outgoing message',
    },
    ...overrides,
  }
}

function renderCard(op: Operation) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <OperationCard operation={op} />
    </QueryClientProvider>,
  )
}

describe('OperationCard — read full SMTP body (GH #152)', () => {
  beforeEach(() => {
    fetchOperation.mockReset()
  })

  it('renders the body_preview inline without fetching the full op', () => {
    renderCard(smtpOp())

    // Preview is visible immediately.
    expect(
      screen.getByText(/this is the complete outgoing message/i),
    ).toBeInTheDocument()
    // The expand control exists but the full body is NOT fetched or shown yet.
    expect(
      screen.getByRole('button', { name: /read full message/i }),
    ).toBeInTheDocument()
    expect(fetchOperation).not.toHaveBeenCalled()
    expect(screen.queryByText(/second line for good measure/i)).not.toBeInTheDocument()
  })

  it('fetches the single op on expand and renders the full body', async () => {
    const user = userEvent.setup()
    fetchOperation.mockResolvedValue(
      smtpOp({ smtp_envelope: { ...smtpOp().smtp_envelope!, body: FULL_BODY } }),
    )
    renderCard(smtpOp())

    await user.click(screen.getByRole('button', { name: /read full message/i }))

    // On-demand fetch fired for exactly this op, and the full body renders.
    await waitFor(() => {
      expect(screen.getByText(/second line for good measure/i)).toBeInTheDocument()
    })
    expect(fetchOperation).toHaveBeenCalledTimes(1)
    expect(fetchOperation).toHaveBeenCalledWith('op-152')

    // Control flips to a collapse affordance.
    expect(
      screen.getByRole('button', { name: /hide full message/i }),
    ).toBeInTheDocument()
  })

  it('caches the fetched body: collapse + re-expand does not refetch', async () => {
    const user = userEvent.setup()
    fetchOperation.mockResolvedValue(
      smtpOp({ smtp_envelope: { ...smtpOp().smtp_envelope!, body: FULL_BODY } }),
    )
    renderCard(smtpOp())

    const trigger = () =>
      screen.getByRole('button', { name: /read full message|hide full message/i })

    await user.click(trigger()) // expand -> fetch
    await waitFor(() =>
      expect(screen.getByText(/second line for good measure/i)).toBeInTheDocument(),
    )
    await user.click(trigger()) // collapse
    await waitFor(() =>
      expect(
        screen.queryByText(/second line for good measure/i),
      ).not.toBeInTheDocument(),
    )
    await user.click(trigger()) // re-expand -> from cache, no refetch
    await waitFor(() =>
      expect(screen.getByText(/second line for good measure/i)).toBeInTheDocument(),
    )

    expect(fetchOperation).toHaveBeenCalledTimes(1)
  })

  it('renders the full body as plain text (no HTML injection)', async () => {
    const user = userEvent.setup()
    const evil = 'plain <script>alert(1)</script> & <b>not bold</b>'
    fetchOperation.mockResolvedValue(
      smtpOp({ smtp_envelope: { ...smtpOp().smtp_envelope!, body: evil } }),
    )
    renderCard(smtpOp())

    await user.click(screen.getByRole('button', { name: /read full message/i }))

    // The literal text (angle brackets and all) is rendered; no <script>/<b>
    // element is created from the agent-supplied content.
    await waitFor(() => {
      expect(screen.getByText(/not bold/i).textContent).toContain('<b>not bold</b>')
    })
    expect(document.querySelector('script')).toBeNull()
    // No real <b> element was injected from the body content.
    expect(document.querySelector('pre b')).toBeNull()
  })
})

describe('OperationCard — prefers decoded body_rendered (GH #154)', () => {
  beforeEach(() => {
    fetchOperation.mockReset()
  })

  const RAW_B64 = 'SGVsbG8gcmV2aWV3ZXIsIHRoaXMgd2FzIGJhc2U2NC1lbmNvZGVkLg=='
  const DECODED = 'Hello reviewer, this was base64-encoded.'

  it('shows body_rendered (decoded) rather than the raw base64 body on expand', async () => {
    const user = userEvent.setup()
    // The single-op fetch returns BOTH the raw (encoded) body and the decoded
    // rendering; the card must display the readable one.
    fetchOperation.mockResolvedValue(
      smtpOp({
        smtp_envelope: {
          ...smtpOp().smtp_envelope!,
          body: RAW_B64,
          body_rendered: DECODED,
        },
      }),
    )
    renderCard(smtpOp())

    await user.click(screen.getByRole('button', { name: /read full message/i }))

    await waitFor(() => {
      expect(screen.getByText(/this was base64-encoded/i)).toBeInTheDocument()
    })
    // The raw base64 blob must NOT be what the reviewer sees.
    expect(screen.queryByText(RAW_B64)).not.toBeInTheDocument()
  })

  it('falls back to the raw body when body_rendered is absent (legacy op)', async () => {
    const user = userEvent.setup()
    const legacyBody = 'Legacy plaintext body with no rendered field.'
    fetchOperation.mockResolvedValue(
      smtpOp({
        smtp_envelope: { ...smtpOp().smtp_envelope!, body: legacyBody },
      }),
    )
    renderCard(smtpOp())

    await user.click(screen.getByRole('button', { name: /read full message/i }))

    await waitFor(() => {
      expect(screen.getByText(/legacy plaintext body/i)).toBeInTheDocument()
    })
  })
})
