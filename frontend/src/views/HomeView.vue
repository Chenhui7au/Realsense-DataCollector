<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import type { AudioTestLive, AudioTestResult } from '@/api'
import AppButton from '@/components/AppButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FolderPicker from '@/components/FolderPicker.vue'
import NewSessionDialog from '@/components/NewSessionDialog.vue'
import { useApp } from '@/composables/useApp'
import { useBootstrap } from '@/composables/useBootstrap'
import { useGuides } from '@/composables/useGuides'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { describeError } from '@/api/errorText'
import { locationForSession } from '@/utils/flow'
import { formatBytes, levelFromDbfs, LEVEL_FLOOR_DB } from '@/utils/format'
import { checkProjectRoot } from '@/utils/paths'

const router = useRouter()
const toast = useToast()
const { ensure } = useBootstrap()

const {
  config,
  health,
  configError,
  canStartSession,
  blockers,
  totalStages,
  project,
  projectReady,
  activeSession,
  audio,
  audioEnabled,
  audioAvailable,
  audioBlocking,
  savingProject,
  refreshHealth,
  saveProjectRoot,
} = useApp()
const { uploaded, total, ready: guidesReady } = useGuides()
const { fetch, create, discard } = useSession()

const starting = ref(false)
const resuming = ref(false)
const discardOpen = ref(false)
const discarding = ref(false)

/* ------------------------------------------------------------- microphone */

/*
 * The input check. It exists because a microphone that opens but delivers digital
 * silence looks identical to a working one everywhere else, and audio is optional
 * by default: without this, a whole round could be recorded without sound and
 * nobody would know until the data was being labelled.
 *
 * Pressing the button opens the device and keeps it open, so the meter moves while
 * the collector speaks and they can judge the level themselves instead of trusting
 * one number. Stop closes the device again.
 */
const TEST_POLL_MS = 120

const testRunning = ref(false)
const testStarting = ref(false)
const testStopping = ref(false)
const testLive = ref<AudioTestLive | null>(null)
const testResult = ref<AudioTestResult | null>(null)
/* Peak hold, in dBFS internally. The highest recent level, falling slowly, so a
 * short word is still visible after the block that carried it has been replaced. */
const testHoldDb = ref(LEVEL_FLOOR_DB)
let testTimer: number | null = null

/* The bar and the number both come from the same 0..100 reading, so they can
 * never disagree. */
const testLevelValue = computed(() => levelFromDbfs(testLive.value?.level_dbfs))
const testHoldValue = computed(() => levelFromDbfs(testHoldDb.value))
const testClipping = computed(() => (testLive.value?.peak_dbfs ?? LEVEL_FLOOR_DB) >= -1)
const testLevelText = computed(() =>
  !testRunning.value
    ? 'idle'
    : (testLive.value?.level_dbfs ?? null) === null
      ? 'silent'
      : `level ${testLevelValue.value} of 100`,
)

const testNote = computed(() => {
  const result = testResult.value
  if (!result) {
    return null
  }
  switch (result.verdict) {
    case 'silent':
      return 'No sound reached the microphone. Check the input device and the Windows microphone privacy setting.'
    case 'quiet':
      return 'The input is very quiet. Move closer to the microphone or raise its gain.'
    case 'clipping':
      return 'The input is clipping. Lower the gain, otherwise the recording will distort.'
    default:
      return `Input level looks good (${levelFromDbfs(result.peak_dbfs)} / 100).`
  }
})

/* Shown while the check runs, before there is a verdict to show. */
const testLiveNote = computed(() => {
  if (!testRunning.value) {
    return null
  }
  const live = testLive.value
  if (!live) {
    return 'Opening the microphone…'
  }
  if ((live.duration_s ?? 0) < 1.2) {
    return 'Listening. Speak or clap near the microphone.'
  }
  if (!live.heard) {
    return 'The microphone is delivering silence. Check the input device and the Windows microphone privacy setting.'
  }
  if (testClipping.value) {
    return 'The input is clipping. Lower the gain, otherwise the recording will distort.'
  }
  return 'Hearing sound. Stop the test when the level looks right.'
})

