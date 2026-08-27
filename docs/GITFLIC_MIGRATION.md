# GITFLIC_MIGRATION.md — переход OEW на GitFlic (основной remote для РФ)

Статус: **ПЕРЕЕЗД 27.08.2026.** GitFlic (`gitflic.ru/project/ivanich19744/motor2`)
становится **основным remote** проекта. GitHub остаётся архивом, GitLab.com —
резервной копией (как было).

## Почему GitFlic

| Проблема со старыми хостами | Решение |
|---|---|
| GitLab.com: CI требует identity verification, рос. номер не принимается — облачный CI недоступен (см. GITLAB_MIGRATION.md) | GitFlic — российский хостинг, работает без VPN, CI есть |
| GitHub: доступен, но не «домашний» для РФ; архив | Остаётся архивом (read-only) |

GitFlic доступен из РФ напрямую (проверено: HTTP 200, ~0.2 c, без VPN).

## Что уже сделано (ПК-1, 27.08.2026)

1. **Репозиторий перенесён**: `git push gitflic --all` — 25 веток, включая
   `main` (SHA совпадает с локальным `2fe03a2`), все `ai4/*`, `ai-fix/*`,
   `ai-hermes/*`, `ai-bench/*`.
2. **CI настроен**:
   - `gitflic-ci.yaml` — порт GitHub Actions (build + hosted/QEMU/pytest тесты
     + commissioning + артефакты);
   - self-hosted агент `pc1-hermes` (Windows, executor=powershell)
     зарегистрирован на проекте и **запущен на ПК-1** (Java 11, runner.jar);
   - облачных (shared) агентов на GitFlic SaaS **нет** — CI выполняется
     только на своих агентах. Пока агент запущен на ПК-1 — CI работает.
3. **Учётные данные**: токен GitFlic сохранён в Windows Credential Manager
   (credential helper `manager`, host `gitflic.ru`, username `ivanich19744`).

## Что нужно сделать на ПК-2 и ПК-3 (обязательно)

### 1. Добавить remote (на каждой машине)

```bash
git remote add gitflic https://gitflic.ru/project/ivanich19744/motor2.git
git fetch gitflic
```

### 2. Учётные данные

При первом push Git Credential Manager спросит логин/пароль. Использовать:
- логин: `ivanich19744`
- пароль: **API-токен GitFlic** (Настройки → API-токены, scope: полный доступ).

Либо вставить токен через `git credential approve`:
```bash
printf "protocol=https\nhost=gitflic.ru\nusername=ivanich19744\npassword=<TOKEN>\n\n" | git credential approve
```

### 3. Проверка

```bash
git ls-remote gitflic refs/heads/main   # должен вернуть SHA main
```

### 4. Рабочий процесс (без изменений по регламенту)

- Ветки по-прежнему `ai<N>/<задача>` от свежего `origin/main`.
- Push теперь в **gitflic** (основной): `git push -u gitflic ai<N>/<ветка>`.
- `git ls-remote gitflic refs/heads/<ветка>` — подтверждение публикации.
- Приёмка — как раньше (Hermes ПК-1), merge в `main`, push в `gitflic main`.
- Pre-push hook работает с любым remote (читает refs, не имя хоста).

### 5. CI-статус

- Конвейеры: https://gitflic.ru/project/ivanich19744/motor2/pipelines
- Артефакты (firmware.bin/.elf/.map + commissioning) — на странице пайплайна:
  **единственный официальный production-образ** (AGENTS_WORKFLOW §5).
- Красный CI = пакет не принимается (как раньше).
- Ограничение: CI работает, только когда агент `pc1-hermes` запущен на ПК-1.
  Если пайплайн «висит» — агент остановлен (проверить/запустить на ПК-1:
  `cd ~/gitflic-runner && java -jar runner.jar start --config=config/application.properties`).

## Роли remote (актуально с 27.08.2026)

| Remote | URL | Роль |
|---|---|---|
| `gitflic` | https://gitflic.ru/project/ivanich19744/motor2.git | **основной** (push, CI) |
| `origin` | https://github.com/1974Ivanich/OEW.git | архив (read-only, no_push) |
| `gitlab` | https://gitlab.com/ooo-group115235/OEW.git | резервная копия |

GitHub/GitLab обновляются из main после приёмки (по возможности) — они не
являются источником истины.

## Откат

```bash
git remote remove gitflic   # на каждой машине
# main остаётся на GitHub/GitLab, CI — GitHub Actions (старый конфиг не тронут)
```
