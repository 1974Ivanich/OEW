# Test № 2: признаки PASS на sigrok и безопасная интеграция ПК‑3 в CI/CD

## 1. Что логический анализатор должен доказать

Логический анализатор контролирует **цифровой PWM-путь**. Он не заменяет UART, DMM, проверку SD и просмотр регистра MOE: эти источники доказательств отвечают на разные вопросы.

Synthetic request `SYNT` использует `CCR1..3=500` на обоих таймерах, `ARR=999`, 5 кГц PWM и 1,5 мкс dead-time. При PC4=0 test № 2 должен перейти в terminal fail-closed после первого непригодного ADC/VBUS кадра, поэтому значение `pulse_count=16` — только верхняя граница request, **а не ожидаемая длина trace**. [1] [2]

| Группа сигрок-каналов | Физические сигналы | Признак PASS |
|---|---|---|
| D0/D1/D4 | HIN U1/V1/W1 (TIM1) | Короткий синхронный burst 5 кГц: период около 200 мкс, duty близок к 50 %. Все три канала должны иметь совпадающие фронты, поскольку их CCR одинаковы. |
| D2/D3/D5 | LIN U1/V1/W1 (TIM1 CHxN) | Комплементарны соответствующим HIN: ни на одной фазе не должно быть перекрытия HIN/LIN. Dead-time около 1,5 мкс виден примерно как 12 отсчётов при 8 МГц. |
| D9/D8/D10 | HIN U2/V2/W2 (TIM8) | Короткий 5-кГц burst, синхронный внутри U/V/W. Из-за PWM mode 2 TIM8 high-side должен быть противофазен соответствующему TIM1 high-side. |
| D7/D6/D11 | LIN U2/V2/W2 (TIM8 CHxN) | Комплементарны HIN U2/V2/W2 без overlap. |
| Все D0…D11 | Все 12 ШИМ-линий | После последнего фронта — отсутствие новых переключений на всём post-trigger окне. Практический минимум доказательства: не менее 20 мс тишины, то есть 100 периодов по 200 мкс. |

> Канальная карта: D0 PC0 HIN_U1, D1 PC1 HIN_V1, D2 PA7 LIN_U1, D3 PB0 LIN_V1, D4 PC2 HIN_W1, D5 PB1 LIN_W1, D6 PC11 LIN_V2, D7 PC10 LIN_U2, D8 PC7 HIN_V2, D9 PC6 HIN_U2, D10 PC8 HIN_W2, D11 PC12 LIN_W2. [3]

### Формальные критерии trace

| Проверка | PASS | FAIL / STOP |
|---|---|---|
| До `mapcap run` | Нет периодического PWM на всех D0…D11 | Любой PWM до команды run |
| После `@MC:RUN:rc=0` | Наблюдается короткий bounded burst | Нет burst при `rc=0`, либо непрерывный PWM |
| Частота и duty | Около 5 кГц / 200 мкс; примерно 50 % при CCR=500 | Иная частота, duty существенно отклонён, U/V/W не синхронны |
| Внутри каждого плеча | HIN и LIN комплементарны, overlap отсутствует; dead-time порядка 1,5 мкс | HIN и LIN одновременно active |
| Между инверторами | HIN_U1↔HIN_U2, HIN_V1↔HIN_V2, HIN_W1↔HIN_W2 противофазны | Синфазная коммутация соответствующей пары |
| Завершение | После первого недопустимого VBUS/ADC кадра нет новых фронтов; UART выдаёт `term=-11` или `-12`, `records=0` | `term=0`, `state=COMPLETE`, `records>0`, новый PWM после terminal state |

В коде terminal stop снимает `CEN` и `MOE`, очищает `CCER`, возвращает CCR к середине и останавливает injected ADC. Логический анализатор подтверждает отсутствие дальнейших фронтов, но **не может достоверно доказать состояние high-Z/MOE**: это обязательно проверяется совместно с UART `p?`/`pdump`. [2]

## 2. Что сейчас делает автоматический скрипт

`tools/bench_test2_capture.py` уже сохраняет CSV D0…D11 и требует корректные UART-признаки: `arm/run rc=0`, `state=5`, `term=-11/-12`, нулевые frames/records и отсутствие `@MC:REC`. Он также требует успешное завершение sigrok-cli и наличие CSV.

Скрипт **пока не выносит окончательный вердикт по форме waveforms**. Это намеренное ограничение: PASS должен дополнительно подтверждаться человеком по CSV/trace до тех пор, пока не будет добавлен отдельный, протестированный анализатор фронтов. Следующее безопасное улучшение — добавить к скрипту pure-function `analyze_pwm_csv()` и unit-тесты на fixture-CSV, проверяющие период, фазировку, dead-time и post-terminal quiet window.

## 3. Два жизнеспособных варианта запуска

| Подход | Компромиссы | Стоимость | Сложность подготовки |
|---|---|---:|---:|
| **Локальный ручной запуск на ПК‑3** | Оператор вручную проходит DMM/SD preflight, затем запускает скрипт и проверяет trace. Максимальная физическая осознанность, минимальная инфраструктура. | Нет дополнительной | Низкая |
| **Защищённый hardware job GitHub Actions на ПК‑3** | Даёт историю запусков, артефакты и обязательное подтверждение; но ПК‑3 становится постоянным self-hosted runner, который требует отдельного обслуживания и строгого контроля того, какой код может попасть на стенд. | GitHub-hosted минуты не требуются для hardware job; поддержка ПК‑3 остаётся за командой. [4] | Средняя/высокая |

