<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GuideEntry } from '@/api'
import { formatBytes, formatDateTime, stageNumber } from '@/utils/format'
import AppButton from './AppButton.vue'

const props = defineProps<{
  entry: GuideEntry
  imageUrl: string
  busy: boolean
  error?: string | null
  highlight?: boolean
  maxLength: number
}>()

const emit = defineEmits<{ pick: [File]; remove: []; describe: [string] }>()

const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const draft = ref(props.entry.instructions)

/*
 * The server is the source of truth for the stored text, so the draft follows
 * every refresh. That also means a rejected save is reflected rather than left
 * showing wording that was never accepted.
 */
watch(
  () => props.entry.instructions,
  (value) => {
    draft.value = value
  },
)

const configured = computed(() => props.entry.configured)
const customised = computed(() => props.entry.instructions_custom)
const trimmed = computed(() => draft.value.trim())
const dirty = computed(() => trimmed.value !== props.entry.instructions)
const empty = computed(() => trimmed.value.length === 0)
const overLong = computed(() => trimmed.value.length > props.maxLength)

function openPicker() {
  if (!props.busy) {
    input.value?.click()
  }
}

function onFileChosen(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    emit('pick', file)
  }
  // Reset so picking the same file twice still fires a change event.
  target.value = ''
}

function onDrop(event: DragEvent) {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    emit('pick', file)
  }
}

function save() {
  if (dirty.value && !overLong.value) {
    emit('describe', trimmed.value)
  }
}

/** Clears the override so the stage falls back to the wording in the config. */
function reset() {
  draft.value = ''
  emit('describe', '')
}

/** Reverts an unsaved edit without touching what is stored. */
function undo() {
  draft.value = props.entry.instructions
}
</script>

<template>
  <article
    class="gcard"
    :class="{
      'gcard--empty': !configured,
      'gcard--busy': busy,
      'gcard--error': Boolean(error),
      'gcard--highlight': highlight && !configured,
      'gcard--dragging': dragging,
    }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <header class="gcard__head">
      <span class="gcard__no mono">{{ stageNumber(entry.index) }}</span>
      <span v-if="configured" class="gcard__tick" aria-label="Configured">
        <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
          <path d="M2.2 6.4 L4.8 9 L9.8 3.4" fill="none" stroke="currentColor" stroke-width="1.8"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
    </header>

    <h3 class="gcard__name">{{ entry.name }}</h3>

    <button
      type="button"
      class="gcard__stage"
      :disabled="busy"
      :aria-label="configured ? `Replace the diagram for ${entry.name}` : `Upload a diagram for ${entry.name}`"
      @click="openPicker"
    >
      <img v-if="configured && imageUrl" :src="imageUrl" :alt="`Pose diagram for ${entry.name}`" />
      <span v-else class="gcard__prompt">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="1.5"
                stroke-linecap="round" />
        </svg>
        <span>Drop an image or click to browse</span>
      </span>
      <span v-if="busy" class="gcard__spin" aria-hidden="true" />
    </button>

    <dl class="gcard__meta">
      <template v-if="configured">
        <div>
          <dt>File</dt>
          <dd :title="entry.original_filename ?? ''">{{ entry.original_filename }}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>
            <span class="mono">{{ formatBytes(entry.size_bytes) }}</span>
            <template v-if="entry.width && entry.height">
              <span class="dim"> &middot; </span>
              <span class="mono">{{ entry.width }}&times;{{ entry.height }}</span>
            </template>
          </dd>
        </div>
        <div>
          <dt>Uploaded</dt>
          <dd class="mono">{{ formatDateTime(entry.uploaded_at) }}</dd>
        </div>
      </template>
      <template v-else>
        <div>
          <dt>Status</dt>
          <dd class="dim">Not configured</dd>
        </div>
      </template>
    </dl>

    <p v-if="error" class="gcard__error">{{ error }}</p>

    <div class="gcard__desc">
      <div class="gcard__desc-head">
        <label :for="`guide-desc-${entry.index}`">Description</label>
        <span v-if="customised" class="gcard__tag">Custom</span>
        <span v-else class="gcard__tag gcard__tag--default">From config</span>
      </div>
      <textarea
        :id="`guide-desc-${entry.index}`"
        v-model="draft"
        class="gcard__desc-input"
        rows="4"
        :maxlength="maxLength"
        :disabled="busy"
        placeholder="What the collector should do for this stage."
        @keydown.ctrl.enter.prevent="save"
      />
      <div class="gcard__desc-foot">
        <span class="gcard__count" :class="{ 'gcard__count--over': overLong }">
          {{ trimmed.length }} / {{ maxLength }}
        </span>
        <span class="gcard__desc-actions">
          <AppButton v-if="dirty" size="sm" variant="ghost" :disabled="busy" @click="undo">
            Undo
          </AppButton>
          <AppButton
            v-if="customised"
            size="sm"
            variant="ghost"
            :disabled="busy || dirty || empty"
            @click="reset"
          >
            Reset
          </AppButton>
          <AppButton
            size="sm"
            variant="secondary"
            :disabled="busy || !dirty || overLong"
            @click="save"
          >
            Save
          </AppButton>
        </span>
      </div>
      <p v-if="overLong" class="gcard__error">
        Keep the description to {{ maxLength }} characters or fewer.
      </p>
    </div>

    <footer class="gcard__foot">
      <AppButton size="sm" variant="secondary" :disabled="busy" @click="openPicker">
        {{ configured ? 'Replace' : 'Upload' }}
      </AppButton>
      <AppButton
        v-if="configured"
        size="sm"
        variant="ghost"
        :disabled="busy"
        @click="emit('remove')"
      >
        Remove
      </AppButton>
    </footer>

    <input
      ref="input"
      class="gcard__input"
      type="file"
      accept="image/png,image/jpeg,image/webp,image/bmp,image/gif"
      @change="onFileChosen"
    />
  </article>
