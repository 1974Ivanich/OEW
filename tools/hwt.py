#!/usr/bin/env python3
"""HWT — проверки прошивки на цели через отладчик (DDTT Core для OEW).

Идея (DDTT §1.2): действия, которые обычно делают руками в сеансе отладчика,
записываются сценарием в репозитории и выполняются инструментом, давая
однозначный вердикт и машиночитаемый отчёт. Тестовый код в прошивку НЕ
добавляется: сценарий останавливает цель в нужной точке, читает состояние
по отладочной информации образа и сравнивает с ожиданием.

Команды:
    list       — список сценариев и их метаданные (без платы);
    preflight  — проверки без оборудования (DDTT-6.7); используется в CI;
    run        — запуск на стенде: идентичность → сверка образа → сценарии;
    sim        — прогон конвейера на симуляторе (не проверка на цели);
    doctor     — диагностика окружения стенда без обращения к цели.

Коды выхода: 0 — все PASS, 1 — есть FAIL (несовпадение проверки),
2 — есть ERROR (отказ входных данных/инфраструктуры/цели).
ERROR никогда не превращается в PASS (DDTT-6.5-1).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # запуск как модуля (`python -m tools.hwt`) и как скрипта
    from . import hwt_elf  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - зависит от способа запуска
    import hwt_elf  # type: ignore[no-redef]

from hwt_target import (  # noqa: E402
    CaseMeta, CaseResult, Check, ScenarioError, VERDICT_ERROR, VERDICT_FAIL,
    VERDICT_PASS, exit_code_for, iter_cases, junit_xml,
)

DEFAULT_SCENARIOS = ROOT / "tests" / "target"
DEFAULT_ELF = ROOT / "build" / "firmware.elf"
DEFAULT_CONFIG_CANDIDATES = (
    ROOT / "tests" / "target" / "stand.local.toml",
    ROOT / "tests" / "target" / "stand.local.json",
    ROOT / "tests" / "target" / "stand.toml",
    ROOT / "tests" / "target" / "stand.json",
)

WIN_GDB_DEFAULT = (r"C:\ST\STM32CubeCLT_1.22.0\GNU-tools-for-STM32\bin"
                   r"\arm-none-eabi-gdb.exe")
WIN_OPENOCD_DEFAULT = (r"C:\Program Files\OpenOCD-20250710-0.12.0\bin"
                       r"\openocd.exe")
WIN_PROGRAMMER_DEFAULT = (r"C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin"
                          r"\STM32_Programmer_CLI.exe")


# ── конфигурация стенда (DDTT-6.3) ────────────────────────────────────────

@dataclasses.dataclass
class Config:
    openocd_cfg: str = "openocd.cfg"
    gdb_port: int = 3333
    state: str = "reset-halt"
    allow_injections: bool = False
    exclusive: bool = True
    device_id: Optional[int] = None
    flash_size_kb: Optional[int] = None
    policy: str = "deny"
    gdb: Optional[str] = None
    openocd: Optional[str] = None
    source: str = ""


def _load_table(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import tomllib
    except ImportError as exc:  # pragma: no cover - Python < 3.11
        raise ScenarioError(
            f"{path}: чтение TOML требует Python 3.11+ или конфиг в .json "
            f"({exc})") from exc
    return tomllib.loads(text)


def load_config(path: Optional[str]) -> Config:
    cfg = Config()
    candidates = [Path(path)] if path else list(DEFAULT_CONFIG_CANDIDATES)
    for cand in candidates:
        if not cand.exists():
            continue
        table = _load_table(cand)
        cfg.source = str(cand)
        probe = table.get("probe", {})
        server = table.get("server", {})
        run = table.get("run", {})
        safety = table.get("safety", {})
        cfg.device_id = probe.get("device_id")
        cfg.flash_size_kb = probe.get("flash_size_kb")
        cfg.policy = str(probe.get("policy", "deny"))
        cfg.openocd_cfg = str(server.get("openocd_cfg", cfg.openocd_cfg))
        cfg.gdb_port = int(server.get("gdb_port", cfg.gdb_port))
        cfg.exclusive = bool(server.get("exclusive", cfg.exclusive))
        cfg.state = str(run.get("state", cfg.state))
        cfg.allow_injections = bool(safety.get("allow_injections", False))
        cfg.gdb = server.get("gdb")
        cfg.openocd = server.get("openocd")
        break
    if path and not Path(path).exists():
        raise ScenarioError(f"описание стенда не найдено: {path}")
    if cfg.state not in ("reset-halt", "attach"):
        raise ScenarioError(f"run.state={cfg.state!r}: ожидается reset-halt "
                            "или attach")
    if cfg.allow_injections:
        print("[hwt] ВНИМАНИЕ: инъекции разрешены описанием стенда "
              "(safety.allow_injections=true)")
    return cfg


def resolve_tool(explicit: Optional[str], names: Sequence[str],
                 fallback: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) \
            else None
    if fallback and Path(fallback).exists():
        return fallback
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


# ── исключительный доступ к адаптеру (DDTT-6.6) ───────────────────────────

class AdapterLock:
    """Один отладчик — один владелец.

    Занятый адаптер даёт ERROR без ожидания (DDTT-6.6-2). Признаки аварийного
    завершения прежнего владельца обнаруживаются и сообщаются, но чужие
    процессы не завершаются и цель не сбрасывается (DDTT-6.6-3).
    """

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self) -> Optional[str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{os.getpid()}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                  f"{socket.gethostname()}\n"
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = self._owner()
            pid = owner.get("pid")
            if pid and _pid_alive(pid):
                return (f"отладчик занят процессом pid={pid} "
                        f"({owner.get('host', '?')}, {owner.get('time', '?')}); "
                        f"дождитесь его завершения или удалите {self.path}")
            return (f"найден признак аварийного завершения прежнего владельца "
                    f"({owner.get('pid', '?')} не работает) — блокировка "
                    f"считается устаревшей и перехвачена; прежний процесс не "
                    f"завершался, цель не сбрасывалась")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        self.held = True
        return None

    def _owner(self) -> Dict[str, Any]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        out: Dict[str, Any] = {}
        if lines:
            try:
                out["pid"] = int(lines[0].strip())
            except ValueError:
                pass
        if len(lines) > 1:
            out["time"] = lines[1].strip()
        if len(lines) > 2:
            out["host"] = lines[2].strip()
        return out

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - проверяется на стенде
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── процессы ──────────────────────────────────────────────────────────────

def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


def wait_tcp(port: int, timeout_s: float = 15.0, host: str = "127.0.0.1") -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _wait_server(proc: subprocess.Popen, port: int, timeout_s: float
                 ) -> Optional[str]:
    """Дождаться порта GDB-сервера. None — поднялся; иначе причина отказа.

    Отдельно ловится случай «сервер упал сразу» (нет отладчика, занятый
    адаптер, не поддержан транспорт) — тогда не ждём весь таймаут.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if wait_tcp(port, timeout_s=0.5):
            return None
        rc = proc.poll()
        if rc is not None:
            return f"процесс OpenOCD завершился сразу (код {rc})"
        time.sleep(0.2)
    return f"порт {port} не открылся за {timeout_s:.0f} c"


