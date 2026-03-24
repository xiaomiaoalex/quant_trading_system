param(
    [switch]$EnsureUp,
    [int]$WaitSeconds = 30,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

. "$PSScriptRoot\dev-env.ps1"

$pythonExe = "$PSScriptRoot\.venv\Scripts\python.exe"
$checkScript = "import asyncio; from trader.adapters.persistence.postgres import check_postgres_connection; ok, msg = asyncio.run(check_postgres_connection()); print(msg); raise SystemExit(0 if ok else 1)"

function Test-PostgresReady {
    $output = & $pythonExe -c $checkScript 2>&1
    $exitCode = $LASTEXITCODE
    return @{
        Ready = ($exitCode -eq 0)
        Output = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
    }
}

function Ensure-PostgresContainer {
    Write-Host "PostgreSQL not ready. Starting docker compose service 'postgres'..."
    docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up -d postgres failed."
    }

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = Test-PostgresReady
        if ($status.Ready) {
            Write-Host "PostgreSQL is ready."
            return
        }
        Start-Sleep -Seconds 2
    }

    $finalStatus = Test-PostgresReady
    if ($finalStatus.Output) {
        throw "PostgreSQL did not become ready within $WaitSeconds seconds. Last check: $($finalStatus.Output)"
    }
    throw "PostgreSQL did not become ready within $WaitSeconds seconds."
}

$postgresStatus = Test-PostgresReady

if (-not $postgresStatus.Ready) {
    if (-not $EnsureUp) {
        if ($postgresStatus.Output) {
            Write-Host "PostgreSQL not ready: $($postgresStatus.Output)"
        } else {
            Write-Host "PostgreSQL not ready."
        }
        Write-Host "Run: docker compose up -d postgres"
        Write-Host "Or rerun this script with -EnsureUp to start it automatically."
        exit 1
    }

    Ensure-PostgresContainer
}

$pytestInvocation = @(
    "-m",
    "pytest",
    "-q",
    "trader/tests/test_postgres_storage.py"
)

if ($PytestArgs.Count -gt 0) {
    $pytestInvocation += $PytestArgs
}

& $pythonExe @pytestInvocation
exit $LASTEXITCODE
