/*
 * Path and name rules for the screens. The backend repeats every check, because a
 * client side check is a convenience and not a control; `app/paths.py` is the
 * authority and the shapes here mirror it.
 */

export const SESSION_NAME_MAX = 64
export const SESSION_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

/*
 * Host paths are either POSIX (`/Users/collector`) or Windows (`D:\capture`).
 * The capture host runs Windows, but the rules stay separator aware so a POSIX
 * path is still understood rather than silently mangled. This mirrors the
 * backend's `app/paths.py`.
 */

/** Windows drive prefix, for example `C:`. */
const DRIVE_PREFIX = /^([A-Za-z]:)/
/** UNC share, for example `\\host\share`. */
const UNC_PREFIX = /^\\\\[^\\/]+[\\/][^\\/]+/
const SEPARATORS = /[\\/]+/

/** True when the value is a Windows drive path such as `C:\` or `C:/`. */
export function isWindowsPath(value: string): boolean {
  return DRIVE_PREFIX.test(value)
}

/** True when the value is a UNC share such as `\\host\share`. */
export function isUncPath(value: string): boolean {
  return UNC_PREFIX.test(value)
}

/** Splits a path on either separator, dropping empty segments. */
export function splitSegments(value: string): string[] {
  return value.split(SEPARATORS).filter((part) => part.length > 0)
}

/** Separator the path is written with. Windows shapes use a backslash. */
export function separatorFor(value: string): string {
  return value.includes('\\') ? '\\' : '/'
}

/** Trailing separators removed, while a drive root like `C:\` stays intact. */
export function trimTrailingSeparator(value: string): string {
  if (/^[A-Za-z]:[\\/]?$/.test(value)) {
    return `${value[0].toUpperCase()}:\\`
  }
  const trimmed = value.replace(/[\\/]+$/, '')
  if (trimmed !== '') {
    return trimmed
  }
  return value.startsWith('/') ? '/' : ''
}

/** True when any segment is literally `..`. */
export function hasParentReference(value: string): boolean {
  return splitSegments(value).includes('..')
}

/**
 * True for a volume root or a bare top level folder, such as `/`, `C:\` or
 * `/Users`. These cannot serve as the project folder, matching the backend.
 */
export function isRootPath(value: string): boolean {
  if (value === '~' || value === '' || value === '/' || value === '\\') {
    return true
  }
  return splitSegments(value).length < 2
}

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

  const absolute =
    value.startsWith('/') || value.startsWith('~') || isWindowsPath(value) || isUncPath(value)
  if (!absolute) {
    return {
      ok: false,
      value,
      error: 'Enter an absolute path, such as C:\\capture or /capture.',
    }
  }
  if (hasParentReference(value)) {
    return { ok: false, value, error: 'The path cannot contain ..' }
  }
  if (isRootPath(value)) {
    return {
      ok: false,
      value,
      error: 'Pick a folder inside the drive or home directory, not the root itself.',
    }
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
  const base = trimTrailingSeparator(root)
  const separator = separatorFor(base)
  return `${base}${separator}${name}`
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
 * The volume root, `/` or `C:\`, becomes its own segment so it stays reachable.
 */
export function breadcrumb(path: string): Crumb[] {
  if (!path) {
    return [{ name: '/', path: '/' }]
  }

  const separator = separatorFor(path)
  const drive = /^([A-Za-z]:)[\\/]?/.exec(path)
  const anchor = drive ? `${drive[1].toUpperCase()}${separator}` : separator
  const rest = drive ? path.slice(drive[0].length) : path.replace(/^[\\/]+/, '')
  const parts = splitSegments(rest)
  const crumbs: Crumb[] = [{ name: anchor, path: anchor }]

  let acc = anchor
  for (const part of parts) {
    acc = acc.endsWith(separator) ? `${acc}${part}` : `${acc}${separator}${part}`
    crumbs.push({ name: part, path: acc })
  }
  return crumbs
}
