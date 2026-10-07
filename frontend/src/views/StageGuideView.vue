<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import AppButton from '@/components/AppButton.vue'
import StatePill from '@/components/StatePill.vue'
import { useApp } from '@/composables/useApp'
import { useGuides, guideImageUrl } from '@/composables/useGuides'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { describeError } from '@/api/errorText'
import { locationForSession } from '@/utils/flow'
import { stageNumber } from '@/utils/format'

const props = defineProps<{ index: number }>()

const router = useRouter()
const toast = useToast()
const { config, totalStages } = useApp()
const { byIndex } = useGuides()
const { stageAt, session, setSession } = useSession()

const advancing = ref(false)

const stage = computed(() => stageAt(props.index))

/*
 * A running session carries its own frozen copy of the stage copy and limits, so
 * that is what the collector sees. Falling back to the live config only covers
 * the moment before a session exists.
 */
const stageConfig = computed(() => config.value?.stages.find((s) => s.index === props.index) ?? null)
const instructions = computed(
  () => stage.value?.instructions ?? stageConfig.value?.instructions ?? null,
)
const maxDuration = computed(
  () => stage.value?.max_duration_s ?? stageConfig.value?.max_duration_s ?? null,
)
const guide = computed(() => byIndex.value.get(props.index) ?? null)
const imageUrl = computed(() => guideImageUrl(guide.value))

/** From the service, which owns the .db3 rule. Shown before any take exists. */
const outputName = computed(() => config.value?.recording.output_name ?? '')

const isRecording = computed(() => stage.value?.state === 'recording')
const isSaved = computed(() => stage.value?.state === 'saved')
const isCurrent = computed(() => session.value?.current_stage === props.index)
const isLastStage = computed(() => props.index >= totalStages.value)

const position = computed(
  () =>
    `Stage ${String(props.index).padStart(2, '0')} of ${String(totalStages.value).padStart(2, '0')}`,
)

/*
 * Ways out of this screen. A saved stage must not dead-end, so when it is still
 * the current stage the primary button moves the session forward and a secondary
 * button offers the capture screen, which is where Re-record lives. Without that
 * second button a collector who navigated away from a take they want to redo has
 * no way back to it: advancing is the only other option and cannot be undone.
 */
type PrimaryKind = 'capture' | 'advance' | 'finish' | 'current'

const primary = computed<{ kind: PrimaryKind; label: string }>(() => {
  if (isRecording.value) {
    return { kind: 'capture', label: 'Return to recording' }
  }
  if (!isSaved.value) {
    return { kind: 'capture', label: 'Next: open capture' }
  }
  if (!isCurrent.value) {
    return { kind: 'current', label: 'Go to current stage' }
  }
  if (isLastStage.value) {
    return { kind: 'finish', label: 'Finish session' }
  }
  return { kind: 'advance', label: 'Next stage' }
})

/** Offered only where the primary button no longer opens the capture screen. */
const canReRecord = computed(() => isSaved.value && isCurrent.value)

function openCapture() {
  void router.push({ name: 'capture', params: { index: String(props.index) } })
}

async function onPrimary() {
  const kind = primary.value.kind

  if (kind === 'capture') {
    openCapture()
    return
  }

  if (kind === 'current' && session.value) {
    await router.push(locationForSession(session.value))
    return
  }

  if (!session.value) {
    await router.push({ name: 'home' })
    return
  }

  advancing.value = true
  try {
    const result = await api.advance(session.value.session_id, props.index)
    setSession(result.session)
    if (result.next.type === 'finish') {
      await router.push({ name: 'finish' })
      return
    }
    await router.push({ name: 'guide', params: { index: String(result.next.stage_index) } })
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not move on', message)
  } finally {
    advancing.value = false
  }
}

/*
 * Leaving for the home screen interrupts the session rather than discarding it.
 * The backend keeps it in progress and nothing is deleted, so the home screen
 * can offer to resume it. No confirmation is needed, because there is nothing
 * to lose and a collector may step away at any point.
 */
