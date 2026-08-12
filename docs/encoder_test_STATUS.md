# Результат: encoder logic test (Gemini web AI, 2026-08-12)

## Статус: код теста получен от Gemini в чате web AI, но НЕ сохранён в репо.
Вывод прогона (13/13 PASS) подтверждён Gemini.

## Что нужно сделать на ПК-2 (или когда вернётся пользователь):
1. Взять полный код `tests/encoder_logic_test.c` из ответа Gemini (был в чате
   web AI, копировался вместе с этим выводом).
2. Положить в `tests/encoder_logic_test.c`.
3. Собрать и прогнать:
   gcc -I tests/mocks tests/encoder_logic_test.c -o tests/enc_test_hosted.exe
   ./tests/enc_test_hosted.exe
4. Ожидаемый результат — ниже. Закоммитить.

## Вывод Gemini (эталон):
```
=== encoder logic test ===
  [PASS] forward: got=42
  [PASS] backward: got=-24
  [PASS] wrap_forward: got=3175
  [PASS] wrap_backward: got=-3554
  [PASS] bad_period: got=2
  [PASS] recovery_bad_period: got=42
  [PASS] pulse_gt_period: got=2
  [PASS] timeout: got=1
  [PASS] recovery_timeout_1: got=0
  [PASS] recovery_timeout_2: got=42
  [PASS] iir_stability: got=336
  [PASS] angle_50: got=8192
  [PASS] angle_25: got=4096

=== ALL PASS (13 checks, 0 failed) ===
```

## ТЗ для генерации (если нужно перегенерировать):
`TZ_for_web_AI_encoder_test.md` в корне репо — полное ТЗ с готовым моком TIM2,
12+ проверок и формулами ожиданий.

## ВАЖНО про мок (из ТЗ):
- Тест инклюдит `#include "../src/encoder.c"` ВНУТРЬ себя
- Мок TIM2: struct + `#define TIM2 (&tim2_mock)` + заглушки NVIC
- Перед каждым вызовом TIM2_IRQHandler() ставить TIM2->SR = TIM_SR_CC1IF
- Проверять ТОЛЬКО observable: ENC_GetSpeed_rpm()/ENC_GetAngle14()/ENC_GetError()
- НЕ менять src/encoder.c (продакшн)
