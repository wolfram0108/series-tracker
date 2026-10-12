"""tracker_session_account — вход на трекер привязан к учётным данным.

Сессия действительна только для той учётной записи, под которой получена.
Без этой привязки смена логина в настройках оставалась незамеченной:
прежние куки восстанавливались из БД, сессия была жива, протухания не
было — релогин не запускался, и запросы продолжали идти под прежним
аккаунтом (его суточные лимиты и его права).

tracker_sessions.account_fingerprint — отпечаток учётных данных входа.
У существующих записей NULL: они считаются полученными под неизвестной
учёткой, поэтому первый же запрос выполняет вход под той, что в настройках.

Пробелы по краям логинов срезаются: они уходили в форму входа как есть.
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tracker_sessions "
               "ADD COLUMN account_fingerprint TEXT")
    op.execute("UPDATE auth SET username=TRIM(username) "
               "WHERE username <> TRIM(username)")


def downgrade() -> None:
    op.execute("ALTER TABLE tracker_sessions "
               "DROP COLUMN account_fingerprint")
