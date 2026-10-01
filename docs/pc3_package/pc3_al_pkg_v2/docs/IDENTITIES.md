# Идентичности пакета

frozen scan:
    source_commit = ab1274d
    binary_sha256 = d2bc0439...   (образ №8, 88 020 Б)
    НЕ изменяется этим пакетом

al:
    source_commit = 4507983
    binary_sha256 = <реальный SHA из SHA256SUMS>
    physical qualification = NOT_PASSED

Полный SHA source commit 4507983 = git rev-parse HEAD после checkout 4507983.

docs:
    revision = 2 (сверено с кодом 4507983; подробности — CORRECTIONS_v2.md)
    правка docs НЕ меняет ни одну из двух идентичностей выше:
    прошивка не пересобирается, binary_sha256 остаётся тем же

0fba575 — отдельное docs-only изменение поверх 4507983 на ветке
(origin/ai2/shunt-cal-campaign-v1). Оно не входит в этот пакет и этим пакетом
не переоткрывается.

Атрибуция данных в логах:
- [scan] — снято на binary d2bc0439... (frozen scan)
- [alq]  — снято на binary SHA из SHA256SUMS этого пакета
Строка без префикса к анализу не принимается.

Какой именно binary был на плате в каждый момент прогона — фиксируется
в логе явно (flash и снятие питания записываются строками с префиксом).
