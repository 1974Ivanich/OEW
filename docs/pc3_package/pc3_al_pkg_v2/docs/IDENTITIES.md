# Идентичности пакета

frozen scan:
    source_commit = ab1274d
    binary_sha256 = d2bc0439...   (образ №8, 88 020 Б)
    НЕ изменяется этим пакетом

al:
    source_commit = 4507983
    source_commit_full = 45079832cc48a4593f77d2e41a93edb1ef702724
    binary_sha256 = 351dbbb1aeadb900d00bf7b06926c4c11cdcb4619fff6c49a28232e5c3b63158
                    (firmware/pc3_foc_4507983.bin, 90 828 Б)
    artifact = firmware-commissioning, CI run #733 (run id 36843085378,
               artifact id 11152755673, head_sha 4507983, conclusion success)
    provenance = firmware/ci733/diagnostic_build_manifest.json
                 (source_sha совпал; defines — те же шесть; внутренний
                  firmware.sha256 = binary_sha256 выше)
    physical qualification = NOT_PASSED

Полный SHA source commit 4507983 = git rev-parse HEAD после checkout 4507983
= 45079832cc48a4593f77d2e41a93edb1ef702724.

Бинарники в git не хранятся (.gitignore: *.bin/*.elf/*.map) — в репозитории
только SHA-256 (SHA256SUMS) и provenance; сам образ переносится как CI-артефакт
и лежит в firmware/ (локально, вне git).

0fba575 — отдельное docs-only изменение поверх 4507983 на ветке
(origin/ai2/shunt-cal-campaign-v1). Оно не входит в этот пакет и этим пакетом
не переоткрывается.

Атрибуция данных в логах:
- [scan] — снято на binary d2bc0439... (frozen scan)
- [alq]  — снято на binary SHA из SHA256SUMS этого пакета
Строка без префикса к анализу не принимается.

Какой именно binary был на плате в каждый момент прогона — фиксируется
в логе явно (flash и снятие питания записываются строками с префиксом).
