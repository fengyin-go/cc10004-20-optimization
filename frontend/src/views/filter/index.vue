<template>
  <section class="page" data-module="filter">
    <header class="page-head">
      <div>
        <h2>数据筛选管理</h2>
        <p class="page-desc">维护数据筛选规则的输入、执行约束与结果记录，为数据过滤提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记筛选规则</button>
        <button class="btn" type="button" @click="exportRows">导出数据筛选清单</button>
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
            <button class="link" type="button" @click="openRevise(row)">修订规则</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据筛选数据，可先登记筛选规则</td>
        </tr>
      </tbody>
    </table>

    <form v-if="reviseTarget" class="revise-bar" @submit.prevent="submitRevise">
      <strong>修订 {{ reviseTarget["规则编号"] }}（当前版本 v{{ reviseTarget.version ?? 1 }}）</strong>
      <label class="filter-item">
        <span>比较值</span>
        <input v-model="reviseDraft['比较值']" placeholder="比较值" />
      </label>
      <label class="filter-item">
        <span>组合表达式</span>
        <input v-model="reviseDraft['组合表达式']" placeholder="如 amount >= 100 AND status = 'paid'" />
      </label>
      <button class="btn primary" type="submit">提交修订</button>
      <button class="btn ghost" type="button" @click="cancelRevise">取消</button>
      <span class="error-text">{{ reviseMessage }}</span>
    </form>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据筛选记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/filter'
const columns = ["规则编号", "目标数据集", "筛选字段", "比较运算符", "比较值", "逻辑组合", "排序字段", "输出行数上限"]
const actions = ["提交校验", "启用规则", "停用规则"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const stats = [{"label": "筛选规则总数", "value": 0}, {"label": "已启用规则", "value": 0}, {"label": "待校验规则", "value": 0}]

const rows = ref<Row[]>([])
const reviseTarget = ref<Row | null>(null)
const reviseDraft = ref<Record<string, string>>({})
const reviseMessage = ref('')
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '筛选规则登记入口尚未接入审批流'
}

function openRevise(row: Row) {
  reviseTarget.value = row
  reviseDraft.value = {
    '比较值': String(row['比较值'] ?? ''),
    '组合表达式': String(row['组合表达式'] ?? ''),
  }
  reviseMessage.value = ''
}

function cancelRevise() {
  reviseTarget.value = null
  reviseDraft.value = {}
  reviseMessage.value = ''
}

async function submitRevise() {
  if (!reviseTarget.value) return
  reviseMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${reviseTarget.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values: reviseDraft.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload?.ok) {
      reviseMessage.value = payload?.message ?? '规则修订未生效'
      return
    }
    reviseTarget.value = null
    await reload()
  } catch (error) {
    reviseMessage.value = error instanceof Error ? error.message : '规则修订失败'
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
      throw new Error('数据筛选动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据筛选操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('筛选规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据筛选列表读取失败'
  }
}

onMounted(reload)
</script>
