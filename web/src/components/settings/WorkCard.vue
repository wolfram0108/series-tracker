<script setup lang="ts">
import { computed } from "vue"
import SourceMark from "../status/output/SourceMark.vue"
import VoiceMark from "../status/output/VoiceMark.vue"
import { t } from "../../i18n"
import { stateLabel } from "../../lib/states"
import type { WorkAction, WorkView } from "../../lib/work"

// The card of the work of an agent, the same for every agent (lib/work.ts): the series and what the work is of, its state, the
// stages, the cells of what it holds, the facts, the progress, the rows of the voice-overs, the log of attempts, the foot of
// the times. The colour of the card is the colour of its state (lib/states.ts); what it asks of the core is told as an event.
const props = defineProps<{ view: WorkView; seriesName: string; busy?: boolean }>()
const emit = defineEmits<{ (e: "action", name: WorkAction): void; (e: "plot", plot: { src: string; title: string }): void }>()

const word = computed(() => props.view.word ?? stateLabel(props.view.state))
</script>

<template>
  <div class="work-card" :data-state="view.state" :data-kind="view.kind" :data-series="view.seriesId ?? undefined">
    <div class="work-head">
      <span class="work-title">
        <span v-if="view.seriesId != null" class="work-series">#{{ view.seriesId }}</span>
        <span v-if="seriesName">{{ seriesName }}</span>
        <template v-if="view.episode"><span class="work-sep">·</span><b>{{ view.episode }}</b></template>
        <VoiceMark v-if="view.voiceNumber" :n="view.voiceNumber" />
        <b v-if="view.what">{{ view.what }}</b>
      </span>
      <span class="work-state">
        <span class="pill" :data-state="view.state">{{ word }}</span>
        <button v-if="view.action" type="button" class="work-action" :class="{ 'is-danger': view.action.danger }" :disabled="busy"
                @click="emit('action', view.action.name)">{{ view.action.label }}</button>
      </span>
    </div>

    <div v-if="view.steps.length" class="work-steps">
      <span v-for="(step, index) in view.steps" :key="index" class="work-step" :class="`is-${step.state}`">
        {{ step.label }}<span v-if="step.time" class="work-step-time">{{ step.time }}</span>
      </span>
    </div>

    <div v-if="view.cells.length" class="work-cells">
      <span v-for="(cell, index) in view.cells" :key="index" class="work-cell" :class="`is-${cell.kind}`" :title="cell.title">{{ cell.n }}</span>
    </div>

    <div v-if="view.facts.length" class="work-facts">
      <span v-for="(each, index) in view.facts" :key="index">
        {{ each.key }}<SourceMark v-if="each.mark" :mark="each.mark" /><b :class="{ 'is-bad': each.bad }">{{ each.value }}</b>
      </span>
    </div>

    <div v-if="view.bar" class="work-bar-row">
      <span v-if="view.bar.label" class="work-bar-label">{{ view.bar.label }}</span>
      <div class="work-bar" :style="{ '--done': `${view.bar.percent}%` }">
        <div class="work-bar-fill"><div class="work-bar-paint" /></div>
        <span class="work-bar-text">{{ view.bar.percent }}%</span>
      </div>
    </div>

    <div v-if="view.rows.length" class="work-rows">
      <div v-for="(row, index) in view.rows" :key="index" class="work-row">
        <span class="work-row-name">{{ row.name }}</span>
        <span v-if="row.metrics" class="work-row-metrics">{{ row.metrics }}</span>
        <span class="pill" :data-state="row.state">{{ row.word }}</span>
        <button v-if="row.plot" type="button" class="work-plot" @click="emit('plot', row.plot)">{{ t("agents.conform.plot.button") }}</button>
      </div>
    </div>

    <div v-if="view.log.length" class="work-log">
      <div v-for="(line, index) in view.log" :key="index" class="work-line">
        <b v-if="line.title" class="work-line-title">{{ line.title }}</b>
        <span v-if="line.variant" class="work-variant"><SourceMark v-if="line.mark" :mark="line.mark" /><b>{{ line.variant }}</b></span>
        <span v-if="line.attempt" class="work-attempt">{{ line.attempt }}</span>
        <span v-if="line.outcome" :class="{ 'is-failed': line.ok === false, 'is-done': line.ok === true }">{{ line.outcome }}</span>
        <span v-if="line.time" class="work-attempt">{{ line.time }}</span>
      </div>
    </div>

    <div v-if="view.note" class="work-note">{{ view.note }}</div>

    <div v-if="view.foot.length" class="work-facts work-foot">
      <span v-for="(each, index) in view.foot" :key="index">{{ each.key }} <b>{{ each.value }}</b></span>
    </div>
  </div>
</template>
