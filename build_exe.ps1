# Lyriplayer（歌詞プレイヤー）を 1 つの exe にする（Windows で実行）
#   使い方: PowerShell でこのフォルダを開いて  .\build_exe.ps1
#   必要なもの: pip install -r requirements.txt pyinstaller（-Install を付けると自動で入れます）
#   GitHub の自動ビルドでは、環境変数 LYRIPLAYER_VERSION（例 v2026.11.18.5）に版の番号を入れて呼びます
param(
    [string]$DistPath = "",
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
if (-not $DistPath) { $DistPath = "$here\dist" }

# Python を探す（python が無ければ py。Microsoft Store を開くだけの偽物の python は使わない）
$py = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*WindowsApps*" } | Select-Object -First 1
if ($py) { $py = $py.Source }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
else { throw "Python が見つかりません。https://www.python.org/downloads/ からインストールしてください。" }

if ($Install) {
    & $py -m pip install --quiet --disable-pip-version-check -r "$here\requirements.txt" pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "必要な部品を入れられませんでした（pip）" }
}

# 版の情報（アプリの設定画面に出す・アップデートの確認に使う）。自分でビルドしたものは版の番号が空になる
New-Item -ItemType Directory -Force "$here\build" | Out-Null
$commit = ""
try { $commit = (git -C $here rev-parse HEAD 2>$null) } catch { }
$version = "$here\build\version.json"
@{ version = "$env:LYRIPLAYER_VERSION".Trim(); commit = "$commit".Trim(); date = (Get-Date -Format "yyyy-MM-dd HH:mm") } |
    ConvertTo-Json | Set-Content -Encoding UTF8 $version

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Lyriplayer" `
    --add-data "$here\fonts;fonts" --add-data "$version;." `
    --add-data "$here\LICENSE;." --add-data "$here\THIRD_PARTY_NOTICES.md;." --add-data "$here\licenses;licenses" `
    --collect-submodules mutagen `
    --workpath "$here\build" --specpath "$here\build" --distpath "$DistPath" `
    "$here\lyrics_player.py"
if ($LASTEXITCODE -ne 0) { throw "exe を作れませんでした（PyInstaller）" }
Write-Host "できました: $DistPath\Lyriplayer.exe"
