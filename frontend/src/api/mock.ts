/*
 * Mock backend.
 *
 * Exists so every screen, state transition and error path can be exercised
 * without a D435i or the FastAPI service. It implements the same CaptureApi
 * surface as the real client, including a synthetic camera feed drawn into a
 * canvas.
 *
 * This file is dead weight in production. vite.config.ts aliases the API module
 * to real.ts in a production build, so this file is never imported.
 */

import { ApiError } from './error'
import { checkFolderName, checkProjectRoot, checkSessionName, sessionDirFor } from '@/utils/paths'
import type {
  AdvanceResult,
  AppConfig,
  CaptureApi,
  DiscardRecordResult,
  FsCreateResult,
  FsEntry,
  FsListing,
  GuideBatchResult,
  GuideDeleteResult,
  GuideEntry,
  GuideUploadResult,
  GuidesResponse,
  Health,
  PreviewResult,
  ProjectInfo,
  Session,
  SessionCreateBody,
  SessionListResponse,
  SessionSummary,
  Stage,
  StageArtifact,
  StageState,
  StartRecordResult,
  StopRecordResult,
} from './types'

/* ------------------------------------------------------------ stage table */

interface StageSeed {
  name: string
  instructions: string
  /** Azimuth around the target, degrees. Zero is dead ahead. */
  azimuth: number
  /** Elevation above the target, degrees. Negative looks up from below. */
  elevation: number
  /** Distance from the target, centimetres. */
  distance: number
  maxDurationS: number
}

const STAGE_SEEDS: StageSeed[] = [
  {
    name: 'Front, level',
    instructions:
      'Hold the camera square to the subject at roughly 60 cm. Keep the subject centred and fill about two thirds of the frame. Hold still for the whole take.',
    azimuth: 0,
    elevation: 0,
    distance: 60,
    maxDurationS: 30,
  },
  {
    name: 'Front left, 45 degrees',
    instructions:
      'Orbit 45 degrees to the left, keeping height and distance unchanged. The subject should stay fully inside the frame.',
    azimuth: -45,
    elevation: 0,
    distance: 60,
    maxDurationS: 30,
  },
  {
    name: 'Front right, 45 degrees',
    instructions:
      'Orbit 45 degrees to the right from the starting position, keeping height and distance unchanged.',
    azimuth: 45,
    elevation: 0,
    distance: 60,
    maxDurationS: 30,
  },
  {
    name: 'Elevated, 30 degrees',
    instructions:
      'Raise the camera to look down on the subject by about 30 degrees. Keep the same 60 cm distance to the subject surface.',
    azimuth: 0,
    elevation: 30,
    distance: 60,
    maxDurationS: 30,
  },
  {
    name: 'Depressed, 30 degrees',
    instructions:
      'Lower the camera to look up at the subject by about 30 degrees. Watch for glare from overhead lighting and shade the lens if needed.',
    azimuth: 0,
    elevation: -30,
    distance: 60,
    maxDurationS: 30,
  },
  {
    name: 'Close range, 30 cm',
    instructions:
      'Move in to roughly 30 cm. This is close to the minimum working distance, so the frame will be tight. Avoid cropping the subject edges.',
    azimuth: 0,
    elevation: 0,
    distance: 30,
    maxDurationS: 30,
  },
  {
    name: 'Far range, 150 cm',
    instructions:
      'Withdraw to roughly 150 cm. Keep the subject centred and confirm the depth readout is still populated before you start.',
    azimuth: 0,
    elevation: 0,
    distance: 150,
    maxDurationS: 30,
  },
  {
    name: 'Orbit sweep',
    instructions:
      'While recording, orbit steadily around the subject through a full turn. Keep the subject centred and hold a constant angular speed. Aim for one complete revolution per take.',
    azimuth: 0,
    elevation: 0,
    distance: 60,
    maxDurationS: 30,
  },
]

const TOTAL_STAGES = STAGE_SEEDS.length
/** Global floor on take length, mirrored from the YAML default. */
const MIN_DURATION_S = 1

const MOCK_CONFIG: AppConfig = {
  app_title: 'D435i Capture',
  total_stages: TOTAL_STAGES,
  preview: { fps: 15, jpeg_quality: 80 },
  recording: { min_duration_s: MIN_DURATION_S, max_duration_s_default: 300 },
  stages: STAGE_SEEDS.map((seed, i) => ({
    index: i + 1,
    name: seed.name,
    instructions: seed.instructions,
    max_duration_s: seed.maxDurationS,
  })),
}

/* --------------------------------------------------------- guide diagrams */

/**
 * Draws a top-down pose diagram for a stage. Rendered as an inline SVG data URL
 * so the mock needs no binary assets.
 */
