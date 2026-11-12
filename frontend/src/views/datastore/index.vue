<template>
  <section class="page" data-module="datastore">
    <header class="page-head">
      <div>
        <h2>数据集管理管理</h2>
        <p class="page-desc">维护数据集定义的输入、执行约束与结果记录，为数据集管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记数据集定义</button>
        <button class="btn" type="button" @click="exportRows">导出数据集管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link field-link" type="button" @click="openFieldPanel(row)">字段引用</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据集管理数据，可先登记数据集定义</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <aside v-if="panelOpen" class="field-panel" data-panel="field-references">
      <header class="field-panel-head">
        <div>
          <h3>字段影响反查</h3>
          <p class="panel-sub">
            {{ activeDataset?.数据集编号 }} · {{ activeDataset?.数据集名称 }} · 字段版本
            v{{ fieldVersion }}
          </p>
        </div>
        <button class="btn ghost" type="button" @click="closeFieldPanel">关闭</button>
      </header>

      <div v-if="fieldsLoading" class="panel-hint">字段定义读取中…</div>
      <div v-else class="field-panel-body">
        <section class="field-column">
          <h4>字段定义</h4>
          <ul class="field-list">
            <li
              v-for="field in fields"
              :key="field.field_id"
              class="field-item"
              :class="{ active: field.field_id === selectedFieldId }"
            >
              <button class="field-main" type="button" @click="selectField(field.field_id)">
                <span class="field-name">{{ field.name }}</span>
                <span class="field-meta">{{ field.label }} · {{ field.type }}</span>
                <span class="field-id">{{ field.field_id }}</span>
                <span v-if="field.aliases.length" class="field-alias">历史名：{{ field.aliases.join('、') }}</span>
              </button>
              <button class="link field-ref" type="button" @click="selectField(field.field_id)">引用</button>
              <div v-if="renameFieldId === field.field_id" class="rename-row">
                <input v-model="renameDraft" class="rename-input" :placeholder="field.name" />
                <button class="link" type="button" @click="submitRename(field)">保存</button>
                <button class="link" type="button" @click="cancelRename">取消</button>
              </div>
              <button v-else class="link rename-toggle" type="button" @click="startRename(field)">改名</button>
            </li>
          </ul>
          <p v-if="renameMessage" class="panel-hint">{{ renameMessage }}</p>
        </section>

        <section class="reference-column">
          <div v-if="!selectedFieldId" class="panel-hint">
            点左侧字段旁的「引用」，查看哪些启用规则直接使用该字段、哪些仅经组合表达式间接依赖。
          </div>
          <template v-else>
            <div class="ref-toolbar">
              <div class="ref-stats">
                <span class="badge direct">直接使用 {{ refSummary.direct }}</span>
                <span class="badge indirect">间接依赖 {{ refSummary.indirect }}</span>
                <span class="badge pending">待确认 {{ refSummary.ambiguous }}</span>
              </div>
              <div class="ref-actions">
                <span v-if="refSnapshot" class="snapshot-tag">
                  快照 {{ refSnapshot.id }} · 规则版本已锁定 · {{ refSnapshot.built_at }}
                </span>
                <button class="btn ghost" type="button" :disabled="refLoading" @click="loadReferences(true)">
                  刷新反查
                </button>
              </div>
            </div>

            <p v-if="refError" class="error-text ref-message">{{ refError }}</p>
            <p v-if="refNotice" class="notice-text ref-message">{{ refNotice }}</p>
            <p v-if="consumeResult" class="ok-text ref-message">
              {{ consumeResult.message }}（规则版本 v{{ consumeResult.rule_version }}）·
              <RouterLink to="/query">查看交互查询</RouterLink>
            </p>

            <table v-if="refRelations.length" class="data-table ref-table">
              <thead>
                <tr>
                  <th>模块</th>
                  <th>规则</th>
                  <th>采用版本</th>
                  <th>关系</th>
                  <th>依据</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="relation in refRelations" :key="`${relation.module}:${relation.rule_id}`">
                  <td>{{ relation.module_label }}</td>
                  <td>
                    <span class="rule-no">{{ relation.rule_no }}</span>
                    <span class="rule-updated">{{ relation.rule_updated_at || '历史规则无修订时间' }}</span>
                  </td>
                  <td>v{{ relation.rule_version }}</td>
                  <td>
                    <span class="badge" :class="relation.relation === 'direct' ? 'direct' : 'indirect'">
                      {{ relation.relation === 'direct' ? '直接使用' : '间接依赖' }}
                    </span>
                    <span v-if="relation.also_indirect" class="badge both">另有表达式引用</span>
                  </td>
                  <td class="ref-basis">{{ relation.summary }}</td>
                  <td>
                    <button
                      class="link"
                      type="button"
                      :disabled="consumeBusyToken === relation.selection_token || consumedTokens.includes(relation.selection_token)"
                      @click="consumeRelation(relation)"
                    >
                      {{ consumedTokens.includes(relation.selection_token) ? '已进入交互查询' : '进入交互查询' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-else-if="!refLoading && !refError" class="panel-hint">
              当前字段没有被任何启用规则直接使用或经表达式依赖。
            </p>

            <section v-if="refAmbiguous.length" class="ambiguous-block">
              <h4>待确认（名称关联有歧义，未参与进入交互查询）</h4>
              <ul>
                <li v-for="item in refAmbiguous" :key="`${item.module}:${item.rule_id}:${item.field_name}`">
                  <strong>{{ item.module_label }} · {{ item.rule_no }} · v{{ item.rule_version }}</strong>
                  <span>{{ item.role }}：{{ item.field_name }} —— {{ item.reason }}</span>
                  <span class="amb-candidates">
                    候选：{{ item.candidates.map((c) => `${c.name}（${c.field_id}）`).join('、') || '无' }}
                  </span>
                </li>
              </ul>
            </section>

            <footer class="ref-foot">
              <span>
                已加载 {{ refRelations.length }} / {{ refTotal }} 条规则关系
                <template v-if="refLoadedAll && refRelations.length !== refTotal">
                  · 分段取数存在缺口，请刷新反查
                </template>
              </span>
              <button
                v-if="refHasMore"
                class="btn"
                type="button"
                :disabled="refLoading"
                @click="loadMore"
              >
                {{ refLoading ? '加载中…' : '加载更多' }}
              </button>
            </footer>
          </template>
        </section>
      </div>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type FieldDef = {
  field_id: string
  name: string
  label: string
  type: string
  aliases: string[]
}

type Usage = {
  role: string
  declared_name: string
  declared_field_id: string | null
  field_id: string
  matched_by: string
  matched_name: string
}

type ExpressionHit = {
  key: string
  label: string
  token: string
  matched_by: string
  matched_name: string
  occurrences: number
}

type Relation = {
  module: string
  module_label: string
  rule_id: number
  rule_no: string
  rule_version: number
  rule_updated_at: string
  rule_status: string
  dataset_code: string
  field_id: string
  field_name: string
  relation: 'direct' | 'indirect'
  also_indirect: boolean
  usages: Usage[]
  expression_hits: ExpressionHit[]
  summary: string
  selection_token: string
}

type Ambiguous = {
  module: string
  module_label: string
  rule_id: number
  rule_no: string
  rule_version: number
  rule_updated_at: string
  rule_status: string
  field_name: string
  role: string
  reason: string
  needs_confirmation: boolean
  candidates: { field_id: string; name: string; label: string }[]
}

type ReferencePayload = {
  snapshot: {
    id: string
    built_at: string
    dataset: { id: number; code: string; name: string; field_version: number }
    field: FieldDef
    rule_versions: Record<string, { rule_no: string; version: number; status: string; updated_at: string }>
  }
  summary: { direct: number; indirect: number; ambiguous: number; relations_total: number }
  relations: Relation[]
  ambiguous: Ambiguous[]
  ambiguous_total: number
  page: { size: number; cursor: string | null; next_cursor: string | null; returned: number; total: number }
}

type ConsumeResult = {
  ok: boolean
  code: string
  message: string
  rule_version?: number
  current_rule_version?: number
  rule_status?: string
  query_entry?: Record<string, unknown>
}

const ENDPOINT = '/api/datastore'
const columns = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
const actions = ["提交审核", "生效发布", "废弃下线"]
const statuses = ["草稿", "待审核", "已生效", "已废弃"]
const stats = [{"label": "数据集定义总数", "value": 0}, {"label": "已生效数据集", "value": 0}, {"label": "待审核数据集", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const panelOpen = ref(false)
const activeDataset = ref<Row | null>(null)
const fields = ref<FieldDef[]>([])
const fieldVersion = ref(0)
const fieldsLoading = ref(false)
const selectedFieldId = ref('')
const renameFieldId = ref('')
const renameDraft = ref('')
const renameMessage = ref('')

const refSnapshotState = ref<ReferencePayload['snapshot'] | null>(null)
const refRelations = ref<Relation[]>([])
const refAmbiguous = ref<Ambiguous[]>([])
const refSummary = ref({ direct: 0, indirect: 0, ambiguous: 0, relations_total: 0 })
const refTotal = ref(0)
const refLoading = ref(false)
const refError = ref('')
const refNotice = ref('')
const refCursor = ref<string | null>(null)
const refHasMore = ref(false)
const consumedTokens = ref<string[]>([])
const consumeBusyToken = ref('')
const consumeResult = ref<ConsumeResult | null>(null)

const refSnapshot = computed(() => refSnapshotState.value)
const refLoadedAll = computed(() => !refHasMore.value && !refLoading.value)

// 旧响应不得覆盖较新定位：字段面板与反查各自持有代次，只有最新代次可以落状态。
let fieldsGeneration = 0
let referenceGeneration = 0

function resetReferenceState() {
  referenceGeneration += 1
  refSnapshotState.value = null
  refRelations.value = []
  refAmbiguous.value = []
  refSummary.value = { direct: 0, indirect: 0, ambiguous: 0, relations_total: 0 }
  refTotal.value = 0
  refCursor.value = null
  refHasMore.value = false
  refError.value = ''
  refNotice.value = ''
  refLoading.value = false
  consumeResult.value = null
  consumedTokens.value = []
  consumeBusyToken.value = ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '数据集定义登记入口尚未接入审批流'
}

async function readJson(response: Response): Promise<any> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorDetail(payload: any, fallback: string): { message: string; code: string } {
  const detail = payload?.detail
  if (typeof detail === 'string' && detail) {
    return { message: detail, code: '' }
  }
  if (detail && typeof detail === 'object') {
    return {
      message: String(detail.message || fallback),
      code: String(detail.code || ''),
    }
  }
  return { message: fallback, code: '' }
}

async function closeFieldPanel() {
  panelOpen.value = false
  fieldsGeneration += 1
  resetReferenceState()
  activeDataset.value = null
  fields.value = []
  fieldVersion.value = 0
  selectedFieldId.value = ''
  renameFieldId.value = ''
  renameMessage.value = ''
}

async function openFieldPanel(row: Row) {
  panelOpen.value = true
  activeDataset.value = row
  fields.value = []
  fieldVersion.value = 0
  selectedFieldId.value = ''
  renameFieldId.value = ''
  renameMessage.value = ''
  resetReferenceState()
  await loadFields()
}

async function loadFields() {
  if (!activeDataset.value) return
  const datasetId = activeDataset.value.id
  const generation = ++fieldsGeneration
  fieldsLoading.value = true
  try {
    const response = await request(`${ENDPOINT}/${datasetId}/fields`)
    const payload = await readJson(response)
    if (generation !== fieldsGeneration) return
    if (!response.ok) {
      const detail = errorDetail(payload, '字段定义读取失败')
      errorMessage.value = detail.message
      return
    }
    fields.value = payload.fields ?? []
    fieldVersion.value = payload.field_version ?? 0
  } catch (error) {
    if (generation !== fieldsGeneration) return
    errorMessage.value = error instanceof Error ? error.message : '字段定义读取失败'
  } finally {
    if (generation === fieldsGeneration) {
      fieldsLoading.value = false
    }
  }
}

function selectField(fieldId: string) {
  if (selectedFieldId.value === fieldId && refSnapshotState.value) return
  selectedFieldId.value = fieldId
  renameFieldId.value = ''
  renameMessage.value = ''
  void loadReferences(true)
}

function startRename(field: FieldDef) {
  renameFieldId.value = field.field_id
  renameDraft.value = field.name
  renameMessage.value = ''
}

function cancelRename() {
  renameFieldId.value = ''
  renameDraft.value = ''
}

async function submitRename(field: FieldDef) {
  if (!activeDataset.value) return
  const name = renameDraft.value.trim()
  if (!name) {
    renameMessage.value = '新字段名不能为空'
    return
  }
  const datasetId = activeDataset.value.id
  try {
    const response = await request(`${ENDPOINT}/${datasetId}/fields/${field.field_id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
    const payload = await readJson(response)
    if (!response.ok || !payload?.ok) {
      renameMessage.value = errorDetail(payload, '字段改名未生效').message
      return
    }
    renameMessage.value = payload.message
    renameFieldId.value = ''
    await loadFields()
    if (selectedFieldId.value === field.field_id) {
      await loadReferences(true)
    }
  } catch (error) {
    renameMessage.value = error instanceof Error ? error.message : '字段改名失败'
  }
}

async function loadReferences(reset: boolean) {
  if (!activeDataset.value || !selectedFieldId.value) return
  const datasetId = activeDataset.value.id
  const fieldId = selectedFieldId.value
  const generation = ++referenceGeneration
  const snapshotId = reset ? null : refSnapshotState.value?.id ?? null
  const cursor = reset ? null : refCursor.value

  refLoading.value = true
  refError.value = ''
  refNotice.value = ''
  consumeResult.value = null
  if (reset) {
    refRelations.value = []
    refAmbiguous.value = []
    refSummary.value = { direct: 0, indirect: 0, ambiguous: 0, relations_total: 0 }
    refTotal.value = 0
    refCursor.value = null
    refHasMore.value = false
    refSnapshotState.value = null
  }

  try {
    const params = new URLSearchParams({ field_id: fieldId, size: '20' })
    if (snapshotId) params.set('snapshot_id', snapshotId)
    if (cursor) params.set('cursor', cursor)
    const response = await request(`${ENDPOINT}/${datasetId}/field-references?${params.toString()}`)
    const payload = await readJson(response)
    if (generation !== referenceGeneration) return

    if (!response.ok) {
      const detail = errorDetail(payload, '字段影响反查失败')
      if (response.status === 409 && detail.code === 'snapshot_conflict') {
        // 反查期间规则被修订：丢弃旧快照结果，避免新旧版本混在同一面板里。
        refRelations.value = []
        refAmbiguous.value = []
        refSnapshotState.value = null
        refCursor.value = null
        refHasMore.value = false
        refNotice.value = `${detail.message}；已定位版本：${describeCurrentVersions(payload?.detail?.current?.rule_versions)}`
        return
      }
      refError.value = detail.message
      if (Array.isArray(payload?.detail?.candidates) && payload.detail.candidates.length) {
        refError.value += `（候选：${payload.detail.candidates.map((item: any) => `${item.name}（${item.field_id}）`).join('、')}）`
      }
      return
    }

    const data = payload as ReferencePayload
    if (!reset && refSnapshotState.value && data.snapshot.id !== refSnapshotState.value.id) {
      refNotice.value = '分页返回的快照与当前锁定快照不一致，已停止合并，请刷新反查'
      return
    }
    mergeReferencePage(data, reset)
  } catch (error) {
    if (generation !== referenceGeneration) return
    refError.value = error instanceof Error ? error.message : '字段影响反查失败'
  } finally {
    if (generation === referenceGeneration) {
      refLoading.value = false
    }
  }
}

function describeCurrentVersions(versions: Record<string, any> | undefined): string {
  if (!versions) return '未知'
  const parts = Object.entries(versions).map(([, value]) => `${value.rule_no} v${value.version}`)
  return parts.length ? parts.slice(0, 6).join('、') + (parts.length > 6 ? ' 等' : '') : '未知'
}

function mergeReferencePage(data: ReferencePayload, reset: boolean) {
  if (reset) {
    refRelations.value = []
    refAmbiguous.value = []
    refSnapshotState.value = data.snapshot
    refSummary.value = data.summary
    refTotal.value = data.page.total
  } else if (refSnapshotState.value) {
    refSnapshotState.value = data.snapshot
    refSummary.value = data.summary
    refTotal.value = data.page.total
    refAmbiguous.value = mergeUnique(refAmbiguous.value, data.ambiguous, (item) => `${item.module}:${item.rule_id}:${item.field_name}`)
  }

  const known = new Set(refRelations.value.map((item) => `${item.module}:${item.rule_id}`))
  const appended: Relation[] = []
  for (const relation of data.relations) {
    const key = `${relation.module}:${relation.rule_id}`
    if (known.has(key)) continue
    known.add(key)
    appended.push(relation)
  }
  refRelations.value = [...refRelations.value, ...appended]
  if (reset) {
    refAmbiguous.value = data.ambiguous
  }
  refCursor.value = data.page.next_cursor
  refHasMore.value = Boolean(data.page.next_cursor)
}

function mergeUnique<T>(base: T[], incoming: T[], keyOf: (item: T) => string): T[] {
  const known = new Set(base.map(keyOf))
  const merged = [...base]
  for (const item of incoming) {
    const key = keyOf(item)
    if (known.has(key)) continue
    known.add(key)
    merged.push(item)
  }
  return merged
}

function loadMore() {
  if (!refHasMore.value || refLoading.value) return
  void loadReferences(false)
}

async function consumeRelation(relation: Relation) {
  if (!activeDataset.value) return
  const token = relation.selection_token
  if (consumedTokens.value.includes(token) || consumeBusyToken.value === token) return
  const datasetId = activeDataset.value.id
  const generation = referenceGeneration
  consumeBusyToken.value = token
  refNotice.value = ''
  consumeResult.value = null
  try {
    const response = await request(`${ENDPOINT}/${datasetId}/field-references/consume`, {
      method: 'POST',
      body: JSON.stringify({
        selection_token: token,
        snapshot_id: refSnapshot.value?.id ?? null,
      }),
    })
    const payload = (await readJson(response)) as ConsumeResult | null
    // 点选后用户可能已经切到别的字段：旧消费结果不得覆盖较新的定位。
    if (generation !== referenceGeneration) return
    if (!response.ok || !payload) {
      refError.value = errorDetail(payload, '进入交互查询失败').message
      return
    }
    if (!payload.ok) {
      // 旧选择被回收：展示本次选择版本、当前版本或规则状态，并重新取最新反查结果。
      const versionNote = payload.current_rule_version
        ? `（本次选择版本 v${payload.rule_version}，当前版本 v${payload.current_rule_version}）`
        : ''
      refNotice.value = `${payload.message}${versionNote}`
      if (['selection_stale', 'rule_disabled', 'rule_missing', 'selection_expired', 'selection_consumed', 'selection_context_mismatch'].includes(payload.code)) {
        consumedTokens.value = [...consumedTokens.value, token]
        consumeBusyToken.value = ''
        await loadReferences(true)
      }
      return
    }
    consumedTokens.value = [...consumedTokens.value, token]
    consumeResult.value = payload
    refNotice.value = ''
  } catch (error) {
    if (generation === referenceGeneration) {
      refError.value = error instanceof Error ? error.message : '进入交互查询失败'
    }
  } finally {
    if (consumeBusyToken.value === token) {
      consumeBusyToken.value = ''
    }
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据集管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('数据集定义列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.field-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(980px, 92vw);
  background: #fff;
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.12);
  display: flex;
  flex-direction: column;
  z-index: 30;
  overflow: hidden;
}
.field-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}
.field-panel-head h3 { margin: 0; font-size: 16px; }
.panel-sub { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.field-panel-body {
  display: flex;
  flex: 1;
  min-height: 0;
}
.field-column {
  width: 250px;
  border-right: 1px solid var(--border);
  padding: 12px;
  overflow-y: auto;
}
.field-column h4, .ambiguous-block h4 { margin: 0 0 8px; font-size: 13px; color: var(--muted); }
.field-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.field-item { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.field-item.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.field-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  border: none;
  background: none;
  padding: 0;
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.field-name { font-size: 13px; font-weight: 600; color: #1f2937; }
.field-meta, .field-id, .field-alias { font-size: 11px; color: var(--muted); }
.field-id { font-family: ui-monospace, monospace; }
.rename-toggle, .field-ref { margin-top: 4px; font-size: 12px; }
.rename-row { display: flex; gap: 6px; margin-top: 6px; align-items: center; }
.rename-input { flex: 1; min-width: 0; padding: 3px 6px; border: 1px solid var(--border); border-radius: 4px; font-size: 12px; }
.reference-column {
  flex: 1;
  padding: 12px 16px;
  overflow-y: auto;
  min-width: 0;
}
.ref-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.ref-stats, .ref-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { border-radius: 999px; padding: 2px 8px; font-size: 11px; border: 1px solid var(--border); background: #f8fafc; }
.badge.direct { color: #166534; border-color: #bbf7d0; background: #f0fdf4; }
.badge.indirect { color: #9a3412; border-color: #fed7aa; background: #fff7ed; }
.badge.pending { color: #92400e; border-color: #fde68a; background: #fffbeb; }
.badge.both { color: #1e40af; border-color: #bfdbfe; background: #eff6ff; }
.snapshot-tag { font-size: 11px; color: var(--muted); }
.ref-table { margin-top: 10px; }
.rule-no { display: block; font-family: ui-monospace, monospace; font-size: 12px; }
.rule-updated { font-size: 11px; color: var(--muted); }
.ref-basis { font-size: 12px; }
.ref-message { margin: 8px 0 0; font-size: 12px; }
.notice-text { color: #92400e; }
.ok-text { color: #166534; }
.ambiguous-block { margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 10px; }
.ambiguous-block ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.ambiguous-block li { font-size: 12px; display: flex; flex-direction: column; gap: 2px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 6px 8px; }
.amb-candidates { color: var(--muted); }
.ref-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 12px; color: var(--muted); }
.panel-hint { color: var(--muted); font-size: 12px; padding: 8px 0; }
.link:disabled { color: var(--muted); cursor: default; }
</style>
