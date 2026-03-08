param(
    [Parameter(Mandatory = $true)]
    [string]$Message
)

$ErrorActionPreference = "Stop"

$staged = @(git diff --cached --name-only --diff-filter=ACMR)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read staged file list."
}

$files = @($staged | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($files.Count -eq 0) {
    throw "No staged changes found. Run git add before committing."
}

$blocked = @(
    "mcp_mission_control.json"
)

$blockedMatches = @(
    $files | Where-Object {
        $_ -in $blocked -or $_ -like "*.lock"
    }
)

if ($blockedMatches.Count -gt 0) {
    Write-Error "Detected blocked runtime files in staged changes:"
    $blockedMatches | ForEach-Object { Write-Error " - $_" }
    Write-Error "Run git restore --staged <file> and try again."
    exit 1
}

Write-Host "About to commit these files:"
$files | ForEach-Object { Write-Host " - $_" }

git commit -m $Message
exit $LASTEXITCODE
