# Первая стендовая сессия — чек-лист (default-deny)

**Цель:** после софт-цикла (SD-рефакторинг e600d53, F1–F7, L3-этапы) — прошить плату,
проверить живой ADC и энкодер. SD-приёмка T1–T4 — по отдельному
`OEW_SD_CHECKLIST.md` (no-HV). **Энергизированные тесты (FOC/V/f/autotune/mapcap)
в этой сессии не запускаются — они заблокированы дизайном.**

**Baseline прошивки:** `origin/main` (актуальный; прошивка на плате — старая).

---

## 0. Подготовка и безопасность

- [ ] DC-link **отсоединён**, HV-источник не подключён; DMM: обе шины < 1 В
- [ ] SD-цепи: SD1→PB12, SD2→PD2 — MCU только слушатель (ничего не драйвит)
- [ ] **aux 3.3V на STEVAL — от отдельного источника** (J2 pin 25/28), НЕ от шины Nucleo
      (иначе снятие aux убьёт MCU; SD-приёмка T4 требует развязки — см. OEW_SD_CHECKLIST.md).
- [ ] Инструменты: DMM, щупы (SC-A = SD1/PB12, SC-B = SD2/PD2), ЛА/осциллограф (опц.)
- [ ] Нужен open-drain фикстурный транзистор (BSS138/2N7002) для T2/T3
      (GPIO → затвор, сток → SD-нет, исток → GND; НЕ сажать GPIO на SD напрямую)

## 1. Сборка и прошивка

```bash
make          # OEW_HS1_COMMISSIONING_RELEASE=0 по умолчанию (default-deny)
make flash    # STM32_Programmer_CLI
```
- [ ] «Download verified successfully» + «MCU Reset» (иначе не прошилось)
- [ ] Переподключить COM-порт в `nucleo_debug_tool.py`
- [ ] Stale-flash probe: `sysinfo` отвечает; свежая команда (например, `p?`) НЕ даёт `unknown`

## 2. Живой ADC (без мостов)

- [ ] `c` — калибровка нулей: `@ADC:CAL:offset_i1=..:offset_i2=..:offset_ires=..`
- [ ] `a` — коды токов около 2048 (±шум), VBUS в ожидаемом окне
      (проверка «живого» ADC: JADSTART-ловушка — если `a` показывает застывшие
      значения, а VBUS не реагирует на изменение — см. skill stm32-motor-foc-debugging)
- [ ] Опционально: подать 96–192 мВ на PC4 (имитация 12–24 В шины) → `a`/`@FOC` VBUS меняется

## 3. Энкодер AS5048A (PA15, TIM2_CH1)

- [ ] `enc` → `@ENC:angle=..:speed=..:period_us=..:pulse_us=..:err=0`
- [ ] Период ≈ 1087 мкс (920 Гц) — если `err=1` (TIMEOUT) или `err=2` (BAD_PERIOD) — проверить провод/подтяжку
- [ ] Прокрутить вал вручную: `angle` меняется 0..16383, `speed` — знак по направлению
- [ ] (Опц.) `ENC_Calibrate` — полный оборот вала, если нужен абсолютный 0

## 4. Журнал результатов

| Тест | Ожидание | Факт | PASS/FAIL/BLOCKED |
|---|---|---|---|
| SD-приёмка T1–T4 | см. OEW_SD_CHECKLIST.md (no-HV) | | |
| ADC | коды ~2048, VBUS живой | | |
| ENC | period 1087 мкс, err=0, angle | | |

**Любой FAIL — терминальный для следующего шага** (энергизация) до выяснения причины
и повторного теста (по OEW_SD_CHECKLIST).

## 5. Что НЕ делаем в этой сессии

- HV/DC-link НЕ подаём (нужна полная SD-приёмка + следующий этап)
- Токовые каналы energised-проверкой (`chu/chv/chw` с током) — НЕ проверяем:
  заблокированы до измеренной карты; правило «шунт НЕ модулирующего инвертора»
  уже подтверждено на железе ранее (2026-08) и описано в доках
- `mapcap build=`/MAP_READY — только после board-qualified профиля (шаги L3)
- Прошивку НЕ менять на commissioning (OEW_HS1_COMMISSIONING_RELEASE) — до полной приёмки

## 6. Блокер будущей квалификации V/f (аудит safety, 2026-08)

Аудит safety-модулей (f58316c) зафиксировал **латентный P1**: `PROTECT_Check()`
(`protect.c:146-156`) вызывает `PROTECT_CheckFrame()` только для
`ADC_FRAME_VALID`; V/f service sample публикуется как `ADC_FRAME_SERVICE_BUSY`
и software protection его не проверяет. Сейчас путь недостижим
(`VFC_Start()` → `VFC_START_CONTEXT_UNVERIFIED`), но **ПЕРЕД любым
board-qualified разрешением V/f обязателен фикс**: service sample должен
проходить токовую/Vbus проверку (или V/f не разрешать).

Сопутствующие P2 (state-consistency) **исправлены и приняты в main (`0dd7f4a`, 2026-08)**:
break ISR теперь вызывает `FOC_Stop()` (после hardware break `foc_running=0`,
телеметрия согласована, ручная команда `0` не нужна) и
`MapCapturePort_OnProtectionLatched()` (commissioning capture не зависает
в `MAP_CAPTURE_RUNNING`). Ожидания в T2/T3 не меняются: после break — fault latched,
`1` → `FAULT! send 'f' to clear`; после `f` — `1` → `rc=-4` (interlock), ШИМ выключен.