function stageDiagram(seed: StageSeed, index: number): string {
  const W = 1600
  const H = 1200
  const cx = W / 2
  const cy = H / 2 + 40

  const rad = (seed.azimuth * Math.PI) / 180
  const radius = 150 + seed.distance * 2.6
  const camX = cx + Math.sin(rad) * radius
  const camY = cy - Math.cos(rad) * radius

  const tilt = Math.max(-26, Math.min(26, seed.elevation * 0.6))
  const target = 190

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
  <defs>
    <pattern id="grid" width="80" height="80" patternUnits="userSpaceOnUse">
      <path d="M80 0H0V80" fill="none" stroke="#e4e7e9" stroke-width="1.5"/>
    </pattern>
    <marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="#14544e"/>
    </marker>
  </defs>

  <rect width="${W}" height="${H}" fill="#ffffff"/>
  <rect width="${W}" height="${H}" fill="url(#grid)"/>

  <circle cx="${cx}" cy="${cy}" r="520" fill="none" stroke="#eceff0" stroke-width="2"/>
  <circle cx="${cx}" cy="${cy}" r="350" fill="none" stroke="#eceff0" stroke-width="2"/>

  <line x1="${camX}" y1="${camY}" x2="${cx}" y2="${cy}"
        stroke="#14544e" stroke-width="2.5" stroke-dasharray="14 12" marker-end="url(#tip)" opacity="0.55"/>

  <g transform="translate(${cx} ${cy})">
    <rect x="${-target / 2}" y="${-target / 2}" width="${target}" height="${target}"
          fill="#f7f8f9" stroke="#14181b" stroke-width="2.5"/>
    <line x1="${-target / 2}" y1="0" x2="${target / 2}" y2="0" stroke="#c9cfd3" stroke-width="2"/>
    <line x1="0" y1="${-target / 2}" x2="0" y2="${target / 2}" stroke="#c9cfd3" stroke-width="2"/>
    <circle cx="0" cy="0" r="7" fill="#14181b"/>
  </g>

  <g transform="translate(${camX} ${camY}) rotate(${seed.azimuth + 180})">
    <rect x="-74" y="-46" width="148" height="92" rx="12"
          fill="#14181b"/>
    <rect x="-74" y="-46" width="148" height="30" rx="12" fill="#2c3237"/>
    <circle cx="-34" cy="4" r="15" fill="#0b0e10"/>
    <circle cx="8" cy="4" r="15" fill="#0b0e10"/>
    <circle cx="50" cy="4" r="8" fill="#14544e"/>
    <g transform="translate(0 -46) rotate(${tilt})">
      <path d="M-20 -14 L20 -14 L11 -40 L-11 -40 z" fill="#14544e" opacity="0.9"/>
    </g>
  </g>

  <g font-family="Instrument Sans, Helvetica, sans-serif" fill="#6a737b">
    <text x="72" y="120" font-size="30" letter-spacing="4" font-weight="600">STAGE ${String(index).padStart(2, '0')}</text>
    <text x="72" y="182" font-family="Instrument Serif, Georgia, serif" font-size="58" fill="#14181b">${seed.name}</text>
    <text x="72" y="${H - 92}" font-size="27" letter-spacing="3" font-weight="600">AZIMUTH</text>
    <text x="72" y="${H - 46}" font-family="IBM Plex Mono, monospace" font-size="38" fill="#14181b">${seed.azimuth > 0 ? '+' : ''}${seed.azimuth}\u00b0</text>
    <text x="420" y="${H - 92}" font-size="27" letter-spacing="3" font-weight="600">ELEVATION</text>
    <text x="420" y="${H - 46}" font-family="IBM Plex Mono, monospace" font-size="38" fill="#14181b">${seed.elevation > 0 ? '+' : ''}${seed.elevation}\u00b0</text>
    <text x="880" y="${H - 92}" font-size="27" letter-spacing="3" font-weight="600">DISTANCE</text>
    <text x="880" y="${H - 46}" font-family="IBM Plex Mono, monospace" font-size="38" fill="#14181b">${seed.distance} cm</text>
  </g>
  <rect x="1340" y="${H - 120}" width="140" height="6" rx="3" fill="#14544e" opacity="0.35"/>
</svg>`
}

/** Stand-in for a captured frame, used as the saved-stage thumbnail. */
function frameThumbnail(seed: StageSeed, index: number): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240" width="320" height="240">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1a2b2e"/>
      <stop offset="1" stop-color="#0d1517"/>
    </linearGradient>
  </defs>
  <rect width="320" height="240" fill="url(#g)"/>
  <circle cx="160" cy="126" r="54" fill="#2f4a44" opacity="0.85"/>
  <circle cx="160" cy="126" r="30" fill="#4d7a70" opacity="0.8"/>
  <path d="M0 194 Q90 158 160 186 T320 168 L320 240 L0 240 Z" fill="#24383a" opacity="0.9"/>
  <g font-family="Instrument Sans, Helvetica, sans-serif" fill="#8fb3ab" font-size="15" letter-spacing="2">
    <text x="16" y="28" font-weight="600">STAGE ${String(index).padStart(2, '0')}</text>
    <text x="16" y="228" font-family="IBM Plex Mono, monospace" font-size="13">${seed.distance} cm \u00b7 ${seed.azimuth > 0 ? '+' : ''}${seed.azimuth}\u00b0 \u00b7 ${seed.elevation > 0 ? '+' : ''}${seed.elevation}\u00b0</text>
  </g>
  <rect x="252" y="16" width="52" height="18" rx="9" fill="#b4441c"/>
  <text x="266" y="30" font-family="IBM Plex Mono, monospace" font-size="12" fill="#ffffff">REC</text>
</svg>`
}

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

function pseudoHash(seed: string): string {
  let h1 = 0x811c9dc5
  let h2 = 0x1000193
  for (let i = 0; i < seed.length; i += 1) {
    h1 = Math.imul(h1 ^ seed.charCodeAt(i), 0x01000193) >>> 0
    h2 = Math.imul(h2 + seed.charCodeAt(i) * (i + 7), 0x85ebca6b) >>> 0
  }
  return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')).repeat(2).slice(0, 64)
}

/* ------------------------------------------------------------------ store */

interface MockGuide {
  entry: GuideEntry
  imageUrl: string
}

interface MockSession {
  session: Session
  /** Wall clock ms when the current take started, null when idle. */
  recordStartedAt: number | null
  /** Timer that auto-stops a take that ran too long. */
  autoStopTimer: number | null
}

