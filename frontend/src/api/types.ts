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
  /** Sidecar WAV, not a camera stream: kept out of `streams` on purpose. */
  audio: AudioArtifact | null
}

export type StageVerdict = 'passed' | 'failed'

/**
 * One take's audio. Null when the take recorded video only, which is a normal
 * outcome because audio is the sidecar rather than the dataset.
 */
export interface AudioArtifact {
  file: string
  device: string
  samplerate: number
  channels: number
  sample_format: string
  duration_s: number
  size_bytes: number
  /** True when the device failed mid take; the audio stops early but is kept. */
  partial: boolean
  overruns: number
  /** Peak level of the whole take, for spotting a silent recording after the fact. */
  peak_dbfs: number | null
  sync: {
    /** Positive when the audio starts later than the video. */
    offset_ms: number
    method: string
    estimated_error_ms: number
  }
}

/**
 * The operator's verdict on a take, written on the capture screen.
 *
 * `verdict` is null until the take is reviewed, and that is also what keeps
 * `advance` out of `allowed_actions`, so the button state needed no local rule.
 */
export interface StageReview {
  verdict: StageVerdict | null
  comment: string
  reviewed_at: string | null
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
  /** Cleared every time a new take is saved, so a verdict never outlives its bag. */
  review: StageReview
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
  audio: AudioStatus
  active_session: SessionSummary | null
}

/**
 * Whether the configured input can be opened. Present even when audio is
 * switched off, so the screens can say why rather than hiding the panel.
 */
export interface AudioStatus {
  enabled: boolean
  required: boolean
  available: boolean
  device: string | null
  /** Why it is unavailable, null when it is fine. */
  reason: string | null
  samplerate: number | null
  channels: number | null
}

/** Live view while a take runs, polled by the capture screen only. */
export interface AudioLive {
  enabled: boolean
  required: boolean
  available: boolean
  capturing: boolean
  device: string | null
  samplerate: number | null
  channels: number | null
  duration_s: number
  /** Peak of the most recent block, 0..1. Zero while the input is silent. */
  level: number
  /**
   * The same peak in dBFS, null while the block is silent. The meters draw from
   * this: a linear ratio parks ordinary speech near zero and looks like failure.
   */
  level_dbfs: number | null
  /** True once any block since the take started rose above the noise floor. */
  heard: boolean
  overruns: number
  error: string | null
  reason: string | null
}

/**
 * Live view of the home screen's input check, polled while it runs.
 *
 * `level_dbfs` is what the meter draws. Amplitude would leave normal speech
 * pinned near the bottom of the bar, because full scale is well above a
 * comfortable speaking level.
 */
export interface AudioTestLive {
  running: boolean
  device: string | null
  samplerate: number | null
  channels: number | null
  duration_s: number
  /** Peak of the most recent block, 0..1. */
  level: number
  /** The same peak in dBFS, null while the block is silent. */
  level_dbfs: number | null
  /** Loudest block since the check started. */
  peak: number
  peak_dbfs: number | null
  heard: boolean
  overruns: number
  error: string | null
}

export interface AudioTestResult {
  device: string
  samplerate: number
  channels: number
  duration_s: number
  overruns: number
  peak: number
  peak_dbfs: number | null
  /** `silent` means the device opened but delivered digital silence. */
  verdict: 'silent' | 'quiet' | 'ok' | 'clipping'
  /** Set when the device failed mid check, rather than the check hearing nothing. */
  error: string | null
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
  recording: {
    min_duration_s: number
    max_duration_s_default: number
    /**
     * On disk name of one take, from the service. It must end in .db3, which the
     * SDK enforces, so the screens that describe the output before a take exists
     * read it from here rather than hardcoding it.
     */
    output_name: string
    /** Derived from the configured streams, for the running size gauge. */
    bytes_per_second: number
  }
  stages: StageConfig[]
  audio: {
    enabled: boolean
    required: boolean
    samplerate: number
    channels: number
    /** On disk name of the sidecar, from the service. */
    output_name: string
  }
}

export interface GuideEntry {
  index: number
  /** Operator title when set, otherwise the stage name from the YAML. */
  name: string
  /** True when the title above came from the guides screen rather than the YAML. */
  name_custom: boolean
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

/**
 * Body of `PUT /api/guides/{index}`. Both fields are optional and independent:
 * only the ones present are applied, so editing one never rewrites the other. An
 * empty string clears that override, which is the reset action.
 */
export interface GuideUpdateBody {
  name?: string
  instructions?: string
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

/** Body of the review endpoint. The verdict is required, the comment is not. */
export interface StageReviewBody {
  verdict: StageVerdict
  comment?: string
}

/**
 * Carries the whole session so the client can replace its state in one step and
 * pick up the recomputed `allowed_actions`, which is what unlocks advance.
 */
export interface ReviewResult {
  session: Session
  stage_index: number
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
 * The surface the HTTP client exposes. Screens only ever talk to this
 * interface, never to fetch directly.
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
  /** Writes a stage's title and description. An empty value resets that field. */
  updateGuide(index: number, body: GuideUpdateBody): Promise<GuideUpdateResult>
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
  /** Marks a saved take passed or failed. Required before `advance`. */
  saveReview(sid: string, index: number, body: StageReviewBody): Promise<ReviewResult>
  advance(sid: string, index: number): Promise<AdvanceResult>

  /**
   * Whether the audio input can be opened. Cached on the host, so `refresh`
   * asks the device again after someone plugs a microphone in.
   */
  audioStatus(refresh?: boolean): Promise<AudioStatus>
  /** Live audio level, polled only while a take is running. */
  audioLive(): Promise<AudioLive>
  /** Opens the input and keeps it open so the meter can move. Nothing is written. */
  audioTestStart(): Promise<AudioTestLive>
  /** Live level of the running check. Polling it is also the heartbeat. */
  audioTestStatus(): Promise<AudioTestLive>
  /** Closes the input and reports what it heard while it was open. */
  audioTestStop(): Promise<AudioTestResult>
}
