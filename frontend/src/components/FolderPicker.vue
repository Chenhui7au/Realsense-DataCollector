<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '@/api'
import type { FsListing } from '@/api'
import { describeError } from '@/api/errorText'
import { breadcrumb, checkFolderName } from '@/utils/paths'
import { formatBytes } from '@/utils/format'
import AppButton from './AppButton.vue'

const props = defineProps<{
  open: boolean
  /** Folder to open on, usually the current project root. */
  startPath: string | null
}>()

const emit = defineEmits<{
  select: [string]
  cancel: []
}>()

const listing = ref<FsListing | null>(null)
const current = ref<string | null>(null)
const loading = ref(false)
const showHidden = ref(false)
const error = ref<string | null>(null)

const creating = ref(false)
const newFolderName = ref('')
const newFolderError = ref<string | null>(null)
const newFolderBusy = ref(false)
const nameInput = ref<HTMLInputElement | null>(null)

const crumbs = computed(() => (listing.value ? breadcrumb(listing.value.path) : []))

const canSelect = computed(() => {
  const l = listing.value
  return l !== null && l.readable && l.error === null
})

/*
 * Why the current folder cannot be chosen, shown next to the Select button so
 * the reason is not buried in a tooltip.
 *
 * The service states this verbatim and its docstring asks the picker not to keep
 * a second copy of the rules. It did keep one, and left out the check that the
 * folder is not itself a volume root, so the button stayed enabled on a drive the
 * backend then refused with PROJECT_PATH_INVALID. Trusting `error` keeps the two
 * from drifting again.
 */
const blockReason = computed(() => {
  const l = listing.value
  if (!l) {
    return null
  }
  if (l.error) {
    return l.error
  }
  if (!l.readable) {
    return 'This folder cannot be read.'
  }
  return null
})

/*
 * Counts session folders from the listing itself rather than cross referencing
 * the session list, which also means the hint works on first run before any
 * session list has been fetched.
 */
const sessionCountHint = computed(() => {
  const here = listing.value?.entries.filter((e) => e.looks_like_session) ?? []
  if (here.length === 0) {
    return null
  }
  const names = here.slice(0, 3).map((e) => e.name)
  return `${here.length} session folde${here.length === 1 ? 'r' : 'rs'} already here: ${names.join(
    ', ',
  )}${here.length > 3 ? ' and more' : ''}`
})

/*
 * Named navigate rather than open on purpose. A setup binding called open would
 * shadow the open prop in the template, making v-if="open" always truthy and
 * leaving the dialog stuck on screen.
 */
async function navigate(path: string | null) {
  loading.value = true
  error.value = null
  try {
    listing.value = await api.listDirectory(path, showHidden.value)
    current.value = listing.value.path
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    loading.value = false
  }
}

function enter(entry: { path: string }) {
  void navigate(entry.path)
}

function goUp() {
  if (listing.value?.parent) {
    void navigate(listing.value.parent)
  }
}

function beginCreate() {
  creating.value = true
  newFolderName.value = ''
  newFolderError.value = null
  void nextTick(() => nameInput.value?.focus())
}

function cancelCreate() {
  creating.value = false
  newFolderName.value = ''
  newFolderError.value = null
}

async function confirmCreate() {
  const check = checkFolderName(newFolderName.value)
  if (!check.ok) {
    newFolderError.value = check.error
    return
  }
  if (!listing.value) {
    return
  }

  newFolderBusy.value = true
  newFolderError.value = null
  try {
    const result = await api.createDirectory(listing.value.path, check.value)
    listing.value = result.listing
    cancelCreate()
    // Landing in the folder that was just made is what the operator expects.
    await navigate(result.path)
  } catch (err) {
    newFolderError.value = describeError(err).message
  } finally {
    newFolderBusy.value = false
  }
}

function confirm() {
  if (canSelect.value && listing.value) {
    emit('select', listing.value.path)
  }
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open || event.key !== 'Escape' || newFolderBusy.value) {
    return
  }
  if (creating.value) {
    cancelCreate()
    return
  }
  emit('cancel')
}

// Escape is handled on the window, because the dialog itself never takes focus.
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// Reload whenever the dialog opens, and follow the caller's starting point.
watch(
  () => props.open,
  async (isOpen) => {
    if (isOpen) {
      creating.value = false
      showHidden.value = false
      await navigate(props.startPath)
    } else {
      listing.value = null
    }
  },
  { immediate: true },
)

watch(showHidden, () => void navigate(current.value))
</script>

