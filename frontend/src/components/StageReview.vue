<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api'
import type { Stage, StageVerdict } from '@/api'
import { describeError } from '@/api/errorText'
import { useSession } from '@/composables/useSession'
import { useToast } from '@/composables/useToast'
import { formatDateTime } from '@/utils/format'

/*
 * The verdict gates advance on the backend too: a saved stage only carries
 * `advance` in `allowed_actions` once this has stored a passed or failed value.
 * So the button here is not the authority, it is the way to satisfy the gate.
 *
 * Nothing is updated optimistically. The selected option is read from the stage
 * the server returned, which means a rejected save leaves the previous choice in
 * place instead of showing a verdict that was never stored.
 */
const props = defineProps<{ stage: Stage; maxLength: number }>()

const { session, setSession } = useSession()
const toast = useToast()

const verdict = computed<StageVerdict | null>(() => props.stage.review?.verdict ?? null)
const serverComment = computed(() => props.stage.review?.comment ?? '')

const draft = ref(serverComment.value)
const dirty = ref(false)
const saving = ref(false)

const trimmed = computed(() => draft.value.trim())
const overLong = computed(() => trimmed.value.length > props.maxLength)

const OPTIONS: { value: StageVerdict; label: string }[] = [
  { value: 'passed', label: 'Passed' },
  { value: 'failed', label: 'Failed' },
]

/*
 * Saves are serialised. The verdict is sent on click and the comment on blur, so
 * a fast pass-fail-pass or a blur landing while a click is still in flight would
 * otherwise race and the older request could win on arrival.
 */
let queue: Promise<unknown> = Promise.resolve()

function persist(next: StageVerdict, comment: string): Promise<void> {
  const run = async (): Promise<void> => {
    const sid = session.value?.session_id
    if (!sid) {
      throw new Error('No active session.')
    }
    saving.value = true
    try {
      const result = await api.saveReview(sid, props.stage.index, {
        verdict: next,
        comment,
      })
      setSession(result.session)
      // Only clear the dirty flag when the draft has not moved on since the
      // request was built, otherwise the next keystroke would be treated as
      // already saved.
      if (draft.value.trim() === comment) {
        dirty.value = false
      }
    } finally {
      saving.value = false
    }
  }

  // `then(run, run)` keeps the queue moving after a failure, while the promise
  // handed back to the caller still rejects so a flush can report it.
  const attempt = queue.then(run, run)
  queue = attempt.catch(() => undefined)
  return attempt
}

async function choose(next: StageVerdict): Promise<void> {
  if (saving.value && verdict.value === next) {
    return
  }
  try {
    await persist(next, trimmed.value)
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not save the review', message)
  }
}

/**
 * The comment alone is never sent without a verdict, because the endpoint
 * requires one. Typing before choosing is fine; it goes up with the first click.
 */
async function saveComment(): Promise<void> {
  if (!verdict.value || !dirty.value || overLong.value) {
    return
  }
  const text = trimmed.value
  if (text === serverComment.value) {
    return
  }
  try {
    await persist(verdict.value, text)
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not save the comment', message)
  }
}

watch(serverComment, (next) => {
  if (!dirty.value) {
    draft.value = next
  }
})

// The capture screen is reused across stage indexes, so a stale draft must not
// follow the collector into the next stage.
watch(
  () => props.stage.index,
  () => {
    dirty.value = false
    draft.value = serverComment.value
  },
)

function onInput(): void {
  dirty.value = true
}

/** Called before advancing, so a comment typed and not blurred is not lost. */
async function flush(): Promise<boolean> {
  if (!verdict.value || !dirty.value) {
    return true
  }
  if (overLong.value) {
    return false
  }
  const text = trimmed.value
  if (text === serverComment.value) {
    return true
  }
  try {
    await persist(verdict.value, text)
    return true
  } catch (error) {
    const { message } = describeError(error)
    toast.danger('Could not save the comment', message)
    return false
  }
}

defineExpose({ flush })
</script>

<template>
  <section class="rev" aria-labelledby="rev-title">
    <div class="rev__head">
      <p id="rev-title" class="label">Take review</p>
      <span v-if="saving" class="rev__saving">Saving</span>
    </div>

    <div class="rev__choices" role="radiogroup" aria-label="Take review verdict">
      <button
        v-for="option in OPTIONS"
        :key="option.value"
        type="button"
        role="radio"
        :aria-checked="verdict === option.value"
        class="rev__choice"
        :class="[
          `rev__choice--${option.value}`,
          { 'rev__choice--on': verdict === option.value },
        ]"
        @click="choose(option.value)"
      >
        {{ option.label }}
      </button>
    </div>

    <p v-if="!verdict" class="rev__hint">
      Choose Passed or Failed to unlock the next stage.
    </p>
    <p v-else class="rev__hint">
      Marked {{ verdict }}<template v-if="stage.review?.reviewed_at">
        at {{ formatDateTime(stage.review.reviewed_at) }}</template>. Re-recording
      this take clears the verdict.
    </p>

    <label class="rev__label" :for="`rev-note-${stage.index}`">
      Comment <span class="rev__optional">optional</span>
    </label>
    <textarea
      :id="`rev-note-${stage.index}`"
      v-model="draft"
      class="rev__note"
      rows="3"
      :maxlength="maxLength"
      placeholder="Anything worth recording about this take, for example glare, motion blur, or the subject shifting."
      @input="onInput"
      @blur="saveComment"
    />
    <span class="rev__count" :class="{ 'rev__count--over': overLong }">
      {{ trimmed.length }} / {{ maxLength }}
    </span>
  </section>
</template>

<style scoped>
.rev {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--s4) var(--s5) var(--s5);
}

.rev__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s3);
}

.rev__saving {
  font-size: var(--t-2xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-400);
}

.rev__choices {
  display: flex;
  gap: var(--s2);
}

/*
 * Two options of equal weight. The colour arrives only once one is chosen, so the
 * panel does not suggest a preferred answer before the operator has looked at the
 * take.
 */
.rev__choice {
  flex: 1;
  padding: var(--s2) var(--s3);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink-500);
  font-size: var(--t-sm);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  transition:
    background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.rev__choice:hover {
  background: var(--surface-hover);
}

.rev__choice--passed.rev__choice--on {
  background: var(--saved-bg);
  border-color: var(--saved-fg);
  color: var(--saved-fg);
}

.rev__choice--failed.rev__choice--on {
  background: var(--danger-bg);
  border-color: var(--danger);
  color: var(--danger);
}

.rev__hint {
  font-size: var(--t-xs);
  color: var(--ink-400);
  line-height: 1.5;
}

.rev__label {
  margin-top: var(--s2);
  font-size: var(--t-2xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-400);
}

.rev__optional {
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
}

.rev__note {
  width: 100%;
  min-height: 64px;
  padding: var(--s2) var(--s3);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface-sunken);
  color: var(--ink-900);
  font-size: var(--t-sm);
  line-height: 1.5;
  resize: vertical;
}

.rev__note:focus {
  background: var(--surface);
}

.rev__count {
  align-self: flex-end;
  font-family: var(--font-mono);
  font-size: var(--t-2xs);
  color: var(--ink-400);
}

.rev__count--over {
  color: var(--danger);
}
</style>