</template>

<style scoped>
.gcard {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--s4);
  transition:
    border-color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease);
}

.gcard--highlight {
  border-color: var(--accent-line);
  box-shadow: 0 0 0 3px var(--accent-wash);
}

.gcard--dragging {
  border-color: var(--accent);
  background: var(--accent-wash);
}

.gcard--error {
  border-color: var(--danger-line);
}

.gcard--busy {
  opacity: 0.7;
}

.gcard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.gcard__no {
  font-size: var(--t-2xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-400);
  font-weight: 600;
}

.gcard__tick {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: var(--r-full);
  background: var(--saved-bg);
  color: var(--saved-fg);
}

.gcard__name {
  font-size: var(--t-base);
  font-weight: 600;
  margin-top: var(--s2);
  line-height: 1.3;
  min-height: 2.6em;
}

.gcard__stage {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 4 / 3;
  width: 100%;
  margin: var(--s3) 0;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-sunken);
}

.gcard--empty .gcard__stage {
  border-style: dashed;
  border-color: var(--line-strong);
}

.gcard__stage:not(:disabled):hover {
  border-color: var(--accent);
}

.gcard__stage img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.gcard__prompt {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3);
  text-align: center;
  font-size: var(--t-xs);
  color: var(--ink-400);
  line-height: 1.35;
}

.gcard__spin {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.66);
  display: grid;
  place-items: center;
}

.gcard__spin::after {
  content: '';
  width: 18px;
  height: 18px;
  border: 2px solid var(--ink-300);
  border-top-color: var(--accent);
  border-radius: var(--r-full);
  animation: gcard-spin 680ms linear infinite;
}

@keyframes gcard-spin {
  to {
    transform: rotate(360deg);
  }
}

.gcard__meta {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
  font-size: var(--t-xs);
  margin-bottom: var(--s4);
  flex: 1;
}

.gcard__meta > div {
  display: flex;
  gap: var(--s2);
  align-items: baseline;
}

.gcard__meta dt {
  color: var(--ink-400);
  flex: none;
  width: 58px;
}

.gcard__meta dd {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-700);
}

.gcard__error {
  font-size: var(--t-xs);
  color: var(--danger);
  background: var(--danger-bg);
  border-radius: var(--r-xs);
  padding: var(--s2) var(--s3);
  margin-bottom: var(--s3);
}

.gcard__desc {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  margin-bottom: var(--s4);
}

.gcard__desc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
  font-size: var(--t-xs);
  color: var(--ink-400);
}

.gcard__tag {
  font-size: var(--t-2xs);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: var(--r-xs);
  background: var(--saved-bg);
  color: var(--saved-fg);
}

.gcard__tag--default {
  background: var(--surface-sunken);
  color: var(--ink-400);
}

.gcard__desc-input {
  width: 100%;
  resize: vertical;
  min-height: 72px;
  padding: var(--s2) var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  color: var(--ink-900);
  font-family: inherit;
  font-size: var(--t-xs);
  line-height: 1.45;
}

.gcard__desc-input:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-wash);
}

.gcard__desc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
}

.gcard__desc-actions {
  display: flex;
  gap: var(--s1);
}

.gcard__count {
  font-family: var(--font-mono);
  font-size: var(--t-2xs);
  color: var(--ink-300);
}

.gcard__count--over {
  color: var(--danger);
}

.gcard__foot {
  display: flex;
  gap: var(--s2);
  align-items: center;
}

.gcard__input {
  display: none;
}
</style>
