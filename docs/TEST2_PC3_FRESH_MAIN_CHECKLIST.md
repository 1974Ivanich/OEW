# ПК-3: чек-лист физического прогона со свежим `main`

## 1. Назначение и выбор разрешённого пути

Этот чек-лист предназначен для оператора ПК-3. Он не отменяет локальную процедуру безопасности стенда, G0 approval, pre-flight или ограничения firmware. Агент не выполняет эти физические действия.

Сначала выберите **один** путь и не смешивайте их в одном campaign folder.

| Путь | Цель | Командная граница | Условие начала |
|---|---|---|---|
| **A — Test №2 ADC baseline** | Подтвердить default-deny, calibration и ADC/measurement-chain при no-HV. | Только `sysinfo`, `p?`, `pdump`, `c`, десять `a`; никаких MapCapture/FOC/V/f. | Полный safe harness, физически отключённый DC-link и verified default-deny image. |
| **B — Test №3 controlled no-HV MapCapture** | Повторить controlled terminal `VBUS_LOW` после VBUS saturation fix и собрать UART/sigrok evidence. | Только approved pre-flight + `bench_test2_capture.py`; его legacy имя не меняет классификацию Test №3. | Все условия пути A **и** exact Test №3 no-HV G0/pre-flight evidence PASS. |

> Если `g0_approval.json` для Test №3 остаётся `PENDING`, путь B **запрещён**. Разрешён только путь A либо offline preparation; не выполняйте `mcarm`, `mapcap run`, `mapcap drain`, `f`, FOC, V/f, autotune или любой DC-link action.

## 2. Подготовка исходного состояния

| ID | Действие оператора | PASS evidence | STOP/BLOCKED condition |
|---|---|---|---|
| C-01 | Закройте приложения, которые могут владеть COM/ST-Link/sigrok. Проверьте clean tracked Git worktree либо зафиксируйте сторонние локальные изменения вне данной кампании. | Короткая запись состояния worktree. | Неясная source base или чужие незаписанные изменения. |
| C-02 | Получите свежий remote state: `git fetch origin`, затем на предназначенной для стенда копии `git switch main` и `git pull --ff-only`. Запишите `git rev-parse origin/main`. | Exact source SHA и UTC записаны в campaign. | Локальный main не fast-forward, нет сети или SHA не записан. |
| C-03 | Проверьте зелёный CI exact source SHA и используйте project-approved CI firmware artifact/provenance согласно workflow. | CI run URL/ID, firmware SHA-256 и source SHA. | Красный/отсутствующий CI, local-only binary без provenance или SHA mismatch. |
| C-04 | Создайте новый каталог **вне Git**: `D:\campaign_raw\test2_adc_<UTC>` для пути A либо `D:\campaign_raw\test3_nohv_<UTC>` для пути B. | Папка существует до UART/flash; source/build/flash/DMM/UART paths определены. | Папка лежит в repository, reused from earlier run или смешивает два пути. |
| C-05 | Начните непрерывный журнал: время, оператор, source/image identity, build/flash verification, DMM values, physical observations и все UART TX/RX. | Непрерывный log без вырезанных failed lines. | Копирование только удачных строк или потеря boot/reset evidence. |

## 3. Общие hard gates перед прошивкой и UART

Ни одна операция прошивки и никакая UART-команда не начинаются, пока все строки не имеют физического PASS и инициалы оператора.

| ID | Gate | PASS criterion | Evidence |
|---|---|---|---|
| H-01 | DC-link boundary | Обе DC-link rails физически отсоединены и каждая измерена DMM `<1 V`. | Два значения, DMM ID, UTC, оператор. |
| H-02 | PC4 boundary | Нет внешнего источника/имитатора VBUS на PC4. | Wiring observation/фото по локальной процедуре. |
| H-03 | Low-voltage path | Разрешённое отдельное aux питание, полный штатный STEVAL/sensor harness и подтверждённая полярность. | Supply identity/reading и harness inspection. |
| H-04 | Default safe state | SD1/SD2 и emergency power-removal action подтверждены по локальной процедуре. | Observation и operator initials. |
| H-05 | Identity | Target Nucleo/ST-Link identity и MCU UART VCP идентифицированы; COM number не угадан. | Programmer/OS observation и later `sysinfo`. |
| H-06 | Capture readiness | Для пути B sigrok подключён, probes/ground safe, `--scan-sigrok` может быть выполнен только внутри approved physical pre-flight. | Pre-flight output; не заменять UART-only evidence. |
| H-07 | Scope attestation | Оператор подтвердил выбранный path и запрет всех команд другого path. | Campaign worksheet. |

При FAIL или сомнении остановитесь, сохраните evidence и не заменяйте физический факт simulation/предположением.

## 4. Путь A — Test №2 ADC/measurement-chain baseline

После H-01…H-07 используйте только approved default-deny baseline image. Физическая прошивка выполняется оператором только по утверждённой локальной programming procedure; сохраните original build/flash verify output. Никакой temporary Test №3 diagnostic configuration не применяется для пути A.

| Шаг | Неразгоняющее наблюдение | Expected evidence | Stop condition |
|---:|---|---|---|
| A1 | После verified reset начните capture actual MCU UART VCP и отправьте `sysinfo`. | Свежий, ожидаемый ответ target firmware. | Empty/stale/unknown response. |
| A2 | Отправьте `p?`, затем `pdump`. | PWM disabled: `MOE=0`/default-deny evidence; нет неожиданной активности. | Любая неясность/active PWM marker. |
| A3 | Отправьте `c` один раз. | Все `offset_i1`, `offset_i2`, `offset_ires`; нет `@ADC:CAL:FAIL`. | Missing/fail/timeout. |
| A4 | Отправьте `a` десять раз в стабильных no-HV условиях, сохраняя каждый полный ответ. | I1/I2/Ires/VBUS fields во всех 10 samples; raw VBUS observation `≤9`; current rails/saturation отсутствуют. | Missing/malformed sample, VBUS above baseline, rail/saturation или unexplained jump. |
| A5 | Остановите сессию на baseline evidence. | Command audit содержит только `sysinfo`, `p?`, `pdump`, `c`, `a`. | Любая energising/control команда — Test №2 FAIL, evidence сохранить. |

Сведите min/max/mean/peak-to-peak raw readings и сохраните DMM/wiring evidence. Test №2 PASS не заполняет RealBoardProfile и не допускает Test №3 или Stage A автоматически. [1]

## 5. Путь B — Test №3 controlled no-HV MapCapture повтор

Этот путь возможен только после положительного решения всех ниже; `PENDING` равно **NO-GO**.

| ID | Prerequisite | Required PASS evidence |
|---|---|---|
| B-01 | Test №2 baseline accepted | Closed report/archived evidence или явное safety-owner решение об approved prerequisite. |
| B-02 | Test №3 G0 | Exact Test №3 `g0_approval.json` approved for the exact source/image and no-HV-only scope; legacy Test №2 validator не подменяет Test №3 approval. |
| B-03 | Physical pre-flight | `preflight_summary.json` with `PREFLIGHT=PASS`, including actual MCU UART identity, PWM-disabled proof, statistical raw VBUS gate, calibration, encoder, IDLE status and sigrok discovery. [2] |
| B-04 | Campaign provenance | Fresh campaign directory, source/image/firmware SHA chain, build/flash verification, DMM facts and continuous UART capture prepared. |
| B-05 | Scope acknowledgement | Operator accepts diagnostic no-HV-only boundary: DC-link remains physically disconnected; no Stage A, FOC, V/f, autotune, build or fault-clear outside approved procedure. |

После B-01…B-05 запустите только отдельно утверждённую physical no-HV automation с actual MCU UART VCP и всеми четырьмя physical confirmations. Используйте новый campaign path explicitly, например:

```powershell
py -3 tools\bench_test2_capture.py `
  --port COM<actual_MCU_VCP> `
  --output-dir D:\campaign_raw\test3_nohv_<UTC> `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

Скрипт должен запустить sigrok до `mapcap run`, дойти до terminal state по absolute deadline и оставить fault latch для review; не добавляйте `--clear-fault-after-evidence` в обычный повторный прогон. [3]

Ожидаемый повтор после VBUS saturation fix — **стабильный** terminal `state=5`, `term=-12`, `detail=7 (VBUS_LOW)`, `adc_status=7 (WINDOW_INVALID)`, zero records и raw VBUS согласно statistical contract. `term=-11`/`ADC_SATURATED`, другие details, timeout, missing CSV или records>0 — FAIL, а не «частичный PASS».

## 6. Post-run evidence и offline verdict

Не редактируя `summary.json` или `uart.log`, выполните на отключённом от управления workstation/offline session:

```powershell
py -3 tools\bench_test2_rerun_verdict.py `
  --campaign D:\campaign_raw\test3_nohv_<UTC>
```

| Результат | Значение | Дальнейшее действие |
|---|---|---|
| `TERMINAL_VERDICT=PASS` | Summary и непрерывный UART log согласованно показывают expected physical terminal no-HV evidence. | Провести manual sigrok scope review, archive/hash campaign и подготовить Test №3 report. `stage_a_60v` остаётся `BLOCKED`. |
| `TERMINAL_VERDICT=FAIL` | Хотя бы один evidence item отсутствует, симулирован, не соответствует terminal contract или расходится между файлами. | Stop; сохранить original files, не повторять/не очищать fault до review. |

Для фиксации evidence после manual review используйте утверждённый archive/hash workflow. `rerun_terminal_verdict.json` дополняет campaign; он не заменяет `summary.json`, sigrok CSV, G0, pre-flight или human scope acceptance.

## 7. Переход к 60 V

После успешных A и B не включайте DC-link. Вернитесь к отдельному документу `docs/TEST3_NOHV_TO_STAGE_A_60V_CRITERIA.md`: все A60-01…A60-10 должны быть approved в отдельном `HIL_STAGE_A_60V` decision record. До этого статусы **`Stage A = BLOCKED`**, **`DC-link = DISCONNECTED`** и **`FOC/V/f/autotune = FORBIDDEN`** сохраняются.

## References

[1]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md` — Test №2 baseline scope, command boundary и acceptance.

[2]: `tools/bench_test2_preflight.md` — fail-closed physical pre-flight.

[3]: `tools/bench_test2_capture.md` — controlled no-HV capture, terminal contract и sigrok evidence boundary.

[4]: `docs/TEST3_NOHV_TO_STAGE_A_60V_CRITERIA.md` — independent criteria for a separate 60 V approval.
