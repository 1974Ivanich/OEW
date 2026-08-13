/* CORDIC_Modulus: boundary-тесты нормализации (hosted).
 *
 * Проверяет дефект из ревью: (x<<31)/max при x == max давал 2^31 →
 * INT32_MIN — знак компоненты инвертировался (угол уходил на ~180°).
 * Также ловит UB при x = INT32_MIN (-INT32_MIN переполняет int32_t).
 *
 * Аппаратный CORDIC заменяется структурой в памяти (мок CORDIC-регистров):
 * cordic_math.c пишет аргументы в CORDIC->WDATA — проверяем, ЧТО ушло
 * в «железо» (после двух записей WDATA хранит второй аргумент = yn).
 * RDATA = 0 → mod/angle вернут 0, это не важно для проверки входов.
 */
#include <stdio.h>
#include <stdint.h>
#include "stm32g474xx.h"   /* мок из tests/mocks — CORDIC_TypeDef */
#include "cordic_math.h"

/* Мок регистров CORDIC вместо аппаратного адреса. */
static CORDIC_TypeDef mock_cordic_regs;
static RCC_TypeDef mock_rcc;
#undef CORDIC
#define CORDIC (&mock_cordic_regs)
#define RCC (&mock_rcc)

/* Включаем реализацию напрямую: она использует CORDIC->CSR/WDATA/RDATA. */
#include "../src/cordic_math.c"

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (cond) { printf("  [PASS] %s\n", msg); } \
    else      { printf("  [FAIL] %s\n", msg); failures++; } \
} while (0)

static void test_q31_normalize(void) {
    printf("--- q31_normalize boundaries ---\n");
    CHECK(q31_normalize(INT32_MAX, INT32_MAX) == INT32_MAX,
          "v=max_abs -> INT32_MAX (раньше INT32_MIN!)");
    CHECK(q31_normalize(-INT32_MAX, INT32_MAX) == -INT32_MAX,
          "v=-max_abs -> -INT32_MAX");
    CHECK(q31_normalize(INT32_MIN, (int64_t)INT32_MAX + 1) == -INT32_MAX,
          "v=INT32_MIN, max=2^31 -> -2147483647 (без UB)");
    CHECK(q31_normalize(0, 12345) == 0,
          "v=0 -> 0");
    CHECK(q31_normalize(5, 10) == INT32_MAX / 2,
          "v=max/2 -> INT32_MAX/2");
    CHECK(q31_normalize(100, 1) == INT32_MAX,
          "v>max -> clamped INT32_MAX");
    CHECK(q31_normalize(-100, 1) == INT32_MIN,
          "v<-max -> clamped INT32_MIN");
}

static void test_modulus_inputs(void) {
    int32_t mod = 0, angle = 0;
    printf("--- CORDIC_Modulus: что уходит в WDATA (второй аргумент yn) ---\n");

    /* (0, INT32_MAX): до фикса yn = (INT32_MAX<<31)/INT32_MAX = 2^31 → INT32_MIN */
    mock_cordic_regs.CSR = 0;
    CORDIC_Modulus(0, INT32_MAX, &mod, &angle);
    CHECK((int32_t)mock_cordic_regs.WDATA == INT32_MAX,
          "(0, INT32_MAX): yn == +INT32_MAX (раньше INT32_MIN, угол 180°)");

    /* (0, INT32_MIN): -INT32_MIN — UB до фикса; после: yn == -INT32_MAX */
    mock_cordic_regs.CSR = 0;
    CORDIC_Modulus(0, INT32_MIN, &mod, &angle);
    CHECK((int32_t)mock_cordic_regs.WDATA == -INT32_MAX,
          "(0, INT32_MIN): yn == -2147483647, без UB");

    /* (INT32_MAX, INT32_MAX): 45° — обе компоненты равны max (оба знака!);
     * yn (второй аргумент) должен остаться +INT32_MAX. */
    mock_cordic_regs.CSR = 0;
    CORDIC_Modulus(INT32_MAX, INT32_MAX, &mod, &angle);
    CHECK((int32_t)mock_cordic_regs.WDATA == INT32_MAX,
          "(max, max): yn == +INT32_MAX (раньше оба компонента инвертировались)");

    /* Вызовы не должны крашиться (UB INT32_MIN в |x|) — просто доживаем. */
    mock_cordic_regs.CSR = 0;
    CORDIC_Modulus(INT32_MIN, 0, &mod, &angle);
    mock_cordic_regs.CSR = 0;
    CORDIC_Modulus(INT32_MAX, 0, &mod, &angle);
    printf("  [PASS] (INT32_MIN,0) и (INT32_MAX,0) не уронили процесс\n");
}

int main(void) {
    printf("=== CORDIC Modulus boundary tests ===\n");
    test_q31_normalize();
    test_modulus_inputs();
    printf("=== %s (%d failures) ===\n", failures == 0 ? "ALL PASS" : "FAILED", failures);
    return failures == 0 ? 0 : 1;
}
