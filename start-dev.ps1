$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PgConn = "postgresql://trader:trader_pwd@127.0.0.1:5432/trading"

function Convert-ToPowerShellLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

$RootLiteral = Convert-ToPowerShellLiteral $Root
$FrontendLiteral = Convert-ToPowerShellLiteral (Join-Path $Root "Frontend")
$PgConnLiteral = Convert-ToPowerShellLiteral $PgConn
$PowerShellExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $PowerShellExe) {
    $PowerShellExe = "powershell.exe"
}

Set-Location -LiteralPath $Root

Write-Host "Starting PostgreSQL..."
docker compose up -d postgres

Write-Host "Waiting for PostgreSQL..."
for ($i = 0; $i -lt 30; $i++) {
    $status = docker inspect -f "{{.State.Health.Status}}" qts-postgres 2>$null
    if ($status -eq "healthy") {
        Write-Host "PostgreSQL is healthy."
        break
    }
    Start-Sleep -Seconds 1
}

Write-Host "Starting backend..."
$BackendCommand = "Set-Location -LiteralPath $RootLiteral; `$env:POSTGRES_CONNECTION_STRING = $PgConnLiteral; python -m uvicorn trader.api.main:app --host 127.0.0.1 --port 8080 --reload"
Start-Process -FilePath $PowerShellExe -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command",
    $BackendCommand
)

Write-Host "Starting frontend..."
$FrontendCommand = "Set-Location -LiteralPath $FrontendLiteral; if (-not (Test-Path -LiteralPath '.\node_modules\vite\bin\vite.js')) { Write-Error 'Frontend dependencies missing. Run npm install in Frontend first.'; exit 1 }; node .\node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173"
Start-Process -FilePath $PowerShellExe -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command",
    $FrontendCommand
)

Write-Host ""
Write-Host "PG:       localhost:5432"
Write-Host "Backend:  http://localhost:8080/docs"
Write-Host "Frontend: http://localhost:5173"
