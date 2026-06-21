"""Корневой conftest: изоляция логирования тестов.

`core.logging.LOG_DIR` читается из ST_LOG_DIR при импорте модуля, а
`get_logger()` → `configure()` навешивает RotatingFileHandler на этот
каталог. Без изоляции тесты (включая fake-модули: fake_backend, probe и
т.п.) писали бы в боевой `logs/`, засоряя реальные журналы и динамический
фильтр групп. Задаём временный каталог ДО первого импорта core.logging —
здесь, на самом верху корневого conftest, который pytest загружает раньше
тестовых модулей. setdefault — чтобы внешний ST_LOG_DIR (CI) имел приоритет.
"""
import os
import tempfile

os.environ.setdefault(
    "ST_LOG_DIR", tempfile.mkdtemp(prefix="st-test-logs-"))
