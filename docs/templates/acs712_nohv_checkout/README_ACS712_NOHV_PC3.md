# Пакет ПК-3 — no-HV checkout ACS712 scope-reference

Назначение: проверить проводку двух датчиков ACS712-20A (CH1=фаза U, CH2=фаза V) и измерить их zero-current output offsets (v0_U, v0_V) при обесточенном моторе/DC-link. Полученные значения записываются в `acs712_calibration.json` и используются позже при ingest mV-CSV для grid-кампании BOAR.

Классификация: **no-HV / de-energized checkout**. Запрещены подача DC-link, команды `mcarm`, `mapcap run/drain/build`, FOC/Vf/autotune, а также любое energizing управление мотором.

## Предварительные проверки проводки (operator done)

- [ ] ACS712 U включён последовательно в фазный провод U (не в DC-link shunt).
- [ ] ACS712 V включён последовательно в фазный провод V.
- [ ] CH1 осциллографа — выход ACS712 U; CH2 — выход ACS712 V.
- [ ] Питание датчиков — **внешние 5.0 В** (не 3.3 В с J2).
- [ ] GND датчиков и GND осциллографа общие; силовая сторона ACS712 остаётся гальванически развязанной.
- [ ] Выходной фильтр CF ≤ 1 нФ; **47 нФ не установлен**.

## Hard gates перед включением питания

| ID | Gate | PASS критерий | Evidence / подпись |
|---|---|---|---|
| G-01 | DC-link | Обе шины физически отсоединены; DMM < 1 В каждая | |
| G-02 | PC4 | Нет внешнего источника/имитатора VBUS на PC4 | |
| G-03 | Aux supply | Только approved low-voltage aux: Nucleo USB/ST-Link и 5 В для ACS712 | |
| G-04 | Emergency | Оператор может обесточить aux по локальной процедуре | |
| G-05 | Firmware identity | Свежий `origin/main`, green CI, default-deny production образ | SHA/run: |
| G-06 | Command boundary | Только `sysinfo`, `p?`, `pdump`, `c`; никаких energizing команд | |

> **PC-3 reality check (2026-09-04):** the production default-deny image does **not** expose `mapcap status` / `@MC:STATUS` — those commands are compiled only when `OEW_MAP_CAPTURE` is defined (`main.c` line 493). This is expected and acceptable for this de-energized checkout, because `G-06` forbids map-capture commands and only permits non-energizing observation commands (`sysinfo`, `p?`, `pdump`, `c`).

## Компоновка кампании (вне Git)

```text
D:\campaign_raw\acs712_nohv_YYYYMMDDTHHMMSSZ\
├── identity/
│   ├── source_sha.txt
│   ├── ci_run_url.txt
│   └── flash_verify.log
├── dmm/
│   └── zero_current_offsets.md
├── scope/
│   └── scope_zero.csv
├── calibration/
│   └── acs712_calibration.json
├── observations/
│   └── wiring_check.md
└── summary/
    └── acs712_nohv_summary.md
```

## Критическое расхождение: firmware identity на ПК-3

ПК-3 отчитался о следующем состоянии (2026-09-04):

- Текущая ветка на ПК-3 — `ai2/sd-monitor-only` (`cfcbeae`), а не `main`.
- Коммит `6955672` (упомянутый в упаковке приёмки) **не существует** ни локально, ни на `origin/main` (`6a91312`), ни на GitFlic.
- В рабочей копии ПК-3 лежат незакоммиченные zip-пакеты приёмки и локальная правка `scripts/cubemx_check_script.txt`.
- `build/firmware.bin` — сборка от 01.09 образа `sd-monitor-only`, не `main`.

**Решение по G-05:** перед физическим no-HV checkout ПК-3 должен перейти на свежий `origin/main`, выполнить `make clean && make` (default-deny production образ) и зашить его. `ai2/sd-monitor-only` уже влита в `main` (merge `af03126`), поэтому образ из `main` содержит те же SD-monitor изменения, но даёт известный SHA/CI/run для evidence. Существующий `build/firmware.bin` от 01.09 **не принимается** как identity evidence.

Если по какой-либо причине пересборка/перепрошивка с `origin/main` невозможна, checkout блокируется на G-05 и Phase 1 не может быть признан PASS.

## Runbook для оператора ПК-3

### Шаг 0. Выбрать рабочее дерево

Ветка `main` занята рабочим деревом `C:\Users\MyHome\Documents\GitHub\OEW-accept-main`. Варианты:

**A.** Использовать дерево `OEW-accept-main`:

```powershell
cd C:\Users\MyHome\Documents\GitHub\OEW-accept-main
git checkout main
git pull origin main
```

**B.** Или в текущем дереве перейти на detached HEAD:

```powershell
git checkout --detach origin/main
```

### Шаг 1. Собрать и зашить default-deny production образ

```powershell
$Campaign = "D:\campaign_raw\acs712_nohv_$(Get-Date -Format 'yyyyMMddTHHmmssZ')"
New-Item -ItemType Directory -Path "$Campaign\identity", "$Campaign\dmm", "$Campaign\scope", "$Campaign\calibration", "$Campaign\observations", "$Campaign\summary" -Force

git log -1 --pretty=format:'%H %ci' > "$Campaign\identity\source_sha.txt"
# Записать URL зелёного CI run для этого SHA вручную:
notepad "$Campaign\identity\ci_run_url.txt"

make clean
make 2>&1 | Tee-Object -FilePath "$Campaign\identity\build.log"
make flash 2>&1 | Tee-Object -FilePath "$Campaign\identity\flash_verify.log"
```

