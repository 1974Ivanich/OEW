# Повторный технический аудит OEW

**Дата:** 20 августа 2026 года  
**Проверенный checkout:** ветка `fix/f1-f7`, HEAD `553e9088419998fc51bda93c2c55e8a93c10d01b`  
**Коммит:** `fix: implement OEW FOC audit package F1-F7`

## 1. Границы и важное расхождение по версии

Аудит выполнен по фактическому дереву `/home/ubuntu/OEW`, а не по текстовому отчёту о коммите `94f7594`. В текущем локальном Git-графе коммит `94f7594` отсутствует: присутствуют `fix/f1-f7 -> 553e908` и `master/origin/master -> 626f847`. Поэтому утверждения о том, что именно `94f7594` был запушен и содержит дополнительные изменения, этим checkout не подтверждаются.

Рабочее дерево чистое. Проверены исходники прошивки, Auto-Tune, FOC, protection, ADC, map capture, PWM/HS-1, GUI, Makefile, документация и hosted/QEMU-тесты.

## 2. Итоговая оценка

В текущем дереве **fail-closed архитектура в целом действительно выдержана**. В частности, normal FOC требует измеренную карту и валидный PWM sample context; `PWM_HardwareInterlockHealthy()` возвращает `false` при default-deny `OEW_HS1_COMMISSIONING_RELEASE=0`; map capture требует compiled profile и аппаратный interlock; а `both_enable()` в Auto-Tune больше не включает мосты напрямую и принудительно завершает попытку с `POWER_BLOCKED` (`src/autotune.c:1475–1484`).

Однако новый аудит выявил несколько реальных проблем вокруг доказуемости тестов, согласованности GUI/прошивки и эксплуатационной документации.

| Приоритет | Находка | Статус |
|---|---|---|
| **P1** | Makefile маскирует падение QEMU через `2>&1 \| tail -3`; `test-qemu` возвращает 0 даже при заведомо неработающем эмуляторе | Подтверждённый дефект инфраструктуры тестов |
| **P1** | GUI имеет fallback `pole_pairs = 4`, тогда как firmware default FOC — 6; неполный `autotune_params.json` может отправить неверное `mp=` | Подтверждённая межслойная несогласованность |
| **P1** | Auto-Tune GUI и руководство описывают энергизированный commissioning как доступный, хотя текущий `both_enable()` намеренно блокирует силовой запуск | Подтверждённый drift документации/UX |
| **P2** | Сборка выдаёт `-Wmaybe-uninitialized` в `autotune.c` для `rr` и `noload`; текущая логика обычно возвращает до использования, но это не доказано компилятором и легко ломается при рефакторинге | Подтверждённый warning, функциональный баг не доказан |
| **P2** | `tests/control_isr_test.c` тестирует абстрактный portable ISR, но не фактический `main.c:ADC1_2_IRQHandler`, включая capture-развилку и seqlock-копирование фрейма | Пробел покрытия |
| **P2** | `make test` зависит от Windows-пути к QEMU; Linux-путь нужно задавать вручную как `make QEMU=/usr/bin/qemu-system-arm test` | Подтверждённый portability-дефект |

## 3. Safety и fail-closed: что подтверждено положительно

### 3.1 Normal FOC

`FOC_Start()` отказывает при fault/clock failure, отсутствии измеренной карты, отсутствии начального валидного контекста, сбое калибровки, невозможности вооружить ADC или отказе PWM enable (`src/foc.c:501–592`). После публикации начального контекста включается control admission, затем вооружается ADC и только после этого вызывается `PWM_Enable()`.

Каждый последующий `FOC_RunFrame()` реконструирует токи из текущего `AdcFrame`; при невалидном фрейме или невозможности выбрать следующий измеренный регион выполняется `FOC_Stop()` без запуска PI-цикла по непроверенным данным (`src/foc.c:609–621`, `src/foc.c:1019–1028`).

### 3.2 Hardware break и центральная остановка

