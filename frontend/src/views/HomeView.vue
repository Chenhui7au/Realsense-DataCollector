<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
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
import { formatBytes } from '@/utils/format'
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
      <section class="card">
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

      <section class="card">
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
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s4);
  margin-top: var(--s7);
  align-items: start;
}

@media (max-width: 980px) {
  .panels {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* The project card carries an editable field, so it gets a little more room. */
.panels__project {
  grid-column: span 1;
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
