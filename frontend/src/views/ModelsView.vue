<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<template>
  <div class="models-page">
    <nav class="navbar">
      <div class="nav-brand">OMNIDROIDS</div>
      <div class="nav-links">
        <router-link to="/" class="nav-link">{{ $t('nav.home') }}</router-link>
        <router-link to="/models" class="nav-link active">MODELS</router-link>
        <AppearanceSwitcher />
        <LanguageSwitcher />
      </div>
    </nav>

    <div class="main-content">
      <!-- 运行状态：内存预算、常驻、并发 -->
      <section class="runtime-strip" v-if="runtime">
        <div class="runtime-cell">
          <span class="runtime-label">{{ $t('models.runtime.budget') }}</span>
          <span class="runtime-value">{{ formatGB(runtime.ram_budget_mb) }}</span>
        </div>
        <div class="runtime-cell">
          <span class="runtime-label">{{ $t('models.runtime.resident') }}</span>
          <span class="runtime-value" :class="{ warn: runtime.resident_mb > runtime.ram_budget_mb * 0.9 }">
            {{ formatGB(runtime.resident_mb) }}
          </span>
        </div>
        <div class="runtime-cell">
          <span class="runtime-label">{{ $t('models.runtime.backend') }}</span>
          <span class="runtime-value">{{ runtime.backend_default }}</span>
        </div>
        <div class="runtime-cell">
          <span class="runtime-label">{{ $t('models.runtime.idle') }}</span>
          <span class="runtime-value">{{ runtime.idle_unload_seconds }}s</span>
        </div>
        <div class="runtime-cell loaded-cell">
          <span class="status-dot" :class="{ on: runtime.loaded && runtime.loaded.length }">■</span>
          <span class="runtime-value">
            {{ (runtime.loaded || []).map(m => m.name).join(', ') || $t('models.runtime.noneLoaded') }}
          </span>
        </div>
      </section>

      <!-- 新增模型：粘贴 HF repo id -->
      <section class="console-box">
        <div class="console-section">
          <div class="console-header">
            <span class="console-label">{{ $t('models.add.title') }}</span>
            <span class="console-meta">HUGGINGFACE</span>
          </div>
          <div class="add-row">
            <input
              v-model="repoInput"
              class="code-input repo-input"
              :placeholder="$t('models.add.placeholder')"
              :disabled="submitting"
              @keyup.enter="submitDownload"
            />
            <select v-model="roleInput" class="code-input role-select" :disabled="submitting">
              <option value="">{{ $t('models.add.noRole') }}</option>
              <option value="small">small</option>
              <option value="big">big</option>
              <option value="embed">embed</option>
            </select>
            <button class="primary-btn" :disabled="!repoInput.trim() || submitting" @click="submitDownload">
              <span>{{ submitting ? $t('models.add.adding') : $t('models.add.submit') }}</span>
              <span class="btn-arrow">→</span>
            </button>
          </div>
          <div v-if="downloadError" class="inline-error">{{ downloadError }}</div>
        </div>

        <!-- 下载任务进度 -->
        <div class="console-section" v-if="jobs.length">
          <div class="console-header">
            <span class="console-label">{{ $t('models.downloads.title') }}</span>
          </div>
          <div class="job-row" v-for="job in jobs" :key="job.job_id">
            <span class="job-status" :class="job.status">{{ statusMark(job.status) }}</span>
            <span class="job-repo">{{ job.repo_id }}</span>
            <div class="job-bar">
              <div class="job-bar-fill" :style="{ width: job.percent + '%' }"
                   :class="{ done: job.status === 'done', error: job.status === 'error' }"></div>
            </div>
            <span class="job-meta">
              <template v-if="job.status === 'running'">
                {{ job.percent }}% · {{ job.speed_mb_s }} MB/s
                <template v-if="job.eta_s != null"> · eta {{ job.eta_s | 0 }}s</template>
              </template>
              <template v-else-if="job.status === 'error'">
                <span class="error-text">{{ job.error }}</span>
              </template>
              <template v-else>{{ $t(`models.status.${job.status}`) }}</template>
            </span>
            <button v-if="job.status === 'running' || job.status === 'queued'"
                    class="ghost-btn" @click="cancel(job.job_id)">{{ $t('models.downloads.cancel') }}</button>
          </div>
        </div>
      </section>

      <!-- 已安装模型 -->
      <section class="installed">
        <div class="section-header">
          <div class="section-line"></div>
          <span class="section-title">{{ $t('models.installed.title') }}</span>
          <div class="section-line"></div>
        </div>

        <div v-if="!models.length" class="empty-hint">{{ $t('models.installed.empty') }}</div>

        <table class="models-table" v-else>
          <thead>
            <tr>
              <th>{{ $t('models.installed.colName') }}</th>
              <th>{{ $t('models.installed.colRepo') }}</th>
              <th>{{ $t('models.installed.colBackend') }}</th>
              <th>{{ $t('models.installed.colSize') }}</th>
              <th>{{ $t('models.installed.colRole') }}</th>
              <th>{{ $t('models.installed.colState') }}</th>
              <th>{{ $t('models.installed.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in models" :key="m.name">
              <td class="cell-name">{{ m.name }}</td>
              <td class="cell-repo">{{ m.repo_id }}</td>
              <td><span class="backend-badge" :class="m.backend">{{ m.backend }}</span></td>
              <td class="cell-size">{{ formatBytes(m.size_bytes) }}</td>
              <td>
                <select class="role-select-sm" :value="roleOf(m)"
                        @change="assign(m.name, $event.target.value)">
                  <option value="">{{ $t('models.add.noRole') }}</option>
                  <option v-for="r in roles" :key="r" :value="r">{{ r }}</option>
                </select>
              </td>
              <td class="cell-state">
                <span class="status-dot" :class="{ on: m.loaded }">■</span>
                <span class="state-text">{{ m.loaded ? $t('models.state.loaded') : $t('models.state.unloaded') }}</span>
                <span v-if="m.loaded && m.footprint_mb" class="fp-text"> · {{ formatGB(m.footprint_mb * 1024 * 1024) }}</span>
              </td>
              <td class="cell-actions">
                <button class="ghost-btn" :disabled="!m.loaded" @click="unload(m.name)">
                  {{ $t('models.actions.unload') }}
                </button>
                <button class="ghost-btn danger" @click="remove(m.name)">
                  {{ $t('models.actions.delete') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 运行时设置 -->
      <section class="settings">
        <div class="section-header">
          <div class="section-line"></div>
          <span class="section-title">{{ $t('models.settings.title') }}</span>
          <div class="section-line"></div>
        </div>
        <div class="console-box">
          <div class="console-section">
            <div class="settings-row">
              <label class="settings-label">
                {{ $t('models.settings.concurrency') }}
                <input v-model.number="settingsDraft.concurrency" type="number" min="1" max="16"
                       class="code-input num-input" />
              </label>
              <label class="settings-label">
                {{ $t('models.settings.ramBudget') }}
                <input v-model.number="settingsDraft.ram_budget_gb" type="number" min="1" max="64" step="0.5"
                       class="code-input num-input" />
              </label>
              <label class="settings-label">
                {{ $t('models.settings.idle') }}
                <input v-model.number="settingsDraft.idle_unload_seconds" type="number" min="5" max="3600"
                       class="code-input num-input" />
              </label>
              <button class="primary-btn" :disabled="!settingsDirty" @click="saveSettings">
                <span>{{ $t('models.settings.save') }}</span>
              </button>
            </div>
            <div v-if="settingsError" class="inline-error">{{ settingsError }}</div>
            <div v-if="settingsSaved" class="inline-ok">{{ $t('models.settings.saved') }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import AppearanceSwitcher from '../components/AppearanceSwitcher.vue'
import {
  listModels, startDownload, listDownloads, cancelDownload, setRole,
  unloadModel, deleteModel, getSettings, updateSettings
} from '../api/models'

const router = useRouter()

const models = ref([])
const runtime = ref(null)
const jobs = ref([])
const repoInput = ref('')
const roleInput = ref('')
const submitting = ref(false)
const downloadError = ref('')
const settings = ref({ concurrency: 4, ram_budget_gb: 11, idle_unload_seconds: 120 })
const settingsDraft = reactive({ concurrency: 4, ram_budget_gb: 11, idle_unload_seconds: 120 })
const settingsError = ref('')
const settingsSaved = ref(false)

const roles = ['small', 'big', 'embed']

let poller = null

const settingsDirty = computed(() =>
  settingsDraft.concurrency !== settings.value.concurrency ||
  Number(settingsDraft.ram_budget_gb) !== Number(settings.value.ram_budget_gb) ||
  Number(settingsDraft.idle_unload_seconds) !== Number(settings.value.idle_unload_seconds))

async function refresh() {
  try {
    const [m, d, s] = await Promise.all([listModels(), listDownloads(), getSettings()])
    if (m.data?.code === 0) {
      models.value = m.data.data.models
      runtime.value = m.data.data.runtime
    }
    if (d.data?.code === 0) jobs.value = d.data.data.jobs
    if (s.data?.code === 0) {
      settings.value = s.data.data
      settingsDraft.concurrency = s.data.data.concurrency
      settingsDraft.ram_budget_gb = s.data.data.ram_budget_gb
      settingsDraft.idle_unload_seconds = s.data.data.idle_unload_seconds
    }
  } catch (e) {
    console.error('models refresh failed', e)
  }
}

async function submitDownload() {
  const repo = repoInput.value.trim()
  if (!repo) return
  submitting.value = true
  downloadError.value = ''
  try {
    const r = await startDownload(repo, roleInput.value || undefined)
    const body = r.data || {}
    if (body.code === 0) {
      repoInput.value = ''
      roleInput.value = ''
    } else {
      downloadError.value = body.message || 'rejected'
    }
  } catch (e) {
    downloadError.value = e.response?.data?.message || e.message
  } finally {
    submitting.value = false
    refresh()
  }
}

async function cancel(jobId) {
  await cancelDownload(jobId)
  refresh()
}

async function assign(name, role) {
  await setRole(name, role || null)
  refresh()
}

async function unload(name) {
  await unloadModel(name)
  refresh()
}

async function remove(name) {
  const r = await deleteModel(name)
  const body = r.data || {}
  if (body.code !== 0) {
    downloadError.value = body.message
  }
  refresh()
}

async function saveSettings() {
  settingsError.value = ''
  settingsSaved.value = false
  try {
    const r = await updateSettings({
      concurrency: settingsDraft.concurrency,
      ram_budget_gb: settingsDraft.ram_budget_gb,
      idle_unload_seconds: settingsDraft.idle_unload_seconds
    })
    const body = r.data || {}
    if (body.code === 0) {
      settings.value = body.data
      settingsSaved.value = true
      setTimeout(() => { settingsSaved.value = false }, 2500)
    } else {
      settingsError.value = body.message || 'invalid'
    }
  } catch (e) {
    settingsError.value = e.response?.data?.message || e.message
  }
}

function roleOf(m) {
  return (m.roles && m.roles[0]) || ''
}

function statusMark(s) {
  return { queued: '◇', running: '▶', done: '■', error: '×', cancelled: '−' }[s] || '?'
}

function formatBytes(n) {
  const b = Number(n || 0)
  if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(2) + ' GB'
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(0) + ' MB'
  return b + ' B'
}

function formatGB(mb) {
  return (Number(mb || 0) / 1024).toFixed(1) + ' GB'
}

onMounted(() => {
  refresh()
  poller = setInterval(refresh, 4000)
})

onUnmounted(() => {
  if (poller) clearInterval(poller)
})
</script>

<style scoped>
/* appearance tokens come from assets/theme.css (§9.2) */

.models-page {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

/* 顶部导航 */
.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1.2rem;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 20px;
}

.nav-link {
  color: var(--on-ink);
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 500;
  letter-spacing: 1px;
  transition: color 0.2s;
}

.nav-link:hover,
.nav-link.active {
  color: var(--white);
}

.nav-link.active {
  border-bottom: 2px solid var(--orange);
}

.main-content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 40px;
}

/* 运行状态条 */
.runtime-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  border: 1px solid var(--border);
  margin-bottom: 24px;
}

.runtime-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-right: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.runtime-cell:last-child {
  border-right: none;
  flex: 1;
}

.runtime-label {
  color: var(--gray-text);
  text-transform: uppercase;
  letter-spacing: 1px;
}

.runtime-value {
  font-weight: 700;
}

.runtime-value.warn {
  color: var(--orange);
}

.status-dot {
  color: var(--border);
  font-size: 0.6rem;
}

.status-dot.on {
  color: var(--orange);
}

/* 控制台盒子（沿用首页双重边框语言） */
.console-box {
  border: 1px solid var(--border);
  padding: 8px;
  margin-bottom: 40px;
}

.console-section {
  padding: 20px;
}

.console-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 15px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--gray-text);
}

.console-label {
  text-transform: uppercase;
  letter-spacing: 1px;
}

.console-meta {
  color: #BBB;
}

/* 新增模型行 */
.add-row {
  display: flex;
  gap: 12px;
}

.code-input {
  border: 1px solid var(--border);
  background: var(--gray-light);
  padding: 14px 16px;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  outline: none;
}

.code-input:focus {
  border-color: var(--black);
  background: var(--white);
}

.repo-input {
  flex: 1;
}

.role-select {
  width: 110px;
}

.primary-btn {
  background: var(--black);
  color: var(--white);
  border: 1px solid var(--black);
  padding: 0 28px;
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.85rem;
  letter-spacing: 1px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.primary-btn:hover:not(:disabled) {
  background: var(--orange);
  border-color: var(--orange);
}

.primary-btn:disabled {
  background: var(--border);
  color: var(--gray-text);
  cursor: not-allowed;
  border-color: var(--border);
}

.btn-arrow {
  font-family: sans-serif;
}

.ghost-btn {
  background: transparent;
  border: 1px solid var(--border);
  padding: 6px 12px;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  letter-spacing: 0.5px;
  color: var(--black);
  cursor: pointer;
  transition: all 0.2s;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--black);
}

.ghost-btn:disabled {
  color: #BBB;
  cursor: not-allowed;
}

.ghost-btn.danger:hover:not(:disabled) {
  border-color: var(--orange);
  color: var(--orange);
}

.inline-error {
  margin-top: 12px;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--orange);
  word-break: break-all;
}

.inline-ok {
  margin-top: 12px;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: #0A7A3A;
}

/* 下载任务 */
.job-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid var(--gray-light);
  font-family: var(--font-mono);
  font-size: 0.78rem;
}