Оба break-handler’а (`TIM1_BRK_TIM15_IRQHandler`, `TIM8_BRK_IRQHandler`) латчат `PROTECT_FAULT_HARDWARE_BREAK` и вызывают `PWM_Disable()` (`main.c:64–84`). `PWM_Disable()` сначала опускает ARM_REQ, затем выключает CEN/MOE/CCER, возвращает CCR в midpoint и останавливает injected ADC (`src/pwm.c:379–395`). Автоматического re-arm после break не обнаружено.

### 3.3 Map capture

`MapCapture_Arm()` и `MapCapture_Run()` требуют активного hook-набора, interlock, отсутствия активных control paths/fault, валидных ADC offsets и approved service pattern (`src/map_capture.c:103–189`). `map_capture_profiles.c` остаётся жёстким fail-closed gate: approved profile и request не выдаются.

Capture специально публикует `ADC_FRAME_WINDOW_INVALID`, не подменяет normal FOC и при terminal condition вызывает единый stop hook, останавливает ADC, снимает expected window/control admission и при необходимости латчит fault (`src/map_capture.c:60–75`, `198–240`). Это соответствует осознанному generic safety-locked дизайну и не является дефектом.

### 3.4 Auto-Tune

Важное подтверждение: энергизированные Auto-Tune-пути не могут включить мосты. `both_enable()` только инвалидирует sample context, устанавливает `g_autotune_abort` и выдаёт `@AT:ERROR:POWER_BLOCKED:no measured OEW sector/window map` (`src/autotune.c:1475–1484`). Поэтому прежний вывод «Auto-Tune обязательно подаст энергию» к текущему дереву неприменим.

## 4. Реальные находки

### P1 — Makefile даёт ложноположительный QEMU-результат

В `Makefile:129–131` команды имеют вид:

```make
$(QEMU) ... 2>&1 | tail -3
```

Без `pipefail` код возврата pipeline равен коду `tail`, а не QEMU. Это подтверждено экспериментом: команда `make QEMU=/bin/false test-qemu` завершилась с `rc=0` и вывела заголовки обоих тестов. Следовательно, сообщение `=== TESTS OK ===` само по себе не доказывает запуск или прохождение QEMU.

Рекомендация: убрать `tail` из критического пути либо включить строгую проверку, например через временный лог и явную проверку `$?`; дополнительно проверять маркер `ALL PASS` в выводе каждого ELF. Также заменить Windows-путь `QEMU = C:/ST/...` на autodetection/override с Linux-совместимым default.

Фактическая ручная проверка текущего дерева с Linux QEMU прошла: FOC — 19/19, V/f — 17/17. Это положительный результат ручного запуска, но не устраняет дефект Makefile.

### P1 — Несогласованный default pole-pairs в GUI

В firmware-side FOC default равен 6, однако `nucleo_debug_tool.py:1371–1375` использует:

```python
pp = p.get('p', 4)
```

И при записи JSON на строках `1383–1388` снова применяется `p.get('p', 4)`. В `foc_control_gui.py` обнаружен аналогичный fallback 4 при загрузке JSON. Если параметры неполные или поле `p` отсутствует, GUI отправит неверное число пар полюсов через `mp=` и сохранит его в JSON.

Рекомендация: убрать независимые числовые defaults из GUI. Лучше сделать поле обязательным и блокировать отправку при отсутствии `p`; альтернативно использовать общий документированный default 6 только после подтверждения, что это действительно board/motor default, а не универсальное значение.

### P1 — GUI и документация обещают недоступный commissioning

`docs/AUTOTUNE_GUI_GUIDE.md:63–75` описывает цепочку `ch → idle → oew → rr → noload → pi → mp` как рабочий порядок измерений. В разделах `11.1–11.2` заявлены ручное применение параметров и Auto-apply после `idle` (`docs/AUTOTUNE_GUI_GUIDE.md:207–217`). Но в firmware текущий `both_enable()` блокирует силовое включение Auto-Tune до появления measured OEW map.

