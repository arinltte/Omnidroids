<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<template>
  <div class="appearance-switcher" role="group" :aria-label="$t('appearance.label')">
    <button
      v-for="m in modes"
      :key="m"
      class="mode-btn"
      :class="{ active: current === m }"
      :title="$t('appearance.' + m)"
      @click="setMode(m)"
    >
      {{ $t('appearance.' + m) }}
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { MODES, currentMode, setMode as applyMode } from '../composables/useTheme'

const current = currentMode()
const modes = MODES
const setMode = applyMode
</script>

<style scoped>
.appearance-switcher {
  display: inline-flex;
  border: 1px solid var(--border);
}

.mode-btn {
  background: transparent;
  border: none;
  color: var(--black);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.06em;
  padding: 4px 8px;
  cursor: pointer;
  opacity: 0.6;
}

.mode-btn + .mode-btn {
  border-left: 1px solid var(--border);
}

.mode-btn.active {
  background: var(--black);
  color: var(--white);
  opacity: 1;
}
</style>