.job-row:last-child {
  border-bottom: none;
}

.job-status {
  width: 14px;
  text-align: center;
  color: var(--gray-text);
}

.job-status.running { color: var(--orange); }
.job-status.done { color: #0A7A3A; }
.job-status.error { color: var(--orange); }

.job-repo {
  flex: 0 0 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-bar {
  flex: 1;
  height: 6px;
  background: var(--gray-light);
  position: relative;
}

.job-bar-fill {
  height: 100%;
  background: var(--orange);
  transition: width 0.4s;
}

.job-bar-fill.done { background: #0A7A3A; }
.job-bar-fill.error { background: var(--orange); }

.job-meta {
  flex: 0 0 220px;
  color: var(--gray-text);
  text-align: right;
}

.error-text {
  color: var(--orange);
}

/* 区块标题（沿用 HistoryDatabase 语言） */
.section-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.section-line {
  flex: 1;
  height: 1px;
  background: var(--border);
}

.section-title {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--gray-text);
  letter-spacing: 2px;
  text-transform: uppercase;
}

.empty-hint {
  border: 1px dashed var(--border);
  padding: 40px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--gray-text);
  margin-bottom: 40px;
}

/* 模型表 */
.models-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  margin-bottom: 40px;
}

.models-table th {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--gray-text);
  text-transform: uppercase;
  letter-spacing: 1px;
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-weight: 500;
}