<template>
  <div v-if="open" class="scrim fade-in" @click.self="!newFolderBusy && emit('cancel')">
    <div class="picker" role="dialog" aria-modal="true" aria-label="Choose project folder">
        <header class="picker__head">
          <div>
            <h2 class="picker__title">Choose project folder</h2>
            <p class="picker__sub">
              Browsing folders on the capture host, not on this computer.
            </p>
          </div>
          <button class="picker__x" aria-label="Close" @click="emit('cancel')">
            <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
              <path d="M2 2 L10 10 M10 2 L2 10" stroke="currentColor" stroke-width="1.6"
                    stroke-linecap="round" />
            </svg>
          </button>
        </header>

        <div class="picker__bar">
          <button class="navbtn" :disabled="!listing?.parent || loading" @click="goUp">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path d="M9.5 3.5 L4.5 8 L9.5 12.5" fill="none" stroke="currentColor"
                    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Up
          </button>

          <nav class="crumbs" aria-label="Current path">
            <template v-for="(crumb, i) in crumbs" :key="crumb.path">
              <span v-if="i > 0" class="crumbs__sep" aria-hidden="true">/</span>
              <button
                class="crumbs__item"
                :class="{ 'crumbs__item--here': i === crumbs.length - 1 }"
                :disabled="loading"
                @click="navigate(crumb.path)"
              >
                {{ crumb.name }}
              </button>
            </template>
          </nav>

          <label class="hidden">
            <input v-model="showHidden" type="checkbox" />
            <span>Hidden</span>
          </label>
        </div>

        <div class="picker__body">
          <aside class="side">
            <p class="label">Places</p>
            <button
              v-for="place in listing?.shortcuts ?? []"
              :key="place.path"
              class="side__item"
              :class="{ 'side__item--on': listing?.path === place.path }"
              :disabled="loading"
              @click="navigate(place.path)"
            >
              {{ place.name }}
            </button>
          </aside>

          <div class="list">
            <div v-if="loading" class="list__state">
              <span class="spin" aria-hidden="true" />
              <span>Reading folder</span>
            </div>

            <div v-else-if="error" class="list__state list__state--bad">
              <p>{{ error }}</p>
              <AppButton size="sm" variant="secondary" @click="navigate(current)">Retry</AppButton>
            </div>

            <div v-else-if="!listing?.readable" class="list__state list__state--bad">
              <p>This folder cannot be read.</p>
            </div>

            <div v-else-if="listing.entries.length === 0" class="list__state">
              <p>No subfolders here.</p>
              <p v-if="canSelect" class="list__hint">You can select this folder, or create one inside it.</p>
              <p v-else class="list__hint">Create a folder inside it to use as the project folder.</p>
            </div>

            <ul v-else class="list__items">
              <li v-for="entry in listing.entries" :key="entry.path">
                <button
                  class="row"
                  :class="{ 'row--blocked': !entry.writable }"
                  :title="entry.path"
                  @click="enter(entry)"
                >
                  <svg class="row__icon" viewBox="0 0 18 16" width="16" height="16" aria-hidden="true">
                    <path
                      d="M1.5 3.2 A1.2 1.2 0 0 1 2.7 2 H6.4 L8 3.9 H15.3 A1.2 1.2 0 0 1 16.5 5.1 V12.3 A1.2 1.2 0 0 1 15.3 13.5 H2.7 A1.2 1.2 0 0 1 1.5 12.3 Z"
                      fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"
                    />
                  </svg>
                  <span class="row__name">{{ entry.name }}</span>
                  <span v-if="entry.looks_like_session" class="row__tag row__tag--session">
                    session
                  </span>
                  <span v-if="entry.is_symlink" class="row__tag">link</span>
                  <span v-if="!entry.writable" class="row__tag row__tag--bad">read only</span>
                  <svg class="row__go" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                    <path d="M6.5 3.5 L11.5 8 L6.5 12.5" fill="none" stroke="currentColor"
                          stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </li>
            </ul>
          </div>
        </div>

        <!-- Create folder -->
        <div v-if="creating" class="creator">
          <label class="field">
            <span class="label">New folder name</span>
            <input
              ref="nameInput"
              v-model="newFolderName"
              class="field__input mono"
              type="text"
              autocomplete="off"
              spellcheck="false"
              placeholder="Project_A"
              :disabled="newFolderBusy"
              @input="newFolderError = null"
              @keydown.enter="confirmCreate"
              @keydown.esc="cancelCreate"
            />
          </label>
          <p v-if="newFolderError" class="field__error">{{ newFolderError }}</p>
          <div class="creator__actions">
            <AppButton size="sm" variant="ghost" :disabled="newFolderBusy" @click="cancelCreate">
              Cancel
            </AppButton>
            <AppButton size="sm" variant="primary" :loading="newFolderBusy" @click="confirmCreate">
              Create
            </AppButton>
          </div>
        </div>

        <footer class="picker__foot">
          <div class="selection">
            <p class="label">Selected folder</p>
            <p class="selection__path mono">{{ listing?.path ?? '--' }}</p>
            <p v-if="blockReason" class="selection__bad">{{ blockReason }}</p>
            <p v-else-if="sessionCountHint" class="selection__hint">{{ sessionCountHint }}</p>
            <p v-else-if="listing" class="selection__hint">
              {{ formatBytes(listing.free_gb * 1024 ** 3) }} free on this volume
            </p>
          </div>

          <div class="picker__actions">
            <AppButton size="sm" variant="secondary" :disabled="creating || loading" @click="beginCreate">
              New folder
            </AppButton>
            <AppButton variant="ghost" @click="emit('cancel')">Cancel</AppButton>
            <AppButton variant="primary" :disabled="!canSelect" @click="confirm">
              Select this folder
            </AppButton>
          </div>
        </footer>
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