> **Требование:** `flash_verify.log` должен содержать успешную верификацию (например, `Verify OK` или `Flash done`). Если прошивка не удалась — STOP, не переходить к измерениям.

### Шаг 2. Проверить identity и command boundary

Открыть терминал на VCP платы (например, PuTTY/Putty или `python tools/serial_repl.py COMxx 115200`) и начать непрерывную запись в файл.

```text
sysinfo
p?
pdump
c
```

Ожидается:

- `sysinfo` выдаёт SHA/branch/версию, совпадающую с `source_sha.txt`.
- `p?` / `pdump` показывают `MOE=0`, нет активного PWM.
- Команды `mcarm=...`, `mapcap run`, `mapcap status`, `foc`, `vf`, `autotune` **отсутствуют или возвращают ошибку** — это и есть default-deny.

Сохранить терминальный лог как `$Campaign\identity\terminal_identity.log`.

### Шаг 3. Оператор подтверждает G-01…G-04

Заполнить таблицу в `observations\wiring_check.md` и подписать каждый gate. Без всех четырёх PASS дальше не идём.

### Шаг 4. ADC baseline

```text
c
c
c
c
c
c
c
c
c
c
```

Зафиксировать `offset_i1`, `offset_i2`, `offset_ires` и raw samples (I1/I2/Ires/VBUS). Ожидается шум около zero-current, без saturation/rail.

### Шаг 5. Zero-current offsets ACS712

При физически отключённом моторе (zero phase current):

- DMM (DC) на выходе U и V;
- Осциллограф: усреднённое значение на плато (не пик-шум).

Записать результаты в `dmm\zero_current_offsets_worksheet.md`.

### Шаг 6. Заполнить calibration JSON и scope CSV

1. Скопировать `acs712_calibration_template.json` → `calibration\acs712_calibration.json`.
2. Записать измеренные `v0_U`, `v0_V` и фактический `Vcc`.
3. Скопировать `scope_region_template_acs712.csv` → `scope\scope_zero.csv` и заполнить `ref_u_mv`, `ref_v_mv`. `ref_w_mv` оставить пустым (KCL). `scope_qualified=0`, т.к. это обесточенная запись, не апертура MapCapture.
4. Проверить JSON:

```powershell
python -m json.tool "$Campaign\calibration\acs712_calibration.json"
```

### Шаг 7. Подписи оператора и safety watcher

Заполнить подписи в `dmm\zero_current_offsets_worksheet.md`, `observations\wiring_check.md` и `summary\acs712_nohv_summary.md`.

```powershell
python tools\fill_acs712_nohv_signatures.py `
    --campaign "$Campaign" `
    --operator "Андрей Изместьев" `
    --dmm-scope-id "<ID DMM / осциллографа>"
```

> Если на ПК‑3 используется safety watcher, запустите команду второй раз с `--role "safety-watcher" --operator "<ФИО наблюдателя>"`.

### Шаг 8. Заполнить summary и прогнать валидатор

Заполнить `summary\acs712_nohv_summary.md` с вердиктом PASS/FAIL для каждого domain.

```powershell
python tools\acs712_nohv_validator.py "$Campaign"
```

Валидатор fail-closed: любое отсутствие evidence или выход за bounds → FAIL. Exit code 0 означает, что пакет готов к передаче на ПК-2/приёмку.

## Критерии PASS/FAIL

| Domain | PASS | FAIL / BLOCKED |
|---|---|---|
| Firmware identity | `sysinfo` отвечает, SHA/CI/run записаны | stale/unknown response, незаверенный образ |
| PWM default-deny | `p?`/`pdump` показывают MOE=0, нет active PWM | energized state |
| ADC baseline | `c` даёт все три offsets, нет `@ADC:CAL:FAIL` | fail/timeout/missing |
| ACS712 zero offsets | v0_U и v0_V в пределах Vcc/2 ± 200 мВ, шум разумный | rail, >200 мВ offset, >200 мВ pp noise, нестабильность |
| No-HV boundary | DC-link <1 В, нет PC4 source, нет energizing команд | любое нарушение |

## Следующий шаг

Этот пакет даёт `acs712_calibration.json`, необходимый для запуска `tools/map_scope_ingest.py --calib ...` при future grid-кампании BOAR. Сам по себе он **не** разрешает Test №3 MapCapture, Stage A или energized characterization — для них требуется отдельное ТЗ, G0 approval и pre-flight.

## Ссылки

- `TZ_ACS712_SCOPE_REFERENCE.md` — ТЗ на scope-reference.
- `tools/map_scope_ingest.md` — формат mV-CSV и calibration JSON.
- `docs/templates/test3_nohv_campaign/scope/scope_region_template_acs712.csv` — шаблон mV-CSV grid-кампании.
- `tools/acs712_nohv_validator.py` — fail-closed валидатор evidence-пакета.
