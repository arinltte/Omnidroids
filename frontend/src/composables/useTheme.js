// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * §9.2 appearance mode: System / Light / Dark.
 *
 * - mode is persisted in localStorage ('appearance') and applied as a `dark`
 *   class on <html> (CSS variables live in assets/theme.css);
 * - 'system' follows prefers-color-scheme live;
 * - themeVersion is a counter components can watch to re-render canvas/SVG
 *   content whose colors cannot come from CSS (d3 graph attributes).
 */

import { ref, computed } from 'vue'

const STORAGE_KEY = 'appearance'
const MODES = ['system', 'light', 'dark']

const mode = ref(readStored())
const systemDark = ref(false)
const themeVersion = ref(0)

let mediaQuery = null

function readStored() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && MODES.includes(stored)) return stored
  } catch (e) { /* private mode — fall through to system */ }
  return 'system'
}

export function isDark() {
  return computed(
    () => mode.value === 'dark' || (mode.value === 'system' && systemDark.value)
  )
}

function apply() {
  const dark = isDark().value
  document.documentElement.classList.toggle('dark', dark)
  themeVersion.value += 1
}

export function setMode(next) {
  if (!MODES.includes(next)) return
  mode.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch (e) { /* ignore persistence failures */ }
  apply()
}

export function currentMode() {
  return computed(() => mode.value)
}

export function initTheme() {
  if (typeof window !== 'undefined' && window.matchMedia) {
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    systemDark.value = mediaQuery.matches
    mediaQuery.addEventListener('change', (e) => {
      systemDark.value = e.matches
      apply()
    })
  }
  apply()
}

export { themeVersion, MODES }