@contextmanager
def openocd_session(cfg: Config, openocd: str, outdir: Path):
    cfg_path = Path(cfg.openocd_cfg)
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    if not cfg_path.exists():
        raise ScenarioError(f"конфиг OpenOCD не найден: {cfg_path}")
    log_path = outdir / "openocd.log"
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([openocd, "-f", str(cfg_path)],
                            stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(ROOT))
    try:
        yield proc, log_path
    finally:
        _kill_tree(proc)
        log.close()


def run_gdb(cfg: Config, gdb: str, elf: Path, mode: str, env_extra: Dict[str, str],
            timeout_s: float, outdir: Path, tag: str
            ) -> Tuple[Optional[Dict[str, Any]], str, bool]:
    """Запустить GDB с харнессом. Возвращает (payload, лог, таймаут?)."""
    out_json = outdir / f"{tag}.json"
    log_path = outdir / f"{tag}.gdb.log"
    env = os.environ.copy()
    env.update({
        "HWT_MODE": mode, "HWT_TOOLS": str(HERE), "HWT_ELF": str(elf),
        "HWT_OUT": str(out_json), "HWT_GDB_PORT": str(cfg.gdb_port),
        "HWT_STATE": cfg.state,
        "HWT_FLASH_BASE": str(hwt_elf.FLASH_BASE_DEFAULT),
        "HWT_FLASH_SIZE": str(hwt_elf.FLASH_SIZE_DEFAULT),
    })
    env.update(env_extra)
    cmd = [gdb, "-q", "-batch", "-nx", "-ex", "set confirm off",
           "-ex", "set pagination off", "-x", str(HERE / "hwt_gdbside.py"),
           str(elf)]
    proc = subprocess.Popen(cmd, env=env, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        output, _ = proc.communicate()
    log_path.write_text(output or "", encoding="utf-8", errors="replace")
    payload: Optional[Dict[str, Any]] = None
    if out_json.exists():
        try:
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover
            payload = None
            output = f"{output}\n[hwt] отчёт {out_json} не разобран: {exc}"
    return payload, output or "", timed_out


def target_recovery(cfg: Config, gdb: Optional[str], elf: Path,
                    outdir: Path) -> Tuple[bool, str]:
    """Вернуть цель в работу (DDTT-6.4-8). Результат обязан быть в отчёте."""
    if gdb:
        payload, _log, timed_out = run_gdb(cfg, gdb, elf, "recover", {},
                                           timeout_s=20, outdir=outdir,
                                           tag="recover")
        if payload and not timed_out:
            return True, "monitor reset run (GDB)"
    programmer = resolve_tool(None, ["STM32_Programmer_CLI"],
                              WIN_PROGRAMMER_DEFAULT)
    if programmer:
        res = subprocess.run([programmer, "-c", "port=SWD", "mode=UR", "-rst"],
                             capture_output=True, text=True, errors="replace",
                             timeout=60)
        if res.returncode == 0:
            return True, "STM32_Programmer_CLI -rst"
        return False, f"откат не удался: STM32_Programmer_CLI rc={res.returncode}"
    return False, "откат невозможен: ни GDB, ни STM32_Programmer_CLI"


# ── сценарии и предварительные проверки (DDTT-6.7) ────────────────────────

def collect_scenarios(paths: Sequence[str]) -> List[Tuple[Path, CaseMeta]]:
    out: List[Tuple[Path, CaseMeta]] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        if p.is_dir():
            files = sorted(x for x in p.glob("hw_*.py"))
        else:
            files = [p]
        for f in files:
            for meta in iter_cases(f):
                out.append((f, meta))
    return out


def preflight(elf: Path, scenarios: Sequence[Tuple[Path, CaseMeta]]
              ) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Проверки, не требующие цели (DDTT-6.7-1/6.7-2)."""
    errors: List[str] = []
    warnings: List[str] = []
    info: Dict[str, Any] = {}
    if not elf.exists():
        errors.append(f"образ не найден: {elf} (соберите: make)")
        return errors, warnings, info
    try:
        image = hwt_elf.ElfImage(elf)
    except hwt_elf.ElfError as exc:
        errors.append(str(exc))
        return errors, warnings, info

    crc, total, per = image.image_crc32()
    info["elf"] = str(elf.relative_to(ROOT)) if str(elf).startswith(str(ROOT)) \
        else str(elf)
    info["elf_sha256"] = hashlib.sha256(elf.read_bytes()).hexdigest()
    info["image_crc32"] = f"{crc:08X}"
    info["image_bytes"] = total
    info["image_sections"] = [{"section": n, "addr": f"0x{a:08X}", "size": s,
                               "crc32": f"{c:08X}"} for n, a, s, c in per]
    if not per:
        errors.append("в образе не найдено ни одной загружаемой секции Flash — "
                      "сверка образа невозможна")
    info["cases"] = []
    for path, meta in scenarios:
        entry = meta.to_dict()
        entry["path"] = str(path.relative_to(ROOT)) \
            if str(path).startswith(str(ROOT)) else str(path)
        missing = [c for c in meta.contracts if not image.has_symbol(c)]
        entry["missing_contracts"] = missing
        if missing:
            errors.append(f"{meta.id}: контракт не выполнен — нет символов "
                          f"{missing} в образе (DDTT-6.7-2)")
        if "injection" in meta.labels:
            warnings.append(f"{meta.id}: сценарий объявлен с инъекциями — "
                            "проверьте допустимость на стенде (DDTT §8)")
        info["cases"].append(entry)
    return errors, warnings, info


# ── встроенные проверки: идентичность и сверка образа ─────────────────────

def case_identity(probe: Dict[str, Any], cfg: Config) -> CaseResult:
    res = CaseResult(id="HWT_TARGET_IDENTITY", verdict=VERDICT_PASS,
                     target={"probe": probe, "config": cfg.source})
    expected_id = cfg.device_id or 0
    expected_kb = cfg.flash_size_kb or 0
    actual_id = probe.get("device_id")
    actual_kb = probe.get("flash_size_kb")
    res.checks.append(Check("DEV_ID совпадает с ожиданием",
                            actual_id, expected_id,
                            ok=(actual_id == expected_id) if expected_id
                            else True,
                            detail="DBGMCU_IDCODE (RM0440 §46.4.2)"))
    res.checks.append(Check("размер Flash совпадает с ожиданием",
                            actual_kb, expected_kb,
                            ok=(actual_kb == expected_kb) if expected_kb
                            else True,
                            detail="FLASHSIZE @0x1FFF75E0 (RM0440)"))
    if not expected_id or not expected_kb:
        res.notes.append("ожидания probe не заданы в описании стенда — "
                         "идентичность только записана, не сверена")
    if not all(c.ok for c in res.checks):
        if cfg.policy == "warn":
            res.verdict = VERDICT_PASS
            res.notes.append("policy=warn: несовпадение идентичности не "
                             "блокирует запуск (DDTT-6.4-3)")
        else:
            res.verdict = VERDICT_FAIL
    return res


def case_image(image: hwt_elf.ElfImage, dumps: Dict[str, Any]) -> CaseResult:
    """Сверить содержимое Flash цели с образом (DDTT-6.4-4)."""
    res = CaseResult(id="HWT_IMAGE", verdict=VERDICT_PASS)
    expected: Dict[str, Tuple[int, int, int]] = {}
    for name, addr, size, crc in image.image_crc32()[2]:
        expected[name] = (addr, size, crc)
    seen = set()
    for entry in dumps.get("dumps", []):
        name = entry["section"]
        seen.add(name)
        exp = expected.get(name)
        path = Path(entry["file"])
        if exp is None:
            res.notes.append(f"{name}: нет в образе — пропущено")
            continue
        data = path.read_bytes() if path.exists() else b""
        ok = len(data) == exp[1] and _crc32(data) == exp[2]
        res.checks.append(Check(f"секция {name} совпадает с образом",
                                f"0x{_crc32(data):08X}" if data else "нет данных",
                                f"0x{exp[2]:08X}", ok=ok,
                                detail=f"{exp[1]} Б @0x{exp[0]:08X}"))
    for name in expected:
        if name not in seen:
            res.checks.append(Check(f"секция {name} прочитана с цели", False,
                                    True, ok=False,
                                    detail="дамп не получен"))
    if not res.checks:
        res.verdict = VERDICT_ERROR
        res.error = "нечего сверять: цель не отдала ни одной секции"
    elif not all(c.ok for c in res.checks):
        res.verdict = VERDICT_FAIL
        res.error = ""
    if res.verdict == VERDICT_FAIL:
        res.notes.append("ВНИМАНИЕ: содержимое Flash на цели не совпадает с "
                         "образом, по которому написан сценарий "
                         "(DDTT-6.4-4). Вероятно, на плате старая прошивка: "
                         "make && make flash")
    return res


def _crc32(data: bytes) -> int:
    import zlib
    return zlib.crc32(data)


def verdict_word(code: int) -> str:
    return {0: VERDICT_PASS, 1: VERDICT_FAIL, 2: VERDICT_ERROR}[code]


# ── отчёты (DDTT-6.5) ────────────────────────────────────────────────────

def write_reports(outdir: Path, meta: Dict[str, Any],
                  results: Sequence[CaseResult]) -> int:
    code = exit_code_for(results)
    report = dict(meta)
    report["verdict"] = verdict_word(code)
    report["exit_code"] = code
    report["cases"] = [r.to_dict() for r in results]
    report["injections"] = [
        {"case": r.id, **c.to_dict()["injection"]}
        for r in results for c in r.checks if c.injected]
    (outdir / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "junit.xml").write_text(
        junit_xml(results, name=meta.get("junit_name", "hwt")),
        encoding="utf-8")
    return code


def _print_results(results: Sequence[CaseResult], simulated: bool) -> None:
    for r in results:
        mark = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERROR"}[r.verdict]
        print(f"  [{mark}] {r.id} ({r.duration_s:.2f} c)")
        for c in r.checks:
            if not c.ok:
                print(f"        ✗ {c.name}: получено {c.actual!r}, "
                      f"ожидалось {c.expected!r}" +
                      (f" — {c.detail}" if c.detail else ""))
        if r.error:
            print(f"        ! {r.error}")
        for n in r.notes:
            print(f"        · {n}")
    if simulated:
        print("  СИМУЛЯЦИЯ: это не проверка на железе (DDTT-6.7 — "
              "предварительная проверка конвейера)")


# ── команды ──────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> int:
    scenarios = collect_scenarios(args.scenarios)
    for path, meta in scenarios:
        print(f"{meta.id:22s} {path.relative_to(ROOT)}:{meta.line}\n"
              f"    timeout={meta.timeout_s:.0f}c labels={list(meta.labels)} "
              f"contracts={list(meta.contracts)}\n"
              f"    {meta.description}")
    print(f"всего сценариев: {len(scenarios)}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    scenarios = collect_scenarios(args.scenarios)
    errors, warnings, info = preflight(Path(args.elf), scenarios)
    print(f"preflight: сценариев {len(scenarios)}, образ "
          f"{info.get('elf', args.elf)}")
    if info:
        print(f"  CRC образа: {info.get('image_crc32')} "
              f"({info.get('image_bytes')} Б, секций "
              f"{len(info.get('image_sections', []))})")
        print(f"  sha256: {info.get('elf_sha256')}")
    for w in warnings:
        print(f"  [предупреждение] {w}")
    for e in errors:
        print(f"  [ошибка] {e}")
    gdb = resolve_tool(cfg.gdb, ["arm-none-eabi-gdb"], WIN_GDB_DEFAULT)
    openocd = resolve_tool(cfg.openocd, ["openocd"], WIN_OPENOCD_DEFAULT)
    if not gdb:
        print("  [предупреждение] arm-none-eabi-gdb не найден — запуск на "
              "стенде недоступен (preflight к железу не обращается)")
    if not openocd:
        print("  [предупреждение] openocd не найден — запуск на стенде "
              "недоступен")
    print("preflight: " + ("ОШИБКИ (exit 2)" if errors else "OK"))
    return 2 if errors else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print("HWT doctor — окружение стенда (без обращения к цели)")
    print(f"  python   : {sys.version.split()[0]} ({sys.executable})")
    try:
        cfg = load_config(args.config)
        print(f"  стенд    : {cfg.source or 'описание не найдено, взяты значения по умолчанию'}")
    except ScenarioError as exc:
        print(f"  стенд    : ОШИБКА {exc}")
        cfg = Config()
    print(f"  состояние: {cfg.state}, порт GDB {cfg.gdb_port}, инъекции "
          f"{'РАЗРЕШЕНЫ' if cfg.allow_injections else 'запрещены'}")
    gdb = resolve_tool(cfg.gdb, ["arm-none-eabi-gdb"], WIN_GDB_DEFAULT)
    openocd = resolve_tool(cfg.openocd, ["openocd"], WIN_OPENOCD_DEFAULT)
    print(f"  gdb      : {gdb or 'НЕ НАЙДЕН'}")
    print(f"  openocd  : {openocd or 'НЕ НАЙДЕН'}")
    if gdb:
        res = subprocess.run([gdb, "--version"], capture_output=True, text=True,
                             errors="replace")
        print(f"             {res.stdout.splitlines()[0] if res.stdout else ''}")
    elf = Path(args.elf)
    print(f"  образ    : {elf if elf.exists() else str(elf) + ' — НЕТ (make)'}")
    scenarios = collect_scenarios(args.scenarios)
    print(f"  сценарии : {len(scenarios)} (" +
          ", ".join(m.id for _p, m in scenarios) + ")")
    if os.name == "nt":  # pragma: no cover
        programmer = resolve_tool(None, ["STM32_Programmer_CLI"],
                                  WIN_PROGRAMMER_DEFAULT)
        if programmer:
            res = subprocess.run([programmer, "--list"], capture_output=True,
                                 text=True, errors="replace", timeout=90)
            for line in res.stdout.splitlines():
                if "ST-Link" in line or "STLink" in line:
                    print(f"  отладчик : {line.strip()}")
    lock = AdapterLock(Path(os.environ.get("TEMP", str(ROOT))) /
                       "hwt_stlink.lock")
    owner = lock._owner() if lock.path.exists() else {}
    print(f"  блокировка: {lock.path} " +
          (f"(владелец pid={owner.get('pid')})" if owner else "(свободна)"))
    missing = [n for n, v in (("gdb", gdb), ("openocd", openocd)) if not v]
    print("doctor: " + ("НЕ ГОТОВ к запуску на стенде" if missing else "OK"))
    return 2 if missing else 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.state = args.state or cfg.state
    if args.allow_injections:
        cfg.allow_injections = True
    elf = Path(args.elf)
    scenarios = collect_scenarios(args.scenarios)
    if args.case:
        scenarios = [s for s in scenarios if s[1].id in set(args.case)]
        if not scenarios:
            print(f"[hwt] сценарии не найдены: {args.case}")
            return 2
    simulated = bool(args.sim)
    run_id = time.strftime("%Y%m%d_%H%M%S") + ("_sim" if simulated else "")
    base_dir = Path(args.out) if args.out else ROOT / "build" / "hwt"
    outdir = base_dir / run_id
    suffix = 1
    while outdir.exists():  # прогоны одной секунды не должны перетирать отчёты
        suffix += 1
        outdir = base_dir / f"{run_id}_{suffix}"
    outdir.mkdir(parents=True, exist_ok=True)

    meta: Dict[str, Any] = {
        "tool": "hwt", "run_id": run_id, "started": time.strftime(
            "%Y-%m-%d %H:%M:%S"),
        "simulated": simulated, "stand": cfg.source, "state": cfg.state,
        "junit_name": "hwt-sim" if simulated else "hwt-target",
        "injections_allowed": cfg.allow_injections,
    }

    errors, warnings, info = preflight(elf, scenarios)
    meta.update(info)
    for w in warnings:
        print(f"  [предупреждение] {w}")
    if errors:
        for e in errors:
            print(f"  [ошибка] {e}")
        meta["error"] = "; ".join(errors)
        reasons = [CaseResult(id="HWT_PREFLIGHT", verdict=VERDICT_ERROR,
                              error="; ".join(errors))]
        return write_reports(outdir, meta, reasons)

    image = hwt_elf.ElfImage(elf)
    print(f"hwt: сценариев {len(scenarios)}, образ {info['elf']}, "
          f"CRC {info['image_crc32']}")
    print(f"hwt: отчёт в {outdir}")

    if simulated:
        return _run_simulated(args, cfg, image, scenarios, outdir, meta)

    gdb = resolve_tool(cfg.gdb, ["arm-none-eabi-gdb"], WIN_GDB_DEFAULT)
    openocd = resolve_tool(cfg.openocd, ["openocd"], WIN_OPENOCD_DEFAULT)
    if not gdb or not openocd:
        meta["error"] = (f"нет инструментов: gdb={gdb}, openocd={openocd} — "
                         "запуск на стенде невозможен (см. hwt doctor)")
        return write_reports(outdir, meta, [CaseResult(
            id="HWT_ENV", verdict=VERDICT_ERROR, error=meta["error"])])

    lock = AdapterLock(Path(os.environ.get("TEMP", str(ROOT))) /
                       "hwt_stlink.lock")
    if cfg.exclusive:
        problem = lock.acquire()
        if problem:
            meta["error"] = problem
            return write_reports(outdir, meta, [CaseResult(
                id="HWT_ADAPTER_LOCK", verdict=VERDICT_ERROR, error=problem)])
        meta["adapter_lock"] = str(lock.path)

    results: List[CaseResult] = []

    def finish(finished: List[CaseResult]) -> int:
        """Возврат цели в работу + отчёт + печать вердикта (DDTT-6.4-8/6.5)."""
        ok, how = target_recovery(cfg, gdb, elf, outdir)
        meta["recovery"] = {"ok": ok, "how": how}
        code = write_reports(outdir, meta, finished)
        _print_results(finished, simulated=False)
        print(f"hwt: вердикт {verdict_word(code)} (exit {code}); отчёт "
              f"{outdir / 'result.json'}")
        return code

    aborted = False
    with openocd_session(cfg, openocd, outdir) as (proc, log_path):
        meta["openocd_log"] = str(log_path.name)
        reason = _wait_server(proc, cfg.gdb_port, args.server_timeout)
        if reason:
            _kill_tree(proc)
            meta["error"] = (f"отладочный сервер не поднялся "
                             f"({reason}) — отладочный адаптер подключён и "
                             f"исправен? см. {log_path}")
            results.append(CaseResult(id="HWT_OPENOCD", verdict=VERDICT_ERROR,
                                      error=meta["error"]))
            aborted = True

        if not aborted:
            # 1. идентичность цели (DDTT-6.4-3)
            payload, _log, timed_out = run_gdb(cfg, gdb, elf, "probe", {},
                                               timeout_s=30, outdir=outdir,
                                               tag="probe")
            if timed_out or not payload:
                results.append(CaseResult(
                    id="HWT_TARGET_IDENTITY", verdict=VERDICT_ERROR,
                    error="цель не отвечает на probe (таймаут/нет ответа)"))
                meta["target"] = {}
            else:
                meta["target"] = payload.get("probe", {})
                results.append(case_identity(meta["target"], cfg))
                if results[-1].verdict != VERDICT_PASS and cfg.policy != "warn":
                    meta["error"] = ("идентичность цели не совпала с описанием "
                                     "стенда — дальше не идём (DDTT-6.4-3)")
                    aborted = True

        if not aborted:
            # 2. сверка содержимого Flash с образом (DDTT-6.4-4)
            dump_dir = outdir / "dumps"
            payload, _log, timed_out = run_gdb(
                cfg, gdb, elf, "image", {"HWT_DUMP_DIR": str(dump_dir)},
                timeout_s=60, outdir=outdir, tag="image")
            if timed_out or not payload:
                results.append(CaseResult(
                    id="HWT_IMAGE", verdict=VERDICT_ERROR,
                    error="не удалось прочитать Flash цели"))
            else:
                results.append(case_image(image, payload.get("image", {})))
                if results[-1].verdict == VERDICT_FAIL:
                    meta["error"] = ("образ на цели не совпадает с ELF — "
                                     "сценарии не запускались")
                    aborted = True

        # 3. сценарии: по одному сценарию на сеанс отладчика
        for path, case_meta in ([] if aborted else scenarios):
            tag = f"case_{case_meta.id}"
            payload, log, timed_out = run_gdb(
                cfg, gdb, elf, "case",
                {"HWT_SCENARIO": str(path), "HWT_CASE": case_meta.id,
                 "HWT_TIMEOUT_S": str(case_meta.timeout_s),
                 "HWT_ALLOW_INJECTIONS":
                     "1" if cfg.allow_injections else "0"},
                timeout_s=case_meta.timeout_s + args.timeout_slack,
                outdir=outdir, tag=tag)
            if timed_out:
                results.append(CaseResult(
                    id=case_meta.id, verdict=VERDICT_ERROR,
                    error=f"предел времени {case_meta.timeout_s:.0f} c "
                          f"исчерпан — цель не остановилась "
                          f"(DDTT-6.4-7); лог {log}"))
            elif not payload:
                results.append(CaseResult(
                    id=case_meta.id, verdict=VERDICT_ERROR,
                    error="отчёт сценария не получен (см. лог GDB)"))
            else:
                results.append(_result_from_payload(payload, case_meta))

    if cfg.exclusive:
        lock.release()
    return finish(results)


def _result_from_payload(payload: Dict[str, Any], meta: CaseMeta) -> CaseResult:
    raw = payload.get("result") or {}
    res = CaseResult(id=raw.get("id", meta.id),
                     verdict=raw.get("verdict", VERDICT_ERROR),
                     error=raw.get("error", ""),
                     notes=list(raw.get("notes", [])),
                     duration_s=float(raw.get("duration_s", 0.0)),
                     target=raw.get("target", {}))
    for c in raw.get("checks", []):
        res.checks.append(Check(name=c.get("name", "?"),
                                actual=c.get("actual"),
                                expected=c.get("expected"),
                                ok=bool(c.get("ok")),
                                detail=c.get("detail", ""),
                                injected="injection" in c,
                                before=(c.get("injection") or {}).get("before")))
    return res


def _run_simulated(args: argparse.Namespace, cfg: Config, image: hwt_elf.ElfImage,
                   scenarios: Sequence[Tuple[Path, CaseMeta]], outdir: Path,
                   meta: Dict[str, Any]) -> int:
    from hwt_sim import SimBackend, load_fixture
    from hwt_target import Target, run_case

    fixture_path = Path(args.sim_values)
    values: Dict[str, Any] = {}
    if fixture_path.exists():
        values = load_fixture(fixture_path)
    meta["sim_values"] = str(fixture_path) if fixture_path.exists() else "нет"
    meta["target"] = {"simulated": True}

    results: List[CaseResult] = []
    # сверка образа: тот же код, что на стенде, но «прочитанное» берётся из ELF
    import zlib
    corrupt = args.sim_corrupt
    checks: List[Check] = []
    for sec in image.flash_sections():
        expected = image.section_bytes(sec)
        actual = bytearray(expected)
        if corrupt == sec.name and actual:
            actual[0] ^= 0xFF
        checks.append(Check(f"секция {sec.name} совпадает с образом",
                            f"0x{zlib.crc32(bytes(actual)):08X}",
                            f"0x{zlib.crc32(expected):08X}",
                            ok=zlib.crc32(bytes(actual)) ==
                            zlib.crc32(expected),
                            detail=f"{sec.size} Б @0x{sec.addr:08X}"))
    image_res = CaseResult(id="HWT_IMAGE", checks=checks,
                           verdict=VERDICT_PASS if all(c.ok for c in checks)
                           else VERDICT_FAIL)
    if image_res.verdict == VERDICT_FAIL:
        image_res.notes.append("симуляция: расхождение задано флагом "
                               "--sim-corrupt (проверка механизма сверки)")
    results.append(image_res)

    for path, case_meta in scenarios:
        backend = SimBackend(str(image.path), values=values,
                             hung_symbols=args.sim_hang)
        target = Target(backend, case_meta, allow_injections=False,
                        image=image)
        results.append(run_case(case_meta, _load_case_func(path, case_meta),
                                target))

    code = write_reports(outdir, meta, results)
    _print_results(results, simulated=True)
    print(f"hwt: СИМУЛЯЦИЯ, вердикт {verdict_word(code)} (exit {code}); отчёт "
          f"{outdir / 'result.json'}")
    return code


def _load_case_func(path: Path, meta: CaseMeta):
    from hwt_target import load_scenario
    cases = load_scenario(path)
    return cases[meta.id][1]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hwt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--elf", default=str(DEFAULT_ELF), help="ELF с отладкой")
    p.add_argument("--scenarios", action="append", default=None,
                   help="файл или каталог сценариев (по умолчанию tests/target)")
    p.add_argument("--config", default=None, help="описание стенда (toml/json)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="список сценариев и метаданных")
    sub.add_parser("preflight", help="проверки без оборудования (CI)")
    sub.add_parser("doctor", help="диагностика окружения стенда")

    run = sub.add_parser("run", help="запуск на стенде")
    run.add_argument("--case", action="append", default=None,
                     help="выполнить только эти сценарии (id)")
    run.add_argument("--out", default=None, help="каталог отчётов")
    run.add_argument("--state", choices=["reset-halt", "attach"], default=None,
                     help="состояние цели перед сценарием (DDTT-6.2-5)")
    run.add_argument("--allow-injections", action="store_true",
                     help="разрешить инъекции (опасно, DDTT §8)")
    run.add_argument("--server-timeout", type=float, default=20.0)
    run.add_argument("--timeout-slack", type=float, default=10.0,
                     help="запас к пределу времени сценария")
    run.add_argument("--sim", action="store_true",
                     help="прогон конвейера без железа (не проверка на цели)")
    run.add_argument("--sim-values", default=str(DEFAULT_SCENARIOS /
                                                 "sim_values.json"))
    run.add_argument("--sim-corrupt", default=None,
                     help="симуляция расхождения секции образа")
    run.add_argument("--sim-hang", action="append", default=None,
                     help="симуляция незакрытой точки останова (символ)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scenarios is None:
        args.scenarios = [str(DEFAULT_SCENARIOS)]
    try:
        if args.command == "list":
            return cmd_list(args)
        if args.command == "preflight":
            return cmd_preflight(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        return cmd_run(args)
    except ScenarioError as exc:
        print(f"[hwt] ошибка: {exc}")
        return 2
    except FileNotFoundError as exc:  # pragma: no cover
        print(f"[hwt] нет файла: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
