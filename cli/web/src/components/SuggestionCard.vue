<template>
  <section class="card hero">
    <div class="eyebrow">
      Up next
      <span
        v-if="why"
        class="why">
        {{ why }}
      </span>
    </div>
    <template v-if="suggestion">
      <h1>{{ suggestion.pursuit }}</h1>
      <div
        v-if="suggestion.choices.length"
        class="choices">
        <button
          v-for="(choice, index) in suggestion.choices"
          :key="choice.id ?? choice.title"
          type="button"
          class="choice"
          :class="{ on: index === chosen }"
          :aria-pressed="index === chosen"
          @click="chosen = index">
          <span class="dot"></span>
          <span
            class="title"
            :title="choice.title">
            {{ choice.title }}
          </span>
          <span
            v-if="choice.by"
            class="by">
            {{ choice.by }}
          </span>
        </button>
      </div>
      <p
        v-else-if="suggestion.choices_error"
        class="failed">
        {{ suggestion.choices_error }}
      </p>
      <div class="logbar">
        <PickButton
          kind="btn-primary"
          :pick-key="logKey"
          :pick="logPick"
          @pick="logKey && store.click(logKey, timed ? minutes : null)">
          <template #default="{ look }">{{ logText(look) }}</template>
        </PickButton>
        <template v-if="timed">
          <button
            v-for="amount in amounts"
            :key="amount"
            type="button"
            class="chip"
            :class="{ on: amount === minutes }"
            :aria-pressed="amount === minutes"
            @click="minutes = amount">
            {{ amount }}
          </button>
        </template>
      </div>
    </template>
    <p
      v-else
      class="nothing-offered">
      nothing offered
      <template v-if="view.suggestion_reason">— {{ view.suggestion_reason }}</template>
    </p>
    <div
      v-if="offered"
      class="feedback">
      <span class="label">{{ offered.label }}</span>
      <button
        v-for="answer in offered.answers"
        :key="answer.key"
        type="button"
        class="answer"
        :title="offered.title"
        :data-key="answer.key"
        @click="store.answer(answer.key, answer.label)">
        {{ answer.label }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { minutesText, pickOf, type Clock, type Look, type NudgeAnswer, type ShownPick, type View } from '../panel'
import { usePanelStore } from '../store'
import PickButton from './PickButton.vue'

const props = defineProps<{ view: View; picks: ShownPick[]; answers: NudgeAnswer[]; clock: Clock }>()
const store = usePanelStore()

// The ready nudge's answers, else the newest answer to change, else the too late that calls the nudge now.
const offered = computed(() => {
  if (props.answers.length) return { label: 'Nudge', answers: props.answers, title: undefined }
  const { answered, early, interval_minutes, counted_minutes } = props.clock
  if (answered) return { label: `Answered ${answered.label} · change to`, answers: answered.changes, title: undefined }
  if (early)
    return {
      label: `Nudge in ${minutesText(Math.max(interval_minutes - counted_minutes, 0))}`,
      answers: [early],
      title: 'Ready before it fired: calls the nudge now and answers it too late',
    }
  return null
})

/** The fixed amounts a timed pursuit's Log offers as chips, beside its usual one. */
const amounts = [15, 30, 45, 60]
/** Where a Log starts on a pursuit with no usual amount. */
const UNUSUAL_START = 30

const suggestion = computed(() => props.view.suggestion)
const timed = computed(() => suggestion.value?.unit === 'minutes')
const chosen = ref(0)
const minutes = ref(UNUSUAL_START)

// A new suggestion starts on the choice its draw settled and on its usual minutes.
watch(
  () => suggestion.value?.pursuit,
  () => {
    const current = suggestion.value
    const settled = current?.choices.findIndex((choice) => choice.id !== null && choice.id === current.item_id) ?? -1
    chosen.value = Math.max(settled, 0)
    minutes.value = current?.usual_minutes ? Math.round(current.usual_minutes) : UNUSUAL_START
  },
  { immediate: true },
)

const logKey = computed(() => suggestion.value?.key ?? suggestion.value?.choices[chosen.value]?.key ?? null)
const logPick = computed(() => pickOf(props.picks, logKey.value))

const why = computed(() => {
  const current = suggestion.value
  if (!current) return null
  const note = props.view.pursuits.rows.find((row) => row.name === current.pursuit)?.note
  const need = current.need === null ? null : timed.value ? `${minutesText(current.need)} asked today` : `${current.need} asked today`
  return [note, need].filter(Boolean).join(' · ') || null
})

function logText(look: Look): string {
  const amount = logPick.value?.minutes ?? minutes.value
  const what = timed.value ? minutesText(amount) : ''
  switch (look) {
    case 'committing':
      return timed.value ? `Logging ${what}` : 'Marking done'
    case 'done':
      return timed.value ? `Logged ${what}` : 'Done'
    case 'uncertain':
      return 'May not have landed'
  }
  return timed.value ? `Log ${what}` : 'Mark done'
}
</script>
