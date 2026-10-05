<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import AppButton from './AppButton.vue'
import { checkSessionName, separatorFor, sessionDirFor, suggestSessionName } from '@/utils/paths'
import type { ProjectInfo } from '@/api'

const props = defineProps<{
  open: boolean
  project: ProjectInfo | null
  /** Folder names already present in the project, for the collision check. */
  existingNames: string[]
  busy: boolean
  /** Error returned by the host, shown after the local checks pass. */
  serverError: string | null
}>()

const emit = defineEmits<{
  create: [string]
  cancel: []
}>()

const name = ref('')
const input = ref<HTMLInputElement | null>(null)
const touched = ref(false)

/*
 * Local validation mirrors the host rules so the operator sees a problem
 * immediately. The host repeats every check, because this is a convenience and
 * not a control.
 */
const check = computed(() => checkSessionName(name.value, props.existingNames))

/** Only surface the error once the operator has typed something. */
const error = computed(() => {
  if (props.serverError) {
    return props.serverError
  }
  if (!touched.value && name.value.length === 0) {
    return null
  }
  return check.value.error
})

const destination = computed(() => {
  const root = props.project?.root
  if (!root || !name.value.trim()) {
    return null
  }
  const dir = sessionDirFor(root, check.value.value || name.value.trim())
  return `${dir}${separatorFor(dir)}`
})

const canCreate = computed(() => check.value.ok && !props.busy)

// Reset and suggest a free name each time the dialog opens.
watch(
  () => props.open,
  (open) => {
    if (open) {
      name.value = suggestSessionName(props.existingNames)
      touched.value = false
      void nextTick(() => {
        input.value?.focus()
        input.value?.select()
      })
    }
  },
  { immediate: true },
)

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && !props.busy) {
    emit('cancel')
  }
  if (event.key === 'Enter' && canCreate.value) {
    emit('create', check.value.value)
  }
}
</script>

<template>
  <div v-if="open" class="scrim fade-in" @click.self="!busy && emit('cancel')">
    <div class="sheet" role="dialog" aria-modal="true" aria-label="New session">
      <h2 class="sheet__title">New session</h2>
      <p class="sheet__msg">
        Name this session. A folder with that name is created inside the project folder and every
        recording for this session is written there.
      </p>

      <label class="field">
        <span class="label">Session name</span>
        <input
          ref="input"
          v-model="name"
          class="field__input mono"
          type="text"
          autocomplete="off"
          spellcheck="false"
          placeholder="session_a"
          :aria-invalid="Boolean(error)"
          :disabled="busy"
          @input="touched = true"
          @keydown="onKeydown"
        />
      </label>

      <p v-if="error" class="field__error">{{ error }}</p>

      <div v-if="destination" class="dest">
        <p class="label">Recordings will be written to</p>
        <p class="dest__path mono">{{ destination }}</p>
      </div>

      <div class="sheet__actions">
        <AppButton variant="ghost" :disabled="busy" @click="emit('cancel')">Cancel</AppButton>
        <AppButton
          variant="primary"
          :disabled="!canCreate"
          :loading="busy"
          @click="emit('create', check.value)"
        >
          Create session
        </AppButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: var(--s5);
  background: rgba(20, 24, 27, 0.42);
  backdrop-filter: blur(2px);
}

.sheet {
  width: min(560px, 100%);
  background: var(--surface);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-3);
  padding: var(--s6);
}

.sheet__title {
  font-family: var(--font-display);
  font-size: var(--t-xl);
  font-weight: 400;
  letter-spacing: -0.01em;
}

.sheet__msg {
  margin-top: var(--s3);
  color: var(--ink-500);
  font-size: var(--t-base);
}

.field {
  display: block;
  margin-top: var(--s5);
}

.field__input {
  width: 100%;
  margin-top: var(--s2);
  padding: 0 var(--s3);
  height: 44px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface);
  font-size: var(--t-md);
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
  font-size: var(--t-sm);
  color: var(--danger);
}

/*
 * The destination preview is the point of the whole screen. Seeing the exact
 * folder before creating it is what makes the layout predictable later.
 */
.dest {
  margin-top: var(--s4);
  padding: var(--s3) var(--s4);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}

.dest__path {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-700);
  overflow-wrap: anywhere;
}

.sheet__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--s3);
  margin-top: var(--s6);
}
</style>
