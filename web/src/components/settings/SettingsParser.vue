<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StLabel from "../StLabel.vue"
import StSelect from "../StSelect.vue"
import RulesEditor from "./RulesEditor.vue"
import RulesAddButtons from "./RulesAddButtons.vue"
import ParserTest from "./ParserTest.vue"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { api } from "../../api/client"
import { t } from "../../i18n"
import { useBusyBar } from "../../composables/useWindowBusy"
import type { SourceAddress } from "../../lib/sources"

// Вкладка «Фильтры VK» окна Настроек — конфигуратор профилей парсера.
// Аккордеон трёх шагов (профили / правила / тест), как в оригинале
// (static/js/components/settingsParser.js). Step 2/3 видимы после выбора
// профиля. Вся API профилей/правил живёт здесь; карточка правила и тест —
// отдельные компоненты.

// testFirst: a profile picked opens the step of the test at once instead of its rules (the add window, whose aim is
// to check the naming); the settings do not pass it.
const props = defineProps<{
  testInitialUrl?: string
  testFirst?: boolean
  /** The sources of the series the section stands in (its fields of the test offer them); none in the settings. */
  loadSources?: () => Promise<SourceAddress[]>
}>()
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

interface Profile { id: number; name: string; preferred_voiceovers?: string }
const profiles = ref<Profile[]>([])
const selectedProfileId = ref<number | null>(null)
const newProfileName = ref("")
const isLoading = ref(false)
useBusyBar(() => isLoading.value)
// The lines of the test (step 3) and the codes of the fields of a line: what the quick templates of a table are made of.
const testLines = ref<string[]>([])
const lineFields = ref<string[]>([])
const openStep = ref<"profiles" | "rules" | "test" | null>("profiles")

const editingProfileId = ref<number | null>(null)
const editingProfileName = ref("")
const editInput = ref<InstanceType<typeof StInput> | null>(null)

const profileOptions = computed(() => [
  { label: t("rules.profile.select"), value: null as number | null },
  ...profiles.value.map((p) => ({ label: p.name, value: p.id as number | null })),
])
const selectedProfileName = computed(
  () => profiles.value.find((p) => p.id === selectedProfileId.value)?.name ?? "",
)

// A rule or a table added from the head of the step opens the step, so the new card is seen.
const rulesEditor = ref<InstanceType<typeof RulesEditor> | null>(null)
function addTo(what: "addRule" | "addTable"): void {
  openStep.value = "rules"
  rulesEditor.value?.[what]()
}

function toggleStep(s: "profiles" | "rules" | "test"): void {
  openStep.value = openStep.value === s ? null : s
}

// --- Профили ----------------------------------------------------------
async function loadProfiles(): Promise<void> {
  isLoading.value = true
  const data = (await request(
    api.GET("/api/parser-profiles"),
    { errorMessage: t("rules.profile.loadFailed") },
  )) as Profile[] | null
  if (data) profiles.value = data
  isLoading.value = false
}

async function createProfile(): Promise<void> {
  const name = newProfileName.value.trim()
  if (!name) return
  isLoading.value = true
  const data = (await request(
    api.POST("/api/parser-profiles", { body: { name } }),
    { errorMessage: t("rules.profile.createFailed") },
  )) as { success: boolean; id: number } | null
  isLoading.value = false
  if (data?.id) {
    newProfileName.value = ""
    await loadProfiles()
    selectedProfileId.value = data.id
  }
}

function startEditingProfile(): void {
  if (!selectedProfileId.value) return
  editingProfileId.value = selectedProfileId.value
  editingProfileName.value = selectedProfileName.value
  nextTick(() => editInput.value?.focus())
}
function cancelEditing(): void {
  editingProfileId.value = null
  editingProfileName.value = ""
}
async function saveProfileName(): Promise<void> {
  const name = editingProfileName.value.trim()
  if (!name || !editingProfileId.value) return cancelEditing()
  const current = profiles.value.find((p) => p.id === editingProfileId.value)
  if (current && current.name === name) return cancelEditing()
  isLoading.value = true
  const ok = await request(
    api.PUT("/api/parser-profiles/{profile_id}", {
      params: { path: { profile_id: editingProfileId.value } },
      body: { name },
    }),
    { errorMessage: t("rules.profile.renameFailed") },
  )
  isLoading.value = false
  if (ok) {
    toast.add({ severity: "success", summary: t("rules.profile.toast"), detail: t("rules.profile.renamed"), life: 2500 })
    const keep = selectedProfileId.value
    await loadProfiles()
    selectedProfileId.value = keep
  }
  cancelEditing()
}
async function deleteProfile(): Promise<void> {
  if (!selectedProfileId.value) return
  const r = await confirm.open({
    title: t("rules.profile.deleteTitle"),
    message: t("rules.profile.deleteMessage", { profile: selectedProfileName.value }),
  })
  if (!r.confirmed) return
  isLoading.value = true
  const ok = await request(
    api.DELETE("/api/parser-profiles/{profile_id}", {
      params: { path: { profile_id: selectedProfileId.value } },
    }),
    { errorMessage: t("rules.profile.deleteFailed") },
  )
  isLoading.value = false
  if (ok) {
    selectedProfileId.value = null
    await loadProfiles()
  }
}

