<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import StatsCard from '../components/StatsCard.vue'
import RangePicker from '../components/RangePicker.vue'

const today = new Date().toISOString().slice(0, 10)

const rangeStart = ref(today)
const rangeEnd = ref(today)
const loading = ref(false)
const collecting = ref(false)
const errorMsg = ref('')
const meta = ref(null)
const metaLoading = ref(true)
const watcherActive = ref(false)

const rangeStats = ref(null)
const dailyStats = ref(null)
const titlesSummary = ref(null)
const insights = ref(null)
const insightsLoading = ref(false)
const lastInsightKey = ref('')

const apiBase = import.meta.env.VITE_API_BASE || '/api'

const totalMinutes = computed(() =>
  Math.round(rangeStats.value?.totals?.total_minutes || 0)
)

const sessionBars = computed(() => {
  const totals = rangeStats.value?.totals
  if (!totals) return []
  return [
    { label: '深潜', value: totals.deep_minutes, color: '#111827', note: '≥ 20 min' },
    { label: '中段', value: totals.mid_minutes, color: '#6366f1', note: '10-20 min' },
    { label: '碎片', value: totals.fragmented_minutes, color: '#cbd5f5', note: '< 10 min', textColor: '#0f172a' }
  ]
})

const categoryStats = computed(() => {
  if (!rangeStats.value) return []
  const total = rangeStats.value.totals?.total_minutes || 1
  return Object.entries(rangeStats.value.by_category || {}).map(([name, data]) => ({
    name,
    minutes: data.minutes,
    ratio: data.minutes / total
  }))
})

const keywordCloud = computed(() => titlesSummary.value?.keywords || [])

const coverageText = computed(() => {
  if (!rangeStats.value || !rangeStats.value.effective_start) return '暂无可用历史'
  return `${rangeStats.value.effective_start} ~ ${rangeStats.value.effective_end}`
})

const coverageRatio = computed(() =>
  Math.round((rangeStats.value?.coverage_ratio || 0) * 100)
)

const coverageNote = computed(() => {
  if (!rangeStats.value) return ''
  if (!rangeStats.value.effective_start) {
    return '所选时间段暂无本地记录，试着先回填最近历史。'
  }
  if (rangeStats.value.coverage_ratio < 1) {
    return `本次统计仅覆盖 ${coverageText.value}，约占所选区间的 ${coverageRatio.value}%`
  }
  return ''
})

function formatMinutes(min) {
  return `${min} min`
}

function formatDate(date) {
  return new Date(date).toISOString().slice(0, 10)
}

function parseISO(dateStr) {
  return dateStr ? new Date(dateStr) : null
}

function latestDate() {
  return meta.value?.latest_date ? parseISO(meta.value.latest_date) : new Date()
}

function earliestDate() {
  return meta.value?.earliest_date ? parseISO(meta.value.earliest_date) : null
}

function presetRange(preset) {
  const end = latestDate()
  let startDate = new Date(end)
  if (preset.days !== undefined) {
    startDate.setDate(end.getDate() - preset.days)
  } else if (preset.mode === 'week') {
    const day = end.getDay() || 7
    startDate.setDate(end.getDate() - (day - 1))
  } else if (preset.mode === 'month') {
    startDate = new Date(end.getFullYear(), end.getMonth(), 1)
  }
  const earliest = earliestDate()
  if (earliest && startDate < earliest) {
    startDate = earliest
  }
  rangeStart.value = formatDate(startDate)
  rangeEnd.value = formatDate(end)
}

function selectFullRange() {
  if (!meta.value?.earliest_date) return
  rangeStart.value = meta.value.earliest_date
  rangeEnd.value = meta.value.latest_date
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || '请求失败')
  }
  return res.json()
}

async function loadMeta({ keepRange = false } = {}) {
  metaLoading.value = true
  try {
    meta.value = await fetchJson(`${apiBase}/bilibili/meta`)
    if (!keepRange && meta.value?.latest_date) {
      presetRange({ days: 29 })
    }
  } finally {
    metaLoading.value = false
  }
}