.picker {
  width: min(760px, 100%);
  max-height: min(640px, calc(100vh - var(--s8)));
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}

.picker__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s4);
  padding: var(--s5) var(--s5) var(--s4);
}

.picker__title {
  font-family: var(--font-display);
  font-size: var(--t-xl);
  font-weight: 400;
  letter-spacing: -0.01em;
}

.picker__sub {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-500);
}

.picker__x {
  border: 0;
  background: transparent;
  color: var(--ink-400);
  padding: var(--s2);
  border-radius: var(--r-xs);
  flex: none;
}

.picker__x:hover {
  background: var(--surface-hover);
  color: var(--ink-900);
}

/* --- path bar --- */

.picker__bar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) var(--s5);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: var(--surface-sunken);
}

.navbtn {
  display: inline-flex;
  align-items: center;
  gap: var(--s1);
  border: 1px solid var(--line-strong);
  background: var(--surface);
  border-radius: var(--r-sm);
  padding: 4px var(--s3);
  font-size: var(--t-xs);
  font-weight: 500;
  flex: none;
}

.navbtn:disabled {
  color: var(--ink-300);
  cursor: not-allowed;
}

/* Breadcrumb wraps rather than scrolls, so the path is always fully visible. */
.crumbs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.crumbs__item {
  border: 0;
  background: transparent;
  border-radius: var(--r-xs);
  padding: 2px var(--s2);
  font-family: var(--font-mono);
  font-size: var(--t-xs);
  color: var(--ink-700);
  max-width: 16ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crumbs__item:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--ink-900);
}

.crumbs__item--here {
  color: var(--ink-900);
  font-weight: 500;
}

.crumbs__sep {
  color: var(--ink-300);
  font-size: var(--t-xs);
}

.hidden {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--t-xs);
  color: var(--ink-500);
  flex: none;
}

/* --- body --- */

.picker__body {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  min-height: 260px;
  flex: 1;
  overflow: hidden;
}

.side {
  border-right: 1px solid var(--line);
  padding: var(--s4) var(--s3);
  background: var(--surface-sunken);
  overflow-y: auto;
}

.side__item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  border-radius: var(--r-xs);
  padding: var(--s2) var(--s3);
  font-size: var(--t-sm);
  color: var(--ink-700);
  margin-top: 2px;
}

.side__item:hover:not(:disabled) {
  background: var(--surface-hover);
}

.side__item--on {
  background: var(--accent-wash);
  color: var(--accent);
  font-weight: 500;
}

.list {
  overflow-y: auto;
  padding: var(--s3);
}

.list__items {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: var(--s3);
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: var(--r-sm);
  padding: var(--s2) var(--s3);
  min-height: 34px;
}

.row:hover {
  background: var(--surface-hover);
  border-color: var(--line);
}

.row__icon {
  color: var(--accent);
  flex: none;
  opacity: 0.75;
}

.row__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--t-base);
}

.row__tag {
  font-size: var(--t-2xs);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-400);
  background: var(--idle-bg);
  border-radius: var(--r-full);
  padding: 1px var(--s2);
  flex: none;
}

.row__tag--bad {
  color: var(--danger);
  background: var(--danger-bg);
}

.row__tag--session {
  color: var(--accent);
  background: var(--accent-wash);
}

.row__go {
  color: var(--ink-300);
  flex: none;
}

.row--blocked .row__name {
  color: var(--ink-400);
}

.list__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s3);
  height: 100%;
  min-height: 200px;
  text-align: center;
  font-size: var(--t-sm);
  color: var(--ink-500);
}

.list__state--bad {
  color: var(--danger);
}

.list__hint {
  color: var(--ink-400);
  font-size: var(--t-xs);
}

.spin {
  width: 18px;
  height: 18px;
  border: 2px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: var(--r-full);
  animation: spin 680ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* --- create --- */

.creator {
  padding: var(--s4) var(--s5);
  border-top: 1px solid var(--line);
  background: var(--surface-sunken);
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
  font-size: var(--t-base);
}

.field__input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-wash);
}

.field__error {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--danger);
}

.creator__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--s2);
  margin-top: var(--s3);
}

/* --- footer --- */

.picker__foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--s5);
  flex-wrap: wrap;
  padding: var(--s4) var(--s5);
  border-top: 1px solid var(--line);
}

.selection {
  min-width: 0;
  flex: 1;
}

.selection__path {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-900);
  overflow-wrap: anywhere;
}

.selection__hint {
  margin-top: var(--s2);
  font-size: var(--t-xs);
  color: var(--ink-400);
}

.selection__bad {
  margin-top: var(--s2);
  font-size: var(--t-xs);
  color: var(--danger);
}

.picker__actions {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--dur) var(--ease);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 620px) {
  .picker__body {
    grid-template-columns: minmax(0, 1fr);
  }

  .side {
    display: none;
  }
}
</style>