const testTone = computed(() => {
  if (testRunning.value) {
    const live = testLive.value
    if (!live || (live.duration_s ?? 0) < 1.2) return ''
    if (!live.heard || testClipping.value) return 'panels__note--bad'
    return 'panels__note--good'
  }
  const verdict = testResult.value?.verdict
  if (!verdict) return ''
  return verdict === 'ok' ? 'panels__note--good' : 'panels__note--bad'
})

const audioFormat = computed(() => {
  const channels = audio.value?.channels
  const name = channels === 1 ? 'mono' : channels === 2 ? 'stereo' : '--'
  return `${audio.value?.samplerate ?? '--'} Hz, ${name}`
})

function stopPolling(): void {
  if (testTimer !== null) {
    window.clearTimeout(testTimer)
    testTimer = null
  }
}

async function pollTest(): Promise<void> {
  testTimer = null
  if (!testRunning.value) {
    return
  }
  try {
    const live = await api.audioTestStatus()
    testLive.value = live
    testHoldDb.value = Math.max(live.level_dbfs ?? LEVEL_FLOOR_DB, testHoldDb.value - 1.5)
    if (!live.running) {
      // The service closed the input on its own: the device failed, or nobody
      // polled for too long. Either way the check is over, so collect the result.
      await stopTest()
      return
    }
  } catch {
    // A dropped poll is not worth a toast; the next one is a tenth of a second away.
  }
  if (testRunning.value) {
    testTimer = window.setTimeout(pollTest, TEST_POLL_MS)
  }
}

async function startTest(): Promise<void> {
  testStarting.value = true
  testResult.value = null
  testHoldDb.value = LEVEL_FLOOR_DB
  try {
    testLive.value = await api.audioTestStart()
    testRunning.value = true
    stopPolling()
    testTimer = window.setTimeout(pollTest, TEST_POLL_MS)
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not start the microphone check', message)
  } finally {
    testStarting.value = false
  }
}

async function stopTest(): Promise<void> {
  if (testStopping.value) {
    return
  }
  testStopping.value = true
  testRunning.value = false
  stopPolling()
  try {
    const result = await api.audioTestStop()
    testLive.value = null
    if (result.error) {
      toast.danger('The microphone check stopped early', result.error)
    } else {
      testResult.value = result
    }
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not stop the microphone check', message)
  } finally {
    testHoldDb.value = LEVEL_FLOOR_DB
    testStopping.value = false
  }
}

onBeforeUnmount(() => {
  stopPolling()
  if (testRunning.value) {
    // Best effort. The service also closes the input when the level stops being
    // polled, so losing this request cannot leave the microphone held.
    testRunning.value = false
    void api.audioTestStop().catch(() => undefined)
  }
})

/* --------------------------------------------------------- project folder */

const pickerOpen = ref(false)
const manualEntry = ref(false)
const projectDraft = ref('')
const projectTouched = ref(false)

const projectCheck = computed(() => checkProjectRoot(projectDraft.value))
const projectError = computed(() => {
  if (!projectTouched.value && projectDraft.value.length === 0) {
    return null
  }
  return projectCheck.value.error
})

/*
 * The picker only ever opens on an explicit click. Auto-opening it on a first
 * run looked helpful but took the choice away from the collector, who may just
 * be opening the page to check status.
 */
function openPicker() {
  pickerOpen.value = true
}

function beginManualEntry() {
  projectDraft.value = project.value?.root ?? ''
  projectTouched.value = false
  manualEntry.value = true
}

function cancelManualEntry() {
  manualEntry.value = false
  projectTouched.value = false
}

async function saveManualEntry() {
  const done = await saveProjectRoot(projectDraft.value)
  if (done) {
    manualEntry.value = false
    projectTouched.value = false
  }
}

