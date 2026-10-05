/*
 * Wire types. These mirror docs/API.md one for one. When the contract changes,
 * change API.md first, then this file, then the screens.
 */

export type StageState = 'idle' | 'recording' | 'saved'

export type StageAction = 'start' | 'stop' | 'discard' | 'advance'

export type SessionStatus = 'in_progress' | 'finished'

export interface StageArtifact {
  bag_path: string
  size_bytes: number
  duration_s: number
  started_at: string
  stopped_at: string
  streams: string[]
  frame_counts: Record<string, number>
  thumbnail_url: string
}

export interface Stage {
  index: number
  name: string
  /**
   * Frozen from the session's config snapshot, so this is what the backend is
   * actually enforcing. Do not read the live config for a running session.
   */
  instructions: string
  /** Takes under this are discarded. */
  min_duration_s: number
  /** Takes over this are stopped and saved automatically. */
  max_duration_s: number
  state: StageState
  allowed_actions: StageAction[]
  /**
   * Set while a take is running, null otherwise. Lets a refreshed page rebuild
   * the elapsed timer instead of restarting it from zero.
   */
  recording_started_at: string | null
  artifact: StageArtifact | null
}

export interface Session {
  session_id: string
  /** Operator supplied folder name. This is what appears on disk. */
  name: string
  created_at: string
  finished_at: string | null
  status: SessionStatus
  current_stage: number
  /** Absolute path of the session folder, always inside the project folder. */
  data_dir: string
  operator: string
  note: string
  stages: Stage[]
}

/** Compact form of a session, used where the full stage list is not needed. */
export interface SessionSummary {
  session_id: string
  name: string
  created_at: string
  finished_at: string | null
  status: SessionStatus
  current_stage: number
  saved_count: number
  size_bytes: number
  data_dir: string
}

export interface DeviceInfo {
  connected: boolean
  name: string | null
  serial: string | null
  firmware: string | null
  usb_type: string | null
  reason: string | null
}

/**
 * Where recordings are written. Set from the home screen and persisted on the
 * host, so every session lands in the same project folder until it is changed.
 */
export interface ProjectInfo {
  configured: boolean
  /** Absolute path of the project folder, null until configured. */
  root: string | null
  /** Basename of root, for display only. */
  name: string | null
  exists: boolean
  writable: boolean
  free_gb: number
  enough: boolean
  /** Why the folder is unusable, null when it is fine. */
  error: string | null
}

export interface GuidesReadiness {
  ready: boolean
  uploaded: number
  total: number
  missing_indices: number[]
}

export interface Health {
  status: 'ok' | 'degraded'
  device: DeviceInfo
  project: ProjectInfo
  guides: GuidesReadiness
  active_session: SessionSummary | null
}

export interface StageConfig {
  index: number
  name: string
  instructions: string
  max_duration_s: number
}

export interface AppConfig {
  app_title: string
  total_stages: number
  preview: { fps: number; jpeg_quality: number }
  /** Only used before a session exists. A running session carries its own. */
  recording: { min_duration_s: number; max_duration_s_default: number }
  stages: StageConfig[]
}

export interface GuideEntry {
  index: number
  name: string
  configured: boolean
  image_url: string | null
  original_filename: string | null
  content_type: string | null
  size_bytes: number | null
  width: number | null
  height: number | null
  uploaded_at: string | null
  sha256: string | null
  /** Operator wording when set, otherwise the stage instructions from the YAML. */
  instructions: string
  /** True when the text above came from the guides screen rather than the YAML. */
  instructions_custom: boolean
}

export interface GuidesResponse {
  required: boolean
  ready: boolean
  total: number
  uploaded: number
  missing_indices: number[]
  guides: GuideEntry[]
}

export interface GuideUploadResult extends GuideEntry {
  generated_preview: boolean
  ready: boolean
}

export interface GuideUpdateResult extends GuideEntry {
  ready: boolean
  uploaded: number
}

export interface GuideUploadFailure {
  index: number
  code: string
  message: string
}

export interface GuideBatchResult {
  applied: number[]
  failed: GuideUploadFailure[]
  guides: GuidesResponse
}

export interface GuideDeleteResult {
  index: number
  configured: boolean
  ready: boolean
  uploaded: number
}