const state: {
  guides: Map<number, MockGuide>
  /** The configured project folder. Null until the operator sets one. */
  projectRoot: string | null
  session: MockSession | null
  /** Folders that exist on disk, kept separately from the live session. */
  existingDirs: string[]
  previewing: boolean
  /**
   * Directories that exist on the simulated host. Populated as a flat lookup so
   * the picker can walk a realistic tree, including folders the operator made.
   */
  dirs: Set<string>
} = {
  guides: new Map(),
  projectRoot: null,
  session: null,
  existingDirs: [],
  previewing: false,
  dirs: new Set(),
}

const MOCK_HOME = '/Users/collector'

/**
 * A plausible home directory, so the picker opens on something real rather than
 * an empty root.
 */
const SEED_DIRS = [
  '/',
  '/Users',
  MOCK_HOME,
  `${MOCK_HOME}/Documents`,
  `${MOCK_HOME}/Documents/Project_A`,
  `${MOCK_HOME}/Documents/Project_A/session_a`,
  // A session folder is recognised by the stage folders inside it, so the
  // seeded one carries them too.
  `${MOCK_HOME}/Documents/Project_A/session_a/stage_01`,
  `${MOCK_HOME}/Documents/Project_A/session_a/stage_02`,
  `${MOCK_HOME}/Documents/Project_B`,
  `${MOCK_HOME}/Documents/Field trial 04`,
  `${MOCK_HOME}/Desktop`,
  `${MOCK_HOME}/Downloads`,
  `${MOCK_HOME}/Movies`,
  `${MOCK_HOME}/.config`,
  '/Volumes',
  '/Volumes/Data',
  '/Volumes/Data/captures',
]

/**
 * Directories outside the allowed roots. Kept out of the seed tree on purpose,
 * so asking for them exercises PATH_NOT_ALLOWED rather than a missing folder.
 */
const OUT_OF_RANGE_DIRS = ['/', '/Users', '/System', '/Library']

function seedDirs(): void {
  state.dirs.clear()
  // Seed the in-range tree, then the out-of-range ones, so asking for either
  // exercises the intended branch rather than a missing directory.
  for (const dir of [...SEED_DIRS, ...OUT_OF_RANGE_DIRS]) {
    state.dirs.add(dir)
  }
}

seedDirs()

function seedGuides(): void {
  state.guides.clear()
  STAGE_SEEDS.forEach((seed, i) => {
    const index = i + 1
    const svg = stageDiagram(seed, index)
    state.guides.set(index, {
      imageUrl: svgUrl(svg),
      entry: {
        index,
        name: seed.name,
        configured: true,
        image_url: svgUrl(svg),
        original_filename: `stage_${String(index).padStart(2, '0')}.svg`,
        content_type: 'image/svg+xml',
        size_bytes: svg.length,
        width: 1600,
        height: 1200,
        uploaded_at: new Date(Date.now() - (TOTAL_STAGES - i) * 3600_000).toISOString(),
        sha256: pseudoHash(`stage-${index}`),
      },
    })
  })
}

seedGuides()

/* ----------------------------------------------------------------- helpers */

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

/** Simulated pipeline restart cost, so the blocking overlay is visible. */
const RECONFIGURE_MS = 900

function nowIso(): string {
  return new Date().toISOString()
}

function guidesSnapshot(): GuidesResponse {
  const guides: GuideEntry[] = STAGE_SEEDS.map((seed, i) => {
    const index = i + 1
    const found = state.guides.get(index)
    if (found) {
      return { ...found.entry }
    }
    return {
      index,
      name: seed.name,
      configured: false,
      image_url: null,
      original_filename: null,
      content_type: null,
      size_bytes: null,
      width: null,
      height: null,
      uploaded_at: null,
      sha256: null,
    }
  })

  const missing = guides.filter((g) => !g.configured).map((g) => g.index)

  return {
    required: true,
    ready: missing.length === 0,
    total: TOTAL_STAGES,
    uploaded: TOTAL_STAGES - missing.length,
    missing_indices: missing,
    guides,
  }
}

function allowedActionsFor(stageState: StageState) {
  switch (stageState) {
    case 'idle':
      return ['start'] as const
    case 'recording':
      return ['stop'] as const
    case 'saved':
      return ['discard', 'advance'] as const
  }
}

function makeStages(): Stage[] {
  return STAGE_SEEDS.map((seed, i) => {
    const stageState: StageState = 'idle'
    return {
      index: i + 1,
      name: seed.name,
      // Copied from the config snapshot, not read live. This is what the mock
      // backs its own limits with, so the two cannot drift.
      instructions: seed.instructions,
      min_duration_s: MIN_DURATION_S,
      max_duration_s: seed.maxDurationS,
      state: stageState,
      allowed_actions: [...allowedActionsFor(stageState)],
      recording_started_at: null,
      artifact: null,
    }
  })
}

function requireSession(sid: string): MockSession {
  if (!state.session || state.session.session.session_id !== sid) {
    throw new ApiError('SESSION_NOT_FOUND', 'That session no longer exists.', 404)
  }
  return state.session
}

/*
 * A real HTTP response is always fresh JSON, which is what makes the store's
 * reactive updates fire. Handing back the live object would mutate state behind
 * the proxy and Vue would never invalidate its computed values, so the mock
 * clones on every read.
 */
function cloneSession(ms: MockSession): Session {
  return structuredClone(ms.session)
}

function requireStage(ms: MockSession, index: number): Stage {
  const stage = ms.session.stages.find((s) => s.index === index)
  if (!stage) {
    throw new ApiError('STAGE_NOT_FOUND', `Stage ${index} is out of range.`, 404)
  }
  return stage
}