async function onFolderChosen(path: string) {
  const done = await saveProjectRoot(path)
  if (done) {
    pickerOpen.value = false
    manualEntry.value = false
  }
}

/* -------------------------------------------------------------- sessions */

const newSessionOpen = ref(false)
const existingNames = ref<string[]>([])
const sessionError = ref<string | null>(null)

const title = computed(() => config.value?.app_title ?? 'RealSense Data-Collector')
const device = computed(() => health.value?.device ?? null)

/** The one line the collector needs to read before pressing anything. */
const actionSummary = computed(() => {
  if (activeSession.value) {
    return {
      title: `Session ${activeSession.value.name} is in progress`,
      detail: `${activeSession.value.saved_count} of ${totalStages.value} stages saved. Resume it to carry on, or discard it and start over.`,
    }
  }
  if (canStartSession.value) {
    return {
      title: `Ready to record into ${project.value?.name ?? 'the project folder'}`,
      detail: `Each session creates its own folder with one bag file per stage.`,
    }
  }
  const missing = blockers.value.filter((b) => !b.met)
  return {
    title: 'Not ready to start',
    // Backend reasons arrive as full sentences with their own full stop, so
    // strip the trailing one before joining. Otherwise the line reads
    // "... enable capture.. Stage diagrams configured".
    detail: missing
      .map((b) => `${b.label}: ${b.detail.replace(/\.\s*$/, '')}`)
      .join('. '),
  }
})

onMounted(() => {
  void ensure()
  void refreshHealth()
})

async function openNewSession() {
  sessionError.value = null
  try {
    const listed = await api.listSessions()
    existingNames.value = listed.sessions.map((s) => s.name)
  } catch {
    existingNames.value = []
  }
  newSessionOpen.value = true
}

async function onCreateSession(name: string) {
  starting.value = true
  sessionError.value = null
  try {
    const created = await create({ name })
    if (created) {
      newSessionOpen.value = false
      toast.success(`Session ${created.name} created`, created.data_dir)
      await router.push({ name: 'guide', params: { index: '1' } })
    }
  } catch (error) {
    // Shown inside the dialog so the operator can correct the name and retry
    // without losing what they typed.
    sessionError.value = describeError(error).message
  } finally {
    starting.value = false
  }
}

async function onResume() {
  const active = activeSession.value
  if (!active) {
    return
  }
  resuming.value = true
  try {
    const found = await fetch(active.session_id)
    if (found) {
      await router.push(locationForSession(found))
    } else {
      toast.danger('Could not resume the session', 'It may have been removed on the host.')
      void refreshHealth()
    }
  } finally {
    resuming.value = false
  }
}

async function onDiscard() {
  const active = activeSession.value
  if (!active) {
    return
  }
  discarding.value = true
  try {
    const done = await discard(active.session_id)
    if (done) {
      discardOpen.value = false
      toast.info(
        `Session ${active.name} discarded`,
        'Its folder and every recording in it were removed from the host.',
      )
      await refreshHealth()
      await router.push({ name: 'home' })
    }
  } finally {
    discarding.value = false
  }
}
</script>

