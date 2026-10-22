<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import type { AudioLive } from '@/api'
import AppButton from '@/components/AppButton.vue'
import CaptureControls from '@/components/CaptureControls.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PreviewPane from '@/components/PreviewPane.vue'
import StageReview from '@/components/StageReview.vue'
import StatePill from '@/components/StatePill.vue'
import { useApp } from '@/composables/useApp'
import { useCountdown } from '@/composables/useCountdown'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { describeError } from '@/api/errorText'
import { locationForSession } from '@/utils/flow'
import { formatBytes, formatClock, formatSeconds, formatDateTime, levelFromDbfs, LEVEL_FLOOR_DB } from '@/utils/format'

const props = defineProps<{ index: number }>()

const router = useRouter()
const toast = useToast()
const { config, totalStages, refreshHealth } = useApp()

const {
  session,
  stageAt,
  isFinished,
  busyAction,
  run,
  startPolling,
  stopPolling,
  fetch,
  setSession,
} = useSession()

const previewStatus = ref<'off' | 'starting' | 'live'>('off')
const previewBusy = ref(false)
const discardOpen = ref(false)
const homeConfirmOpen = ref(false)
const streamUrl = api.previewStreamUrl()
const previewPane = ref<InstanceType<typeof PreviewPane> | null>(null)
const reviewRef = ref<InstanceType<typeof StageReview> | null>(null)
const { elapsed, remaining, start: startClock, resumeFrom, reset: resetClock } = useCountdown()

/*
 * Live audio state, polled while a take runs. Two rates on purpose: a fast poll
 * for the level meter, which has to move with the voice to be believable, and the
 * same call feeding the banner below. Access logging is already suppressed for
 * these routes, so the extra requests cost nothing but a few microseconds of I/O.
 */
const AUDIO_POLL_MS = 120
const audioLive = ref<AudioLive | null>(null)
/** Peak hold in dBFS, falling slowly, so a short word stays visible. */
const audioHoldDb = ref(LEVEL_FLOOR_DB)
let audioTimer: number | null = null
/** Guards against piling up requests if one poll is slow to answer. */
let audioInFlight = false

async function pollAudio(): Promise<void> {
  if (audioInFlight) {
    return
  }
  audioInFlight = true
  try {
    const live = await api.audioLive()
    audioLive.value = live
    audioHoldDb.value = Math.max(live.level_dbfs ?? LEVEL_FLOOR_DB, audioHoldDb.value - 2)
  } catch {
    // A failed poll must not disturb the screen. The banner simply keeps its
    // previous state until the next tick.
  } finally {
    audioInFlight = false
  }
}

function startAudioPoll(): void {
  stopAudioPoll()
  audioHoldDb.value = LEVEL_FLOOR_DB
  void pollAudio()
  audioTimer = window.setInterval(() => void pollAudio(), AUDIO_POLL_MS)
}

function stopAudioPoll(): void {
  if (audioTimer !== null) {
    window.clearInterval(audioTimer)
    audioTimer = null
  }
}

/*
 * The meter beside REC. Same 0..100 reading as the home screen's input check, from
 * the same helper, so the two surfaces cannot disagree about what "level 40" means.
 */
const audioLevel = computed(() => levelFromDbfs(audioLive.value?.level_dbfs))
const audioHoldLevel = computed(() => levelFromDbfs(audioHoldDb.value))
const audioClipping = computed(() => (audioLive.value?.level_dbfs ?? LEVEL_FLOOR_DB) >= -1)
/** What the meter says in words, for the rail and for readers of the HUD. */
const audioMeterText = computed(() => {
  const live = audioLive.value
  if (audioStopping.value) return 'closing'
  if (!live) return 'starting'
  if (live.error) return 'stopped'
  if (!live.capturing) return 'no audio'
  // The number, always, because a number that moves is the only thing that
  // actually shows the microphone is working. The old wording replaced it with
  // "no sound yet" whenever the device had not been heard, and since that test
  // was set too high the rail sat on those three words for whole takes while the
  // WAV was fine.
  const level = `level ${audioLevel.value}`
  if (!live.heard) return `${level} · silent`
  return level
})

