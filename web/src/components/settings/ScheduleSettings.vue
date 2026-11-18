<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import Button from "primevue/button"
import SheetField from "../SheetField.vue"
import StNumber from "../StNumber.vue"
import StSwitch from "../StSwitch.vue"
import DayLine from "./DayLine.vue"
import SourceTerms from "./SourceTerms.vue"
import { api } from "../../api/client"
import { t } from "../../i18n"
import { useApi } from "../../composables/useApi"
import { useAction } from "../../composables/useAction"
import { useScannerStore } from "../../stores/scanner"
import { shortDate } from "../../lib/freshness"
import { Gather } from "../../lib/gather"
import { freeSpot, minuteOf, scheduleOf, tidy, type PointKind, type ScheduleValue } from "../../lib/schedule"

// The settings «Расписание»: the syncs of all series at times of the day and the checks of the torrent releases on one line
// of 24 hours, free or between the syncs. What is shown is the core's schedule (scanner_status_update); a change the user is
// making is shown at once and goes to the core as one command when the user stops (law-ui: debounce).
const scanner = useScannerStore()
const { request } = useApi()

const PAUSE_MS = 800
const draft = ref<ScheduleValue | null>(null)
// The schedule as the user is making it goes as one command once the user is quiet; a refused one gives way to the core's.
const outbox = new Gather<ScheduleValue>({
  pause: PAUSE_MS,
  send: async (changes) => {
    const sent = changes[changes.length - 1]
    const saved = await request(api.POST("/api/schedule", { body: { schedule: sent } }), { errorMessage: t("schedule.saveFailed") })
    if (saved === null && draft.value === sent) draft.value = null
    return saved === null ? t("schedule.saveFailed") : null
  },
})
const schedule = computed<ScheduleValue>(() => draft.value ?? scheduleOf(scanner.status?.schedule))
const free = computed(() => schedule.value.torrent_mode === "free")
// The checks the line shows: the user's own in the free mode, the core's between the syncs otherwise.
const checks = computed(() => (free.value ? schedule.value.torrent_free : scanner.status?.checks ?? []))
const picked = ref<{ kind: PointKind; at: number } | null>(null)
const exactOpen = ref(false)

function stage(next: ScheduleValue): void {
  draft.value = { ...next, syncs: tidy(next.syncs), torrent_free: tidy(next.torrent_free) }
  picked.value = null
  outbox.put("schedule", draft.value)
}
// The core's schedule took the change: the draft gives way to it.
watch(() => scanner.status?.schedule, (held) => {
  if (draft.value && held && JSON.stringify(scheduleOf(held)) === JSON.stringify(draft.value)) draft.value = null
})
onBeforeUnmount(() => void outbox.flush())

const listOf = (kind: PointKind): "syncs" | "torrent_free" => (kind === "sync" ? "syncs" : "torrent_free")
function move(kind: PointKind, at: number, time: string): void {
  if (minuteOf(time) === null) return
  const list = [...schedule.value[listOf(kind)]]
  list[at] = time
  stage({ ...schedule.value, [listOf(kind)]: list })
}
function remove(kind: PointKind, at: number): void {
  stage({ ...schedule.value, [listOf(kind)]: schedule.value[listOf(kind)].filter((_, index) => index !== at) })
}
function add(kind: PointKind): void {
  const taken = [...schedule.value.syncs, ...checks.value]
  stage({ ...schedule.value, [listOf(kind)]: [...schedule.value[listOf(kind)], freeSpot(taken)] })
}
function setMode(mode: ScheduleValue["torrent_mode"]): void {
  if (mode === schedule.value.torrent_mode) return
  // The free mode begins from the checks the line showed, so switching moves no point.
  const freeTimes = mode === "free" && !schedule.value.torrent_free.length ? [...checks.value] : schedule.value.torrent_free
  stage({ ...schedule.value, torrent_mode: mode, torrent_free: freeTimes })
}
const between = ref(String(schedule.value.torrent_between))
watch(() => schedule.value.torrent_between, (value) => { between.value = String(value) })
function setBetween(): void {
  const value = Number(between.value)
  if (Number.isInteger(value) && value >= 0 && value <= 12) stage({ ...schedule.value, torrent_between: value })
  else between.value = String(schedule.value.torrent_between)
}

async function setEnabled(value: boolean): Promise<void> {
  await request(api.POST("/api/scanner/settings", { body: { enabled: value } }), { errorMessage: t("debug.autoSync.saveFailed") })
}
const scanAll = useAction(async () => {
  await request(api.POST("/api/scanner/scan_all", {}), { errorMessage: t("debug.syncAll.failed") })
})
const nextSync = computed(() => scanner.status?.next_scan_time ? shortDate(scanner.status.next_scan_time) : "—")
const nextCheck = computed(() => scanner.status?.next_check_time ? shortDate(scanner.status.next_check_time) : "—")
</script>

