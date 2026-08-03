# DeepSeek (chat.deepseek.com) — ответ на ТЗ atan2_q31

## Код (получен через браузер пользователя)

```c
#include <stdint.h>

#define Q31_ONE     0x7FFFFFFF
#define Q31_HALF    0x40000000
#define Q31_PI      0x7FFFFFFF
#define Q31_PI2     0x3FFFFFFF
#define Q31_PI4     0x1FFFFFFF
#define Q31_TWO31   0x80000000  //  2^31 (как беззнаковое)

static int32_t atan_q31(int32_t t)
{
    if (t < 0) t = -t;
    if (t >= Q31_ONE) return Q31_PI4;

    int64_t z;
    int32_t use_pi4 = 0;

    if (t <= Q31_HALF) {
        z = t;
    } else {
        // t > 0.5  →  atan(t) = π/4 + atan((t-1)/(1+t))
        int64_t T = t;
        int64_t num = T - Q31_TWO31;          // T - 2^31   <-- НЕВЕРНО
        int64_t den = Q31_TWO31 + T;          // 2^31 + T   <-- НЕВЕРНО
        int64_t u_q31 = (num * Q31_TWO31) / den;
        z = -u_q31;
        use_pi4 = 1;
    }

    int64_t z2 = (z * z) >> 31;
    int64_t z3 = (z2 * z) >> 31;
    int64_t z5 = (z3 * z2) >> 31;
    int64_t z7 = (z5 * z2) >> 31;
    int64_t z9 = (z7 * z2) >> 31;

    int64_t res = z - z3/3 + z5/5 - z7/7 + z9/9;

    if (use_pi4) {
        res = (int64_t)Q31_PI4 - res;
    }
    return (int32_t)res;
}

int32_t atan2_q31(int32_t y, int32_t x)
{
    if (x == 0 && y == 0) return 0;
    if (x == 0) return (y > 0) ? Q31_PI2 : (int32_t)(-Q31_PI2);
    if (y == 0) return (x > 0) ? 0 : Q31_PI;

    uint32_t ax = (x < 0) ? (uint32_t)(-(int64_t)x) : (uint32_t)x;
    uint32_t ay = (y < 0) ? (uint32_t)(-(int64_t)y) : (uint32_t)y;

    int32_t base;
    if (ax >= ay) {
        int32_t t = (int32_t)(((uint64_t)ay << 31) / ax);
        base = atan_q31(t);
    } else {
        int32_t t = (int32_t)(((uint64_t)ax << 31) / ay);
        int32_t at = atan_q31(t);
        base = (int32_t)((int64_t)Q31_PI2 - at);
    }

    if (x > 0 && y > 0) return base;
    else if (x < 0 && y > 0) return (int32_t)((int64_t)Q31_PI - base);
    else if (x < 0 && y < 0) return (int32_t)((int64_t)base - Q31_PI);
    else return -base;
}
```

## Численная проверка (имитация int32/int64)

| Метрика | Результат |
|---------|-----------|
| Макс. ошибка | **325.7°** (при 151°) |
| Ошибка > 2° | почти во всех точках |
| Ошибка > 55° | систематически (51°, 116°, 26°, −64°, −154°) |

## 🔴 Критический баг: знак `Q31_TWO31`

```c
#define Q31_TWO31   0x80000000   // как int32 это -2147483648, НЕ +2^31!
```

`0x80000000` в **знаковом** int32/int64 = **−2^31**, а не 2^31. Поэтому:

```c
int64_t num = T - Q31_TWO31;   // T - (-2^31) = T + 2^31   (нужно T - 2^31)
int64_t den = Q31_TWO31 + T;   // -2^31 + T = T - 2^31    (нужно T + 2^31)
```

Формула `u = (t-1)/(1+t)` превратилась в `(t+1)/(t-1)` — знаки перепутаны. Для t > 0.5 (угол > 26.6°) всё ломается. Ветка t ≤ 0.5 работает, но в atan2 при 45° уже задействована битая ветка.

## Оценка

- ✅ Архитектура: октантная редукция + ряд Тейлора до z^9 — **правильная идея**
- ✅ Особые случаи (x==0, y==0) — корректны
- ✅ Использование uint64 для деления без переполнения — верно
- 🔴 **Q31_TWO31 = 0x80000000 в знаковом типе = −2^31** → перевёрнутые знаки в формуле приведения
- 🔴 Ряд Тейлора обрезан на z^9: остаток ~z^11/11 при |z|≤0.5 даёт до 0.003° — приемлемо, но не заявленные 0.1°
- 🔴 `Q31_PI2 = 0x3FFFFFFF` — это не π/2 точно (π/2·2^31 = 0x40000000), погрешность постоянная
