/*
 * Path and name rules, shared by the screens and the mock backend so the two
 * can never disagree about what is acceptable. The real backend repeats every
 * check, because a client side check is a convenience and not a control.
 */

export const SESSION_NAME_MAX = 64
export const SESSION_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

export interface NameCheck {
  ok: boolean
  /** Normalised value to send to the server. */
  value: string
  /** Human readable reason, null when ok. */
  error: string | null
}

/** Validates a session folder name and returns the value to submit. */
export function checkSessionName(raw: string, existing: string[] = []): NameCheck {
  const value = raw.trim()

  if (value.length === 0) {
    return { ok: false, value, error: 'Enter a name for this session.' }
  }
  if (value.length > SESSION_NAME_MAX) {
    return {
      ok: false,
      value,
      error: `Keep the name under ${SESSION_NAME_MAX} characters.`,
    }
  }
  if (!SESSION_NAME_PATTERN.test(value)) {
    return {
      ok: false,
      value,
      error: 'Use letters, digits, dot, dash and underscore. Start with a letter or digit.',
    }
  }
  if (existing.some((name) => name === value)) {
    return { ok: false, value, error: `A session folder named ${value} already exists.` }
  }

  return { ok: true, value, error: null }
}

/**
 * Validates a single directory name, used by the folder picker when creating a
 * folder. The rules match session names on purpose, so a folder created here is
 * always a valid thing to record into.
 */
export function checkFolderName(raw: string): NameCheck {
  const value = raw.trim()

  if (value.length === 0) {
    return { ok: false, value, error: 'Enter a folder name.' }
  }
  if (value.length > SESSION_NAME_MAX) {
    return { ok: false, value, error: `Keep the name under ${SESSION_NAME_MAX} characters.` }
  }
  if (!SESSION_NAME_PATTERN.test(value)) {
    return {
      ok: false,
      value,
      error: 'Use letters, digits, dot, dash and underscore. Start with a letter or digit.',
    }
  }

  return { ok: true, value, error: null }
}

export interface PathCheck {
  ok: boolean
  value: string
  error: string | null
}

/** Validates a project folder path before it is sent to the host. */
export function checkProjectRoot(raw: string): PathCheck {
  const value = raw.trim()

  if (value.length === 0) {
    return { ok: false, value, error: 'Enter the folder where recordings should be stored.' }
  }
  if (!value.startsWith('/') && !value.startsWith('~')) {
    return { ok: false, value, error: 'Enter an absolute path, starting with / or ~.' }
  }
  if (value.split('/').includes('..')) {
    return { ok: false, value, error: 'The path cannot contain ..' }
  }
  if (value === '/' || value === '~') {
    return { ok: false, value, error: 'Pick a folder inside the home directory, not the root.' }
  }
  if (value.length > 512) {
    return { ok: false, value, error: 'That path is too long.' }
  }

  return { ok: true, value, error: null }
}

/**
 * Joins a project root and a session name into the folder a session will use.
 * Kept here so the preview in the dialog and the value the host builds come
 * from the same place.
 */
export function sessionDirFor(root: string, name: string): string {
  const base = root.endsWith('/') ? root.slice(0, -1) : root
  return `${base}/${name}`
}

/** Suggests a free session name, based on the date and what already exists. */
export function suggestSessionName(existing: string[], now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  const base = `session_${stamp}`

  if (!existing.includes(base)) {
    return base
  }
  for (let i = 2; i < 100; i += 1) {
    const candidate = `${base}_${i}`
    if (!existing.includes(candidate)) {
      return candidate
    }
  }
  return `${base}_${Date.now()}`
}

export interface Crumb {
  name: string
  path: string
}

/**
 * Splits an absolute path into clickable segments for the picker breadcrumb.
 * The leading slash becomes its own segment so the root stays reachable.
 */
export function breadcrumb(path: string): Crumb[] {
  if (!path || path === '/') {
    return [{ name: '/', path: '/' }]
  }

  const parts = path.split('/').filter(Boolean)
  const crumbs: Crumb[] = [{ name: '/', path: '/' }]
  let acc = ''
  for (const part of parts) {
    acc += `/${part}`
    crumbs.push({ name: part, path: acc })
  }
  return crumbs
}
