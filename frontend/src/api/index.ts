import { backendApi } from './real'
import type { CaptureApi } from './types'

/**
 * The one backend the interface talks to: the HTTP client in `real.ts`. In
 * development it reaches the capture service through the Vite proxy, and in the
 * served build it is same origin, so both paths exercise the same client.
 */
export const api: CaptureApi = backendApi

export * from './types'
export { ApiError } from './error'
