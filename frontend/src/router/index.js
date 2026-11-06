import { createRouter, createWebHistory } from 'vue-router'
import SessionsView from '../views/SessionsView.vue'
import Home from '../views/Home.vue'
import Process from '../views/MainView.vue'
import ModelsView from '../views/ModelsView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import MonitorView from '../views/MonitorView.vue'

const routes = [
  {
    // §8.3: the main page the launcher opens — session history + resume
    path: '/',
    name: 'Sessions',
    component: SessionsView
  },
  {
    // the new-prediction console (upload + simulation requirement)
    path: '/new',
    name: 'NewPrediction',
    component: Home
  },
  {
    path: '/models',
    name: 'Models',
    component: ModelsView
  },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
  },
  {
    path: '/monitor',
    name: 'Monitor',
    component: MonitorView,
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
