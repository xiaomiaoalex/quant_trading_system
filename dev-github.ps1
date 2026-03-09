$proxy = "http://127.0.0.1:4780"
$ghPaths = @(
    "C:\Program Files\GitHub CLI\gh.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe")
)

$env:HTTP_PROXY = $proxy
$env:HTTPS_PROXY = $proxy
$windowsOpenSsh = "C:\Windows\System32\OpenSSH\ssh.exe"
$env:GIT_TERMINAL_PROMPT = "0"
$env:GH_PROMPT_DISABLED = "1"

$gh = $ghPaths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($gh) {
    $env:GH_EXE = $gh
    Write-Host "GH_EXE=$gh"
} else {
    Write-Host "GH_EXE not found in common install paths."
}

if (-not $env:GH_TOKEN -and $env:GITHUB_TOKEN) {
    $env:GH_TOKEN = $env:GITHUB_TOKEN
}

if ($env:GH_TOKEN) {
    $env:GITHUB_TOKEN = $env:GH_TOKEN
    Write-Host "GH_AUTH_MODE=token"
    Write-Host "GH_TOKEN=present"
} else {
    Write-Host "GH_AUTH_MODE=keyring"
    Write-Host "GH_TOKEN=missing"
    Write-Host 'Hint: [System.Environment]::SetEnvironmentVariable("GH_TOKEN", "<PAT>", "User")'
}

Write-Host "HTTP_PROXY=$($env:HTTP_PROXY)"
Write-Host "HTTPS_PROXY=$($env:HTTPS_PROXY)"
if (-not $env:GIT_SSH_COMMAND -and (Test-Path $windowsOpenSsh)) {
    $env:GIT_SSH_COMMAND = "`"$windowsOpenSsh`" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
}
Write-Host "GIT_SSH_COMMAND=$($env:GIT_SSH_COMMAND)"
Write-Host "GIT_TERMINAL_PROMPT=$($env:GIT_TERMINAL_PROMPT)"
Write-Host "GH_PROMPT_DISABLED=$($env:GH_PROMPT_DISABLED)"
Write-Host "Recommended sync: git fetch origin main; git merge --ff-only origin/main"
Write-Host "Git remote recommendation: use SSH origin (git@github.com:owner/repo.git)"