const stage = computed(() => stageAt(props.index))
const stageConfig = computed(() => config.value?.stages.find((s) => s.index === props.index) ?? null)

/*
 * How much disk a running take is using, and the name of the file it lands in.
 * Both come from the service, which owns the recording streams and the .db3 rule.
 * The gauge used to multiply elapsed time by a hand measured constant here, which
 * was four times too low, so it understated the one number it exists to show.
 */
const bytesPerSecond = computed(() => config.value?.recording.bytes_per_second || null)
const outputName = computed(() => config.value?.recording.output_name ?? '')
/** Used only when the service could not derive a rate, so the gauge still shows. */
const FALLBACK_BYTES_PER_S = 60 * 1024 * 1024
/** Mirrors review.comment_max_length in the shipped YAML. */
const REVIEW_COMMENT_MAX_LENGTH = 500

/*
 * Limits come from the session's frozen stage object when there is one. Reading
 * the live config instead would let the countdown disagree with the backend's
 * own auto-stop if the YAML changed mid-session.
 */
const minDuration = computed(
  () => stage.value?.min_duration_s ?? config.value?.recording.min_duration_s ?? 1,
)
const maxDuration = computed(
  () => stage.value?.max_duration_s ?? stageConfig.value?.max_duration_s ?? null,
)
const isLastStage = computed(() => props.index >= totalStages.value)
const recording = computed(() => stage.value?.state === 'recording')
const artifact = computed(() => stage.value?.artifact ?? null)

/**
 * Whether the HUD shows a meter at all. Only while a take is running and audio
 * capture is switched on.
 *
 * Deliberately not gated on the first poll reporting `capturing`: that arrives a
 * beat after the take starts, and a meter that pops into existence makes the
 * collector wonder what just changed. It is dimmed until audio is confirmed
 * instead, so the rail and the HUD agree from the first frame.
 */
const audioMeterVisible = computed(
  () => recording.value && Boolean(config.value?.audio.enabled),
)
/** True once the service reports the microphone is actually being written. */
const audioMeterActive = computed(() => audioLive.value?.capturing === true)
/**
 * A stop is in flight. The audio worker closes before the video pipeline does, so
 * for a moment `capturing` is false while the stage still reads `recording`. That
 * is the stop working, not a failure, and it must not flash a red warning now
 * that the meter polls eight times a second instead of once.
 */
const audioStopping = computed(() => busyAction.value === 'stop')
/**
 * Nothing has reached the microphone. Waits three seconds, the same grace the
 * banner below gives, so the first moment of a normal take does not turn the rail
 * amber before the collector has said anything.
 */
const audioSilent = computed(() => {
  const live = audioLive.value
  if (!live || !live.capturing || audioStopping.value) return false
  return !live.heard && live.duration_s >= 3
})

/**
 * The one line about audio that must not be missed.
 *
 * A message that disappears is not enough here: the dangerous case is a
 * microphone that opened and delivers silence, which every other view reports as
 * success. So this stays on screen for the whole take and, for a saved take,
 * keeps saying the file has no audio.
 */
const audioNotice = computed<{ tone: 'warn' | 'danger'; text: string } | null>(() => {
  const live = audioLive.value

  if (recording.value) {
    if (!config.value?.audio.enabled) {
      return null
    }
    // The audio worker closes before the video pipeline, so during a stop the
    // take briefly reports no capture. Warning about it would cry wolf on every
    // single take.
    if (audioStopping.value) {
      return null
    }
    if (!live) {
      return null
    }
    if (live.error) {
      return { tone: 'danger', text: `Audio stopped: ${live.error}` }
    }
    if (!live.capturing) {
      return {
        tone: 'danger',
        text: 'Recording video without audio. This take will have no sound.',
      }
    }
    // Three seconds of take time before complaining, so the first moment of a
    // normal take does not flash a warning at the collector.
    if (!live.heard && live.duration_s >= 3) {
      return {
        tone: 'warn',
        text: 'The microphone is open but delivering silence. Check the input device and the Windows microphone privacy setting.',
      }
    }
    return null
  }

  if (artifact.value) {
    const audio = artifact.value.audio
    if (!audio) {
      return { tone: 'warn', text: 'This take has no audio. The bag file is complete.' }
    }
    if (audio.partial) {
      return {
        tone: 'danger',
        text: `Audio ended early at ${formatSeconds(audio.duration_s)}. The bag file is complete.`,
      }
    }
    if (audio.overruns > 0) {
      return {
        tone: 'warn',
        text: `The microphone dropped ${audio.overruns} buffer${audio.overruns === 1 ? '' : 's'}; the audio runs slightly short of the video.`,
      }
    }
  }

  return null
})

