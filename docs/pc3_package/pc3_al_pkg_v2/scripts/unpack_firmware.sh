#!/bin/sh
# unpack_firmware.sh — распаковка CI-образа из firmware/ci733/artifact_ci733.zip
# в пути пакета и проверка SHA256SUMS (git-bash / Linux).
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

zip="firmware/ci733/artifact_ci733.zip"
[ -f "$zip" ] || { echo "нет $zip" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
unzip -q -o "$zip" -d "$tmp"

for f in bin elf map; do
    cp "$tmp/firmware.$f" "firmware/pc3_foc_4507983.$f"
    cp "$tmp/firmware.$f" "firmware/ci733/firmware.$f"
done

sha256sum -c SHA256SUMS
echo
echo "Образ готов: firmware/pc3_foc_4507983.bin  (flash: ST-Link V4, SWD)"
