@echo off
setlocal EnableExtensions

set "SRC=D:\projekty\itf-helpers\chnw_MIS_downloader\chainway_downloads"
set "DST=%SRC%\jen_CE_RoHS_FCC_EN18031"
set "TMPPS=%TEMP%\itf_copy_cert_files_%RANDOM%%RANDOM%.ps1"

(
echo $ErrorActionPreference = 'Stop'
echo try {
echo     $src = '%SRC%'
echo     $dst = '%DST%'
echo     $dstName = Split-Path $dst -Leaf
echo.
echo     if ^(-not ^(Test-Path -LiteralPath $src^)^) {
echo         throw "Zdrojova slozka neexistuje: $src"
echo     }
echo.
echo     if ^(-not ^(Test-Path -LiteralPath $dst^)^) {
echo         New-Item -ItemType Directory -Path $dst ^| Out-Null
echo     }
echo.
echo     $modelDirs = Get-ChildItem -LiteralPath $src -Directory ^| Where-Object { $_.Name -ne $dstName }
echo.
echo     foreach ^($model in $modelDirs^) {
echo         $modelDst = Join-Path $dst $model.Name
echo         if ^(-not ^(Test-Path -LiteralPath $modelDst^)^) {
echo             New-Item -ItemType Directory -Path $modelDst ^| Out-Null
echo         }
echo.
echo         Get-ChildItem -LiteralPath $model.FullName -Recurse -File ^| Where-Object {
echo             $_.BaseName -match '(?i)(?<![\p{L}\p{Nd}])(CE^|RoHS^|FCC^|EN18031)(?![\p{L}\p{Nd}])'
echo         } ^| ForEach-Object {
echo             $relativeDir = $_.DirectoryName.Substring^($model.FullName.Length^).TrimStart('\')
echo             if ^([string]::IsNullOrWhiteSpace^($relativeDir^)^) {
echo                 $targetDir = $modelDst
echo             } else {
echo                 $targetDir = Join-Path $modelDst $relativeDir
echo             }
echo.
echo             if ^(-not ^(Test-Path -LiteralPath $targetDir^)^) {
echo                 New-Item -ItemType Directory -Path $targetDir -Force ^| Out-Null
echo             }
echo.
echo             Copy-Item -LiteralPath $_.FullName -Destination ^(Join-Path $targetDir $_.Name^) -Force
echo             Write-Host ^("Kopiruju: " + $model.Name + "\" + $(if ^($relativeDir^) { $relativeDir + "\" } else { "" }) + $_.Name^)
echo         }
echo     }
echo.
echo     Write-Host ""
echo     Write-Host "Hotovo."
echo     exit 0
echo }
echo catch {
echo     Write-Host ""
echo     Write-Host "DOSLO K CHYBE:" -ForegroundColor Red
echo     Write-Host $_.Exception.Message -ForegroundColor Red
echo     Write-Host ""
echo     Write-Host "Detail:" -ForegroundColor Yellow
echo     Write-Host $_
echo     exit 1
echo }
) > "%TMPPS%"

echo.
echo Spoustim zpracovani...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%TMPPS%"
set "ERR=%ERRORLEVEL%"

echo.
echo Navratovy kod: %ERR%
echo Docasny PS skript: %TMPPS%
echo.
pause