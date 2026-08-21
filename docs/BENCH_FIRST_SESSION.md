# Первая стендовая сессия — чек-лист (default-deny)

**Цель:** после софт-цикла (SD-рефакторинг e600d53, F1–F7, L3-этапы) — прошить плату,
принять SD-защиту по `OEW_SD_CHECKLIST.md` (T1–T4), подтвердить fail-closed,
проверить живой ADC и энкодер. **Энергизированные тесты (FOC/V/f/autotune/mapcap)
в этой сессии не запускаются — они заблокированы дизайном.**

**Baseline прошивки:** `origin/main` (актуальный; прошивка на плате — старая).

---

## 0. Подготовка и безопасность

- [ ] DC-link **отсоединён**, HV-источник не подключён; DMM: обе шины < 1 В
- [ ] SD-цепи: SD1→PB12, SD2→PD2 — MCU только слушатель (ничего не драйвит)
- [ ] **aux 3.3V на STEVAL — от отдельного источника** (J2 pin 25/28), НЕ от шины Nucleo
      (иначе T4 невозможен: снятие aux убьёт MCU). Если развязка ещё не сделана — пометить BLOCKED для T4
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

## 2. SD-приёмка T1–T4 (по OEW_SD_CHECKLIST.md, no-HV)

**T1 — default-deny + SD high:**
- [ ] `p?` → BDTR без MOE (бит 15 = 0), CR1 CEN=0
- [ ] Щупы: SC-A (SD1) и SC-B (SD2) — высокий (3.3V через R28 при aux)
- [ ] `1` (FOC start) → `@FOC:START:FAIL:rc=-4` (interlock open) — PWM отклонён

**T2 — SD1:**
- [ ] Open-drain фикстурой дёрнуть SD1 в 0 → щуп SC-A: фронт в 0
- [ ] `p?` → MOE=0, CEN=0 (аппаратный break)
- [ ] `1` → `FAULT! send 'f' to clear` (fault latched, причина HARDWARE_BREAK)
- [ ] Отпустить фикстуру (SD1 high) → `1` всё ещё FAULT (no self-rearm)
- [ ] `f` → clear OK; `1` → снова `rc=-4` (interlock, не fault) — ШИМ остался выключен

**T3 — SD2:** повторить T2 для SD2 (PD2/TIM8)

**T4 — reset:**
- [ ] Кнопка NRST (или power cycle) при подключённой SD-топологии, DC-link отсутствует
- [ ] После boot: `p?` → MOE=0; `1` → rc=-4 (default-deny после ресета, без самовзвода)

## 3. Fail-closed подтверждение (ожидаемое поведение, НЕ баги)

- [ ] `chu`/`chv`/`chw` → `@AT:ERROR:POWER_BLOCKED:no measured OEW sector/window map`
      (energised autotune заблокирован до измеренной карты)
- [ ] `vf=500` → `V/f blocked: rc=... (sample context unverified)`
- [ ] `idle`/`oew`/`rr`/`noload` → POWER_BLOCKED (те же причины)
- [ ] `mapcap arm=1` (если commissioning-сборка) → `@MC:ARM:BLOCKED:PROFILE`

## 4. Живой ADC (без мостов)

- [ ] `c` — калибровка нулей: `@ADC:CAL:offset_i1=..:offset_i2=..:offset_ires=..`
- [ ] `a` — коды токов около 2048 (±шум), VBUS в ожидаемом окне
      (проверка «живого» ADC: JADSTART-ловушка — если `a` показывает застывшие
      значения, а VBUS не реагирует на изменение — см. skill stm32-motor-foc-debugging)
- [ ] Опционально: подать 96–192 мВ на PC4 (имитация 12–24 В шины) → `a`/`@FOC` VBUS меняется

## 5. Энкодер AS5048A (PA15, TIM2_CH1)

- [ ] `enc` → `@ENC:angle=..:speed=..:period_us=..:pulse_us=..:err=0`
- [ ] Период ≈ 1087 мкс (920 Гц) — если `err=1` (TIMEOUT) или `err=2` (BAD_PERIOD) — проверить провод/подтяжку
- [ ] Прокрутить вал вручную: `angle` меняется 0..16383, `speed` — знак по направлению
- [ ] (Опц.) `ENC_Calibrate` — полный оборот вала, если нужен абсолютный 0

## 6. Журнал результатов

| Тест | Ожидание | Факт | PASS/FAIL/BLOCKED |
|---|---|---|---|
| T1 | MOE=0, SD high, rc=-4 | | |
| T2 (SD1) | break→MOE=0, latch, clear-гейт | | |
| T3 (SD2) | то же для TIM8 | | |
| T4 | reset → PWM off, default-deny | | |
| POWER_BLOCKED | chu/ch/vf/idle → блокировка | | |
| ADC | коды ~2048, VBUS живой | | |
| ENC | period 1087 мкс, err=0, angle | | |

**Любой FAIL — терминальный для следующего шага** (энергизация) до выяснения причины
и повторного теста (по OEW_SD_CHECKLIST).

## 7. Что НЕ делаем в этой сессии

- HV/DC-link НЕ подаём (нужна полная SD-приёмка + следующий этап)
- Токовые каналы energised-проверкой (`chu/chv/chw` с током) — НЕ проверяем:
  заблокированы до измеренной карты; правило «шунт НЕ модулирующего инвертора»
  уже подтверждено на железе ранее (2026-08) и описано в доках
- `mapcap build=`/MAP_READY — только после board-qualified профиля (шаги L3)
- Прошивку НЕ менять на commissioning (OEW_HS1_COMMISSIONING_RELEASE) — до полной приёмки

## 8. Блокер будущей квалификации V/f (аудит safety, 2026-08)

Аудит safety-модулей (f58316c) зафиксировал **латентный P1**: `PROTECT_Check()`
(`protect.c:146-156`) вызывает `PROTECT_CheckFrame()` только для
`ADC_FRAME_VALID`; V/f service sample публикуется как `ADC_FRAME_SERVICE_BUSY`
и software protection его не проверяет. Сейчас путь недостижим
(`VFC_Start()` → `VFC_START_CONTEXT_UNVERIFIED`), но **ПЕРЕД любым
board-qualified разрешением V/f обязателен фикс**: service sample должен
проходить токовую/Vbus проверку (или V/f не разрешать).

Сопутствующие P2 (state-consistency, не rearm): break ISR не вызывает
`FOC_Stop()` (после hardware break `foc_running` остаётся 1 — ложная
телеметрия, восстановление ручной командой `0`); `MapCapturePort_OnProtectionLatched()`
не вызывается из break path (commissioning capture может зависнуть в
`MAP_CAPTURE_RUNNING` до abort).
