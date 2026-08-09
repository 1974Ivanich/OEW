#include "cordic_math.h"
#include "stm32g474xx.h"

/*
 * CORDIC на STM32G4 — Reference Manual RM0440, секция 17.
 *
 * ВНИМАНИЕ: CORDIC — один аппаратный accelerator, не поддерживает
 * одновременные вызовы из разных контекстов. Все вызовы CORDIC_*
 * должны выполняться из одного контекста (FOC ISR) либо сериализованы.
 * Конкуренция между main и ISR приведёт к конфликту конфигурации CSR.
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

/* PRECISION: рекомендованный RM0440 диапазон 3..6 циклов (12..24 итераций).
 * 6 циклов = 24 итерации — максимальная точность Q1.31 для sin/cos в FOC. */
#define CORDIC_PRECISION_BITS      4U
#define CORDIC_PRECISION_VALUE     6U   /* 6 циклов = 24 итерации, ошибка 2^-22 */

/* ARGSIZE / RESSIZE = 0 (32-bit, q1.31) */
/* NARGS = 1 (два аргумента), NRES = 1 (два результата) — для функций с двумя аргументами */
#define CORDIC_CSR_NARGS_BIT       (1U << 20)
#define CORDIC_CSR_NRES_BIT        (1U << 19)

void CORDIC_Init(void) {
    /* Тактирование CORDIC на AHB1 (RM0440, RCC_AHB1ENR.CORDICEN) */
    RCC->AHB1ENR |= RCC_AHB1ENR_CORDICEN;
    /* Dummy read для синхронизации после включения тактирования */
    (void)CORDIC->CSR;
    /* Запрещаем прерывания и DMA — используем polling/zero-overhead */
    CORDIC->CSR = 0;
}

/*
 * CORDIC работает в zero-overhead режиме (AN5325):
 *
 * После записи необходимого количества аргументов в WDATA
 * аппаратно запускается вычисление.
 *
 * Чтение RDATA блокируется аппаратно до готовности
 * соответствующего результата, поэтому отдельный polling
 * RRDY не требуется.
 *
 * NARGS=1 (два аргумента) — для Cosine, Sine, Phase, Modulus.
 * NARGS=0 (один аргумент) — для остальных (Atan, Sqrt и т.д.).
 */
static void cordic_write_two_args(int32_t arg1, int32_t arg2, uint32_t func) {
    /* Конфигурация CSR: PRECISION=6, NARGS=1, NRES=1, 32-bit (q1.31), функция.
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
 * Sqrt: √x в формате q1.31.
 * Input:  q1.31, значение x ∈ [0, 1) — т.е. arg ∈ [0, 0x7FFFFFFF].
 * Output: q1.31, результат √x.
 *
 * RM0440: CORDIC sqrt с SCALE=1 (n=1):
 *   Вход:  ARG1 = x/2  (в q1.31)
 *   CORDIC: RES1 = sqrt(ARG1·2^n) / 2^n = sqrt(x/2·2) / 2 = sqrt(x) / 2
 *   Результат: RES1 · 2 = sqrt(x)
 *
 * Проверка: x=0.5 (0x40000000) → write 0x20000000 →
 *   CORDIC: sqrt(0.25·2)/2 = sqrt(0.5)/2 ≈ 0.354 →
 *   shift: 0.707 ≈ sqrt(0.5) ✓
 *
 * FUNC=9 (Square root), NARGS=0 (один аргумент), NRES=0 (один результат).
 * SCALE field: биты 10:8, значение 1.
 */
int32_t CORDIC_Sqrt(int32_t x_q31) {
    if (x_q31 <= 0) return 0;
    CORDIC->CSR = (CORDIC_PRECISION_VALUE << CORDIC_PRECISION_BITS) |
                  (1U << 8) |              /* SCALE = 1 */
                  CORDIC_FUNC_SQRT;
    CORDIC->WDATA = (uint32_t)(x_q31 >> 1);  /* x / 2 */
    int32_t res = (int32_t)CORDIC->RDATA;    /* √x / 2 */
    /* Overflow protection: √x ≤ 1.0 → √x/2 ≤ 0.5 → res ≤ 0x40000000.
     * Но при граничных значениях добавляем явную проверку. */
    if (res > 0x3FFFFFFF) return 0x7FFFFFFF;  /* saturate */
    res <<= 1;                               /* √x */
    if (res > 0x7FFFFFFF) res = 0x7FFFFFFF;  /* saturate */
    return res;
}

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
 * Modulus: √(x²+y²) и atan2(y,x) за одну операцию CORDIC.
 *
 * Входы x/y в q1.31. Если |v| = √(x²+y²) > 1.0,
 * RES1 насыщается к +1.0 (RM0440, ST training).
 * Поэтому входы нормализуются: делим на max(|x|,|y|),
 * затем результат умножаем на тот же масштаб.
 *
 * RES1 = modulus (q1.31), RES2 = phase/π (q1.31).
 */
void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle) {
    int32_t ax = (x >= 0) ? x : -x;
    int32_t ay = (y >= 0) ? y : -y;
    int32_t max_v = (ax > ay) ? ax : ay;
    if (max_v <= 0) {
        *mod = 0;
        *angle = 0;
        return;
    }
    /* Нормализация в полный Q1.31: x,y → [-1, +1], |v| ≤ √2.
     * <<31 использует все 31 значащих бита CORDIC (вместо ~15 при <<15).
     * Масштаб сокращается: k=2^31/max_v, mod_raw=k·|v|, result=mod_raw·max_v/2^31. */
    int32_t xn = (int32_t)(((int64_t)x << 31) / max_v);
    int32_t yn = (int32_t)(((int64_t)y << 31) / max_v);
    cordic_write_two_args(xn, yn, CORDIC_FUNC_MODULUS);
    int32_t mod_raw, angle_raw;
    cordic_read_two(&mod_raw, &angle_raw);
    /* Восстановление масштаба: mod = mod_raw · max_v / 2^31 */
    *mod = (int32_t)(((int64_t)mod_raw * max_v) >> 31);
    *angle = angle_raw;
}