const position = computed(
  () => `Stage ${String(props.index).padStart(2, '0')} / ${String(totalStages.value).padStart(2, '0')}`,
)

/** Rough figure so the collector can watch disk usage while a take runs. */
const estimatedBytes = computed(() =>
  Math.round(elapsed.value * (bytesPerSecond.value ?? FALLBACK_BYTES_PER_S)),
)

const overlayVisible = computed(() => previewBusy.value || previewStatus.value === 'starting')
const overlayLabel = computed(() =>
  previewStatus.value === 'starting' ? 'Starting preview' : 'Reconfiguring the camera',
)
const overlayDetail = computed(() =>
  previewBusy.value
    ? 'The recording pipeline is being restarted. The feed returns in a moment.'
    : undefined,
)

const statusHint = computed(() => {
  if (!stage.value) return ''
  switch (stage.value.state) {
    case 'idle':
      return `Press Start to begin the take. The button locks once you do. Takes under ${minDuration.value} s are discarded.`
    case 'recording':
      return `Recording. This take stops on its own at ${maxDuration.value ?? '--'} s.`
    case 'saved':
      return stage.value.review?.verdict
        ? 'Saved and reviewed. Move on, or re-record to replace this take.'
        : 'Saved. Mark the take Passed or Failed to unlock the next stage.'
    default:
      return ''
  }
})

/* ------------------------------------------------------------- lifecycle */

async function openPreview() {
  if (!session.value) return
  previewStatus.value = 'starting'
  try {
    await api.startPreview(session.value.session_id)
    previewStatus.value = 'live'
  } catch (error) {
    previewStatus.value = 'off'
    const { message } = describeError(error)
    toast.danger('Could not start the preview', message)
  }
}

/*
 * The preview is stopped at most once per visit. Relying on both this and the
 * unload beacon meant two stops could land either side of the next screen's
 * start, and the later one closed the pipeline the new screen had just opened.
 * The collector then saw a frozen feed while the recording itself stayed healthy,
 * because the camera writes the bag regardless of who is watching it.
 */
let previewStopped = false

async function closePreview() {
  if (!session.value || previewStopped) return
  previewStopped = true
  previewStatus.value = 'off'
  try {
    const result = await api.stopPreview(session.value.session_id)
    if (result.auto_saved) {
      toast.info('Recording stopped and saved', 'Leaving the capture screen ended the take.')
    }
  } catch {
    // Leaving the screen must never block on a failing call.
  }
}

onMounted(async () => {
  if (!session.value) {
    return
  }
  startPolling(session.value.session_id)

  if (recording.value) {
    const startedAt = stage.value?.recording_started_at ?? null
    if (startedAt) {
      resumeFrom(startedAt, maxDuration.value)
    } else {
      startClock(maxDuration.value)
    }
    // A refreshed page mid-take must resume the banner too, not only the timer.
    startAudioPoll()
  }

  await openPreview()
})

onBeforeUnmount(() => {
  stopPolling()
  stopAudioPoll()
  /*
   * sendBeacon survives the page unloading, which a plain fetch does not. The
   * service treats this route as idempotent and tolerates an empty body because of
   * it; see docs/API.md section 6.6. It is only needed when the screen is left
   * without closePreview having run, so a visit never stops the pipeline twice.
   */
  if (!previewStopped && session.value) {
    if (navigator.sendBeacon) {
      previewStopped = true
      navigator.sendBeacon(`/api/sessions/${session.value.session_id}/preview/stop`)
    } else {
      void closePreview()
    }
  }
  resetClock()
})

