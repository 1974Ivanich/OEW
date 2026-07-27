# PC Test & Debug Tool for OEW Motor FOC — Implementation Plan

> **Goal:** Универсальная программа для ПК для тестирования и отладки ПО Nucleo-G474RE на всех этапах разработки. Три режима: PWM test, ADC test, Full FOC.

**Tech Stack:** Python 3.11, Tkinter, matplotlib (real-time plots), pyserial, на базе существующего `foc_control_gui.py`.

**Architecture:** Одно окно, переключение режимов через вкладки (Notebook). Каждая вкладка — свой набор элементов управления и отображения. Единое UART-соединение для всех режимов.

---

## Task 1: Каркас приложения — три вкладки

**Objective:** Создать основное окно с Notebook (3 вкладки) и общим COM-подключением.

**Files:**
- Create: `C:/ST/boyler/Motor/nucleo_debug_tool.py`
- Modify (ref): `C:/ST/boyler/Motor/foc_control_gui.py`

**Структура окна:**
```
┌────────────────────────────────────────────┐
│  COM: [COM15 ▼] [Connect]  [Status: OK]    │
├───┬────────┬──────────┬────────────────────┤
│   │ 1.PWM  │  2.ADC   │    3.FOC Full      │
│   ├────────┴──────────┴────────────────────┤
│   │                                        │
│   │     (содержимое текущей вкладки)        │
│   │                                        │
│   └────────────────────────────────────────┘
│  Log: [...]                                │
└────────────────────────────────────────────┘
```

**Базовые классы:**
```python
class NucleoDebugTool:
    def __init__(self):
        # COM connection (общий)
        self.serial = None
        self.reader_thread = None
        
        # Вкладки
        self.notebook = ttk.Notebook()
        self.tab_pwm = PWMTab(self.notebook)
        self.tab_adc = ADCTab(self.notebook)
        self.tab_foc = FOCTab(self.notebook)
        self.notebook.add(self.tab_pwm, text="1. PWM Test")
        self.notebook.add(self.tab_adc, text="2. ADC Test")
        self.notebook.add(self.tab_foc, text="3. FOC Full")
        
    def send_cmd(self, cmd): ...
    def on_line(self, line): ...  # диспетчеризация во вкладки
```

---

## Task 2: Вкладка 1 — PWM Test (голая Nucleo)

**Objective:** Тестирование всех 12 ШИМ-каналов. Частота, duty cycle, dead-time, фаза.

**Элементы управления:**
```
┌────────────────────────────────────────────┐
│ [Select all] [Clear all]                   │
│                                            │
│ Инвертор 1:                                │
│  ☑ PC0 HIN_U1  ☑ PA7 LIN_U1              │
│  ☑ PC1 HIN_V1  ☑ PB0 LIN_V1              │
│  ☑ PC2 HIN_W1  ☑ PB1 LIN_W1              │
│                                            │
│ Инвертор 2:                                │
│  ☑ PC6 HIN_U2  ☑ PC10 LIN_U2             │
│  ☑ PC7 HIN_V2  ☑ PC11 LIN_V2             │
│  ☑ PC8 HIN_W2  ☑ PC12 LIN_W2             │
│                                            │
│ Частота: [1000..10000] [5000 ▼] Гц        │
│ Duty:     [0..100] [15] %                 │
│ Dead-time: [0..100] [16] тиков (~1 мкс)   │
│                                            │
│ [▶ Start PWM] [■ Stop PWM]                │
│                                            │
│ Режим теста:                               │
│ ○ Continuous (непрерывный)                  │
│ ● Burst (10ms, с замером Saleae)          │
│ ○ Sweep (duty от 1 до 99 за 5 сек)        │
│                                            │
│ Status: Active  // PWM: 5044 Hz ✅        │
└────────────────────────────────────────────┘
```

**Команды на Nucleo:**
- `p=100,15,16` — включить все каналы: период 100, duty 15, dead-time 16
- `p=100,15,16,1,2,3` — только каналы 1,2,3 (по номеру)
- `p=0` — выключить ШИМ

**Добавить в прошивку** команду `p` (PWM test mode):
```c
// В main.c обработчик UART
if(cmd == 'p') {
    // парсим: p=<arr>,<duty>,<dt>[,channels]
    PWM_CmdParse(buf);
}
```

**Визуализация:** простые индикаторы "сигнал есть/нет" (зелёный/красный круг рядом с каждым каналом). При интеграции с Saleae — автоматическая проверка.

**Интеграция с Saleae (опционально):**
```python
def check_pwm_with_saleae(self):
    from saleae import automation
    # Захват 50 мс
    # Проверка частоты, duty, dead-time на каждом канале
    # Вывод: PASS/FAIL для каждого параметра
```

---

## Task 3: Вкладка 2 — ADC Test (Nucleo + STEVAL)

