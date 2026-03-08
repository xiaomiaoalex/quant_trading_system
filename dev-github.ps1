$proxy = "http://127.0.0.1:4780"
$ghPaths = @(
    "C:\Program Files\GitHub CLI\gh.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe")
)

$env:HTTP_PROXY = $proxy
$env:HTTPS_PROXY = $proxy

$gh = $ghPaths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($gh) {
    $env:GH_EXE = $gh
    Write-Host "GH_EXE=$gh"
} else {
    Write-Host "GH_EXE not found in common install paths."
}

Write-Host "HTTP_PROXY=$($env:HTTP_PROXY)"
Write-Host "HTTPS_PROXY=$($env:HTTPS_PROXY)"