/* --------------------------------------------------------------- actions */

/**
 * Rebuild the preview stream by hand. The MJPEG element reports a closed stream
 * but not a silent freeze, so this covers the case where the pipeline was closed
 * underneath us and the picture simply stopped moving.
 */
function reconnectPreview() {
  previewPane.value?.retry()
}

async function onStart() {
  if (!session.value) return
  const sid = session.value.session_id
  stopPolling()
  // The recorder is attached at pipeline start, so the feed goes down for about a
  // second here. Cover it instead of letting the collector read a still frame as a
  // hung camera.
  previewBusy.value = true
  try {
    const result = await run('start', () => api.startRecord(sid, props.index))
    if (!result) return
    await fetch(sid)
    startClock(result.auto_stop_at_s)
    startAudioPoll()
    toast.success('Recording started')
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not start recording', message)
  } finally {
    previewBusy.value = false
    startPolling(sid)
  }
}

async function onStop() {
  if (!session.value) return
  const sid = session.value.session_id
  stopPolling()
  // Stopping closes the recording pipeline and reopens the preview one, so this is
  // the same one second gap as a start.
  previewBusy.value = true
  try {
    await run('stop', () => api.stopRecord(sid, props.index))
    await fetch(sid)
    resetClock()
    // The banner switches from the live view to what the saved artifact says, so
    // the poll is no longer needed.
    stopAudioPoll()
    audioLive.value = null
    toast.success('Take saved')
  } catch (error) {
    const { code, message } = describeError(error)
    await fetch(sid)
    resetClock()
    if (code === 'RECORDING_TOO_SHORT') {
      toast.info('Take discarded', message)
    } else {
      toast.danger('Could not save the take', message)
    }
  } finally {
    previewBusy.value = false
    startPolling(sid)
  }
}

async function onDiscard() {
  if (!session.value) return
  const sid = session.value.session_id
  discardOpen.value = false
  try {
    await run('discard', () => api.discardRecord(sid, props.index))
    await fetch(sid)
    resetClock()
    toast.info('Take deleted', 'Press Start when you are ready to record again.')
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not delete the take', message)
  }
}

async function onAdvance() {
  if (!session.value) return
  const sid = session.value.session_id

  /*
   * A comment typed but not yet blurred is saved first. The verdict itself is
   * already stored, it is what enabled this button. If the flush fails the round
   * stays put rather than advancing with the note silently dropped.
   */
  if (reviewRef.value && !(await reviewRef.value.flush())) {
    return
  }

  try {
    const result = await run('advance', () => api.advance(sid, props.index))
    if (!result) return
    // The guard reads the store, so the returned session has to land there
    // before navigating, otherwise it will bounce straight back here.
    setSession(result.session)
    await closePreview()
    if (result.next.type === 'finish') {
      await router.push({ name: 'finish' })
      return
    }
    await router.push({ name: 'guide', params: { index: String(result.next.stage_index) } })
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not move to the next stage', message)
  }
}

/** Keeps the URL honest if the backend changed state underneath us. */
function onBack() {
  if (!session.value) {
    void router.push({ name: 'home' })
    return
  }
  if (isFinished.value) {
    void router.push({ name: 'finish' })
    return
  }
  void router.push(locationForSession(session.value))
}

/*
 * Returning home interrupts the session rather than discarding it. The backend
 * keeps it in progress, so the home screen can offer to resume.
 *
 * Mid-take it is destructive to the take, because leaving the screen stops the
 * recording, so that case is confirmed first. Otherwise there is nothing at
 * risk and a confirmation would only be friction.
 */
function askLeaveForHome() {
  if (recording.value) {
    homeConfirmOpen.value = true
    return
  }
  void leaveForHome()
}

