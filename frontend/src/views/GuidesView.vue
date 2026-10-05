<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import GuideCard from '@/components/GuideCard.vue'
import AppButton from '@/components/AppButton.vue'
import { useGuides, guideImageUrl } from '@/composables/useGuides'
import { useToast } from '@/composables/useToast'

const toast = useToast()
const {
  guides,
  byIndex,
  ready,
  uploaded,
  total,
  progress,
  busyIndex,
  batchBusy,
  load,
  upload,
  uploadBatch,
  saveInstructions,
  remove,
  inferStageFromFilename,
} = useGuides()

/** Mirrors guides.instructions_max_length in the shipped YAML. */
const INSTRUCTIONS_MAX_LENGTH = 500

const cardErrors = ref(new Map<number, string>())
const pageDragging = ref(false)

const entries = computed(() => guides.value?.guides ?? [])
const missingCount = computed(() => total.value - uploaded.value)

onMounted(() => void load())

async function onCardPick(index: number, file: File) {
  // Clear the previous failure, and keep the map identity stable for reactivity.
  const next = new Map(cardErrors.value)
  next.delete(index)
  cardErrors.value = next

  const result = await upload(index, file)
  if (!result.ok && result.message) {
    const failed = new Map(cardErrors.value)
    failed.set(index, result.message)
    cardErrors.value = failed
  }
}

async function onCardDescribe(index: number, text: string) {
  const next = new Map(cardErrors.value)
  next.delete(index)
  cardErrors.value = next

  const result = await saveInstructions(index, text)
  if (!result.ok && result.message) {
    const failed = new Map(cardErrors.value)
    failed.set(index, result.message)
    cardErrors.value = failed
  }
}

async function onCardRemove(index: number) {
  const next = new Map(cardErrors.value)
  next.delete(index)
  cardErrors.value = next
  await remove(index)
}

/*
 * A single dropped file is ambiguous, so the collector is asked to drop it on
 * the card it belongs to. Multiple files are matched by the number in the file
 * name, and anything unmatched is reported rather than guessed at.
 */
async function onPageDrop(event: DragEvent) {
  pageDragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length === 0) {
    return
  }

  if (files.length === 1) {
    toast.info('Drop a single file onto the stage card it belongs to')
    return
  }

  const mapped = new Map<number, File>()
  const unmatched: string[] = []
  for (const file of files) {
    const index = inferStageFromFilename(file)
    if (index === null) {
      unmatched.push(file.name)
    } else {
      mapped.set(index, file)
    }
  }

  if (mapped.size === 0) {
    toast.danger(
      'No stage number found in those file names',
      'Name files like stage_03.jpg, or drop each one onto its card.',
    )
    return
  }

  const { failed } = await uploadBatch(mapped)
  cardErrors.value = failed

  if (unmatched.length > 0) {
    toast.info(
      `${unmatched.length} file${unmatched.length === 1 ? '' : 's'} skipped`,
      `No stage number in the name: ${unmatched.slice(0, 3).join(', ')}${
        unmatched.length > 3 ? ' and others' : ''
      }`,
    )
  }
}
</script>

<template>
  <div
    class="page shell"
    :class="{ 'page--dragging': pageDragging }"
    @dragenter.prevent="pageDragging = true"
    @dragover.prevent
    @dragleave.self="pageDragging = false"
    @drop.prevent="onPageDrop"
  >
    <header class="head">
      <div>
        <p class="label">Setup</p>
        <h1 class="display">Stage diagrams</h1>
        <p class="page-lede">
          One pose diagram per stage, shown as a reminder before each recording. Each stage also
          carries the description the collector reads there. Both are stored on the host and reused
          for every session, so this is a one time job.
        </p>
      </div>

      <div class="head__progress">
        <p class="head__count">
          <span class="mono-lg">{{ uploaded }}</span>
          <span class="dim"> / {{ total }}</span>
        </p>
        <p class="label">Configured</p>
      </div>
    </header>

    <div class="meter" :class="{ 'meter--done': ready }">
      <span class="meter__fill" :style="{ width: `${progress * 100}%` }" />
    </div>

    <p v-if="ready" class="wash wash--ok">
      All {{ total }} diagrams are configured. You can start a session whenever you are ready.
    </p>
    <p v-else class="wash wash--warn">
      {{ missingCount }} of {{ total }} diagrams still need an image. Drop one onto a card, or drop
      several at once and name them <span class="mono">stage_01.png</span> and so on. Descriptions
      can be written at any time, a stage does not need an image for that.
    </p>

    <div class="grid">
      <GuideCard
        v-for="entry in entries"
        :key="entry.index"
        :entry="entry"
        :image-url="guideImageUrl(byIndex.get(entry.index))"
        :busy="busyIndex === entry.index || batchBusy"
        :error="cardErrors.get(entry.index) ?? null"
        :highlight="!ready"
        :max-length="INSTRUCTIONS_MAX_LENGTH"
        @pick="(file) => onCardPick(entry.index, file)"
        @describe="(text) => onCardDescribe(entry.index, text)"
        @remove="onCardRemove(entry.index)"
      />
    </div>

    <footer class="foot">
      <p class="foot__note">
        PNG is the expected format. JPEG, WebP, BMP and GIF work too, up to 10 MB each. Images wider
        than 1600 px are rescaled for display, the original is kept as well. Clearing a description
        restores the wording from the service configuration.
      </p>
      <AppButton variant="primary" size="lg" @click="$router.push({ name: 'home' })">
        {{ ready ? 'Back to home' : 'Save and return home' }}
      </AppButton>
    </footer>

    <div v-if="pageDragging" class="dropveil fade-in">
      <div class="dropveil__inner">
        <p class="dropveil__title">Drop files to assign them by name</p>
        <p class="dropveil__body">For a single file, drop it directly on the stage card instead.</p>
      </div>
    </div>
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

.head__progress {
  text-align: right;
}

.head__count {
  color: var(--ink-900);
  line-height: 1;
}

.meter {
  height: 4px;
  border-radius: var(--r-full);
  background: var(--line);
  overflow: hidden;
  margin: var(--s6) 0 var(--s5);
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

.wash--ok {
  background: var(--saved-bg);
  border-color: #cbe3d6;
  color: var(--saved-fg);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(258px, 1fr));
  gap: var(--s4);
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s6);
  flex-wrap: wrap;
  margin-top: var(--s7);
  padding-top: var(--s5);
  border-top: 1px solid var(--line);
}

.foot__note {
  font-size: var(--t-sm);
  color: var(--ink-400);
  max-width: 64ch;
}

.dropveil {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  pointer-events: none;
  background: rgba(20, 24, 27, 0.32);
}

.dropveil__inner {
  border: 2px dashed var(--accent-line);
  background: var(--surface);
  border-radius: var(--r-lg);
  padding: var(--s6) var(--s7);
  text-align: center;
  box-shadow: var(--shadow-2);
}

.dropveil__title {
  font-size: var(--t-lg);
  font-weight: 500;
}

.dropveil__body {
  margin-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-500);
}
</style>