<template>
  <div class="page shell">
    <header class="hero">
      <p class="label">Raw sensor capture</p>
      <h1 class="display">{{ title }}</h1>
      <p class="page-lede">
        Eight guided passes over one subject. Each session writes into its own folder inside the
        project folder, one bag file per stage.
      </p>
    </header>

    <p v-if="configError" class="wash wash--danger hero__error">
      {{ configError }}
    </p>

    <div class="panels">
      <section class="card panels__camera">
        <div class="card-head">
          <h2 class="card-title">Camera</h2>
          <span class="chip" :class="device?.connected ? 'chip--on' : 'chip--off'">
            {{ device?.connected ? 'Connected' : 'Not detected' }}
          </span>
        </div>
        <div class="card-body">
          <dl class="spec">
            <dt>Model</dt>
            <dd>{{ device?.name ?? '--' }}</dd>
            <dt>Serial</dt>
            <dd>{{ device?.serial ?? '--' }}</dd>
            <dt>Firmware</dt>
            <dd>{{ device?.firmware ?? '--' }}</dd>
            <dt>Link speed</dt>
            <dd>USB {{ device?.usb_type ?? '--' }}</dd>
          </dl>
          <p v-if="device && !device.connected" class="panels__note panels__note--bad">
            {{ device.reason ?? 'Re-seat the USB cable and check the host.' }}
          </p>
        </div>
      </section>

      <section v-if="audioEnabled" class="card panels__mic">
        <div class="card-head">
          <h2 class="card-title">Microphone</h2>
          <span class="chip" :class="audioAvailable ? 'chip--on' : 'chip--warn'">
            {{ audioAvailable ? 'Ready' : 'Unavailable' }}
          </span>
        </div>
        <div class="card-body">
          <dl class="spec">
            <dt>Device</dt>
            <dd>{{ audio?.device ?? '--' }}</dd>
            <dt>Format</dt>
            <dd>{{ audioFormat }}</dd>
            <dt>Output</dt>
            <dd class="mono">{{ config?.audio.output_name ?? 'audio.wav' }}</dd>
          </dl>

          <div class="lmeter" :class="{ 'lmeter--idle': !testRunning }">
            <div class="lmeter__head">
              <span class="lmeter__label">Input level</span>
              <span class="lmeter__readout">{{ testLevelValue }}</span>
            </div>
            <div
              class="lmeter__track"
              role="meter"
              aria-label="Microphone input level"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="testLevelValue"
              :aria-valuetext="testLevelText"
            >
              <div
                class="lmeter__fill"
                :class="{ 'lmeter__fill--hot': testClipping }"
                :style="{ width: `${testLevelValue}%` }"
              />
              <div v-if="testRunning" class="lmeter__hold" :style="{ left: `${testHoldValue}%` }" />
            </div>
            <div class="lmeter__scale" aria-hidden="true">
              <span>0</span>
              <span>25</span>
              <span>50</span>
              <span>75</span>
              <span>100</span>
            </div>
          </div>

          <p v-if="!audioAvailable" class="panels__note panels__note--bad">
            {{ audio?.reason ?? 'The microphone could not be opened.' }}
          </p>
          <p v-if="!audioAvailable" class="panels__hint">
            {{
              audioBlocking
                ? 'A round cannot start until this works.'
                : 'Recording still works; each take will simply have no audio.'
            }}
          </p>
          <p v-else-if="testRunning && testLiveNote" class="panels__note" :class="testTone">
            {{ testLiveNote }}
          </p>
          <p v-else-if="testNote" class="panels__note" :class="testTone">{{ testNote }}</p>

          <div class="panels__actions">
            <AppButton
              v-if="!testRunning"
              size="sm"
              variant="secondary"
              :disabled="!audioAvailable || testStarting"
              :loading="testStarting"
              @click="startTest"
            >
              Test microphone
            </AppButton>
            <AppButton v-else size="sm" variant="danger" :loading="testStopping" @click="stopTest">
              Stop test
            </AppButton>
          </div>
        </div>
      </section>

      <section class="card panels__project">
        <div class="card-head">
          <h2 class="card-title">Project folder</h2>
          <span class="chip" :class="projectReady ? 'chip--on' : project?.configured ? 'chip--off' : 'chip--warn'">
            {{ projectReady ? 'Ready' : project?.configured ? 'Unusable' : 'Not set' }}
          </span>
        </div>

        <div class="card-body">
          <!-- Manual entry is a fallback, so it sits behind a link and is not
               the first thing a collector sees. -->
          <template v-if="manualEntry">
            <label class="field">
              <span class="label">Folder path on the host</span>
              <input
                v-model="projectDraft"
                class="field__input mono"
                type="text"
                autocomplete="off"
                spellcheck="false"
                placeholder="C:\capture\Project_A"
                :aria-invalid="Boolean(projectError)"
                :disabled="savingProject"
                @input="projectTouched = true"
                @keydown.enter="projectCheck.ok && saveManualEntry()"
              />
            </label>
            <p v-if="projectError" class="field__error">{{ projectError }}</p>
            <p v-else class="panels__hint">
              An absolute path. The folder is created if it does not exist.
            </p>

            <div class="panels__actions">
              <AppButton size="sm" variant="ghost" :disabled="savingProject" @click="cancelManualEntry">
                Cancel
              </AppButton>
              <AppButton
                size="sm"
                variant="primary"
                :disabled="!projectCheck.ok"
                :loading="savingProject"
                @click="saveManualEntry"
              >
                Save folder
              </AppButton>
            </div>
          </template>

          <template v-else-if="project?.configured">
            <p class="panels__name">{{ project.name ?? '--' }}</p>
            <p class="panels__path mono" :title="project.root ?? ''">{{ project.root }}</p>

            <!--
              Neither free space nor a session count is shown here. Free space
              belongs to the readiness checklist below, where a shortfall reads
              as a reason the session cannot start rather than a figure to
              interpret. The count duplicated the hint the folder picker already
              gives for a folder that holds rounds, so it was dropped.
            -->
            <p v-if="project.error" class="panels__note panels__note--bad">{{ project.error }}</p>

            <div class="panels__actions panels__actions--split">
              <button class="linkish" :disabled="savingProject" @click="beginManualEntry">
                Type a path
              </button>
              <AppButton size="sm" variant="secondary" @click="openPicker">
                Browse folders
              </AppButton>
            </div>
          </template>

          <template v-else>
            <p class="panels__empty">No project folder yet.</p>
            <p class="panels__note">
              Pick the folder where recordings should be kept. It can be created from the picker if
              it does not exist yet.
            </p>

            <div class="panels__actions panels__actions--split">
              <button class="linkish" :disabled="savingProject" @click="beginManualEntry">
                Type a path
              </button>
              <AppButton size="sm" variant="primary" @click="openPicker">
                Browse folders
              </AppButton>
            </div>
          </template>
        </div>
      </section>

      <section class="card panels__diagrams">
        <div class="card-head">
          <h2 class="card-title">Stage diagrams</h2>
          <span class="chip" :class="guidesReady ? 'chip--on' : 'chip--warn'">
            {{ uploaded }} / {{ total }}
          </span>
        </div>
        <div class="card-body">
          <div class="meter" :class="{ 'meter--done': guidesReady }">
            <span
              class="meter__fill"
              :style="{ width: `${total ? (uploaded / total) * 100 : 0}%` }"
            />
          </div>
          <p class="panels__note">
            <template v-if="guidesReady">
              All {{ total }} diagrams are stored on the host and reused for every session.
            </template>
            <template v-else>
              {{ total - uploaded }} diagrams still need an image before you can start.
            </template>
          </p>
          <RouterLink to="/guides" class="panels__link">
            {{ guidesReady ? 'Review or replace diagrams' : 'Configure diagrams' }}
            <span aria-hidden="true">&rarr;</span>
          </RouterLink>
        </div>
      </section>
    </div>

    <section
      class="actionbar"
      :class="{ 'actionbar--blocked': !canStartSession && !activeSession }"
    >
      <div class="actionbar__text">
        <p class="label">Next step</p>
        <p class="actionbar__title">{{ actionSummary.title }}</p>
        <p class="actionbar__detail">{{ actionSummary.detail }}</p>

        <ul v-if="!canStartSession && !activeSession" class="checklist">
          <li v-for="item in blockers" :key="item.key" :class="{ 'checklist__item--met': item.met }">
            <span class="checklist__mark" aria-hidden="true">{{ item.met ? '\u2713' : '\u00b7' }}</span>
            <span class="checklist__label">{{ item.label }}</span>
            <span class="checklist__detail" :title="item.detail">{{ item.detail }}</span>
          </li>
        </ul>
      </div>

      <div class="actionbar__buttons">
        <template v-if="activeSession">
          <AppButton variant="ghost" @click="discardOpen = true">Discard session</AppButton>
          <AppButton variant="primary" size="lg" :loading="resuming" @click="onResume">
            Resume session
          </AppButton>
        </template>
        <template v-else>
          <AppButton
            variant="primary"
            size="lg"
            :disabled="!canStartSession"
            @click="openNewSession"
          >
            Start new session
          </AppButton>
        </template>
      </div>
    </section>

    <p class="footnote">
      One take per stage is on the order of
      <span class="mono">150&ndash;500 MB</span>, so budget roughly
      <span class="mono">{{ formatBytes(totalStages * 400 * 1024 * 1024) }}</span> per session.
    </p>

    <NewSessionDialog
      :open="newSessionOpen"
      :project="project"
      :existing-names="existingNames"
      :busy="starting"
      :server-error="sessionError"
      @cancel="newSessionOpen = false"
      @create="onCreateSession"
    />

    <FolderPicker
      :open="pickerOpen"
      :start-path="project?.root ?? null"
      @cancel="pickerOpen = false"
      @select="onFolderChosen"
    />

    <ConfirmDialog
      :open="discardOpen"
      tone="danger"
      title="Discard this session?"
      :message="`The folder ${activeSession?.data_dir ?? ''} and every bag file in it will be deleted from the host. This cannot be undone.`"
      confirm-label="Discard and delete"
      :busy="discarding"
      @cancel="discardOpen = false"
      @confirm="onDiscard"
    />
  </div>
