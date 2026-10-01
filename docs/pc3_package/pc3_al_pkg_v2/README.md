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

## Вариант сборки — обязательное условие (не production)

Прошивать **production-артефакт `firmware` нельзя**: в нём (сборка `make` без
defines, `.github/workflows/ci.yml` @4507983) `PWM_AlignApertureStart()` —
default-deny заглушка, возвращающая `PWM_ENABLE_INTERLOCK_OPEN`
(`src/pwm.c`), поэтому `al` не создаёт вектор и отвечает
`@AL:FAIL:start (PWM off, no fault, SD high, ADC idle)`; команд
`mapcap …`/`mapload` в нём тоже нет (тело `cli_mapcap_command` компилируется
как `return 0`, `main.c:644-646`) — то есть гейт `-2 map_unverified`
недостижим в принципе.

Для этого пакета нужен артефакт CI **`firmware-commissioning`**, собранный из
того же source commit с defines:

```
-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7
-DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1 -DOEW_HS1_COMMISSIONING_RELEASE=1
```

(тот же набор + `-DOEW_BENCH_APERTURE=1` — артефакт `firmware-bench-aperture`,
маркеры PA4/PA5; для `al` он не требуется). Какой именно артефакт прошит —
записать в лог прогона строкой с префиксом.

Отдельно: набор commissioning **содержит синтетический профиль** (оба гейта
`OEW_MAP_SYNTHETIC_PROFILE` и `OEW_HOST_TEST` определены,
`src/map_capture_profiles.c:33-38`). Карта, полученная через `mapcap build=` с
профилем `0x53594E54` ("SYNT"), физическим доказательством НЕ является;
для снятия гейта `-2` используется `mapload <994 hex>` измеренного артефакта
либо board-профиль "BOAR" `0x424F4152`.

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
