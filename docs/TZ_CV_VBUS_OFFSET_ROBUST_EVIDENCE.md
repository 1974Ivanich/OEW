# Приложение к ТЗ `TZ_CV_VBUS_OFFSET_ROBUST.md`: сырые данные прогонов ПК-3

Назначение: сделать числа §1 ТЗ **проверяемыми** приёмщиком (ПК-1/ПК-2), не полагаясь
на пересказ. Это приложение — не ТЗ и не расширяет область пакета: код в области
пакета не меняется, файлы касаются только доказательной базы.

Стенд: ПК-3, порт COM4, ST-Link; дата: 02.10.2026; команды — только read-only/`PWM OFF`
(`a`, `cv`, `c`, `ci 0/1/2`, `sysinfo`, `dump`, `dump8`, `breakdiag`). Пути в выдержках
санитизированы: `C:\Users\<user>\Documents\GitHub\OEW\` → `<repo>\` (в исходных логах
стоит локальный путь с именем пользователя).

## 1. Артефакты и их sha256 (проверяемый список)

| Артефакт | Где лежит | sha256 | Что доказывает |
|---|---|---|---|
| `inject_log.txt` (прогон 1, гейт **ПРОШЁЛ**) | ПК-3: `C:\campaign_raw\pc3_foc_bench_20260930\`; копия в пакет — `<repo>\build\inject_run1_passed.log` | `c21a817b63b94277beb40929a9cc01ca7e9563f529381de20ba8c8b800454c60` | `cv → @ADC:CV:OK:offset_vbus=21`, `rc=0`, вердикт `preflight-only` |
| `inject_preflight2.log` | `<repo>\build\` | `5695f3e39760714c74d63f3efd98c444f4c9fd2ee3fce0186bc12d13943feef3` | после `ci 0`/`c`: `cv → rc=-2` |
| `inject_preflight3.log` | `<repo>\build\` | `5154426070a719f9407cf845ca6b22d53f9bae6c4b7d20dad6d768e8042bd6e4` | то же (после сброса цели `-rst`) |
| `inject_preflight4.log` | `<repo>\build\` | `f19edf6fd56e2f04ee77d14ac290acac4c75538f27d92586e6f843c389a31661` | 3/3 `rc=-2` ⇒ `PREFLIGHT BLOCKED` (fail-closed) |
| `diag_cv_noise.log` | `<repo>\build\` | `a114588f3f5063db31edd2c5d77f7522cea1f24277d1613f2f623661164ea2db` | тихий канал + `ci 0.vbus_mv=10476` при `a`=22…23 |
| `diag_cv_noise.stdout` (полный UART-транскрипт) | `<repo>\build\` | `bd60fd2e181123976c272810cb6a99096ba4ee3cc2a4d808c5212a4c59b3c29c` | то же + серия `a`=24/**40**/22/23/24 |
| `ready_before.stdout` | `<repo>\build\` | `3d9ffc48a1c38f008bd6d2c560318bc07c10ae6e48a76c75c17f5fc8c61f9adf` | `a`=22/22/22/23/23 (разброс 1), `cv` 3/3 `rc=-2` |
| дамп Flash до опыта (512 КБ) | ПК-3: `backup\flash_pc3_inject_pre.bin` + `SHA256SUMS.txt` | `2cbab38a9182a48e1537bd5dfeb16888772514cdf987c37cc6a5fb93659329e2` | что было в плате до опыта |

Все восемь файлов — сырые логи стенда; в git они не публикуются (каталоги `build/`,
`backup/`, `C:\campaign_raw\` — gitignore/вне репо), поэтому проверяемость
обеспечивается этой выдержкой и хешами.

## 2. Прогон 1: гейт пройден (единственный раз за сессию)

```
$ dump    @PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:CR2=0x00000020:CCER=0x00000000
$ dump8   @PWM8:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:CR2=0x00000000:CCER=0x00000000
$ breakdiag  @BRK:valid=0
$ a       @ADC:I1=2037:I2=2068:Ires=0:VBUS=19
$ cv      @ADC:CV:OK:offset_vbus=21 (raw at 0 V)
$ sysinfo @SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x01005532:OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=0:uart_trunc=0
[pre] MOE1=0 MOE8=0 CCER1=0x00000000 CCER8=0x00000000 valid=0 VBUS raw=19 (cv offset=21) FAULT=0
$ a       @ADC:I1=2038:I2=2065:VBUS=23  ;  @ADC:I1=2038:I2=2065:VBUS=21  ;  @ADC:I1=2037:I2=2076:VBUS=24
[pre] VBUS offset=21 counts, residual median +2.0 / max 3 counts (gate <= 9 / <= 200) -> 201 mV (gate 5000 mV)
$ ci 0    @CI:ZERO:samples=256:raw_i1_avg=2037:raw_i2_avg=2066:raw_i1_min=2030:raw_i1_max=2043:raw_i2_min=2041:raw_i2_max=2113:dt_ms=5:vbus_mv=402
$ ci 0    @CI:ZERO:samples=256:raw_i1_avg=2037:raw_i2_avg=2066:raw_i1_min=2021:raw_i1_max=2048:raw_i2_min=2030:raw_i2_max=2141:dt_ms=4:vbus_mv=100
$ ci 0    @CI:ZERO:samples=256:raw_i1_avg=2037:raw_i2_avg=2066:raw_i1_min=2023:raw_i1_max=2051:raw_i2_min=2035:raw_i2_max=2098:dt_ms=4:vbus_mv=0
INJECTION OBSERVATION: preflight-only (baseline captured, nothing injected)
```

## 3. Тихий канал — и всё равно отказ (диагностика ПК-3, read-only)

```
[diag] raw VBUS: [22, 22, 22, 23, 23] (spread 1 counts)      <- канал тихий
   cv 1/2/3: @ADC:CV:FAIL:rc=-2  ;  cv after c: @ADC:CV:FAIL:rc=-2