async function leaveForHome() {
  homeConfirmOpen.value = false
  const name = session.value?.name
  await router.push({ name: 'home' })
  toast.info(
    name ? `Session ${name} interrupted` : 'Session interrupted',
    'Nothing was deleted. Resume it from the home screen when you are ready.',
  )
  /*
   * Stopping a take is still finishing on the host when this runs, so the home
   * screen's first read of the session summary is one take behind. A single
   * follow-up read once the save has settled keeps the count honest.
   */
  window.setTimeout(() => void refreshHealth(), 3000)
}
</script>

<template>
  <div class="page page--tight shell">
    <div v-if="stage" class="cap">
      <div class="cap__view">
        <PreviewPane
          ref="previewPane"
          :status="previewStatus"
          :stream-url="streamUrl"
          :overlay-visible="overlayVisible"
          :overlay-label="overlayLabel"
          :overlay-detail="overlayDetail"
        >
          <template #hud>
            <div v-if="recording" class="hud hud--rec">
              <span class="hud__dot" aria-hidden="true" />
              <span class="hud__label">REC</span>
              <span class="hud__time mono">{{ formatClock(elapsed) }}</span>
            </div>
            <div v-else-if="artifact" class="hud hud--saved">
              <span class="hud__label">SAVED</span>
              <span class="hud__time mono">{{ formatSeconds(artifact.duration_s) }}</span>
            </div>

            <!--
              The microphone level, over the picture the collector is watching. An
              open microphone that hears nothing is the one failure that looks like
              success everywhere else, so it belongs in the same eyeline as the
              take rather than in a note further down the page.
            -->
            <div
              v-if="audioMeterVisible"
              class="hud hud--mic"
              :class="{ 'hud--mic-idle': !audioMeterActive }"
              role="meter"
              aria-label="Microphone level"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="audioLevel"
              :aria-valuetext="audioMeterText"
            >
              <svg class="hud__mic" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d="M8 1.5a2 2 0 0 1 2 2v4a2 2 0 1 1-4 0v-4a2 2 0 0 1 2-2Z"
                  fill="currentColor"
                />
                <path
                  d="M4 7.5a4 4 0 0 0 8 0M8 11.5V14"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.3"
                  stroke-linecap="round"
                />
              </svg>
              <span class="hud__bars" aria-hidden="true">
                <span
                  class="hud__bars-fill"
                  :class="{ 'hud__bars-fill--hot': audioClipping }"
                  :style="{ width: `${audioLevel}%` }"
                />
                <span class="hud__bars-hold" :style="{ left: `${audioHoldLevel}%` }" />
              </span>
              <span class="hud__level mono">{{ audioLevel }}</span>
            </div>

            <div class="hud hud--right mono">
              {{ config?.preview ? `${config.preview.fps} fps` : '--' }}
            </div>
          </template>
        </PreviewPane>

        <!--
          Persistent, unlike a toast: the dangerous audio failure is a mic that
          opened and delivers silence, which every other view reports as success.
        -->
        <p
          v-if="audioNotice"
          class="audio-notice"
          :class="`audio-notice--${audioNotice.tone}`"
          role="status"
        >
          {{ audioNotice.text }}
        </p>

        <!--
          Shown only once a take exists. The verdict is what unlocks Next stage,
          so it sits directly under the feed the collector is judging.
        -->
        <StageReview
          v-if="artifact && stage"
          ref="reviewRef"
          :stage="stage"
          :max-length="REVIEW_COMMENT_MAX_LENGTH"
        />
      </div>

      <aside class="cap__rail">
        <div class="railblock">
          <p class="label">{{ position }}</p>
          <h1 class="railblock__name">{{ stage.name }}</h1>
          <StatePill :state="stage.state" />
        </div>

        <dl class="spec railblock__spec">
          <template v-if="recording">
            <dt>Elapsed</dt>
            <dd>{{ formatClock(elapsed) }}</dd>
            <dt>Auto stop in</dt>
            <dd>{{ remaining === null ? '--' : formatClock(remaining) }}</dd>
            <dt>Size so far</dt>
            <dd>~{{ formatBytes(estimatedBytes) }}</dd>
            <template v-if="config?.audio.enabled">
              <dt>Audio</dt>
              <dd :class="{ 'railblock__bad': audioSilent }">{{ audioMeterText }}</dd>
            </template>
          </template>

          <template v-else-if="artifact">
            <dt>Duration</dt>
            <dd>{{ formatSeconds(artifact.duration_s) }}</dd>
            <dt>File size</dt>
            <dd>{{ formatBytes(artifact.size_bytes) }}</dd>
            <dt>Recorded</dt>
            <dd class="railblock__stamp">{{ formatDateTime(artifact.stopped_at) }}</dd>
          </template>

          <template v-else>
            <dt>Limit</dt>
            <dd>{{ maxDuration ?? '--' }} s</dd>
            <dt>Minimum</dt>
            <dd>{{ minDuration }} s</dd>
            <dt>Output</dt>
            <dd>{{ outputName }}</dd>
            <template v-if="config?.audio.enabled">
              <dt>Audio</dt>
              <dd>{{ config.audio.samplerate }} Hz, {{ config.audio.output_name }}</dd>
            </template>
          </template>
        </dl>

        <CaptureControls
          :stage="stage"
          :busy="busyAction"
          :is-last-stage="isLastStage"
          @start="onStart"
          @stop="onStop"
          @discard="discardOpen = true"
          @advance="onAdvance"
        />

        <p class="cap__hint">{{ statusHint }}</p>

        <!--
          A freeze the browser never reported would otherwise force the collector
          to leave and re-enter the stage to get the feed back.
        -->
        <AppButton
          v-if="previewStatus === 'live'"
          variant="ghost"
          size="sm"
          @click="reconnectPreview"
        >
          Reconnect preview
        </AppButton>

        <figure v-if="artifact" class="thumb">
          <img :src="artifact.thumbnail_url" alt="Last frame of the saved take" />
          <figcaption>Last frame of the saved take</figcaption>
        </figure>

        <div class="rail-actions">
          <AppButton variant="ghost" size="sm" @click="onBack">Leave this stage</AppButton>
          <AppButton variant="ghost" size="sm" @click="askLeaveForHome">Back to home</AppButton>
        </div>
      </aside>
    </div>

    <div v-else class="missing">
      <p class="missing__title">This stage is not available</p>
      <p class="missing__body">The session may have been discarded on the host.</p>
      <AppButton variant="primary" @click="$router.push({ name: 'home' })">Back to home</AppButton>
    </div>

    <ConfirmDialog
      :open="discardOpen"
      tone="danger"
      title="Delete this take?"
      message="The bag file for this stage will be removed from the host. You can record it again afterwards."
      confirm-label="Delete take"
      @cancel="discardOpen = false"
      @confirm="onDiscard"
    />

    <ConfirmDialog
      :open="homeConfirmOpen"
      title="Stop recording and leave?"
      message="The current take is still running. Leaving now stops it and saves what has been captured so far. The session stays open, so you can resume it from the home screen."
      confirm-label="Stop and leave"
      cancel-label="Keep recording"
      @cancel="homeConfirmOpen = false"
      @confirm="leaveForHome"
    />
  </div>
