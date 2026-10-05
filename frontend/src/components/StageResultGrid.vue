<script setup lang="ts">
import { computed } from 'vue'
import type { Stage } from '@/api'
import { formatBytes, formatSeconds, formatDateTime, stageNumber } from '@/utils/format'

const props = defineProps<{
  stages: Stage[]
}>()

interface Row {
  stage: Stage
  recorded: boolean
}

const rows = computed<Row[]>(() =>
  props.stages.map((stage) => ({ stage, recorded: stage.artifact !== null })),
)
</script>

<template>
  <div class="grid">
    <article
      v-for="row in rows"
      :key="row.stage.index"
      class="rcard"
      :class="{ 'rcard--empty': !row.recorded }"
    >
      <div class="rcard__shot">
        <img
          v-if="row.stage.artifact"
          :src="row.stage.artifact.thumbnail_url"
          :alt="`Captured frame for ${row.stage.name}`"
        />
        <span v-else class="rcard__none">No take</span>
      </div>

      <div class="rcard__body">
        <div class="rcard__top">
          <span class="rcard__no mono">{{ stageNumber(row.stage.index) }}</span>
          <span v-if="row.recorded" class="rcard__ok">Saved</span>
          <span v-else class="rcard__miss">Missing</span>
        </div>
        <h3 class="rcard__name">{{ row.stage.name }}</h3>

        <dl class="rcard__specs">
          <div>
            <dt>Duration</dt>
            <dd class="mono">{{ formatSeconds(row.stage.artifact?.duration_s) }}</dd>
          </div>
          <div>
            <dt>Size</dt>
            <dd class="mono">{{ formatBytes(row.stage.artifact?.size_bytes) }}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd class="mono">{{ formatDateTime(row.stage.artifact?.started_at) }}</dd>
          </div>
        </dl>
      </div>
    </article>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--s4);
}

.rcard {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
}

.rcard--empty {
  background: var(--surface-sunken);
  border-style: dashed;
}

.rcard__shot {
  aspect-ratio: 16 / 9;
  background: #0d1517;
  display: grid;
  place-items: center;
  border-bottom: 1px solid var(--line);
}

.rcard__shot img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.rcard__none {
  font-size: var(--t-xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-400);
}

.rcard__body {
  padding: var(--s3) var(--s4) var(--s4);
  flex: 1;
  display: flex;
  flex-direction: column;
}

.rcard__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
}

.rcard__no {
  font-size: var(--t-2xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-400);
  font-weight: 600;
}

.rcard__ok,
.rcard__miss {
  font-size: var(--t-2xs);
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  border-radius: var(--r-full);
  padding: 2px var(--s2);
}

.rcard__ok {
  background: var(--saved-bg);
  color: var(--saved-fg);
}

.rcard__miss {
  background: var(--danger-bg);
  color: var(--danger);
}

.rcard__name {
  font-size: var(--t-base);
  font-weight: 600;
  line-height: 1.3;
  margin: var(--s2) 0 var(--s3);
}

.rcard__specs {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
  margin-top: auto;
  font-size: var(--t-xs);
}

.rcard__specs > div {
  display: flex;
  justify-content: space-between;
  gap: var(--s3);
  align-items: baseline;
}

.rcard__specs dt {
  color: var(--ink-400);
}

.rcard__specs dd {
  margin: 0;
  color: var(--ink-700);
}
</style>
