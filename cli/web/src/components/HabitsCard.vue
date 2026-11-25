<template>
  <section class="card habits-card">
    <h2 class="card-title">
      Habits
      <span class="count">{{ doneCount }} of {{ part.rows.length }}</span>
    </h2>
    <div class="bar habits-bar">
      <i :style="{ width: `${part.rows.length ? (doneCount / part.rows.length) * 100 : 0}%` }"></i>
    </div>
    <p
      v-if="part.status === 'unavailable'"
      class="failed">
      {{ part.reason ?? 'habits could not be read' }}
    </p>
    <div class="grid2">
      <TickRow
        v-for="{ row } in plain"
        :key="identity(row)"
        :label="row.name"
        :pick-key="row.key"
        :pick="pickOf(picks, row.key)"
        :done="row.done || held(row.key)"
        @pick="row.key && store.click(row.key)" />
    </div>
    <div
      v-for="{ row } in lists"
      :key="identity(row)"
      class="checklist">
      <div class="habit-head">
        <TickRow
          :label="row.name"
          :pick-key="row.key"
          :pick="pickOf(picks, row.key)"
          :done="row.done || held(row.key)"
          @pick="row.key && store.click(row.key)" />
        <button
          type="button"
          class="more inline fold"
          :class="{ open: isOpen(row) }"
          :aria-expanded="isOpen(row)"
          :aria-label="`${row.name}'s sets`"
          @click="toggle(row)">
          <span class="sets-total">{{ setsDone(row) }} of {{ setsTarget(row) }} sets</span>
          <span class="chev">▸</span>
        </button>
      </div>
      <div
        v-show="isOpen(row)"
        class="sets">
        <SetCircles
          v-for="item in row.items"
          :key="item.id"
          :item="item"
          :picks="picks"
          @pick="store.click" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'
import { pickOf, setsCounted, type HabitRow, type Panel, type ShownPick, type View } from '../panel'
import { usePanelStore } from '../store'
import SetCircles from './SetCircles.vue'
import TickRow from './TickRow.vue'

const props = defineProps<{ view: View; picks: ShownPick[]; picked: Panel['picked'] }>()
const store = usePanelStore()

const part = computed(() => props.view.habits)
const identity = (row: HabitRow) => String(row.id ?? row.name)
// Life lists habits in id order, so a habit done keeps its place.
const placed = computed(() => store.steady('habits', part.value.rows, identity, (row) => [row.key]))
const plain = computed(() => placed.value.filter(({ row }) => row.items.length === 0))
const lists = computed(() => placed.value.filter(({ row }) => row.items.length > 0))
const doneCount = computed(() => part.value.rows.filter((row) => row.done).length)

const held = (key: string | null) => key !== null && props.picked.some((pick) => pick.key === key)
const setsDone = (row: HabitRow) => row.items.reduce((sum, item) => sum + Math.min(setsCounted(item, props.picks), item.target), 0)
const setsTarget = (row: HabitRow) => row.items.reduce((sum, item) => sum + item.target, 0)

// Each checklist's sets start open, as layout C draws them.
const closed = reactive(new Set<string>())
const isOpen = (row: HabitRow) => !closed.has(identity(row))
function toggle(row: HabitRow) {
  const id = identity(row)
  if (closed.has(id)) closed.delete(id)
  else closed.add(id)
}
</script>
