param([string]$PythonW)

$ErrorActionPreference = 'Stop'
$url = 'http://127.0.0.1:8765/'
$manifestUrl = 'http://127.0.0.1:8765/.well-known/fund-watch.json'
$entryPoint = Join-Path $PSScriptRoot 'fund_watch_entry.py'
if (-not $PythonW) {
    if ($env:FUND_SYSTEM_PYTHONW -and (Test-Path -LiteralPath $env:FUND_SYSTEM_PYTHONW)) {
        $PythonW = $env:FUND_SYSTEM_PYTHONW
    } elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe')) {
        $PythonW = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    } elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE 'fund_python\python313\pythonw.exe')) {
        $PythonW = Join-Path $env:USERPROFILE 'fund_python\python313\pythonw.exe'
    } else {
        $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if ($command) { $PythonW = $command.Source }
    }
}

function Test-FundWatchServer {
    try {
        $manifest = Invoke-RestMethod -Uri $manifestUrl -TimeoutSec 2
        return $manifest.name -eq 'Local Fund Drawdown Watch'
    } catch {
        return $false
    }
}

if (-not (Test-FundWatchServer)) {
    if (-not $PythonW) { throw 'Python was not found. Set FUND_SYSTEM_PYTHONW or install Python.' }
    if (-not (Test-Path -LiteralPath $PythonW)) { throw "Python executable not found: $PythonW" }
    if (-not (Test-Path -LiteralPath $entryPoint)) { throw "Fund watch entrypoint not found: $entryPoint" }
    Start-Process -WindowStyle Hidden -FilePath $PythonW `
        -ArgumentList @("`"$entryPoint`"", '--serve-no-browser') `
        -WorkingDirectory $PSScriptRoot

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-FundWatchServer) { $ready = $true; break }
    }
    if (-not $ready) { throw 'Fund watch server did not start within 15 seconds. Check the logs folder.' }
}

Start-Process $url
