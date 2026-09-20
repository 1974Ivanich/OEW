#!/usr/bin/env python3
"""Fail-closed offline analysis of autotune ``idle``/``pairs`` logs."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

IDLE_RE = re.compile(r"@IDLE:PROG=(?P<progress>\d+)/(?:\d+):D=(?P<duty>\d+):I=(?P<current>-?\d+):L=(?P<ls>-?\d+)(?::REP=(?P<rep>\d+)/(?:\d+))?")
PAIR_RE = re.compile(r"@AT:PAIR:(?P<name>[A-Za-z0-9_-]+):Rs=(?P<rs>-?\d+):Ls=(?P<ls>-?\d+)(?::Isat=(?P<isat>-?\d+))?(?::V=(?P<valid>\d+))?")
PAIRS_RE = re.compile(r"@AT:PAIRS:OK:Rs=(?P<rs>-?\d+):Ls=(?P<ls>-?\d+):ASYM=(?P<asym>-?\d+)%:VALID=(?P<valid>\d+)")


def _spread(values: list[int]) -> float:
    m = median(values)
    return 0.0 if m == 0 else (max(values) - min(values)) * 100.0 / abs(m)


def parse(path: Path) -> tuple[list[dict], list[dict], dict | None]:
    idle: list[dict] = []
    pairs: list[dict] = []
    summary = None
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_no, line in enumerate(stream, 1):
            match = IDLE_RE.search(line)
            if match:
                item = {"duty_pct": int(match["duty"]), "current_ma": int(match["current"]),
                        "ls_uh": int(match["ls"]), "line": line_no}
                if match["rep"] is not None:
                    item["rep"] = int(match["rep"])
                idle.append(item)
                continue
            match = PAIR_RE.search(line)
            if match:
                pairs.append({"name": match["name"], "rs_mohm": int(match["rs"]),
                              "ls_uh": int(match["ls"]),
                              "isat_ma": int(match["isat"]) if match["isat"] else None,
                              "valid": int(match["valid"]) if match["valid"] else 1,
                              "line": line_no})
                continue
            match = PAIRS_RE.search(line)
            if match:
                summary = {"rs_mohm": int(match["rs"]), "ls_uh": int(match["ls"]),
                           "asym_pct": int(match["asym"]), "valid": int(match["valid"]),
                           "line": line_no}
    if not idle and not pairs:
        raise ValueError("log: no @IDLE:PROG or @AT:PAIR records")
    return idle, pairs, summary


def analyze(path: Path) -> dict:
    idle, pairs, summary = parse(path)
    flags: list[dict] = []
    rejected: list[dict] = []
    points: list[dict] = []
    if idle:
        grouped: dict[int, list[dict]] = defaultdict(list)
        for item in idle:
            grouped[item["duty_pct"]].append(item)
        # A complete repeated measurement has an explicit REP field, or at least five records.
        for duty in sorted(grouped):
            group = grouped[duty]
            reps = sorted(x.get("rep", i + 1) for i, x in enumerate(group))
            if len(group) < 5 or reps != list(range(1, len(group) + 1)):
                rejected.append({"code": "INCOMPLETE_REPETITIONS", "duty_pct": duty,
                                 "records": len(group), "reps": reps})
            vals = [x["ls_uh"] for x in group]
            p = {"duty_pct": duty, "current_ma": round(median(x["current_ma"] for x in group), 6),
                 "ls_median_uh": round(median(vals), 6), "ls_values_uh": vals,
                 "spread_pct": round(_spread(vals), 6), "records": len(group)}
            points.append(p)
            if p["spread_pct"] >= 15.0:
                flags.append({"code": "SPREAD_HIGH", "duty_pct": duty, "spread_pct": p["spread_pct"]})
        if len(points) < 2:
            rejected.append({"code": "INSUFFICIENT_CURVE_POINTS", "points": len(points)})
        ordered = sorted(points, key=lambda x: (x["current_ma"], x["duty_pct"]))
        for before, after in zip(ordered, ordered[1:]):
            if after["ls_median_uh"] > before["ls_median_uh"]:
                flags.append({"code": "LS_RISES_WITH_I", "from_current_ma": before["current_ma"],
                              "to_current_ma": after["current_ma"], "from_ls_uh": before["ls_median_uh"],
                              "to_ls_uh": after["ls_median_uh"]})
        zero_points = [p["duty_pct"] for p in points if p["ls_median_uh"] == 0]
        if zero_points:
            flags.append({"code": "LS_ZERO", "duty_pct": zero_points,
                          "possible_causes": ["insufficient di", "ADC desynchronization",
                                               "current-path noise or failure"]})
        if len(points) >= 2:
            l0 = points[0]["ls_median_uh"]
            reached = any(p["ls_median_uh"] <= l0 * 0.7 for p in points[1:]) if l0 > 0 else False
            if l0 <= 0 or not reached:
                flags.append({"code": "ISAT_UNREACHED", "reason": "current range did not reach a 30% Ls drop"})
    pair_result = None
    if pairs:
        by_name: dict[str, list[dict]] = defaultdict(list)
        for item in pairs:
            by_name[item["name"]].append(item)
        names = list(by_name)
        if len(names) != 3 or any(len(v) != 1 for v in by_name.values()):
            rejected.append({"code": "INCOMPLETE_PAIRS", "names": names,
                             "records_per_name": {k: len(v) for k, v in by_name.items()}})
        valid_pairs = [v[0] for v in by_name.values() if len(v) == 1 and v[0]["valid"] != 0]
        if len(valid_pairs) < 3:
            rejected.append({"code": "VALID_LT_3", "valid": len(valid_pairs)})
        if len(valid_pairs) == 3:
            ls_values = [x["ls_uh"] for x in valid_pairs]
            asym = _spread(ls_values)
            pair_result = {"coils": sorted(valid_pairs, key=lambda x: x["name"]),
                           "median_ls_uh": round(median(ls_values), 6), "asymmetry_pct": round(asym, 6),
                           "valid": 3}
            if asym >= 10.0:
                flags.append({"code": "ASYM_HIGH", "asymmetry_pct": round(asym, 6),
                              "coils": [x["name"] for x in valid_pairs]})
            if summary is not None and (summary["valid"] != 3 or summary["asym_pct"] >= 10):
                rejected.append({"code": "SUMMARY_INCONSISTENT", "summary": summary,
                                 "computed_valid": 3, "computed_asym_pct": round(asym, 6)})
    if not points and pair_result is None:
        rejected.append({"code": "NO_VALID_RESULT"})
    # Incomplete input has no verdict. Rule failures have a reject verdict.
    # LS_ZERO — это ОТСУТСТВИЕ измерения (нулевой/недовозбуждённый результат), а не успех:
    # вердикт PASS на нулевой кривой был бы приёмкой ничего.
    reject_codes = {"SPREAD_HIGH", "ASYM_HIGH", "LS_RISES_WITH_I", "LS_ZERO"}
    verdict = None if rejected else ("REJECT" if any(f["code"] in reject_codes for f in flags) else "PASS")
    return {"source": str(path), "curve_ls_i": points, "pairs": pair_result,
            "summary": summary, "flags": flags, "rejected": rejected,
            "verdict": verdict,
            "limitations": [
                "Offline log analysis only; it does not energize hardware or measure Ls itself.",
                "Ls=0 is diagnostic, not a selected root cause.",
                "Isat=0/unreached is not an error when the curve never drops by 30%.",
                "No verdict is emitted for incomplete or inconsistent input.",
            ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--json", required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(Path(args.log))
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        Path(args.json).write_text(text, encoding="utf-8", newline="\n")
        print(text, end="")
        return 2 if result["rejected"] else (1 if result["verdict"] == "REJECT" else 0)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
