<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../api/schema"
import { t, type MessageKey } from "../../i18n"
import SourceMark from "../status/output/SourceMark.vue"
import VoiceMark from "../status/output/VoiceMark.vue"

type Card = components["schemas"]["DownloadCard"]
type Variant = components["schemas"]["DownloadVariant"]
type Line = components["schemas"]["AttemptLine"]

// A download as the core keeps it (core/contracts/downloads.py): one need of a plan through every variant it took, in the
// marks of the window «Выход». The card only shows what the core tells; its time comes with every packet.
const props = defineProps<{ card: Card }>()
const emit = defineEmits<{ (e: "remove", id: string): void }>()

const ended = computed(() => props.card.status === "done" || props.card.status === "failed")
const ground = computed(() => ({
  "is-running": !ended.value && props.card.status !== "queued",
  "is-done": props.card.status === "done",
  "is-failed": props.card.status === "failed",
}))
// What the core leaves out of a packet is empty, never missing.
const stages = computed(() => props.card.stages ?? [])
const log = computed(() => props.card.log ?? [])
const figures = computed(() => props.card.figures ?? {})
const active = computed(() => stages.value.find((stage) => stage.state === "active")?.name ?? null)
const bar = computed(() => (active.value === "download" || active.value === "remux") && figures.value.progress != null)
const exhausted = computed(() => log.value.at(-1)?.kind === "exhausted")

