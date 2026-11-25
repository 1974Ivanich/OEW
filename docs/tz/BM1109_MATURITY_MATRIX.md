# BM1109 OEW-6T — матрица зрелости (DOE/ROM/control)

Статусы зрелости работ, не gate-статусы. Контрольная точка: `cdb6e3a` (после rebase на `bd82341`).

| Работа | Статус | Основание |
|---|---|---|
| P2-N1 incremental error budget | CLOSED | контракт POC2 §2, impl `13c6a26` |
| P2-N2 reciprocity | CLOSED | контракт §4, impl `96d79f5` |
| P2-N3 grid-scale consistency | CLOSED | контракт §5, impl `b4abd8c` |
| DOE-3 control contract impl | CLOSED | TZ_DOE3 frozen `bc79893`, impl `7aa9cb7`+`6553b86`, audit пройден |
| Phase-4 torque data path | CLOSED | TZ_PHASE4 frozen (V0.3), h=0.5°мех, impl `6011e2c`, integration report `6db58fa` |
| Task 17 стенсили dm_di | **RESEARCH_COMPLETE / CONTRACT_DECISION_PENDING** | `cdb6e3a`, docs/evidence/stencil_study_task17.json |
| Реальная FEM-интеграция | NOT_YET_VALIDATED | нет реального ROM; кампания не разрешена |
| HWT/hardware validation | NOT_YET_VALIDATED / INFRASTRUCTURE_BLOCKED | нет OpenOCD/ST-Link |

## Task 17 — зафиксированные выводы (решение владельца 2026-11-25)

- `h=0.5·I_r` не принят как улучшение; `h=1.0·I_r` не объявлен оптимальным.
- Главное ограничение — покрытие dm_di (1/33 и 7/33 узлов), не шаг.
- Truncation-сравнение отложено до нелинейной углово-токовой модели или реального FEM.
- Текущая реализация и статус `A_UNAVAILABLE` остаются без обходных путей.
- Изменение покрытия — только отдельным ТЗ: адаптивный стенсил с фактическим
  знаменателем либо односторонние разности с явной маркировкой типа аппроксимации.
- P2-N1/N2/N3 вне области влияния (собственный стенсил DOE-1 0.05·I_r).

## Тестовая статистика — оговорка

856 passed/4 skipped (`cdb6e3a`, с `--ignore` 4 GUI/HWT-файлов) vs сообщённые 900
для `bd82341`: различие объясняется набором тестов — в этом окружении 4 GUI/HWT
файла не коллекционируются (ошибки импорта зависимостей) и исключаются флагами.
Полная коллекция здесь: 858 тестов после ignore, 874+3 ошибки коллекции без.
Регрессии нет: сравнение корректно только для одинаковой команды и окружения.
