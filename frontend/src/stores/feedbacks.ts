import { defineStore } from 'pinia'
import { ref } from 'vue'

import { http } from '@/lib/http'
import type {
  Feedback,
  FeedbackListResponse,
  FeedbackSpamFilter,
  Pagination,
  Sentiment,
} from '@/types/api'

export const useFeedbacksStore = defineStore('feedbacks', () => {
  const items = ref<Feedback[]>([])
  const pagination = ref<Pagination | null>(null)
  const loading = ref(false)

  async function list(params: {
    page?: number
    perPage?: number
    sentiment?: Sentiment | null
    spam?: FeedbackSpamFilter
  }): Promise<void> {
    loading.value = true
    try {
      const query = new URLSearchParams()
      query.set('page', String(params.page ?? 1))
      query.set('per_page', String(params.perPage ?? 20))
      if (params.sentiment) query.set('sentiment', params.sentiment)
      if (params.spam && params.spam !== 'exclude') query.set('spam', params.spam)
      const data = await http.get<FeedbackListResponse>(`/api/feedbacks?${query.toString()}`)
      items.value = data.items
      pagination.value = data.pagination
    } finally {
      loading.value = false
    }
  }

  async function getById(id: string): Promise<Feedback> {
    return http.get<Feedback>(`/api/feedbacks/${id}`)
  }

  /** Revisão humana: corrige a análise da IA (spam e/ou sentimento). */
  async function review(
    id: string,
    changes: { is_spam?: boolean; sentiment?: Sentiment },
  ): Promise<Feedback> {
    return http.patch<Feedback>(`/api/feedbacks/${id}`, changes, true)
  }

  async function setSpam(id: string, isSpam: boolean): Promise<Feedback> {
    return review(id, { is_spam: isSpam })
  }

  async function setSentiment(id: string, sentiment: Sentiment): Promise<Feedback> {
    return review(id, { sentiment })
  }

  return { items, pagination, loading, list, getById, review, setSpam, setSentiment }
})
