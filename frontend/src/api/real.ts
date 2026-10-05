import { request, jsonBody, ApiError } from './error'
import type {
  AdvanceResult,
  AppConfig,
  CaptureApi,
  DiscardRecordResult,
  FsCreateResult,
  FsListing,
  GuideBatchResult,
  GuideDeleteResult,
  GuideUpdateResult,
  GuideUploadResult,
  GuidesResponse,
  Health,
  PreviewResult,
  ProjectInfo,
  Session,
  SessionCreateBody,
  SessionListResponse,
  StartRecordResult,
  StopRecordResult,
} from './types'

/** Talks to the FastAPI service defined in docs/API.md. */
export const backendApi: CaptureApi = {
  health: () => request<Health>('/health'),

  config: () => request<AppConfig>('/config'),

  project: () => request<ProjectInfo>('/project'),

  updateProject: (root) =>
    request<ProjectInfo>('/project', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root }),
    }),

  listDirectory: (path, showHidden = false) => {
    const query = new URLSearchParams()
    if (path) {
      query.set('path', path)
    }
    if (showHidden) {
      query.set('show_hidden', 'true')
    }
    const suffix = query.toString()
    return request<FsListing>(`/fs/list${suffix ? `?${suffix}` : ''}`)
  },

  createDirectory: (parent, name) =>
    request<FsCreateResult>('/fs/mkdir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent, name }),
    }),

  guides: () => request<GuidesResponse>('/guides'),

  uploadGuide: (index, file) => {
    const form = new FormData()
    form.append('file', file)
    return request<GuideUploadResult>(`/guides/${index}`, { method: 'POST', body: form })
  },

  uploadGuidesBatch: (files) => {
    const form = new FormData()
    for (const [index, file] of files) {
      form.append(`stage_${index}`, file)
    }
    return request<GuideBatchResult>('/guides/batch', { method: 'POST', body: form })
  },

  saveGuideInstructions: (index, instructions) =>
    request<GuideUpdateResult>(`/guides/${index}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instructions }),
    }),

  deleteGuide: (index) => request<GuideDeleteResult>(`/guides/${index}`, { method: 'DELETE' }),

  guideImageUrl: (index, sha256, size = 'display') => {
    const version = sha256 ? `&v=${sha256.slice(0, 8)}` : ''
    return `/api/guides/${index}/image?size=${size}${version}`
  },

  createSession: (body: SessionCreateBody) => request<Session>('/sessions', jsonBody(body)),

  listSessions: () => request<SessionListResponse>('/sessions'),

  getSession: (sid) => request<Session>(`/sessions/${sid}`),

  discardSession: (sid) =>
    request<{ session_id: string; deleted: boolean; removed_dir: string }>(`/sessions/${sid}`, {
      method: 'DELETE',
    }),

  startPreview: (sid) => request<PreviewResult>(`/sessions/${sid}/preview/start`, jsonBody({})),

  stopPreview: (sid) => request<PreviewResult>(`/sessions/${sid}/preview/stop`, jsonBody({})),

  previewStreamUrl: () => '/api/preview/stream',

  startRecord: (sid, index, note) =>
    request<StartRecordResult>(`/sessions/${sid}/stages/${index}/record/start`, jsonBody({ note })),

  stopRecord: (sid, index) =>
    request<StopRecordResult>(`/sessions/${sid}/stages/${index}/record/stop`, jsonBody({})),

  discardRecord: (sid, index) =>
    request<DiscardRecordResult>(`/sessions/${sid}/stages/${index}/record/discard`, jsonBody({})),

  advance: (sid, index) =>
    request<AdvanceResult>(`/sessions/${sid}/stages/${index}/advance`, jsonBody({})),
}

export { ApiError }
