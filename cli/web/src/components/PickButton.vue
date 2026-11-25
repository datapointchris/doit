<template>
  <button
    type="button"
    :class="[kind, shown.look]"
    :disabled="!pickable"
    :data-key="pickKey"
    :title="shown.said ?? undefined"
    @click="emit('pick')">
    <slot :look="shown.look" />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { lookOf, type ShownPick } from '../panel'

const props = defineProps<{
  kind: 'btn-primary' | 'mini' | 'mini go'
  pickKey: string | null
  pick: ShownPick | null
  done?: boolean
}>()
const emit = defineEmits<{ pick: [] }>()

const shown = computed(() => lookOf(props.pick, props.done ?? false))
const pickable = computed(() => props.pickKey !== null && shown.value.look === 'idle')
</script>