watch(selectedProfileId, (id) => {
  if (id) openStep.value = props.testFirst ? "test" : "rules"
})

async function loadLineFields(): Promise<void> {
  const codes = await request(api.GET("/api/rules/line-fields"))
  if (codes) lineFields.value = codes
}

onMounted(() => {
  void loadProfiles()
  void loadLineFields()
})
</script>

<template>
  <div class="parser-accordion">
    <!-- Шаг 1: профили -->
    <div class="accordion-item">
      <button class="accordion-button" :class="{ active: openStep === 'profiles' }" @click="toggleStep('profiles')">
        <span>{{ t("rules.step.profiles") }}</span>
        <i class="pi pi-chevron-down acc-chevron" />
      </button>
      <div v-if="openStep === 'profiles'" class="accordion-body">
        <p class="parser-hint">{{ t("rules.step.profiles.hint") }}</p>
        <StGroup>
          <template v-if="!editingProfileId">
            <StLabel icon="pi pi-user" :text="t('rules.profile.field')" />
            <StSelect v-model="selectedProfileId" :options="profileOptions" :placeholder="t('rules.profile.select')" />
            <StInput v-model="newProfileName" :placeholder="t('rules.profile.new')" :aria-label="t('rules.profile.new')" @keyup.enter="createProfile" />
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-add" :disabled="!newProfileName.trim() || isLoading" :title="t('rules.profile.create')" :aria-label="t('rules.profile.create')" @click="createProfile">
                <i class="pi pi-plus" />
              </button>
              <button class="btn-icon btn-edit" :disabled="!selectedProfileId || isLoading" :title="t('rules.profile.rename')" :aria-label="t('rules.profile.rename')" @click="startEditingProfile">
                <i class="pi pi-pencil" />
              </button>
              <button class="btn-icon btn-delete" :disabled="!selectedProfileId || isLoading" :title="t('rules.profile.delete')" :aria-label="t('rules.profile.delete')" @click="deleteProfile">
                <i class="pi pi-trash" />
              </button>
            </div>
          </template>
          <template v-else>
            <StLabel icon="pi pi-pencil" :text="t('rules.profile.renameLabel')" />
            <StInput ref="editInput" v-model="editingProfileName" :aria-label="t('rules.profile.renameLabel')" @keyup.enter="saveProfileName" @keydown.esc.prevent="cancelEditing" />
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-confirm" :disabled="!editingProfileName.trim()" :title="t('rules.profile.apply')" :aria-label="t('rules.profile.apply')" @click="saveProfileName">
                <i class="pi pi-check" />
              </button>
              <button class="btn-icon btn-cancel" :title="t('rules.profile.cancel')" :aria-label="t('rules.profile.cancel')" @click="cancelEditing">
                <i class="pi pi-times" />
              </button>
            </div>
          </template>
        </StGroup>
      </div>
    </div>

    <template v-if="selectedProfileId">
      <!-- Шаг 2: правила -->
      <div class="accordion-item">
        <!-- The whole bar folds the step (its title button is the one of the keyboard); the chevron stands after the buttons. -->
        <div class="accordion-bar" :class="{ active: openStep === 'rules' }" @click="toggleStep('rules')">
          <button class="accordion-button" :class="{ active: openStep === 'rules' }" :aria-expanded="openStep === 'rules'">
            <span>{{ t("rules.step.rules", { profile: selectedProfileName }) }}</span>
          </button>
          <RulesAddButtons :disabled="rulesEditor?.isLoading ?? true" @rule="addTo('addRule')" @table="addTo('addTable')" />
          <i class="pi pi-chevron-down acc-chevron" aria-hidden="true" />
        </div>
        <!-- Kept while folded, as the rules were before: a rule being written is not lost when the step is closed. -->
        <div v-show="openStep === 'rules'" class="accordion-body">
          <RulesEditor ref="rulesEditor" :profile-id="selectedProfileId" :lines="testLines" :codes="lineFields" />
        </div>
      </div>

      <!-- Шаг 3: тест -->
      <div class="accordion-item">
        <button class="accordion-button" :class="{ active: openStep === 'test' }" @click="toggleStep('test')">
          <span>{{ t("rules.step.test") }}</span>
          <i class="pi pi-chevron-down acc-chevron" />
        </button>
        <div v-show="openStep === 'test'" class="accordion-body">
          <ParserTest
            :profile-id="selectedProfileId"
            :initial-url="testInitialUrl"
            :load-sources="loadSources"
            @lines="testLines = $event"
          />
        </div>
      </div>
    </template>
  </div>
</template>
