<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, isMock } from '@/api'
import AppButton from '@/components/AppButton.vue'
import CaptureControls from '@/components/CaptureControls.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PreviewPane from '@/components/PreviewPane.vue'
import StatePill from '@/components/StatePill.vue'
import { useApp } from '@/composables/useApp'
import { useCountdown } from '@/composables/useCountdown'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { describeError } from '@/api/errorText'
import { locationForSession } from '@/utils/flow'
import { formatBytes, formatClock, formatSeconds, formatDateTime } from '@/utils/format'

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
const { elapsed, remaining, start: startClock, resumeFrom, reset: resetClock } = useCountdown()

const stage = computed(() => stageAt(props.index))
const stageConfig = computed(() => config.value?.stages.find((s) => s.index === props.index) ?? null)

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

const position = computed(
  () => `Stage ${String(props.index).padStart(2, '0')} / ${String(totalStages.value).padStart(2, '0')}`,
)

/** Rough figure so the collector can watch disk usage while a take runs. */
const estimatedBytes = computed(() => Math.round(elapsed.value * 17.4 * 1024 * 1024))

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
      return 'Saved. Move on, or re-record to replace this take.'
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

async function closePreview() {
  if (!session.value) return
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
  }

  await openPreview()
})

onBeforeUnmount(() => {
  stopPolling()
  // sendBeacon survives the page unloading, which a plain fetch does not. The
  // mock has no HTTP endpoint, so it is called directly instead.
  if (session.value) {
    if (isMock) {
      void api.stopPreview(session.value.session_id).catch(() => undefined)
    } else if (navigator.sendBeacon) {
      navigator.sendBeacon(`/api/sessions/${session.value.session_id}/preview/stop`)
    } else {
      void closePreview()
    }
  }
  resetClock()
})

/* --------------------------------------------------------------- actions */

async function onStart() {
  if (!session.value) return
  const sid = session.value.session_id
  stopPolling()
  try {
    const result = await run('start', () => api.startRecord(sid, props.index))
    if (!result) return
    await fetch(sid)
    startClock(result.auto_stop_at_s)
    toast.success('Recording started')
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not start recording', message)
  } finally {
    startPolling(sid)
  }
}

async function onStop() {
  if (!session.value) return
  const sid = session.value.session_id
  stopPolling()
  try {
    await run('stop', () => api.stopRecord(sid, props.index))
    await fetch(sid)
    resetClock()
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

            <div class="hud hud--right mono">
              {{ config?.preview ? `${config.preview.fps} fps` : '--' }}
            </div>
          </template>
        </PreviewPane>
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
            <dd>capture.bag</dd>
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

.thumb img {
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
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
