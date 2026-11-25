<template>
  <button
    type="button"
    :class="[kind, shown.look]"
    :disabled="!pickable"
    :data-key="pickKey"
    :title="shown.said ?? undefined"
    @click="emit('pick')">
    <slot :look="shown.look" />
    <PickDrain
      v-if="pick?.state === 'pending'"
      :key="pick.id"
      :commit-at="pick.commit_at" />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { lookOf, type ShownPick } from '../panel'
import PickDrain from './PickDrain.vue'

const props = defineProps<{
  kind: 'btn-primary' | 'mini' | 'mini go'
  pickKey: string | null
  pick: ShownPick | null
  done?: boolean
}>()
const emit = defineEmits<{ pick: [] }>()

const shown = computed(() => lookOf(props.pick, props.done ?? false))
const pickable = computed(() => props.pickKey !== null && (shown.value.look === 'idle' || shown.value.look === 'pending'))
</script>