async function refreshStats() {
  if (metaLoading.value) return
  errorMsg.value = ''
  loading.value = true
  const start = rangeStart.value
  const end = rangeEnd.value
  try {
    const tasks = [
      fetchJson(`${apiBase}/bilibili/stats/range?start=${start}&end=${end}`),
      fetchJson(`${apiBase}/bilibili/titles/range?start=${start}&end=${end}`)
    ]
    if (start === end) {
      tasks.push(fetchJson(`${apiBase}/bilibili/stats/daily?day=${start}`))
    }
    const [rangeResult, titlesResult, dailyResult] = await Promise.all(tasks)
    rangeStats.value = rangeResult
    titlesSummary.value = titlesResult
    dailyStats.value = dailyResult || null
    await generateInsights(rangeResult)
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    loading.value = false
  }
}

async function collectDay() {
  errorMsg.value = ''
  collecting.value = true
  try {
    const res = await fetch(`${apiBase}/bilibili/collect?day=${rangeStart.value}`, { method: 'POST' })
    if (!res.ok) throw new Error('采集失败，请检查 Cookie 配置')
    await refreshStats()
    await loadMeta({ keepRange: true })
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    collecting.value = false
  }
}

async function generateInsights(rangeResult) {
  const key = `${rangeStart.value}_${rangeEnd.value}_${rangeResult.totals?.video_count || 0}`
  if (lastInsightKey.value === key) return
  insightsLoading.value = true
  try {
    const payload = {
      start: rangeStart.value,
      end: rangeEnd.value,
      force_refresh: false
    }
    const res = await fetchJson(`${apiBase}/bilibili/insights/range`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    insights.value = res
    lastInsightKey.value = key
  } catch (err) {
    console.error(err)
  } finally {
    insightsLoading.value = false
  }
}

watch([rangeStart, rangeEnd], () => {
  if (!watcherActive.value) return
  if (rangeStart.value > rangeEnd.value) {
    rangeEnd.value = rangeStart.value
  }
  refreshStats()
})

onMounted(async () => {
  await loadMeta()
  watcherActive.value = true
  await refreshStats()
})
</script>

<template>
  <section class="day-view">
    <div class="meta-banner" v-if="meta && meta.earliest_date">
      <p>
        本地已记录的历史：{{ meta.earliest_date }} ~ {{ meta.latest_date }}，
        更早的记录需要靠你持续同步；B 站接口也只能看到最近几个月。
      </p>
    </div>

    <div class="toolbar">
      <div class="left">
        <RangePicker
          :start="rangeStart"
          :end="rangeEnd"
          :full-range-label="meta && meta.earliest_date ? '全部可见' : ''"
          :full-range-disabled="!(meta && meta.earliest_date)"
          @update:start="rangeStart = $event"
          @update:end="rangeEnd = $event"
          @quick="presetRange"
          @full="selectFullRange"
        />
      </div>
      <div class="actions">
        <button @click="collectDay" :disabled="collecting">
          {{ collecting ? '采集中...' : '拉取当天数据' }}
        </button>
        <button @click="refreshStats" :disabled="loading">
          {{ loading ? '刷新中...' : '刷新统计' }}
        </button>
      </div>
    </div>

    <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

    <div v-if="coverageNote" class="coverage-note">
      <span>{{ coverageNote }}</span>
    </div>

    <div v-if="rangeStats" class="stats-grid">
      <StatsCard
        label="区间总观看"
        :value="`${totalMinutes} min`"
        :sub="`共 ${rangeStats.totals.video_count} 个视频 · 覆盖 ${coverageText}`"
      />
      <StatsCard label="深潜时间" :value="formatMinutes(rangeStats.totals.deep_minutes)" sub="单次 ≥ 20 分钟" />
      <StatsCard label="中段时间" :value="formatMinutes(rangeStats.totals.mid_minutes)" sub="10-20 分钟" />
      <StatsCard label="碎片时间" :value="formatMinutes(rangeStats.totals.fragmented_minutes)" sub="单次 < 10 分钟" />
    </div>

    <div v-if="rangeStats" class="charts">
      <div class="chart-block session">
        <h3>注意力曲线</h3>
        <div class="bars">
          <div v-for="bar in sessionBars" :key="bar.label" class="bar-row">
            <div class="bar-label">
              <span>{{ bar.label }}</span>
              <small>{{ bar.note }}</small>
            </div>
            <div class="bar-track">
              <div
                class="bar-fill"
                :style="{ width: `${Math.min(bar.value, 400)}px`, background: bar.color, color: bar.textColor || '#fff' }"
              >
                <span>{{ bar.value }} min</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="chart-block categories">
        <h3>兴趣结构</h3>
        <div class="pie-grid">
          <div v-for="cat in categoryStats" :key="cat.name" class="pie-row">
            <span>{{ cat.name }}</span>
            <div class="pie-bar">
              <div class="fill" :style="{ width: `${Math.round(cat.ratio * 100)}%` }"></div>
            </div>
            <span>{{ Math.round(cat.minutes) }} min</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="titlesSummary" class="title-cloud card">
      <h3>这段时间常出现的标题词</h3>
      <div class="keywords">
        <span v-for="title in titlesSummary.keywords" :key="title">{{ title }}</span>
      </div>
    </div>

    <div class="insight-grid">
      <div class="insight card" v-if="insights">
        <header>
          <h3>{{ insights.title }}</h3>
          <p>{{ insights.summary }}</p>
        </header>
        <section>
          <h4>ADHD 视角的提醒</h4>
          <ul>
            <li v-for="line in insights.adhd_insights" :key="line">{{ line }}</li>
          </ul>
        </section>
        <section>
          <h4>温柔的小建议</h4>
          <ul>
            <li v-for="line in insights.gentle_suggestions" :key="line">{{ line }}</li>
          </ul>
        </section>
      </div>
      <div v-else class="insight card placeholder">
        <p>{{ insightsLoading ? '生成温柔解读中...' : '稍等片刻，即将生成专属于你的温柔解读。' }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.day-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.25rem;
  background: white;
  border-radius: 20px;
  box-shadow: 0 20px 35px -30px rgba(15, 23, 42, 0.6);
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.actions button {
  padding: 0.6rem 1.4rem;
  border: none;
  border-radius: 999px;
  background: #111827;
  color: white;
  font-weight: 600;
  transition: transform 200ms ease, box-shadow 200ms ease;
}

.actions button:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 30px -20px rgba(0, 0, 0, 0.8);
}

.actions button:disabled {
  opacity: 0.6;
  transform: none;
  box-shadow: none;
}

.error {
  color: #dc2626;
  margin: 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
}

.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1.25rem;
}

