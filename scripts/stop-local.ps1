$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot 'backend/instance/processes.json'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host '没有本项目脚本管理的进程。'; exit 0 }
$records = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
foreach ($entry in $records) {
    $running = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.id)" -ErrorAction SilentlyContinue
    if ($running -and $running.ExecutablePath -eq $entry.executable -and $running.CommandLine.Contains($entry.marker) -and (-not $entry.created -or $running.CreationDate.ToUniversalTime().Ticks.ToString() -eq $entry.created)) {
        Stop-Process -Id $entry.id
        Write-Host "Stopped $($entry.name)"
    }
}
Remove-Item -LiteralPath $pidPath
Write-Host 'ClickHouse 和本机 MySQL 保持运行，数据未删除。'
