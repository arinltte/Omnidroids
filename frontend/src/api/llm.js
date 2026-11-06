import axios from 'axios'

// AI Monitor API client (本地 LLM 使用量 / 状态 / 实时切换)
const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

export function getLlmStatus () {
  return http.get('/api/llm/status')
}

export function getLlmMetrics (recent = 80) {
  return http.get('/api/llm/metrics', { params: { recent } })
}

export function getLlmConfig () {
  return http.get('/api/llm/config')
}

export function setLlmConfig (cfg) {
  return http.post('/api/llm/config', cfg)
}
