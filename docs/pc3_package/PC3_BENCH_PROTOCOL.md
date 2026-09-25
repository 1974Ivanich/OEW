# PC-3 Bench Protocol: ACS712-5A Phase-0 → 1–3 A Physical Response

**Ветка:** `ai2/acs712-scope-foc-map`  
**Commit:** `d1bef50dab9db8ea1904fcc1ae9b7f2a89179e79`  
**Пакет:** `docs/pc3_package/`  
**Цель:** Phase-0 (zero-current) → 1–3 A physical response capture

---

## 0. Safety First

**До включения силовой части — прочитать `OEW_SD_CHECKLIST.md` (§1–§4).**

Стенд — OEW bench, не автономный. Действующие правила:
- RELEASE=0 (safety limits enforced)
- SD мониторинг активен
- BKIN отключён (monitor-only mode, не hardware trip)
- Оператор отвечает за physical safety

Если что-то неясно — **не включать**. Остановить, спросить.

---

## 1. Аппаратная подготовка (до включения питания)

### 1.1 Провода и подключения

```
STM32 Nucleo (USB → ПК-3)
         │
         └── USB type-A

TAO3104A (USB → ПК-3)
         │
         └── USB type-A

ACS712-5A × 2
    U-phase:  выход ACS712-U → щуп осциллографа CH1 (1× или 10×)
    V-phase:  выход ACS712-V → щуп осциллографа CH2 (1× или 10×)
    GND:      общий GND щупов → GND платы OEW

PB6 (Nucleo) → щуп осциллографа CH3 (1×)

ACS712 питание: 5 В внешний блок питания (измерить мультиметром)
```

### 1.2 Проверить глазами

- [ ] ACS712-U подключён к CH1
- [ ] ACS712-V подключён к CH2
- [ ] PB6 подключён к CH3
- [ ] Все GND соединены (осциллограф, плата, ACS712)
- [ ] Щупы осциллографа на 1× или 10× — записать что выбрано
- [ ] Питание ACS712: измерить мультиметром, записать (например, 5.02 В)
- [ ] Питание Nucleo: USB, не требует проверки

### 1.3 Режим осциллографа

TAO3104A должен быть в режиме **Screen Capture** (не Waveform):
- На экране осциллографа: MODE → SCREEN
- :DATA:WAVE:SCREEN:HEAD? возвращает JSON header
- :DATA:WAVE:SCREEN:CH1? возвращает waveform data

---

## 2. Программная подготовка

### 2.1 Софт

```bash
cd C:\ST\boyler\Motor

# Если репо ещё не склонирован:
git clone https://github.com/1974Ivanich/OEW.git
cd OEW
git checkout ai2/acs712-scope-foc-map
git pull

# Если репо уже есть:
git fetch origin
git checkout ai2/acs712-scope-foc-map
git pull origin ai2/acs712-scope-foc-map
```

### 2.2 Виртуальное окружение

```bash
cd C:\ST\boyler\Motor

# Создать виртуальное окружение (один раз)
python -m venv .venv

# Активировать
.venv\Scripts\activate

# Установить зависимости
pip install -r docs\pc3_package\requirements-acs712-scope.txt
pip install -r docs\pc3_package\requirements-tao3104a.txt
```

### 2.3 Проверка связи с осциллографом

```bash
python docs\pc3_package\scope_acs712_selftest.py --probe
```

**Ожидаемый вывод:**
```
IDN = OWON,TAO3104A,...
DATATYPE = BYTE RUNSTATUS = RUN
DATALEN  = 1520 SAMPLERATE = 30.0MSa/s
  CH1: scale=1.0  probe=1X  offset=128  freq=...
  CH2: scale=1.0  probe=1X  offset=128  freq=...
  CH3: scale=1.0  probe=1X  offset=128  freq=...
```

Если IDN не виден → проверить драйвер Zadig (libusb-win32) для TAO3104A.

---

## 3. Phase-0: Zero-Current Characterisation (БЕЗ силовой части)

**Цель:** измерить шум электронной цепи (ACS712 + осциллограф + щупы) при нулевом токе.

**Условие:** мотор не подключён, инвертор выключен, только логика 3.3 В на плате.

### 3.1 Подготовка

- [ ] TAO3104A включён и в режиме SCREEN
- [ ] USB подключён к ПК-3
- [ ] Все щупы подключены (CH1, CH2, CH3)
- [ ] ACS712 запитан 5 В (записать точное напряжение)
- [ ] На осциллографе: убедиться что сигнал на CH1/CH2 — приблизительно VCC/2 (например, ~2.5 В при 5 В питании). Это подтверждает что на входе ACS712 нет тока.

### 3.2 Запуск

