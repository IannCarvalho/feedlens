<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Card from 'primevue/card'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import SentimentChart from '@/components/SentimentChart.vue'
import FeaturesChart from '@/components/FeaturesChart.vue'
import { useStatsStore } from '@/stores/stats'
import { HttpError } from '@/lib/http'
import { formatDate, sentimentMeta } from '@/lib/sentiment'

const store = useStatsStore()
const error = ref<string | null>(null)

const stats = computed(() => store.data)
const hasData = computed(() => stats.value?.has_data === true)
const unanalyzed = computed(() => stats.value?.unanalyzed ?? 0)

onMounted(async () => {
  try {
    await store.load()
  } catch (e) {
    error.value =
      e instanceof HttpError
        ? e.message
        : 'Não foi possível carregar as estatísticas. Recarregue a página; se persistir, verifique se o servidor está no ar.'
  }
})

function truncate(text: string, max = 60): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}
</script>

<template>
  <section class="fl-page">
    <header class="fl-page__head">
      <h1>Visão geral</h1>
      <p class="fl-muted">Foco no que seus usuários pedem, com a IA que você escolher.</p>
    </header>

    <Message
      v-if="!store.loading && !error && unanalyzed > 0"
      severity="warn"
      :closable="false"
      class="fl-unanalyzed"
    >
      <strong>{{ unanalyzed }}</strong>
      {{ unanalyzed === 1 ? 'feedback não foi analisado' : 'feedbacks não foram analisados' }} pela
      IA (cota excedida, tempo esgotado etc.).
      {{ unanalyzed === 1 ? 'Ele aparece' : 'Eles aparecem' }} como “Inconclusivo” só por padrão.
      <RouterLink :to="{ path: '/feedbacks', query: { analysis: 'unanalyzed' } }" class="fl-link">
        Ver e reanalisar
      </RouterLink>
    </Message>

    <div v-if="store.loading" class="fl-loading">
      <ProgressSpinner style="width: 2.5rem; height: 2.5rem" />
    </div>

    <Message v-else-if="error" severity="warn" :closable="false">{{ error }}</Message>

    <Message v-else-if="!hasData" severity="info" :closable="false">
      Ainda não há feedbacks para analisar. Assim que o primeiro chegar pela API
      (<code>POST /api/feedbacks</code>), os números e gráficos aparecem aqui.
    </Message>

    <template v-else-if="stats">
      <!-- Cards -->
      <div class="fl-cards">
        <Card class="fl-stat">
          <template #content>
            <div class="fl-stat__label">Total de feedbacks</div>
            <div class="fl-stat__value">{{ stats.total_feedbacks }}</div>
          </template>
        </Card>
        <Card class="fl-stat">
          <template #content>
            <div class="fl-stat__label">Positivos</div>
            <div class="fl-stat__value" style="color: var(--fl-positive)">
              {{ stats.sentiment_percentages?.POSITIVO.toFixed(1) }}%
            </div>
            <div class="fl-stat__sub">{{ stats.sentiment_counts?.POSITIVO }} feedbacks</div>
          </template>
        </Card>
        <Card class="fl-stat">
          <template #content>
            <div class="fl-stat__label">Negativos</div>
            <div class="fl-stat__value" style="color: var(--fl-negative)">
              {{ stats.sentiment_percentages?.NEGATIVO.toFixed(1) }}%
            </div>
            <div class="fl-stat__sub">{{ stats.sentiment_counts?.NEGATIVO }} feedbacks</div>
          </template>
        </Card>
      </div>

      <!-- Gráficos -->
      <div class="fl-charts">
        <Card>
          <template #title>Distribuição de sentimentos</template>
          <template #content>
            <SentimentChart v-if="stats.sentiment_counts" :counts="stats.sentiment_counts" />
          </template>
        </Card>
        <Card>
          <template #title>Funcionalidades mais solicitadas</template>
          <template #content>
            <FeaturesChart v-if="stats.top_features" :features="stats.top_features" />
          </template>
        </Card>
      </div>

      <!-- Recentes -->
      <Card class="fl-recent">
        <template #title>
          <div class="fl-recent__head">
            <span>Feedbacks recentes</span>
            <RouterLink to="/feedbacks" class="fl-link">Ver todos</RouterLink>
          </div>
        </template>
        <template #content>
          <ul class="fl-recent__list">
            <li v-for="f in stats.recent_feedbacks" :key="f.id" class="fl-recent__row">
              <span :class="sentimentMeta(f.sentiment).chipClass">
                <i :class="sentimentMeta(f.sentiment).icon" aria-hidden="true" />
                {{ sentimentMeta(f.sentiment).label }}
              </span>
              <span class="fl-recent__text">{{ truncate(f.feedback) }}</span>
              <time class="fl-recent__date">{{ formatDate(f.created_at) }}</time>
            </li>
          </ul>
        </template>
      </Card>
    </template>
  </section>
</template>

<style scoped>
.fl-page__head h1 {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
}
.fl-muted {
  color: var(--fl-text-muted);
  margin: 0 0 1.5rem;
}
.fl-loading {
  display: flex;
  justify-content: center;
  padding: 3rem;
}
.fl-unanalyzed {
  margin-bottom: 1rem;
}
.fl-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 1rem;
}
.fl-stat__label {
  font-size: 0.8rem;
  color: var(--fl-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.fl-stat__value {
  font-size: 2rem;
  font-weight: 700;
  margin-top: 0.25rem;
}
.fl-stat__sub {
  font-size: 0.8rem;
  color: var(--fl-text-muted);
}
.fl-charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1rem;
}
.fl-recent__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.fl-link {
  font-size: 0.85rem;
  color: var(--fl-cyan);
  text-decoration: none;
}
.fl-link:hover {
  text-decoration: underline;
}
.fl-recent__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.fl-recent__row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--fl-border);
}
.fl-recent__row:last-child {
  border-bottom: none;
}
.fl-recent__text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.9rem;
}
.fl-recent__date {
  font-size: 0.78rem;
  color: var(--fl-text-muted);
  font-family: var(--fl-font-mono);
  white-space: nowrap;
}
@media (max-width: 860px) {
  .fl-cards,
  .fl-charts {
    grid-template-columns: 1fr;
  }
}
</style>
