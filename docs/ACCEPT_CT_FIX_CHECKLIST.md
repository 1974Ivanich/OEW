# Приёмка ветки `ai-bench/ct-fix` — чек-лист

**Что принимается:** фикс калибровки токовых оффсетов при прямом подключении CT (Ires/PA6 без ОУ):
- честный FAIL в CLI `c` (`2c0cc09`);
- Путь B: `src/adc.c` `adc_ct_sample_is_usable()` (`e21d72b`) — нулевой уровень Ires валиден, верхняя рейка — насыщение;
- тест `tests/adc_frame_host_test.c` обновлён под новую семантику;
- документация: `TZ_CT_CHANNEL_CALIBRATION_FIX.md` (статус исполнения), `docs/BENCH_SESSION_20260823.md`.

**Ветка:** `ai-bench/ct-fix` → HEAD `519c1ba` (SHA подтверждён `git ls-remote`).
**Эталон:** `origin/main` (95d132a).

---

## 0. Подготовка worktree (регламент §3)

```bash
git fetch origin
git worktree add /tmp/accept_ct_fix origin/ai-bench/ct-fix
cd /tmp/accept_ct_fix
```

## 1. Проверки

### 1.1 Merge-base
```bash
git merge-base origin/ai-bench/ct-fix origin/main
# должен указывать на 95d132a (или рядом; ветка создана от него)
git rev-list --count origin/main..origin/ai-bench/ct-fix   # = количеству коммитов пакета
```

### 1.2 Production build (default-deny)
```bash
make
# Ожидание: Build complete, text ~67900-68100 B
```

### 1.3 Hosted + QEMU + pytest (все)
```bash
make test
# ВАЖНО: adc_frame_host_test должен пройти с новой семантикой (ires=0 → OK/VALID)
```

### 1.4 Commissioning build
```bash
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"
# PASS
```

### 1.5 Diff границы (safety)
```bash
git diff origin/main...HEAD --stat
# должно быть: src/adc.c, tests/adc_frame_host_test.c, docs/...
# НЕ должно быть: src/protect.c, src/pwm.c, .ioc
git diff origin/main...HEAD --check   # чисто
```

### 1.6 Переводы строк
- `.c/.h` — CRLF в рабочем дереве (Windows), `.py/.md/.yml` — LF.

### 1.7 CI
- https://github.com/1974Ivanich/OEW/actions — `build-test` для `ai-bench/ct-fix` **зелёный**.

## 2. Стендовое подтверждение (no-HV, по желанию, если доступен ПК-1)

```text
make flash
c    → @ADC:CAL:offset_i1≈2040:offset_i2≈2068:offset_ires=0   (SUCCESS)
1    → @FOC:START:FAIL:rc=-2 (map_unverified)                  (fail-closed)
p?   → @PWM:CR1=224:CCER=0                                     (PWM off)
```

## 3. Merge

```bash
git checkout main && git pull origin main
git merge origin/ai-bench/ct-fix
git push origin main
```

После merge:
- пометить старую ветку `ai4/adc-calibration-ct-boundary` как поглощённую (она несёт тот же `e73b8f0`; пакет не дублировать).
- обновить `docs/AGENTS_STATUS.md` (статус «влит в main»).

## 4. Запреты при приёмке

- Не вносить правки в пакет (только приёмка/отказ с комментарием).
- Не ослаблять fail-closed (protect/pwm/adc_frame_status для I1/I2/VBUS).
- Не менять `.ioc`.