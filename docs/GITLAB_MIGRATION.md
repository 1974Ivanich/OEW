# GITLAB_MIGRATION.md — переход OEW с GitHub на GitLab.com

Статус: **ОТКАЧЕНО 26.08.2026.** GitLab.com остаётся **резервной копией**,
основной remote — GitHub (решение пользователя).

Причина отката: CI на GitLab.com требует **identity verification** аккаунта
(телефон/карта) для shared runners. Российский номер телефона не принимается
(гео-ограничение GitLab), карты РФ не работают — облачный CI на GitLab.com
недоступен. Self-hosted runner (CI на ПК-1) отклонён: теряется смысл облачного
CI (production-образ = артефакт CI зависел бы от локальной машины).

Итог: GitHub = основной remote (Actions работают), GitLab = резервная копия
(вся история перенесена, protected branches настроены, `.gitlab-ci.yml` готов —
пригодится при возврате). Пакет подготовки остаётся в main как документация.

---

## 1. Зачем это (итог инвентаризации GitHub-завязок)

| Что завязано на GitHub | Где | Действие |
|---|---|---|
| CI (GitHub Actions) | `.github/workflows/ci.yml` | порт → `.gitlab-ci.yml` (готов) |
| Branch protection | GitHub free = платный (403) | GitLab free **поддерживает** Protected branches — реальный серверный запрет прямого push в main |
| pre-push hook (main-guard + cubemx) | `scripts/hooks/pre-push` | платформо-независим (читает refs), правка формулировки PR→MR/PR |
| Регламент | `docs/AGENTS_WORKFLOW.md` §5 | обновлён под обе платформы |
| URL в доках | `PROJECT_OVERVIEW.md:378`, `TZ_HS1_glue_fix.md:4`, `docs/ACCEPT_CT_FIX_CHECKLIST.md:62`, `agent.instructions.md:41` | обновить URL после переезда (исторические ТЗ не трогать) |
| Креды | Git Credential Manager (`credential.helper=manager`) | переиспользуется для gitlab.com (OAuth/PAT) |
| Manus (ai4) | публикация через git bundle | платформо-независима — без изменений |
| Пульт (Pult_Encod_Koda) | github.com/metrotest190/Pult | **не мигрирует** (решение) |

## 2. Чек-лист перехода (исполняется в момент «Go»)

### Шаг 1 — аккаунт и проект на GitLab.com (делает человек)
- [ ] Зарегистрировать/войти на https://gitlab.com (ник `1974Ivanich` — как на GitHub, чтобы URL совпадали по стилю).
- [ ] **New project → Create blank project** → name: `OEW`, visibility: **Private**.
- [ ] НЕ инициализировать README (репо приедет с GitHub со всей историей).
- [ ] Settings → Members: пригласить агентов (ai1–ai4, koda) ролями **Developer**;
      приёмщик (Hermes ПК-1) — **Maintainer**.

### Шаг 2 — перенос истории (делает Hermes на ПК-1)
```bash
cd C:/ST/boyler/Motor
git remote add gitlab https://gitlab.com/1974Ivanich/OEW.git
git push --mirror gitlab        # все ветки + теги + refs, включая ai4/*
# проверка:
git ls-remote gitlab refs/heads/main
```
- [ ] `git push --mirror` на **пустой** проект — полный перенос истории (ветки, теги, все refs).
- [ ] Ошибка auth → Git Credential Manager откроет окно входа (OAuth gitlab.com),
      либо PAT: Settings → Access Tokens → scope `write_repository` (+`api` для CI-статусов).

### Шаг 3 — переключение origin (делает Hermes на ПК-1)
```bash
git remote rename origin github-archive          # GitHub → read-only архив
git remote set-url --push github-archive no_push # защита от случайного push в архив
git remote rename gitlab origin                  # GitLab становится основным
git remote -v                                    # проверить
git fetch origin && git status -sb
```
- [ ] Все локальные ветки переключаются на `origin/main` (GitLab) — база веток
      перестаёт зависеть от GitHub.
- [ ] На **других ПК** (ПК-2, ПК-3): тот же rename/set-url, чтобы агенты пушили в GitLab.