async function goHome() {
  const name = session.value?.name
  await router.push({ name: 'home' })
  toast.info(
    name ? `Session ${name} interrupted` : 'Session interrupted',
    'Nothing was deleted. Resume it from the home screen when you are ready.',
  )
}
</script>

<template>
  <div class="page shell">
    <header class="head">
      <div class="head__text">
        <p class="label">{{ position }}</p>
        <h1 class="display">{{ stage?.name ?? stageConfig?.name ?? stageNumber(index) }}</h1>
      </div>
      <StatePill v-if="stage" :state="stage.state" />
    </header>

    <div class="body">
      <figure class="shot">
        <img v-if="imageUrl" :src="imageUrl" :alt="`Pose diagram for ${stage?.name ?? ''}`" />
        <figcaption v-else class="shot__missing">
          <p class="shot__missing-title">No diagram for this stage</p>
          <p class="shot__missing-body">
            Upload one on the stage diagrams screen, then come back here.
          </p>
          <AppButton variant="secondary" size="sm" @click="$router.push('/guides')">
            Configure diagrams
          </AppButton>
        </figcaption>
      </figure>

      <div class="brief">
        <p class="label">How to position the camera</p>
        <p class="brief__text">
          {{ instructions ?? 'No instructions have been configured for this stage.' }}
        </p>

        <dl class="spec brief__spec">
          <dt>Maximum take length</dt>
          <dd>{{ maxDuration ?? '--' }} s</dd>
          <dt>Output</dt>
          <dd>{{ outputName || 'capture.db3' }}</dd>
        </dl>
      </div>
    </div>

    <p v-if="isSaved" class="wash wash--ok">
      This stage has already been saved. Re-record it to take it again, or move on to the next
      stage.
    </p>

    <footer class="foot">
      <div class="foot__side">
        <AppButton
          v-if="index > 1"
          variant="ghost"
          @click="$router.push({ name: 'guide', params: { index: String(index - 1) } })"
        >
          Previous stage
        </AppButton>
      </div>

      <div class="foot__side foot__side--end">
        <AppButton variant="ghost" @click="goHome">Back to home</AppButton>
        <AppButton v-if="canReRecord" variant="secondary" size="lg" @click="openCapture">
          Re-record this stage
        </AppButton>
        <AppButton variant="primary" size="lg" :loading="advancing" @click="onPrimary">
          {{ primary.label }}
        </AppButton>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s5);
}

.body {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(260px, 1fr);
  gap: var(--s7);
  align-items: start;
  margin-top: var(--s6);
}

@media (max-width: 940px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--s5);
  }
}

.shot {
  margin: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
}

.shot img {
  width: 100%;
  max-height: 58vh;
  object-fit: contain;
  background: var(--surface-sunken);
}

.shot__missing {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s3);
  text-align: center;
  padding: var(--s8) var(--s5);
  background: var(--surface-sunken);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-sm);
  margin: var(--s5);
}

.shot__missing-title {
  font-weight: 600;
}

.shot__missing-body {
  font-size: var(--t-sm);
  color: var(--ink-500);
  max-width: 34ch;
}

.brief__text {
  margin-top: var(--s3);
  font-size: var(--t-md);
  line-height: 1.6;
  color: var(--ink-700);
  /*
   * pre-line, not pre-wrap: the operator writes this as prose, so a blank line
   * should separate paragraphs and runs of spaces should still collapse the way
   * text normally does. Only the description behaves this way; the title is
   * folded onto one line by the service, because it is rendered inline on the
   * rail and the result cards.
   */
  white-space: pre-line;
}

.brief__spec {
  margin-top: var(--s5);
  border-top: 1px solid var(--line);
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s4);
  flex-wrap: wrap;
  margin-top: var(--s7);
}

.foot__side {
  display: flex;
  align-items: center;
  gap: var(--s3);
}

/* Pushes the interrupting and the primary action to the right edge. */
.foot__side--end {
  margin-left: auto;
}

.wash--ok {
  background: var(--saved-bg);
  border-color: #cbe3d6;
  color: var(--saved-fg);
  margin-top: var(--s5);
}
</style>
