# Шаблон кампании ПК‑3 — Test №3 controlled no-HV MapCapture

## Статус шаблона

Этот каталог создан после утверждённой перенумерации: **Test №2** теперь означает базовую физическую проверку измерительной цепи и ADC, а controlled no-HV MapCapture campaign с G0 относится к **Test №3**. Все файлы в шаблоне имеют статус `PENDING`; каталог не является разрешением на прошивку, `mcarm`, `mapcap run`, RealBoardProfile или automatic characterization.

> Нельзя менять только текстовое имя Test №2 на Test №3 и требовать PASS от существующего `HIL_TEST2_G0` validator. Такой PASS был бы семантически ложным. Текущий шаблон намеренно несовместим с legacy validator, пока отдельный reviewed migration package не введёт Test №3 G0 contract, schema и tests.

## Размещение

Распакуйте каталог на ПК‑3 **вне Git working tree**, затем переименуйте его в отдельную UTC-кампанию.

```text
D:\campaign_raw\test3_nohv_YYYYMMDDTHHMMSSZ\
```

| Путь | Назначение и текущий статус |
|---|---|
| `g0_approval.json` | Test №3 G0 approval template; `decision=PENDING`, execution запрещено. Будущий APPROVED файл обязан связать `source_sha`, относительный `firmware_path` внутри campaign и `firmware_sha256`. |

| `diagnostic_build_manifest.json` | Test №3 source/build/binary identity template; identities пусты. |
| `diagnostic_build.log` | Placeholder, не build evidence. |
| `build/` | Будущий exact diagnostic binary только после утверждённого Test №3 G0 contract. |
| `g0/` | Будущий Test №3 validator summary. Не копируйте legacy `HIL_TEST2_G0` PASS как Test №3 evidence. |
| `uart/`, `sigrok/`, `dmm/`, `scope/`, `closure/` | Будущие evidence locations; они не должны заполняться synthetic PASS data. |

## Transition gate

| Вопрос | Текущий ответ |
|---|---|
| Имеется ли G0 approval для Test №3? | Нет. `decision=PENDING`. |
| Разрешён ли diagnostic flash? | Нет. |
| Разрешены ли `mcarm` и `mapcap run`? | Нет. |
| Можно ли использовать current `HIL_TEST2_G0` validator как Test №3 PASS? | Нет. Он кодирует `HIL_TEST2_G0` и `MAPCAP_TEST2`; несовместимость намеренна. |
| Можно ли начать RealBoardProfile/automatic characterization? | Нет. Только после отдельного Test №3 G0 migration, G0 PASS и controlled no-HV pre-flight. |

## Следующая правильная последовательность

Сначала выполните новый **Test №2 ADC chain** по отдельному плану и сохраните физические ADC/identity evidence. Затем создаётся отдельное ТЗ на migration G0: оно должно переименовать machine-readable gate/schema/profile references на Test №3, обновить validators/tests/docs и пройти независимую CI-приёмку. Только после этой приёмки возможны offline Test №3 diagnostic build, human safety approval, Test №3 G0 PASS и subsequent physical no-HV pre-flight.
