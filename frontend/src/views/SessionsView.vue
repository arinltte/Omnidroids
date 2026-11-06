<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<template>
  <div class="sessions-page">
    <nav class="navbar">
      <div class="nav-brand">OMNIDROIDS</div>
      <div class="nav-links">
        <router-link to="/new" class="nav-link">{{ $t('sessions.newPrediction') }}</router-link>
        <router-link to="/models" class="nav-link">{{ $t('nav.models') }}</router-link>
        <LanguageSwitcher />
      </div>
    </nav>

    <div class="main-content">
      <section class="console-box">
        <div class="console-header">
          <span class="console-label">{{ $t('sessions.title') }}</span>
          <span class="console-meta">{{ sessions.length }} · {{ $t('sessions.polling') }}</span>
        </div>

        <div v-if="loading && !sessions.length" class="empty-state">
          {{ $t('sessions.loading') }}
        </div>

        <div v-else-if="!sessions.length" class="empty-state">
          <p class="empty-title">{{ $t('sessions.emptyTitle') }}</p>
          <button class="primary-btn" @click="router.push('/new')">
            <span>{{ $t('sessions.newPrediction') }}</span>
            <span class="btn-arrow">→</span>
          </button>
        </div>

        <div v-else class="session-list">
          <div v-for="session in sessions" :key="session.id" class="session-card">
            <div class="card-main" @click="continueSession(session)">
              <div class="card-top">
                <span class="session-title">{{ session.title }}</span>
                <span class="stage-chip" :class="session.stage">{{ stageLabel(session.stage) }}</span>
                <span class="status-dot" :class="session.status" :title="statusLabel(session.status)"></span>
                <span class="status-text" v-if="session.status !== 'idle'">{{ statusLabel(session.status) }}</span>
              </div>
              <div class="card-meta">
                <span>{{ formatTime(session.created_at) }}</span>
                <span v-if="session.meta && session.meta.chunks" class="meta-sep">
                  {{ session.meta.chunks }} chunks
                </span>
                <span v-if="session.graph_id" class="meta-sep">{{ shortId(session.graph_id) }}</span>
              </div>
              <div v-if="session.error" class="card-error">{{ session.error }}</div>
              <div v-if="taskOf(session)" class="card-progress">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: taskOf(session).progress + '%' }"></div>
                </div>
                <span class="progress-text">{{ taskOf(session).message || taskOf(session).status }}</span>
              </div>
            </div>
            <div class="card-actions">
              <button
                v-if="canResume(session)"
                class="primary-btn small"
                :disabled="resuming === session.id"
                @click.stop="resume(session)"
              >
                <span>{{ resuming === session.id ? '…' : $t('sessions.resume') }}</span>
                <span class="btn-arrow">↻</span>
              </button>
              <button v-else class="ghost-btn small" @click.stop="continueSession(session)">
                {{ $t('sessions.continue') }}
              </button>
              <button class="ghost-btn small danger" @click.stop="remove(session)">
                {{ $t('sessions.delete') }}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import i18n from '../i18n'
import { listSessions, resumeSession, deleteSession } from '../api/sessions'

const router = useRouter()

const sessions = ref([])
const loading = ref(false)
const resuming = ref(null)
let pollTimer = null

const hasActive = computed(() =>
  sessions.value.some(s => s.status === 'running' || (s.tasks || []).some(t => t.status === 'processing'))
)

