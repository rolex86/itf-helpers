$base = 'D:\projekty\itf-helpers\chnw_MIS_downloader\jen_CE_RoHS_FCC_EN18031'
$zipDir = Join-Path $base 'ZIP'

$tokens = @('CE', 'RoHS', 'FCC', 'EN18031')

Get-ChildItem -LiteralPath $base -Directory |
    Where-Object { $_.Name -ne 'ZIP' } |
    ForEach-Object {
        $modelName = $_.Name
        $found = [System.Collections.Generic.List[string]]::new()

        $files = Get-ChildItem -LiteralPath $_.FullName -Recurse -File

        foreach ($token in $tokens) {
            $pattern = '(?i)(?<![\p{L}\p{Nd}])' + [regex]::Escape($token) + '(?![\p{L}\p{Nd}])'

            if ($files | Where-Object { $_.BaseName -match $pattern } | Select-Object -First 1) {
                $found.Add($token)
            }
        }

        $oldZip = Join-Path $zipDir ($modelName + '.zip')

        if (-not (Test-Path -LiteralPath $oldZip)) {
            Write-Host "ZIP nenalezen: $oldZip" -ForegroundColor Yellow
            return
        }

        if ($found.Count -gt 0) {
            $newZipName = $modelName + '_' + ($found -join '_') + '.zip'
        }
        else {
            $newZipName = $modelName + '.zip'
        }

        $newZip = Join-Path $zipDir $newZipName

        if ($oldZip -ne $newZip) {
            if (Test-Path -LiteralPath $newZip) {
                Remove-Item -LiteralPath $newZip -Force
            }

            Rename-Item -LiteralPath $oldZip -NewName $newZipName
            Write-Host "Prejmenovano: $modelName -> $newZipName"
        }
        else {
            Write-Host "Beze zmeny: $newZipName"
        }
    }

Write-Host ""
Write-Host "Hotovo."