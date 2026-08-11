# STM32 Firmware Development

Ты — агент-разработчик под STM32. Твоя среда — VS Code, компилятор arm-none-eabi-gcc.

## Правила

1. **Только CMSIS, никакого HAL** — используй только регистры через CMSIS. Запрещено использовать HAL-функции (HAL_*, HAL_StatusTypeDef и т.д.).

2. **Проверяй регистры по RM0440** — перед использованием любого регистра периферии проверь его описание в Reference Manual RM0440 (STM32G4). Не используй регистры по памяти или предположениям.

3. **Каждое изменение → компиляция + прошивка** — любое изменение кода должно сопровождаться:
   - `make` (компиляция)
   - `flash` (заливка на плату через Makefile: `make flash` или `flash.bat`)

4. **Бинарный поиск при зависаниях** — если плата не отвечает, используй метод бинарного поиска: добавляй отладочные принты (printf через UART/SWO), чтобы найти точное место зависания. Не гадай — локализуй принтами.

5. **Изменил периферию → CubeMX самоконтроль** — после любого изменения конфигурации периферии (пины, таймеры, ADC, тактирование в `.c`/`.h`/`.ioc`) обязательно запусти:
   ```bash
   python scripts/cubemx_check.py
   ```
   Скрипт генерирует код из эталонного `OEW_Motor.ioc` через CubeMX headless и сверяет пины + значения (PSC/ARR/dead-time TIM1/TIM8, каналы ADC2, тактирование PLL) с реальным кодом. Код выхода 0 = PASS, 1 = расхождение. При FAIL — исправь код или `.ioc` (таблица в `scripts/make_ioc.py`), затем пересобери: `make && make flash`.
   Также подключён pre-push hook: `cp scripts/hooks/pre-push .git/hooks/` (блокирует push при расхождении; отключить: `git config hooks.cubemx-check false`).

## Сборка и прошивка

- Компилятор: `arm-none-eabi-gcc` (ARM GNU Toolchain)
- Makefile в корне проекта — цели: `all`, `clean`, `flash`
- Путь к тулчейну: `C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.2 Rel1\bin`
- CMSIS: `C:/Users/190/STM32CubeG4/Drivers/CMSIS/`
- Цель: STM32G474RE (Cortex-M4, FPU)
