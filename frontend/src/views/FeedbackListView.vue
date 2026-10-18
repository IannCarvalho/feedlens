<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable, { type DataTablePageEvent } from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import Message from 'primevue/message'

import { useFeedbacksStore } from '@/stores/feedbacks'
import { HttpError } from '@/lib/http'
import { formatDate, sentimentMeta } from '@/lib/sentiment'
import { analysisErrorLabel } from '@/lib/analysis'
import type {
  Feedback,
  FeedbackAnalysisFilter,
  FeedbackSpamFilter,
  Sentiment,
} from '@/types/api'

const store = useFeedbacksStore()
const route = useRoute()
const router = useRouter()
const error = ref<string | null>(null)
const perPage = 20

const SENTIMENTS: readonly Sentiment[] = ['POSITIVO', 'NEGATIVO', 'INCONCLUSIVO']
const SPAM_FILTERS: readonly FeedbackSpamFilter[] = ['exclude', 'include', 'only']

function sentimentFromUrl(): Sentiment | null {
  const value = route.query.sentiment
  return typeof value === 'string' && (SENTIMENTS as readonly string[]).includes(value)
    ? (value as Sentiment)
    : null
}

function spamFromUrl(): FeedbackSpamFilter {
  const value = route.query.spam
  return typeof value === 'string' && (SPAM_FILTERS as readonly string[]).includes(value)
    ? (value as FeedbackSpamFilter)
    : 'exclude'
}

function analysisFromUrl(): FeedbackAnalysisFilter {
  return route.query.analysis === 'unanalyzed' ? 'unanalyzed' : 'any'
}

// Os filtros vivem na URL (?sentiment=&spam=&analysis=), então links salvos e o reload os mantêm.
const sentimentFilter = ref<Sentiment | null>(sentimentFromUrl())
const spamFilter = ref<FeedbackSpamFilter>(spamFromUrl())
const analysisFilter = ref<FeedbackAnalysisFilter>(analysisFromUrl())
const sentimentOptions = [
  { label: 'Todos os sentimentos', value: null },
  { label: 'Positivo', value: 'POSITIVO' as const },
  { label: 'Negativo', value: 'NEGATIVO' as const },
  { label: 'Inconclusivo', value: 'INCONCLUSIVO' as const },
]
const spamOptions = [
  { label: 'Sem spam', value: 'exclude' as const },
  { label: 'Com spam', value: 'include' as const },
  { label: 'Só spam', value: 'only' as const },
]
const analysisOptions = [
  { label: 'Qualquer análise', value: 'any' as const },
  { label: 'Não analisados', value: 'unanalyzed' as const },
]

async function load(page = 1) {
  error.value = null
  try {
    await store.list({
      page,
      perPage,
      sentiment: sentimentFilter.value,
      spam: spamFilter.value,
      analysis: analysisFilter.value,
    })
  } catch (e) {
    error.value =
      e instanceof HttpError
        ? e.message
        : 'Não foi possível carregar os feedbacks. Recarregue a página; se persistir, verifique se o servidor está no ar.'
  }
}

function onPage(event: DataTablePageEvent) {
  load(event.page + 1)
}

function onFilterChange() {
  const query: Record<string, string> = {}
  if (sentimentFilter.value) query.sentiment = sentimentFilter.value
  if (spamFilter.value !== 'exclude') query.spam = spamFilter.value
  if (analysisFilter.value !== 'any') query.analysis = analysisFilter.value
  router.replace({ query })
  load(1)
}

function openDetail(feedback: Feedback) {
  router.push({ name: 'feedback-detail', params: { id: feedback.id } })
}

function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

onMounted(() => load(1))
</script>

