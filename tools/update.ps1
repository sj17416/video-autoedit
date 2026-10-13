# 한방 업데이트 — GitHub에서 최신 코드를 받아 설치 폴더에 덮어쓴다.
#
# 이 파일은 `업데이트.bat` 안에 base64(UTF-16LE)로 박혀 있다. 여기를 고친 뒤에는
# `python tools/make_update_bat.py`를 실행해 .bat을 다시 만들어야 한다.
# (왜 base64인가: 배치 파일에 PowerShell을 그대로 넣으면 > | & % 같은 글자를 일일이
#  탈출시켜야 하고, 한글도 깨진다. -EncodedCommand는 둘 다 피해 간다.)
#
# 설치 폴더를 찾는 순서
#   1) 이 .bat이 놓인 폴더에 app.py가 있으면 거기
#   2) 바탕화면 바로가기가 가리키는 폴더 (OneDrive로 옮겨진 바탕화면도 본다)
#   3) 내 문서 폴더 아래에서 launcher.pyw 찾기
#
# 사진·영상·API 키는 ZIP에 애초에 들어있지 않으므로(.gitignore) 덮어써도 사라지지 않는다.

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'sj17416/video-autoedit'
$Zip = "https://github.com/$Repo/archive/refs/heads/main.zip"
# .bat이 자기 폴더를 환경변수로 넘겨 준다.
# (-EncodedCommand 뒤에 인자를 붙이면 PowerShell이 스크립트 경로로 보고 오류를 낸다 — 실제로 확인했다.)
$BatDir = if ($env:AUTOEDIT_BAT_DIR) { $env:AUTOEDIT_BAT_DIR } else { (Get-Location).Path }

function Find-Install {
    param([string]$BatDir)

    if (Test-Path (Join-Path $BatDir 'app.py')) { return (Resolve-Path $BatDir).Path }

    $desks = New-Object System.Collections.Generic.List[string]
    foreach ($d in @([Environment]::GetFolderPath('Desktop'),
                     (Join-Path $env:USERPROFILE 'Desktop'),
                     $(if ($env:OneDrive) { Join-Path $env:OneDrive 'Desktop' }))) {
        if ($d -and (Test-Path $d) -and -not $desks.Contains($d)) { $desks.Add($d) }
    }
    # WScript.Shell이 없는 환경(또는 COM이 막힌 PC)에서도 아래 3)번으로 넘어가야 한다
    $sh = $null
    try { $sh = New-Object -ComObject WScript.Shell } catch { }
    foreach ($d in $(if ($sh) { $desks } else { @() })) {
        foreach ($lnk in (Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue)) {
            try { $s = $sh.CreateShortcut($lnk.FullName) } catch { continue }
            foreach ($cand in @($s.WorkingDirectory, (Split-Path $s.TargetPath -Parent))) {
                if ($cand -and (Test-Path (Join-Path $cand 'app.py'))) { return $cand }
            }
        }
    }

    foreach ($root in @($env:USERPROFILE, $env:OneDrive)) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        $hit = Get-ChildItem -LiteralPath $root -Filter 'launcher.pyw' -Recurse -Depth 5 `
                             -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.Directory.FullName }
    }
    return $null
}

$dest = Find-Install $BatDir
if (-not $dest) {
    Write-Host ''
    Write-Host '  설치 폴더를 찾지 못했습니다.' -ForegroundColor Red
    Write-Host '  이 파일(업데이트.bat)을 app.py가 있는 폴더에 넣고 다시 실행해 주세요.'
    exit 1
}
Write-Host "  설치 폴더: $dest"

$tmp = Join-Path $env:TEMP ('autoedit_update_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
    Write-Host '  최신 버전을 내려받는 중...'
    $zipPath = Join-Path $tmp 'main.zip'
    Invoke-WebRequest -Uri $Zip -OutFile $zipPath -UseBasicParsing
    $mb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "  받기 완료 ($mb MB). 압축을 풉니다..."

    $out = Join-Path $tmp 'x'
    Expand-Archive -LiteralPath $zipPath -DestinationPath $out -Force
    $src = Get-ChildItem -LiteralPath $out -Directory | Select-Object -First 1
    if (-not $src -or -not (Test-Path (Join-Path $src.FullName 'app.py'))) {
        throw '받은 파일이 온전하지 않습니다. 잠시 뒤 다시 시도해 주세요.'
    }

    Write-Host '  덮어쓰는 중...'
    Copy-Item -Path (Join-Path $src.FullName '*') -Destination $dest -Recurse -Force
    $n = (Get-ChildItem -LiteralPath $src.FullName -Recurse -File).Count
    Write-Host ''
    Write-Host "  [완료] 파일 $n 개를 최신으로 바꿨습니다." -ForegroundColor Green
    Write-Host '  사진, 영상, API 키는 그대로입니다.'
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
