<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import AppButton from '@/components/AppButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import NewSessionDialog from '@/components/NewSessionDialog.vue'
import StageResultGrid from '@/components/StageResultGrid.vue'
import { useApp } from '@/composables/useApp'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { describeError } from '@/api/errorText'
import { formatBytes, formatDateTime, formatSeconds } from '@/utils/format'

const router = useRouter()
const toast = useToast()
const { totalStages, project, refreshHealth } = useApp()
const { session, stages, create, savedCount, discard } = useSession()

const starting = ref(false)
const copied = ref(false)
const discardOpen = ref(false)
const discarding = ref(false)

/* Starting the next session requires a name, so the same dialog is reused. */
const newSessionOpen = ref(false)
const existingNames = ref<string[]>([])
const sessionError = ref<string | null>(null)

const recorded = computed(() => stages.value.filter((s) => s.artifact))

const totals = computed(() => {
  const bytes = recorded.value.reduce((sum, s) => sum + (s.artifact?.size_bytes ?? 0), 0)
  const seconds = recorded.value.reduce((sum, s) => sum + (s.artifact?.duration_s ?? 0), 0)
  return { bytes, seconds }
})

const complete = computed(() => savedCount.value === totalStages.value)

onMounted(() => {
  // Landing here without a finished session means the flow is out of order.
  if (session.value && session.value.status !== 'finished') {
    void router.replace({ name: 'home' })
  }
})

async function openNewSession() {
  sessionError.value = null
  try {
    const listed = await api.listSessions()
    // The session just finished is already on disk, so its name is taken.
    existingNames.value = listed.sessions.map((s) => s.name)
  } catch {
    existingNames.value = []
  }
  newSessionOpen.value = true
}

async function onStartNew(name: string) {
  starting.value = true
  sessionError.value = null
  try {
    const created = await create({ name })
    if (created) {
      newSessionOpen.value = false
      toast.success(`Session ${created.name} created`, created.data_dir)
      await refreshHealth()
      await router.push({ name: 'guide', params: { index: '1' } })
    }
  } catch (error) {
    sessionError.value = describeError(error).message
  } finally {
    starting.value = false
  }
}

async function onDiscard() {
  if (!session.value) return
  discarding.value = true
  try {
    const done = await discard(session.value.session_id)
    if (done) {
      discardOpen.value = false
      toast.info('Session deleted', 'The recorded files were removed from the host.')
      await router.push({ name: 'home' })
    }
  } finally {
    discarding.value = false
  }
}

async function copyPath() {
  if (!session.value) return
  try {
    await navigator.clipboard.writeText(session.value.data_dir)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 2000)
  } catch {
    toast.info('Copy the path manually', session.value.data_dir)
  }
}
</script>

<template>
  <div class="page shell">
    <header class="head">
      <div>
        <p class="label">Session complete</p>
        <h1 class="display">
          {{ complete ? 'All stages recorded' : 'Session closed' }}
        </h1>
        <p class="page-lede">
          <span class="mono">{{ session?.name ?? '--' }}</span>
          <template v-if="complete">
            holds {{ totalStages }} bag files, ready to collect.
          </template>
          <template v-else>
            closed with {{ savedCount }} of {{ totalStages }} stages saved.
          </template>
        </p>
      </div>

      <div class="head__actions">
        <AppButton variant="ghost" @click="discardOpen = true">Delete session</AppButton>
        <AppButton variant="primary" size="lg" @click="openNewSession">
          Start new session
        </AppButton>
      </div>
    </header>

    <dl class="summary">
      <div>
        <dt class="label">Stages saved</dt>
        <dd class="mono-lg">{{ savedCount }} <span class="dim">/ {{ totalStages }}</span></dd>
      </div>
      <div>
        <dt class="label">Total duration</dt>
        <dd class="mono-lg">{{ formatSeconds(totals.seconds) }}</dd>
      </div>
      <div>
        <dt class="label">Total size</dt>
        <dd class="mono-lg">{{ formatBytes(totals.bytes) }}</dd>
      </div>
      <div>
        <dt class="label">Finished</dt>
        <dd class="mono-lg summary__stamp">{{ formatDateTime(session?.finished_at) }}</dd>
      </div>
    </dl>

    <section class="files">
      <div class="files__bar">
        <div>
          <p class="label">Session identifier</p>
          <p class="mono files__id">{{ session?.session_id ?? '--' }}</p>
        </div>
        <div class="files__path">
          <p class="label">Data directory</p>
          <p class="mono files__dir" :title="session?.data_dir">{{ session?.data_dir ?? '--' }}</p>
        </div>
        <AppButton size="sm" variant="secondary" @click="copyPath">
          {{ copied ? 'Copied' : 'Copy path' }}
        </AppButton>
      </div>

      <StageResultGrid :stages="stages" />
    </section>

    <p class="footnote">
      Stage diagrams stay configured for the next session. Recordings are cleared only when you
      delete a session.
    </p>

    <NewSessionDialog
      :open="newSessionOpen"
      :project="project"
      :existing-names="existingNames"
      :busy="starting"
      :server-error="sessionError"
      @cancel="newSessionOpen = false"
      @create="onStartNew"
    />

    <ConfirmDialog
      :open="discardOpen"
      tone="danger"
      title="Delete this session?"
      :message="`The folder ${session?.data_dir ?? ''} and every bag file in it will be removed from the host. This cannot be undone.`"
      confirm-label="Delete session"
      :busy="discarding"
      @cancel="discardOpen = false"
      @confirm="onDiscard"
    />
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--s6);
  flex-wrap: wrap;
}

.head__actions {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
}

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--s5);
  margin: var(--s7) 0 var(--s6);
  padding: var(--s5);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}

@media (max-width: 800px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.summary dt {
  color: var(--ink-400);
}

.summary dd {
  margin: var(--s2) 0 0;
  color: var(--ink-900);
}

.summary__stamp {
  font-size: var(--t-base);
}

.files__bar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--s5);
  flex-wrap: wrap;
  padding: var(--s4) 0;
  margin-bottom: var(--s5);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.files__id {
  margin-top: var(--s2);
  font-size: var(--t-base);
}

.files__path {
  flex: 1;
  min-width: 240px;
  text-align: right;
}

.files__dir {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-500);
  overflow-wrap: anywhere;
}

@media (max-width: 800px) {
  .files__path {
    text-align: left;
  }
}

.footnote {
  margin-top: var(--s6);
  font-size: var(--t-sm);
  color: var(--ink-400);
}
</style>
