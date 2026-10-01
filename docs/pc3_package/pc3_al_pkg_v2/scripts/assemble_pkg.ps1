# assemble_pkg.ps1 — сборка и верификация pc3-pkg-al-final-v2
# Запуск из корня пакета:  powershell -ExecutionPolicy Bypass -File .\scripts\assemble_pkg.ps1
# Назначение: подставить хеш .bin в manifest.json, собрать SHA256SUMS и архив.
# Файл: UTF-8 with BOM + CRLF (PS 5.1).

param(
    [string]$Root = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $Root

$required = @(
    'firmware/pc3_foc_4507983.bin',
    'firmware/pc3_foc_4507983.elf',
    'firmware/pc3_foc_4507983.map',
    'docs/COMMANDS.md',
    'docs/AL_PROCEDURE.md',
    'docs/SAFETY.md',
    'docs/IDENTITIES.md',
    'README.md',
    'CORRECTIONS_v2.md',
    'manifest.json'
)

foreach ($f in $required) {
    if (-not (Test-Path -LiteralPath $f)) { throw "нет обязательного файла: $f" }
}

# 1) хеш прошивки -> manifest.json
$binHash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'firmware/pc3_foc_4507983.bin').Hash.ToLower()
$placeholder = '<фактический sha256 .bin из SHA256SUMS>'
$manifest = Get-Content -Raw -LiteralPath 'manifest.json'
if ($manifest.Contains($placeholder)) {
    $manifest = $manifest.Replace($placeholder, $binHash)
    Set-Content -LiteralPath 'manifest.json' -Value $manifest -Encoding UTF8 -NoNewline
    Write-Host "manifest.json: al.binary_sha256 = $binHash"
} else {
    Write-Host "manifest.json уже без плейсхолдера; сверь al.binary_sha256 с фактическим .bin: $binHash"
}

# 2) SHA256SUMS (+ .txt — для проверки через Get-FileHash)
$lines = foreach ($f in $required) {
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower()
    "$h  $f"
}
Set-Content -LiteralPath 'SHA256SUMS' -Value $lines -Encoding ASCII
Copy-Item -LiteralPath 'SHA256SUMS' -Destination 'SHA256SUMS.txt' -Force
Write-Host "SHA256SUMS и SHA256SUMS.txt записаны ($($lines.Count) записей)"

# 3) архив
$pkgName = Split-Path -Leaf (Get-Location).Path
$parent  = Split-Path -Parent (Get-Location).Path
$archive = Join-Path $parent ($pkgName + '.tar.gz')
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
if (Get-Command tar.exe -ErrorAction SilentlyContinue) {
    & tar.exe -czf $archive -C $parent $pkgName
} else {
    Compress-Archive -Path (Join-Path (Get-Location).Path '*') -DestinationPath ($archive -replace '\.tar\.gz$', '.zip') -Force
    $archive = $archive -replace '\.tar\.gz$', '.zip'
}
$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLower()
Set-Content -LiteralPath ($archive + '.sha256') -Value "$archiveHash  $(Split-Path -Leaf $archive)" -Encoding ASCII
Write-Host "архив: $archive"
Write-Host "SHA256: $archiveHash"

# 4) что делать дальше
Write-Host ""
Write-Host "Проверка получателем (до flash):"
Write-Host "  git-bash:   sha256sum -c SHA256SUMS"
Write-Host "  PowerShell: `$bad=0; Get-Content SHA256SUMS.txt | ForEach-Object { `$p=`$_ -split '\s+',2; `$h=(Get-FileHash -Algorithm SHA256 `$p[1]).Hash.ToLower(); if (`$h -ne `$p[0]) { `$bad++; Write-Host ('MISMATCH: ' + `$p[1]) } }; if (`$bad -eq 0) { 'SHA256SUMS: OK' }"
Write-Host "Дальше: flash firmware/pc3_foc_4507983.bin (ST-Link V4, SWD), затем docs/AL_PROCEDURE.md."