```bash
# Перейти в каталог с tools
cd C:\ST\boyler\Motor

# Запустить Phase-0
# --vcc-mv: измеренное напряжение питания ACS712 в милливольтах
# --sens-mv-per-a: 185.0 для ACS712-5A
# --timer-hz: 170000000 для STM32G4 (APB2 = 170 МГц)
# --out: директория для артефактов

python docs\pc3_package\scope_acs712_capture.py `
    --phase0 `
    --out .tzref01_phase0_pc3 `
    --vcc-mv 5020 `
    --sens-mv-per-a 185.0 `
    --timer-hz 170000000 `
    --u-ch CH1 `
    --v-ch CH2 `
    --sync-ch CH3
```

### 3.3 Ожидаемый вывод

```
=== Phase-0 zero-current characterisation ===
IDN: OWON,TAO3104A,...

  CH1 (ACS712-U):
    scale=1.0  probe=1X  offset=...
    v_mean=... mV   noise_vpp=... mV   noise_vrms=... mV
    samples=1520  sr=30000000 Hz  window=0.0000507 s

  CH2 (ACS712-V):
    ...

  CH3 (PB6 marker):
    ...

  Conservative noise floor: ... mVpp = ... mApp(pp)  ... mVrms = ... mApp(rms)
  vcc_acs712 (nominal, operator-supplied): 5020 mV
  NOTE: this data is PHASE0 only. Quantitative gate depends on
  Phase 0 session completion per TZ-REF-01 §4.3.
Saved: .tzref01_phase0_pc3\phase0_characterisation.json
```

### 3.4 Что записать вручную (сессионный лог)

```
Дата/время UTC:
Vcc ACS712 (мультиметр): ... мВ
Probe attenuation (CH1/CH2/CH3): 1× или 10×
Температура в помещении:
ACS712 серийные номера (если видны):
Длина проводов щупов:
```

### 3.5 Проверка артефакта

Открыть `.tzref01_phase0_pc3\phase0_characterisation.json` и проверить глазами:

```json
{
  "idn": "OWON,TAO3104A,...",     ← осциллограф опознан
  "phase": "PHASE0",              ← это Phase-0
  "acs712_sensitivity_mv_per_a": 185.0,
  "characterisation": {
    "CH1": {
      "noise_vpp_mv": < 50,        ← шум не более 50 мВ peak-to-peak
      "noise_vrms_mv": < 10,       ← шум не более 10 мВ RMS
      "zero_noise_pp_ma": < 300,   ← эквивалентный токовый шум
      "zero_noise_rms_ma": < 100,
      "window_duration_s": < 1     ← захват короткий
    }
  }
}
```

Если `noise_vpp_mv > 100` — записать, проверить щупы и заземление. Не продолжать без записи.

---

## 4. Decision Gate: SNR Sufficiency

На основании Phase-0 вычислить SNR:

```
noise_floor_mA_pp = zero_noise_pp_ma  (из JSON,worst channel)
smallest_expected_current_mA = 1000  (1 A = нижняя граница qual range)

SNR_pp = smallest_expected_current_mA / noise_floor_mA_pp
```

| SNR_pp | Значение |
|---|---|
| < 3 | Цепь квалифицирована (chain/timing), масштаб НЕ доказан |
| 3–10 | Можно наблюдать амплитуду, но не количественную шкалу |
| ≥ 10 | Допустимо quantity claim (при остальных gates) |

**Записать:**
```
noise_floor_mA_pp = ... мА
SNR_pp = ...
Вердикт: ...
```

---

## 5. 1–3 A Physical Response Capture

**Когда:** SNR_pp ≥ 3 (если < 3 — записать и продолжить с пометкой CHAIN_ONLY).

**Условие:** Phase-0 пройден, RELEASE=0, SD мониторинг активен, оператор у рубильника.

### 5.1 Подготовка стенда

- [ ] Мотор подключён
- [ ] OEW board: RELEASE=0 (если не уверен — проверить)
- [ ] SD мониторинг: убедиться что логирование sd1/sd2 активно
- [ ] Vbus измерить мультиметром до включения: ___ В

### 5.2 Включение и ожидание

Включить силовое питание. Дождаться стабильного Vbus. Записать:

```
Vbus стабилизирован: ___ В
Температура платы (рука): ___ / норма / горячая
```

### 5.3 Запуск FOC на минимальном токе

Минимальный ток FOC — через GUI или CLI. Цель: убедиться что цепь работает передcampaign.

Если FOC не запускается — **не продолжать**. Записать ошибку, остановить.

### 5.4 Campaign 1–3 A

Campaign = набор точек: 1 A, 1.5 A, 2 A, 2.5 A, 3 A.

Для каждой точки (пример для ручного режима):

```bash
# Захват одной точки — например 2 A
python docs\pc3_package\scope_acs712_capture.py `
    --capture `
    --out .campaign_pc3 `
    --pwm-hz 3000 `
    --timer-hz 170000000 `
    --vcc-mv 5020 `
    --sens-mv-per-a 185.0 `
    --u-ch CH1 `
    --v-ch CH2 `
    --sync-ch CH3
