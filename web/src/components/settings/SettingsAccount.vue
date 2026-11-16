<script setup lang="ts">
import { ref, computed } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useAuthStore } from "../../stores/auth"
import { t, tAround } from "../../i18n"

// The tab «Аккаунт»: the password of the administrator. POST /api/auth/password (behind the lock) changes the hash. The
// current password is not asked: the session already proves the owner of a one-user application. The fields are
// SheetField with their frame for validity; the outcome is a Toast.
const auth = useAuthStore()
const toast = useToast()
const { request, loading } = useApi()

const next = ref("")
const next2 = ref("")
const attempted = ref(false)

type VState = "valid" | "invalid" | null
const touched = (v: string) => attempted.value || v.length > 0

const nextState = computed<VState>(
  () => (touched(next.value) ? (next.value.length >= 8 ? "valid" : "invalid") : null))
const next2State = computed<VState>(() => {
  if (!touched(next2.value)) return null
  return next2.value === next.value && next.value.length >= 8 ? "valid" : "invalid"
})
const hint = tAround("account.hint", "name")

const canSubmit = computed(
  () => next.value.length >= 8 && next.value === next2.value)

async function submit(): Promise<void> {
  attempted.value = true
  if (!canSubmit.value) {
    toast.add({
      severity: "warn", summary: t("common.checkFields"), life: 4000,
      detail: next.value.length < 8
        ? t("account.error.short")
        : t("common.passwordsMismatch"),
    })
    return
  }
  const ok = await request(
    api.POST("/api/auth/password", {
      body: { new: next.value },
    }),
    { errorMessage: t("account.error.change") },
  )
  if (ok === null) return
  toast.add({ severity: "success", summary: t("common.done"),
    detail: t("account.changed"), life: 4000 })
  next.value = ""
  next2.value = ""
  attempted.value = false
}
</script>

<template>
  <div class="sheet settings-account">
    <SheetRow :caption="t('account.section')">
      <div class="sheet-pair">
        <SheetField :label="t('account.new')" icon="pi pi-key" :state="nextState">
          <StInput v-model="next" type="password" :placeholder="t('account.newHint')" />
        </SheetField>
        <SheetField :label="t('account.repeat')" icon="pi pi-key" :state="next2State">
          <StInput v-model="next2" type="password" />
        </SheetField>
      </div>
      <p class="sheet-hint">{{ hint[0] }}<b>{{ auth.adminName }}</b>{{ hint[1] }}</p>
      <div class="sheet-line is-end">
        <Button :label="t('account.change')" icon="pi pi-check" :loading="loading" @click="submit" />
      </div>
    </SheetRow>
  </div>
</template>
