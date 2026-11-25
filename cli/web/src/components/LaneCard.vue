<template>
  <section
    class="card list-card lane"
    :data-lane="lane.name">
    <h2 class="card-title">
      {{ lane.title || lane.name }}
      <span class="count">{{ lane.total }} due</span>
    </h2>
    <p
      v-if="lane.reason"
      class="failed">
      {{ lane.reason }}
    </p>
    <p
      v-else-if="lane.status === 'unavailable'"
      class="failed">
      {{ lane.name }} could not be read
    </p>
    <template
      v-for="({ row, gone }, index) in rows"
      :key="identity(row)">
      <div v-show="index < SHOWN || open">
        <TickRow
          v-if="row.key"
          :label="row.label.replaceAll('-', ' ')"
          :sub="row.text"
          :note="row.note"
          :note-late="row.urgency === 'overdue'"
          :pick-key="row.key"
          :pick="pickOf(picks, row.key)"
          :done="gone"
          @pick="row.key && store.click(row.key)" />
        <div
          v-else
          class="prow"
          :title="row.text">
          <div class="pwhat plain">{{ row.text || row.label }}</div>
          <div class="pnote">{{ row.note }}</div>
        </div>
      </div>
    </template>
    <button
      v-if="rows.length > SHOWN"
      type="button"
      class="more fold"
      :class="{ open }"
      :aria-expanded="open"
      @click="open = !open">
      <span>{{ rows.length - SHOWN }} more</span>
      <span class="chev">▸</span>
    </button>
    <p
      v-if="lane.total > lane.rows.length"
      class="not-listed">
      {{ lane.total - lane.rows.length }} more not listed here
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { pickOf, type Lane, type LaneRow, type ShownPick } from '../panel'
import { usePanelStore } from '../store'
import TickRow from './TickRow.vue'

const props = defineProps<{ lane: Lane; picks: ShownPick[] }>()
const store = usePanelStore()

/** How many rows a lane shows before its fold. */
const SHOWN = 3

const open = ref(false)
const identity = (row: LaneRow) => row.key ?? (row.label || row.text)
const rows = computed(() => store.steady(`lane:${props.lane.name}`, props.lane.rows, identity, (row) => [row.key]))
</script>
