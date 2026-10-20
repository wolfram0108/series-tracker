import { defineStore } from "pinia"
import { computed, ref } from "vue"

/** Прогресс опроса серий (sources.video.progress → video_scan_progress). */
export interface VideoScanProgress {
  series_id: number | null
  current: number
  total: number
  rate: number
  phase: string
  active: boolean
}

export const useVideoScanStore = defineStore("videoScan", () => {
  const progress = ref<VideoScanProgress | null>(null)

  function set(value: VideoScanProgress): void {
    progress.value = value.active ? value : null
  }

  function forSeries(id: number): VideoScanProgress | null {
    const value = progress.value
    return value && value.series_id === id ? value : null
  }

  function format(value: VideoScanProgress | null): string {
    if (!value) return ""
    return `${value.current} из ${value.total} | ${value.rate.toFixed(1)} с/с`
  }

  const text = computed(() => format(progress.value))

  return { progress, set, forSeries, format, text }
})
