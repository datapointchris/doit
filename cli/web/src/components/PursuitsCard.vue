<template>
  <section class="card list-card">
    <h2 class="card-title">
      Other pursuits
      <span class="count">{{ overdue }} overdue</span>
    </h2>
    <p
      v-if="part.status === 'unavailable'"
      class="failed">
      {{ part.reason ?? 'pursuits could not be read' }}
    </p>
    <div
      v-for="{ row, gone } in rows"
      :key="row.name"
      class="prow"
      :class="{ done: gone }">
      <div class="phead">
        <span class="pname">{{ row.name }}</span>
        <span class="pnote">{{ gone ? 'done today' : row.note }}</span>
        <PickButton
          v-if="row.unit === 'checkoffs' && !row.writes_through"
          kind="mini go"
          :pick-key="row.key"
          :pick="pickOf(picks, row.key)"
          :done="gone"
          @pick="row.key && store.click(row.key)">
          done
        </PickButton>
      </div>
      <div
        v-if="row.unit === 'minutes' && !row.writes_through"
        class="pact">
        <div class="bar minutes"><i :style="{ width: share(row) }"></i></div>
        <span class="value">{{ minutesText(row.done ?? 0) }} / {{ minutesText(row.need ?? 0) }}</span>
        <PickButton
          v-for="amount in amounts"
          :key="amount"
          kind="mini"
          :pick-key="row.key"
          :pick="pickAt(row, amount)"
          :done="gone"
          @pick="row.key && store.click(row.key, amount)">
          +{{ amount }}
        </PickButton>
      </div>
      <template v-if="!row.writes_through">
        <ul
          v-if="row.items.length"
          class="pchoices">
          <li
            v-for="item in row.items"
            :key="item.id ?? item.title"
            :title="item.by ? `${item.title} · ${item.by}` : item.title">
            {{ item.title }}
            <span
              v-if="item.by"
              class="by">
              · {{ item.by }}
            </span>
          </li>
        </ul>
        <p
          v-else-if="row.items_error"
          class="failed">
          {{ row.items_error }}
        </p>
      </template>
      <div
        v-else
        class="pitems">
        <p
          v-if="row.items_error"
          class="failed">
          {{ row.items_error }}
        </p>
        <TickRow
          v-for="{ row: item, gone: itemGone } in itemsOf(row)"
          :key="item.id ?? item.title"
          :label="item.title"
          :note="item.by || null"
          :pick-key="item.key"
          :pick="pickOf(picks, item.key)"
          :done="itemGone"
          @pick="item.key && store.click(item.key, row.unit === 'minutes' ? usual(row) : null)" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { minutesText, pickOf, type PursuitRow, type ShownPick, type View } from '../panel'
import { usePanelStore } from '../store'
import PickButton from './PickButton.vue'
import TickRow from './TickRow.vue'

const props = defineProps<{ view: View; picks: ShownPick[] }>()
const store = usePanelStore()

/** The minutes a timed pursuit's row adds in one click. */
const amounts = [15, 30, 60]

const part = computed(() => props.view.pursuits)
// Every pursuit but the one the suggestion card holds, owed ones first as the view orders them, so any can be logged.
const rows = computed(() =>
  store.steady(
    'pursuits',
    part.value.rows.filter((row) => row.name !== props.view.suggestion?.pursuit),
    (row) => row.name,
    (row) => [row.key, ...row.items.map((item) => item.key)],
  ),
)
const overdue = computed(() => rows.value.filter(({ row, gone }) => row.owed && !gone).length)

const itemsOf = (row: PursuitRow) =>
  store.steady(
    `items:${row.name}`,
    row.items,
    (item) => item.id ?? item.title,
    (item) => [item.key],
  )

const share = (row: PursuitRow) => `${row.need ? Math.min(100, ((row.done ?? 0) / row.need) * 100) : 0}%`

const usual = (row: PursuitRow) => (row.usual_minutes ? Math.round(row.usual_minutes) : 30)

// A timed pursuit's three buttons share its key, so its pick shows on the amount it logs.
function pickAt(row: PursuitRow, amount: number): ShownPick | null {
  const pick = pickOf(props.picks, row.key)
  return pick && pick.minutes === amount ? pick : null
}
</script>
