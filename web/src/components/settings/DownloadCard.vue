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
  <div class="card-final card-queue download-card work-card" :class="ground" :data-status="card.status">
    <div class="queue-row work-head">
      <span class="queue-title work-title">
        <span class="work-series">#{{ card.need.series_id }}</span>
        <span>{{ card.need.series_name }} · {{ card.need.episode }} ·</span>
        <template v-if="card.need.kind === 'dub'">
          <VoiceMark v-if="card.need.voice_number" :n="card.need.voice_number" />
          <span>{{ card.need.group }}</span>
        </template>
        <span v-else>{{ t(`download.kind.${card.need.kind}` as MessageKey) }}</span>
      </span>
      <span class="pill">{{ t(`download.status.${card.status}` as MessageKey) }}</span>
      <button v-if="ended" type="button" class="work-remove" @click="emit('remove', card.id)">{{ t("download.remove") }}</button>
    </div>

    <div class="work-steps">
      <span v-for="stage in stages" :key="stage.name" class="work-step" :class="`is-${stage.state}`">
        {{ t(`download.stage.${stage.name}` as MessageKey) }}
        <span v-if="(stage.seconds ?? 0) >= 0.5" class="work-step-time">{{ duration(stage.seconds) }}</span>
      </span>
    </div>

    <div class="work-facts">
      <span>{{ t("download.label.sync") }} <b>#{{ card.need.series_id }} · {{ clock(card.need.sync_started, false) }}</b></span>
      <span v-if="card.variant" class="work-variant">
        {{ t(card.status === "done" ? "download.label.got" : "download.label.variant") }}
        <SourceMark :mark="mark(card.variant)" /><span>{{ card.variant.place }} ·</span><b>{{ card.variant.player_label || card.variant.player }}</b>
        <span v-if="card.variant.height">· {{ height(card.variant) }}</span>
      </span>
      <span>{{ t("download.label.cap") }} <b>{{ card.need.cap ? t("download.value.cap", { height: card.need.cap }) : t("download.value.noCap") }}</b></span>
      <span v-if="card.method">{{ t("download.label.method") }} <b>{{ t(`download.method.${card.method}` as MessageKey) }}</b></span>
    </div>

    <div v-if="card.status === 'queued' && card.position" class="work-facts">
      <span>{{ t("download.label.position") }} <b>{{ card.position }}</b></span>
      <span>{{ t("download.label.running") }} <b>{{ t("download.value.of", { done: card.running, all: card.limit }) }}</b></span>
    </div>

    <div v-if="log.length" class="work-log">
      <div v-for="(line, index) in log" :key="index" class="work-line" :class="`is-${line.kind}`">
        <template v-if="line.kind === 'attempt' && line.variant">
          <span class="work-variant"><SourceMark :mark="mark(line.variant)" /><span>{{ line.variant.place }} ·</span><b>{{ line.variant.player_label || line.variant.player }}</b><span v-if="line.variant.height">· {{ height(line.variant) }}</span></span>
          <span class="work-attempt">{{ t("download.attempt", { n: line.attempt, of: line.of }) }}</span>
          <span :class="`is-${line.outcome}`">{{ lineText(line) }}</span>
          <span>{{ duration(line.seconds) }}</span>
        </template>
        <template v-else-if="line.kind === 'replan' && line.variant">
          <b class="work-replan">{{ t("download.replan") }}</b>
          <span class="work-variant"><SourceMark :mark="mark(line.variant)" /><span>{{ line.variant.place }} ·</span><b>{{ line.variant.player_label || line.variant.player }}</b><span v-if="line.variant.height">· {{ height(line.variant) }}</span></span>
        </template>
        <b v-else-if="line.kind === 'exhausted'" class="work-replan">{{ t("download.exhausted") }}</b>
      </div>
    </div>

    <template v-if="bar">
      <div class="progress">
        <div class="progress-bar progress-bar-striped progress-bar-animated" :class="{ 'bg-info': active === 'remux' }"
             :style="{ width: `${figures.progress}%` }">{{ figures.progress }}%</div>
      </div>
      <div class="work-facts">
        <span v-if="figures.total_mb && active === 'download'">{{ t("download.label.loaded") }}
          <b>{{ t("download.value.of", { done: t("download.value.mb", { value: figures.done_mb ?? 0 }), all: t("download.value.mb", { value: figures.total_mb }) }) }}</b></span>
        <span v-if="figures.speed && active === 'download'">{{ t("download.label.speed") }} <b>{{ t("download.value.mbs", { value: figures.speed }) }}</b></span>
        <span v-if="figures.speed_x">{{ t("download.label.speed") }} <b>{{ t("download.value.x", { value: figures.speed_x }) }}</b></span>
        <span v-if="figures.eta != null">{{ t("download.label.left") }} <b>{{ duration(figures.eta) }}</b></span>
        <span v-if="figures.source_seconds">{{ t("download.label.sourceLength") }} <b>{{ duration(figures.source_seconds) }}</b></span>
      </div>
    </template>
    <div v-else-if="card.status === 'done'" class="work-facts">
      <span v-if="figures.got_height">{{ t("download.label.quality") }} <b>{{ t("download.value.cap", { height: figures.got_height }) }}</b></span>
      <span v-if="figures.got_seconds">{{ t("download.label.length") }} <b>{{ duration(figures.got_seconds) }}</b></span>
      <span v-if="figures.size_mb">{{ t("download.label.size") }} <b>{{ t("download.value.mb", { value: figures.size_mb }) }}</b></span>
    </div>

    <div v-if="card.status === 'failed' && exhausted" class="work-facts">
      <span>{{ t("download.label.outcome") }} <b>{{ t(`download.outcome.${card.need.kind}` as MessageKey) }}</b></span>
      <span>{{ t("download.label.repeat") }} <b>{{ t("download.outcome.repeat") }}</b></span>
    </div>

    <div class="work-facts work-foot">
      <span>{{ t("download.label.queuedAt") }} <b>{{ clock(card.queued_at) }}</b></span>
      <span v-if="card.finished_at">{{ t(card.status === "failed" ? "download.label.failedAt" : "download.label.finishedAt") }} <b>{{ clock(card.finished_at) }}</b></span>
      <span>{{ t("download.label.total") }} <b>{{ duration(card.seconds) }}</b></span>
    </div>
  </div>
</template>
