# Lyriplayer（歌詞プレイヤー）を 1 つの exe にする（Windows で実行）
#   使い方: PowerShell でこのフォルダを開いて  .\build_exe.ps1
#   必要なもの: pip install -r requirements.txt pyinstaller（-Install を付けると自動で入れます）
#   アプリの「アップデート」からは  -DistPath <作る場所> -Install  を付けて呼ばれます
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

# 版の情報（アプリの設定画面に出す・アップデートの確認に使う）
New-Item -ItemType Directory -Force "$here\build" | Out-Null
$commit = ""; $branch = ""
try {
    $commit = (git -C $here rev-parse HEAD 2>$null)
    $branch = (git -C $here rev-parse --abbrev-ref HEAD 2>$null)
} catch { }
$version = "$here\build\version.json"
@{ commit = "$commit".Trim(); branch = "$branch".Trim(); date = (Get-Date -Format "yyyy-MM-dd HH:mm") } |
    ConvertTo-Json | Set-Content -Encoding UTF8 $version

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Lyriplayer" `
    --add-data "$here\fonts;fonts" --add-data "$version;." `
    --add-data "$here\LICENSE;." --add-data "$here\THIRD_PARTY_NOTICES.md;." --add-data "$here\licenses;licenses" `
    --collect-submodules mutagen `
    --workpath "$here\build" --specpath "$here\build" --distpath "$DistPath" `
    "$here\lyrics_player.py"
if ($LASTEXITCODE -ne 0) { throw "exe を作れませんでした（PyInstaller）" }
Write-Host "できました: $DistPath\Lyriplayer.exe"