export interface StartRecordResult {
  stage_index: number
  state: StageState
  started_at: string
  /** Absolute path, unlike StageArtifact.bag_path which is session relative. */
  bag_abs_path: string
  auto_stop_at_s: number
}

export interface StopRecordResult {
  stage_index: number
  state: StageState
  artifact: StageArtifact
}

export interface DiscardRecordResult {
  stage_index: number
  state: StageState
  deleted: string[]
}

export interface AdvanceResult {
  session: Session
  next: { type: 'guide' | 'finish'; stage_index?: number }
}

export interface PreviewResult {
  streaming: boolean
  stream_url: string
  auto_saved?: boolean
}

export interface SessionCreateBody {
  /** Folder name for this session, relative to the project root. */
  name: string
  operator?: string
  note?: string
}

export interface ProjectUpdateBody {
  root: string
}

/** One subdirectory in a listing. Files are never returned. */
export interface FsEntry {
  name: string
  path: string
  writable: boolean
  /** True for a directory reached through a symlink. */
  is_symlink: boolean
  /** True when the directory already holds a session.json. */
  looks_like_session: boolean
}

/** A jump point offered in the picker sidebar. */
export interface FsShortcut {
  name: string
  path: string
}

/**
 * Result of browsing one directory on the host. This is what lets the picker
 * show real folders, since a browser cannot read a path from the OS dialog and
 * would in any case be looking at the wrong machine.
 */
export interface FsListing {
  path: string
  /** null when path is the filesystem root. */
  parent: string | null
  name: string
  home: string
  shortcuts: FsShortcut[]
  readable: boolean
  writable: boolean
  free_gb: number
  enough: boolean
  /** Why the directory cannot be used as a project folder, null when it can. */
  error: string | null
  entries: FsEntry[]
}

export interface FsCreateResult {
  path: string
  listing: FsListing
}

export interface SessionListResponse {
  project_root: string | null
  sessions: SessionSummary[]
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: Record<string, unknown>
  }
}

/*
 * The surface both the real client and the mock implement. Screens only ever
 * talk to this interface, so switching backends is a build time concern.
 */
export interface CaptureApi {
  health(): Promise<Health>
  config(): Promise<AppConfig>

  /** Reads the configured project folder and its usability. */
  project(): Promise<ProjectInfo>
  /** Sets the project folder. Server validates and persists it. */
  updateProject(root: string): Promise<ProjectInfo>

  /**
   * Browses one directory on the host. Passing null starts at the service home
   * directory, which is the only sensible default before anything is set.
   */
  listDirectory(path: string | null, showHidden?: boolean): Promise<FsListing>
  /** Creates a directory and returns a listing of its parent. */
  createDirectory(parent: string, name: string): Promise<FsCreateResult>

  guides(): Promise<GuidesResponse>
  uploadGuide(index: number, file: File): Promise<GuideUploadResult>
  uploadGuidesBatch(files: Map<number, File>): Promise<GuideBatchResult>
  /** Writes the description that goes with a stage. An empty value resets it. */
  saveGuideInstructions(index: number, instructions: string): Promise<GuideUpdateResult>
  deleteGuide(index: number): Promise<GuideDeleteResult>
  guideImageUrl(index: number, sha256: string | null, size?: 'display' | 'original'): string

  /** Sessions already present in the project folder, for name collision checks. */
  listSessions(): Promise<SessionListResponse>

  createSession(body: SessionCreateBody): Promise<Session>
  getSession(sid: string): Promise<Session>
  discardSession(sid: string): Promise<{ session_id: string; deleted: boolean; removed_dir: string }>

  startPreview(sid: string): Promise<PreviewResult>
  stopPreview(sid: string): Promise<PreviewResult>
  previewStreamUrl(): string

  startRecord(sid: string, index: number, note?: string): Promise<StartRecordResult>
  stopRecord(sid: string, index: number): Promise<StopRecordResult>
  discardRecord(sid: string, index: number): Promise<DiscardRecordResult>
  advance(sid: string, index: number): Promise<AdvanceResult>

  /**
   * Mock only. Drives a synthetic feed into a canvas so the capture screen is
   * demonstrable without hardware. Real backend renders the MJPEG stream into
   * an img element instead.
   */
  attachMockPreview?(canvas: HTMLCanvasElement): () => void
}
