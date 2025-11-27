<script setup>
const props = defineProps({
  start: { type: String, required: true },
  end: { type: String, required: true },
  fullRangeLabel: { type: String, default: '' },
  fullRangeDisabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:start', 'update:end', 'quick', 'full'])

const presets = [
  { label: '近 7 天', days: 6 },
  { label: '近 30 天', days: 29 },
  { label: '本周', mode: 'week' },
  { label: '本月', mode: 'month' }
]

function handlePreset(preset) {
  emit('quick', preset)
}
</script>

<template>
  <div class="range-picker">
    <div class="date-inputs">
      <label>
        开始
        <input type="date" :value="props.start" @input="emit('update:start', $event.target.value)" />
      </label>
      <label>
        结束
        <input type="date" :value="props.end" @input="emit('update:end', $event.target.value)" />
      </label>
    </div>
    <div class="quick">
      <button
        v-for="preset in presets"
        :key="preset.label"
        type="button"
        @click="handlePreset(preset)"
      >
        {{ preset.label }}
      </button>
      <button
        v-if="props.fullRangeLabel"
        type="button"
        class="full"
        :disabled="props.fullRangeDisabled"
        @click="emit('full')"
      >
        {{ props.fullRangeLabel }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.range-picker {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.date-inputs {
  display: flex;
  gap: 1rem;
}

label {
  font-size: 0.85rem;
  color: #475569;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

input[type='date'] {
  padding: 0.4rem 0.6rem;
  border-radius: 10px;
  border: 1px solid #cbd5f5;
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

button {
  border: 1px solid #e2e8f0;
  background: white;
  padding: 0.35rem 0.9rem;
  border-radius: 999px;
  font-size: 0.85rem;
  transition: background 200ms ease, transform 200ms ease;
}

button:hover {
  background: #111827;
  color: white;
  transform: translateY(-1px);
}

.full {
  border: 1px dashed #94a3b8;
  color: #475569;
  background: transparent;
}

.full:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
