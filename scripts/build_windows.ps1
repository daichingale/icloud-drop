# 学校のパソコンでも、そのまま起動できるフォルダを作る。
# 管理者権限は使わない。レジストリにも書かない。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -e ".[icloud,keyring,dev]" pyinstaller
python -m pytest
python -m PyInstaller --noconfirm --clean icloud_drop.spec

$dest = Join-Path $root "dist\IcloudDrop"
Copy-Item (Join-Path $root "README.md") (Join-Path $dest "README.md") -Force
@"
このフォルダごとUSBメモリやデスクトップに置いて、IcloudDrop.exe を起動してください。
インストールは要りません。設定は隣の data フォルダに保存します。
"@ | Set-Content -Path (Join-Path $dest "portable.txt") -Encoding utf8

$zip = Join-Path $root "dist\IcloudDrop-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $dest "*") -DestinationPath $zip
Write-Host "できました: $zip"
Write-Host "中の IcloudDrop.exe を、フォルダごとコピーして使います。"
