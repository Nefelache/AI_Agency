<script setup>
import DayView from './views/DayView.vue'
</script>

<template>
  <main class="app-shell">
    <header>
      <h1>Gentle Bilibili Diary</h1>
      <p>用温柔的方式，看见注意力的流动</p>
    </header>
    <DayView />
  </main>
</template>

<style scoped>
.app-shell {
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem 1rem 3rem;
}

header {
  text-align: center;
  margin-bottom: 2rem;
}

h1 {
  margin-bottom: 0.25rem;
}
</style>
