param(
    [int]$Port = 8766,
    [string]$Python = '',
    [string]$CodexSourceHome = '',
    [string]$StringDataDir = '',
    [string]$BatmanDataDir = '',
    [string]$LocalPackages = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

if (!$Python) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } else { 'python' }
}
if (Test-Path -LiteralPath $Python -PathType Leaf) {
    $Python = (Resolve-Path -LiteralPath $Python).Path
} else {
    $pythonCommand = Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue
    if (!$pythonCommand) {
        throw 'Python executable not found. Create the project environment or pass -Python with an executable path.'
    }
    $Python = $pythonCommand.Source
}

function Set-OptionalPathEnv([string]$Name, [string]$Value) {
    if (!$Value) { return }
    if (!(Test-Path -LiteralPath $Value -PathType Container)) {
        throw "$Name directory not found: $Value"
    }
    $resolved = (Resolve-Path -LiteralPath $Value).Path
    Set-Item -Path ("Env:" + $Name) -Value $resolved
}

Set-OptionalPathEnv 'PHARM_CODEX_SOURCE_HOME' $CodexSourceHome
Set-OptionalPathEnv 'PHARM_STRING_DATA_DIR' $StringDataDir
Set-OptionalPathEnv 'PHARM_BATMAN_DATA_DIR' $BatmanDataDir

if ($LocalPackages) {
    if (!(Test-Path -LiteralPath $LocalPackages -PathType Container)) {
        throw "Local Python package directory not found: $LocalPackages"
    }
    $resolvedPackages = (Resolve-Path -LiteralPath $LocalPackages).Path
    $env:PYTHONPATH = if ($env:PYTHONPATH) {
        "$resolvedPackages;$env:PYTHONPATH"
    } else {
        $resolvedPackages
    }
}

$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $projectRoot
try {
    Write-Host "Pharmacology Multi-Agent: http://127.0.0.1:$Port"
    & $Python server/app.py --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Workbench startup failed. Python exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