```

**Для каждой точки записать:**

```
Ток (уставка): ... A
ACS712-U mean: ... мВ
ACS712-V mean: ... мВ
Число импульсов: ...
qualified: 0 или 1
```

### 5.5 Контрольные точки campaign

| Точка | Ток | Записать |
|---|---|---|
| P1 | 1.0 A | CH1 mean, CH2 mean, qualified |
| P2 | 1.5 A | CH1 mean, CH2 mean, qualified |
| P3 | 2.0 A | CH1 mean, CH2 mean, qualified |
| P4 | 2.5 A | CH1 mean, CH2 mean, qualified |
| P5 | 3.0 A | CH1 mean, CH2 mean, qualified |

### 5.6 Остановить FOC

После campaign остановить мотор. Записать:

```
Campaign завершена
Vbus после campaign: ___ В
```

---

## 6. Критерии остановки (Safety)

**Немедленно остановить если:**
- Vbus просел более чем на 10% от номинала
- SD fault сработал (sd1=1 или sd2=1 в логе)
- Нагрев платы ощутим рукой
- Осциллограмма показывает clipping (сигнал ушёл в потолок или пол)
- Operator instinct: что-то не так — остановить

---

## 7. Артефакты для передачи

После завершения сессии собрать:

```
C:\ST\boyler\Motor\
  .tzref01_phase0_pc3\
        phase0_characterisation.json     ← Phase-0 JSON
  .campaign_pc3\
        scope_capture.csv
        scope_capture.preamble.json
  session_notes.txt                      ← всё что записали вручную
```

Передать через GitHub (push в ветку `ai2/acs712-scope-foc-map`) или на shared drive.

---

## 8. Чеклист сессии

### До включения
- [ ] OEW_SD_CHECKLIST.md прочитан
- [ ] Wiring проверена глазами
- [ ] Vcc ACS712 измерен мультиметром
- [ ] TAO3104A в режиме SCREEN
- [ ] USB подключён
- [ ] RELEASE=0

### Phase-0
- [ ] Phase-0 выполнен без ошибок
- [ ] JSON сохранён
- [ ] noise_vpp_mv < 50 мВ (оба канала)
- [ ] SNR_pp вычислен и записан

### Campaign
- [ ] 5 точек (1–3 A) собраны
- [ ] Для каждой: mean и qualified записаны
- [ ] Остановка безопасная

### После сессии
- [ ] Артефакты собраны
- [ ] Сессионный лог заполнен
- [ ] Силовая часть выключена
- [ ] USB отключён (опционально)

---

## 9. Конвертация в mA из Phase-0 (ручной расчёт)

Если хотите проверить SNR вручную:

```python
import json
with open('.tzref01_phase0_pc3/phase0_characterisation.json') as f:
    data = json.load(f)

sens = data['acs712_sensitivity_mv_per_a']  # 185.0

for ch, d in data['characterisation'].items():
    noise_vpp = d['noise_vpp_mv']
    noise_rms = d['noise_vrms_mv']
    noise_ma_pp  = noise_vpp / sens * 1000   # мА peak-to-peak
    noise_ma_rms = noise_rms / sens * 1000  # мА RMS

    print(f"{ch}: {noise_vpp:.1f} mVpp = {noise_ma_pp:.1f} mApp(pp)")
    print(f"{ch}: {noise_rms:.1f} mVrms = {noise_ma_rms:.1f} mArms")
```

**SNR_pp** = 1000 мА / noise_ma_pp

Пример:
```
noise_ma_pp = 216 мА  (40 мВpp / 185 мВ/А × 1000)
SNR_pp = 1000 / 216 ≈ 4.6
→ Допустимо наблюдение амплитуды
```

---

## 10. Краткая сводка артефактов

| Файл | Описание | Обязателен |
|---|---|---|
| `phase0_characterisation.json` | Phase-0 noise floor | ✅ |
| `scope_capture.csv` | Campaign raw data | ✅ |
| `scope_capture.preamble.json` | Campaign metadata | ✅ |
| `session_notes.txt` | Ручной лог (всё что записали) | ✅ |
| `Vbus_before/after.txt` | Если Vbus измеряли | опционально |

---

## 11. Куда передавать

**Вариант A: GitHub**
```bash
git add .tzref01_phase0_pc3 .campaign_pc3 session_notes.txt
git commit -m "PC-3 Phase-0 + campaign 1-3A"
git push origin ai2/acs712-scope-foc-map
```

**Вариант B: Shared drive**
Скопировать папки на E:\ или USB и передать на ПК-2.

---

## 12. Ожидаемый результат сессии

После Phase-0 и campaign ПК-2 получит:
1. `phase0_characterisation.json` → фактический noise floor ACS712-5A
2. Campaign CSV → physical response 1–3 A
3. Сессионный лог → provenance всех измерений

На основе этих данных:
- Подтвердить или опровергнуть SNR ≥ 3
- Решить: достаточно ли текущей цепи или нужен G3 (independent reference)
- Перейти к FOC с известным noise floor
