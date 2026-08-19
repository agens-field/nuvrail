// Vitest global setup: register jest-dom matchers (toBeInTheDocument, etc.)
// and clean up the DOM between tests.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
