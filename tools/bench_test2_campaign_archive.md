# `bench_test2_campaign_archive.py` — archive и SHA-256 evidence Test №2

`bench_test2_campaign_archive.py` завершает **уже законченную** physical no-HV кампанию Test №2. Он инвентаризирует raw evidence, рассчитывает SHA-256 каждого regular file, создаёт deterministic ZIP с internal manifest и отдельный receipt с SHA-256 самого ZIP.

> Инструмент работает только с файлами. Он не открывает COM, не управляет Nucleo/ST-Link/STEVAL, не запускает sigrok, не прошивает firmware и не меняет исходный campaign directory. `ARCHIVE=PASS` доказывает целостность и полноту заданного evidence inventory; он **не является** physical Test №2 PASS и не является Stage-A разрешением.

## 1. Когда запускать

Запускать архиватор только после того, как завершены: automated capture, manual scope review, сохранение UART/sigrok/DMM evidence, fault-clear/closure recording и возврат к generic default-deny по физическому протоколу. Сначала завершите запись доказательств, затем заморозьте содержимое campaign directory, и только после этого создавайте archive.

Нельзя задавать output directory внутри campaign или campaign внутри output directory. Архиватор не разрешает перезаписывать существующие ZIP/receipt: для повторной архивации используйте новый пустой output folder. Это предотвращает подмену ранее выпущенного evidence archive.

## 2. Required evidence inventory

В корне campaign должны находиться следующие **regular files**. G0 summary допускается либо в корне, либо в `g0/`, но не одновременно в обоих местах.

| Logical evidence | Допустимый путь | Роль |
|---|---|---|
| G0 approval | `g0_approval.json` | Human approval record, binding source and firmware identity. |
| G0 build manifest | `diagnostic_build_manifest.json` | Full define/source/firmware identity. |
| G0 build log | `diagnostic_build.log` | Original compiler output. |
| G0 summary | `g0_check_summary.json` **or** `g0/g0_check_summary.json` | Must contain `gate=HIL_TEST2_G0`, `verdict=PASS` for an accepted archive. |
| Execution metadata | `metadata.json` | Physical execution identity and arguments. |
| Execution summary | `summary.json` | Automation/scope/final outcome. |
| UART log | `uart.log` | Continuous boot/TX/RX log. |
| sigrok CSV | `sigrok_digital.csv` | Actual Test №2 capture. |
| sigrok stdout | `sigrok_stdout.log` | Capture process evidence. |
| sigrok stderr | `sigrok_stderr.log` | Capture process diagnostics, including an intentionally empty file. |

Other regular files—for example DMM worksheet, screenshots, manual scope review and default-deny closure records—are **also included and hashed** automatically. Preserve them in the campaign before archive creation; their manual adequacy remains the responsibility of the physical report reviewer.

## 3. Create archive

Create a fresh sibling output folder outside the campaign. Example for ПК-3:

```powershell
$campaign = 'D:\campaign_raw\test2_nohv_20260825T120000Z'
$output   = 'D:\campaign_archive\test2_nohv_20260825T120000Z'

py -3 tools\bench_test2_campaign_archive.py archive `
  --campaign $campaign `
  --output-dir $output
```

For a complete campaign with G0 PASS, output is:

```text
ARCHIVE=PASS
receipt=D:\campaign_archive\...\test2_nohv_<UTC>.archive_receipt.json
archive=D:\campaign_archive\...\test2_nohv_<UTC>.evidence.zip
```

| Result | Exit code | Meaning | Required handling |
|---|---:|---|---|
| `ARCHIVE=PASS` | `0` | Required inventory exists, G0 summary is PASS, internal manifest/file hashes and ZIP self-check pass. | Preserve ZIP and receipt together with the physical report. |
| `ARCHIVE=FAIL` | `2` | Missing/ambiguous evidence, G0 invalid/FAIL, symlink, read error or archive-integrity failure. | Recovery ZIP/receipt may be created for forensic retention, but it is **not** an accepted evidence archive. Do not replace raw evidence or claim Test PASS. |

## 4. Archive contents and integrity

The ZIP includes all regular campaign files and one internal `campaign_manifest.json`. The manifest lists every evidence member in lexical order as:

```json
{
  "path": "uart.log",
  "size_bytes": 1234,
  "sha256": "<64 lowercase hex>"
}
```

It also contains a `tree_sha256` binding the complete ordered inventory. ZIP member names use only canonical relative POSIX paths; symlinks, absolute paths, `..`, duplicates and non-regular files are rejected. ZIP metadata and member order are fixed, so identical campaign bytes produce identical archive bytes.

The external receipt contains the archive SHA-256, internal manifest SHA-256, all checks and a receipt timestamp. SHA-256 establishes file-integrity linkage; it is not a cryptographic signature of a person or a replacement for safety-owner approval.

## 5. Verify later without extraction

Copy both files together. On any workstation with Python, verify without extracting the evidence:

```powershell
py -3 tools\bench_test2_campaign_archive.py verify `
  --archive D:\campaign_archive\test2_nohv_<UTC>\test2_nohv_<UTC>.evidence.zip `
  --receipt D:\campaign_archive\test2_nohv_<UTC>\test2_nohv_<UTC>.archive_receipt.json
```

`ARCHIVE_VERIFY=PASS` and exit `0` require a matching ZIP SHA-256, readable/canonical ZIP, one valid internal manifest, full file coverage and SHA-256/size agreement for every member. Any tampering with ZIP, receipt or manifest returns exit `2`.

## 6. Boundaries

The archive accepts a Test №2 campaign whose `summary.json` says automation/scope/final FAIL: a failure must be retained rather than discarded. It requires G0 PASS only to classify the archive itself as an accepted evidence package. Neither archive result changes the test verdict or permits Stage A.

## References

[1]: `TZ_BENCH_TEST2_CAMPAIGN_ARCHIVE.md` — formal archive contract.

[2]: `tools/bench_test2_g0_check.md` — G0 evidence and pre-flash approval boundary.

[3]: `tools/bench_test2_capture.md` — physical Test №2 evidence and strict verdict contract.
