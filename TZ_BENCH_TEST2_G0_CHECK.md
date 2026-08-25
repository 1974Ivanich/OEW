# ТЗ: offline validator Hard Gate G0 для physical HIL Test №2

## Цель

Создать отдельный fail-closed Python-валидатор, который проверяет подготовленные **офлайн** артефакты Hard Gate G0 перед physical no-HV HIL Test №2. Валидатор не управляет MCU, не открывает COM-порт, не запускает sigrok, не прошивает и не обращается к ST-Link, STEVAL, DC-link или любому USB-оборудованию.

## Область проверки

В каталоге кампании валидатор требует ровно следующие входные доказательства:

| Артефакт | Роль |
|---|---|
| `g0_approval.json` | Явное документированное решение safety-owner для physical **no-HV diagnostic-only** build. |
| `diagnostic_build_manifest.json` | Полный набор preprocessor defines, SHA исходного кода, идентичность артефакта и метаданные diagnostic build. |
| `diagnostic_build.log` | Build evidence, содержащее все четыре required diagnostic defines. |
| Binary из manifest | Существует внутри каталога кампании и соответствует SHA-256 из manifest. |

## Strict contract

Automation PASS возможен только при одновременном выполнении всех пунктов:

1. `g0_approval.json` имеет `gate=HIL_TEST2_G0`, `decision=APPROVED`, роль `safety-owner`, непустые `approval_id`, approver name и time. Scope явно разрешает **physical no-HV execution** именно данного binary: `target=physical-nohv-diagnostic`, `test=MAPCAP_TEST2`, `allows_oew_host_test=true`, `allows_physical_nohv_execution=true`, `diagnostic_only=true`, `forbids_stage_a=true`, `forbids_production=true`, `forbids_dc_link=true`, `forbids_foc=true`, `forbids_vf=true`, `forbids_autotune=true`.
2. SHA исходного кода в approval и manifest — одинаковые 40-hex SHA; SHA-256 firmware в approval, manifest и actual binary — одинаковые 64-hex значения. Таким образом G0 привязан к source SHA и конкретному firmware image, а не только к конфигурации сборки.
3. Manifest имеет `defines_complete=true` и содержит целевые значения required defines: `OEW_MAP_CAPTURE=1`, `OEW_MAP_L3=1`, `PWM_OEW_BOARD_REVISION=7`, `OEW_MAP_SYNTHETIC_PROFILE=1`, `OEW_HOST_TEST=1`.
4. Binary path не выходит за корень каталога кампании, файл существует и его SHA-256 совпадает с manifest и approval.
5. `diagnostic_build.log` существует и содержит exact required `-DNAME=VALUE` tokens для шести required defines.
6. В summary нет FAIL. Любая пустота, неизвестное/лишнее критическое значение, повреждённый JSON, path traversal, hash mismatch или отклонённое разрешение — FAIL. Exit 0 валидатора является обязательным **pre-flash evidence** в протоколе; сам script не перехватывает Makefile, поэтому enforce-блокировку `make flash` можно реализовать только отдельным явно согласованным пакетом Makefile/flash integration.

## Выход

CLI принимает `--campaign <directory>`, пишет `g0_check_summary.json`, печатает `HARD_GATE_G0=PASS` или `HARD_GATE_G0=FAIL` и возвращает `0` только при PASS, иначе `2`.

Summary обязан содержать machine-readable `gate`, `verdict`, полный список checks (`id`, `result`, `expected`, `actual`, `detail`) и пути входных evidence. Не разрешается считать результат криптографической проверкой личности подписанта или доказательством фактически прошитой цели: validator проверяет полноту и согласованность указанных офлайн полей; человеческое утверждение safety-owner и post-flash chain `flash verification → sysinfo → UART log` остаются отдельным процессом отчёта.

## Тесты

Добавить deterministic pytest coverage как минимум для: полного PASS; missing/REJECTED approval; неверного scope; SHA mismatch; missing/invalid/incomplete defines; отсутствующего или некорректного build-log; path traversal; отсутствующего binary; SHA-256 mismatch; malformed JSON. Добавить операторскую документацию с schema/examples и явной границей no-hardware.

## Приёмка

Выполнить `py_compile`, новые pytest, регрессию Test №2, `make` и `make test`. Не выполнять `make flash`: пакет является offline-validator и не имеет права обращаться к физическому оборудованию. Перед публикацией выполнить diff check, rebase, push и ls-remote; CI должен быть зелёным перед merge.