**Objective:** Тестирование АЦП с подключенными датчиками. Проверка offset, шума, реакции на ток.

**Элементы управления:**
```
┌────────────────────────────────────────────┐
│  Режим:                                     │
│  ○ Single shot (один замер)                │
│  ● Continuous (100 мс, автоповтор)         │
│  ○ Sweep (развёртка, запись в файл)       │
│                                            │
│  Каналы:                                    │
│  ┌──────────────────────────────────┐      │
│  │ I1:  ████████████░░░  1234 код   │      │
│  │     ≈ 1.52 В  →  +215 мА        │      │
│  │ I2:  █████████░░░░░  1024 код   │      │
│  │     ≈ 1.26 В  →  -102 мА        │      │
│  │ IN:  ░░░░░░░░░░░░░  512 код     │      │
│  │     ≈ 0.63 В  →  0 мА (TBD)    │      │
│  │ Vbus: ██░░░░░░░░░░  245 код     │      │
│  │     ≈ 0.30 В  →  37.4 В         │      │
│  └──────────────────────────────────┘      │
│                                            │
│  Offset: [▼ Calibrate (0A)]               │
│  Offset I1: 2048  I2: 2050  IN: 512       │
│                                            │
│  [Start] [Stop] [Export CSV]             │
│                                            │
│  ┌──────────────────────────────────┐      │
│  │          Real-time plot          │      │
│  │   I1 ──  I2 ──  IN ──          │      │
│  │   ▁▁▁▁▁▁▅▅▆▇▇▇▆▅▅▁▁▁▁        │      │
│  └──────────────────────────────────┘      │
└────────────────────────────────────────────┘
```

**Команды на Nucleo:**
- `a` — однократное чтение всех 4 каналов АЦП
- `a=100` — непрерывное чтение с периодом 100 мс
- `a=0` — остановить
- `c` — калибровка offset (усреднение 256 выборок)

**Формат ответа:**
```
@ADC:I1=1234:I2=1024:IN=512:VBUS=245
```

**Real-time plot:** Использовать matplotlib FigureCanvasTkAgg. 4 линии разного цвета. Auto-scroll последние 5 секунд.

**Экспорт CSV:** timestamp, I1_code, I2_code, IN_code, Vbus_code, I1_mA, I2_mA, IN_mA, Vbus_V

---

## Task 4: Вкладка 3 — FOC Full Test (с двигателем)

**Objective:** Полное управление двигателем: пуск, задание скорости, мониторинг, логирование.

**Элементы управления:**
```
┌────────────────────────────────────────────┐
│  Состояние: ● RUNNING  Speed: 1450 RPM     │
│                                            │
│  ┌─ Управление ────────────────────────┐   │
│  │ Speed: [0 ════════●══════ 3000] RPM│   │
│  │        [▶ START]  [■ STOP] [0]      │   │
│  │ Id ref: [0 ════●═══════ 2000] mA   │   │
│  │ Iq ref:  [0 ════●═══════ 2000] mA  │   │
│  └──────────────────────────────────────┘   │
│                                            │
│  ┌─ Телеметрия ────────────────────────┐   │
│  │  Id:  -12 mA    Speed: 1450 RPM     │   │
│  │  Iq:  345 mA    Theta: 1.23 рад    │   │
│  │  I1:  512 mA    I2:  -128 mA       │   │
│  │  Vbus: 58.2 V  IN: 0 mA            │   │
│  └──────────────────────────────────────┘   │
│                                            │
│  ┌─ Real-time plots ───────────────────┐   │
│  │  ┌── Id/Iq ────┐ ┌── Speed ──────┐ │   │
│  │  │  ▁▅▇▆▅▁     │ │  ▁▂▄▆▇▆▄▂▁   │ │   │
│  │  └─────────────┘ └──────────────┘ │   │
│  └────────────────────────────────────┘   │
│                                            │
│  [Start Log] [Stop Log] [Export CSV]      │
└────────────────────────────────────────────┘
```

**Команды на Nucleo:** уже есть — `1` (start), `0` (stop), `s=...` (speed)

**Формат ответа:** `@FOC:I1=...:I2=...:IN=...:VBUS=...`

**Дополнительно:**
- Ползунок скорости (Scale widget) — отправляет `s=N` при изменении
- Два графика: Id/Iq и Speed + Theta
- Логирование в CSV для пост-анализа

---

## Task 5: Saleae интеграция (опционально)

**Objective:** Автоматическая верификация ШИМ-сигналов через логический анализатор.

**Files:**
- Modify: `C:/ST/boyler/Motor/nucleo_debug_tool.py`

