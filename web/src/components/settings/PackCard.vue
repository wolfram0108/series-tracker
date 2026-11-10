<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../api/schema"
import { t, type MessageKey } from "../../i18n"
import SourceMark from "../status/output/SourceMark.vue"

type Card = components["schemas"]["PackCard"]
type Track = components["schemas"]["PackTrack"]

// A container a sync builds, as the core keeps it (core/contracts/packing.py): its stages with their time, the progress of
// the build, its tracks as numbers of the season (the marks of «Выход») and what changed against the file before.
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
const tracks = computed(() => props.card.tracks ?? [])
const figures = computed(() => props.card.figures ?? {})
const placed = computed(() => tracks.value.filter((track) => track.kind !== "gone"))
const added = computed(() => tracks.value.filter((track) => track.kind === "new"))
const gone = computed(() => tracks.value.filter((track) => track.kind === "gone"))

const duration = (seconds: number | null | undefined): string => {
  const whole = Math.max(0, Math.round(seconds ?? 0))
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`
}
const clock = (iso: string | undefined, seconds = true): string => iso
  ? new Date(iso).toLocaleTimeString("ru-RU", seconds ? undefined : { hour: "2-digit", minute: "2-digit" }) : ""
const mark = computed(() => props.card.video
  ? { n: props.card.video.source_n || 0, color: props.card.video.source_color ?? 0, place: props.card.video.place ?? "" } : null)
const title = (track: Track): string => t(`pack.track.${track.kind}` as MessageKey, { name: track.label })
</script>

<template>
  <div class="card-final card-queue pack-card work-card" :class="ground" :data-status="card.status">
    <div class="queue-row work-head">
      <span class="queue-title work-title"><span class="work-series">#{{ card.series_id }}</span>
        <span>{{ card.series_name }} · {{ card.episode }}</span></span>
      <span class="pill">{{ t(`pack.status.${card.status}` as MessageKey) }}</span>
      <button v-if="card.status === 'failed'" type="button" class="work-remove" @click="emit('remove', card.id)">{{ t("download.remove") }}</button>
    </div>

    <div class="work-steps">
      <span v-for="stage in stages" :key="stage.name" class="work-step" :class="`is-${stage.state}`">
        {{ t(`pack.stage.${stage.name}` as MessageKey) }}
        <span v-if="(stage.seconds ?? 0) >= 0.5" class="work-step-time">{{ duration(stage.seconds) }}</span>
      </span>
    </div>

    <div class="work-facts"><span>{{ t("pack.label.file") }} <b>{{ card.name }}</b></span></div>
    <div class="work-facts">
      <span v-if="card.video" class="work-variant">{{ t("pack.label.video") }}
        <SourceMark :mark="mark" /><span>{{ card.video.place }} ·</span><b>{{ card.video.player_label || card.video.player }}</b>
        <span v-if="card.video.height">· {{ t("download.value.cap", { height: card.video.height }) }}</span>
      </span>
      <span>{{ t("download.label.sync") }} <b>#{{ card.series_id }} · {{ clock(card.sync_started, false) }}</b></span>
    </div>

    <div class="work-tracks">
      <span v-for="(track, index) in placed" :key="index" class="figure-mark" :class="`is-${track.kind}`" :title="title(track)">
        {{ track.number ?? "?" }}
      </span>
      <span v-for="(track, index) in gone" :key="`gone-${index}`" class="figure-mark is-gone" :title="title(track)">
        {{ track.number ?? "?" }}
      </span>
    </div>

    <div class="work-facts">
      <span v-for="(track, index) in added" :key="`add-${index}`" class="work-variant pack-added">+
        <span class="figure-mark">{{ track.number ?? "?" }}</span>{{ track.label }}</span>
      <span v-for="(track, index) in gone" :key="`del-${index}`" class="work-variant pack-gone">−
        <span class="figure-mark">{{ track.number ?? "?" }}</span>{{ track.label }}</span>
      <span v-if="card.reason">{{ t("pack.label.reason") }} <b>{{ t(`pack.reason.${card.reason}` as MessageKey) }}</b></span>
    </div>

    <template v-if="card.status === 'build' && figures.progress != null">
      <div class="progress">
        <div class="progress-bar bg-info progress-bar-striped progress-bar-animated" :style="{ width: `${figures.progress}%` }">{{ figures.progress }}%</div>
      </div>
      <div class="work-facts">
        <span v-if="figures.written_mb">{{ t("pack.label.written") }} <b>{{ t("download.value.mb", { value: figures.written_mb }) }}</b></span>
        <span v-if="figures.speed_x">{{ t("download.label.speed") }} <b>{{ t("download.value.x", { value: figures.speed_x }) }}</b></span>
        <span v-if="figures.eta != null">{{ t("download.label.left") }} <b>{{ duration(figures.eta) }}</b></span>
      </div>
    </template>
    <div v-if="card.status === 'apply' && figures.steps_of" class="work-facts">
      <span>{{ t("pack.label.steps") }} <b>{{ t("download.value.of", { done: figures.steps_done ?? 0, all: figures.steps_of }) }}</b></span>
    </div>
    <div v-if="card.status === 'done' && figures.size_mb" class="work-facts">
      <span>{{ t("download.label.size") }} <b>{{ t("download.value.mb", { value: figures.size_mb }) }}</b></span>
      <span>{{ t("pack.label.tracks") }} <b>{{ placed.length }}</b></span>
    </div>
    <div v-if="card.status === 'failed'" class="work-facts">
      <span>{{ t("download.label.outcome") }} <b>{{ t(card.waited ? "pack.outcome.waited" : "pack.outcome.failed") }}</b></span>
      <span>{{ t("download.label.repeat") }} <b>{{ t("download.outcome.repeat") }}</b></span>
    </div>

    <div class="work-facts work-foot">
      <span>{{ t("download.label.queuedAt") }} <b>{{ clock(card.queued_at) }}</b></span>
      <span v-if="card.finished_at">{{ t(card.status === "failed" ? "download.label.failedAt" : "download.label.finishedAt") }} <b>{{ clock(card.finished_at) }}</b></span>
      <span>{{ t("download.label.total") }} <b>{{ duration(card.seconds) }}</b></span>
    </div>
  </div>
</template>

<style scoped>
.pack-added { color: var(--color-blue); font-weight: 600; }
.pack-gone { color: var(--color-red-1); font-weight: 600; }
</style>
