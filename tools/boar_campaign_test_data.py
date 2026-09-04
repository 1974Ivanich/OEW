#!/usr/bin/env python3
"""Create a synthetic, internally consistent BOAR campaign for local testing.

This tool is NOT for production evidence. It generates placeholder data that
passes verify_boar_campaign_ready.py and provides dummy campaign/manifest.json +
samples.jsonl so that boar_campaign_archive.py can be exercised end-to-end.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


CSV_HEADER = (
    "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,"
    "scope_qualified,note\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root", type=Path, required=True,
        help="output campaign directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.campaign_root.resolve()

    import tempfile

    script_dir = Path(__file__).resolve().parent
    template_script = script_dir / "boar_campaign_template.py"

    # Build a minimal calibration JSON and pass it to the template.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as calib_fh:
        calib_fh.write(json.dumps({"vcc_mv": 5000}))
        calib_path = Path(calib_fh.name)
    try:
        template_res = subprocess.run(
            [sys.executable, str(template_script),
             "--campaign-root", str(root),
             "--calibration", str(calib_path)],
            text=True,
            capture_output=True,
        )
    finally:
        calib_path.unlink(missing_ok=True)
    if template_res.returncode != 0:
        print(template_res.stderr, file=sys.stderr)
        return template_res.returncode

    # Fill logs and CSVs with non-placeholder content.
    log_text = "@MC:REC:dummy\n@MC:DRAIN:records=8\n"
    csv_text = CSV_HEADER + "".join(
        f"{i},2510,2495,,110,15,1,ok\n" for i in range(1, 9)
    )
    for log in (root / "logs").glob("*.log"):
        log.write_text(log_text, encoding="utf-8")
    for csv in (root / "scope").glob("*.csv"):
        csv.write_text(csv_text, encoding="utf-8")

    # Provide dummy ingest output so archive can run without real map_scope_ingest.
    (root / "campaign").mkdir(exist_ok=True)
    (root / "campaign" / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "campaign" / "samples.jsonl").write_text("\n", encoding="utf-8")

    print(f"Synthetic campaign ready: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
