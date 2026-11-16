param([switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$pythonPath = Join-Path $backendRoot '.venv/Scripts/python.exe'
$instanceRoot = Join-Path $backendRoot 'instance'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先按 backend/README.md 安装后端依赖。' }
New-Item -ItemType Directory -Path $instanceRoot -Force | Out-Null
Push-Location $projectRoot
try {
    docker compose --env-file backend/.env -f infra/compose.yaml up -d --wait --wait-timeout 90
    if ($LASTEXITCODE -ne 0) { throw 'ClickHouse 启动失败，请确认 Docker Desktop 正在运行。' }
    Push-Location $backendRoot
    try {
        & $pythonPath -X utf8 -m app.cli init
        if ($LASTEXITCODE -ne 0) { throw '数据库初始化失败，请检查 backend/.env。' }
    } finally { Pop-Location }
    $pidPath = Join-Path $instanceRoot 'processes.json'
    if (Test-Path -LiteralPath $pidPath) {
        $saved = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        foreach ($entry in $saved) {
            $running = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.id)" -ErrorAction SilentlyContinue
            if ($running -and $running.ExecutablePath -eq $entry.executable -and $running.CommandLine.Contains($entry.marker) -and (-not $entry.created -or $running.CreationDate.ToUniversalTime().Ticks.ToString() -eq $entry.created)) {
                throw '本项目服务已启动。请使用 scripts/stop-local.ps1 停止后再启动。'
            }
        }
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) { throw '端口 8000 已占用，未改动现有进程。' }
    $records = @()
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $instanceRoot 'api.stdout.log') -RedirectStandardError (Join-Path $instanceRoot 'api.stderr.log') -PassThru
    $records += @{ id=$apiProcess.Id; executable=$pythonPath; marker='app.main:app'; name='API' }
    $workerProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-m','app.worker') -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $instanceRoot 'worker.stdout.log') -RedirectStandardError (Join-Path $instanceRoot 'worker.stderr.log') -PassThru
    $records += @{ id=$workerProcess.Id; executable=$pythonPath; marker='app.worker'; name='Worker' }
    if (-not $SkipFrontend -and -not (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue)) {
        $nodePath = (Get-Command node.exe).Source
        $frontendRoot = Join-Path $projectRoot 'frontend'
        $vitePath = Join-Path $frontendRoot 'node_modules/vite/bin/vite.js'
        $frontProcess = Start-Process -FilePath $nodePath -ArgumentList @(('"'+$vitePath+'"'),'--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $instanceRoot 'frontend.stdout.log') -RedirectStandardError (Join-Path $instanceRoot 'frontend.stderr.log') -PassThru
        $records += @{ id=$frontProcess.Id; executable=$nodePath; marker=$vitePath; name='Frontend' }
    }
    foreach ($entry in $records) { $managed = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.id)"; $entry.created = $managed.CreationDate.ToUniversalTime().Ticks.ToString() }
    ConvertTo-Json -InputObject @($records) | Set-Content -LiteralPath $pidPath -Encoding utf8
    $healthy = $false
    for ($i=0; $i -lt 20; $i++) {
        try { $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health'; if ($response.status -eq 'ready') { $healthy=$true; break } } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) { throw '服务未能通过健康检查，请查看 backend/instance 下的日志。' }
    Write-Host 'Frontend: http://127.0.0.1:5173/'
    Write-Host 'API docs: http://127.0.0.1:8000/api/docs'
    Write-Host 'First admin credentials: backend/instance/first-admin.txt (local only)'
} finally { Pop-Location }
