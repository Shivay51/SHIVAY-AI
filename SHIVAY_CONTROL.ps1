param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Restart", "Status", "Logs", "Run")]
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

function Resolve-BasePython {
    # Prefer the Windows "py" launcher (most reliable), then fall back to python.exe.
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3 -c "import sys; assert sys.version_info[:2] >= (3, 10)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $pyLauncher.Source; Args = @("-3") }
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        & $python.Source -c "import sys; assert sys.version_info[:2] >= (3, 10)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $python.Source; Args = @() }
        }
    }
    throw "Python 3.10+ was not found. Install 64-bit Python 3.11+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run."
}

function Get-VenvPython {
    # Returns the path to a self-contained virtual environment's python.exe,
    # creating the venv and installing dependencies on first run (or whenever
    # requirements.txt changes). Never touches the system Python site-packages.
    $venvDirectory = Join-Path $projectRoot ".venv"
    $venvPython = Join-Path $venvDirectory "Scripts\python.exe"
    $stampFile = Join-Path $venvDirectory ".deps_installed"
    $requirements = Join-Path $projectRoot "requirements.txt"

    if (-not (Test-Path -LiteralPath $requirements)) {
        throw "requirements.txt was not found next to this script."
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        $base = Resolve-BasePython
        Write-Host "Creating self-contained environment (.venv) - first run only, this can take a minute..."
        & $base.Exe @($base.Args + @("-m", "venv", $venvDirectory))
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw "Failed to create the virtual environment (.venv)."
        }
    }

    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installedHash = ""
    if (Test-Path -LiteralPath $stampFile) {
        $stampContent = Get-Content -LiteralPath $stampFile -Raw -ErrorAction SilentlyContinue
        $installedHash = if ($null -eq $stampContent) { "" } else { ([string]$stampContent).Trim() }
    }

    if ($installedHash -ne $requirementsHash) {
        Write-Host "Installing dependencies into .venv (first run or requirements.txt changed)..."
        & $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed. Try running: .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        }
        Set-Content -LiteralPath $stampFile -Value $requirementsHash -Encoding ascii
    }

    return $venvPython
}

function Test-Prerequisites {
    if (-not (Test-Path -LiteralPath $botFile)) {
        throw "bot.py was not found next to this script."
    }
    $envFile = Join-Path $projectRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "No .env file was found. Copy .env.example to .env and fill in your credentials."
    }
    return Get-VenvPython
}

function Stop-Shivay {
    $running = Get-ShivayProcess
    if ($null -eq $running) {
        Remove-StaleLock
        Write-Host "SHIVAY AI is not running."
        return
    }
    # Targeted by PID only: never terminates unrelated Python processes.
    Stop-Process -Id $running.ProcessId
    Wait-Process -Id $running.ProcessId -Timeout 20 -ErrorAction SilentlyContinue
    Remove-StaleLock
    Write-Host "SHIVAY AI stopped (PID $($running.ProcessId))."
}

function Start-Shivay {
    $running = Get-ShivayProcess
    if ($null -ne $running) {
        Write-Host "SHIVAY AI is already running (PID $($running.ProcessId))."
        return
    }
    Remove-StaleLock
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $python = Test-Prerequisites
    $started = Start-Process -FilePath $python -ArgumentList @($botFile) -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    Start-Sleep -Seconds 3
    if ($started.HasExited) {
        throw "SHIVAY AI failed to start. Check logs\bot_stderr.log."
    }
    Write-Host "SHIVAY AI started (PID $($started.Id))."
}

function Invoke-ShivayForeground {
    $running = Get-ShivayProcess
    if ($null -ne $running) {
        Write-Host "SHIVAY AI is already running (PID $($running.ProcessId)). Use STOP_SHIVAY.bat first." -ForegroundColor Yellow
        return
    }
    Remove-StaleLock
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $python = Test-Prerequisites
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Starting SHIVAY AI - watch the startup summary below." -ForegroundColor Cyan
    Write-Host "  Press Ctrl+C in this window to stop the bot." -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    & $python $botFile
}

switch ($Action) {
    "Run" {
        Invoke-ShivayForeground
        exit 0
    }
    "Status" {
        $running = Get-ShivayProcess
        if ($null -ne $running) { Write-Host "SHIVAY AI RUNNING (PID $($running.ProcessId))"; exit 0 }
        Remove-StaleLock
        Write-Host "SHIVAY AI STOPPED"
        exit 1
    }
    "Stop" {
        Stop-Shivay
        exit 0
    }
    "Logs" {
        if (-not (Test-Path -LiteralPath $stdoutFile)) {
            Write-Host "No log file yet at logs\bot_stdout.log."
            exit 1
        }
        Get-Content -LiteralPath $stdoutFile -Tail 60
        if (Test-Path -LiteralPath $stderrFile) {
            Write-Host ""
            Write-Host "--- errors ---"
            Get-Content -LiteralPath $stderrFile -Tail 30
        }
        exit 0
    }
    "Restart" {
        Stop-Shivay
        Start-Shivay
    }
    "Start" { Start-Shivay }
}
