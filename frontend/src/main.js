import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { initTheme } from './composables/useTheme'
import './assets/theme.css'

// §9.2: apply the stored/system appearance before the first paint so there is
// no light flash on a dark-mode machine.
initTheme()

const app = createApp(App)

app.use(router)
app.use(i18n)

app.mount('#app')
