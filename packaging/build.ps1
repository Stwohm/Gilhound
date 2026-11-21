param([switch]$Stub)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot
Push-Location $repo
try {
    $python = if (Test-Path '.venv/Scripts/python.exe') {
        (Resolve-Path '.venv/Scripts/python.exe').Path
    } else { 'python' }
    function Run([string]$program, [string[]]$arguments) {
        & $program @arguments
        if ($LASTEXITCODE -ne 0) { throw "$program exited with $LASTEXITCODE" }
    }
    $version = & $python -c 'from gilhound import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Gilhound version' }
    if (-not $Stub -and -not (Test-Path 'gilhound/desktop.py')) {
        throw 'Desktop entry missing. Use -Stub only for a local packaging smoke test.'
    }
    Push-Location ui
    try {
        Run npm.cmd @('ci')
        Run npm.cmd @('run','build')
    } finally { Pop-Location }

    $work = Join-Path $repo ('build/' + [guid]::NewGuid())
    $stage = Join-Path $work 'bundle'
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $env:GILHOUND_BUILD_STUB = if ($Stub) { '1' } else { '0' }
    Run $python @('-m','PyInstaller','--noconfirm','--distpath',$work,'--workpath',
        "$work/cache",'packaging/gilhound-app.spec')
    Move-Item -LiteralPath "$work/gilhound-app" -Destination "$stage/app-$version"
    New-Item -ItemType File -Path "$stage/app-$version/READY" | Out-Null
    Run $python @('-m','PyInstaller','--onefile','--noconsole','--name','Gilhound',
        '--paths',$repo,'--specpath',$work,'--workpath',"$work/cache",'--distpath',$stage,
        'launcher/launcher.py')
    Copy-Item -LiteralPath "$PSScriptRoot/install.ps1" -Destination $stage
    $output = if ($Stub) { 'dist/stub' } else { 'dist' }
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    $base = Join-Path (Resolve-Path $output) "Gilhound-$version-win64"
    Run $python @('-c','import shutil,sys; shutil.make_archive(*sys.argv[1:])',
        $base,'zip',$stage)
    $hash = (Get-FileHash -Algorithm SHA256 "$base.zip").Hash.ToLowerInvariant()
    "$hash  Gilhound-$version-win64.zip" | Set-Content "$output/SHA256SUMS.txt" -Encoding ascii
    Write-Output "$base.zip"
} finally {
    Remove-Item Env:GILHOUND_BUILD_STUB -ErrorAction SilentlyContinue
    Pop-Location
}

