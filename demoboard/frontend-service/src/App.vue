<template>
  <main class="app-shell">
    <header class="hero">
      <p class="eyebrow">TP Demoboard</p>
      <h1>Tableau de bord des tâches</h1>
      <p class="subtitle">
        Ajoutez des tâches, lancez un traitement long et observez leur statut en
        direct.
      </p>
    </header>
    <TaskList />
  </main>
</template>

<script setup>
import TaskList from "./components/TaskList.vue";
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  padding: 2rem 1rem 3rem;
}

.hero {
  text-align: center;
  margin-bottom: 2rem;
}

.hero h1 {
  margin: 0.25rem 0;
  font-size: clamp(2rem, 4vw, 2.75rem);
}

.subtitle {
  color: #475569;
  margin-top: 0.5rem;
}

.eyebrow {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #2563eb;
  font-weight: 600;
}
</style>