### Шаг 4 — защита main на GitLab (серверная, бесплатная — то, чего не было на GitHub free)
- [ ] Settings → Repository → **Protected branches** → `main`:
  - Allowed to push and merge: **Maintainers** (приёмщик);
  - Allowed to force push: **No**.
- [ ] Эффект: прямой push агентов (Developer) в main **физически невозможен**
      на сервере — это заменяет/усиливает hook main-guard. CI + hook остаются
      вторым эшелоном (защита от ошибок, а не от злого умысла).

### Шаг 5 — CI на GitLab
- [ ] `.gitlab-ci.yml` уже в репо (порт Actions) — GitLab подхватит сам при первом push.
- [ ] Settings → CI/CD → Runners: **shared runners** включить (Enable shared runners).
- [ ] Первый прогон на main: Build → Pipelines — ждать **зелёный**.
      Локальный прогон для сравнения: `make && make test QEMU=$(command -v qemu-system-arm)`.
- [ ] Артефакты (firmware.bin/.elf/.map + commissioning) — на странице пайплайна:
      **единственный официальный production-образ** (AGENTS_WORKFLOW §5).

### Шаг 6 — GitHub → архив (read-only)
- [ ] GitHub → repo Settings → Danger Zone → **Archive this repository**.
- [ ] GitHub Actions на архиве не запускаются — расход минут прекращается.
- [ ] Remote `github-archive` остаётся в конфигах для чтения истории; push защищён `no_push`.

### Шаг 7 — доки (после зелёного CI на GitLab)
- [ ] `PROJECT_OVERVIEW.md:378` → `https://gitlab.com/1974Ivanich/OEW`.
- [ ] `agent.instructions.md:41` → упоминание `.gitlab/agent.md` (GitLab тоже поддерживает).
- [ ] Новые ТЗ/чеклисты: URL указывать GitLab. Исторические (`TZ_HS1_glue_fix.md`,
      `docs/ACCEPT_CT_FIX_CHECKLIST.md`) не переписывать — они фиксируют момент времени.
- [ ] AGENTS_STATUS.md — строка «миграция завершена, CI зелёный на <sha>».

## 3. Проверка после миграции (приёмка перехода)

1. `git ls-remote origin refs/heads/main` — SHA совпадает с GitHub-архивом (история цела).
2. `git log --oneline -3 origin/main` — ветки/мержи на месте.
3. Пайплайн GitLab на main — зелёный, артефакты скачиваются, `firmware.bin` есть.
4. Тестовая ветка `ai-hermes/gitlab-smoke` → push → CI запустился → MR к main отклонён
   сервером (403) для Developer — защита работает.
5. ПК-2/ПК-3: `git fetch origin` работает с GitLab (креды настроены).

## 4. Откат (если GitLab CI не докажет себя)

```bash
git remote rename origin gitlab
git remote rename github-archive origin
git remote set-url --push origin https://github.com/1974Ivanich/OEW.git
```
GitHub-архив **не удалять** до полной уверенности (минимум 2 недели зелёного CI).

## 5. Лимиты и риски free-tier GitLab.com

| Риск | Оценка | Митигация |
|---|---|---|
| CI-минуты: ~400 мин/мес на namespace (было 2000, урезано 2024) | **основной риск**: 1 прогон ≈ 7–10 мин → ~40–55 прогонов/мес. Проект пушит десятки веток | Следить: Settings → Usage Quotas. Если не хватает: (а) CI только на `main` + MR (агенты гоняют `make test` локально, §4 workflow), (б) self-hosted runner на ПК-1 (GitLab Runner, бесплатно), (в) лимит времени на job |
| Shared runners очередь | бывает в часы пик | не критично — пайплайны встают в очередь, не падают |
| Хранение артефактов 1 год | free quota 5 ГБ | образы ~1–2 МБ; при росте — `expire_in` уменьшить |
| Geo-доступность gitlab.com | проверено: доступен | если появится блокировка — VPN (v2RayTun) уже в работе |

## 6. Что НЕ меняется

- Регламент AGENTS_WORKFLOW (ветки `ai<N>/...`, приёмка, rebase, SHA+`ls-remote`).
- Pre-push hook: main-guard + cubemx_check (работает с любым remote).
- Маnus: публикация через git bundle — платформо-независима.
- `make` / `make test` / `make flash` — без изменений.
