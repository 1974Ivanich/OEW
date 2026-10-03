# -*- coding: utf-8 -*-
"""Триаж очереди origin/*: что реально изменится в main, если ветку влить.

Аудит (scripts/queue_audit.py) отвечает на вопрос «что ветка заявляет»: считает её
собственные файлы (git diff --name-only main...<ref>) и классифицирует их. Триаж
отвечает на вопрос «что изменится в main» и делает это без изменения рабочего
дерева — сухим merge через git merge-tree --write-tree (git >= 2.38).

Для каждой ветки с коммитами вне main:
  * ahead/behind относительно main; последний коммит (дата, автор, тема);
  * СУХОЙ MERGE: дерево результата влития, чисто/конфликты, список конфликтных
    файлов (stage-строки 1/2/3 в выводе merge-tree);
  * ДЕЛЬТА ВЛИТИЯ: git diff --name-status main <tree> — файлы, которые влитие
    реально изменит в main (M/A/D). Пустая дельта = влитие ничего не изменит →
    ветку можно закрывать без потерь;
  * корзина решения: пусто-по-содержимому / только-журнал / safety / код / docs.

Оговорки:
  * merge-tree пишет в object DB только деревья результата (ссылок не создаёт),
    рабочее дерево и индекс не трогает;
  * при конфликте версии файлов в дереве содержат маркеры конфликта: в дельту они
    попадают, но вливать их нельзя — нужен ручной разбор (столбец «Конфликтов»);
  * docs/AGENTS_STATUS.md правился в main (a853ce3, 15498c6 — ремонт рендера
    таблиц), поэтому строку журнала ветки переносят вручную, а не merge'ем.

Запуск: py -3 scripts/queue_triage.py [--md]
Пишет build/queue_triage.txt (+ --md), печатает в stdout.
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
TREE_RE = re.compile(r'^[0-9a-f]{40}$')
STAGE_RE = re.compile(r'^[0-7]{6} [0-9a-f]{40} ([123])\t(.+)$')
TXT = os.path.join('build', 'queue_triage.txt')
MD = os.path.join('build', 'queue_triage.md')

# порядок корзин = порядок действий: закрытие, ТЗ, приёмка, docs
BUCKETS = [
    ('пусто-по-содержимому', 'влитие ничего не меняет в main — закрыть без потерь'),
    ('только-журнал', 'влитие меняет только строку docs/AGENTS_STATUS.md — закрыть'),
    ('safety', 'модули защиты/привода и main.c/Makefile/.ioc — только по явному ТЗ'),
    ('код', 'прочий src/* — публикация + CI + приёмка'),
    ('docs', 'docs/tools/tests/scripts — doc-пакет, вливать по сверке'),
]
BUCKET_ORDER = [b for b, _ in BUCKETS]


def git(*args):
    p = subprocess.run(['git'] + list(args), capture_output=True)
    return p.stdout.decode('utf-8', 'replace')


def merge_dry_run(ref):
    """Сухой merge main + ref: (tree, конфликты, дельта [(M/A/D, путь)])."""
    p = subprocess.run(['git', 'merge-tree', '--write-tree', 'main', ref],
                       capture_output=True)
    out = p.stdout.decode('utf-8', 'replace').splitlines()
    tree = out[0].strip() if out and TREE_RE.match(out[0].strip()) else None
    conflicts = []
    for line in out:
        m = STAGE_RE.match(line)
        if m and m.group(2) not in conflicts:
            conflicts.append(m.group(2))
    delta = []
    if tree:
        for line in git('diff', '--name-status', 'main', tree).splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                delta.append((parts[0][:1], parts[-1]))
    return tree, conflicts, delta


def bucket_of(delta, safety, code):
    paths = [p for _, p in delta]
    if not paths:
        return 'пусто-по-содержимому'
    if paths == [JOURNAL]:
        return 'только-журнал'
    if safety:
        return 'safety'
    if code:
        return 'код'
    return 'docs'


def blobs(keys):
    """OID blob'а для каждого ключа ('main:path' / 'origin/x:path'); None — нет объекта.
    Один процесс git cat-file --batch-check на весь список."""
    if not keys:
        return []
    p = subprocess.run(['git', 'cat-file', '--batch-check'],
                       input=('\n'.join(keys) + '\n').encode(), capture_output=True)
    oids = []
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        parts = line.split()
        oids.append(parts[0] if len(parts) >= 2 and parts[1] == 'blob' else None)
    return oids


def overlaps(rows):
    """Файлы, которые приносят ≥2 ветви: сколько разных версий содержимого и кто их
    приносит. Нужно, чтобы не вливать одно и то же дважды и знать, где выбрать
    самую позднюю версию."""
    owners = {}
    for r in rows:
        for s, f in r['delta']:
            if s != 'D':
                owners.setdefault(f, []).append(r)
    out = []
    for f, rs in sorted(owners.items()):
        if len(rs) < 2:
            continue
        oids = blobs([r['ref'] + ':' + f for r in rs])
        out.append({'file': f, 'rows': rs, 'versions': len(set(o for o in oids if o))})
    out.sort(key=lambda o: (-len(o['rows']), -o['versions'], o['file']))
    return out


def collect():
    ab = git('for-each-ref', '--format=%(ahead-behind:main) %(refname:short)',
             'refs/remotes/origin').splitlines()
    journal_main = git('show', 'HEAD:' + JOURNAL)
    rows, empty = [], []
    for line in ab:
        p = line.split()
        if len(p) != 3 or not p[0].isdigit():
            continue
        if p[2] in ('origin/main', 'origin/HEAD') or p[2].endswith('/HEAD'):
            continue
        ahead, behind, name = int(p[0]), int(p[1]), p[2][len('origin/'):]
        if not ahead:
            empty.append(name)
            continue
        ref = 'origin/' + name
        meta = git('log', '-1', '--format=%cd|%an|%s', '--date=short', ref).strip()
        parts = (meta.split('|', 2) + ['', '', ''])[:3]
        tree, conflicts, delta = merge_dry_run(ref)
        paths = [f for _, f in delta]
        code = [f for f in paths if CODE.search(f)]
        safety = [f for f in code if SAFETY.search(f)]
        rows.append({'name': name, 'ref': ref, 'ahead': ahead, 'behind': behind,
                     'date': parts[0], 'author': parts[1], 'subject': parts[2],
                     'tree': tree, 'conflicts': conflicts, 'delta': delta,
                     'code': code, 'safety': safety,
                     'in_main': name in journal_main,
                     'journal': JOURNAL in paths,
                     'add': sum(1 for s, _ in delta if s == 'A'),
                     'mod': sum(1 for s, _ in delta if s == 'M'),
                     'del': sum(1 for s, _ in delta if s == 'D'),
                     'bucket': bucket_of(delta, safety, code)})
    rows.sort(key=lambda r: (BUCKET_ORDER.index(r['bucket']), r['name']))
    return rows, empty


def render_txt(rows, empty, head):
    L = ['ТРИАЖ ОЧЕРЕДИ ВЕТОК origin относительно main (сухой merge), срез %s' % head, '']
    L.append('Веток на origin (кроме main): %d; без уникальных коммитов: %d; '
             'с коммитами вне main: %d' % (len(rows) + len(empty), len(empty), len(rows)))
    for b, why in BUCKETS:
        L.append('  %-22s %3d — %s' % (b, sum(1 for r in rows if r['bucket'] == b), why))
    L.append('  конфликтный сухой merge: %d; меняют %s: %d'
             % (sum(1 for r in rows if r['conflicts']), JOURNAL,
                sum(1 for r in rows if r['journal'])))
    L.append('  имя ветки уже в журнале main: %d' % sum(1 for r in rows if r['in_main']))
    L.append('')
    for b, why in BUCKETS:
        sel = [r for r in rows if r['bucket'] == b]
        if not sel:
            continue
        L.append('=== %s (%d) — %s' % (b, len(sel), why))
        for r in sel:
            L.append('  %s  (ahead %d, behind %d)' % (r['name'], r['ahead'], r['behind']))
            L.append('      %s | %s | %s' % (r['date'], r['author'], r['subject']))
            L.append('      влитие изменит файлов: %d (M:%d A:%d D:%d), конфликтов: %d,'
                     ' журнал: %s, имя в журнале main: %s'
                     % (len(r['delta']), r['mod'], r['add'], r['del'],
                        len(r['conflicts']), 'да' if r['journal'] else 'нет',
                        'да' if r['in_main'] else 'нет'))
            for f in r['conflicts']:
                L.append('        ! конфликт: %s' % f)
            for s, f in r['delta']:
                L.append('        - [%s] %s' % (s, f))
        L.append('')
    ov = overlaps(rows)
    L.append('ПЕРЕСЕЧЕНИЯ (%d файлов приносят ≥2 ветвей) — вливать один раз, сверив версию:'
             % len(ov))
    for o in ov:
        L.append('  %s — ветвей: %d, разных версий содержимого: %d'
                 % (o['file'], len(o['rows']), o['versions']))
        for r in o['rows']:
            L.append('      %s (%s)' % (r['name'], r['date']))
    L.append('')
    L.append('B. Ветки без уникальных коммитов (%d): %s' % (len(empty), ', '.join(empty)))
    return '\n'.join(L) + '\n'


def render_md(rows, head):
    L = ['Срез `main` = %s. Сухой merge (merge-tree) для %d ветвей с коммитами вне `main`.'
         % (head, len(rows)), '',
         '| Ветка | Корзина | ahead | behind | Влитие изменит | M | A | D | Конфликтов |'
         ' Журнал | Имя в журнале | Коммит | Автор | Тема последнего коммита |',
         '|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|']
    for r in rows:
        subj = r['subject'].replace('|', '\\|')
        L.append('| `%s` | %s | %d | %d | %d | %d | %d | %d | %d | %s | %s | %s | %s | %s |'
                 % (r['name'], r['bucket'], r['ahead'], r['behind'], len(r['delta']),
                    r['mod'], r['add'], r['del'], len(r['conflicts']),
                    'да' if r['journal'] else '—', 'да' if r['in_main'] else '—',
                    r['date'], r['author'], subj))
    ov = overlaps(rows)
    L += ['', '## Пересечения: файлы, которые приносят ≥2 ветвей', '',
          '`%d` файлов приносят несколько ветвей — вливать такой файл нужно один раз,' % len(ov),
          'сверив версии (разные версии = разные версии содержимого в ветвях).', '',
          '| Файл | Ветвей | Разных версий | Ветви (дата) |', '|---|---:|---:|---|']
    for o in ov:
        who = ', '.join('%s (%s)' % (r['name'], r['date']) for r in o['rows'])
        L.append('| `%s` | %d | %d | %s |' % (o['file'].replace('|', '\\|'),
                                              len(o['rows']), o['versions'], who))
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
        with open(MD, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(render_md(rows, head))
        print('ЗАПИСАНО: %s' % MD)


if __name__ == '__main__':
    main()