[diag] `ci 0` windows:
   ci 0 #1: raw_i1_min=2030:raw_i1_max=2052:raw_i2_min=2032:raw_i2_max=2116:vbus_mv=2619
   ci 0 #2: raw_i1_min=2028:raw_i1_max=2053:raw_i2_min=1963:raw_i2_max=2079:vbus_mv=10476
   ci 1:    vbus_mv=2216  ;  cv after ci 1: rc=-2
   ci 2:    vbus_mv=3727  ;  cv after ci 2: rc=-2
[diag] CCER1=0x00000000, CCER8=0x00000000, @BRK:valid=0 (PWM off на время прогона)
```

Другая серия (`diag_cv_noise.stdout`): `a` = 24/**40**/22/23/24 (разброс 18 отсч),
`ci 0` → 2316 / 2115 мВ, `ci 1` → **9569** мВ, `ci 2` → 2014 мВ, `cv` — 3/3 `rc=-2`.

Пересчёт по формуле `ADC_VbusMvFromRaw` (`src/adc.c:89-97`: `raw·125·3300/4095` =
100.73 мВ/отсч при невалидном смещении): `10476 мВ ⇔ raw ≈ 104 отсч`, `9569 ⇔ 95`,
`3727 ⇔ 37`, `2619 ⇔ 26`, `2316 ⇔ 23`, `2115 ⇔ 21`. То есть в одиночном
regular-чтении VBUS наблюдались значения 21…**104** отсч при уровне `a` = 22…24, и два
из них выше порога `ADC_VBUS_OFFSET_MAX_RAW = 40` (`src/adc.h:25-27`).


## 4. Чего в сырых данных НЕТ (границы доказательной базы)

* **Нет полного набора 256 сэмплов** прогона `cv`: `ADC_CalibrateVbusOffset()`
  статистику наружу не печатает (только код возврата), а `ci` печатает лишь
  `avg/min/max`. ⇒ медиана и доля выбросов из живых логов **не выводимы**: T1–T5
  строятся как синтетические последовательности мока по числам §1, а фактические
  медиана/макс/доля появятся только после R5 ТЗ.
* **Лога прогона 1 нет в репозитории**: до фикса дефекта harness писал лог по
  жёсткому дефолтному пути (`log -> C:\campaign_raw\pc3_foc_bench_20260930\inject_log.txt`);
  копия для пакета — `build/inject_run1_passed.log` (sha256 совпадает).
* **Ревизия исходников образа под тестом не задокументирована**: `mapcap identity`
  в логах прогонов не выполнялся, `@SYS:` ревизию не печатает.

## 5. Идентичность образа под тестом (важно для приёмки)

* В плате во время прогонов был образ, поддерживающий `cv`/`ci` (подтверждено живыми
  ответами); sha256 дампа — `2cbab38a…29e2`.
* Локальные артефакты сборки **не являются** этим образом:
  `build/firmware.bin` (побайтово равен `build-commissioning/firmware.bin`) =
  `a5c4aaf1b9e552b69974434afe580e30a6f6d440ff21bda4b0b36bcdd7d5de86`, и в нём **нет**
  строк `@ADC:CV` и `@CI:` (это сборка 30.09, до появления `cv`/`ci`); reset-вектор в
  дампе `0x080121ad` против `0x0800c4e9` в локальном артефакте.
  ⇒ Сравнивать поведение образа под тестом с локальным `build/firmware.bin` нельзя —
  это разные образы.
* Рекомендация (закрывает пробел, read-only): в прогон §7 добавить `mapcap identity` и
  фиксировать `acs`/`ccs` в отчёте. Эталон для сравнения (ПК-3, 30.09,
  `C:\campaign_raw\pc3_foc_bench_20260930\verify_flash_log.txt`):
  `@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0xABE94C77`

## 6. Что передаётся ПК-2, а что нет

Передаётся (нужно для RED и приёмки): это приложение (выдержки + sha256) плюс факт
«локальные `.bin` — не образ под тестом». Этого достаточно, чтобы числа §1
проверялись, и чтобы приёмщик не мерил поведение против устаревшего артефакта.

Не передаётся (не нужно, создаёт шум): полные сырые логи (≈20 КБ, лежат на ПК-3),
дамп Flash 512 КБ, внутренние отладочные скрипты ПК-3, лог `pytest` HWT-тулов
(к области ТЗ не относится).
