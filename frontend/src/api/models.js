// SPDX-License-Identifier: AGPL-3.0-or-later
import service from './index'

/**
 * Model Manager API client — inventory, HF downloads (job registry),
 * roles, unload/delete, and settings (B1/B2/B3).
 * All calls stay on the local backend; nothing leaves the machine.
 */

export function listModels() {
  return service({ url: '/api/models', method: 'get' })
}

export function getStatus() {
  return service({ url: '/api/models/status', method: 'get' })
}

export function startDownload(repoId, role) {
  return service({
    url: '/api/models/download',
    method: 'post',
    data: role ? { repo_id: repoId, role } : { repo_id: repoId }
  })
}

export function listDownloads() {
  return service({ url: '/api/models/downloads', method: 'get' })
}

export function getDownload(jobId) {
  return service({ url: `/api/models/downloads/${encodeURIComponent(jobId)}`, method: 'get' })
}

export function cancelDownload(jobId) {
  return service({ url: `/api/models/downloads/${encodeURIComponent(jobId)}/cancel`, method: 'post' })
}

export function setRole(name, role) {
  return service({ url: '/api/models/role', method: 'post', data: { name, role } })
}

export function unloadModel(name) {
  return service({ url: '/api/models/unload', method: 'post', data: { name } })
}

export function deleteModel(name) {
  return service({ url: `/api/models/${encodeURIComponent(name)}`, method: 'delete' })
}

export function getSettings() {
  return service({ url: '/api/models/settings', method: 'get' })
}

export function updateSettings(patch) {
  return service({ url: '/api/models/settings', method: 'put', data: patch })
}
