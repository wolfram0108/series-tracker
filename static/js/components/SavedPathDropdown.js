// static/js/components/SavedPathDropdown.js
//
// Кастомный выпадающий со списком сохранённых путей (Настройки →
// Отладка → «Сохранённые пути»). Аддитивный: НЕ заменяет поле ввода
// пути (у того своя обработка слэшей/валидация), а лишь подставляет
// выбранное значение через событие select.
//
// Если задан catalogName (готовое имя каталога «Имя (год) [tmdbid-XXXX]»),
// он дописывается в конец выбранного пути через один слэш.
//
// Логика написана с нуля: позиционирование списка — обычным absolute
// в относительном контейнере (без ручного расчёта координат, в отличие
// от ConstructorItemSelect); переиспользуется только CSS-класс .option.

const SavedPathDropdown = {
    props: {
        // Имя каталога для дописывания в конец пути (необязательно).
        catalogName: { type: String, default: '' },
    },
    emits: ['select'],
    data() {
        return { isOpen: false, paths: [] };
    },
    mounted() {
        document.addEventListener('click', this.onOutside, true);
    },
    beforeUnmount() {
        document.removeEventListener('click', this.onOutside, true);
    },
    methods: {
        async toggle() {
            if (!this.isOpen) await this.loadPaths();
            this.isOpen = !this.isOpen;
        },
        async loadPaths() {
            try {
                const response = await fetch('/api/settings/saved_paths');
                if (response.ok) this.paths = (await response.json()).paths;
            } catch (e) {
                // Тихо: при сбое список просто пуст, ввод пути руками не блокируется.
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
        <div class="saved-path-dropdown">
            <button type="button"
                    class="btn btn-sm btn-outline-secondary w-100 d-flex justify-content-between align-items-center"
                    :class="{ open: isOpen }" @click.stop="toggle">
                <span><i class="bi bi-folder2-open me-1"></i>Сохранённые пути</span>
                <i class="bi bi-chevron-down chevron"></i>
            </button>
            <div v-if="isOpen" class="path-combo-list">
                <div v-if="!paths.length" class="path-combo-empty">Нет сохранённых путей</div>
                <div v-for="p in paths" :key="p.id" class="option" @click.stop="choose(p.path)">{{ p.path }}</div>
            </div>
        </div>
    `,
};
