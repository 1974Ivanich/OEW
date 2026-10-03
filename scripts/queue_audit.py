# -*- coding: utf-8 -*-
"""Аудит "невидимой очереди": ветки origin/*, коммитов которых нет в истории main.

Зачем: агенты объявляют пакеты в docs/AGENTS_STATUS.md (и в своих копиях журнала),
но ветки могут не попадать в main. Скрипт показывает реальное состояние: сколько
коммитов вне main, затрагивают ли они код (src/, main.c, Makefile, .ioc) и не
является ли ветка однострочной правкой журнала.

Классификация файлов:
  * код     — src/, main.c, Makefile, .ioc (код => только приёмка по ТЗ)
  * safety  — подмножество кода: foc/protect/vf/adc/adc_dispatch/pwm/pwm_board_pins
              (+ main.c, Makefile, .ioc) — правки таких модулей только по явному ТЗ
  * docs    — docs/, tools/, tests/, scripts/ (документ-пакеты)
  * только-журнал — единственный отличающийся файл docs/AGENTS_STATUS.md
                    (содержимого нет => кандидат на закрытие/гигиену origin)

Запуск: py -3 scripts/queue_audit.py [--md]
Пишет build/queue_audit.txt и (с --md) build/queue_audit.md, печатает в stdout.
Требует git >= 2.36 (%(ahead-behind:) в for-each-ref).
"""
import io
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SAFETY = re.compile(r'(^|/)(foc|protect|vf|adc|adc_dispatch|pwm|pwm_board_pins)\.(c|h)$'
                    r'|\.ioc$|^Makefile$|^main\.c$')
CODE = re.compile(r'^src/|^main\.c$|^Makefile$|\.ioc$')
JOURNAL = 'docs/AGENTS_STATUS.md'
TXT = os.path.join('build', 'queue_audit.txt')
MD = os.path.join('build', 'queue_audit.md')


def git(*args):
    p = subprocess.run(['git'] + list(args), capture_output=True)
    return p.stdout.decode('utf-8', 'replace')


def journals_batch(refs, path=JOURNAL):
    """Содержимое <ref>:<path> для всех refs одним процессом git cat-file --batch."""
    if not refs:
        return {}
    keys = ['%s:%s' % (r, path) for r in refs]
    p = subprocess.run(['git', 'cat-file', '--batch'],
                       input=('\n'.join(keys) + '\n').encode(), capture_output=True)
    raw, out, pos = p.stdout, {}, 0
    for ref in refs:
        nl = raw.find(b'\n', pos)
        header = raw[pos:nl].decode('utf-8', 'replace')
        if ' missing' in header or not header.strip():
            out[ref] = ''
            pos = nl + 1
            continue
        size = int(header.rsplit(' ', 1)[1])
        out[ref] = raw[nl + 1:nl + 1 + size].decode('utf-8', 'replace')
        pos = nl + 1 + size + 1
    return out


def collect():
    ab = git('for-each-ref', '--format=%(ahead-behind:main) %(refname:short)',
             'refs/remotes/origin').splitlines()
    refs, ahead = [], {}
    for line in ab:
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        if parts[2] in ('origin/main', 'origin/HEAD') or parts[2].endswith('/HEAD'):
            continue
        refs.append(parts[2])
        ahead[parts[2]] = int(parts[0])
    status = git('show', 'HEAD:' + JOURNAL)
    journals = journals_batch([r for r in refs if ahead[r]])
    rows, empty = [], []
    for ref in sorted(refs):
        name = ref[len('origin/'):]
        if not ahead[ref]:
            empty.append(name)
            continue
        files = [f for f in git('diff', '--name-only', 'main...' + ref).splitlines() if f.strip()]
        code = [f for f in files if CODE.search(f)]
        rows.append({'name': name, 'ahead': ahead[ref], 'in_main': name in status,
                     'self_decl': name in journals.get(ref, ''),
                     'files': files, 'code': code,
                     'safety': [f for f in code if SAFETY.search(f)],
                     'journal_only': files == [JOURNAL]})
    return rows, empty


