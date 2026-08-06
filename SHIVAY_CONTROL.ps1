param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Restart", "Status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$botFile = Join-Path $projectRoot "bot.py"
$lockFile = Join-Path $projectRoot ".shivay_ai.lock"
$logDirectory = Join-Path $projectRoot "logs"
$stdoutFile = Join-Path $logDirectory "bot_stdout.log"
$stderrFile = Join-Path $logDirectory "bot_stderr.log"

function Get-ShivayProcess {
    if (Test-Path -LiteralPath $lockFile) {
        $lockContent = Get-Content -LiteralPath $lockFile -Raw -ErrorAction SilentlyContinue
        $rawPid = if ($null -eq $lockContent) { "" } else { ([string]$lockContent).Trim() }
        if ($rawPid -match '^\d+$') {
            $botPid = [int]$rawPid
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $botPid" -ErrorAction SilentlyContinue
            if ($null -ne $process -and $process.Name -match '^python(?:w)?\.exe$' -and
                [string]$process.CommandLine -match '(?i)(^|[\\/\s\"])(bot\.py)([\s\"]|$)') {
                return $process
            }
        }
    }
    $escapedBotFile = [regex]::Escape($botFile)
    $pythonProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue)
    $absoluteMatch = @($pythonProcesses | Where-Object { [string]$_.CommandLine -match $escapedBotFile })
    if ($absoluteMatch.Count -eq 1) { return $absoluteMatch[0] }
    if (Test-Path -LiteralPath $lockFile) {
        $relativeMatch = @($pythonProcesses | Where-Object {
            [string]$_.CommandLine -match '(?i)(^|[\\/\s\"])(bot\.py)([\s\"]|$)'
        })
        if ($relativeMatch.Count -eq 1) { return $relativeMatch[0] }
    }
    return $null
}

function Remove-StaleLock {
    if ((Test-Path -LiteralPath $lockFile) -and $null -eq (Get-ShivayProcess)) {
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                Remove-Item -LiteralPath $lockFile -Force -ErrorAction Stop
                return
            }
            catch [System.IO.IOException] {
                Start-Sleep -Milliseconds 250
            }
        }
        if (Test-Path -LiteralPath $lockFile) {
            throw "The previous SHIVAY AI instance lock was not released safely."
        }
    }
}

function Start-Shivay {
    $running = Get-ShivayProcess
    if ($null -ne $running) {
        Write-Host "SHIVAY AI is already running (PID $($running.ProcessId))."
        return
    }
    Remove-StaleLock
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $started = Start-Process -FilePath $python -ArgumentList @($botFile) -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    Start-Sleep -Seconds 3
    if ($started.HasExited) {
        throw "SHIVAY AI failed to start. Check logs\bot_stderr.log."
    }
    Write-Host "SHIVAY AI started (PID $($started.Id))."
}

switch ($Action) {
    "Status" {
        $running = Get-ShivayProcess
        if ($null -ne $running) { Write-Host "SHIVAY AI RUNNING (PID $($running.ProcessId))"; exit 0 }
        Remove-StaleLock
        Write-Host "SHIVAY AI STOPPED"
        exit 1
    }
    "Restart" {
        $running = Get-ShivayProcess
        if ($null -ne $running) {
            Stop-Process -Id $running.ProcessId
            Wait-Process -Id $running.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        }
        Remove-StaleLock
        Start-Shivay
    }
    "Start" { Start-Shivay }
}
