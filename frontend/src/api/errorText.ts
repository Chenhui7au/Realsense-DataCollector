import type { ApiError } from '@/api'

/**
 * Backend error codes carry stable meaning, so the UI can explain what to do
 * next rather than echoing the raw message. Anything unmapped falls back to the
 * backend message.
 */
const EXPLANATIONS: Record<string, string> = {
  DEVICE_NOT_FOUND: 'The D435i is not detected. Check the USB cable and re-seat it.',
  DEVICE_BUSY: 'Another process is holding the camera. Close realsense-viewer and retry.',
  SESSION_NOT_FOUND: 'That session no longer exists. Start a new one from the home screen.',
  SESSION_ACTIVE_EXISTS: 'A session is already running. Resume it or discard it first.',
  SESSION_FINISHED: 'This session is finished. Start a new one.',
  STAGE_NOT_FOUND: 'That stage number is out of range.',
  STAGE_ALREADY_RECORDING: 'This stage is already recording.',
  STAGE_ALREADY_SAVED: 'This stage is already saved. Use Re-record to take it again.',
  STAGE_NOT_SAVED: 'Save this stage before moving on.',
  STAGE_NOT_RECORDING: 'This stage is not recording, so there is nothing to stop.',
  RECORDING_TOO_SHORT: 'That take was too short and has been discarded. Record for longer.',
  DISK_SPACE_LOW: 'Not enough free disk space to record. Clear some space on the host.',
  CAMERA_ERROR: 'The camera reported an error. Check the connection and try again.',
  PROJECT_NOT_CONFIGURED: 'Set a project folder on the home screen before starting a session.',
  PROJECT_PATH_INVALID: 'That project folder path is not valid. Use an absolute path.',
  PROJECT_PATH_NOT_WRITABLE: 'The service cannot write to that folder. Pick another one.',
  SESSION_NAME_REQUIRED: 'Enter a name for this session.',
  SESSION_NAME_INVALID:
    'Use letters, digits, dot, dash and underscore, starting with a letter or digit.',
  SESSION_DIR_EXISTS: 'A session folder with that name already exists in the project.',
  GUIDES_INCOMPLETE: 'Some stage diagrams are missing. Add them before starting a session.',
  GUIDE_NOT_FOUND: 'No diagram has been uploaded for this stage.',
  GUIDE_INVALID_IMAGE: 'That file could not be read as an image.',
  GUIDE_TOO_LARGE: 'That file is over the size limit.',
  GUIDE_UNSUPPORTED_TYPE: 'Only PNG and JPEG images are accepted.',
  REQUEST_INVALID: 'The service rejected the request. Check the values and try again.',
  NOT_IMPLEMENTED:
    'This part of the service is not built yet. It needs the camera or the session state machine.',
  REQUEST_TIMEOUT: 'The service did not respond in time. It may be busy restarting the camera.',
  NETWORK_ERROR: 'Cannot reach the capture service. Check that the backend is running.',
  UNEXPECTED_RESPONSE: 'The service returned something unexpected. Check the backend log.',
}

export function describeError(error: unknown): { code: string; message: string } {
  if (typeof error === 'object' && error !== null && 'code' in error && 'message' in error) {
    const code = String((error as ApiError).code)
    return { code, message: EXPLANATIONS[code] ?? String((error as ApiError).message) }
  }
  if (error instanceof Error) {
    return { code: 'UNKNOWN', message: error.message }
  }
  return { code: 'UNKNOWN', message: 'Something went wrong.' }
}
