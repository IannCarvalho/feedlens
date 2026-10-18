<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Card from 'primevue/card'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import ProgressSpinner from 'primevue/progressspinner'

import { useProvidersStore } from '@/stores/providers'
import { HttpError } from '@/lib/http'
import type { ProviderCredential, ProviderId } from '@/types/api'

const store = useProvidersStore()
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const hasChain = computed(() => store.environmentChain.length > 0)
const sorted = computed(() =>
  [...store.credentials].sort((a, b) => a.priority - b.priority),
)

/* --- Formulário de cadastro --- */
const showDialog = ref(false)
const form = ref<{ provider: ProviderId; model: string; api_key: string }>({
  provider: 'anthropic',
  model: '',
  api_key: '',
})
const saving = ref(false)
const formError = ref<string | null>(null)

const providerOptions = computed(() =>
  store.catalog.map((c) => ({ label: c.label, value: c.id })),
)

/* Dica do campo "Modelo": o padrão do provedor escolhido (cada um tem o seu). */
const modelPlaceholder = computed(() => {
  const entry = store.catalog.find((c) => c.id === form.value.provider)
  return `ex.: ${entry?.default_model ?? 'claude-haiku-4-5'}`
})

function openDialog() {
  form.value = { provider: 'anthropic', model: '', api_key: '' }
  formError.value = null
  showDialog.value = true
}

async function submit() {
  formError.value = null
  if (!form.value.api_key.trim()) {
    formError.value =
      'Cole a chave de API do provedor selecionado para salvar. Você a gera no painel do próprio provedor (ex.: platform.openai.com, console.anthropic.com, console.groq.com, openrouter.ai).'
    return
  }
  saving.value = true
  try {
    await store.create({
      provider: form.value.provider,
      api_key: form.value.api_key.trim(),
      model: form.value.model.trim() || undefined,
    })
    showDialog.value = false
    notice.value = 'Chave salva e verificada. Já entra na cadeia de análise.'
  } catch (e) {
    formError.value =
      e instanceof HttpError
        ? e.message
        : 'Não foi possível salvar a chave. Confira a chave e a conexão, e tente de novo.'
  } finally {
    saving.value = false
  }
}

/* --- Ações por linha --- */
const busyId = ref<string | null>(null)

async function run(id: string, action: () => Promise<void>, ok?: string) {
  busyId.value = id
  error.value = null
  try {
    await action()
    if (ok) notice.value = ok
  } catch (e) {
    error.value =
      e instanceof HttpError
        ? e.message
        : 'A operação não foi concluída. Verifique sua conexão e tente novamente.'
  } finally {
    busyId.value = null
  }
}

async function testKey(row: ProviderCredential) {
  await run(row.id, async () => {
    const result = await store.test(row.id)
    if (result.ok) {
      notice.value = `Chave de ${row.provider} verificada${result.latency_ms ? ` (${result.latency_ms} ms)` : ''}.`
      return
    }
    const hint =
      result.kind === 'auth'
        ? 'o provedor recusou a chave — gere uma nova no painel dele e cadastre de novo'
        : result.kind === 'rate_limit'
          ? 'o provedor está limitando as chamadas — espere um pouco e teste de novo'
          : result.kind === 'timeout'
            ? 'a chamada expirou — verifique a conexão e tente de novo'
            : result.kind === 'not_found'
              ? `o modelo “${row.model}” não existe para essa conta — remova a chave e cadastre de novo informando outro modelo`
              : `falhou (${result.kind ?? 'erro'}) — tente de novo em instantes`
    error.value = `A chave de ${row.provider} não passou no teste: ${hint}.`
  })
}

function toggle(row: ProviderCredential) {
  run(row.id, () => store.update(row.id, { enabled: !row.enabled }))
}

function remove(row: ProviderCredential) {
  run(row.id, () => store.remove(row.id), 'Chave removida.')
}

async function move(row: ProviderCredential, dir: -1 | 1) {
  const ids = sorted.value.map((c) => c.id)
  const i = ids.indexOf(row.id)
  const j = i + dir
  if (j < 0 || j >= ids.length) return
  ;[ids[i], ids[j]] = [ids[j], ids[i]]
  await run(row.id, () => store.reorder(ids))
}

