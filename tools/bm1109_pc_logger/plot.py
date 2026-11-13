"""Generate trend plots from a BM1109 logger CSV file."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit("Matplotlib missing; install requirements.txt") from exc


PLOT_GROUPS = {
    "pzem_electrical": [
        ("pzem_voltage_v", "Voltage, V"),
        ("pzem_current_a", "Current, A"),
        ("pzem_active_power_w", "Active power, W"),
    ],
    "pzem_quality": [
        ("pzem_frequency_hz", "Frequency, Hz"),
        ("pzem_power_factor", "Power factor"),
    ],
    "pzem_energy": [
        ("pzem_energy_kwh", "Energy, kWh"),
    ],
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return rows


def numeric(value: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def plot_file(path: Path) -> list[Path]:
    rows = load_rows(path)
    if not rows:
        raise ValueError("CSV contains no data rows")
    times = [datetime.fromisoformat(r["timestamp_utc"]) for r in rows]
    created: list[Path] = []

    groups = dict(PLOT_GROUPS)
    ad_cols = sorted({key for row in rows for key in row if key.startswith("ad800_") and key != "ad800_error"})
    if ad_cols:
        groups["ad800_registers"] = [(key, key.removeprefix("ad800_")) for key in ad_cols]

    for group, series in groups.items():
        available = [(key, label) for key, label in series if any(numeric(r.get(key, "")) is not None for r in rows)]
        if not available:
            continue
        fig, ax = plt.subplots(figsize=(11, 5.5))
        for key, label in available:
            vals = [numeric(r.get(key, "")) for r in rows]
            ax.plot(times, vals, label=label, linewidth=1.2)
        ax.set_title(group.replace("_", " ").title())
        ax.set_xlabel("Time (UTC)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        fig.autofmt_xdate()
        fig.tight_layout()
        target = path.with_name(path.stem + "_" + group + ".png")
        fig.savefig(target, dpi=150)
        plt.close(fig)
        created.append(target)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot BM1109 logger CSV trends")
    parser.add_argument("csv_file", type=Path)
    args = parser.parse_args()
    outputs = plot_file(args.csv_file)
    if not outputs:
        print("No numeric measurement columns found.")
        return 1
    for item in outputs:
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
