#ifndef TELEMETRY_FORMAT_H
#define TELEMETRY_FORMAT_H

/*
 * Единый источник истины для строки baseline-телеметрии @FOC.
 *
 * Зачем отдельный заголовок: контракт длины пакета обязан проверяться ТОЙ ЖЕ
 * строкой формата, что уходит в эфир. Если тест держит свою копию формата, то
 * добавление поля в main.c не ломает тест — пакет молча перестаёт помещаться
 * в буфер, а на стенде оператор видит только пропавшие строки. Поэтому и
 * main.c, и hosted-тест бюджета включают ЭТОТ заголовок.
 *
 * Бюджет: строка форматируется в UART_TELEMETRY_BUF_SIZE (512) байт и
 * отправляется ТОЛЬКО целиком (см. UART_SendTelemetryChecked() в uart.c):
 * не поместившаяся строка отбрасывается как единое целое, её CRLF гарантирован,
 * событие учитывается в UART_GetTruncatedCount().
 *
 * Замеренные значения (tests/telemetry_budget_test.c):
 *   - холостой no-HV прогон (run_id=M0-Rn, токи 0): 244 байта;
 *   - худший реалистичный случай (границы int32 по всем полям): 314 байт.
 * 244 < 256, но запас в 12 байт не является контрактом: 314 в старый 256-байтовый
 * буфер не влезает и раньше приводил к усечению без CRLF.
 */
#define FOC_TELEMETRY_FMT \
    "@FOC:t=%lu:run_id=%s:map_id=M0:map_crc32=%08lX:I1=%ld:I2=%ld:Ires=%ld:" \
    "Id=%ld:Iq=%ld:Id_ref=%ld:Iq_ref=%ld:VBUS=%ld:STATE=%u:SPD=%ld:TH=%ld:" \
    "sector=%u:window=%u:CCR1=%u:CCR2=%u:CCR3=%u:ADC_STATUS=%u:" \
    "FAULT=%d:FAULT_R=%d:FAIL=%d:RUN=%d:em_stop1=%u:em_stop2=%u\r\n"

#endif /* TELEMETRY_FORMAT_H */
