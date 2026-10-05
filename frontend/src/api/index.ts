import { backendApi } from '@api-backend'
import type { CaptureApi } from './types'

/*
 * Exactly one backend is compiled in. vite.config.ts aliases @api-backend to
 * either the mock or the real HTTP client, so the other never reaches the
 * bundle.
 */
export const api: CaptureApi = backendApi

export const isMock = __USE_MOCK__

if (isMock) {
  // Loud on purpose. Nobody should mistake a mock run for real hardware.
  console.info(
    '%cD435i Capture is running against the mock backend.%c\n' +
      'No camera and no recorder. Set VITE_USE_MOCK=false in .env.development to talk to the real service.',
    'background:#14544e;color:#fff;padding:2px 6px;border-radius:3px;font-weight:600',
    'color:inherit',
  )
}

export * from './types'
export { ApiError } from './error'