.models-table td {
  padding: 14px 12px;
  border-bottom: 1px solid var(--gray-light);
  vertical-align: middle;
}

.models-table tr:hover td {
  background: var(--gray-light);
}

.cell-name {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.8rem;
}

.cell-repo {
  color: var(--gray-text);
  font-size: 0.78rem;
}

.cell-size, .job-meta {
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.cell-size {
  font-family: var(--font-mono);
}

.backend-badge {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  border: 1px solid var(--border);
  padding: 2px 8px;
  color: var(--gray-text);
}

.backend-badge.mlx {
  border-color: var(--orange);
  color: var(--orange);
}

.cell-state {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  white-space: nowrap;
}

.state-text {
  color: var(--gray-text);
}

.fp-text {
  color: var(--gray-text);
}

.cell-actions {
  white-space: nowrap;
}

.cell-actions .ghost-btn {
  margin-right: 8px;
}

.role-select-sm {
  border: 1px solid var(--border);
  background: var(--white);
  font-family: var(--font-mono);
  font-size: 0.75rem;
  padding: 6px 8px;
  outline: none;
}

/* 设置 */
.settings-row {
  display: flex;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
}

.settings-label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--gray-text);
  text-transform: uppercase;
  letter-spacing: 1px;
}

.num-input {
  width: 110px;
  padding: 10px 12px;
}

.settings .primary-btn {
  padding: 12px 24px;
}

@media (max-width: 768px) {
  .add-row {
    flex-direction: column;
  }

  .role-select {
    width: 100%;
  }

  .job-meta {
    display: none;
  }
}
</style>
