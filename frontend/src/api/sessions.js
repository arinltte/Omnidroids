// SPDX-License-Identifier: AGPL-3.0-or-later
import service from './index'

/**
 * Sessions API client (§8) — the main page's data source.
 * Sessions are a durable projection over projects/tasks; read on every poll.
 */

export function listSessions(limit = 50) {
  return service({ url: '/api/sessions', method: 'get', params: { limit } })
}

export function getSession(sessionId) {
  return service({ url: `/api/sessions/${encodeURIComponent(sessionId)}`, method: 'get' })
}

export function resumeSession(sessionId, options = {}) {
  return service({
    url: `/api/sessions/${encodeURIComponent(sessionId)}/resume`,
    method: 'post',
    data: options.force ? { force: true } : {}
  })
}

export function deleteSession(sessionId, withProject = false) {
  return service({
    url: `/api/sessions/${encodeURIComponent(sessionId)}`,
    method: 'delete',
    params: withProject ? { with_project: 1 } : {}
  })
}
