import axios from 'axios'
import i18n from '../i18n'

// 后端本体生成是「一次同步 LLM 调用」：本地 1.7B 模型（LFM3）实测约 11 分钟
// （含 LLM 代理 600s 上限与 OpenAI SDK 重试）。早期 5 分钟的上限会让前端在后端
// 仍在运行时提前放弃，而后端其实会继续跑完并成功——用户只看到一条超时报错。
// 因此这里默认给足 30 分钟，并可用 VITE_API_TIMEOUT_MS 覆盖。
const DEFAULT_TIMEOUT_MS = 1800000 // 30 分钟

// 创建axios实例
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001',
  timeout: Number(import.meta.env.VITE_API_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    const apiError = error.response?.data?.error || error.response?.data?.message
    
    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
      // 前端只是停止等待，服务端任务通常仍在跑（本地模型推理远慢于云端耗时）。
      // 换成可操作提示，不要再把原始 "timeout of 300000ms exceeded" 抛给界面。
      error.message =
        '请求等待超时（本地模型推理较慢）。服务端可能仍在处理中——请稍后刷新页面查看进度，切勿重复上传，以免创建重复项目。'
    }
    
    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }

    // Axios rejects non-2xx responses before the success interceptor can
    // surface the backend's safe, actionable error message.
    if (typeof apiError === 'string' && apiError) {
      error.message = apiError
    }
    
    return Promise.reject(error)
  }
)

export default service
