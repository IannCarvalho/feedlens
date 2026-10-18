<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import Card from 'primevue/card'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Button from 'primevue/button'
import Select from 'primevue/select'

import { useFeedbacksStore } from '@/stores/feedbacks'
import { HttpError } from '@/lib/http'
import { formatDate, sentimentMeta } from '@/lib/sentiment'
import { analysisErrorLabel } from '@/lib/analysis'
import type { Feedback, Sentiment } from '@/types/api'

const store = useFeedbacksStore()
const route = useRoute()
const feedback = ref<Feedback | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const reviewing = ref(false)
const reviewError = ref<string | null>(null)
const reviewNotice = ref<string | null>(null)
const reanalyzing = ref(false)

const sentimentOptions: { label: string; value: Sentiment }[] = [
  { label: 'Positivo', value: 'POSITIVO' },
  { label: 'Negativo', value: 'NEGATIVO' },
  { label: 'Inconclusivo', value: 'INCONCLUSIVO' },
]

const REVIEW_ERROR =
  'Não foi possível salvar a correção. Tente de novo; se persistir, verifique se o servidor está no ar.'

/** Roda a IA de novo (a chamada espera a resposta do provedor, até alguns segundos). */
async function reanalyze() {
  const current = feedback.value
  if (!current || reanalyzing.value || reviewing.value) return
  reanalyzing.value = true
  reviewError.value = null
  reviewNotice.value = null
  try {
    feedback.value = await store.reanalyze(current.id)
    reviewNotice.value = 'Reanalisado pela IA. Sentimento, spam e funcionalidades foram atualizados.'
  } catch (e) {
    if (e instanceof HttpError && e.code === 'analysis_unavailable') {
      const kind = e.body?.detail
      current.analysis_error = kind ?? current.analysis_error
      reviewError.value = `A IA ainda não respondeu: ${analysisErrorLabel(kind)}. O feedback continua como não analisado; tente de novo mais tarde.`
    } else {
      reviewError.value = e instanceof HttpError ? e.message : REVIEW_ERROR
    }
  } finally {
    reanalyzing.value = false
  }
}

/** Revisão humana: confirma ou desfaz o spam marcado pela IA. */
async function review(isSpam: boolean) {
  if (!feedback.value || reviewing.value) return
  reviewing.value = true
  reviewError.value = null
  reviewNotice.value = null
  try {
    feedback.value = await store.setSpam(feedback.value.id, isSpam)
    reviewNotice.value = isSpam
      ? 'Marcado como spam. Ele sai das listas normais e dos gráficos.'
      : 'Restaurado. Ele volta às listas e aos gráficos.'
  } catch (e) {
    reviewError.value = e instanceof HttpError ? e.message : REVIEW_ERROR
  } finally {
    reviewing.value = false
  }
}

/** Revisão humana: troca o sentimento que a IA atribuiu. */
async function changeSentiment(sentiment: Sentiment | null) {
  const current = feedback.value
  if (!current || !sentiment || sentiment === current.sentiment || reviewing.value) return
  reviewing.value = true
  reviewError.value = null
  reviewNotice.value = null
  try {
    feedback.value = await store.setSentiment(current.id, sentiment)
    reviewNotice.value = `Sentimento alterado para ${sentimentMeta(sentiment).label}. Os gráficos já refletem a correção.`
  } catch (e) {
    reviewError.value = e instanceof HttpError ? e.message : REVIEW_ERROR
  } finally {
    reviewing.value = false
  }
}

