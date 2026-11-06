// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * d3/SVG colors that CSS variables cannot reach. Reads the live computed
 * tokens off <html> so a theme flip changes canvas content on re-render.
 */

const FALLBACKS = {
  white: '#FFFFFF',
  black: '#000000',
  grayText: '#666666',
  inkMuted: '#333333',
  surfaceVeil: 'rgba(255, 255, 255, 0.95)',
  border: '#E5E5E5'
}

export function themeTokens() {
  if (typeof window === 'undefined' || !window.getComputedStyle) {
    return { ...FALLBACKS }
  }
  const style = getComputedStyle(document.documentElement)
  const read = (name, fallback) =>
    style.getPropertyValue(name).trim() || fallback
  return {
    white: read('--white', FALLBACKS.white),
    black: read('--black', FALLBACKS.black),
    grayText: read('--gray-text', FALLBACKS.grayText),
    inkMuted: read('--ink-muted', FALLBACKS.inkMuted),
    surfaceVeil: read('--surface-veil', FALLBACKS.surfaceVeil),
    border: read('--border', FALLBACKS.border)
  }
}
