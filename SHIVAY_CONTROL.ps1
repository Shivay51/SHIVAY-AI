<#
    SHIVAY AI PRO — one-click Windows control script.

    Actions:
      Start    validate settings, then start the bot if it is not already running
      Stop     stop only the verified SHIVAY bot process
      Restart  stop the verified process, then start it again
      Status   report process state and settings health (no values printed)
      Update   pull the latest code, preserve local changes, reinstall, test, restart
      Check    settings + safety pre-flight only

    Never prints the value of any credential. Never places a trade.
    Rollback: see ROLLBACK section printed by Update, or run
      git -C <project> reset --hard <previous-sha>  then  RESTART_SHIVAY.bat
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Restart", "Status", "Update", "Check")]
    [string]$Action,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$botFile = Join-Path $projectRoot "bot.py"
$lockFile = Join-Path $projectRoot ".shivay_ai.lock"
$logDirectory = Join-Path $projectRoot "logs"
$stdoutFile = Join-Path $logDirectory "bot_stdout.log"
$stderrFile = Join-Path $logDirectory "bot_stderr.log"
$stateFile = Join-Path $projectRoot ".shivay_last_good_sha"

function Get-Python {
    $candidate = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return (Get-Command python.exe -ErrorAction Stop).Source
}

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

function Invoke-RuntimeCheck {
    param([switch]$Health)
    $python = Get-Python
    $arguments = @((Join-Path $projectRoot "runtime_check.py"))
    if ($Health) { $arguments += "--health" }
    & $python @arguments
    return $LASTEXITCODE
}

function Stop-Shivay {
    $running = Get-ShivayProcess
    if ($null -eq $running) {
        Remove-StaleLock
        Write-Host "SHIVAY AI was not running."
        return
    }
    Write-Host "Stopping SHIVAY AI (PID $($running.ProcessId))."
    Stop-Process -Id $running.ProcessId
    Wait-Process -Id $running.ProcessId -Timeout 20 -ErrorAction SilentlyContinue
    Remove-StaleLock
    Write-Host "SHIVAY AI stopped."
}

function Start-Shivay {
    $running = Get-ShivayProcess
    if ($null -ne $running) {
        Write-Host "SHIVAY AI is already running (PID $($running.ProcessId)). Nothing to do."
        return
    }
    if ((Invoke-RuntimeCheck) -ne 0) {
        throw "Settings are incomplete or unsafe. SHIVAY AI was not started. Fix the values listed as MISSING in your .env file and run START_SHIVAY.bat again."
    }
    Remove-StaleLock
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $python = Get-Python
    $started = Start-Process -FilePath $python -ArgumentList @($botFile) -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    Start-Sleep -Seconds 4
    if ($started.HasExited) {
        throw "SHIVAY AI failed to start. Check logs\bot_stderr.log."
    }
    Write-Host "SHIVAY AI started (PID $($started.Id))."
}

function Update-Shivay {
    Push-Location $projectRoot
    try {
        $git = (Get-Command git.exe -ErrorAction Stop).Source
        $currentSha = (& $git rev-parse HEAD).Trim()
        Set-Content -LiteralPath $stateFile -Value $currentSha -Encoding ascii
        Write-Host "Current commit: $currentSha (saved for rollback)."

        $dirty = @(& $git status --porcelain)
        $stashed = $false
        if ($dirty.Count -gt 0) {
            Write-Host "Local changes found — preserving them with git stash."
            & $git stash push --include-untracked -m "SHIVAY_CONTROL auto-stash" | Out-Null
            $stashed = $true
        }

        $branch = (& $git rev-parse --abbrev-ref HEAD).Trim()
        Write-Host "Pulling latest code for branch $branch."
        & $git pull --ff-only origin $branch
        if ($LASTEXITCODE -ne 0) { throw "git pull failed. Nothing was changed." }

        if ($stashed) {
            Write-Host "Restoring your local changes."
            & $git stash pop
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Your local changes are still safely stored. Run: git stash list / git stash pop"
            }
        }

        $python = Get-Python
        if (Test-Path -LiteralPath (Join-Path $projectRoot "requirements.txt")) {
            Write-Host "Installing dependencies."
            & $python -m pip install -q -r requirements.txt
        }

        if (-not $SkipTests) {
            Write-Host "Running the test suite."
            & $python -m pytest tests -q
            if ($LASTEXITCODE -ne 0) {
                Write-Host ""
                Write-Host "ROLLBACK: tests failed, so SHIVAY AI was NOT restarted."
                Write-Host "  git -C `"$projectRoot`" reset --hard $currentSha"
                Write-Host "  RESTART_SHIVAY.bat"
                throw "Tests failed after update."
            }
        }

        if ((Invoke-RuntimeCheck) -ne 0) {
            Write-Host "ROLLBACK: git -C `"$projectRoot`" reset --hard $currentSha  then RESTART_SHIVAY.bat"
            throw "Settings are incomplete or unsafe after update. SHIVAY AI was not restarted."
        }

        Stop-Shivay
        Start-Shivay
        $newSha = (& $git rev-parse HEAD).Trim()
        Write-Host ""
        Write-Host "UPDATE COMPLETE — $currentSha -> $newSha"
        Write-Host "ROLLBACK if needed:"
        Write-Host "  git -C `"$projectRoot`" reset --hard $currentSha"
        Write-Host "  RESTART_SHIVAY.bat"
    }
    finally {
        Pop-Location
    }
}

switch ($Action) {
    "Check" { exit (Invoke-RuntimeCheck -Health) }
    "Status" {
        $running = Get-ShivayProcess
        if ($null -ne $running) {
            Write-Host "SHIVAY AI RUNNING (PID $($running.ProcessId))"
            Invoke-RuntimeCheck -Health | Out-Null
            exit 0
        }
        Remove-StaleLock
        Write-Host "SHIVAY AI STOPPED"
        Invoke-RuntimeCheck -Health | Out-Null
        exit 1
    }
    "Stop" { Stop-Shivay }
    "Restart" { Stop-Shivay; Start-Shivay }
    "Start" { Start-Shivay }
    "Update" { Update-Shivay }
}
