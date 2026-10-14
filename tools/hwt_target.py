"""HWT: сценарии проверки прошивки на цели через отладчик (DDTT-совместимо).

Модуль не зависит от железа и от GDB: он задаёт
* описание сценария (`@case`) и его метаданные, читаемые без выполнения кода
  сценария (DDTT-6.1-2);
* API цели — то, что сценарию разрешено делать с платой (DDTT-6.2);
* проверки с именем, фактическим и ожидаемым значением (DDTT-6.1-6);
* вердикты PASS/FAIL/ERROR, где ERROR никогда не превращается в PASS
  (DDTT-6.5-1).

Реализации API цели: `hwt_gdbside.GdbBackend` (реально внутри GDB)
и `hwt_sim.SimBackend` (прогон конвейера без железа).
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import hashlib
import json
import pathlib
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_ERROR = "ERROR"
# Снято записанным отказом (waiver): проверка не выполнена, но прогон не
# валится — только явной записью в реестре, и это НЕ PASS (см. ниже).
VERDICT_WAIVED = "WAIVED"

# Сценарию запрещено обращаться к цели дольше, чем это разрешил хост
# (DDTT-6.4-7: предел контролирует хост, а не сценарий).
DEFAULT_TIMEOUT_S = 30.0


class TargetError(RuntimeError):
    """Отказ API цели — это ERROR, а не FAIL (DDTT-6.5-1)."""


class ScenarioError(RuntimeError):
    """Дефект самого сценария (метаданные, неизвестный символ и т. п.)."""


# ── метаданные сценария ───────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class CaseMeta:
    id: str
    function: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    labels: Tuple[str, ...] = ()
    contracts: Tuple[str, ...] = ()
    description: str = ""
    source: str = ""
    line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "function": self.function,
            "timeout_s": self.timeout_s, "labels": list(self.labels),
            "contracts": list(self.contracts), "description": self.description,
            "source": self.source, "line": self.line,
        }


def case(case_id: Optional[str] = None, timeout_s: float = DEFAULT_TIMEOUT_S,
         labels: Sequence[str] = (), contracts: Sequence[str] = (),
         description: str = "") -> Callable[[Callable], Callable]:
    """Декоратор сценария.

    Метаданные обязаны быть литералами: иначе хост не сможет собрать их без
    выполнения кода сценария (DDTT-6.1-2) и preflight откажет.
    """
    def wrap(func: Callable) -> Callable:
        setattr(func, "__hwt_case__", {
            "id": case_id or func.__name__,
            "timeout_s": float(timeout_s),
            "labels": tuple(labels),
            "contracts": tuple(contracts),
            "description": description,
        })
        return func
    return wrap


def _literal(node: ast.AST, what: str,
             consts: Optional[Dict[str, Any]] = None) -> Any:
    """Литерал, в том числе через константы модуля.

    Код сценария при этом НЕ исполняется (DDTT-6.1-2): имя разрешается по
    литеральным присваиваниям верхнего уровня, а не вычислением.
    """
    consts = consts or {}
    if isinstance(node, ast.Name):
        if node.id in consts:
            return consts[node.id]
        raise ScenarioError(f"{what}: '{node.id}' не является литеральной "
                            "константой модуля — метаданные должны быть "
                            "известны без выполнения кода")
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_literal(e, what, consts) for e in node.elts)
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError) as exc:
        raise ScenarioError(
            f"{what}: метаданные должны быть литералами "
            f"(получено {ast.dump(node)[:60]}…)") from exc


def _module_constants(tree: ast.Module) -> Dict[str, Any]:
    """Литеральные константы верхнего уровня: `CHECKPOINT = "..."`."""
    out: Dict[str, Any] = {}
    for node in tree.body:
        targets: List[ast.expr] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            targets, value = [node.targets[0]], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not targets or value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = _literal(value, target.id, out)
                except ScenarioError:
                    continue
    return out


def iter_cases(path: str | pathlib.Path) -> List[CaseMeta]:
    """Метаданные всех сценариев файла — без импорта и выполнения (DDTT-6.1-2).

    Разбор идёт через AST: файл сценария в этот момент не исполняется, поэтому
    preflight безопасен и не требует ни GDB, ни платы.
    """
    p = pathlib.Path(path)
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except (OSError, SyntaxError) as exc:
        raise ScenarioError(f"сценарий не читается/не разбирается: {p}: {exc}") \
            from exc

    out: List[CaseMeta] = []
    consts = _module_constants(tree)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = False
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = target.attr if isinstance(target, ast.Attribute) else \
                getattr(target, "id", None)
            if name != "case":
                continue
            decorated = True
            kwargs: Dict[str, Any] = {}
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg is None:
                        raise ScenarioError(
                            f"{p}:{node.lineno}: **kwargs в @case не "
                            "поддерживаются — метаданные должны быть явными")
                    kwargs[kw.arg] = _literal(kw.value, f"{p}:{node.lineno} "
                                                     f"{kw.arg}", consts)
                if dec.args:
                    kwargs["id"] = _literal(dec.args[0],
                                            f"{p}:{node.lineno} id", consts)
            out.append(CaseMeta(
                id=str(kwargs.get("id") or node.name),
                function=node.name,
                timeout_s=float(kwargs.get("timeout_s", DEFAULT_TIMEOUT_S)),
                labels=tuple(str(x) for x in kwargs.get("labels", ())),
                contracts=tuple(str(x) for x in kwargs.get("contracts", ())),
                description=str(kwargs.get("description", "")),
                source=str(p), line=node.lineno))
    if not out:
        raise ScenarioError(f"в {p} нет ни одной функции с @case")
    return out


def load_scenario(path: str | pathlib.Path) -> Dict[str, CaseMeta]:
    """Загрузить сценарий как Python-модуль и вернуть {id: (meta, функция)}.

    Сценарии импортируют `hwt_target`/`hwt_stm32g4` по имени, поэтому перед
    исполнением в `sys.modules` подставляются уже загруженные модули: иначе
    появилась бы вторая копия классов и вердикты/исключения разошлись бы с
    раннером.
    """
    import importlib.util
    import sys as _sys
    _sys.modules.setdefault("hwt_target", _sys.modules[__name__])
    for flat in ("hwt_stm32g4", "hwt_elf"):
        loaded = _sys.modules.get(f"tools.{flat}")
        if flat not in _sys.modules and loaded is not None:
            _sys.modules[flat] = loaded
    p = pathlib.Path(path).resolve()
    spec = importlib.util.spec_from_file_location(f"hwt_scenario_{p.stem}", p)
    if spec is None or spec.loader is None:
        raise ScenarioError(f"сценарий не загружается: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out: Dict[str, CaseMeta] = {}
    for meta in iter_cases(p):
        func = getattr(mod, meta.function, None)
        if func is None:  # pragma: no cover - невозможно после exec_module
            raise ScenarioError(f"{p}: функция {meta.function} не найдена")
        out[meta.id] = (meta, func)  # type: ignore[assignment]
    return out  # type: ignore[return-value]


# ── записанные отказы (waivers): «эта проверка не проверяется, и вот почему» ──
#
# Режим кампании: гейт, который сегодня не может быть зелёным (например
# HW_START_PATH_READY при Tr ниже порога прямого старта — паспорт, шаг 4), не
# отключается и не ослабляется, а снимается ЯВНОЙ записью в реестре в
# репозитории. Требования к записи подобраны так, чтобы случайный обход был
# невозможен:
#
# * адресация точная: сценарий + ИМЯ проверки, без масок и префиксов;
# * снять можно только проверку, объявленную в сценарии как `waivable=True`
#   (`t.check(..., waivable=True)`) — шильдиковые проверки (пары полюсов)
#   снять нельзя ничем;
# * причина и компенсация обязательны и непусты, автор и дата обязательны;
# * неизвестные поля, опечатки в именах полей, дубли и не-ISO дата — это
#   ERROR при разборе, а не «запись проигнорирована»;
# * отказ снимает FAIL и только FAIL: ERROR (не дошли/не прочитали) не
#   снимается никогда;
# * вердикт становится WAIVED, а не PASS, и попадает в result.json и junit;
# * если проверка прошла, запись становится ненужной и печатается
#   предупреждение — отказ сам выключается, когда параметр измерен.
#
# Отсутствие файла реестра — не ошибка: это обычный режим без отказов.

WAIVER_FIELDS = ("id", "case", "check", "reason", "compensating", "author",
                 "date")


@dataclasses.dataclass(frozen=True)
class Waiver:
    """Одна записанная причина не проверять одну проверку.

    `reason` — чего не хватает (почему проверка сегодня не может быть
    зелёной), `compensating` — почему кампания допустима и без неё. Оба поля
    обязательны: отказ без компенсации — это не режим, а выключенный гейт.
    """

    id: str
    case: str
    check: str
    reason: str
    compensating: str
    author: str
    date: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "case": self.case, "check": self.check,
                "reason": self.reason, "compensating": self.compensating,
                "author": self.author, "date": self.date}


@dataclasses.dataclass
class WaiverRegistry:
    entries: List[Waiver] = dataclasses.field(default_factory=list)
    source: str = ""
    sha256: str = ""
    exists: bool = False

    def for_case(self, case_id: str) -> List[Waiver]:
        return [w for w in self.entries if w.case == case_id]

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.source, "exists": self.exists,
                "sha256": self.sha256,
                "entries": [w.to_dict() for w in self.entries]}


def _waiver(item: Any, where: str) -> Waiver:
    if not isinstance(item, dict):
        raise ScenarioError(f"{where}: запись должна быть объектом")
    unknown = sorted(set(item) - set(WAIVER_FIELDS))
    if unknown:
        raise ScenarioError(f"{where}: неизвестные поля {unknown} — проверьте "
                            f"написание (допустимы {list(WAIVER_FIELDS)})")
    values: Dict[str, str] = {}
    for field in WAIVER_FIELDS:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ScenarioError(f"{where}: поле {field!r} обязательно и не может "
                                "быть пустым")
        values[field] = value.strip()
    for field in ("case", "check"):
        if "*" in values[field] or "?" in values[field]:
            raise ScenarioError(f"{where}: {field!r} — точное имя, маски "
                                "запрещены")
    try:
        datetime.date.fromisoformat(values["date"])
    except ValueError as exc:
        raise ScenarioError(f"{where}: date — ISO-дата ГГГГ-ММ-ДД, получено "
                            f"{values['date']!r}") from exc
    return Waiver(**values)


def load_waivers(path: str | pathlib.Path) -> WaiverRegistry:
    """Прочитать реестр отказов. Отсутствие файла — не ошибка: отказов нет.

    Ошибка разбора (битый JSON, неполная запись, опечатка в имени поля, дубль,
    не-ISO дата, маска вместо имени) — ScenarioError: неверно записанный отказ
    не должен превращаться ни в «нет отказов» (тихий красный гейт), ни тем
    более в снятую проверку.
    """
    p = pathlib.Path(path)
    reg = WaiverRegistry(source=str(p))
    if not p.exists():
        return reg
    text = p.read_text(encoding="utf-8")
    reg.exists = True
    reg.sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{p}: реестр отказов не разбирается как JSON: "
                            f"{exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f'{p}: реестр отказов — объект с ключом "waivers"')
    unknown = [k for k in data if k != "waivers" and not k.startswith("_")]
    if unknown:
        raise ScenarioError(f"{p}: неизвестные ключи верхнего уровня {unknown} "
                            "— опечатка не должна менять смысл реестра")
    raw = data.get("waivers")
    if raw is None:
        raise ScenarioError(f'{p}: нет ключа "waivers"')
    if not isinstance(raw, list):
        raise ScenarioError(f'{p}: "waivers" должен быть списком записей')
    seen: Dict[Tuple[str, str], str] = {}
    seen_ids: set = set()
    for i, item in enumerate(raw):
        where = f"{p}: waivers[{i}]"
        entry = _waiver(item, where)
        if entry.id in seen_ids:
            raise ScenarioError(f"{where}: id {entry.id!r} повторяется")
        seen_ids.add(entry.id)
        pair = (entry.case, entry.check)
        if pair in seen:
            raise ScenarioError(f"{where}: проверка «{entry.check}» в "
                                f"{entry.case} уже снята записью {seen[pair]!r} — "
                                "дубль запрещён: реестр должен быть однозначным")
        seen[pair] = entry.id
        reg.entries.append(entry)
    return reg


def declared_checks(path: str | pathlib.Path) -> Dict[str, List[Tuple[str, bool]]]:
    """Литеральные проверки `t.check("...")` из AST: функция → (имя, waivable).

    Нужны preflight'у, чтобы заметить реестр, разошедшийся со сценарием, ещё до
    стенда. Имена, собранные в коде вычислением, сюда не попадают — поэтому это
    проверка-предупреждение, а решает прогон (он видит настоящие имена).
    """
    p = pathlib.Path(path)
    tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    out: Dict[str, List[Tuple[str, bool]]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        checks: List[Tuple[str, bool]] = []
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr != "check":
                continue
            if not call.args:
                continue
            try:
                name = ast.literal_eval(call.args[0])
            except (ValueError, SyntaxError):
                continue
            if not isinstance(name, str):
                continue
            checks.append((name, _literal_true(call, "waivable")))
        if checks:
            out[node.name] = checks
    return out


def _literal_true(call: ast.Call, keyword: str) -> bool:
    """Литеральное `keyword=True` в вызове? Не литерал — значит «нет».

    Это только статический срез (preflight, `hwt list`): во время прогона
    право снять проверку определяет сама проверка (`Check.waivable`),
    а не этот разбор. Динамические имена проверок здесь просто не видны.
    """
    for kw in call.keywords:
        if kw.arg != keyword or kw.value is None:
            continue
        try:
            return ast.literal_eval(kw.value) is True
        except (ValueError, SyntaxError):
            return False
    return False


def _apply_waivers(meta: CaseMeta, target: "Target",
                   waivers: Sequence[Waiver]) -> None:
    """Снять несовпавшие проверки, которые сценарий объявил снимаемыми.

    Снимается только FAIL: вердикт по снятой проверке станет WAIVED, но сама
    проверка в отчёте остаётся с фактическим значением и пометкой отказа.
    """
    for check in target.checks:
        if check.ok:
            continue
        waiver = next((w for w in waivers if w.case == meta.id and
                       w.check == check.name), None)
        if waiver is None:
            continue
        if not check.waivable:
            target.note(f"отказ {waiver.id} адресован проверке «{check.name}», "
                        "но она не объявлена waivable — отказ не применён")
            continue
        check.waiver = waiver
        target.note(
            f"отказ {waiver.id} снял проверку «{check.name}»: {waiver.reason} "
            f"Компенсация: {waiver.compensating} "
            f"(запись: {waiver.author}, {waiver.date}) — это WAIVED, не PASS")


# ── результат ─────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Check:
    name: str
    actual: Any
    expected: Any
    ok: bool
    detail: str = ""
    injected: bool = False
    before: Any = None
    waivable: bool = False
    waiver: Optional[Waiver] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "ok": bool(self.ok),
             "actual": _plain(self.actual), "expected": _plain(self.expected)}
        if self.detail:
            d["detail"] = self.detail
        if self.injected:
            d["injection"] = {"before": _plain(self.before),
                              "after": _plain(self.actual)}
        if self.waivable:
            d["waivable"] = True
        if self.waiver is not None:
            # `ok` остаётся false: снятая проверка — это «не проверено», а не
            # «совпало». Пометка отказа видна в каждом отчёте.
            d["waiver"] = self.waiver.to_dict()
        return d


@dataclasses.dataclass
class CaseResult:
    id: str
    verdict: str
    checks: List[Check] = dataclasses.field(default_factory=list)
    notes: List[str] = dataclasses.field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0
    target: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return {VERDICT_PASS: 0, VERDICT_WAIVED: 0, VERDICT_FAIL: 1,
                VERDICT_ERROR: 2}[self.verdict]

    def to_dict(self) -> Dict[str, Any]:
        d = {"id": self.id, "verdict": self.verdict,
             "duration_s": round(self.duration_s, 3),
             "checks": [c.to_dict() for c in self.checks]}
        if self.notes:
            d["notes"] = list(self.notes)
        if self.error:
            d["error"] = self.error
        if self.target:
            d["target"] = self.target
        return d


def _plain(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


# ── API цели ──────────────────────────────────────────────────────────────

class Backend:
    """Транспорт до цели. Реализации: GDB-на-хосте и симулятор."""

    def target_info(self) -> Dict[str, Any]:
        return {}

    def reset_halt(self) -> None:
        raise NotImplementedError

    def break_at(self, symbol: str) -> None:
        raise NotImplementedError

    def clear_breakpoints(self) -> None:
        raise NotImplementedError

    def continue_for(self, timeout_s: float, what: str) -> bool:
        """Продолжить выполнение; True — цель остановилась.

        False означает, что остановки не произошло (это ERROR сценария, а не
        FAIL проверки): сценарию запрещено трактовать «не дождались» как
        совпадение.
        """
        raise NotImplementedError

    def stop_reason(self) -> str:
        raise NotImplementedError

    def evaluate(self, expr: str) -> Any:
        raise NotImplementedError

    def read_memory(self, addr: int, size: int) -> bytes:
        raise NotImplementedError

    def set_value(self, expr: str, value: Any) -> None:
        """Записать значение в цель (только для явных инъекций, DDTT-6.2-4)."""
        raise NotImplementedError

    def force_return(self, value: int) -> None:
        raise NotImplementedError


class Target:
    """То, что доступно сценарию: достижение точки, чтение, проверка.

    Сценарий не знает ни про OpenOCD, ни про адреса точек останова
    (DDTT-6.1-3), и не может ничего записать без явного разрешения стенда
    (DDTT-6.2-4, раздел 8: инъекции в силовых схемах опасны).
    """

    def __init__(self, backend: Backend, meta: CaseMeta,
                 allow_injections: bool = False,
                 deadline: Optional[float] = None,
                 image: Any = None,
                 log: Optional[Callable[[str], None]] = None):
        self.backend = backend
        self.meta = meta
        self.allow_injections = allow_injections
        self.deadline = deadline
        self.image = image  # hwt_elf.ElfImage: адреса символов берутся из него
        self.checks: List[Check] = []
        self.notes: List[str] = []
        self._log = log or (lambda _msg: None)

    # договор с хостом ───────────────────────────────────────────────────
    def _left(self) -> float:
        if self.deadline is None:
            return self.meta.timeout_s
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TargetError(f"{self.meta.id}: исчерпан предел времени "
                              f"{self.meta.timeout_s:.0f} c (DDTT-6.4-7)")
        return left

    def note(self, text: str) -> None:
        self.notes.append(str(text))
        self._log(f"[{self.meta.id}] {text}")

    # API цели ───────────────────────────────────────────────────────────
    def reach(self, symbol: str, occurrence: int = 1,
              reset: bool = True) -> None:
        """Достичь точки программы: аппаратная точка останова на символе.

        `occurrence` — какой по счёту проход считать достижением; это позволяет
        доказать, что прошивка не стоит на месте, а идёт по основному циклу.
        """
        if reset:
            self.backend.reset_halt()
        self.backend.clear_breakpoints()
        self.backend.break_at(symbol)
        budget = self._left()
        per_hit = max(0.5, budget / max(occurrence, 1))
        for i in range(occurrence):
            if not self.backend.continue_for(min(per_hit, self._left()),
                                             f"достижение {symbol} (#{i + 1})"):
                raise TargetError(
                    f"{self.meta.id}: не достигли {symbol} за "
                    f"{per_hit:.1f} c ({self.backend.stop_reason()})")
        self._log(f"[{self.meta.id}] reach {symbol} x{occurrence} ok")

    def value(self, expr: str) -> Any:
        """Значение выражения по отладочной информации образа (DDTT-6.2-2)."""
        self._left()
        try:
            val = self.backend.evaluate(expr)
        except Exception as exc:
            raise TargetError(f"{self.meta.id}: значение {expr!r} недоступно: "
                              f"{exc}") from exc
        if val is None:
            raise TargetError(f"{self.meta.id}: {expr!r} недоступно "
                              "(оптимизировано?) — API обязан отказать, "
                              "а не вернуть произвольный результат")
        return val

    def fields(self, expr: str, names: Sequence[str]) -> List[Any]:
        return [self.value(f"({expr}).{n}") for n in names]

    def check(self, name: str, actual: Any, expected: Any,
              detail: str = "", tol: Optional[float] = None,
              waivable: bool = False) -> bool:
        """Зафиксировать проверку. `waivable=True` разрешает снять её отказом.

        Разрешение даёт сам сценарий, а не реестр: снять можно только то, что
        автор проверки отметил как снимаемое. Отказ на проверку без этой
        отметки не применяется и попадает в отчёт как не применённый.
        """
        ok = _matches(actual, expected, tol)
        self.checks.append(Check(name=name, actual=actual, expected=expected,
                                 ok=ok, detail=detail, waivable=waivable))
        self._log(f"[{self.meta.id}] {'ok ' if ok else 'FAIL'} {name}: "
                  f"{actual!r} vs {expected!r}")
        return ok

    # чтение памяти и символов ───────────────────────────────────────────
    def read_u32(self, addr: int) -> int:
        """32-битное слово по абсолютному адресу.

        Регистры периферии читаются именно так: макросы `TIM1->BDTR`
        существуют только в препроцессоре и в отладочной информации образа
        отсутствуют, поэтому по имени на цели они не вычисляются.
        """
        self._left()
        addr = int(addr)
        try:
            raw = self.backend.read_memory(addr, 4)
        except Exception as exc:
            raise TargetError(f"{self.meta.id}: память 0x{addr:08X} недоступна: "
                              f"{exc}") from exc
        if len(raw) != 4:
            raise TargetError(f"{self.meta.id}: прочитано {len(raw)} байт "
                              f"вместо 4 по 0x{addr:08X}")
        return int.from_bytes(raw, "little")

    def read_u8(self, addr: int) -> int:
        """8-битное значение по абсолютному адресу.

        Отдельно от `read_u32`: однобайтовые переменные (`static volatile
        uint8_t`) нельзя читать словом — в старших байтах окажутся соседние
        объекты, и проверка вынесет вердикт по чужим данным.
        """
        self._left()
        addr = int(addr)
        try:
            raw = self.backend.read_memory(addr, 1)
        except Exception as exc:
            raise TargetError(f"{self.meta.id}: память 0x{addr:08X} недоступна: "
                              f"{exc}") from exc
        if len(raw) != 1:
            raise TargetError(f"{self.meta.id}: прочитано {len(raw)} байт "
                              f"вместо 1 по 0x{addr:08X}")
        return raw[0]

    def word_at_symbol(self, name: str, offset: int = 0) -> int:
        """Значение 32-битной переменной по адресу из таблицы символов образа.

        Адрес берётся из ELF, а не из DWARF-имени: локальные статические
        переменные (`static volatile int fault`) в DWARF зависят от написания
        имени файла, а в .symtab они есть всегда.

        `offset` — смещение поля внутри символа. Поля структур иначе недоступны:
        DWARF в образе может не быть, а транспорт (RSP/OpenOCD) не вычисляет
        выражений вида `g_motor_params.Tr_rotor_us` — он читает память по
        адресу. Поэтому сценарий указывает смещение явно, а его правильность
        держит контракт сборки на `offsetof`, а не доверие к константе.
        """
        if self.image is None:
            raise TargetError(f"{self.meta.id}: образ ELF не передан в Target — "
                              "адрес символа неизвестен")
        sym = self.image.find_symbol(name)
        if sym is None:
            raise TargetError(f"{self.meta.id}: символ {name!r} отсутствует "
                              "в образе (нарушен контракт сценария)")
        if offset < 0:
            raise TargetError(f"{self.meta.id}: отрицательное смещение "
                              f"{offset} внутри {name!r} — это опечатка, а не "
                              "поле")
        return self.read_u32(sym.value + int(offset))

    def byte_at_symbol(self, name: str, offset: int = 0) -> int:
        """Значение 8-битной переменной по адресу из таблицы символов образа.

        Размер берётся из ELF и обязан быть ровно 1 байт: иначе сценарий
        читал бы чужой диапазон (см. `read_u8`), поэтому несовпадение — ERROR
        до вердикта, а не «прочитали как получилось». Так это работает для
        `static volatile uint8_t enc_error` (`src/encoder.c:50`).
        """
        if self.image is None:
            raise TargetError(f"{self.meta.id}: образ ELF не передан в Target — "
                              "адрес символа неизвестен")
        sym = self.image.find_symbol(name)
        if sym is None:
            raise TargetError(f"{self.meta.id}: символ {name!r} отсутствует "
                              "в образе (нарушен контракт сценария)")
        size = int(getattr(sym, "size", 0))
        if size != 1:
            raise TargetError(f"{self.meta.id}: символ {name!r} занимает "
                              f"{size} байт, а не 1 — 8-битное чтение "
                              "захватило бы соседние объекты")
        if offset < 0:
            raise TargetError(f"{self.meta.id}: отрицательное смещение "
                              f"{offset} внутри {name!r} — это опечатка, а не "
                              "поле")
        return self.read_u8(sym.value + int(offset))

    # инъекции (по умолчанию запрещены) ──────────────────────────────────
    def _require_injection(self, what: str) -> None:
        if not self.allow_injections:
            raise TargetError(
                f"{self.meta.id}: {what} — инъекция запрещена: включите её "
                "явно в описании стенда (safety.allow_injections=true) "
                "и объявите label 'injection' в сценарии")

    def set_value(self, expr: str, value: Any) -> None:
        """Явная инъекция записи значения (DDTT-6.2-4).

        После записи значение перечитывается: в отчёт попадает фактическое
        состояние цели, а не намерение сценария.
        """
        self._require_injection(f"запись {expr}")
        before = self.value(expr)
        self.backend.set_value(expr, value)
        after = self.value(expr)
        self.checks.append(Check(name=f"inject {expr}", actual=after,
                                 expected=value, ok=after == value,
                                 injected=True, before=before))
        self.note(f"инъекция: {expr} {before!r} -> {after!r}")

    def force_return(self, value: int) -> None:
        """Принудительный возврат из текущей функции (DDTT-6.2-4).

        Позволяет проверить ветку обработки ошибки, не воспроизводя отказ
        периферии физически.
        """
        self._require_injection("принудительный возврат из функции")
        try:
            self.backend.evaluate(f"__hwt_force_return = {value}")
        except Exception:  # pragma: no cover - транспорт без поддержки
            pass
        self.backend.force_return(value)
        self.checks.append(Check(name="force_return", actual=value,
                                 expected=value, ok=True, injected=True))
        self.note(f"инъекция: принудительный возврат {value}")


def _matches(actual: Any, expected: Any, tol: Optional[float]) -> bool:
    if tol is not None:
        try:
            return abs(float(actual) - float(expected)) <= abs(float(tol))
        except (TypeError, ValueError):
            return False
    return actual == expected


# ── исполнение сценария ───────────────────────────────────────────────────

def run_case(meta: CaseMeta, func: Callable[[Target], None], target: Target,
             waivers: Sequence[Waiver] = ()) -> CaseResult:
    """Выполнить сценарий и вынести вердикт.

    Исключение API цели или любое неожиданное исключение = ERROR, а не FAIL
    (DDTT-6.5-1); FAIL ставится только по несовпавшей проверке. Проверка,
    снятая записанным отказом, даёт WAIVED — это не PASS: прогон не валится,
    но и успехом не считается, и отказ виден в отчёте.
    """
    started = time.monotonic()
    result = CaseResult(id=meta.id, verdict=VERDICT_ERROR, notes=[],
                        target=target.backend.target_info())
    try:
        func(target)
    except TargetError as exc:
        result.error = str(exc)
        result.verdict = VERDICT_ERROR
    except Exception as exc:  # noqa: BLE001 - дефект сценария = ERROR
        result.error = f"{type(exc).__name__}: {exc}"
        result.verdict = VERDICT_ERROR
    else:
        if not target.checks:
            result.verdict = VERDICT_ERROR
            result.error = f"{meta.id}: сценарий не выполнил ни одной проверки"
        else:
            _apply_waivers(meta, target, waivers)
            failed = [c for c in target.checks if not c.ok and c.waiver is None]
            if failed:
                result.verdict = VERDICT_FAIL
            elif any(c.waiver is not None for c in target.checks):
                result.verdict = VERDICT_WAIVED
            else:
                result.verdict = VERDICT_PASS
    result.checks = list(target.checks)
    result.notes = list(target.notes)
    result.duration_s = time.monotonic() - started
    return result


def exit_code_for(results: Sequence[CaseResult]) -> int:
    """Код завершения: 0 — все PASS или WAIVED, 1 — есть FAIL, 2 — есть ERROR.

    WAIVED не валит прогон (иначе кампанию через V/f нельзя было бы провести),
    но код 0 сам по себе не говорит «всё проверено»: слово вердикта берётся из
    `overall_verdict`, а не из кода завершения.
    """
    codes = {r.exit_code for r in results}
    if 2 in codes:
        return 2
    if 1 in codes:
        return 1
    return 0


def overall_verdict(results: Sequence[CaseResult]) -> str:
    """Вердикт прогона словом: ERROR > FAIL > WAIVED > PASS.

    Прогон с хотя бы одним WAIVED не может называться PASS: иначе снятая
    отказом проверка исчезала бы из итоговой строки отчёта.
    """
    verdicts = {r.verdict for r in results}
    if VERDICT_ERROR in verdicts:
        return VERDICT_ERROR
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if VERDICT_WAIVED in verdicts:
        return VERDICT_WAIVED
    return VERDICT_PASS


def junit_xml(results: Sequence[CaseResult], name: str = "hwt") -> str:
    """JUnit XML для CI (DDTT-6.5-2); отчёт формируется при любом вердикте."""
    total = len(results)
    failures = sum(1 for r in results if r.verdict == VERDICT_FAIL)
    errors = sum(1 for r in results if r.verdict == VERDICT_ERROR)
    waived = sum(1 for r in results if r.verdict == VERDICT_WAIVED)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<testsuite name="{_esc(name)}" tests="{total}" '
             f'failures="{failures}" errors="{errors}" '
             f'skipped="{waived}">']
    for r in results:
        secs = f"{r.duration_s:.3f}"
        lines.append(f'  <testcase classname="{_esc(name)}" '
                     f'name="{_esc(r.id)}" time="{secs}">')
        if r.verdict == VERDICT_FAIL:
            bad = [c for c in r.checks if not c.ok]
            msg = "; ".join(f"{c.name}: получено {c.actual!r}, "
                            f"ожидалось {c.expected!r}" for c in bad) or "FAIL"
            lines.append(f'    <failure message="{_esc(msg)}"/>')
        elif r.verdict == VERDICT_ERROR:
            lines.append(f'    <error message="{_esc(r.error or "ERROR")}"/>')
        elif r.verdict == VERDICT_WAIVED:
            # Снятая проверка — это skipped, а не успешный тест: CI не должен
            # показывать её зелёной.
            first = next((c for c in r.checks if c.waiver is not None), None)
            what = (f"отказ {first.waiver.id}: {first.name}"
                    if first is not None and first.waiver is not None
                    else "записанный отказ")
            lines.append(f'    <skipped message="{_esc(what)}"/>')
        for c in r.checks:
            state = "ok" if c.ok else ("WAIVED" if c.waiver is not None
                                       else "FAIL")
            lines.append(f'    <system-out>{_esc(c.name)}: '
                         f'{state}</system-out>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def waiver_summary(results: Sequence[CaseResult], registry: WaiverRegistry,
                   selected_cases: Optional[Set[str]] = None
                   ) -> Dict[str, Any]:
    """Что отказ снял, а что нет — сверка реестра с фактическим прогоном.

    «Не применён» всегда означает ровно это: проверка осталась FAIL (запись
    адресована неснимаемой или несуществующей проверке), либо прошла без нужды
    в отказе, либо сценарий завершился ERROR. Ни один из этих случаев не
    превращается в PASS и не скрывается: всё попадает в отчёт.
    """
    by_case = {r.id: r for r in results}
    applied: List[Dict[str, Any]] = []
    not_applied: List[Dict[str, Any]] = []
    for result in results:
        for check in result.checks:
            if check.waiver is None:
                continue
            applied.append({"case": result.id, "check": check.name,
                            "actual": _plain(check.actual),
                            "expected": _plain(check.expected),
                            **check.waiver.to_dict()})
    for waiver in registry.entries:
        if selected_cases is not None and waiver.case not in selected_cases:
            continue  # сценария нет в этом прогоне (--case и т. п.)
        result = by_case.get(waiver.case)
        if result is None:
            why = "сценарий не выполнялся"
        elif result.verdict == VERDICT_ERROR:
            why = "сценарий завершился ERROR — отказ снимает только FAIL"
        else:
            check = next((c for c in result.checks
                          if c.name == waiver.check), None)
            if check is None:
                why = "в сценарии нет проверки с таким именем"
            elif check.waiver is not None and check.waiver.id == waiver.id:
                continue
            elif check.ok:
                why = ("проверка прошла — отказ не понадобился, запись пора "
                       "снять")
            else:
                why = "проверка не объявлена waivable — отказ её не снимает"
        not_applied.append({"id": waiver.id, "case": waiver.case,
                            "check": waiver.check, "why": why})
    return {"applied": applied, "not_applied": not_applied}


def _esc(text: Any) -> str:
    s = str(text)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))
