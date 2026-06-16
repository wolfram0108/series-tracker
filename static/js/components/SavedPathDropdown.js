// static/js/components/SavedPathDropdown.js
//
// Выбор сохранённого пути (Настройки → Отладка → «Сохранённые пути»).
// Тонкая обёртка над ConstructorItemSelect — тем же контролом, что у
// «Профиль правил»: одна строка, интегрируется в constructor-group поля
// пути, список через position:fixed не обрезается overflow'ом модалки.
//
// Сам не хранит выбор (всегда показывает placeholder): при выборе
// эмитит готовый путь событием select, а поле пути остаётся обычным
// редактируемым input'ом родителя. Если задан catalogName
// («Имя (год) [tmdbid-XXXX]»), он дописывается в конец через слэш.

const SavedPathDropdown = {
    props: {
        catalogName: { type: String, default: '' },
    },
    emits: ['select'],
    data() {
        return { paths: [] };
    },
    async mounted() {
        await this.loadPaths();
    },
    computed: {
        options() {
            return this.paths.map(p => ({ text: p.path, value: p.path }));
        },
    },
    methods: {
        async loadPaths() {
            try {
                const response = await fetch('/api/settings/saved_paths');
                if (response.ok) this.paths = (await response.json()).paths;
            } catch (e) {
                // Тихо: список просто пуст, ручной ввод пути не блокируется.
            }
        },
        onPick(base) {
            const name = (this.catalogName || '').trim();
            const full = name ? base.replace(/\/+$/, '') + '/' + name : base;
            this.$emit('select', full);
        },
    },
    template: `
        <constructor-item-select
            class="saved-path-select"
            :options="options"
            placeholder="Сохранённые пути"
            @update:modelValue="onPick">
        </constructor-item-select>
    `,
};
