// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * §9.2 appearance mode — **light only** (owner instruction, 2026-11-15).
 *
 * The dark mode (and the System/Dark switcher that drove it) was removed; the
 * original white theme in `assets/theme.css` is the single appearance. This
 * module stays the seam the app already imports, so nothing else changes:
 *
 * - `initTheme()` clears any previously stored `system`/`dark` mode, removes a
 *   leftover `dark` class from `<html>` and flips `themeVersion` once so the
 *   graph canvases (d3/SVG colors that cannot come from CSS) re-render on boot;
 * - `isDark()` is always false, `currentMode()` always `light`, `setMode`
 *   only accepts `light` — a `dark` request is ignored, not stored.
 */

import { ref, computed } from 'vue'

const STORAGE_KEY = 'appearance'

const LIGHT = 'light'
const MODES = [LIGHT]

const mode = ref(LIGHT)
const themeVersion = ref(0)

function readStored() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && MODES.includes(stored)) return stored
  } catch (e) { /* private mode — fall through to light */ }
  return LIGHT
}

export function isDark() {
  return computed(() => false)
}

function apply() {
  document.documentElement.classList.remove('dark')
  themeVersion.value += 1
}

export function setMode(next) {
  if (next !== LIGHT) return
  mode.value = LIGHT
  try {
    localStorage.setItem(STORAGE_KEY, LIGHT)
  } catch (e) { /* ignore persistence failures */ }
  apply()
}

export function currentMode() {
  return computed(() => mode.value)
}

export function initTheme() {
  mode.value = readStored()
  // A previous session may have left 'system'/'dark' stored or the class on
  // <html>; the white theme is the only appearance, so both are cleared.
  try {
    localStorage.setItem(STORAGE_KEY, LIGHT)
  } catch (e) { /* ignore persistence failures */ }
  apply()
}

export { themeVersion, MODES }