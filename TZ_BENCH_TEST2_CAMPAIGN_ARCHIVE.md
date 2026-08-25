# ТЗ: offline archive и SHA-256 inventory кампании physical HIL Test №2

## Цель

Создать `tools/bench_test2_campaign_archive.py` для завершения физической кампании Test №2 на ПК-3. Инструмент читает evidence directory, проверяет обязательный инвентарь, рассчитывает SHA-256 каждого файла и выпускает переносимый ZIP с internal hash manifest и отдельный receipt с SHA-256 архива. Он является **offline-only**: не открывает COM, не вызывает ST-Link/sigrok, не прошивает и не управляет DC-link/STEVAL.

## Вход

CLI:

```powershell
py -3 tools\bench_test2_campaign_archive.py `
  --campaign <campaign-directory> `
  --output-dir <sibling-or-other-output-directory>
```

`output-dir` обязателен, не может быть внутри campaign и campaign не может быть внутри output-dir. Исходная campaign всегда read-only для инструмента: запрещены изменения, удаления, переносы или дописывание input evidence.

Обязательные evidence (все regular files):

| Logical evidence | Допустимый путь в campaign |
|---|---|
| G0 approval | `g0_approval.json` |
| G0 build manifest | `diagnostic_build_manifest.json` |
| G0 build log | `diagnostic_build.log` |
| G0 summary | `g0_check_summary.json` **или** `g0/g0_check_summary.json` (ровно один) |
| Execution metadata | `metadata.json` |
| Execution summary | `summary.json` |
| Continuous UART | `uart.log` |
| Actual sigrok CSV | `sigrok_digital.csv` |
| sigrok stdout | `sigrok_stdout.log` |
| sigrok stderr | `sigrok_stderr.log` |

Physical Test №2 may have an automation/scope/final FAIL; its evidence must still be archived. Однако G0 summary должен иметь `gate=HIL_TEST2_G0` и `verdict=PASS`. При G0 invalid/FAIL инструмент всё равно формирует recovery archive для расследования, но возвращает FAIL/exit 2 и не выдаёт accepted archive receipt.

## Выход

Для campaign `<id>` выпускать в output-dir:

| Файл | Назначение |
|---|---|
| `<id>.evidence.zip` | Deterministic ZIP: все regular files кампании, включая non-required, и `campaign_manifest.json`; input files не меняются. |
| `<id>.archive_receipt.json` | Внешний receipt: SHA-256 ZIP, SHA-256 internal manifest, tree digest, checks и verdict. |

Internal `campaign_manifest.json` должен содержать schema, campaign identifier, лексикографически отсортированный inventory (`path`, `size_bytes`, `sha256`) и обязательные logical paths. Archive не должен содержать абсолютных путей, symlinks, собственного output или timestamp-зависимого manifest content. ZIP names и timestamp должны быть canonical/fixed, порядок файлов lexical, поэтому одинаковые input bytes дают одинаковый archive bytes.

Receipt содержит отдельное время создания, archive SHA-256 и `ARCHIVE=PASS|FAIL`. `ARCHIVE=PASS`/exit 0 возможен только если: все required evidence присутствуют; нет path traversal/symlink; каждый source file прочитан и hashed; g0 summary корректен и PASS; ZIP self-verification подтверждает manifest/file hashes и archive SHA-256. Любое отклонение: `ARCHIVE=FAIL`, exit 2; инструмент пытается сохранить recovery archive/receipt с failed checks, если это возможно.

`--verify --archive <zip> --receipt <receipt>` проверяет archive receipt SHA, internal manifest, canonical member names и SHA-256 всех evidence members. Он ничего не извлекает на диск и возвращает 0 только при PASS.

## Защитные свойства

1. Никакого verdict Test №2 не повышается: архивация доказывает сохранность evidence, не физический PASS.
2. G0 PASS обязателен для accepted archive, но evidence failure кампании сохраняется.
3. Symlink, absolute path, `..`, duplicate zip member и output nesting считаются FAIL.
4. В archive не должны попадать исходные output ZIP/receipt и инструменты нельзя запускать с output внутри campaign.
5. Receipt/manifest используют SHA-256 только для integrity; это не криптографическая подпись человека или safety approval.

## Тесты

Добавить pytest fixture кампании и deterministic проверки: complete campaign PASS; same input => identical ZIP hash; physical `automation=FAIL` remains archive PASS when G0 PASS; missing each required logical artifact; ambiguous G0 summary; malformed/non-PASS G0; symlink; output nesting; source change after archive detected by verify; tampered ZIP/receipt detected; empty/non-regular input handling. Добавить operator documentation, примеры CLI и recovery semantics.

## Приёмка

Выполнить `py_compile`, новые pytest, существующие Test №2 parser/simulation/G0 tests, `make` и `make test`. `make flash` не запускать: инструмент offline-only и не имеет права обращаться к hardware. Перед публикацией: diff check, rebase, push и ls-remote; merge только при зелёном CI.
