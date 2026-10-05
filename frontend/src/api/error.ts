import type { ApiErrorBody } from './types'

/**
 * Error carrying the backend's stable machine readable code, so screens can
 * branch on `code` while showing `message` verbatim.
 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly detail: Record<string, unknown> | undefined

  constructor(code: string, message: string, status: number, detail?: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
  }

  /** True when the failure is a plain network or transport problem. */
  get isTransport(): boolean {
    return this.status === 0
  }
}

const BASE = '/api'

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === 'object' &&
    value !== null &&
    'error' in value &&
    typeof (value as ApiErrorBody).error === 'object'
  )
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (isErrorBody(body)) {
    const { code, message, detail } = body.error
    return new ApiError(code, message, response.status, detail)
  }

  return new ApiError(
    'UNEXPECTED_RESPONSE',
    `Request failed with status ${response.status}`,
    response.status,
  )
}

/**
 * Single entry point for every call. Turns the backend error envelope into an
 * ApiError and unwraps the JSON body for callers.
 */
export async function request<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 20000, ...rest } = init
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { ...rest, signal: controller.signal })
  } catch (error) {
    window.clearTimeout(timer)
    const timedOut = error instanceof DOMException && error.name === 'AbortError'
    throw new ApiError(
      timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
      timedOut
        ? 'The service did not respond in time.'
        : 'Cannot reach the capture service. Check that the backend is running.',
      0,
    )
  }
  window.clearTimeout(timer)

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export function jsonBody(value: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value ?? {}),
  }
}