function runActionGuards(stage: Stage, action: 'start' | 'stop' | 'discard' | 'advance'): void {
  if (!stage.allowed_actions.includes(action)) {
    const map: Record<string, [string, string]> = {
      start: ['STAGE_ALREADY_SAVED', 'This stage is already saved. Use Re-record to take it again.'],
      stop: ['STAGE_NOT_RECORDING', 'This stage is not recording.'],
      discard: ['STAGE_NOT_SAVED', 'Nothing to discard, this stage has not been saved yet.'],
      advance: ['STAGE_NOT_SAVED', 'Save this stage before moving on.'],
    }
    const [code, message] = map[action]
    throw new ApiError(code, message, 409, { stage_index: stage.index })
  }
}

function syncStage(ms: MockSession, stage: Stage): void {
  stage.allowed_actions = [...allowedActionsFor(stage.state)]
  void ms
  // current_stage is deliberately left alone here. It only moves through
  // advance, so saving a stage never jumps the collector forward on its own.
}

function clearAutoStop(ms: MockSession): void {
  if (ms.autoStopTimer !== null) {
    window.clearTimeout(ms.autoStopTimer)
    ms.autoStopTimer = null
  }
}

/* -------------------------------------------------------------- project */

/** Deterministic free space figure, so the same folder always reads the same. */
function freeSpaceFor(root: string): number {
  let h = 0
  for (let i = 0; i < root.length; i += 1) {
    h = (Math.imul(h, 31) + root.charCodeAt(i)) >>> 0
  }
  return 40 + (h % 460) / 2
}

/** Paths that always look read only, so the failure path is demonstrable. */
const READ_ONLY_PREFIXES = ['/System', '/Library', '/private', '/usr', '/bin', '/sbin']

/**
 * Roots the picker is allowed to browse. Mirrors the fs.allow_roots config, and
 * exists to stop a project folder being aimed at a system directory rather than
 * as a security boundary.
 */
const ALLOWED_ROOTS = [MOCK_HOME, '/Volumes', '/media', '/mnt']