</template>

<style scoped>
/*
 * Preview on the left takes all remaining width, controls sit in a fixed rail
 * on the right so nothing moves between stages.
 */
.cap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: var(--s5);
  align-items: start;
}

/* Feed and review panel stack; the panel appears only after a take is saved. */
.cap__view {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  min-width: 0;
}

/*
 * Audio warning. Sits between the feed and the review panel so it is in the same
 * eyeline as the picture the collector is judging.
 */
.audio-notice {
  font-size: var(--t-sm);
  line-height: 1.5;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  border: 1px solid transparent;
}

.audio-notice--warn {
  background: var(--warn-bg);
  border-color: var(--warn);
  color: var(--warn);
}

.audio-notice--danger {
  background: var(--danger-bg);
  border-color: var(--danger-line);
  color: var(--danger);
}

@media (max-width: 1080px) {
  .cap {
    grid-template-columns: minmax(0, 1fr);
  }
}

.cap__rail {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--s5);
}

.railblock__name {
  font-family: var(--font-display);
  font-size: var(--t-lg);
  font-weight: 400;
  line-height: 1.2;
  margin: var(--s2) 0 var(--s3);
  letter-spacing: -0.01em;
}

.railblock__spec {
  border-top: 1px solid var(--line);
  margin: 0;
}

