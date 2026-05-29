param(
    [string]$ProxySubUrl = "",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required. Please install Docker Desktop and make sure Docker Compose is available."
    }
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $line = "$Key=$Value"
    if (-not (Test-Path $Path)) {
        Set-Content -Path $Path -Value $line -Encoding utf8
        return
    }
    $content = Get-Content -Path $Path -ErrorAction SilentlyContinue
    $found = $false
    $escapedKey = [regex]::Escape($Key)
    $next = foreach ($item in $content) {
        if ($item -match "^$escapedKey=") {
            $found = $true
            $line
        } else {
            $item
        }
    }
    if (-not $found) {
        $next = @($next) + $line
    }
    Set-Content -Path $Path -Value $next -Encoding utf8
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$DefaultValue
    )
    if (Test-Path $Path) {
        $escapedKey = [regex]::Escape($Key)
        $match = Get-Content -Path $Path | Where-Object { $_ -match "^$escapedKey=" } | Select-Object -First 1
        if ($match) {
            return ($match -replace "^$escapedKey=", "")
        }
    }
    return $DefaultValue
}

function Set-ProxyConfigLine {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $line = "${Key}: $Value"
    $content = Get-Content -Path $Path -ErrorAction SilentlyContinue
    $escapedKey = [regex]::Escape($Key)
    $found = $false
    $next = foreach ($item in $content) {
        if ($item -match "^${escapedKey}:") {
            $found = $true
            $line
        } else {
            $item
        }
    }
    if (-not $found) {
        $next = @($next) + $line
    }
    Set-Content -Path $Path -Value $next -Encoding utf8
}

function Refresh-ProxyConfig {
    param([string]$Url)
    if (-not $Url) {
        return
    }
    $userAgent = Get-EnvValue -Path ".env" -Key "PROXY_USER_AGENT" -DefaultValue "clash-verge/1.7.7"
    Invoke-WebRequest -Uri $Url -Headers @{ "User-Agent" = $userAgent } -OutFile "proxy/config.yaml"
    Set-ProxyConfigLine -Path "proxy/config.yaml" -Key "mixed-port" -Value "7890"
    Set-ProxyConfigLine -Path "proxy/config.yaml" -Key "allow-lan" -Value "true"
    Set-ProxyConfigLine -Path "proxy/config.yaml" -Key "bind-address" -Value "'*'"
    Set-ProxyConfigLine -Path "proxy/config.yaml" -Key "external-controller" -Value "'0.0.0.0:9090'"
}

Require-Command docker
docker compose version | Out-Null

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if ($ProxySubUrl) {
    Set-EnvValue -Path ".env" -Key "PROXY_SUB_URL" -Value $ProxySubUrl
}

New-Item -ItemType Directory -Force -Path "proxy", "state/cron", "state/login-profile", "DouYinSparkFlow/logs" | Out-Null
if (-not (Test-Path "proxy/config.yaml")) {
    Copy-Item "proxy/config.example.yaml" "proxy/config.yaml"
}

Refresh-ProxyConfig -Url $ProxySubUrl

docker compose up -d --build

$webPort = Get-EnvValue -Path ".env" -Key "WEB_PORT" -DefaultValue "8787"
$url = "http://localhost:$webPort"
Write-Host "Douyin SparkFlow is running: $url"
Write-Host "Next: create the admin password, open the login desktop, scan the QR code, select target friends, and set the send window."
if (-not $NoOpen) {
    Start-Process $url
}