<template>
  <div class="schedule">
    <div class="sheet-pair">
      <SheetField :label="t('series.autoSync')" control>
        <StSwitch :model-value="scanner.status?.scanner_enabled ?? false" @update:model-value="setEnabled" />
      </SheetField>
      <SheetField :label="t('schedule.checksMode')" control>
        <div class="st-tabs schedule-mode" role="group" :aria-label="t('schedule.checksMode')">
          <button type="button" class="st-tab" :class="{ active: free }" :aria-pressed="free" @click="setMode('free')">
            {{ t("schedule.mode.free") }}</button>
          <button type="button" class="st-tab" :class="{ active: !free }" :aria-pressed="!free" @click="setMode('between')">
            {{ t("schedule.mode.between") }}</button>
        </div>
      </SheetField>
    </div>

    <DayLine :syncs="schedule.syncs" :checks="checks" :checks-move="free" :picked="picked"
             @move="move" @remove="remove" @pick="(kind, at) => (picked = { kind, at })" />

    <section class="schedule-fold">
      <button type="button" class="schedule-fold-head" :aria-expanded="exactOpen" @click="exactOpen = !exactOpen">
        <i class="pi" :class="exactOpen ? 'pi-chevron-down' : 'pi-chevron-right'" aria-hidden="true" />{{ t("schedule.exact") }}
        <span class="sheet-count">{{ schedule.syncs.length + checks.length }}</span>
      </button>
      <div v-if="exactOpen" class="schedule-exact">
        <div class="schedule-column">
          <h4 class="schedule-column-head is-sync">{{ t("schedule.syncs") }}</h4>
          <div v-for="(time, at) in schedule.syncs" :key="`s${at}${time}`" class="schedule-time">
            <span class="schedule-dot is-sync" aria-hidden="true" />
            <input type="time" :value="time" :aria-label="t('schedule.syncPoint', { at: time })"
                   @change="move('sync', at, ($event.target as HTMLInputElement).value)" />
            <button type="button" class="schedule-x" :aria-label="t('schedule.remove', { at: time })" @click="remove('sync', at)">
              <i class="pi pi-times" aria-hidden="true" /></button>
          </div>
        </div>
        <div class="schedule-column">
          <h4 class="schedule-column-head is-check">{{ t("schedule.checks") }}</h4>
          <div v-for="(time, at) in checks" :key="`c${at}${time}`" class="schedule-time">
            <span class="schedule-dot is-check" aria-hidden="true" />
            <input v-if="free" type="time" :value="time" :aria-label="t('schedule.checkPoint', { at: time })"
                   @change="move('check', at, ($event.target as HTMLInputElement).value)" />
            <span v-else class="schedule-fixed">{{ time }}</span>
            <button v-if="free" type="button" class="schedule-x" :aria-label="t('schedule.remove', { at: time })"
                    @click="remove('check', at)"><i class="pi pi-times" aria-hidden="true" /></button>
          </div>
        </div>
      </div>
    </section>

    <div class="schedule-actions">
      <Button :label="t('schedule.addSync')" icon="pi pi-plus" severity="info" outlined @click="add('sync')" />
      <Button v-if="free" :label="t('schedule.addCheck')" icon="pi pi-plus" severity="warn" outlined @click="add('check')" />
      <SheetField v-else :label="t('schedule.between')" control>
        <StNumber v-model="between" @change="setBetween" />
      </SheetField>
      <span class="schedule-next">
        <span>{{ t("schedule.nextSync") }} <b class="is-sync">{{ nextSync }}</b></span>
        <span>{{ t("schedule.nextCheck") }} <b class="is-check">{{ nextCheck }}</b></span>
      </span>
    </div>
    <div class="sheet-line">
      <Button :label="t('debug.syncAll.button')" icon="pi pi-refresh" :loading="scanAll.pending()"
              :disabled="scanner.status?.is_scanning" @click="scanAll.run()" />
    </div>

    <SourceTerms />
  </div>
</template>

<style scoped>
.schedule { display: flex; flex-direction: column; gap: 0.75rem; }
.schedule-mode { flex-wrap: wrap; }
.schedule-fold { display: flex; flex-direction: column; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; }
.schedule-fold-head { display: flex; align-items: center; gap: 0.6rem; min-block-size: 2.5rem; padding-inline: 1rem; border: 0;
  background: var(--bg-light); font-weight: 600; text-align: start; cursor: pointer; color: inherit; font-size: 0.9rem; }
.schedule-fold-head .sheet-count { margin-inline-start: auto; }
.schedule-exact { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); }
.schedule-column { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.5rem 1rem 0.75rem; }
.schedule-column-head { margin: 0; padding-block: 0.25rem; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.is-sync { color: var(--color-blue-ink); }
.is-check { color: var(--fresh-stale-ink); }
.schedule-time { display: flex; align-items: center; gap: 0.5rem; min-block-size: 2.5rem; }
.schedule-time input { font: inherit; padding: 0.2rem 0.4rem; border: 1px solid var(--border-color); border-radius: 0.4rem; }
.schedule-dot { inline-size: 0.85rem; block-size: 0.85rem; flex: none; }
.schedule-dot.is-sync { border-radius: 0.25rem; background: var(--color-blue); }
.schedule-dot.is-check { border-radius: 50%; background: var(--check-color); }
.schedule-x { margin-inline-start: auto; border: 0; background: transparent; color: var(--text-muted); cursor: pointer; padding: 0.4rem; }
.schedule-actions { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.schedule-next { margin-inline-start: auto; display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.9rem; color: var(--text-muted); }
</style>