async function refresh() {
  loading.value = true
  try {
    const res = await listSessions()
    sessions.value = res.data?.sessions || []
  } catch (e) {
    console.error('sessions refresh failed', e)
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  // §8.3: 3–5s cadence while a session is active, then idle saves the round trips
  pollTimer = setInterval(() => { if (hasActive.value) refresh() }, 4000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function taskOf(session) {
  const tasks = session.tasks || []
  return tasks.find(t => t.status === 'processing') || tasks[0]
}

function stageLabel(stage) {
  const key = 'sessions.stage.' + (stage || '')
  const translated = i18n.global.t(key)
  // a missing key echoes itself — fall back to the raw stage tag
  return translated === key ? String(stage || '').toUpperCase() : translated
}

function statusLabel(status) {
  const key = 'sessions.status.' + (status || '')
  const translated = i18n.global.t(key)
  return translated === key ? String(status || '') : translated
}

function canResume(session) {
  return session.status === 'interrupted' || session.status === 'failed'
}

async function resume(session) {
  resuming.value = session.id
  try {
    await resumeSession(session.id, { force: session.status === 'failed' })
    await refresh()
  } catch (e) {
    alert(e.message || 'resume failed')
  } finally {
    resuming.value = null
  }
}

function continueSession(session) {
  const route = targetRoute(session)
  if (route) router.push(route)
}

function targetRoute(session) {
  const meta = session.meta || {}
  if (meta.report_id && session.stage === 'qa') {
    return { name: 'Interaction', params: { reportId: meta.report_id } }
  }
  if (meta.report_id && session.stage === 'report') {
    return { name: 'Report', params: { reportId: meta.report_id } }
  }
  if (session.sim_id && session.stage === 'simulate') {
    return { name: 'Simulation', params: { simulationId: session.sim_id } }
  }
  return { name: 'Process', params: { projectId: session.project_id } }
}

async function remove(session) {
  if (!confirm(i18n.global.t('sessions.deleteConfirm'))) return
  try {
    await deleteSession(session.id)
    await refresh()
  } catch (e) {
    alert(e.message || 'delete failed')
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function shortId(id) {
  return id && id.length > 12 ? id.slice(0, 12) + '…' : id
}

onMounted(() => { refresh(); startPolling() })
onUnmounted(stopPolling)
</script>

<style scoped>
:root {
  --black: #000000;
  --white: #FFFFFF;
  --orange: #FF4500;
  --gray-light: #F5F5F5;
  --gray-text: #666666;
  --border: #E5E5E5;
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.sessions-page {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 2px solid var(--orange);
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 0.12em;
  cursor: pointer;
}

.nav-links { display: flex; align-items: center; gap: 18px; }
.nav-link {
  color: var(--white);
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  opacity: 0.75;
}
.nav-link:hover, .nav-link.active { opacity: 1; }

.main-content { max-width: 980px; margin: 0 auto; padding: 32px 24px 64px; }

.console-box {
  border: 1px solid var(--border);
  background: var(--white);
}

.console-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.console-label { font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.1em; }
.console-meta { font-family: var(--font-mono); font-size: 11px; color: var(--gray-text); }

.empty-state { padding: 48px 16px; text-align: center; color: var(--gray-text); font-size: 13px; }
.empty-title { margin: 0 0 16px; }

.session-list { display: flex; flex-direction: column; }

.session-card {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid var(--border);
}
.session-card:last-child { border-bottom: none; }

.card-main { flex: 1; padding: 14px 16px; cursor: pointer; }
.card-main:hover { background: var(--gray-light); }

.card-top { display: flex; align-items: center; gap: 10px; }
.session-title { font-weight: 600; font-size: 14px; }

.stage-chip {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 2px 8px;
  border: 1px solid var(--border);
  color: var(--gray-text);
}
.stage-chip.graph_build { border-color: var(--orange); color: var(--orange); }
.stage-chip.report { border-color: var(--black); color: var(--black); }

.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.status-dot.running { background: var(--orange); }
.status-dot.interrupted { background: #E6A23C; }
.status-dot.failed { background: #D93025; }
.status-dot.idle { background: var(--gray-text); }

.status-text { font-family: var(--font-mono); font-size: 10px; color: var(--gray-text); letter-spacing: 0.06em; }

.card-meta {
  display: flex; gap: 8px; margin-top: 6px;
  font-family: var(--font-mono); font-size: 11px; color: var(--gray-text);
}
.meta-sep::before { content: '· '; }

.card-error {
  margin-top: 6px; font-size: 12px; color: #D93025;
  max-width: 640px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.card-progress { margin-top: 8px; max-width: 420px; }
.progress-bar { height: 4px; background: var(--border); }
.progress-fill { height: 100%; background: var(--orange); }
.progress-text {
  display: block; margin-top: 4px;
  font-family: var(--font-mono); font-size: 10px; color: var(--gray-text);
}

.card-actions { display: flex; flex-direction: column; gap: 6px; justify-content: center; padding: 12px 16px; }

.primary-btn {
  background: var(--black); color: var(--white); border: 1px solid var(--black);
  font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.06em;
  padding: 8px 14px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px;
}
.primary-btn:disabled { opacity: 0.5; cursor: default; }
.primary-btn.small { padding: 6px 10px; }

.ghost-btn {
  background: transparent; border: 1px solid var(--border); color: var(--black);
  font-family: var(--font-mono); font-size: 12px; padding: 6px 10px; cursor: pointer;
}
.ghost-btn.danger { color: #D93025; border-color: #D93025; }
.ghost-btn.small { padding: 6px 10px; }
</style>