</template>

<style scoped>
.hero__error {
  margin-top: var(--s5);
}

.panels {
  display: grid;
  /* Two by two: the sensors on the left, the two things the operator configures
     on the right. Placement is explicit rather than flow-based, so the grouping
     survives the microphone card being absent (audio switched off) instead of
     letting the stage diagrams slide into the left column. */
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s4);
  margin-top: var(--s7);
  align-items: start;
}

.panels__camera {
  grid-column: 1;
  grid-row: 1;
}

.panels__mic {
  grid-column: 1;
  grid-row: 2;
}

.panels__project {
  grid-column: 2;
  grid-row: 1;
}

.panels__diagrams {
  grid-column: 2;
  grid-row: 2;
}

@media (max-width: 980px) {
  .panels {
    grid-template-columns: minmax(0, 1fr);
  }

  /* One column on narrow screens: drop the explicit cells so the cards stack in
     DOM order. */
  .panels > * {
    grid-column: 1;
    grid-row: auto;
  }
}

.panels__name {
  font-family: var(--font-display);
  font-size: var(--t-lg);
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.panels__path {
  font-size: var(--t-xs);
  color: var(--ink-500);
  margin-top: var(--s2);
  overflow-wrap: anywhere;
}

.panels__note {
  font-size: var(--t-sm);
  color: var(--ink-500);
  margin-top: var(--s3);
}

.panels__note--bad {
  color: var(--danger);
}

.panels__note--good {
  color: var(--saved-fg);
}

.panels__hint {
  font-size: var(--t-xs);
  color: var(--ink-400);
  margin-top: var(--s2);
}

.panels__actions {
  display: flex;
  gap: var(--s2);
  justify-content: flex-end;
  align-items: center;
  margin-top: var(--s4);
}

/* Key action on the right, escape hatch on the left. */
.panels__actions--split {
  justify-content: space-between;
}

.panels__empty {
  font-family: var(--font-display);
  font-size: var(--t-lg);
  color: var(--ink-400);
}

/* The input level meter. Always rendered, dimmed when idle, so the card does not
   change height the moment a check starts and the grid row does not jump.

   Named `lmeter` rather than `meter` because the stage diagrams progress bar in
   this same component already owns `.meter` and `.meter__fill`, and a shared name
   would have silently styled the level bar with the progress bar's rules. */
.lmeter {
  margin-top: var(--s4);
}

.lmeter--idle {
  opacity: 0.45;
}

.lmeter__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--s2);
}

