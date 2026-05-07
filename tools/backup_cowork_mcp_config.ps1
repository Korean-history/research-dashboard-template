param(
    [string]$SourcePath = "C:\Users\simpl\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json",
    [string]$BackupDir = ".\\backups\\cowork-mcp-config"
)

$ErrorActionPreference = "Stop"

function Write-LogLine {
    param([string]$Message)

    $logPath = Join-Path $BackupDir "backup.log"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($logPath, "$Message`r`n", $utf8NoBom)
}

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stampHuman = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$backupPath = Join-Path $BackupDir "claude_desktop_config_$timestamp.json"
$latestPath = Join-Path $BackupDir "latest.json"

if (-not (Test-Path -LiteralPath $SourcePath)) {
    $message = "[$stampHuman] ERROR source missing: $SourcePath"
    Write-LogLine $message
    Write-Error $message
    exit 1
}

$bytes = [System.IO.File]::ReadAllBytes($SourcePath)
[System.IO.File]::WriteAllBytes($backupPath, $bytes)

$hasUtf8Bom = (
    $bytes.Length -ge 3 -and
    $bytes[0] -eq 0xEF -and
    $bytes[1] -eq 0xBB -and
    $bytes[2] -eq 0xBF
)
$hasUtf16Bom = (
    $bytes.Length -ge 2 -and
    (
        ($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or
        ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF)
    )
)

$jsonValid = $false
try {
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $null = $text | ConvertFrom-Json -ErrorAction Stop
    $jsonValid = $true
}
catch {
    $jsonValid = $false
}

if ($jsonValid -and -not $hasUtf8Bom -and -not $hasUtf16Bom) {
    [System.IO.File]::WriteAllBytes($latestPath, $bytes)
    $message = "[$stampHuman] OK backup=$backupPath bytes=$($bytes.Length) latest=$latestPath"
    Write-LogLine $message
    Write-Host $message
    exit 0
}

$warnings = @()
if (-not $jsonValid) { $warnings += "invalid-json" }
if ($hasUtf8Bom) { $warnings += "utf8-bom" }
if ($hasUtf16Bom) { $warnings += "utf16-bom" }

$message = "[$stampHuman] WARNING backup=$backupPath bytes=$($bytes.Length) latest-not-updated reason=$($warnings -join ',')"
Write-LogLine $message
Write-Warning $message
exit 2
