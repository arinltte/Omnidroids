<template>
  <div class="monitor">
    <header class="m-header">
      <div class="brand">MIROFISH</div>
      <div class="title">本地 AI 监控 / LLM Monitor</div>
      <div class="spacer"></div>
      <span class="conn" :class="connClass"><span class="dot"></span>{{ connText }}</span>
      <button class="link-btn" @click="goBack">← 返回</button>
    </header>

    <main class="m-body">
      <!-- Row 1: Ollama server status + live model (memory) -->
      <section class="card">
        <div class="card-h">本地模型 / Ollama 实例 <span class="hint">（同一实例并发推理，不额外拉起进程）</span></div>
        <div class="kv-grid">
          <div class="kv"><span>Ollama</span><b>{{ ollama.version || '—' }} · {{ ollama.reachable ? '在线' : '离线' }}</b></div>
          <div class="kv"><span>模型</span><b>{{ config.model || '—' }}</b></div>
          <div class="kv"><span>转发上游</span><b>{{ config.upstream || '—' }}</b></div>
          <div class="kv"><span>思考模式</span><b :class="config.thinking_off ? 'off' : 'on'">{{ config.thinking || '—' }}</b></div>
          <div class="kv"><span>并发上限</span><b>{{ config.num_parallel ?? '—' }}</b></div>
          <div class="kv"><span>上下文窗口</span><b>{{ config.num_ctx ?? '—' }}</b></div>
        </div>
        <div v-if="ollama.models && ollama.models.length" class="loaded">
          <table class="tbl">
            <thead><tr><th>已加载实例</th><th>规模</th><th>权重占用</th><th>显存/内存占用</th><th>量化</th></tr></thead>
            <tbody>
              <tr v-for="m in ollama.models" :key="m.name">
                <td>{{ m.name }}</td>
                <td>{{ m.parameter_size || '—' }}</td>
                <td>{{ bytes(m.size) }}</td>
                <td>{{ bytes(m.size_vram) }}</td>
                <td>{{ m.quantization || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <div class="note">实例数量：<b>{{ ollama.models.length }}</b>（应为 1；配合 并发上限 同时服务多个子 Agent）</div>
        </div>
        <div v-else class="empty">暂无已加载实例（首次请求后或空闲超时后会在此显示）。</div>
      </section>

      <!-- Row 2: controls -->
      <section class="card">
        <div class="card-h">控制 / Controls</div>
        <div class="ctrl-row">
          <button class="toggle" :class="config.thinking_off ? 't-off' : 't-on'" @click="toggleThinking">
            思考模式：{{ config.thinking_off ? '关闭 (none)' : '开启 (high)' }} — 点击切换
          </button>
          <label>并发 <input type="number" min="1" max="16" v-model.number="numParallelCtl" @change="applyTuning"></label>
          <label>上下文 <input type="number" min="1024" step="1024" v-model.number="numCtxCtl" @change="applyTuning"></label>
          <button class="mini" @click="togglePause">{{ paused ? '继续刷新' : '暂停刷新' }}</button>
        </div>
        <div class="note">切换思考模式后立即生效；<span class="off">none</span>=快且稳定，<span class="on">high</span>=更慢但推理更充分（便于对比质量与耗时）。</div>
      </section>

      <!-- Row 3: usage totals -->
      <section class="card">
        <div class="card-h">累计用量 / Usage</div>
        <div class="grid">
          <div class="stat"><i>总请求</i><b>{{ m.total_requests ?? 0 }}</b></div>
          <div class="stat"><i>成功 / 失败</i><b>{{ m.ok_requests ?? 0 }} / {{ m.error_requests ?? 0 }}</b></div>
          <div class="stat"><i>总 tokens</i><b>{{ num(m.total_tokens) }}</b></div>
          <div class="stat"><i>输入 / 输出</i><b>{{ num(m.prompt_tokens) }} / {{ num(m.completion_tokens) }}</b></div>
          <div class="stat"><i>平均延迟</i><b>{{ twoFix(m.avg_latency_s) }}s</b></div>
          <div class="stat"><i>请求/分钟</i><b>{{ m.req_per_min ?? 0 }}</b></div>
          <div class="stat"><i>思考 开/关 次数</i><b>{{ m.thinking_on_requests ?? 0 }} / {{ m.thinking_off_requests ?? 0 }}</b></div>
          <div class="stat"><i>运行时长</i><b>{{ fmtUptime(m.uptime_seconds) }}</b></div>
        </div>
        <div v-if="Object.keys(m.by_model || {}).length" class="loaded">
          <table class="tbl">
            <thead><tr><th>模型</th><th>请求</th><th>tokens</th><th>耗时(s)</th></tr></thead>
            <tbody>
              <tr v-for="(v,k) in m.by_model" :key="k">
                <td>{{ k }}</td><td>{{ v.requests }}</td><td>{{ num(v.tokens) }}</td><td>{{ v.seconds }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Row 4: recent calls -->
      <section class="card wide">
        <div class="card-h">最近调用 / Recent calls</div>
        <div class="tablewrap">
          <table class="tbl">
            <thead><tr><th>时间</th><th>模型</th><th>思考</th><th>tokens(入/出)</th><th>耗时</th><th>结束</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="(r,i) in m.recent" :key="i" :class="r.ok ? '' : 'err'">
                <td>{{ r.ts }}</td>
                <td>{{ r.model }}</td>
                <td><span :class="r.thinking && r.thinking.startsWith('off') ? 'off' : 'on'">{{ r.thinking }}</span></td>
                <td>{{ r.prompt_tokens ?? '-' }} / {{ r.completion_tokens ?? '-' }}</td>
                <td>{{ r.seconds }}s</td>
                <td>{{ r.finish || '-' }}</td>
                <td>{{ r.ok ? 'OK' : ('ERR ' + (r.error || '')) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getLlmStatus, getLlmMetrics, getLlmConfig, setLlmConfig } from '../api/llm'

const router = useRouter()
const goBack = () => router.back()

const ollama = ref({ reachable: false, version: null, models: [], error: null })
const config = ref({})
const m = ref({})
const paused = ref(false)
const numParallelCtl = ref(4)
const numCtxCtl = ref(8192)
let timer = null

const connClass = computed(() => (ollama.value.reachable ? 'ok' : 'bad'))
const connText = computed(() => (ollama.value.reachable ? 'Ollama 在线' : 'Ollama 离线'))

const refresh = async () => {
  try {
    const s = await getLlmStatus()
    if (s.data?.success) { ollama.value = s.data.ollama; config.value = s.data.config }
    numParallelCtl.value = config.value.num_parallel ?? 4
    numCtxCtl.value = config.value.num_ctx ?? 8192
  } catch (e) { ollama.value.reachable = false }
  try {
    const me = await getLlmMetrics(80)
    if (me.data?.success) { m.value = me.data.metrics; config.value = me.data.config }
  } catch (e) { /* ignore */ }
}

const togglePause = () => { paused.value = !paused.value; if (!paused.value && !timer) start() }

const toggleThinking = async () => {
  const next = config.value.thinking_off ? 'high' : 'none'
  await setLlmConfig({ reasoning_effort: next }); await refresh()
}
const applyTuning = async () => {
  await setLlmConfig({ num_parallel: numParallelCtl.value, num_ctx: numCtxCtl.value }); await refresh()
}

const start = () => { if (timer) return; timer = setInterval(() => { if (!paused.value) refresh() }, 2000) }
const stop = () => { if (timer) { clearInterval(timer); timer = null } }

onMounted(async () => { await refresh(); start() })
onUnmounted(stop)

// ---- formatting helpers ----
const bytes = (v) => { if (v == null) return '—'; const u = ['B','KB','MB','GB','TB']; let i=0,n=v; while(n>=1024&&i<u.length-1){n/=1024;i++} return n.toFixed(n<10?2:0)+' '+u[i] }
const num = (v) => (v == null ? 0 : v).toLocaleString()
const fmtUptime = (s) => { if(!s) return '—'; const h=Math.floor(s/3600), mm=Math.floor((s%3600)/60), ss=Math.floor(s%60); return `${h}h ${mm}m ${ss}s` }
const twoFix = (v) => (typeof v === 'number' ? v.toFixed(2) : (v ?? 0))
</script>

<style scoped>
.monitor { height: 100vh; display: flex; flex-direction: column; background: #FFF; color: #000;
  font-family: 'JetBrains Mono', 'Noto Sans SC', monospace; }
.m-header { height: 56px; border-bottom: 1px solid #EAEAEA; display: flex; align-items: center; gap: 16px; padding: 0 20px; }
.brand { font-weight: 800; letter-spacing: 1px; font-size: 16px; }
.title { font-size: 13px; color: #666; }
.spacer { flex: 1; }
.conn { font-size: 12px; display: inline-flex; align-items: center; gap: 6px; color: #444; }
.conn .dot { width: 8px; height: 8px; border-radius: 50%; background: #999; display: inline-block; }
.conn.ok .dot { background: #16a34a; } .conn.bad .dot { background: #dc2626; }
.link-btn, .mini, .toggle { border: 1px solid #000; background: #FFF; padding: 6px 12px; font-size: 12px; cursor: pointer; font-family: inherit; }
.link-btn:hover, .mini:hover { background: #000; color: #FFF; }
.m-body { flex: 1; overflow: auto; padding: 16px; display: grid; gap: 14px; grid-template-columns: 1fr 1fr; align-content: start; }
.card { border: 1px solid #EAEAEA; border-radius: 8px; padding: 14px; background: #FFF; }
.card.wide { grid-column: 1 / span 2; }
.card-h { font-size: 13px; font-weight: 700; margin-bottom: 10px; }
.card-h .hint { font-weight: 400; color: #999; font-size: 11px; }
.kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }
.kv { display: flex; justify-content: space-between; font-size: 12px; border-bottom: 1px dashed #EEE; padding: 4px 0; }
.kv span { color: #888; } .kv b { font-weight: 600; }
.on { color: #d97706; font-weight: 700; } .off { color: #16a34a; font-weight: 700; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.stat { border: 1px solid #EEE; border-radius: 6px; padding: 10px; }
.stat i { display: block; font-style: normal; font-size: 11px; color: #888; margin-bottom: 4px; }
.stat b { font-size: 16px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #F0F0F0; white-space: nowrap; }
.tbl th { color: #888; font-weight: 600; }
.tbl tr.err td { background: #fff5f5; }
.tablewrap { max-height: 300px; overflow: auto; }
.loaded { margin-top: 10px; }
.empty, .note { font-size: 12px; color: #888; margin-top: 8px; }
.ctrl-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.ctrl-row label { font-size: 12px; color: #444; display: inline-flex; align-items: center; gap: 6px; }
.ctrl-row input { width: 72px; border: 1px solid #DDD; border-radius: 4px; padding: 4px 6px; font-family: inherit; }
.toggle.t-off { border-color: #16a34a; color: #16a34a; } .toggle.t-on { border-color: #d97706; color: #d97706; }
@media (max-width: 980px) { .m-body { grid-template-columns: 1fr; } .card.wide { grid-column: auto; } .grid { grid-template-columns: repeat(2,1fr);} }
</style>