.lmeter__label {
  font-size: var(--t-2xs);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-400);
}

/* Tabular figures so the number does not jitter as its width changes. */
.lmeter__readout {
  font-family: var(--font-mono);
  font-size: var(--t-base);
  font-variant-numeric: tabular-nums;
  color: var(--ink-900);
  min-width: 3ch;
  text-align: right;
}

.lmeter__track {
  position: relative;
  height: 10px;
  border-radius: var(--r-full);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  overflow: hidden;
}

.lmeter__fill {
  height: 100%;
  border-radius: var(--r-full);
  background: var(--accent);
  transition: width 90ms linear;
}

.lmeter__fill--hot {
  background: var(--danger);
}

/* Peak hold. Sits on top of the fill, so a level that has already fallen still
   leaves a mark for a moment. */
.lmeter__hold {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: var(--ink-900);
  transition: left 90ms linear;
}

.lmeter__scale {
  display: flex;
  justify-content: space-between;
  margin-top: var(--s1);
  font-size: var(--t-2xs);
  color: var(--ink-400);
  font-variant-numeric: tabular-nums;
}

.linkish {
  border: 0;
  background: transparent;
  padding: 0;
  font-size: var(--t-sm);
  color: var(--ink-500);
  text-decoration: underline;
  text-decoration-color: var(--line-strong);
  text-underline-offset: 3px;
}