**Функции:**
```python
class SaleaeProbe:
    def __init__(self, port=10430):
        self.manager = automation.Manager.connect(port=port)
    
    def measure_frequency(self, channel, duration_ms=50):
        """Захват и измерение частоты на канале"""
        
    def measure_duty(self, channel):
        """Измерение duty cycle"""
        
    def measure_deadtime(self, ch_high, ch_low):
        """Измерение dead-time между верхним и нижним ключом"""
        
    def pwm_test_all(self, channels_config):
        """Автоматический тест всех каналов — PASS/FAIL"""
```

Кнопка "Run Saleae Test" на вкладке PWM запускает замер, сравнивает с ожидаемыми параметрами и выводит зелёные/красные индикаторы.

---

## Task 6: Прошивка Nucleo — обработчик тестовых команд

**Objective:** Добавить в прошивку обработку новых команд для тестовых режимов.

**Files:**
- Modify: `C:/ST/boyler/Motor/main.c`
- Modify: `C:/ST/boyler/Motor/src/pwm.c`
- Modify: `C:/ST/boyler/Motor/src/adc.c`

**Новые команды UART:**
| Команда | Формат | Действие |
|---------|--------|----------|
| PWM set | `p=<arr>,<duty>,<dt>[,mask]` | Установить ШИМ: ARR, CCR, DTG, маска каналов |
| PWM stop | `p=0` | Выключить ШИМ |
| PWM status | `p?` | Прочитать текущие регистры таймеров |
| ADC read | `a` | Однократное чтение всех каналов |
| ADC stream | `a=<Nms>` | Непрерывный поток каждые N мс |
| ADC stop | `a=0` | Остановить поток |
| ADC cal | `c` | Калибровка offset (256 сэмплов) |
| ADC status | `a?` | Прочитать offset и VREF |

**Обработка в main.c:**
```c
void ProcessTestCommand(char *buf) {
    if(buf[0] == 'p') { /* PWM command */ }
    else if(buf[0] == 'a') { /* ADC command */ }
    else if(buf[0] == 'c') { /* calibrate */ }
}
```

**ADC stream mode:** при включении режима `a=100`, прошивка отправляет `@ADC:...` каждые 100 мс. ПК только принимает.

---

## Task 7: GUI улучшения — общие для всех вкладок

**Objective:** Логирование, экспорт, цветовые темы, горячие клавиши.

**Files:**
- Modify: `C:/ST/boyler/Motor/nucleo_debug_tool.py`

**Функции:**
- Цветной лог: команды (серый), ответы (чёрный), телеметрия (синий), ошибки (красный)
- Кнопка "Copy log" — копирует выделенное в буфер
- Export CSV — для ADC и FOC вкладок
- Статусная строка: COM подключён, скорость, ошибки
- Цветовая индикация подключения: зелёный/красный индикатор

---

## Task 8: Сборка и установка

**Objective:** Автономный запуск, bat-файл, установка зависимостей.

**Files:**
- Modify: `C:/ST/boyler/Motor/run_foc_gui.bat` → переименовать/обновить
- Create: `C:/ST/boyler/Motor/run_debug_tool.bat`

```batch
@echo off
echo Starting Nucleo Debug Tool...
call pip install pyserial matplotlib --quiet 2>nul
"C:\Users\190\AppData\Local\Programs\Python\Python311\python.exe" nucleo_debug_tool.py
pause
```

**Зависимости:** `pyserial`, `matplotlib`, `tkinter` (встроен), `saleae` (опционально)

---

## Files that will change

| Файл | Действие |
|------|----------|
| `C:/ST/boyler/Motor/nucleo_debug_tool.py` | **Create** (главный файл) |
| `C:/ST/boyler/Motor/run_debug_tool.bat` | **Create** (запуск) |
| `C:/ST/boyler/Motor/main.c` | **Modify** (обработчик test-команд) |
| `C:/ST/boyler/Motor/src/pwm.c` | **Modify** (PWM_CmdParse) |
| `C:/ST/boyler/Motor/src/adc.c` | **Modify** (ADC stream mode) |

## Task ordering

1. Task 6 — сначала прошивка Nucleo (добавить команды `p`, `a`, `c`)
2. Task 1 — каркас приложения (окно, COM, вкладки)
3. Task 2 — PWM вкладка (Mode 1)
4. Task 3 — ADC вкладка (Mode 2)
5. Task 4 — FOC вкладка (Mode 3)
6. Task 5 — Saleae интеграция (опционально)
7. Task 7 — GUI улучшения (лог, экспорт)
8. Task 8 — bat-файл и установка

## Risks

1. **ADC stream** может создать нагрузку на UART при 100 мс — 10 сообщений/сек, ~200 байт/сек — не проблема для 115200 бод
2. **matplotlib real-time** может тормозить при 10+ обновлениях/сек. Решение: обновление 5 раз/сек, ограниченный буфер (500 точек)
3. **Saleae интеграция** требует запущенного Logic 2 с Automation Server. Если не запущен — кнопка неактивна
