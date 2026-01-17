<template>
  <section class="card">
    <form class="add-task" @submit.prevent="addTask">
      <input
        v-model.trim="title"
        type="text"
        placeholder="Nouvelle tâche"
        :disabled="loading"
        required
      />
      <button type="submit" :disabled="loading || !title">Ajouter</button>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="workerDisabledMessage" class="hint">
      {{ workerDisabledMessage }}
    </p>

    <div v-if="tasks.length === 0" class="empty-state">
      Aucune tâche pour le moment.
    </div>
    <ul v-else class="task-list">
      <li v-for="task in tasks" :key="task.id" class="task-item">
        <div class="task-meta">
          <strong>{{ task.title }}</strong>
          <span class="status" :data-status="task.status">{{ task.status }}</span>
        </div>
        <div class="actions">
          <button
            v-if="enableWorker"
            @click="startJob(task.id)"
            :disabled="task.status !== 'pending' || loading"
          >
            Lancer traitement
          </button>
          <button
            class="secondary"
            @click="deleteTask(task.id)"
            :disabled="loading"
          >
            Supprimer
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const API_BASE = import.meta.env.VITE_API_URL ?? "/api";
const enableWorker =
  (import.meta.env.VITE_ENABLE_WORKER ?? "true").toString().toLowerCase() === "true";

const tasks = ref([]);
const title = ref("");
const loading = ref(false);
const error = ref("");
let intervalId = null;

const handleResponse = async (response) => {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Erreur réseau");
  }
  return response;
};

const loadTasks = async () => {
  const response = await handleResponse(await fetch(`${API_BASE}/tasks`));
  tasks.value = await response.json();
};

const addTask = async () => {
  if (!title.value) return;
  loading.value = true;
  error.value = "";
  try {
    await handleResponse(
      await fetch(`${API_BASE}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.value }),
      }),
    );
    title.value = "";
    await loadTasks();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
};

const startJob = async (taskId) => {
  if (!enableWorker) {
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    await handleResponse(
      await fetch(`${API_BASE}/tasks/${taskId}/start-job`, {
        method: "POST",
      }),
    );
    await loadTasks();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
};

const deleteTask = async (taskId) => {
  loading.value = true;
  error.value = "";
  try {
    await handleResponse(
      await fetch(`${API_BASE}/tasks/${taskId}`, {
        method: "DELETE",
      }),
    );
    await loadTasks();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
};

const workerDisabledMessage = computed(() =>
  enableWorker
    ? ""
    : "Mode light : le traitement long est désactivé. Utilisez cette interface pour créer ou supprimer vos tâches.",
);

onMounted(async () => {
  await loadTasks();
  intervalId = setInterval(loadTasks, 4000);
});

onUnmounted(() => {
  if (intervalId) {
    clearInterval(intervalId);
  }
});
</script>

<style scoped>
.card {
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08);
  padding: 1.5rem;
  max-width: 720px;
  margin: 0 auto;
}

.add-task {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}

.add-task input {
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  border: 1px solid #cbd5f5;
  font-size: 1rem;
}

button {
  background-color: #2563eb;
  border: none;
  border-radius: 8px;
  color: #fff;
  cursor: pointer;
  padding: 0.75rem 1rem;
  font-weight: 600;
}

button:disabled {
  background-color: #94a3b8;
  cursor: not-allowed;
}

.task-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.task-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.task-meta {
  flex: 1;
}

.actions {
  display: flex;
  gap: 0.5rem;
}

.status {
  margin-left: 0.5rem;
  text-transform: capitalize;
  font-size: 0.9rem;
  color: #475569;
}

.secondary {
  background-color: #f8fafc;
  color: #1e293b;
  border: 1px solid #94a3b8;
}

.status[data-status="completed"] {
  color: #22c55e;
}

.status[data-status="processing"] {
  color: #f97316;
}

.empty-state {
  text-align: center;
  color: #475569;
  padding: 1rem;
  border: 1px dashed #cbd5f5;
  border-radius: 8px;
}

.error {
  color: #dc2626;
  margin-bottom: 0.5rem;
}

.hint {
  margin-top: 0.5rem;
  font-size: 0.9rem;
  color: #475569;
}
</style>