.chart-block {
  background: white;
  border-radius: 18px;
  padding: 1.25rem 1.5rem;
  box-shadow: 0 25px 40px -30px rgba(15, 23, 42, 0.45);
  transition: transform 200ms ease, box-shadow 200ms ease;
}

.chart-block:hover {
  transform: translateY(-4px);
  box-shadow: 0 35px 60px -30px rgba(15, 23, 42, 0.4);
}

.bars {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.bar-row {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.bar-label span {
  font-weight: 600;
}

.bar-label small {
  display: block;
  color: #94a3b8;
  font-size: 0.75rem;
}

.bar-track {
  flex: 1;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
}

.bar-fill {
  height: 42px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  padding-left: 1rem;
  transition: width 320ms ease;
}

.pie-grid {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.pie-row {
  display: grid;
  grid-template-columns: 100px 1fr 80px;
  align-items: center;
  font-size: 0.95rem;
}

.pie-bar {
  height: 12px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
}

.fill {
  height: 100%;
  background: linear-gradient(90deg, #111827, #4c1d95);
  width: 0;
  transition: width 320ms ease;
}

.title-cloud {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.card {
  background: white;
  border-radius: 20px;
  padding: 1.5rem;
  box-shadow: 0 20px 40px -35px rgba(15, 23, 42, 0.55);
}

.keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.keywords span {
  padding: 0.35rem 0.8rem;
  border-radius: 999px;
  background: #f1f5f9;
  font-size: 0.85rem;
}

.insight-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr);
}

.insight header h3 {
  margin-bottom: 0.35rem;
}

.insight header p {
  color: #475569;
}

.insight section {
  margin-top: 1rem;
}

.insight ul {
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
  color: #0f172a;
}

.placeholder {
  text-align: center;
  color: #94a3b8;
}

.meta-banner {
  background: #eef2ff;
  border-radius: 16px;
  padding: 0.9rem 1.1rem;
  color: #312e81;
  box-shadow: inset 0 0 0 1px rgba(79, 70, 229, 0.15);
}

.meta-banner p {
  margin: 0;
  font-size: 0.9rem;
}

.coverage-note {
  background: #fef3c7;
  color: #854d0e;
  border-radius: 999px;
  padding: 0.4rem 0.9rem;
  font-size: 0.85rem;
  width: fit-content;
}

@media (prefers-reduced-motion: reduce) {
  .chart-block,
  .fill,
  .bar-fill,
  .actions button {
    transition: none;
  }
}
</style>
