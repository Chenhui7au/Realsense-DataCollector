import type { Session } from '@/api'

/**
 * Where the collector belongs, derived only from backend state.
 *
 * Used both by the route guard and by the home screen after resuming a session,
 * so there is exactly one definition of the flow order.
 */
export function locationForSession(session: Session) {
  if (session.status === 'finished') {
    return { name: 'finish' as const }
  }

  const stage = session.stages.find((s) => s.index === session.current_stage)
  if (stage?.state === 'recording') {
    return { name: 'capture' as const, params: { index: String(stage.index) } }
  }

  return { name: 'guide' as const, params: { index: String(session.current_stage) } }
}
