# ТЗ: WEB AI — @VFLOG: не блокировать UART из main во время V/f

## Цель
Стрим `@VFLOG` из TIM6 (`UART_TrySendTelemetry`, 40 мс) не должен
рваться из-за busy-wait `UART_SendTelemetry` в main (`@VF` каждые 100 мс
на том же кольце).

## Требования
1. Пока `VFC_IsRunning()` и `vflog_period_ms > 0` — не слать периодический
   `@VF` из main (сессия принадлежит VFLOG).
2. Пока V/f без VFLOG — `@VF` и `@ADC`-stream только через `UART_TrySendTelemetry`.
3. Комментарии NVIC: TIM6 = 2 (равен USART2), не 1.
4. Формат полей `@VFLOG` не менять (суффикс `vflog_drp` уже есть).

## Контекст
- Стенд: обрыв VFLOG ~1.5–2 с в 8/10.
- Файлы: `main.c`, `src/uart.c`.
