import { createApp } from 'vue'

// Self hosted so the tool still renders correctly on an isolated lab network.
import '@fontsource/instrument-serif/400.css'
import '@fontsource/instrument-serif/400-italic.css'
import '@fontsource/instrument-sans/400.css'
import '@fontsource/instrument-sans/500.css'
import '@fontsource/instrument-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'

import './styles/base.css'

import App from './App.vue'
import router from './router'

createApp(App).use(router).mount('#app')