onMounted(async () => {
  try {
    feedback.value = await store.getById(route.params.id as string)
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) {
      error.value =
        'Esse feedback não existe ou foi removido. Use “Voltar aos feedbacks” para ver a lista atual.'
    } else {
      error.value =
        e instanceof HttpError
          ? e.message
          : 'Não foi possível carregar o feedback. Recarregue a página; se persistir, verifique se o servidor está no ar.'
    }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="fl-page">
    <RouterLink to="/feedbacks" class="fl-back">
      <Button label="Voltar aos feedbacks" icon="pi pi-arrow-left" text size="small" />
    </RouterLink>

    <div v-if="loading" class="fl-loading">
      <ProgressSpinner style="width: 2.5rem; height: 2.5rem" />
    </div>

    <Message v-else-if="error" severity="warn" :closable="false">{{ error }}</Message>

    <template v-else-if="feedback">
      <header class="fl-detail__head">
        <span :class="sentimentMeta(feedback.sentiment).chipClass">
          <i :class="sentimentMeta(feedback.sentiment).icon" aria-hidden="true" />
          {{ sentimentMeta(feedback.sentiment).label }}
        </span>
        <span v-if="feedback.is_spam" class="fl-sentiment fl-sentiment--noise">
          <i class="pi pi-times" aria-hidden="true" /> Ruído (spam)
        </span>
        <span v-if="feedback.analysis_status === 'unanalyzed'" class="fl-sentiment fl-sentiment--pending">
          <i class="pi pi-clock" aria-hidden="true" /> Não analisado
        </span>
        <time class="fl-detail__date">{{ formatDate(feedback.created_at) }}</time>
      </header>

      <Message
        v-if="feedback.analysis_status === 'unanalyzed'"
        severity="warn"
        :closable="false"
        class="fl-detail__card"
      >
        <div class="fl-pending">
          <span>
            A IA não analisou este feedback ({{ analysisErrorLabel(feedback.analysis_error) }}). O
            sentimento “Inconclusivo” é só o valor padrão, não uma opinião.
          </span>
          <Button
            label="Reanalisar com a IA"
            icon="pi pi-refresh"
            size="small"
            :loading="reanalyzing"
            :disabled="reviewing"
            @click="reanalyze"
          />
        </div>
      </Message>

      <Message v-if="feedback.is_spam" severity="info" :closable="false" class="fl-detail__card">
        A IA marcou este texto como spam, por isso ele fica fora das listas normais e dos gráficos.
        Se foi engano, use “Não é spam”.
      </Message>
      <Message v-if="reviewNotice" severity="success" :closable="false" class="fl-detail__card">
        {{ reviewNotice }}
      </Message>
      <Message v-if="reviewError" severity="warn" :closable="false" class="fl-detail__card">
        {{ reviewError }}
      </Message>

      <Card class="fl-detail__card">
        <template #title>Feedback</template>
        <template #content>
          <p class="fl-detail__text">{{ feedback.feedback }}</p>
          <code class="fl-detail__id">{{ feedback.id }}</code>
        </template>
        <template #footer>
          <div class="fl-review">
            <div class="fl-review__field">
              <span class="fl-review__label">Sentimento</span>
              <Select
                :model-value="feedback.sentiment"
                :options="sentimentOptions"
                option-label="label"
                option-value="value"
                placeholder="Escolha"
                :disabled="reviewing"
                aria-label="Alterar o sentimento"
                class="fl-review__select"
                @update:model-value="changeSentiment"
              />
            </div>
            <Button
              v-if="feedback.is_spam"
              label="Não é spam"
              icon="pi pi-check"
              severity="secondary"
              size="small"
              :loading="reviewing"
              @click="review(false)"
            />
            <Button
              v-else
              label="Marcar como spam"
              icon="pi pi-ban"
              severity="secondary"
              size="small"
              :loading="reviewing"
              @click="review(true)"
            />
          </div>
        </template>
      </Card>

      <Card v-if="feedback.requested_features.length" class="fl-detail__card">
        <template #title>Funcionalidades solicitadas</template>
        <template #content>
          <ul class="fl-features">
            <li v-for="(f, i) in feedback.requested_features" :key="i" class="fl-features__row">
              <code class="fl-features__code">{{ f.code }}</code>
              <span class="fl-features__reason">{{ f.reason }}</span>
            </li>
          </ul>
        </template>
      </Card>
    </template>
  </section>
</template>

<style scoped>
.fl-back {
  display: inline-block;
  margin-bottom: 0.5rem;
}
.fl-loading {
  display: flex;
  justify-content: center;
  padding: 3rem;
}
.fl-detail__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.fl-detail__date {
  margin-left: auto;
  font-family: var(--fl-font-mono);
  font-size: 0.82rem;
  color: var(--fl-text-muted);
}
.fl-detail__card {
  margin-bottom: 1rem;
}
.fl-detail__text {
  margin: 0 0 1rem;
  line-height: 1.7;
  white-space: pre-wrap;
}
.fl-detail__id {
  font-size: 0.78rem;
  color: var(--fl-text-muted);
}
.fl-review {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem 1.5rem;
}
.fl-pending {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem 1.5rem;
}
.fl-review__field {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.fl-review__label {
  font-size: 0.85rem;
  color: var(--fl-text-muted);
}
.fl-review__select {
  min-width: 11rem;
}
.fl-features {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.fl-features__row {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.fl-features__code {
  font-size: 0.82rem;
  color: var(--fl-cyan);
  font-weight: 600;
}
.fl-features__reason {
  font-size: 0.9rem;
  color: var(--fl-text-muted);
}
</style>
