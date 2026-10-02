# unpack_firmware.ps1 — распаковка CI-образа из firmware/ci733/artifact_ci733.zip
# в пути пакета и проверка SHA256SUMS. Файл: UTF-8 with BOM + CRLF (PS 5.1).
# Запуск из корня пакета: powershell -ExecutionPolicy Bypass -File .\scripts\unpack_firmware.ps1

param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $Root

$zip = 'firmware/ci733/artifact_ci733.zip'
if (-not (Test-Path -LiteralPath $zip)) { throw "нет $zip" }

$tmp = Join-Path $env:TEMP ('pc3fw_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force

Copy-Item (Join-Path $tmp 'firmware.bin') 'firmware/pc3_foc_4507983.bin' -Force
Copy-Item (Join-Path $tmp 'firmware.elf') 'firmware/pc3_foc_4507983.elf' -Force
Copy-Item (Join-Path $tmp 'firmware.map') 'firmware/pc3_foc_4507983.map' -Force
Copy-Item (Join-Path $tmp 'firmware.bin') 'firmware/ci733/firmware.bin' -Force
Copy-Item (Join-Path $tmp 'firmware.elf') 'firmware/ci733/firmware.elf' -Force
Copy-Item (Join-Path $tmp 'firmware.map') 'firmware/ci733/firmware.map' -Force
Remove-Item -LiteralPath $tmp -Recurse -Force

$bad = 0
Get-Content -LiteralPath 'SHA256SUMS.txt' | ForEach-Object {
    if ($_ -match '^\s*$') { return }
    $parts = $_ -split '\s+', 2
    $file = $parts[1]
    if (-not (Test-Path -LiteralPath $file)) { Write-Host "MISSING: $file"; $bad++ ; return }
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLower()
    if ($h -ne $parts[0].ToLower()) { Write-Host "MISMATCH: $file"; $bad++ }
}
if ($bad -eq 0) { Write-Host 'SHA256SUMS: OK (все 16 файлов на месте)' } else { Write-Host "проблем: $bad"; exit 1 }
Write-Host ''
Write-Host 'Образ готов: firmware/pc3_foc_4507983.bin  (flash: ST-Link V4, SWD)'
