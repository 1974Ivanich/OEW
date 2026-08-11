/* Заглушки V/f-функций для FOC-теста (линкуется ТОЛЬКО с foc_math_test.c).
 * V/f-тест НЕ линкует этот файл — там реальный src/vf_control.c. */
#include <stdint.h>

int VFC_IsRunning(void) { return 0; }
void VFC_Stop(void) { }
