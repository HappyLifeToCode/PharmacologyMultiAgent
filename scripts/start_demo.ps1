param([int]$Port = 8766, [string]$Python = '')
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
    if (!$pythonCommand) { throw '找不到 Python。请先创建项目 .venv、激活已有环境，或通过 -Python 指定解释器路径。' }
    $Python = $pythonCommand.Source
}
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $projectRoot
try {
    Write-Host "Pharmacology Multi-Agent: http://127.0.0.1:$Port"
    & $Python server/app.py --port $Port
    if ($LASTEXITCODE -ne 0) { throw "工作台启动失败，Python 退出码：$LASTEXITCODE" }
} finally { Pop-Location }
