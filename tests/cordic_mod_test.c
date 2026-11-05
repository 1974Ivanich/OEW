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
 *
 * Дополнительно (11.2026): проверяются ВОЗВРАЩАЕМЫЕ значения Sqrt/Sin/Cos/SinCos
 * и насыщение — раньше тест смотрел только на аргументы в WDATA, и мутация
 * res <<= 2 в CORDIC_Sqrt проходила незамеченной.
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

static void test_init(void) {
    printf("--- CORDIC_Init: тактирование и сброс CSR ---\n");
    mock_rcc.AHB1ENR = 0;
    mock_cordic_regs.CSR = 0xFFFFFFFFu;
    CORDIC_Init();
    CHECK((mock_rcc.AHB1ENR & RCC_AHB1ENR_CORDICEN) != 0,
          "Init: включено тактирование CORDIC (RCC_AHB1ENR.CORDICEN)");
    CHECK(mock_cordic_regs.CSR == 0,
          "Init: CSR сброшен в 0 (прерывания и DMA выключены)");
}

static void test_result_values(void) {
    int32_t mod = 0, angle = 0;
    int32_t s = 0, c = 0;
    printf("--- возвращаемые значения (не только входы WDATA) ---\n");

    /* Sqrt: RDATA=0.25 -> 0.5 (res <<= 1). Ловит сдвиг на 2 и порчу масштаба. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x20000000;
    CHECK(CORDIC_Sqrt(0x40000000) == 0x40000000,
          "Sqrt: RDATA=0.25 -> 0.5 (res<<1)");
    CHECK((int32_t)mock_cordic_regs.WDATA == 0x20000000,
          "Sqrt: в WDATA ушло x/2");

    /* Sqrt: RDATA=0x3FFFFFFF (ровно на границе) -> 0x7FFFFFFE. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x3FFFFFFF;
    CHECK(CORDIC_Sqrt(0x7FFFFFFE) == 0x7FFFFFFE,
          "Sqrt: граница 0x3FFFFFFF не насыщается перед сдвигом");

    /* Sqrt: RDATA выше границы -> насыщение в INT32_MAX. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x40000000;
    CHECK(CORDIC_Sqrt(0x7FFFFFFE) == INT32_MAX,
          "Sqrt: RDATA>0x3FFFFFFF -> насыщение INT32_MAX");

    /* Sqrt: кодировка CSR (PRECISION=6, NARGS/NRES, SCALE=1, FUNC=9) и граница x=1. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x20000000;
    (void)CORDIC_Sqrt(0x40000000);
    CHECK(mock_cordic_regs.CSR == ((6U << 4) | (1U << 8) | 9U),
          "Sqrt: CSR = PRECISION=6, SCALE=1, FUNC=9 (NARGS/NRES=0 для sqrt)");
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x20000000;
    CHECK(CORDIC_Sqrt(1) == 0x40000000,
          "Sqrt: x=1 не считается неположительным (граница <=)");

    /* Sqrt: неположительный вход — ранний выход, WDATA не трогаем. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.WDATA = 0xDEADBEEF;
    mock_cordic_regs.RDATA = 0x20000000;
    CHECK(CORDIC_Sqrt(0) == 0 && CORDIC_Sqrt(-5) == 0,
          "Sqrt: x<=0 -> 0");
    CHECK((int32_t)mock_cordic_regs.WDATA == 0xDEADBEEF,
          "Sqrt: при x<=0 запись в WDATA не выполняется");

    /* Sin/Cos: q1.31 -> Q15 сдвигом >>16. Ловит порчу сдвига и знака. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x00010000;
    CHECK(CORDIC_Sin(0) == 1, "Sin: RDATA=0x10000 -> 1");
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = (int32_t)0xFFFF0000;
    CHECK(CORDIC_Sin(0) == -1, "Sin: RDATA=-0x10000 -> -1");
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x00030000;
    CHECK(CORDIC_Cos(0) == 3, "Cos: RDATA=0x30000 -> 3");

    /* SinCos: обе компоненты из RDATA мока, обе обязаны быть переведены в Q15. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x00020000;
    CORDIC_SinCos(0, &s, &c);
    CHECK(s == 2 && c == 2, "SinCos: обе компоненты 0x20000 -> 2");

    /* Modulus: восстановление масштаба mod = mod_raw*max_v>>31. */
    mock_cordic_regs.CSR = 0;
    mock_cordic_regs.RDATA = 0x40000000;
    CORDIC_Modulus(INT32_MAX, 0, &mod, &angle);
    CHECK(mod == 0x3FFFFFFF,
          "Modulus: mod_raw=0x40000000, max_v=INT32_MAX -> 0x3FFFFFFF");
}

int main(void) {
    printf("=== CORDIC Modulus boundary tests ===\n");
    test_q31_normalize();
    test_modulus_inputs();
    test_result_values();
    test_init();
    printf("=== %s (%d failures) ===\n", failures == 0 ? "ALL PASS" : "FAILED", failures);
    return failures == 0 ? 0 : 1;
}
