. "$PSScriptRoot\\dev-env.ps1"

$pytestArgs = @(
    "-m",
    "pytest",
    "-q",
    "trader/tests/test_postgres_storage.py"
)

if ($args.Count -gt 0) {
    $pytestArgs += $args
}

& "$PSScriptRoot\\.venv\\Scripts\\python.exe" @pytestArgs