def kind_of(row):
    if not row['code']:
        return 'только-журнал' if row['journal_only'] else 'docs'
    return 'код' if not row['safety'] else 'код (safety)'


def sort_key(row):
    return (kind_of(row) != 'код (safety)', not row['code'], not row['in_main'], row['name'])


def render_txt(rows, empty, head):
    L = ['АУДИТ ОЧЕРЕДИ ВЕТОК origin относительно main, срез %s' % head, '']
    L.append('Всего веток на origin: %d' % (len(rows) + len(empty)))
    L.append('  * без уникальных коммитов (дубликаты/пустые): %d — кандидаты на гигиену origin'
             % len(empty))
    L.append('  * с коммитами вне истории main: %d' % len(rows))
    L.append('      - только строка в docs/AGENTS_STATUS.md (содержимого нет): %d'
             % sum(1 for r in rows if r['journal_only']))
    L.append('      - затрагивают код (src/, main.c, Makefile, .ioc): %d, из них safety: %d'
             % (sum(1 for r in rows if r['code']), sum(1 for r in rows if r['safety'])))
    L.append('      - упомянуты в журнале main: %d; самообъявление в своей копии журнала: %d'
             % (sum(1 for r in rows if r['in_main']), sum(1 for r in rows if r['self_decl'])))
    L.append('')
    L.append('%-46s %5s %8s %9s %7s %s' % ('ветка', 'комм.', 'в журн.', 'самообъ.', 'файлов',
                                           'тип : файлы'))
    for r in sorted(rows, key=sort_key):
        detail = ', '.join(r['safety']) or ', '.join(r['code'])
        L.append('%-46s %5d %8s %9s %7d %s' % (
            r['name'], r['ahead'], 'да' if r['in_main'] else '-', 'да' if r['self_decl'] else '-',
            len(r['files']), kind_of(r) + ((' : ' + detail) if detail else '')))
    L.append('')
    L.append('Ветки, у которых нет содержимого, кроме строки журнала: %s'
             % (', '.join(r['name'] for r in rows if r['journal_only']) or 'нет'))
    L.append('')
    L.append('B. Ветки без уникальных коммитов (%d): %s' % (len(empty), ', '.join(empty)))
    return '\n'.join(L) + '\n'


def render_md(rows, empty, head):
    L = ['Срез `main` = %s. Веток на `origin`: **%d**; без уникальных коммитов: **%d**; '
         'с коммитами вне истории `main`: **%d** (затрагивают код: **%d**, из них safety: **%d**).'
         % (head, len(rows) + len(empty), len(empty), len(rows),
            sum(1 for r in rows if r['code']), sum(1 for r in rows if r['safety'])), '']
    L.append('| Ветка | Коммитов вне main | В журнале main | Самообъявление | Файлов | Тип | Ключевые файлы |')
    L.append('|---|---:|---|---|---:|---|---|')
    for r in sorted(rows, key=sort_key):
        detail = ', '.join(r['safety']) or ', '.join(r['code'])
        L.append('| `%s` | %d | %s | %s | %d | %s | %s |' % (
            r['name'], r['ahead'], 'да' if r['in_main'] else '—', 'да' if r['self_decl'] else '—',
            len(r['files']), kind_of(r), detail or '—'))
    return '\n'.join(L) + '\n'


def main():
    head = git('log', '-1', '--format=%h (%cd)', '--date=short').strip()
    rows, empty = collect()
    text = render_txt(rows, empty, head)
    with open(TXT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    print(text, end='')
    print('ЗАПИСАНО: %s' % TXT)
    if '--md' in sys.argv[1:]:
        md = render_md(rows, empty, head)
        with open(MD, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(md)
        print('ЗАПИСАНО: %s' % MD)


if __name__ == '__main__':
    main()
