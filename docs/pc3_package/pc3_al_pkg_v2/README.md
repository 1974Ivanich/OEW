# PC3 FOC package — align-check edition (final, docs ред. 2)

Статус: физическая квалификация `al` НЕ ПРОЙДЕНА.
Пакет — артефакт передачи для стендового прогона; результат прогона
определяет следующий шаг.

Frozen scan identity не изменяется этим пакетом.
Данные, полученные на source commit 4507983, не относятся к frozen scan.

Docs этого пакета — редакция 2: команды, ack-строки и числа сверены с
фактическим деревом `4507983` (`git show 4507983:<файл>`), а не со справочным
срезом. Перечень правок и доказательства — CORRECTIONS_v2.md. Прошивка и её
хеш ред. 2 не затрагивает.

Состав:
- firmware/            образ source commit 4507983 (bin/elf/map)
- docs/COMMANDS.md     CLI-справочник, сверенный с кодом
- docs/AL_PROCEDURE.md стендовая процедура align-check
- docs/SAFETY.md       защитный envelope для al и маршрута B
- docs/IDENTITIES.md   две идентичности пакета
- CORRECTIONS_v2.md    что исправлено против ред. 1 и чем доказано
- manifest.json, SHA256SUMS

Применение:
1. Верификация (раздел «Верификация» ниже).
2. Flash firmware/pc3_foc_4507983.bin (ST-Link V4, SWD).
3. Прогон docs/AL_PROCEDURE.md с лимитом источника из docs/SAFETY.md,
   порядок: префлайт (7 команд) → `al` → срединный `dumpa` (ожидается
   JADSTART = 1) → живая `@FOC` в удержании (знак, устойчивость, runaway) →
   `al off` → пост-мортем (5 команд).
4. По результату: параметры двигателя → pp=3 → первое окно `i=<Id>,<Iq>` →
   короткий `1`, либо разбор конкретного наблюдаемого симптома.

Никакого дополнительного gate перед этим прогоном нет.

## Верификация

```bash
tar -xzf pc3-pkg-al-final-v2.tar.gz && cd pc3-pkg-al-final-v2
sha256sum -c SHA256SUMS                              # все OK
grep binary_sha256 manifest.json                     # сверить с .bin
sha256sum -c ../pc3-pkg-al-final-v2.tar.gz.sha256    # архив
```

PowerShell (ручной ввод, хеши по SHA256SUMS.txt):

```powershell
Get-FileHash .\SHA256SUMS.txt -Algorithm SHA256
$hash = (Get-FileHash .\firmware\pc3_foc_4507983.bin -Algorithm SHA256).Hash.ToLower()
Select-String -Path .\SHA256SUMS -Pattern $hash
```