function isReadOnly(path: string): boolean {
  return READ_ONLY_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`))
}

function allowedRootFor(path: string): string | null {
  let best: string | null = null
  for (const root of ALLOWED_ROOTS) {
    if (path === root || path.startsWith(`${root}/`)) {
      if (best === null || root.length > best.length) {
        best = root
      }
    }
  }
  // The configured project folder is always treated as in range, so tightening
  // the list never locks a working setup out.
  if (best === null && state.projectRoot) {
    if (path === state.projectRoot || path.startsWith(`${state.projectRoot}/`)) {
      best = state.projectRoot
    }
  }
  return best
}

function isAllowed(path: string): boolean {
  return allowedRootFor(path) !== null
}

/** Splits a path into normalised segments, resolving . and .. textually. */
function normalise(path: string): string {
  if (path === '/') {
    return '/'
  }
  const out: string[] = []
  for (const part of path.split('/')) {
    if (!part || part === '.') {
      continue
    }
    if (part === '..') {
      out.pop()
      continue
    }
    out.push(part)
  }
  return `/${out.join('/')}`
}

function parentOf(path: string): string | null {
  if (path === '/' || !path) {
    return null
  }
  const parts = path.split('/').filter(Boolean)
  parts.pop()
  return parts.length === 0 ? '/' : `/${parts.join('/')}`
}

function joinPath(parent: string, name: string): string {
  const base = parent.endsWith('/') ? parent.slice(0, -1) : parent
  return base === '' ? `/${name}` : `${base}/${name}`
}

/** Immediate subdirectory names of a path, used for the name collision check. */
function foldersUnder(path: string): string[] {
  const names: string[] = []
  for (const dir of state.dirs) {
    if (parentOf(dir) !== path) {
      continue
    }
    const name = dir.split('/').pop()
    if (name && !name.startsWith('.')) {
      names.push(name)
    }
  }
  return names
}

/**
 * Builds a directory listing by looking for anything in the simulated tree
 * that sits directly under the requested path.
 */
function fsListingOf(rawPath: string | null, showHidden: boolean): FsListing {
  const path = normalise(rawPath ?? MOCK_HOME)

  if (!isAllowed(path)) {
    throw new ApiError('PATH_NOT_ALLOWED', 'That folder is outside the allowed browse range.', 403, {
      path,
    })
  }

  const exists = state.dirs.has(path)

  const children: FsEntry[] = []
  if (exists) {
    for (const dir of state.dirs) {
      if (parentOf(dir) !== path) {
        continue
      }
      const name = dir.split('/').pop() ?? dir
      if (!showHidden && name.startsWith('.')) {
        continue
      }
      children.push({
        name,
        path: dir,
        writable: !isReadOnly(dir),
        is_symlink: false,
        looks_like_session: state.dirs.has(`${dir}/stage_01`) || dir === state.session?.session.data_dir,      })
    }
  }

  children.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))

  const writable = exists && !isReadOnly(path)
  const free = freeSpaceFor(path)
  const enough = free >= 5

  let error: string | null = null
  if (!exists) {
    error = 'That folder does not exist on the host.'
  } else if (!writable) {
    error = 'That folder is not writable by the capture service.'
  } else if (!enough) {
    error = `Only ${free.toFixed(1)} GB free. At least 5 GB is needed.`
  }

  // Reaching the edge of the allowed range reads as the top of the tree, so the
  // Up button disables exactly as it does at the filesystem root.
  const rawParent = parentOf(path)
  const parent = rawParent !== null && isAllowed(rawParent) ? rawParent : null

  return {
    path,
    parent,
    name: path === '/' ? '/' : (path.split('/').pop() ?? path),
    home: MOCK_HOME,
    shortcuts: [
      { name: 'Home', path: MOCK_HOME },
      { name: 'Documents', path: `${MOCK_HOME}/Documents` },
      { name: 'Desktop', path: `${MOCK_HOME}/Desktop` },
      { name: 'Volumes', path: '/Volumes' },
    ],
    readable: exists,
    writable,
    free_gb: free,
    enough,
    error,
    entries: children,
  }
}

function projectSnapshot(): ProjectInfo {
  const root = state.projectRoot

  if (!root) {
    return {
      configured: false,
      root: null,
      name: null,
      exists: false,
      writable: false,
      free_gb: 0,
      enough: false,
      error: 'No project folder is set. Recordings have nowhere to go.',
    }
  }

  const segments = root.split('/').filter(Boolean)
  const name = segments[segments.length - 1] ?? root
  const writable = !isReadOnly(root)
  const free = freeSpaceFor(root)
  const enough = free >= 5

  let error: string | null = null
  if (!writable) {
    error = 'That folder is not writable by the capture service.'
  } else if (!enough) {
    error = `Only ${free.toFixed(1)} GB free. At least 5 GB is needed.`
  }

  return {
    configured: true,
    root,
    name,
    exists: true,
    writable,
    free_gb: free,
    enough,
    error,
  }
}

function requireUsableProject(): ProjectInfo {
  const project = projectSnapshot()
  if (!project.configured) {
    throw new ApiError('PROJECT_NOT_CONFIGURED', 'Set a project folder before starting.', 409)
  }
  if (!project.writable) {
    throw new ApiError('PROJECT_PATH_NOT_WRITABLE', project.error ?? 'Folder not writable.', 403, {
      root: project.root,
    })
  }
  if (!project.enough) {
    throw new ApiError('DISK_SPACE_LOW', project.error ?? 'Not enough free space.', 507, {
      free_gb: project.free_gb,
    })
  }
  return project
}

function summaryOf(session: Session): SessionSummary {
  const saved = session.stages.filter((s) => s.artifact)
  return {
    session_id: session.session_id,
    name: session.name,
    created_at: session.created_at,
    finished_at: session.finished_at,
    status: session.status,
    current_stage: session.current_stage,
    saved_count: saved.length,
    size_bytes: saved.reduce((sum, s) => sum + (s.artifact?.size_bytes ?? 0), 0),
    data_dir: session.data_dir,
  }
}

/* -------------------------------------------------------------- mock feed */

/**
 * Paints a synthetic depth-style feed. Dark, cool, slightly noisy, with a
 * couple of objects drifting so it is obvious the preview is live.
 */
export function attachMockPreview(canvas: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    return () => {}
  }

  let frame = 0
  let raf = 0
  let last = 0

  const resize = () => {
    const rect = canvas.getBoundingClientRect()
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = Math.max(320, Math.round(rect.width * dpr))
    const h = Math.max(180, Math.round(rect.height * dpr))
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w
      canvas.height = h
    }
  }

  const draw = (ts: number) => {
    raf = window.requestAnimationFrame(draw)
    if (ts - last < 1000 / 15) {
      return
    }
    last = ts
    frame += 1

    resize()
    const { width: w, height: h } = canvas
    const t = frame / 15

    const bg = ctx.createLinearGradient(0, 0, 0, h)
    bg.addColorStop(0, '#16262a')
    bg.addColorStop(0.55, '#0f1b1e')
    bg.addColorStop(1, '#0a1214')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, w, h)

    // Drifting subject, brightness doubling as a rough depth cue
    const cx = w / 2 + Math.sin(t * 0.45) * w * 0.05
    const cy = h * 0.52 + Math.cos(t * 0.33) * h * 0.03
    const r = Math.min(w, h) * 0.17

    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 1.9)
    glow.addColorStop(0, 'rgba(126, 189, 176, 0.5)')
    glow.addColorStop(1, 'rgba(126, 189, 176, 0)')
    ctx.fillStyle = glow
    ctx.fillRect(0, 0, w, h)

    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, Math.PI * 2)
    ctx.fillStyle = '#3f5f5a'
    ctx.fill()
    ctx.beginPath()
    ctx.arc(cx, cy, r * 0.6, 0, Math.PI * 2)
    ctx.fillStyle = '#6f9a90'
    ctx.fill()

    // Secondary object drifting the other way
    const ox = w * 0.2 + Math.sin(t * 0.7) * w * 0.03
    const oy = h * 0.72
    ctx.fillStyle = '#2a4245'
    ctx.fillRect(ox - w * 0.09, oy, w * 0.18, h * 0.16)

    // Ground plane
    ctx.beginPath()
    ctx.moveTo(0, h * 0.78)
    ctx.quadraticCurveTo(w * 0.5, h * 0.71, w, h * 0.76)
    ctx.lineTo(w, h)
    ctx.lineTo(0, h)
    ctx.closePath()
    ctx.fillStyle = 'rgba(58, 88, 92, 0.5)'
    ctx.fill()

    // Sensor speckle
    ctx.fillStyle = 'rgba(160, 200, 195, 0.09)'
    for (let i = 0; i < 460; i += 1) {
      const x = (Math.sin(i * 12.9898 + frame * 0.7) * 43758.5453) % 1
      const y = (Math.sin(i * 78.233 + frame * 0.31) * 12345.6789) % 1
      ctx.fillRect(Math.abs(x) * w, Math.abs(y) * h, 2, 2)
    }

    // Slow scan sweep
    const sweep = ((frame * 6) % (h + 120)) - 60
    const sw = ctx.createLinearGradient(0, sweep - 40, 0, sweep + 40)
    sw.addColorStop(0, 'rgba(180, 230, 220, 0)')
    sw.addColorStop(0.5, 'rgba(180, 230, 220, 0.06)')
    sw.addColorStop(1, 'rgba(180, 230, 220, 0)')
    ctx.fillStyle = sw
    ctx.fillRect(0, sweep - 40, w, 80)

    // Corner brackets, reads as a targeting reticle
    const m = Math.round(Math.min(w, h) * 0.06)
    const len = Math.round(Math.min(w, h) * 0.09)
    ctx.strokeStyle = 'rgba(190, 232, 224, 0.35)'
    ctx.lineWidth = 2
    const corners: [number, number, number, number][] = [
      [m, m, 1, 1],
      [w - m, m, -1, 1],
      [m, h - m, 1, -1],
      [w - m, h - m, -1, -1],
    ]
    for (const [x, y, sx, sy] of corners) {
      ctx.beginPath()
      ctx.moveTo(x, y + sy * len)
      ctx.lineTo(x, y)
      ctx.lineTo(x + sx * len, y)
      ctx.stroke()
    }
  }

  raf = window.requestAnimationFrame(draw)
  return () => window.cancelAnimationFrame(raf)
}

/* -------------------------------------------------------------------- api */

export const backendApi: CaptureApi = {
  async health(): Promise<Health> {
    await delay(220)
    const g = guidesSnapshot()
    const project = projectSnapshot()

    return {
      status: 'ok',
      device: {
        connected: true,
        name: 'Intel RealSense D435i',
        serial: '0123456789',
        firmware: '5.13.0.50',
        usb_type: '3.2',
        reason: null,
      },
      project,
      guides: {
        ready: g.ready,
        uploaded: g.uploaded,
        total: g.total,
        missing_indices: g.missing_indices,
      },
      active_session:
        state.session && state.session.session.status === 'in_progress'
          ? summaryOf(state.session.session)
          : null,
    }
  },

  async config(): Promise<AppConfig> {
    await delay(120)
    return MOCK_CONFIG
  },

  async project(): Promise<ProjectInfo> {
    await delay(140)
    return projectSnapshot()
  },

  async updateProject(root: string): Promise<ProjectInfo> {
    await delay(420)

    const check = checkProjectRoot(root)
    if (!check.ok) {
      throw new ApiError('PROJECT_PATH_INVALID', check.error ?? 'Invalid path.', 400, { root })
    }

    // A tilde is expanded by the service, so the resolved path is echoed back.
    const resolved = check.value.startsWith('~')
      ? `${MOCK_HOME}${check.value.slice(1)}`
      : check.value
    const target = normalise(resolved)

    const previous = state.projectRoot
    // Picking an existing folder means the service adopts it. A missing one is
    // created, which mirrors what the real service does.
    state.dirs.add(target)
    state.projectRoot = target

    const snapshot = projectSnapshot()
    if (!snapshot.writable || !snapshot.enough) {
      // Reject and roll back, so a bad path never becomes the stored value.
      state.projectRoot = previous
      throw new ApiError(
        snapshot.writable ? 'DISK_SPACE_LOW' : 'PROJECT_PATH_NOT_WRITABLE',
        snapshot.error ?? 'That folder cannot be used.',
        snapshot.writable ? 507 : 403,
        { root: target },
      )
    }

    if (previous !== state.projectRoot) {
      // Recordings from another project are not in this one, so the name
      // collision list is rebuilt from the folders that are actually here.
      state.session = null
      state.existingDirs = foldersUnder(state.projectRoot)
    }

    return snapshot
  },

  async listDirectory(path: string | null, showHidden = false): Promise<FsListing> {
    await delay(200)
    return fsListingOf(path, showHidden)
  },

  async createDirectory(parent: string, name: string): Promise<FsCreateResult> {
    await delay(360)

    const check = checkFolderName(name)
    if (!check.ok) {
      throw new ApiError('DIR_NAME_INVALID', check.error ?? 'Invalid folder name.', 400, {
        name,
      })
    }

    const base = normalise(parent)
    if (!state.dirs.has(base)) {
      throw new ApiError('DIR_NOT_FOUND', 'The parent folder does not exist.', 404, {
        parent: base,
      })
    }
    if (isReadOnly(base)) {
      throw new ApiError('PROJECT_PATH_NOT_WRITABLE', 'That folder is not writable.', 403, {
        parent: base,
      })
    }

    const target = joinPath(base, check.value)
    if (state.dirs.has(target)) {
      throw new ApiError('DIR_EXISTS', `A folder named ${check.value} already exists.`, 409, {
        path: target,
      })
    }

    state.dirs.add(target)
    return { path: target, listing: fsListingOf(base, false) }
  },

  async guides(): Promise<GuidesResponse> {
    await delay(180)
    return guidesSnapshot()
  },

  async uploadGuide(index: number, file: File): Promise<GuideUploadResult> {
    await delay(420)

    if (index < 1 || index > TOTAL_STAGES) {
      throw new ApiError('STAGE_NOT_FOUND', `Stage ${index} is out of range.`, 404)
    }
    if (!file.type.startsWith('image/')) {
      throw new ApiError('GUIDE_UNSUPPORTED_TYPE', 'Only PNG and JPEG images are accepted.', 415, {
        stage_index: index,
        received: file.type || 'unknown',
      })
    }
    if (file.size > 10 * 1024 * 1024) {
      throw new ApiError('GUIDE_TOO_LARGE', 'That file is larger than 10 MB.', 413, {
        stage_index: index,
        size_bytes: file.size,
      })
    }

    const imageUrl = await readAsDataUrl(file)
    const dimensions = await measureImage(imageUrl)
    const seed = STAGE_SEEDS[index - 1]

    const entry: GuideEntry = {
      index,
      name: seed.name,
      configured: true,
      image_url: imageUrl,
      original_filename: file.name,
      content_type: file.type,
      size_bytes: file.size,
      width: dimensions.width,
      height: dimensions.height,
      uploaded_at: nowIso(),
      sha256: pseudoHash(`${file.name}:${file.size}`),
    }
    state.guides.set(index, { entry, imageUrl })

    return { ...entry, generated_preview: dimensions.width > 1600, ready: guidesSnapshot().ready }
  },

  async uploadGuidesBatch(files: Map<number, File>): Promise<GuideBatchResult> {
    await delay(620)
    const applied: number[] = []
    const failed: GuideBatchResult['failed'] = []

    for (const [index, file] of files) {
      try {
        await this.uploadGuide(index, file)
        applied.push(index)
      } catch (error) {
        if (error instanceof ApiError) {
          failed.push({ index, code: error.code, message: error.message })
        } else {
          failed.push({ index, code: 'GUIDE_INVALID_IMAGE', message: 'Could not read that image.' })
        }
      }
    }

    return { applied, failed, guides: guidesSnapshot() }
  },

  async deleteGuide(index: number): Promise<GuideDeleteResult> {
    await delay(240)
    if (!state.guides.has(index)) {
      throw new ApiError('GUIDE_NOT_FOUND', 'No diagram has been uploaded for this stage.', 404)
    }
    state.guides.delete(index)
    const g = guidesSnapshot()
    return { index, configured: false, ready: g.ready, uploaded: g.uploaded }
  },

  guideImageUrl(index, sha256, size = 'display') {
    const found = state.guides.get(index)
    void sha256
    void size
    return found ? found.imageUrl : ''
  },

  async listSessions(): Promise<SessionListResponse> {
    await delay(180)
    const sessions: SessionSummary[] = []
    if (state.session) {
      sessions.push(summaryOf(state.session.session))
    }
    // Folders that exist on disk but have no live session are reported with
    // just enough detail for the name collision check.
    for (const name of state.existingDirs) {
      if (sessions.some((s) => s.name === name)) {
        continue
      }
      sessions.push({
        session_id: name,
        name,
        created_at: nowIso(),
        finished_at: null,
        status: 'finished',
        current_stage: TOTAL_STAGES,
        saved_count: 0,
        size_bytes: 0,
        data_dir: state.projectRoot ? sessionDirFor(state.projectRoot, name) : name,
      })
    }

    return { project_root: state.projectRoot, sessions }
  },

  async createSession(body: SessionCreateBody): Promise<Session> {
    await delay(320)

    const project = requireUsableProject()

    if (state.session && state.session.session.status === 'in_progress') {
      throw new ApiError('SESSION_ACTIVE_EXISTS', 'A session is already in progress.', 409, {
        session_id: state.session.session.session_id,
      })
    }

    const g = guidesSnapshot()
    if (g.required && !g.ready) {
      throw new ApiError('GUIDES_INCOMPLETE', 'Stage diagrams are not fully configured.', 409, {
        missing_indices: g.missing_indices,
      })
    }

    const nameCheck = checkSessionName(body.name ?? '', state.existingDirs)
    if (!nameCheck.ok) {
      const code = (body.name ?? '').trim().length === 0 ? 'SESSION_NAME_REQUIRED' : 'SESSION_NAME_INVALID'
      throw new ApiError(code, nameCheck.error ?? 'Invalid session name.', 400, { name: body.name })
    }

    const root = project.root as string
    const dataDir = sessionDirFor(root, nameCheck.value)

    if (state.existingDirs.includes(nameCheck.value)) {
      throw new ApiError(
        'SESSION_DIR_EXISTS',
        `A folder named ${nameCheck.value} already exists in the project.`,
        409,
        { name: nameCheck.value, data_dir: dataDir },
      )
    }

    const stamp = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    const sid = `${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-${pad(
      stamp.getHours(),
    )}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}-m0ck`

    const session: Session = {
      session_id: sid,
      name: nameCheck.value,
      created_at: nowIso(),
      finished_at: null,
      status: 'in_progress',
      current_stage: 1,
      data_dir: dataDir,
      operator: body.operator ?? '',
      note: body.note ?? '',
      stages: makeStages(),
    }

    state.session = { session, recordStartedAt: null, autoStopTimer: null }
    state.existingDirs.push(nameCheck.value)
    // The session folder is real on disk, so the picker should show it.
    state.dirs.add(dataDir)
    return cloneSession(state.session)
  },

  async getSession(sid: string): Promise<Session> {
    await delay(140)
    return cloneSession(requireSession(sid))
  },

  async discardSession(sid: string) {
    await delay(300)
    const ms = requireSession(sid)
    const recording = ms.session.stages.find((s) => s.state === 'recording')
    if (recording) {
      throw new ApiError(
        'STAGE_ALREADY_RECORDING',
        'Stop the current take before discarding the session.',
        409,
        { stage_index: recording.index },
      )
    }
    const removed = ms.session.data_dir
    clearAutoStop(ms)
    // Discarding removes the folder from disk, so the name becomes free again.
    state.existingDirs = state.existingDirs.filter((n) => n !== ms.session.name)
    state.dirs.delete(removed)
    state.session = null
    state.previewing = false
    return { session_id: sid, deleted: true, removed_dir: removed }
  },

  async startPreview(): Promise<PreviewResult> {
    await delay(200)
    state.previewing = true
    return { streaming: true, stream_url: '/api/preview/stream' }
  },

  async stopPreview(): Promise<PreviewResult> {
    await delay(200)
    const autoSaved = Boolean(state.session?.session.stages.some((s) => s.state === 'recording'))
    if (autoSaved && state.session) {
      const stage = state.session.session.stages.find((s) => s.state === 'recording')
      if (stage) {
        await this.stopRecord(state.session.session.session_id, stage.index)
      }
    }
    state.previewing = false
    return { streaming: false, stream_url: '', auto_saved: autoSaved }
  },

  previewStreamUrl: () => '/api/preview/stream',

  async startRecord(sid: string, index: number): Promise<StartRecordResult> {
    const ms = requireSession(sid)
    const stage = requireStage(ms, index)
    runActionGuards(stage, 'start')

    await delay(RECONFIGURE_MS)

    const seed = STAGE_SEEDS[index - 1]
    stage.state = 'recording'
    stage.recording_started_at = nowIso()
    syncStage(ms, stage)

    ms.recordStartedAt = Date.now()
    clearAutoStop(ms)
    ms.autoStopTimer = window.setTimeout(() => {
      void backendApi.stopRecord(sid, index).catch(() => undefined)
    }, seed.maxDurationS * 1000)

    return {
      stage_index: index,
      state: 'recording',
      started_at: stage.recording_started_at,
      bag_abs_path: `${ms.session.data_dir}/stage_${String(index).padStart(2, '0')}/capture.bag`,
      auto_stop_at_s: seed.maxDurationS,
    }
  },

  async stopRecord(sid: string, index: number): Promise<StopRecordResult> {
    const ms = requireSession(sid)
    const stage = requireStage(ms, index)
    // Idempotent, so a retry after a lost response reports success rather than
    // telling the collector the take failed when it actually saved fine.
    if (stage.state === 'saved' && stage.artifact) {
      return { stage_index: index, state: 'saved', artifact: structuredClone(stage.artifact) }
    }
    requireStartRecorded(stage)

    await delay(RECONFIGURE_MS)
    clearAutoStop(ms)

    const seed = STAGE_SEEDS[index - 1]
    const elapsedMs = ms.recordStartedAt ? Date.now() - ms.recordStartedAt : 0
    ms.recordStartedAt = null
    const duration = Math.max(0.2, elapsedMs / 1000)

    // A take shorter than the minimum is thrown away and the stage returns to idle.
    if (duration < 1) {
      stage.state = 'idle'
      stage.recording_started_at = null
      stage.artifact = null
      syncStage(ms, stage)
      throw new ApiError(
        'RECORDING_TOO_SHORT',
        'That take was under one second, so it was discarded. Record for longer.',
        400,
        { stage_index: index, duration_s: round1(duration) },
      )
    }

    const depthFrames = Math.round(duration * 30)
    const artifact: StageArtifact = {
      bag_path: `stage_${String(index).padStart(2, '0')}/capture.bag`,
      size_bytes: Math.round(duration * 17.4 * 1024 * 1024),
      duration_s: round1(duration),
      started_at: new Date(Date.now() - elapsedMs).toISOString(),
      stopped_at: nowIso(),
      streams: ['depth', 'color', 'infrared_1', 'infrared_2', 'accel', 'gyro'],
      frame_counts: {
        depth: depthFrames,
        color: depthFrames,
        infrared_1: depthFrames,
        infrared_2: depthFrames,
        accel: Math.round(duration * 63),
        gyro: Math.round(duration * 200),
      },
      // The mock has no files on disk, so the thumbnail is a generated frame.
      thumbnail_url: svgUrl(frameThumbnail(seed, index)),
    }

    stage.state = 'saved'
    stage.recording_started_at = null
    stage.artifact = artifact
    syncStage(ms, stage)

    return { stage_index: index, state: 'saved', artifact: structuredClone(artifact) }
  },

  async discardRecord(sid: string, index: number): Promise<DiscardRecordResult> {
    const ms = requireSession(sid)
    const stage = requireStage(ms, index)

    // Also idempotent. Discarding something already discarded is not an error.
    if (stage.state === 'idle') {
      return { stage_index: index, state: 'idle', deleted: [] }
    }
    runActionGuards(stage, 'discard')

    await delay(260)

    stage.state = 'idle'
    stage.recording_started_at = null
    stage.artifact = null
    ms.recordStartedAt = null
    syncStage(ms, stage)

    return { stage_index: index, state: 'idle', deleted: ['capture.bag', 'meta.json', 'thumb.jpg'] }
  },

  async advance(sid: string, index: number): Promise<AdvanceResult> {
    const ms = requireSession(sid)
    const stage = requireStage(ms, index)
    runActionGuards(stage, 'advance')

    // Advancing out of turn would rewind the session, so it is rejected.
    if (index !== ms.session.current_stage) {
      throw new ApiError(
        'STAGE_NOT_CURRENT',
        'Only the current stage can be advanced.',
        409,
        { stage_index: index, current_stage: ms.session.current_stage },
      )
    }

    await delay(180)

    if (index >= TOTAL_STAGES) {
      ms.session.status = 'finished'
      ms.session.finished_at = nowIso()
      return { session: cloneSession(ms), next: { type: 'finish' } }
    }

    ms.session.current_stage = index + 1
    return { session: cloneSession(ms), next: { type: 'guide', stage_index: index + 1 } }
  },

  attachMockPreview,
}

function requireStartRecorded(stage: Stage): void {
  if (stage.state !== 'recording') {
    throw new ApiError('STAGE_NOT_RECORDING', 'This stage is not recording.', 409, {
      stage_index: stage.index,
    })
  }
}

function round1(value: number): number {
  return Math.round(value * 10) / 10
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

function measureImage(src: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight })
    img.onerror = () => resolve({ width: 0, height: 0 })
    img.src = src
  })
}
