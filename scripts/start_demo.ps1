param([int]$Port = 8766, [string]$Python = 'D:\Anaconda\envs\prim\python.exe')
$ErrorActionPreference = 'Stop'
if (!(Test-Path -LiteralPath $Python)) { throw '指定 Python 不存在，请激活 prim 或传入 -Python 路径。' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $projectRoot
try {
    Write-Host "Pharmacology Multi-Agent: http://127.0.0.1:$Port"
    & $Python server/app.py --port $Port
} finally { Pop-Location }
