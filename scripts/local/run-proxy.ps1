param(
    [string]$ProxyHost = "",
    [string]$ProxyPort = "",
    [string]$LaunchCommand = ""
)

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# Load .env / .env.local
function Import-EnvFile($path) {
    if (!(Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        $line = $_ -replace '#.*', ''
        if ($line -match '^\s*([^=]+)=(.*)$') {
            $k = $Matches[1].Trim()
            $v = $Matches[2].Trim().Trim('"', "'")
            Set-Item -Path "env:$k" -Value $v -ErrorAction SilentlyContinue
        }
    }
}
Import-EnvFile "$RepoRoot\.env"
Import-EnvFile "$RepoRoot\.env.local"

if (-not $ProxyHost) { $ProxyHost = [Environment]::GetEnvironmentVariable('TG_PROXY_HOST') }
if (-not $ProxyPort) { $ProxyPort = [Environment]::GetEnvironmentVariable('TG_PROXY_PORT') }
if (-not $LaunchCommand) { $LaunchCommand = [Environment]::GetEnvironmentVariable('PROXY_LAUNCH_COMMAND') }

if (-not $ProxyHost) { $ProxyHost = '127.0.0.1' }
if (-not $ProxyPort) { $ProxyPort = '7994' }

# 1) Already listening?
$conn = $null
try {
    $conn = New-Object System.Net.Sockets.TcpClient
    $conn.ConnectAsync($ProxyHost, [int]$ProxyPort).Wait(1000)
    if ($conn.Connected) {
        Write-Host "Proxy already running on ${ProxyHost}:${ProxyPort}"
        $conn.Close()
        exit 0
    }
} catch { }
finally { if ($conn) { $conn.Close() } }

# 2) If a launch command was configured
if ($LaunchCommand) {
    Write-Host "Starting proxy via: $LaunchCommand"
    $logPath = "$RepoRoot\.local\runtime-logs\proxy.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $logPath) | Out-Null
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell'
    $psi.Arguments = "-NoProfile -Command `"$LaunchCommand`""
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.WaitForExit(10000)

    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $conn = New-Object System.Net.Sockets.TcpClient
            $conn.ConnectAsync($ProxyHost, [int]$ProxyPort).Wait(1000)
            if ($conn.Connected) {
                Write-Host "Proxy is now listening on ${ProxyHost}:${ProxyPort}"
                $conn.Close()
                exit 0
            }
        } catch { }
        finally { if ($conn) { $conn.Close() } }
    }

    Write-Host "Warning: Proxy launched but port $ProxyPort is not listening."
    exit 1
}

# 3) No proxy configured
Write-Host "Not listening on ${ProxyHost}:${ProxyPort} and no PROXY_LAUNCH_COMMAND set."
exit 1