const duration = (seconds: number | null | undefined): string => {
  const whole = Math.max(0, Math.round(seconds ?? 0))
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`
}
const clock = (iso: string, seconds = true): string => iso
  ? new Date(iso).toLocaleTimeString("ru-RU", seconds ? undefined : { hour: "2-digit", minute: "2-digit" }) : ""
const mark = (variant: Variant) => ({ n: variant.source_n || 0, color: variant.source_color ?? 0, place: variant.place ?? "" })
const height = (variant: Variant): string => variant.height ? t("download.value.cap", { height: variant.height }) : ""
const lineText = (line: Line): string => line.outcome === "done" ? t("download.done")
  : line.outcome === "failed" ? t(`download.failed.${line.stage ?? "link"}` as MessageKey)
    : t(`download.running.${active.value ?? "queue"}` as MessageKey)
</script>

<template>
  <div class="card-final card-queue download-card" :class="ground" :data-status="card.status">
    <div class="queue-row download-head">
      <span class="queue-title download-title">
        <span class="download-series">#{{ card.need.series_id }}</span>
        <span>{{ card.need.series_name }} · {{ card.need.episode }} ·</span>
        <template v-if="card.need.kind === 'dub'">
          <VoiceMark v-if="card.need.voice_number" :n="card.need.voice_number" />
          <span>{{ card.need.group }}</span>
        </template>
        <span v-else>{{ t(`download.kind.${card.need.kind}` as MessageKey) }}</span>
      </span>
      <span class="pill">{{ t(`download.status.${card.status}` as MessageKey) }}</span>
      <button v-if="ended" type="button" class="download-remove" @click="emit('remove', card.id)">{{ t("download.remove") }}</button>
    </div>

    <div class="download-steps">
      <span v-for="stage in stages" :key="stage.name" class="download-step" :class="`is-${stage.state}`">
        {{ t(`download.stage.${stage.name}` as MessageKey) }}
        <span v-if="(stage.seconds ?? 0) >= 0.5" class="download-step-time">{{ duration(stage.seconds) }}</span>
      </span>
    </div>

    <div class="download-facts">
      <span>{{ t("download.label.sync") }} <b>#{{ card.need.series_id }} · {{ clock(card.need.sync_started, false) }}</b></span>
      <span v-if="card.variant" class="download-variant">
        {{ t(card.status === "done" ? "download.label.got" : "download.label.variant") }}
        <SourceMark :mark="mark(card.variant)" /><span>{{ card.variant.place }} ·</span><b>{{ card.variant.player_label || card.variant.player }}</b>
        <span v-if="card.variant.height">· {{ height(card.variant) }}</span>
      </span>
      <span>{{ t("download.label.cap") }} <b>{{ card.need.cap ? t("download.value.cap", { height: card.need.cap }) : t("download.value.noCap") }}</b></span>
      <span v-if="card.method">{{ t("download.label.method") }} <b>{{ t(`download.method.${card.method}` as MessageKey) }}</b></span>
    </div>

    <div v-if="card.status === 'queued' && card.position" class="download-facts">
      <span>{{ t("download.label.position") }} <b>{{ card.position }}</b></span>
      <span>{{ t("download.label.running") }} <b>{{ t("download.value.of", { done: card.running, all: card.limit }) }}</b></span>
    </div>

    <div v-if="log.length" class="download-log">
      <div v-for="(line, index) in log" :key="index" class="download-line" :class="`is-${line.kind}`">
        <template v-if="line.kind === 'attempt' && line.variant">
          <span class="download-variant"><SourceMark :mark="mark(line.variant)" /><span>{{ line.variant.place }} ·</span><b>{{ line.variant.player_label || line.variant.player }}</b><span v-if="line.variant.height">· {{ height(line.variant) }}</span></span>
          <span class="download-attempt">{{ t("download.attempt", { n: line.attempt, of: line.of }) }}</span>
          <span :class="`is-${line.outcome}`">{{ lineText(line) }}</span>
          <span>{{ duration(line.seconds) }}</span>
        </template>
        <template v-else-if="line.kind === 'replan' && line.variant">
          <b class="download-replan">{{ t("download.replan") }}</b>
          <span class="download-variant"><SourceMark :mark="mark(line.variant)" /><span>{{ line.variant.place }} ·</span><b>{{ line.variant.player_label || line.variant.player }}</b><span v-if="line.variant.height">· {{ height(line.variant) }}</span></span>
        </template>
        <b v-else-if="line.kind === 'exhausted'" class="download-replan">{{ t("download.exhausted") }}</b>
      </div>
    </div>

    <template v-if="bar">
      <div class="progress">
        <div class="progress-bar progress-bar-striped progress-bar-animated" :class="{ 'bg-info': active === 'remux' }"
             :style="{ width: `${figures.progress}%` }">{{ figures.progress }}%</div>
      </div>
      <div class="download-facts">
        <span v-if="figures.total_mb && active === 'download'">{{ t("download.label.loaded") }}
          <b>{{ t("download.value.of", { done: t("download.value.mb", { value: figures.done_mb ?? 0 }), all: t("download.value.mb", { value: figures.total_mb }) }) }}</b></span>
        <span v-if="figures.speed && active === 'download'">{{ t("download.label.speed") }} <b>{{ t("download.value.mbs", { value: figures.speed }) }}</b></span>
        <span v-if="figures.speed_x">{{ t("download.label.speed") }} <b>{{ t("download.value.x", { value: figures.speed_x }) }}</b></span>
        <span v-if="figures.eta != null">{{ t("download.label.left") }} <b>{{ duration(figures.eta) }}</b></span>
        <span v-if="figures.source_seconds">{{ t("download.label.sourceLength") }} <b>{{ duration(figures.source_seconds) }}</b></span>
      </div>
    </template>
    <div v-else-if="card.status === 'done'" class="download-facts">
      <span v-if="figures.got_height">{{ t("download.label.quality") }} <b>{{ t("download.value.cap", { height: figures.got_height }) }}</b></span>
      <span v-if="figures.got_seconds">{{ t("download.label.length") }} <b>{{ duration(figures.got_seconds) }}</b></span>
      <span v-if="figures.size_mb">{{ t("download.label.size") }} <b>{{ t("download.value.mb", { value: figures.size_mb }) }}</b></span>
    </div>

    <div v-if="card.status === 'failed' && exhausted" class="download-facts">
      <span>{{ t("download.label.outcome") }} <b>{{ t(`download.outcome.${card.need.kind}` as MessageKey) }}</b></span>
      <span>{{ t("download.label.repeat") }} <b>{{ t("download.outcome.repeat") }}</b></span>
    </div>

    <div class="download-facts download-foot">
      <span>{{ t("download.label.queuedAt") }} <b>{{ clock(card.queued_at) }}</b></span>
      <span v-if="card.finished_at">{{ t(card.status === "failed" ? "download.label.failedAt" : "download.label.finishedAt") }} <b>{{ clock(card.finished_at) }}</b></span>
      <span>{{ t("download.label.total") }} <b>{{ duration(card.seconds) }}</b></span>
    </div>
  </div>
</template>

<style scoped>
.download-head { flex-wrap: wrap; }
.download-title { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; flex: 1 1 16rem; min-width: 0; font-weight: 600; }
.download-series { color: var(--color-gray-600); font-weight: 700; }
.download-steps { display: flex; flex-wrap: wrap; gap: 4px; }
.download-step { display: inline-flex; gap: 5px; align-items: baseline; font-size: 0.75rem; padding: 2px 7px; border-radius: 6px;
  border: 1px solid rgba(0, 0, 0, 0.08); background: rgba(255, 255, 255, 0.55); color: var(--text-muted); font-variant-numeric: tabular-nums; }
.download-step.is-done { background: rgba(255, 255, 255, 0.9); color: var(--color-text); }
.download-step.is-active { background: var(--bg-white); border-color: var(--color-blue); color: var(--color-text); font-weight: 600; }
.download-step.is-failed { background: var(--bg-white); border-color: var(--color-red-1); color: var(--color-text); font-weight: 600; }
.download-step.is-skipped { text-decoration: line-through; opacity: 0.55; }
.download-step-time { color: var(--text-muted); font-weight: 400; }
.download-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 16px; font-size: 0.8rem; color: var(--color-gray-600);
  font-variant-numeric: tabular-nums; }
.download-facts > span { display: inline-flex; align-items: center; gap: 6px; }
.download-facts b { color: var(--color-text); font-weight: 600; }
.download-foot { border-top: 1px dashed rgba(0, 0, 0, 0.2); padding-top: 8px; }
.download-variant { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.download-log { display: grid; gap: 3px; font-size: 0.8rem; border-left: 2px solid rgba(0, 0, 0, 0.12); padding-left: 10px;
  font-variant-numeric: tabular-nums; }
.download-line { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
.download-attempt { color: var(--color-gray-600); }
.download-line .is-failed { color: var(--color-red-1); }
.download-line .is-done { color: var(--color-green-1); }
.download-replan { color: var(--color-red-2); }
.download-remove { border: 0; border-radius: 7px; padding: 4px 12px; font: inherit; font-size: 0.8rem; font-weight: 600; color: #fff;
  cursor: pointer; background: linear-gradient(135deg, var(--color-red-1) 0%, #c82333 100%); }
.download-remove:focus-visible { outline: 2px solid var(--color-blue); outline-offset: 2px; }
</style>