.linkish:hover:not(:disabled) {
  color: var(--ink-900);
  text-decoration-color: var(--ink-400);
}

.linkish:disabled {
  color: var(--ink-300);
  cursor: not-allowed;
}

.panels__link {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  margin-top: var(--s4);
  font-size: var(--t-sm);
  font-weight: 500;
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}

.panels__link:hover {
  border-bottom-color: var(--accent-line);
}

.field {
  display: block;
}

.field__input {
  width: 100%;
  margin-top: var(--s2);
  padding: 0 var(--s3);
  height: 38px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface);
  font-size: var(--t-sm);
  transition:
    border-color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease);
}

.field__input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-wash);
}

.field__input[aria-invalid='true'] {
  border-color: var(--danger-line);
}

.field__error {
  margin-top: var(--s2);
  font-size: var(--t-xs);
  color: var(--danger);
}

.meter {
  height: 6px;
  border-radius: var(--r-full);
  background: var(--line);
  overflow: hidden;
}

.meter__fill {
  display: block;
  height: 100%;
  background: var(--ink-400);
  border-radius: var(--r-full);
  transition: width var(--dur-slow) var(--ease);
}

.meter--done .meter__fill {
  background: var(--saved-fg);
}

.actionbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s6);
  flex-wrap: wrap;
  margin-top: var(--s6);
  padding: var(--s5) var(--s6);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
}

.actionbar--blocked {
  background: var(--surface-sunken);
}

.actionbar__text {
  max-width: 70ch;
  flex: 1;
  min-width: 320px;
}

.actionbar__title {
  font-size: var(--t-lg);
  font-weight: 500;
  margin-top: var(--s2);
  letter-spacing: -0.01em;
}

.actionbar__detail {
  font-size: var(--t-sm);
  color: var(--ink-500);
  margin-top: var(--s2);
}

.actionbar__buttons {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
}

/*
 * Gate checklist. Shown only when something is blocking, so on a healthy host
 * the home screen stays quiet.
 */
.checklist {
  list-style: none;
  margin: var(--s4) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.checklist li {
  display: grid;
  grid-template-columns: 16px 200px minmax(0, 1fr);
  gap: var(--s3);
  align-items: baseline;
  font-size: var(--t-sm);
}

.checklist__mark {
  color: var(--ink-300);
}

.checklist__item--met .checklist__mark {
  color: var(--saved-fg);
}

.checklist__label {
  color: var(--ink-700);
}

.checklist__detail {
  color: var(--ink-400);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.footnote {
  margin-top: var(--s5);
  font-size: var(--t-sm);
  color: var(--ink-400);
}
</style>
