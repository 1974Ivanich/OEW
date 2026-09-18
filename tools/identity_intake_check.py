#!/usr/bin/env python3
"""Intake-гейт входов со стенда: `mapcap identity` + raw `uart.log` (TZ-02, шаг «входы»).

Назначение: до любых действий с картой проверить ФАКТИЧЕСКИЕ входы стенда и не пропустить
пакет, из которого нельзя собрать авторитетный `M0-rebased`. Один и тот же инструмент
запускается на стендовом ПК перед возвратом (self-check) и на принимающем ПК (независимый
вердикт) — расхождение вердиктов означает дефект пакета, а не «мнение».

Этот шаг — NO-HV: PWM выключен, звено не подаётся. Наличие признаков PWM/энергизации в логе
считается нарушением входного контракта (I11).

Правила (ID в отчёте; FAIL любого → INTAKE BLOCKED):
  I1  манифест SHA256SUMS.txt/RETURN_SHA256.txt есть и сходится со всеми файлами папки
  I2  непрерывный raw-лог присутствует и непуст
  I3  команда `mapcap identity` действительно исполнялась (эхо команды в логе)
  I4  получена строка @MAP:IDENTITY со всеми 11 полями (или явный FAIL от прошивки)
  I5  критические поля identity не нулевые (иначе артефакт заведомо не примут)
  I6  identity.txt (если приложен) совпадает с логом построчно
  I7  нет ответа `unknown` на mapcap (признак прошивки без map-команд)
  I8  целостность: uart_drp=0 и uart_trunc=0 в КАЖДОЙ строке @SYS
  I9  нет @BRK:valid=1 и нет строк с FAULT != 0; t монотонен (если есть @FOC)
  I10 SESSION_META.json: firmware_sha256, source_commit, board_revision, оператор, дата;
      board_revision совпадает с identity
  I11 PWM выключен (CCER=0 из p? или MOE=0) — входной шаг неэнергический

Дополнительно: `--emit-live-txt <путь>` собирает live.txt для следующего шага
(`map_variant_cli --rebase-identity`). Писать его следует ВНЕ возвращаемой папки.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

IDENT_RE = re.compile(
    r"@MAP:IDENTITY:board=(\d+):pwm=(\d+):arr=(\d+):trig=0x([0-9A-Fa-f]+):off=(\d+):dt=(\d+):"
    r"adc_clk=(\d+):sample_x2=(\d+):res=(\d+):acs=0x([0-9A-Fa-f]+):ccs=0x([0-9A-Fa-f]+)")
IDENT_KEYS = ("board_revision", "pwm_frequency_hz", "timer_arr", "adc_trigger_id",
              "trigger_offset_ticks", "deadtime_ticks", "adc_clock_hz", "adc_sample_cycles_x2",
              "adc_resolution", "adc_config_signature", "current_calibration_signature")
LIVE_ALIASES = {"board": "board_revision", "pwm": "pwm_frequency_hz", "arr": "timer_arr",
                "trig": "adc_trigger_id", "off": "trigger_offset_ticks", "dt": "deadtime_ticks",
                "adc_clk": "adc_clock_hz", "sample_x2": "adc_sample_cycles_x2",
                "res": "adc_resolution", "acs": "adc_config_signature",
                "ccs": "current_calibration_signature"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})$")
MANIFEST_NAMES = ("SHA256SUMS.txt", "RETURN_SHA256.txt", "SHA256SUMS")


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, rid: str, ok: bool, detail: str) -> None:
        self.rows.append((rid, "PASS" if ok else "FAIL", detail))

    def info(self, rid: str, detail: str) -> None:
        self.rows.append((rid, "INFO", detail))

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    def render(self) -> str:
        w = max((len(f"[{r[0]}]") for r in self.rows), default=4)
        return "\n".join(f"{f'[{i}]':<{w}}  {s:<4}  {d}" for i, s, d in self.rows)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_identity_line(text: str) -> dict[str, int] | None:
    m = IDENT_RE.search(text)
    if not m:
        return None
    values = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4), 16),
              int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8)),
              int(m.group(9)), int(m.group(10), 16), int(m.group(11), 16)]
    return dict(zip(IDENT_KEYS, values))


def parse_live_txt(text: str) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        name = key if key in IDENT_KEYS else LIVE_ALIASES.get(key)
        if name and re.fullmatch(r"(0x[0-9A-Fa-f]+|\d+)", value):
            found[name] = int(value, 16) if value.startswith("0x") else int(value)
    return found or None


def check_manifest(folder: Path, rep: Report) -> Path | None:
    manifest = next((folder / n for n in MANIFEST_NAMES if (folder / n).exists()), None)
    if manifest is None:
        rep.add("I1", False, "нет SHA256SUMS.txt / RETURN_SHA256.txt")
        return None
    listed: dict[str, str] = {}
    bad: list[str] = []
    for line in manifest.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            bad.append(f"строка без пути: {line[:40]}")
            continue
        digest, rel = parts[0].strip().lower(), parts[1].strip().lstrip("*")
        listed[rel.replace("\\", "/")] = digest
    missing: list[str] = []
    for rel, digest in listed.items():
        target = folder / rel
        if not target.exists():
            missing.append(f"нет файла {rel}")
        elif sha256_of(target) != digest:
            missing.append(f"sha256 не совпал: {rel}")
    on_disk = {p.relative_to(folder).as_posix() for p in folder.rglob("*")
               if p.is_file() and p.name not in MANIFEST_NAMES}
    uncovered = sorted(on_disk - set(listed))
    problems = bad + missing
    if uncovered:
        problems.append(f"не покрыто манифестом: {', '.join(uncovered)}")
    ok = bool(listed) and not problems
    detail = (f"{manifest.name}: записей {len(listed)}, файлов вне манифеста {len(uncovered)}"
              if ok else ("; ".join(problems[:4]) if problems else "манифест пуст"))
    rep.add("I1", ok, detail)
    return manifest


def check_logs(folder: Path, rep: Report, log_override: Path | None) -> tuple[str, dict | None]:
    logs = [log_override] if log_override else sorted(
        (p for p in folder.rglob("*.log") if p.is_file()), key=lambda p: -p.stat().st_size)
    logs = [p for p in logs if p is not None]
    if not logs:
        rep.add("I2", False, "в папке нет ни одного *.log")
        return "", None
    primary = logs[0]
    size = primary.stat().st_size
    rep.add("I2", size > 100,
            f"{primary.name}: {size} Б" + ("" if size > 100 else " — файл практически пуст"))
    if len(logs) > 1:
        rep.info("I2", "логов в папке: " + ", ".join(f"{p.name}({p.stat().st_size} Б)" for p in logs))

    text = primary.read_text(encoding="utf-8", errors="replace")

    command_seen = any(re.search(r"(>>>\s*|TX[:\s].*?)mapcap\s+identity", line, re.IGNORECASE)
                       for line in text.splitlines())
    rep.add("I3", command_seen,
            "эхо команды `mapcap identity` найдено" if command_seen else
            "в логе нет эха команды `mapcap identity` — снимать identity обязательно этой командой")

    identity = parse_identity_line(text)
    if identity is None:
        fail_line = re.search(r"@MAP:IDENTITY:FAIL[^\r\n]*", text)
        rep.add("I4", False,
                f"прошивка ответила {fail_line.group(0)[:80]}" if fail_line else
                "нет строки @MAP:IDENTITY с 11 полями")
    else:
        rep.add("I4", True, "11 полей получены: " + " ".join(f"{k}={v}" for k, v in identity.items())[:120])

    if identity:
        critical = {k: identity[k] for k in IDENT_KEYS if identity[k] == 0 and k != "trigger_offset_ticks" and k != "adc_resolution"}
        rep.add("I5", not critical,
                "критические поля не нулевые" if not critical else
                f"нулевые поля: {', '.join(critical)} — артефакт заведомо не примут")

    ident_txt = next((p for p in folder.rglob("*.txt")
                      if p.name.lower() in ("identity.txt", "live.txt", "live_identity.txt")), None)
    if ident_txt is None:
        rep.info("I6", "identity.txt не приложен (не обязателен: live.txt собирается из лога)")
    else:
        parsed = parse_live_txt(ident_txt.read_text(encoding="utf-8", errors="replace"))
        if not parsed or len(parsed) != 11:
            rep.add("I6", False, f"{ident_txt.name}: не разобрано 11 полей")
        else:
            mismatch = [k for k in IDENT_KEYS if identity and parsed.get(k) != identity[k]]
            rep.add("I6", not mismatch,
                    f"{ident_txt.name} совпадает с логом" if not mismatch else
                    f"расхождения: {', '.join(mismatch)}")

    unknown_after = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"mapcap\s+identity", line, re.IGNORECASE):
            window = "\n".join(lines[i:i + 4])
            if re.search(r"^\s*(>>>\s*)?unknown\b", window, re.IGNORECASE | re.MULTILINE) \
                    or re.search(r"\bunknown\b", window, re.IGNORECASE):
                unknown_after = True
                break
    rep.add("I7", not unknown_after,
            "ответ на mapcap не unknown" if not unknown_after else
            "прошивка ответила `unknown` — образ без map-команд (перепрошить energize-образ)")

    sys_lines = re.findall(r"@SYS:.*?uart_drp=(\d+):uart_trunc=(\d+)", text)
    lost = [f"drp={d}/trunc={t}" for d, t in sys_lines if int(d) or int(t)]
    rep.add("I8", bool(sys_lines) and not lost,
            f"строк @SYS: {len(sys_lines)}, потерь нет" if sys_lines and not lost else
            ("нет строк @SYS (sysinfo не исполнялся)" if not sys_lines
             else f"потери UART: {', '.join(lost)}"))

    brk = re.search(r"@BRK:valid=1", text)
    fault_rows = [ln for ln in lines if ln.lstrip("> ").startswith("@FOC:")
                  and (m := re.search(r"FAULT=(\d+)", ln)) and int(m.group(1)) != 0]
    t_values = [int(m.group(1)) for ln in lines if ln.lstrip("> ").startswith("@FOC:")
                for m in [re.search(r"t=(\d+)", ln)] if m]
    monotonic = all(a <= b for a, b in zip(t_values, t_values[1:])) if t_values else True
    ok = not brk and not fault_rows and monotonic
    rep.add("I9", ok,
            f"BREAK/FAULT чисто, t-строк {len(t_values)}" if ok else
            "; ".join(filter(None, ["@BRK:valid=1" if brk else "",
                                    f"строк FAULT!=0: {len(fault_rows)}" if fault_rows else "",
                                    "t не монотонен" if not monotonic else ""])))

    meta_path = next((p for p in folder.rglob("*.json")
                      if p.name.lower() in ("session_meta.json", "build_info.json")), None)
    meta_missing: list[str] = []
    if meta_path is None:
        rep.add("I10", False, "нет SESSION_META.json (firmware_sha256, source_commit, board_revision, operator, date_utc)")
    else:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            meta, meta_missing = None, [f"не разобран как JSON: {exc}"]
        if isinstance(meta, dict):
            for key in ("firmware_sha256", "source_commit", "board_revision", "operator", "date_utc"):
                if not meta.get(key):
                    meta_missing.append(f"нет {key}")
            fw = str(meta.get("firmware_sha256") or "")
            if fw and not SHA256_RE.match(fw):
                meta_missing.append("firmware_sha256 не sha256")
            commit = str(meta.get("source_commit") or "")
            if commit and not re.fullmatch(r"[0-9a-f]{7,40}", commit):
                meta_missing.append("source_commit не hex-хэш")
            date = str(meta.get("date_utc") or "")
            if date and not TS_RE.match(date):
                meta_missing.append("date_utc не ISO-8601")
            if identity and meta.get("board_revision") is not None \
                    and int(meta["board_revision"]) != identity["board_revision"]:
                meta_missing.append(f"board_revision={meta['board_revision']} != identity "
                                    f"{identity['board_revision']}")
        rep.add("I10", not meta_missing,
                f"{meta_path.name}: все поля, board_revision сверен" if not meta_missing
                else "; ".join(meta_missing))

    pwm_off_p = re.search(r"@PWM:CR1=(\d+):CCER=(\d+):BDTR=(\d+):CNT=(\d+)", text)
    moe_zero = bool(re.search(r"MOE=0", text))
    ok11 = bool(pwm_off_p and int(pwm_off_p.group(2)) == 0) or moe_zero
    rep.add("I11", ok11,
            "PWM выключен (CCER=0)" if pwm_off_p and int(pwm_off_p.group(2)) == 0
            else ("PWM выключен (MOE=0)" if moe_zero else
                  "нет подтверждения PWM OFF — входной шаг обязан быть неэнергическим (CCER=0/MOE=0)"))
    return text, identity


def main() -> int:
    ap = argparse.ArgumentParser(description="Intake-гейт входов со стенда (identity + raw uart.log)")
    ap.add_argument("folder", help="папка, полученная со стенда (или её копия)")
    ap.add_argument("--log", default=None, help="явно указать основной лог")
    ap.add_argument("--json", default=None, help="отчёт JSON (писать ВНЕ возвращаемой папки)")
    ap.add_argument("--emit-live-txt", default=None, help="собрать live.txt для --rebase-identity")
    args = ap.parse_args()

    folder = Path(args.folder)
    rep = Report()
    if not folder.is_dir():
        print(f"BLOCKED: нет каталога {folder}")
        return 1

    check_manifest(folder, rep)
    _, identity = check_logs(folder, rep, Path(args.log) if args.log else None)

    print(f"INTAKE CHECK: {folder}")
    print(rep.render())
    verdict = "BLOCKED" if rep.failed else "PASS"
    if rep.failed:
        print(f"\nINTAKE {verdict}: не пройдено {len(rep.failed)} — "
              f"{', '.join(r[0] for r in rep.failed)}")
    else:
        print(f"\nINTAKE {verdict}: входы пригодны для сборки M0-rebased")

    if args.emit_live_txt and identity:
        out = Path(args.emit_live_txt)
        out.write_text("\n".join(f"{k}={identity[k]}" for k in IDENT_KEYS) + "\n",
                       encoding="utf-8", newline="\n")
        print(f"live.txt: {out} (11 полей)")
    elif args.emit_live_txt:
        print("live.txt не создан: identity не получена")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "folder": str(folder),
            "verdict": verdict,
            "checks": [{"id": i, "status": s, "detail": d} for i, s, d in rep.rows],
            "identity": identity,
            "checked_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"json: {args.json}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
