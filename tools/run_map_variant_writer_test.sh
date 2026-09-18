#!/usr/bin/env sh
# TZ-02: hosted-проверка генератора вариантов карты (offline-only, firmware не меняется).
# Использование: sh tools/run_map_variant_writer_test.sh
set -eu

HERE=$(dirname "$0")
ROOT=$(cd "$HERE/.." && pwd)
cd "$ROOT"

echo "--- 1. unit-тесты (round-trip / identity / canonical / CRC / negatives) ---"
make -f tools/map_variant_writer.mk map-variant-test
make -f tools/map_variant_writer.mk map-variant-cli >/dev/null

OUT=variant_out
rm -rf "$OUT"
mkdir -p "$OUT"

echo "--- 2. e2e: фикстура -> M1/M2/M3 ---"
./tools/map_variant_cli.exe --emit-fixture "$OUT/M0_fixture.bin"
./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/M1.bin" --variant M1 --scale 11/10 --manifest "$OUT/M1.json"
./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/M2.bin" --variant M2 --offset 50 --manifest "$OUT/M2.json"
./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/M3.bin" --variant M3 --sector 3 --window 1 --scale 2/1

for f in M0_fixture M1 M2 M3; do
    size=$(wc -c < "$OUT/$f.bin" | tr -d ' \r\n')
    if [ "$size" != "497" ]; then
        echo "FAIL: $f.bin размер $size != 497"; exit 1
    fi
done
if cmp -s "$OUT/M0_fixture.bin" "$OUT/M1.bin"; then echo "FAIL: M1 совпал с M0"; exit 1; fi
if cmp -s "$OUT/M0_fixture.bin" "$OUT/M2.bin"; then echo "FAIL: M2 совпал с M0"; exit 1; fi
if cmp -s "$OUT/M1.bin" "$OUT/M2.bin"; then echo "FAIL: M1 == M2"; exit 1; fi
if [ ! -s "$OUT/M1.json" ]; then echo "FAIL: манифест пуст"; exit 1; fi

echo "--- 3. негативные кейсы (CLI должен отказать) ---"
if ./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/bad.bin" --variant BAD --scale 1/100000 >/dev/null 2>&1; then
    echo "FAIL: downscale с обнулением матрицы принят"; exit 1
fi
if ./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/bad.bin" --variant BAD --scale 1/0 >/dev/null 2>&1; then
    echo "FAIL: scale_den=0 принят"; exit 1
fi
if ./tools/map_variant_cli.exe "$OUT/M0_fixture.bin" "$OUT/bad.bin" --variant BAD --scale 300000000/1 >/dev/null 2>&1; then
    echo "FAIL: переполнение принято"; exit 1
fi
if [ -e "$OUT/bad.bin" ]; then echo "FAIL: отвергнутый вариант всё равно записан"; exit 1; fi

echo "--- 4. sha256 артефактов ---"
sha256sum "$OUT/M0_fixture.bin" "$OUT/M1.bin" "$OUT/M2.bin" "$OUT/M3.bin"

rm -rf "$OUT"
echo "--- run_map_variant_writer_test: PASS (offline generator, firmware SHA не затронут) ---"