onMounted(async () => {
  try {
    await store.load()
  } catch (e) {
    error.value =
      e instanceof HttpError
        ? e.message
        : 'Não foi possível carregar os provedores. Recarregue a página; se persistir, verifique se o servidor está no ar.'
  }
})
</script>

<template>
  <section class="fl-page">
    <header class="fl-page__head">
      <div>
        <h1>Provedores de IA</h1>
        <p class="fl-muted">
          Ordene os provedores que analisam os feedbacks. A chave principal é tentada
          primeiro; as demais entram como reserva.
        </p>
      </div>
      <Button
        v-if="store.vaultEnabled"
        class="fl-add-key"
        label="Adicionar chave"
        icon="pi pi-plus"
        @click="openDialog"
      />
    </header>

    <div v-if="store.loading" class="fl-loading">
      <ProgressSpinner style="width: 2.5rem; height: 2.5rem" />
    </div>

    <template v-else>
      <Message v-if="error" severity="error" :closable="true">{{ error }}</Message>
      <Message v-if="notice" severity="success" :closable="true">{{ notice }}</Message>

      <Message v-if="!store.manageEnabled" severity="info" :closable="false">
        <strong>Modo somente leitura.</strong> Para cadastrar, testar e reordenar chaves,
        defina a variável <code>DASHBOARD_PASSWORD</code> no servidor e faça login no painel.
        Enquanto isso, a análise continua usando as chaves do ambiente, listadas abaixo.
      </Message>

      <Message
        v-if="store.manageEnabled && !store.vaultEnabled"
        severity="info"
        :closable="false"
      >
        <strong>Cofre de chaves desligado.</strong> Para salvar chaves de provedor pela
        tela, gere uma chave Fernet e defina <code>FEEDLENS_ENCRYPTION_KEY</code> no servidor
        (<code>python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"</code>).
        Sem ela, use as chaves do ambiente, listadas abaixo.
      </Message>

      <Card class="fl-chain">
        <template #title>Cadeia do ambiente</template>
        <template #content>
          <p class="fl-muted fl-chain__hint">
            Chaves definidas por variável de ambiente no servidor, usadas quando não há
            chave salva nem BYOK por requisição.
          </p>
          <ol v-if="hasChain" class="fl-chain__list">
            <li v-for="(modelRef, i) in store.environmentChain" :key="modelRef">
              <span class="fl-chain__pos">{{ i + 1 }}</span>
              <code>{{ modelRef }}</code>
            </li>
          </ol>
          <p v-else class="fl-muted">Nenhuma chave de provedor no ambiente.</p>
        </template>
      </Card>

      <Card v-if="store.vaultEnabled" class="fl-saved">
        <template #title>Chaves salvas</template>
        <template #content>
          <ol v-if="sorted.length" class="fl-saved__list">
            <li v-for="(c, idx) in sorted" :key="c.id" class="fl-saved__row">
              <div class="fl-saved__order">
                <button
                  type="button"
                  class="fl-ord"
                  :disabled="idx === 0 || busyId === c.id"
                  aria-label="Subir prioridade"
                  @click="move(c, -1)"
                >
                  <i class="pi pi-chevron-up" aria-hidden="true" />
                </button>
                <span class="fl-chain__pos">{{ c.priority }}</span>
                <button
                  type="button"
                  class="fl-ord"
                  :disabled="idx === sorted.length - 1 || busyId === c.id"
                  aria-label="Descer prioridade"
                  @click="move(c, 1)"
                >
                  <i class="pi pi-chevron-down" aria-hidden="true" />
                </button>
              </div>
              <div class="fl-saved__id">
                <code>{{ c.provider }} · {{ c.model }}</code>
                <code class="fl-hint">{{ c.key_hint }}</code>
              </div>
              <div class="fl-saved__tags">
                <Tag
                  :value="c.enabled ? 'ativa' : 'desativada'"
                  :severity="c.enabled ? 'success' : 'secondary'"
                />
                <Tag v-if="c.verified" value="verificada" severity="info" icon="pi pi-check" />
                <Tag
                  v-else-if="c.last_error_kind"
                  :value="c.last_error_kind"
                  severity="warn"
                />
              </div>
              <div class="fl-saved__actions">
                <Button
                  icon="pi pi-bolt"
                  text
                  rounded
                  size="small"
                  aria-label="Testar chave"
                  :loading="busyId === c.id"
                  @click="testKey(c)"
                />
                <Button
                  :icon="c.enabled ? 'pi pi-pause' : 'pi pi-play'"
                  text
                  rounded
                  size="small"
                  :aria-label="c.enabled ? 'Desativar' : 'Ativar'"
                  :disabled="busyId === c.id"
                  @click="toggle(c)"
                />
                <Button
                  icon="pi pi-trash"
                  text
                  rounded
                  size="small"
                  severity="danger"
                  aria-label="Remover chave"
                  :disabled="busyId === c.id"
                  @click="remove(c)"
                />
              </div>
            </li>
          </ol>
          <p v-else class="fl-muted">
            Nenhuma chave salva. Clique em “Adicionar chave” para cadastrar a primeira.
          </p>
        </template>
      </Card>
    </template>

    <Dialog
      v-model:visible="showDialog"
      modal
      header="Adicionar chave de provedor"
      :style="{ width: '28rem' }"
    >
      <div class="fl-form">
        <label class="fl-form__label">
          Provedor
          <Select
            v-model="form.provider"
            :options="providerOptions"
            option-label="label"
            option-value="value"
          />
        </label>
        <label class="fl-form__label">
          Modelo <span class="fl-muted">(opcional — usa o padrão do provedor)</span>
          <InputText v-model="form.model" :placeholder="modelPlaceholder" />
        </label>
        <label class="fl-form__label">
          Chave de API
          <InputText v-model="form.api_key" type="password" autocomplete="off" />
        </label>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <p class="fl-muted fl-form__note">
          A chave é testada com uma chamada mínima antes de salvar e nunca é exibida de
          volta — só o final dela.
        </p>
      </div>
      <template #footer>
        <Button label="Cancelar" text @click="showDialog = false" />
        <Button label="Salvar e testar" icon="pi pi-check" :loading="saving" @click="submit" />
      </template>
    </Dialog>
  </section>
</template>

<style scoped>
.fl-page__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  margin-bottom: 1.25rem;
}
.fl-add-key {
  flex-shrink: 0;
  white-space: nowrap;
}
@media (max-width: 40rem) {
  .fl-page__head {
    flex-direction: column;
  }
  .fl-add-key {
    align-self: flex-start;
  }
}
.fl-page__head h1 {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
}
.fl-muted {
  color: var(--fl-text-muted);
  margin: 0;
}
.fl-loading {
  display: flex;
  justify-content: center;
  padding: 3rem;
}
.fl-chain,
.fl-saved {
  margin-top: 1rem;
}
.fl-chain__hint {
  margin-bottom: 0.75rem;
}
.fl-chain__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-family: var(--fl-font-mono);
  font-size: 0.85rem;
}
.fl-saved__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.fl-saved__row {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--fl-border);
}
.fl-saved__row:last-child {
  border-bottom: none;
}
.fl-saved__order {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.15rem;
}
.fl-ord {
  background: transparent;
  border: none;
  color: var(--fl-text-muted);
  cursor: pointer;
  padding: 0;
  line-height: 1;
  font-size: 0.7rem;
}
.fl-ord:disabled {
  opacity: 0.3;
  cursor: default;
}
.fl-ord:not(:disabled):hover {
  color: var(--fl-cyan);
}
.fl-saved__id {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-family: var(--fl-font-mono);
  font-size: 0.85rem;
  flex: 1;
  min-width: 0;
}
.fl-hint {
  color: var(--fl-text-muted);
}
.fl-chain__pos {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  border-radius: 50%;
  background: color-mix(in srgb, var(--fl-cyan) 16%, transparent);
  color: var(--fl-cyan);
  font-weight: 600;
  flex-shrink: 0;
  font-family: var(--fl-font-sans);
}
.fl-saved__tags {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.fl-saved__actions {
  display: flex;
  gap: 0.1rem;
  margin-left: auto;
}
.fl-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.fl-form__label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.88rem;
  font-weight: 500;
}
.fl-form__note {
  font-size: 0.8rem;
}
</style>
