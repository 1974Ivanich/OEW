#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only зонд словаря команд образа на плате (без HV, без инъекций).

Отвечает на один вопрос, из-за которого в ред. 2 пакета появился B17:
«есть ли в прошитом образе mapload / mapcap build» — или «unknown» пришло
из-за неверной формы команды.

Что отправляется (всё читающее или синтаксически-проверочное):
  p?, breakdiag        — состояние регистров/снапшот break (чтение);
  mapcap status        — статус захвата (чтение);
  mapcap identity      — identity карты (чтение, ветка #if OEW_MAP_L3);
  mapload 00           — 2 hex-символа: длина != 994*2 -> @MAP:LOAD:FAIL:DECODE,
                         разбор не начинается, карта НЕ грузится, состояние НЕ меняется;
  mapcap build=        — пустой аргумент -> err: mapcap build=<profile>, действий НЕТ.

НЕ отправляются: 1 / 0 / i= / c / f / a / mp= / mpapply / mcarm= / mapcap run|drain|abort.

Fail-closed: если @FOC показывает RUN != 0 или FAULT != 0 — прогон останавливается
до зондирования (ERROR, код 2), плата не трогается.

Запуск: py -3 build/cap_probe.py --port COM4 --out docs/evidence/.../09_capability_probe_raw.log
"""
from __future__ import annotations

import argparse
import re
import sys
import time

import serial

COMMANDS = [
    "p?",
    "breakdiag",
    "mapcap status",
    "mapcap identity",
    "mapload 00",
    "mapcap build=",
]

# В кадре @FOC порядок полей: ...:FAULT=0:FAULT_R=0:FAIL=0:RUN=0:... — читаем
# каждое поле отдельно (порядок в регекспе, ожидающий RUN перед FAULT, молча
# давал «frames=0» и гейт безопасности не проверялся — проверено на живой плате).
RUN_RE = re.compile(r'(?<![A-Za-z_])RUN=(-?\d+)')
FAULT_RE = re.compile(r'(?<![A-Za-z_])FAULT=(-?\d+)')
FOC_TAG = '@FOC:'
# Целый кадр телеметрии: от @FOC: до последнего поля em_stop2=<n>.
FRAME_RE = re.compile(r'@FOC:.*?em_stop2=-?\d+', re.S)


def collapse_stream(text):
    """Поток ответа с свёрнутыми кадрами @FOC: порядок строк сохранён, ничего не скрыто."""
    text = FRAME_RE.sub(' @FOC:<frame> ', text)
    out = []
    for raw in text.replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line or line.startswith('> '):
            continue
        if 'em_stop2=' in line or 'run_id=' in line:
            continue
        out.append(line)
    joined = ' '.join(out)
    while '  ' in joined:
        joined = joined.replace('  ', ' ')
    return joined


def strip_telemetry(text):
    """Убрать периодическую телеметрию @FOC (100 мс) и эхо команды.

    Читающий порт отдаёт порциями, поэтому кадр может разорваться на границе
    чтения — после удаления целых кадров остаются хвосты (`…:run_id=…` или
    `…:em_stop2=1`); их тоже убираем, чтобы в таблице вердиктов был ТОЛЬКО
    ответ платы на команду (иначе в доказательство попадает чужая строка).
    """
    text = FRAME_RE.sub('', text)
    keep = []
    for raw in text.replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line or line.startswith('> ') or line.startswith('@FOC'):
            continue
        if 'em_stop2=' in line or 'run_id=' in line:
            continue
        keep.append(line)
    return '\n'.join(keep)


def pump(ser, per_cmd_s=2.0, quiet_s=0.3):
    deadline = time.time() + per_cmd_s
    last = time.time()
    got = []
    while time.time() < deadline:
        pending = ser.in_waiting
        if pending:
            got.append(ser.read(pending).decode('ascii', 'replace'))
            last = time.time()
        elif got and time.time() - last >= quiet_s:
            break
        else:
            time.sleep(0.02)
    return ''.join(got)


def send(ser, cmd, eol):
    ser.write((cmd + eol).encode('ascii'))
    return pump(ser)


def lines_of(text, token):
    return [ln.strip() for ln in text.replace('\r', '\n').split('\n')
            if ln.strip().startswith(token)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--out', required=True)
    ap.add_argument('--eol', default='\\r\\n')
    args = ap.parse_args()
    eol = args.eol.encode('ascii').decode('unicode_escape')

    ser = serial.Serial(args.port, args.baud, timeout=0, write_timeout=1)
    ser.reset_input_buffer()
    log = []

    def record(text):
        log.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()

    record('# capability probe: port=%s baud=%d commands=%s\n'
           % (args.port, args.baud, ','.join(COMMANDS)))
    record('# idling 0.8 s: read the periodic @FOC telemetry for the safety gate\n')
    idle = pump(ser, per_cmd_s=0.8)
    frames = idle.count(FOC_TAG)
    runs = [int(m.group(1)) for m in RUN_RE.finditer(idle)]
    faults = [int(m.group(1)) for m in FAULT_RE.finditer(idle)]
    record('# @FOC frames=%d RUN=%s FAULT=%s\n'
           % (frames, runs[-1] if runs else 'NA', faults[-1] if faults else 'NA'))
    if frames == 0 or not runs or not faults:
        record('# ABORT: нет кадров @FOC (RUN/FAULT не подтверждены) -> '
               'зонд не выполняется (fail-closed)\n')
        open(args.out, 'w', encoding='utf-8', newline='\n').write(''.join(log))
        return 2
    if runs[-1] != 0:
        record('# ABORT: RUN != 0 -> зонд не выполняется (fail-closed)\n')
        open(args.out, 'w', encoding='utf-8', newline='\n').write(''.join(log))
        return 2
    if faults[-1] != 0:
        record('# ABORT: FAULT != 0 -> зонд не выполняется (fail-closed)\n')
        open(args.out, 'w', encoding='utf-8', newline='\n').write(''.join(log))
        return 2

    verdicts = []
    for cmd in COMMANDS:
        record('\n> %s\n' % cmd)
        ans = send(ser, cmd, eol)
        record('# stream (кадры @FOC свёрнуты, порядок сохранён): %s\n'
               % collapse_stream(ans))
        body = strip_telemetry(ans)
        record(body + '\n' if body else 'NO ANSWER\n')
        verdicts.append((cmd, body))

    record('\n# --- verdict table: только ответ на команду (100-мс @FOC удалён,\n'
           '# --- дословные строки выше; порядок команд = порядок в шапке) ---\n')
    for cmd, body in verdicts:
        first = body.split('\n')[0] if body else 'NO ANSWER'
        record('%-18s %s\n' % (cmd, first))

    with open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(''.join(log))
    print('\nraw log: %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