Это не safety-баг: блокировка правильная. Это **операционный дефект** — оператору предлагается нажать кнопки, которые будут приводить к `POWER_BLOCKED`, а документация утверждает соответствие реализации.

Рекомендация: явно разделить режимы `generic safety-locked` и `board-qualified commissioning`; для текущей ветки показывать в GUI disabled/locked состояние и причину, а в руководстве пометить `rr`, `noload`, `oew`, `idle` как reserved до board-qualified map/release. Автоприменение `mp=` не должно создавать впечатление, что силовой контур уже готов к запуску.

### P2 — Compiler warnings в Auto-Tune

`make` проходит, но выдаёт `-Wmaybe-uninitialized` в `src/autotune.c` около строк 1916–1949 и 2109–2156 для `vbus`, `i_offset`, `rr_amp`, сумм и счётчиков. Причина — переходы `goto rr_done`/`goto noload_disable` пересекают объявления, после чего код проверяет `retcode < 0` и обычно возвращает до использования.

На текущих путях это выглядит как analyzer warning, а не доказанный runtime-баг: все ранние ошибки выставляют отрицательный `retcode`, а использование следует после проверки. Но конструкция хрупкая и будет опасной при добавлении нового пути, который перейдёт на cleanup без отрицательного кода.

Рекомендация: поднять все аккумуляторы и значения, которые используются после cleanup-label, в начало функции и инициализировать безопасными значениями; либо вынести расчёт успешного результата в отдельную ветку после явного `if (retcode < 0)`. Затем включить `-Werror=maybe-uninitialized` хотя бы для commissioning/CI-сборки.

### P2 — Не покрыт фактический ADC ISR dispatch

`tests/control_isr_test.c` проверяет portable `ControlISR_Handle()` и полезен для абстрактной проверки порядка protect-before-foc. Но фактический production handler в `main.c:86–112` содержит другую логику: `ADC_InjectedIrq()`, seqlock-копирование `ADC_GetLatestFrame()`, ветку `MapCapture_IsActive()`, `MapCapture_OnAdcFrame()` и FOC-only dispatch.

Поэтому текущий passing test не доказывает корректность настоящей развилки normal-control/capture. Нужен host-test для `main.c`-подобного dispatch либо отдельный тестируемый production helper, покрывающий: invalid frame при FOC, copy failure, active capture, fault-before-FOC и отсутствие FOC на capture frame.

## 5. Проверки, выполненные в этом аудите

| Проверка | Результат |
|---|---|
| `make` | PASS; firmware собран |
| `make QEMU=/usr/bin/qemu-system-arm test` | PASS; hosted и ручной QEMU: FOC 19/19, V/f 17/17, остальные тесты PASS |
| `python3 -m py_compile vf_panel.py nucleo_debug_tool.py` | PASS |
| `git diff --check` | PASS; дерево чистое |
| `make QEMU=/bin/false test-qemu` | Ложно PASS с `rc=0`, тем самым подтверждён дефект Makefile |
| Компилятор | PASS, но с `-Wmaybe-uninitialized` в Auto-Tune |

## 6. Что не следует считать ошибкой

Не следует классифицировать как баги текущую блокировку `both_enable()`, default-deny `OEW_HS1_COMMISSIONING_RELEASE=0`, отсутствие approved map profile и отсутствие pipeline `mapcap → measured map → CurrentMap_LoadMeasured → MAP_READY`. Это сознательные границы generic safety-locked ветки. Их реализация должна появиться только в отдельном board-qualified commissioning-этапе с подтверждённым железом, картой, trigger revision и стендовыми испытаниями.

## 7. Рекомендуемый порядок исправлений

Сначала исправить Makefile, потому что сейчас он снижает доверие ко всем последующим CI-результатам. Затем синхронизировать pole-pairs между firmware и обеими GUI. После этого обновить GUI/documentation под locked состояние и добавить явный commissioning banner. Четвёртым шагом убрать compiler warnings и включить их как ошибки. Наконец, добавить тест фактического production ADC dispatch.

Автор: **Manus AI**