Для первого реального no-HV прогона предпочтительнее ручной запуск: он проверяет кабели, COM, sigrok и фактический trace без дополнительной CI-инфраструктуры. После хотя бы одного воспроизводимого PASS можно внедрять защищённый hardware job.

## 4. Рекомендуемая архитектура CI/CD

Текущий `build-test` уже запускается на GitHub-hosted `ubuntu-latest`: production build, hosted/QEMU/pytest, synthetic host-only map capture tests и commissioning build. Его нельзя заменять стендом: normal CI остаётся быстрым и не имеет доступа к физическому железу. [5]

Добавляется **отдельный workflow**, например `.github/workflows/bench-pc3-nohv.yml`, со следующими правилами.

1. **Только ручной запуск** через `workflow_dispatch`; никакого запуска на `push`, `pull_request`, cron или webhook.
2. **Выделенный repository-level self-hosted runner ПК‑3** с labels: `self-hosted`, `windows`, `x64`, `pc3`, `motor-bench-nohv`. Labels GitHub сопоставляет кумулятивно, поэтому обычный CI никогда не должен использовать этот набор. [6]
3. **GitHub Environment `pc3-nohv`**: required reviewer, запрет self-review, по возможности запрет admin bypass; ограничение deploy branches на `main` (или отдельно согласованный защищённый pattern). Environment rules проверяются до выполнения job и до выдачи его secrets. [7]
4. **Concurrency group** `pc3-nohv-bench` с `cancel-in-progress: false`, чтобы две задачи никогда не обращались к COM/sigrok одновременно.
5. **Никаких управляемых источников питания** в runner. DC-link остаётся физически снятым и проверяется DMM оператором; GitHub inputs — только аттестация, не доказательство физической безопасности.
6. **Отдельная учётная запись Windows для runner**; runner не должен иметь прав администратора, доступа к токовым источникам, SSH-ключей для main или иных секретов, кроме token, необходимого самому GitHub runner.
7. Job всегда выгружает `uart.log`, `summary.json`, CSV sigrok и stderr/stdout sigrok как Actions artifacts, включая случай FAIL.

### Каркас workflow

```yaml
name: bench-pc3-nohv

on:
  workflow_dispatch:
    inputs:
      git_ref:
        description: "Только approved main SHA/tag"
        required: true
        type: string
      dc_link_disconnected:
        description: "DMM подтвердил обе шины <1 V и DC-link отсоединён"
        required: true
        default: false
        type: boolean
      pc4_zero:
        description: "PC4 без внешнего VBUS/имитатора"
        required: true
        default: false
        type: boolean
      sd_high:
        description: "SD1 и SD2 проверены high"
        required: true
        default: false
        type: boolean

jobs:
  bench-test2:
    if: ${{ inputs.dc_link_disconnected && inputs.pc4_zero && inputs.sd_high }}
    runs-on: [self-hosted, windows, x64, pc3, motor-bench-nohv]
    environment: pc3-nohv
    concurrency:
      group: pc3-nohv-bench
      cancel-in-progress: false
    timeout-minutes: 20
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ inputs.git_ref }}
          clean: true
      - name: Verify hardware tools
        shell: pwsh
        run: |
          py -3 --version
          py -3 tools\bench_test2_capture.py --scan-sigrok
          py -3 tools\bench_test2_capture.py --list-ports
      - name: Build and flash temporary test-2 image
        shell: pwsh
        run: |
          make clean
          make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1"
          make flash
      - name: Run no-HV test 2
        shell: pwsh
        run: |
          py -3 -m pip install pyserial
          py -3 tools\bench_test2_capture.py `
            --port COM4 `
            --confirm-dc-link-disconnected `
            --confirm-pc4-zero `
            --confirm-sd-high
      - name: Upload bench evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pc3-test2-nohv-${{ github.run_id }}
          path: campaign_raw/test2_nohv_*/
          if-no-files-found: warn
      - name: Restore generic default-deny image
        if: always()
        shell: pwsh
        run: |
          make clean
          make
          make flash
```

`COM4` нельзя оставлять жёстко заданным в долгосрочной версии. Лучше сделать его environment variable `BENCH_COM_PORT` в защищённом environment или передавать как ручной input, который оператор сверяет с `--list-ports` до фактического запуска.

## 5. Условия, прежде чем включать hardware job

| Гейт | Обязательное доказательство |
|---|---|
| Ручной PASS | Хотя бы один полностью документированный no-HV PASS: UART, DMM, CSV/trace, protocol. |
| CSV-анализатор | Автоматическая проверка waveform реализована и имеет unit-test fixtures; до этого human trace review остаётся обязательным. |
| Runner isolation | ПК‑3 выделен только bench job, доступ к работе с PR/непроверенными ветками закрыт. |
| Environment approval | Настроен `pc3-nohv`, review выполняет не инициатор запуска; branch policy не допускает произвольные ветки. [7] |
| Recovery | `if: always()` восстанавливает generic default-deny образ; операционная процедура покрывает зависший runner/COM/sigrok. |
| Evidence retention | Artifacts сохраняются на срок, достаточный для приёмки и расследования. |

## References

[1]: https://github.com/1974Ivanich/OEW/blob/main/src/map_capture_profiles.c
[2]: https://github.com/1974Ivanich/OEW/blob/main/src/pwm.c
[3]: https://github.com/1974Ivanich/OEW/blob/main/PROJECT_OVERVIEW.md
[4]: https://docs.github.com/actions/hosting-your-own-runners
[5]: https://github.com/1974Ivanich/OEW/blob/main/.github/workflows/ci.yml
[6]: https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow
[7]: https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment
