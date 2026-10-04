#!/usr/bin/env python3
"""PC-3 bench commissioning steps over the ST-Link VCP (fail-closed).

Разделяет ЧТЕНИЕ и ДЕЙСТВИЕ, чтобы каждый шаг комиссионинга был
машинно-проверяемым сценарием, а не ручной перепиской (AGENTS_WORKFLOW 9.1):

  status                  read-only: @FOC, p?, breakdiag, a?, mapcap status/identity
  params                  mp=<Rs>,<Ls> + mpapply + gate '1' (verbatim reply)
  calib                   'c' (ADC zero offsets) + a? + mapcap identity
  arm <profile_id>        mcarm=<profile_id> (ADC injected armed, PWM still off)
  capture <profile_id>    mcarm + mapcap run + status + drain [+ build]  (ENERGIZING)
  abort                   mapcap abort

Гейты (любое действие отказывает, если гейт не проходит):
  * @FOC:FAULT==0 и FAULT_R==0 (нет латча);
  * RUN==0 (нет активного FOC/автотюна);
  * для 'calib' PWM выключен (p? CCER==0, BDTR MOE=0) и VBUS <= --max-vbus-for-calib;
  * для 'capture' обязателен явный флаг --allow-energize и VBUS >= --vbus-min.

Выход: 0 = шаг выполнен, 2 = гейт/порт отказал, 1 = неожиданный ответ платы.

Все сообщения - ASCII (урок B19: консоль стенда в cp1251/ascii).
"""

import argparse
import os
import re
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover - bench dependency
    serial = None

FOC_FRAME = re.compile(r"@FOC:([^\r\n]*)")
MC_STATUS = re.compile(r"@MC:STATUS:([^\r\n]*)")
ADC_STATUS = re.compile(r"@ADC:STATUS:([^\r\n]*)")
MAP_IDENTITY = re.compile(r"@MAP:IDENTITY:([^\r\n]*)")
PWM_QUERY = re.compile(r"@PWM:([^\r\n]*)")
BRK_FRAME = re.compile(r"(@BRK:[^\r\n]*)")

FOC_STATE = {0: "IDLE", 1: "ARMED", 2: "RUNNING", 3: "COMPLETE",
             4: "ABORTED", 5: "FAULTED"}


def kv(text):
    out = {}
    for part in text.split(":"):
        if "=" in part:
            key, val = part.split("=", 1)
            out[key.strip()] = val.strip()
    return out


def to_int(value, default=None):
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return default


def first_line(text, pattern):
    out = None
    for match in pattern.finditer(text):
        out = match.group(1)
    return out

class Link(object):
    """Serial link with a raw transcript for the evidence file."""

    def __init__(self, port, baud, eol, log_path):
        self.port = port
        self.baud = baud
        self.eol = eol
        self.buf = ""
        self.fh = None
        if log_path:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            self.fh = open(log_path, "w", encoding="utf-8")
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        self.ser = serial.Serial(port, baud, timeout=0)

    def _record(self, text):
        if self.fh is not None:
            self.fh.write(text)
            self.fh.flush()

    def write(self, line):
        self._record("\n$ %s\n" % line)
        self.ser.write((line + self.eol).encode("ascii", "replace"))
        self.ser.flush()

    def pump(self, quiet=0.25, limit=2.0):
        """Read until the line is quiet for `quiet` s or `limit` s passed."""
        start = time.time()
        last = start
        while True:
            chunk = self.ser.read(4096)
            if chunk:
                text = chunk.decode("utf-8", "replace")
                self.buf += text
                self._record(text)
                last = time.time()
            now = time.time()
            if now - last >= quiet or now - start >= limit:
                break
            time.sleep(0.01)
        out = self.buf
        self.buf = ""
        return out

    def command(self, line, quiet=0.25, limit=2.0):
        self.write(line)
        return self.pump(quiet=quiet, limit=limit)

    def idle(self, seconds=1.0):
        start = time.time()
        while time.time() - start < seconds:
            chunk = self.ser.read(4096)
            if chunk:
                text = chunk.decode("utf-8", "replace")
                self.buf += text
                self._record(text)
            time.sleep(0.01)
        out = self.buf
        self.buf = ""
        return out

    def close(self):
        if self.fh is not None:
            self.fh.close()


