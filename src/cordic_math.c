#include "cordic_math.h"
#include "stm32g474xx.h"

/*
 * CORDIC на STM32G4 — Reference Manual RM0440, секция 17.
 *
 * Регистр CORDIC_CSR (битовые поля по RM0440 стр.475-477):
 *   бит 31  RRDY      — 1 = результат готов, 0 = нет
 *   бит 22  ARGSIZE   — 0 = 32-bit (q1.31), 1 = 16-bit (q1.15)
 *   бит 21  RESSIZE   — 0 = 32-bit (q1.31), 1 = 16-bit (q1.15)
 *   бит 20  NARGS     — 0 = один аргумент, 1 = два аргумента
 *   бит 19  NRES      — 0 = один результат, 1 = два результата
 *   бит 18  DMAWEN
 *   бит 17  DMAREN
 *   бит 16  IEN
 *   биты 10:8 SCALE[2:0]
 *   биты 7:4  PRECISION[3:0]  — 0 = reserved, 1..15 = (итераций)/4
 *   биты 3:0  FUNC[3:0]
 *
 * FUNC (RM0440 Table 105/стр.477):
 *   0 = Cosine        (ARG1=angle, ARG2=modulus; RES1=m·cos, RES2=m·sin)
 *   1 = Sine          (ARG1=angle, ARG2=modulus; RES1=m·sin, RES2=m·cos)
 *   2 = Phase         (ARG1=x, ARG2=y; RES1=atan2(y,x)/π, RES2=modulus)
 *   3 = Modulus       (ARG1=x, ARG2=y; RES1=modulus, RES2=phase/π)
 *   4 = Arctangent
 *   5 = Hyperbolic cosine (Cosh)  — НЕ Modulus, не путать!
 *   ...
 *   9 = Square root
 */

/* FUNC коды (биты 3:0) */
#define CORDIC_FUNC_COSINE         0U
#define CORDIC_FUNC_SINE           1U
#define CORDIC_FUNC_PHASE          2U
#define CORDIC_FUNC_MODULUS        3U   /* √(x²+y²) */
#define CORDIC_FUNC_ARCTANGENT     4U
#define CORDIC_FUNC_COSH           5U   /* hyperbolic cosine */
#define CORDIC_FUNC_SINH           6U
#define CORDIC_FUNC_ATANH          7U
#define CORDIC_FUNC_LN             8U
#define CORDIC_FUNC_SQRT           9U

/* PRECISION: рекомендованный RM0440 диапазон 3..6 циклов (12..24 итераций) */
#define CORDIC_PRECISION_BITS      4U
#define CORDIC_PRECISION_VALUE     5U   /* 5 циклов = 20 итераций, ошибка 2^-18 */

/* ARGSIZE / RESSIZE = 0 (32-bit, q1.31) */
/* NARGS = 1 (два аргумента), NRES = 1 (два результата) — для функций с двумя аргументами */
#define CORDIC_CSR_NARGS_BIT       (1U << 20)
#define CORDIC_CSR_NRES_BIT        (1U << 19)

/* Маска для очистки битов CSR перед записью новых настроек */
#define CORDIC_CSR_CONFIG_MASK     (0x07U | CORDIC_CSR_NARGS_BIT | CORDIC_CSR_NRES_BIT | \
                                    CORDIC_CSR_RESSIZE | CORDIC_CSR_ARGSIZE | \
                                    (0x7U << 8) | (0xFU << 4))

void CORDIC_Init(void) {
    /* Тактирование CORDIC на AHB1 (RM0440, RCC_AHB1ENR.CORDICEN) */
    RCC->AHB1ENR |= RCC_AHB1ENR_CORDICEN;
    /* Dummy read для синхронизации после включения тактирования */
    (void)CORDIC->CSR;
    /* Запрещаем прерывания и DMA — используем polling/zero-overhead */
    CORDIC->CSR = 0;
}

/*
 * Zero-overhead write: запись WDATA автоматически ждёт завершения предыдущей
 * операции (если RDATA ещё не прочитан — запуск новой операции будет
 * заблокирован аппаратно). Поэтому никаких while(CSR) не нужно.
 *
 * NARGS=1 (два аргумента) — для Cosine, Sine, Phase, Modulus.
 * NARGS=0 (один аргумент) — для остальных (Atan, Cosh и т.д.).
 */