<template>
  <section class="fl-page">
    <header class="fl-page__head">
      <h1>Feedbacks</h1>
      <div class="fl-filters">
        <Select
          v-model="analysisFilter"
          :options="analysisOptions"
          option-label="label"
          option-value="value"
          class="fl-filter"
          aria-label="Filtrar por análise da IA"
          @change="onFilterChange"
        />
        <Select
          v-model="spamFilter"
          :options="spamOptions"
          option-label="label"
          option-value="value"
          class="fl-filter"
          aria-label="Filtrar por spam"
          @change="onFilterChange"
        />
        <Select
          v-model="sentimentFilter"
          :options="sentimentOptions"
          option-label="label"
          option-value="value"
          class="fl-filter"
          placeholder="Todos os sentimentos"
          aria-label="Filtrar por sentimento"
          @change="onFilterChange"
        />
      </div>
    </header>

    <Message v-if="analysisFilter === 'unanalyzed'" severity="info" :closable="false" class="fl-note">
      Estes feedbacks chegaram, mas a IA não conseguiu analisá-los (cota excedida, tempo esgotado
      etc.). O sentimento “Inconclusivo” deles é só o valor padrão. Abra um e use “Reanalisar com
      a IA”, ou rode <code>flask reanalyze-pending</code> no servidor para todos de uma vez.
    </Message>

    <Message v-if="spamFilter !== 'exclude'" severity="info" :closable="false" class="fl-note">
      <template v-if="spamFilter === 'only'">
        Estes são os textos que a IA marcou como spam. Eles ficam fora das listas normais e dos
        gráficos. Abra um deles e use “Não é spam” se foi engano.
      </template>
      <template v-else>
        Esta lista mostra também o que a IA marcou como spam, com o selo “Spam”. Os gráficos
        da Visão geral continuam sem eles.
      </template>
    </Message>

    <Message v-if="error" severity="warn" :closable="false">{{ error }}</Message>

    <DataTable
      :value="store.items"
      :loading="store.loading"
      lazy
      paginator
      :rows="perPage"
      :total-records="store.pagination?.total ?? 0"
      :first="((store.pagination?.page ?? 1) - 1) * perPage"
      data-key="id"
      selection-mode="single"
      @page="onPage"
      @row-select="openDetail($event.data)"
    >
      <template #empty>
        <span v-if="analysisFilter === 'unanalyzed'">
          Nenhum feedback aguardando análise. Tudo o que chegou foi analisado pela IA.
        </span>
        <span v-else-if="spamFilter === 'only'">
          Nenhum feedback marcado como spam. Quando a IA classificar um texto como ruído, ele
          aparece aqui para você revisar.
        </span>
        <span v-else-if="sentimentFilter">
          Nenhum feedback com esse sentimento. Troque o filtro para “Todos os sentimentos”
          para ver todos.
        </span>
        <span v-else>
          Nenhum feedback ainda. Assim que chegarem pela API
          (<code>POST /api/feedbacks</code>), eles aparecem nesta lista.
        </span>
      </template>

      <Column header="Sentimento" style="width: 17rem">
        <template #body="{ data }">
          <span :class="sentimentMeta(data.sentiment).chipClass">
            <i :class="sentimentMeta(data.sentiment).icon" aria-hidden="true" />
            {{ sentimentMeta(data.sentiment).label }}
          </span>
          <span v-if="data.is_spam" class="fl-sentiment fl-sentiment--noise fl-spam-chip">
            <i class="pi pi-ban" aria-hidden="true" /> Spam
          </span>
          <span
            v-if="data.analysis_status === 'unanalyzed'"
            class="fl-sentiment fl-sentiment--pending fl-spam-chip"
            :title="`A IA não analisou: ${analysisErrorLabel(data.analysis_error)}`"
          >
            <i class="pi pi-clock" aria-hidden="true" /> Não analisado
          </span>
        </template>
      </Column>

      <Column header="Feedback">
        <template #body="{ data }">{{ truncate(data.feedback) }}</template>
      </Column>

      <Column header="Funcionalidades" style="width: 9rem">
        <template #body="{ data }">
          <span class="fl-count">{{ data.requested_features.length }}</span>
        </template>
      </Column>

      <Column header="Data" style="width: 12rem">
        <template #body="{ data }">
          <time class="fl-date">{{ formatDate(data.created_at) }}</time>
        </template>
      </Column>
    </DataTable>
  </section>
</template>

<style scoped>
.fl-page__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.25rem;
}
.fl-page__head h1 {
  margin: 0;
  font-size: 1.5rem;
}
.fl-filters {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.75rem;
}
.fl-filter {
  min-width: 12rem;
}
.fl-note {
  margin-bottom: 1rem;
}
.fl-spam-chip {
  margin-left: 0.4rem;
}
.fl-count {
  font-family: var(--fl-font-mono);
  color: var(--fl-text-muted);
}
.fl-date {
  font-family: var(--fl-font-mono);
  font-size: 0.82rem;
  color: var(--fl-text-muted);
}
</style>