def gate_foc(link, require_moe_off=False):
    """Fail-closed gate on a live @FOC frame. Returns (ok, reason, fields)."""
    body = first_line(link.idle(0.4), FOC_FRAME)
    if body is None:
        body = first_line(link.pump(quiet=0.6, limit=2.0), FOC_FRAME)
    if body is None:
        return False, "no live @FOC frame (is the telemetry alive?)", {}
    foc = kv(body)
    fault = to_int(foc.get("FAULT"), 0)
    fault_r = to_int(foc.get("FAULT_R"), 0)
    run = to_int(foc.get("RUN"), 0)
    if fault != 0 or fault_r != 0:
        return False, "latched FAULT=%s FAULT_R=%s (clear with 'f')" % (fault, fault_r), foc
    if run != 0:
        return False, "RUN=%s (stop with '0' first)" % run, foc
    if require_moe_off:
        pwm = kv(first_line(link.command("p?"), PWM_QUERY) or "")
        if pwm.get("CCER") not in (None, "0"):
            return False, "PWM CCER=%s (PWM must be off)" % pwm.get("CCER"), foc
        bdtr = to_int(pwm.get("BDTR"))
        if bdtr is not None and (bdtr & (1 << 15)) != 0:
            return False, "BDTR MOE=1 (PWM must be off)", foc
    return True, "", foc


def show(link, cmd, pattern, label):
    text = link.command(cmd)
    body = first_line(text, pattern)
    if body is None:
        print("  %-16s %s -> NO ANSWER" % (label, cmd))
        for line in text.strip().splitlines()[:3]:
            print("      | %s" % line.strip()[:160])
        return None
    print("  %-16s %s -> %s" % (label, cmd, body))


def cmd_status(link, _args):
    print("== status (read-only) ==")
    body = first_line(link.idle(0.6), FOC_FRAME)
    if body:
        foc = kv(body)
        print("  FOC              FAULT=%s FAULT_R=%s RUN=%s STATE=%s VBUS=%s mV "
              "map_crc32=%s Id_ref=%s em_stop=%s/%s"
              % (foc.get("FAULT"), foc.get("FAULT_R"), foc.get("RUN"), foc.get("STATE"),
                 foc.get("VBUS"), foc.get("map_crc32"), foc.get("Id_ref"),
                 foc.get("em_stop1"), foc.get("em_stop2")))
    show(link, "p?", PWM_QUERY, "PWM")
    show(link, "breakdiag", BRK_FRAME, "BRK")
    show(link, "a?", ADC_STATUS, "ADC")
    show(link, "mapcap status", MC_STATUS, "MC")
    show(link, "mapcap identity", MAP_IDENTITY, "MAP")
    return 0


