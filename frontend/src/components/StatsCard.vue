<script setup>
defineProps({
  label: String,
  value: [String, Number],
  sub: String
})
</script>

<template>
  <div class="card">
    <p class="label">{{ label }}</p>
    <p class="value">{{ value }}</p>
    <p class="sub" v-if="sub">{{ sub }}</p>
  </div>
</template>

<style scoped>
.card {
  padding: 1.2rem 1.4rem;
  background: white;
  border-radius: 20px;
  box-shadow: 0 20px 40px -35px rgba(15, 23, 42, 0.6);
  transition: transform 220ms ease, box-shadow 220ms ease;
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: 0 35px 60px -35px rgba(15, 23, 42, 0.45);
}

.label {
  font-size: 0.9rem;
  color: #6b7280;
  margin-bottom: 0.35rem;
}

.value {
  font-size: 1.9rem;
  font-weight: 600;
  margin: 0;
  transition: color 220ms ease, transform 220ms ease;
}

.card:hover .value {
  color: #111827;
  transform: translateY(-2px);
}

.sub {
  margin: 0.4rem 0 0;
  font-size: 0.85rem;
  color: #94a3b8;
}
</style>
