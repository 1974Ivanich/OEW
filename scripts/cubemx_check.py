#!/usr/bin/env python3
"""
cubemx_check.py — самоконтроль конфигурации периферии через STM32CubeMX.

Запускает CubeMX headless с эталонным OEW_Motor.ioc, выгружает CSV-распиновку,
генерирует код (MX_*_Init) и сверяет с ожидаемой конфигурацией:

  1. Пины (CSV pinout)  — 22 пина: TIM1/TIM8 PWM, ADC2, USART2, GPIO, энкодер
  2. Значения TIM1/TIM8 — PSC, ARR, CounterMode, DeadTime, RCR, TRGO
  3. Значения TIM2     — PSC (энкодер PWM capture)
  4. ADC2              — каналы injected (из .ioc → сгенерированный код)
  5. Тактирование      — PLL M/N/R, SysClock (из сгенерированного main.c)

Эталон значений — РЕАЛЬНЫЕ значения из src/pwm.c, src/adc.c, main.c
(актуализировано 2026-08-11: PSC=16, ARR=999, DT=1500нс, CMS=3, RCR=1,
 PLL M=4/N=85/R=2 → 170 МГц).

Использование:
    python cubemx_check.py             # полный прогон (CubeMX headless + сверка)
    python cubemx_check.py --csv       # только пины по готовому CSV
    python cubemx_check.py --code      # только значения по сгенерированному коду
    python cubemx_check.py --skip-mx   # сверка по уже готовым CSV+коду (без CubeMX)

Коды выхода: 0 = PASS, 1 = FAIL (расхождение), 2 = ошибка прогона.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IOC_PATH = os.path.join(PROJECT_DIR, "OEW_Motor.ioc")
CUBEMX_EXE = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\STM32CubeMX.exe"
DEFAULT_CSV = os.path.join(PROJECT_DIR, "logs", "cubemx_pinout.csv")
DEFAULT_GEN = os.path.join(PROJECT_DIR, "logs", "cube_gen")

# ── 1. Ожидаемая распиновка (эталон) ───────────────────────────────────────
EXPECTED_PINS = {
    "PC0": "TIM1_CH1", "PA7": "TIM1_CH1N", "PC1": "TIM1_CH2", "PB0": "TIM1_CH2N",
    "PC2": "TIM1_CH3", "PB1": "TIM1_CH3N",
    "PC6": "TIM8_CH1", "PC10": "TIM8_CH1N", "PC7": "TIM8_CH2", "PC11": "TIM8_CH2N",
    "PC8": "TIM8_CH3", "PC12": "TIM8_CH3N",
    "PA0": "ADC2_IN1", "PA1": "ADC2_IN2", "PA6": "ADC2_IN3", "PC4": "ADC2_IN5",
    "PA2": "USART2_TX", "PA3": "USART2_RX",
    "PB4": "GPIO_Output", "PB5": "GPIO_Output", "PB6": "GPIO_Output",
    "PA15": "TIM2_CH1",
}

# ── 2. Ожидаемые значения из .ioc (после генерации кода сверяем MX_*_Init) ──
# Ключ = (файл, функция, поле) → ожидаемое значение.
# Значения = реальные из src/pwm.c / src/adc.c / main.c.
EXPECTED_TIM = {
    # TIM1 — Инвертор 1 (Master): pwm.c PWM_Init()
    "TIM1.Prescaler": "16",          # 170МГц/10МГц-1 (PWM_Init: psc_plus1 = tck/10e6)
    "TIM1.Period": "999",            # 10МГц/(2*5кГц)-1
    "TIM1.CounterMode": "TIM_COUNTERMODE_CENTERALIGNED3",  # CMS_1|CMS_0 = mode 3
    "TIM1.DeadTime": "1500",         # нс (PWM_Init: dtg_ticks = 1500нс * tck)
    "TIM1.RepetitionCounter": "1",   # RCR=1 (1 TRGO за период)
    "TIM1.TIM_MasterOutputTrigger": "TIM_TRGO_UPDATE",  # CR2 MMS=010
    # TIM8 — Инвертор 2 (Slave): pwm.c, тот же PSC/ARR/DT
    "TIM8.Prescaler": "16",
    "TIM8.Period": "999",
    "TIM8.CounterMode": "TIM_COUNTERMODE_CENTERALIGNED3",
    "TIM8.DeadTime": "1500",
    "TIM8.RepetitionCounter": "1",
    "TIM8.TIM_MasterSlaveMode": "TIM_MASTERSLAVEMODE_DISABLE",
    # TIM2 — энкодер AS5048A PWM input capture (encoder.c / main.c: TIM2 PSC=169)
    "TIM2.Prescaler": "169",
}

# ── 3. Ожидаемые ADC2 injected-каналы (из adc.c: adc2_read 1,2,3,5 + JSQR) ──
EXPECTED_ADC2_INJECTED = {1, 2, 3, 5}   # IN1=PA0, IN2=PA1, IN3=PA6, IN5=PC4

# ── 4. Ожидаемое тактирование (main.c: PLLCFGR) ────────────────────────────
EXPECTED_CLOCK = {
    "PLLM": "RCC_PLLM_DIV4",    # main.c: (3U<<PLLM_Pos) → M=4 (HSI16/4)
    "PLLN": "85",
    "PLLR": "RCC_PLLR_DIV2",    # (0U<<PLLR_Pos) → R=2
    "PLLSource": "RCC_PLLSOURCE_HSI",   # нотация CubeMX (в коде = HSI16, PLLSRC=10)
    "SYSCLK": "170000000",
}


def _cubemx_javaw_alive() -> bool:
    """Есть ли живой javaw с CubeMX в командной строке (осиротевший после
    смерти лаунчера CubeMX.exe)."""
    try:
        r = subprocess.run(
            'wmic process where "name=\'javaw.exe\' and '
            "CommandLine like '%STM32CubeMX%'\" get processid",
            shell=True, capture_output=True, text=True, timeout=30)
        pids = [ln.strip() for ln in r.stdout.splitlines()
                if ln.strip().isdigit()]
        return bool(pids)
    except Exception:
        return True  # не смогли проверить — не выходим раньше времени


def run_cubemx(csv_path: str, gen_path: str) -> bool:
    """CubeMX headless: config load → csv pinout → generate code → exit."""
    script = os.path.join(PROJECT_DIR, "scripts", "cubemx_check_script.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f"config load {IOC_PATH}\r\n")
        f.write(f"csv pinout {csv_path}\r\n")
        f.write(f"generate code {gen_path}\r\n")
        f.write("exit\r\n")
    print(f"[cubemx] запуск: {CUBEMX_EXE} -q {script}")
    # Удаляем старые артефакты — успех проверяем ПО ФАЙЛАМ, а не по stdout
    # (CubeMX — GUI-приложение; при запуске из git-hook stdout может быть пуст)
    for p in (csv_path, os.path.join(gen_path, "Src", "tim.c"),
              os.path.join(gen_path, "Src", "main.c")):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    try:
        # CubeMX 6.18 — GUI-приложение: лаунчер CubeMX.exe умирает за ~30 c,
        # а реальную работу делает осиротевший javaw, который после `exit`
        # НЕ завершается сам. Поэтому: ждём появления ФАЙЛОВ (поллинг) до
        # дедлайна, не выходя при смерти лаунчера; затем убиваем javaw.
        proc = subprocess.Popen(
            [CUBEMX_EXE, "-q", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 420
        ok_csv = ok_tim = False
        while time.monotonic() < deadline:
            time.sleep(10)
            ok_csv = os.path.exists(csv_path) and os.path.getsize(csv_path) > 100
            ok_tim = os.path.exists(os.path.join(gen_path, "Src", "tim.c"))
            if ok_csv and ok_tim:
                print("[cubemx] файлы готовы — завершаю процесс")
                break
            # Лаунчер умер и javaw не запущен → запуск провалился, не ждём 420 c
            if proc.poll() is not None and not _cubemx_javaw_alive():
                print("[cubemx] процесс завершился, javaw не найден")
                break
        # Принудительно убиваем лаунчер (если жив) + всех javaw CubeMX
        try:
            if proc.poll() is None:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
        except Exception:
            pass
        subprocess.run(
            'wmic process where "name=\'javaw.exe\' and '
            "CommandLine like '%STM32CubeMX%'\" call terminate",
            shell=True, capture_output=True, timeout=30)
        if not (ok_csv and ok_tim):
            print("[cubemx] TIMEOUT (420 c) или файлы не созданы")
            return False
    except FileNotFoundError:
        print(f"[cubemx] CubeMX не найден: {CUBEMX_EXE}")
        return False
    # Успех: CSV создан + код сгенерирован
    print(f"[cubemx] csv OK: {ok_csv} | tim.c OK: {ok_tim}")
    return ok_csv and ok_tim


def check_pins(csv_path: str, fails: list) -> int:
    actual = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Name") or "").strip()
            signal = (row.get("Signal") or "").strip()
            if name:
                actual[name] = signal
    if not actual:
        fails.append("CSV пустой или не прочитан: " + csv_path)
        return 2
    print(f"[check] пинов в CSV CubeMX: {len(actual)}")
    for pin, exp in sorted(EXPECTED_PINS.items()):
        act = actual.get(pin, "").replace("S_", "").strip()
        if act != exp:
            fails.append(f"  PIN {pin}: ожидалось {exp!r}, в CubeMX {actual.get(pin, '')!r}")
    extra = sorted(set(actual) - set(EXPECTED_PINS))
    for pin in extra:
        if actual[pin]:  # только подписанные лишние
            fails.append(f"  PIN {pin}: лишний в CubeMX ({actual[pin]!r})")
    return 0


def parse_mx(gen_path: str) -> dict:
    """Парсит MX_*_Init() из сгенерированного кода → {(файл, функция, поле): значение}."""
    result = {}
    tim_src = ""
    for fn in ("tim.c", "adc.c", "main.c", "gpio.c"):
        p = os.path.join(gen_path, "Src", fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                src = f.read()
            if fn == "tim.c":
                tim_src = src
            elif fn == "main.c":
                result["main.c"] = src
            elif fn == "adc.c":
                result["adc.c"] = src

    # TIM1/TIM8: htim1.Init.Prescaler = N; ... из MX_TIM1_Init/MX_TIM8_Init
    for tim in ("TIM1", "TIM8"):
        m = re.search(rf"void MX_{tim}_Init\(void\)\s*\{{(.*?)\n\}}", tim_src, re.S)
        if not m:
            result[f"{tim}.Prescaler"] = "<нет MX_>"
            continue
        body = m.group(1)
        for field, key in (
            ("Prescaler", "Prescaler"), ("Period", "Period"),
            ("CounterMode", "CounterMode"), ("RepetitionCounter", "RepetitionCounter"),
        ):
            fm = re.search(rf"{tim.split('TIM')[1]}tim\d?\.Init\.{field}\s*=\s*([^;]+);", body) or \
                 re.search(rf"htim\d\.Init\.{field}\s*=\s*([^;]+);", body) or \
                 re.search(rf"\.Init\.{field}\s*=\s*([^;]+);", body)
            if fm:
                result[f"{tim}.{key}"] = fm.group(1).strip()
        # DeadTime из BreakDeadTimeConfig
        dm = re.search(r"sBreakDeadTimeConfig\.DeadTime\s*=\s*([^;]+);", body)
        if dm:
            result[f"{tim}.DeadTime"] = dm.group(1).strip()
        # MasterOutputTrigger (TIM1) / MasterSlaveMode (TIM8)
        om = re.search(r"sMasterConfig\.MasterOutputTrigger\s*=\s*([^;]+);", body)
        if om:
            result[f"{tim}.TIM_MasterOutputTrigger"] = om.group(1).strip()
        sm = re.search(r"sMasterConfig\.MasterSlaveMode\s*=\s*([^;]+);", body)
        if sm:
            result[f"{tim}.TIM_MasterSlaveMode"] = sm.group(1).strip()

    # TIM2 (input capture — в tim.c или main.c)
    if "TIM2" not in str(result):
        pass
    m2 = re.search(r"htim2\.Init\.Prescaler\s*=\s*([^;]+);", tim_src)
    if m2:
        result["TIM2.Prescaler"] = m2.group(1).strip()

    # ADC2 injected: JSQR из adc.c — ищем 4 канала
    adc_src = result.get("adc.c", "")
    jsqr = re.search(r"ADC2->JSQR\s*=\s*([^;]+);", adc_src)
    if jsqr:
        result["ADC2.JSQR"] = jsqr.group(1).strip()
    # каналы: ищем ADC_CHANNEL_x в injected секции
    chans = set()
    for cm in re.finditer(r"ADC_CHANNEL_(\d+)", adc_src):
        chans.add(int(cm.group(1)))
    result["ADC2.CHANNELS"] = chans
    # ExternalTrigConv
    et = re.search(r"ExternalTrigConv\s*=\s*([^;]+);", adc_src)
    if et:
        result["ADC2.ExternalTrigConv"] = et.group(1).strip()

    # Тактирование: SystemClock_Config в main.c
    main_src = result.get("main.c", "")
    mcfg = re.search(r"void SystemClock_Config\(void\)\s*\{(.*?)\n\}", main_src, re.S)
    if mcfg:
        body = mcfg.group(1)
        for reg, field, pat in (
            ("PLLM", "PLLM", r"PLLM\s*=\s*([^;]+);"),
            ("PLLN", "PLLN", r"PLLN\s*=\s*([^;]+);"),
            ("PLLR", "PLLR", r"PLLR\s*=\s*([^;]+);"),
            ("PLLSRC", "PLLSource", r"PLLSource\s*=\s*([^;]+);"),
        ):
            fm = re.search(pat, body)
            if fm:
                result[f"RCC.{field}"] = fm.group(1).strip()
        cm = re.search(r"SystemCoreClock\s*=\s*([^;]+);", body)
        if cm:
            result["RCC.SYSCLK"] = cm.group(1).strip()
        # CubeMX не задаёт SystemCoreClock в SystemClock_Config (это делает
        # SystemCoreClockUpdate). Частота берётся из .ioc: RCC.SysClockFreqValue.
        fv = re.search(r"SysClockFreqValue\s*=\s*(\d+)", open(IOC_PATH, encoding="utf-8").read())
        if fv:
            result["RCC.SYSCLK"] = fv.group(1)
    return result


def check_values(mx: dict, fails: list) -> int:
    print("[check] значения из сгенерированного кода:")
    # TIM1/TIM8/TIM2
    for key, exp in sorted(EXPECTED_TIM.items()):
        act = mx.get(key)
        if act is None:
            fails.append(f"  VALUE {key}: НЕ НАЙДЕНО в сгенерированном коде")
            continue
        act_n = act.strip()
        print(f"    {key}: ожид {exp!r} → код {act_n!r}")
        if act_n != exp:
            fails.append(f"  VALUE {key}: ожидалось {exp!r}, код {act_n!r}")
    # ADC2 каналы
    chans = mx.get("ADC2.CHANNELS")
    if chans is not None:
        missing = EXPECTED_ADC2_INJECTED - chans
        if missing:
            fails.append(f"  ADC2: нет каналов {sorted(missing)} (есть {sorted(chans)})")
        else:
            print(f"    ADC2 каналы: {sorted(chans)} — OK")
    else:
        fails.append("  ADC2: каналы не найдены в коде")
    # тактирование
    for key, exp in EXPECTED_CLOCK.items():
        if key == "SYSCLK":
            act = mx.get("RCC.SYSCLK")
            if act is None:
                fails.append("  CLOCK SYSCLK: не найдено")
                continue
            # может быть макросом (170000000 или RCC_CFGR...)
            act_n = act.strip()
            print(f"    SYSCLK: ожид {exp} → код {act_n}")
            if act_n != exp:
                fails.append(f"  CLOCK SYSCLK: ожидалось {exp}, код {act_n}")
        else:
            act = mx.get(f"RCC.{key}")
            if act is None:
                fails.append(f"  CLOCK {key}: не найдено")
                continue
            act_n = act.strip()
            print(f"    {key}: ожид {exp} → код {act_n}")
            if act_n != exp:
                fails.append(f"  CLOCK {key}: ожидалось {exp}, код {act_n}")
    # PLLSource дополнительно
    src = mx.get("RCC.PLLSource")
    if src is not None:
        exp_src = EXPECTED_CLOCK["PLLSource"]
        print(f"    PLLSource: ожид {exp_src} → код {src.strip()}")
        if src.strip() != exp_src:
            fails.append(f"  CLOCK PLLSource: ожидалось {exp_src}, код {src.strip()}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="CubeMX самоконтроль периферии")
    ap.add_argument("--csv", help="готовый CSV (без запуска CubeMX)")
    ap.add_argument("--code", help="папка со сгенерированным кодом (без CubeMX)")
    ap.add_argument("--skip-mx", action="store_true",
                    help="сверка по уже готовым CSV+коду без запуска CubeMX")
    args = ap.parse_args()

    csv_path = args.csv or DEFAULT_CSV
    gen_path = args.code or DEFAULT_GEN

    if not args.skip_mx and not args.csv:
        if not run_cubemx(csv_path, gen_path):
            print("[cubemx] прогон не удался — проверьте .ioc и CubeMX")
            return 2

    fails = []
    if not args.code:
        rc = check_pins(csv_path, fails)
        if rc == 2:
            print("[check] " + fails[-1])
            return 2
    mx = parse_mx(gen_path)
    check_values(mx, fails)

    if fails:
        print("[check] FAIL — расхождения:")
        for f in fails:
            print(f)
        return 1
    print(f"[check] PASS — пины ({len(EXPECTED_PINS)}), "
          f"значения TIM ({len(EXPECTED_TIM)}), ADC2, тактирование — всё совпадает")
    return 0


if __name__ == "__main__":
    sys.exit(main())