def cmd_params(link, args, check_gate=True):
    print("== params: mp=%s + mpapply (RAM only) ==" % args.mp)
    ok, reason, _foc = gate_foc(link, require_moe_off=False)
    if not ok:
        print("  REFUSED: %s" % reason)
        return 2
    text = link.command("mp=%s" % args.mp)
    print("  mp               -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    if "@MP:OK" not in text:
        print("  FAIL: mp= not acknowledged")
        return 1
    text = link.command("mpapply")
    print("  mpapply          -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    if "@MPAPPLY:OK" not in text:
        print("  FAIL: mpapply not acknowledged")
        return 1
    if check_gate:
        print("  -- gate '1' (map still absent => fail-closed before PWM) --")
        text = link.command("1", quiet=0.4, limit=3.0)
        print("  '1'              -> %s" % " | ".join(
            line.strip() for line in text.strip().splitlines()[:3])[:300])
        if "FOC started" in text:
            print("  NOTE: FOC started -- sending '0' and re-checking the map state")
            link.command("0")
            return 1
    return 0


def cmd_calib(link, args):
    print("== calib: 'c' (ADC zero offsets, PWM off, DC link unpowered) ==")
    ok, reason, foc = gate_foc(link, require_moe_off=True)
    if not ok:
        print("  REFUSED: %s" % reason)
        return 2
    vbus = to_int(foc.get("VBUS"), 0)
    if vbus is not None and vbus > args.max_vbus_for_calib:
        print("  REFUSED: VBUS=%s mV > --max-vbus-for-calib=%s (unpower the DC link first)"
              % (vbus, args.max_vbus_for_calib))
        return 2
    text = link.command("c", quiet=0.5, limit=3.0)
    print("  c                -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    if "@ADC:CAL:FAIL" in text:
        print("  FAIL: offset calibration refused by the board")
        return 1
    if "@ADC:CAL:" not in text:
        print("  FAIL: no @ADC:CAL answer")
        return 1
    show(link, "a?", ADC_STATUS, "ADC")
    show(link, "mapcap identity", MAP_IDENTITY, "MAP")
    return 0


def cmd_arm(link, args):
    print("== arm: mcarm=%u (ADC injected armed; PWM stays off) ==" % args.profile_id)
    ok, reason, _foc = gate_foc(link)
    if not ok:
        print("  REFUSED: %s" % reason)
        return 2
    text = link.command("mcarm=%u" % args.profile_id, quiet=0.4, limit=3.0)
    print("  mcarm            -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    show(link, "mapcap status", MC_STATUS, "MC")
    if "@MC:ARM:BLOCKED" in text:
        return 1
    if "rc=-4" in text:
        print("  NOTE: rc=-4 = MAP_CAPTURE_OFFSET_INVALID: run 'calib' first")
        return 1
    if "rc=0" not in text:
        print("  FAIL: unexpected arm reply")
        return 1
    return 0


def cmd_abort(link, _args):
    text = link.command("mapcap abort")
    print("  abort            -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    show(link, "mapcap status", MC_STATUS, "MC")
    return 0

    return body



def cmd_capture(link, args):
    print("== capture: mcarm=%u + run + drain%s (ENERGIZING) ==" %
          (args.profile_id, " + build" if args.build else ""))
    if not args.allow_energize:
        print("  REFUSED: --allow-energize is required for a PWM service burst")
        return 2
    ok, reason, foc = gate_foc(link, require_moe_off=True)
    if not ok:
        print("  REFUSED: %s" % reason)
        return 2
    vbus = to_int(foc.get("VBUS"), 0)
    if vbus is None or vbus < args.vbus_min:
        print("  REFUSED: VBUS=%s mV < --vbus-min=%s mV (power the DC link first)"
              % (vbus, args.vbus_min))
        return 2
    rc = cmd_arm(link, args)
    if rc != 0:
        return rc
    text = link.command("mapcap run", quiet=0.4, limit=3.0)
    print("  run              -> %s" % " | ".join(
        line.strip() for line in text.strip().splitlines()[:2])[:200])
    if "@MC:RUN:rc=0" not in text:
        print("  FAIL: run not acknowledged")
        link.command("mapcap abort")
        return 1
    deadline = time.time() + args.timeout
    state = None
    fields = {}
    while time.time() < deadline:
        body = first_line(link.command("mapcap status"), MC_STATUS) or ""
        fields = kv(body)
        state = to_int(fields.get("state"))
        if state in (3, 4, 5):
            print("  final status     -> state=%s(%s) term=%s avail=%s vbus_mv=%s"
                  % (state, FOC_STATE.get(state, "?"), fields.get("term"),
                     fields.get("avail"), fields.get("vbus_mv")))
            break
        time.sleep(0.2)
    if state not in (3, 4, 5):
        print("  FAIL: capture did not reach a terminal state in %s s" % args.timeout)
        link.command("mapcap abort")
        return 1
    drain = link.command("mapcap drain", quiet=0.6, limit=6.0)
    records = len(re.findall(r"@MC:REC:", drain))
    board_n = first_line(drain, re.compile(r"@MC:DRAIN:records=(\d+)"))
    print("  drain            -> @MC:REC=%d (board says records=%s)" % (records, board_n))
    if args.build:
        text = link.command("mapcap build=%u" % args.profile_id, quiet=0.6, limit=6.0)
        print("  build            -> %s" % " | ".join(
            line.strip() for line in text.strip().splitlines()[:2])[:200])
        show(link, "mapcap status", MC_STATUS, "MC")
    if state != 3:
        print("  NOTE: capture terminal state is not COMPLETE (state=%s)" % state)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=["status", "params", "calib", "arm",
                                        "capture", "abort"])
    parser.add_argument("profile_id", nargs="?", type=int,
                        help="BOAR profile id for mcarm=, e.g. 1112490322")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--eol", default="\r", help="line ending the CLI expects")
    parser.add_argument("--mp", default="15120,19410", help="Rs_mOhm,Ls_uH for mp=")
    parser.add_argument("--out", default=None, help="raw transcript file")
    parser.add_argument("--allow-energize", action="store_true",
                        help="required by 'capture': enables a PWM service burst")
    parser.add_argument("--vbus-min", type=int, default=20000,
                        help="mV required for 'capture'")
    parser.add_argument("--max-vbus-for-calib", type=int, default=1000,
                        help="mV allowed while running 'c'")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="seconds to wait for a terminal capture state")
    parser.add_argument("--build", action="store_true",
                        help="call mapcap build=<id> after drain")
    parser.add_argument("--no-gate-check", action="store_true",
                        help="'params': skip the '1' gate probe")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd in ("arm", "capture") and args.profile_id is None:
        print("refusing: 'arm'/'capture' need a profile id")
        return 2
    try:
        link = Link(args.port, args.baud, args.eol, args.out)
    except Exception as exc:  # port/serial failure is infra, not a verdict
        print("INFRA: cannot open %s: %s" % (args.port, exc))
        return 2
    try:
        if args.cmd == "status":
            return cmd_status(link, args)
        if args.cmd == "params":
            return cmd_params(link, args, check_gate=not args.no_gate_check)
        if args.cmd == "calib":
            return cmd_calib(link, args)
        if args.cmd == "arm":
            return cmd_arm(link, args)
        if args.cmd == "capture":
            return cmd_capture(link, args)
        return cmd_abort(link, args)
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