static void cordic_write_two_args(int32_t arg1, int32_t arg2, uint32_t func) {
    /* Конфигурация CSR: PRECISION=5, NARGS=1, NRES=1, 32-bit (q1.31), функция.
     * ARGSIZE=0 / RESSIZE=0 — 32-битные аргументы/результаты (RM0440):
     * при NARGS=1 требуется ДВЕ записи WDATA, при NRES=1 — ДВА чтения RDATA.
     * (ARGSIZE=1/RESSIZE=1 упаковали бы оба значения в одно 16-битное слово
     * и две записи рассинхронизировали бы FIFO.) */
    CORDIC->CSR = (CORDIC_PRECISION_VALUE << CORDIC_PRECISION_BITS) |
                  CORDIC_CSR_NARGS_BIT | CORDIC_CSR_NRES_BIT |
                  (func & 0x0FU);
    /* Запись двух аргументов: первым ARG1, затем ARG2 */
    CORDIC->WDATA = (uint32_t)arg1;
    CORDIC->WDATA = (uint32_t)arg2;
}

/*
 * Read двух результатов: первое чтение = RES1, второе = RES2.
 * Чтение RDATA автоматически вставляет wait-states, пока CORDIC не закончит.
 * После второго чтения RRDY сбрасывается аппаратно.
 */
static void cordic_read_two(int32_t *res1, int32_t *res2) {
    *res1 = (int32_t)CORDIC->RDATA;
    *res2 = (int32_t)CORDIC->RDATA;
}

/* ── Публичные функции ─────────────────────────────────────────────────── */

/*
 * Sin: используем FUNC=1 (Sine), где первое чтение = sin, второе = cos.
 * angle_q31: угол в формате q1.31, где 0x7FFFFFFF = π.
 *            Допустимый диапазон: [-π, π] (т.е. [-0x80000000, 0x7FFFFFFF]).
 *            Для углов вне диапазона — нужно предварительно привести
 *            через atan2, либо нормализовать в main коде.
 */
int32_t CORDIC_Sin(int32_t angle_q31) {
    int32_t s, c;
    /* Modulus m = 1.0 в q1.31 = 0x7FFFFFFF */
    cordic_write_two_args(angle_q31, 0x7FFFFFFF, CORDIC_FUNC_SINE);
    cordic_read_two(&s, &c);
    /* q1.31 → Q15: результат в [-32768..32767] для целочисленной FOC-математики */
    return s >> 16;
}

int32_t CORDIC_Cos(int32_t angle_q31) {
    int32_t c, s;
    /* FUNC=Cosine: RES1 = m·cos, RES2 = m·sin (RM0440 Table 105) */
    cordic_write_two_args(angle_q31, 0x7FFFFFFF, CORDIC_FUNC_COSINE);
    cordic_read_two(&c, &s);
    return c >> 16;
}

/*
 * SinCos: один вызов CORDIC — RES1=sin, RES2=cos (FUNC=Sine).
 * В 2 раза быстрее раздельных CORDIC_Sin + CORDIC_Cos.
 * Результаты в Q15 [-32768..32767].
 */
void CORDIC_SinCos(int32_t angle_q31, int32_t *sin_q15, int32_t *cos_q15) {
    int32_t s, c;
    cordic_write_two_args(angle_q31, 0x7FFFFFFF, CORDIC_FUNC_SINE);
    cordic_read_two(&s, &c);
    *sin_q15 = s >> 16;
    *cos_q15 = c >> 16;
}

/*
 * Atan2(y, x): возвращает угол в q1.31 (где 0x7FFFFFFF = π).
 * Если нужны оба значения (atan2 + modulus) за раз — использовать Phase.
 */
int32_t CORDIC_Atan2(int32_t y, int32_t x) {
    /* Arctangent: ARG1 = x, результат = atan(x) (без знака y).
     * Но нам нужен atan2(y,x) — используем Phase и читаем RES1 (angle). */
    cordic_write_two_args(x, y, CORDIC_FUNC_PHASE);
    int32_t angle, mod;
    cordic_read_two(&angle, &mod);
    return angle;
}

/*
 * Modulus: возвращает √(x²+y²) в q1.31 (saturation к 1.0).
 * Параллельно возвращает угол через angle_q31.
 */
void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle) {
    /* Modulus: ARG1=x, ARG2=y; RES1=modulus, RES2=phase */
    cordic_write_two_args(x, y, CORDIC_FUNC_MODULUS);
    cordic_read_two(mod, angle);
}