.railblock__stamp {
  font-size: var(--t-xs);
}

.railblock__bad {
  color: var(--warn);
}

.cap__hint {
  font-size: var(--t-xs);
  color: var(--ink-400);
  line-height: 1.5;
}

/* Two equal escape hatches, so neither reads as the primary action. */
.rail-actions {
  display: flex;
  gap: var(--s2);
}

.rail-actions > * {
  flex: 1;
}

.thumb {
  margin: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
  background: #0d1517;
}

/*
 * The poster frame normally comes from the colour stream, which is 4:3 like this
 * box, so cover and contain agree. contain is used anyway so a plan without a
 * colour stream, whose frame falls back to depth, is not cropped.
 */
.thumb img {
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: contain;
  background: #0d1517;
}

.thumb figcaption {
  font-size: var(--t-2xs);
  color: var(--ink-400);
  padding: var(--s2) var(--s3);
  background: var(--surface);
  border-top: 1px solid var(--line);
}

/* Overlay readouts on the feed itself */
.hud {
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  padding: 5px var(--s3);
  border-radius: var(--r-full);
  font-size: var(--t-xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  z-index: 4;
}

.hud--rec {
  top: var(--s4);
  left: var(--s4);
  background: rgba(158, 58, 19, 0.94);
  color: #fff;
}

.hud--saved {
  top: var(--s4);
  left: var(--s4);
  background: rgba(26, 97, 68, 0.94);
  color: #fff;
}

.hud--right {
  top: var(--s4);
  right: var(--s4);
  background: rgba(12, 16, 18, 0.6);
  color: rgba(232, 240, 241, 0.82);
  font-weight: 400;
  letter-spacing: 0.04em;
}

/*
 * Microphone level. Sits directly under REC so it shares that eyeline, and stays
 * on a dark translucent pill whatever the picture behind it is doing: the whole
 * point is to be readable while someone is watching the subject, not the HUD.
 */
.hud--mic {
  top: calc(var(--s4) + 34px);
  left: var(--s4);
  gap: var(--s2);
  padding: 5px var(--s3) 5px var(--s2);
  background: rgba(12, 16, 18, 0.72);
  color: rgba(232, 240, 241, 0.92);
  font-weight: 400;
  letter-spacing: 0;
}

/* Audio configured but not confirmed yet. Kept visible rather than unmounted, so
   the pill does not appear mid take; dimmed so a flat bar is not read as silence. */
.hud--mic-idle {
  opacity: 0.55;
}

.hud__mic {
  width: 13px;
  height: 13px;
  flex: none;
  opacity: 0.75;
}

/* The bar is drawn inside a fixed width box, so the pill does not grow and shrink
   as the level moves and pull the eye away from the subject. */
.hud__bars {
  position: relative;
  display: block;
  width: 74px;
  height: 6px;
  border-radius: var(--r-full);
  background: rgba(232, 240, 241, 0.22);
  overflow: hidden;
}

.hud__bars-fill {
  display: block;
  height: 100%;
  border-radius: var(--r-full);
  background: #8fd3b8;
  transition: width 90ms linear;
}

.hud__bars-fill--hot {
  background: #f0907f;
}

.hud__bars-hold {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: rgba(255, 255, 255, 0.9);
  transition: left 90ms linear;
}

.hud__level {
  min-width: 2ch;
  font-size: var(--t-2xs);
  letter-spacing: 0;
  text-align: right;
  opacity: 0.82;
}

.hud__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--r-full);
  background: #fff;
  animation: hud-blink 1.4s var(--ease) infinite;
}

.hud__time {
  letter-spacing: 0;
}

@keyframes hud-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.2;
  }
}

.missing {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--s3);
  padding: var(--s9) var(--s5);
  text-align: center;
}

.missing__title {
  font-size: var(--t-lg);
}

.missing__body {
  color: var(--ink-500);
}
</style>
