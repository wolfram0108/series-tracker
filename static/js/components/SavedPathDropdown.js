// static/js/components/SavedPathDropdown.js
//
// Выбор сохранённого пути (Настройки → Отладка → «Сохранённые пути»).
// Встраивается в constructor-group поля пути как компактная кнопка-
// шеврон без подписи. Выпадающий список разворачивается во всю ширину
// поля и позиционируется position:fixed — поэтому не обрезается
// overflow'ом модалки (та же идея, что в ConstructorItemSelect, но без
// его глобального querySelector и ручной логики «вверх/вниз»).
//
// Сам не хранит выбор: при клике по пути эмитит готовое значение
// событием select; поле ввода остаётся обычным редактируемым input'ом
// родителя. Если задан catalogName («Имя (год) [tmdbid-XXXX]»), он
// дописывается в конец через слэш.

const SavedPathDropdown = {
    props: {
        catalogName: { type: String, default: '' },
    },
    emits: ['select'],
    data() {
        return { isOpen: false, paths: [], listStyle: {} };
    },
    mounted() {
        document.addEventListener('click', this.onOutside, true);
    },
    beforeUnmount() {
        document.removeEventListener('click', this.onOutside, true);
    },
    methods: {
        async toggle() {
            if (this.isOpen) { this.isOpen = false; return; }
            await this.loadPaths();
            this.positionList();
            this.isOpen = true;
        },
        positionList() {
            // Якорь — вся группа поля пути: список во всю её ширину.
            const group = this.$el.closest('.input-constructor-group') || this.$el;
            const rect = group.getBoundingClientRect();
            this.listStyle = {
                top: `${rect.bottom + 4}px`,
                left: `${rect.left}px`,
                width: `${rect.width}px`,
            };
        },
        async loadPaths() {
            try {
                const response = await fetch('/api/settings/saved_paths');
                if (response.ok) this.paths = (await response.json()).paths;
            } catch (e) {
                // Тихо: список просто пуст, ручной ввод пути не блокируется.
            }
        },
        choose(base) {
            const name = (this.catalogName || '').trim();
            const full = name ? base.replace(/\/+$/, '') + '/' + name : base;
            this.$emit('select', full);
            this.isOpen = false;
        },
        onOutside(event) {
            if (this.isOpen && this.$el && !this.$el.contains(event.target)) {
                this.isOpen = false;
            }
        },
    },
    template: `
        <div class="constructor-item saved-path-trigger" :class="{ open: isOpen }"
             @click.stop="toggle" title="Сохранённые пути">
            <i class="bi bi-chevron-down chevron"></i>
            <div class="options-list" :style="listStyle">
                <div v-if="!paths.length" class="path-combo-empty">Нет сохранённых путей</div>
                <div v-for="p in paths" :key="p.id" class="option" @click.stop="choose(p.path)">{{ p.path }}</div>
            </div>
        </div>
    `,
};
