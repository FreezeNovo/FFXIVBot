#Requires -Version 5.1
[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$setupLock = $null
$runtimeGuard = $null

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $([IO.Path]::GetFileName($Program)). Installation/startup stopped."
    }
}

function Test-WorkspaceWrite {
    $probe = Join-Path $workspaceRoot ('.write-check-' + [guid]::NewGuid().ToString('N'))
    $handle = [IO.File]::Open($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    $handle.Dispose()
    Remove-Item -LiteralPath $probe -Force
}

function Find-ManagedPython {
    param([string]$InstallDirectory)
    if (-not (Test-Path -LiteralPath $InstallDirectory)) { return $null }
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
    $pattern = '^cpython-(3\.14\.\d+)-windows-' + $architecture + '-none$'
    # Never use uv's cpython-3.14 Junction: LTSC can reject it with os448.
    $candidates = Get-ChildItem -LiteralPath $InstallDirectory -Directory | Where-Object {
        $_.Name -match $pattern -and ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
    } | Sort-Object { [version]([regex]::Match($_.Name, $pattern).Groups[1].Value) } -Descending
    foreach ($candidate in $candidates) {
        $executable = Join-Path $candidate.FullName 'python.exe'
        if (-not (Test-Path -LiteralPath $executable)) { continue }
        try {
            & $executable -c 'import sys; raise SystemExit(sys.version_info[:2] != (3,14))' 2>$null
            if ($LASTEXITCODE -eq 0) { return $executable }
        }
        catch { continue }
    }
    return $null
}

function Enable-WorkspaceWrite {
    try { Test-WorkspaceWrite; return }
    catch [System.UnauthorizedAccessException] {
        Write-Host "Current user cannot write to: $workspaceRoot"
        Write-Host 'Move the extracted folder to a private writable location, or allow UAC to grant this user Modify permission on THIS folder.'
        if ((Read-Host 'Type UAC to repair this folder permission; anything else cancels') -cne 'UAC') {
            throw 'No permission changes made. Move the folder and retry.'
        }
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        # Elevate only this fixed ACL operation, never the installer or the bot.
        # Encode the command so spaces, apostrophes and Unicode in the path are safe.
        $literalRoot = $workspaceRoot.Replace("'", "''")
        $aclCommand = "& `$env:SystemRoot\System32\icacls.exe '$literalRoot' /grant '*${sid}:(OI)(CI)M'; exit `$LASTEXITCODE"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($aclCommand))
        $repair = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-EncodedCommand', $encoded)
        if ($repair.ExitCode -ne 0) { throw 'UAC permission repair failed. Move the folder to a writable location.' }
        Test-WorkspaceWrite
    }
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'This launcher requires Windows.' }
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        throw 'Use 64-bit Windows and 64-bit PowerShell.'
    }
    Set-Location -LiteralPath $workspaceRoot
    foreach ($required in @('pyproject.toml', 'uv.lock', 'ff14_app\main.py', '.env.example', 'tests')) {
        if (-not (Test-Path -LiteralPath (Join-Path $workspaceRoot $required))) { throw "Incomplete ZIP extraction: $required" }
    }
    Enable-WorkspaceWrite
    # Serialize installers before touching .venv; a running bot is protected by the
    # same Windows byte-range lock used by RuntimeLock in Python (msvcrt).
    $setupLock = [IO.File]::Open((Join-Path $workspaceRoot '.windows-setup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    [IO.Directory]::CreateDirectory((Join-Path $workspaceRoot 'data')) | Out-Null
    $runtimeGuard = [IO.File]::Open((Join-Path $workspaceRoot 'data\runtime.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    if ($runtimeGuard.Length -eq 0) { $runtimeGuard.WriteByte(48); $runtimeGuard.Flush() }
    try { $runtimeGuard.Lock(0, 1) }
    catch { throw 'FFXIVBot is already running in this folder. Stop it before installing dependencies.' }

    Write-Host ('Running as: ' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
    Write-Host 'Checking/installing current-user dependencies. First run requires Internet access.'
    # Own pinned uv, not an unrelated uv on PATH. No system PATH/registry changes.
    $uvHome = Join-Path $env:LOCALAPPDATA 'FFXIVBot\tools\uv-0.12.4'
    $uv = Join-Path $uvHome 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $installer = Join-Path ([IO.Path]::GetTempPath()) ('ffxivbot-uv-' + [guid]::NewGuid().ToString('N') + '.ps1')
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.12.4/install.ps1' -OutFile $installer -TimeoutSec 90
            $env:UV_UNMANAGED_INSTALL = $uvHome
            Invoke-Checked "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer)
        }
        finally {
            Remove-Item Env:\UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
        }
    }
    $uvVersion = & $uv --version
    if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch '^uv 0\.12\.4(?:\s|$)') { throw 'Pinned uv 0.12.4 is missing or invalid.' }

    $env:UV_PROJECT_ENVIRONMENT = Join-Path $workspaceRoot '.venv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $env:LOCALAPPDATA 'FFXIVBot\python'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONPATH = $workspaceRoot
    $managedPython = Find-ManagedPython $env:UV_PYTHON_INSTALL_DIR
    if (-not $managedPython) {
        Invoke-Checked $uv @('python', 'install', '3.14')
        $managedPython = Find-ManagedPython $env:UV_PYTHON_INSTALL_DIR
    }
    if (-not $managedPython) { throw 'No working Python 3.14 version directory found after installation.' }
    $managedFolder = Split-Path -Leaf (Split-Path -Parent $managedPython)
    $managedVersion = [regex]::Match($managedFolder, '^cpython-(3\.14\.\d+)-').Groups[1].Value
    if (-not $managedVersion) { throw 'Could not determine the installed Python patch version.' }
    # uv normalizes even absolute executable paths to its minor-version Junction.
    # An explicit patch-version request keeps pyvenv.cfg on the real directory.
    # Preserve a moved/broken virtual environment instead of deleting user files.
    $python = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath (Join-Path $workspaceRoot '.venv')) {
        $healthy = $false
        if (Test-Path -LiteralPath $python) {
            try {
                & $python -c "import sys; raise SystemExit('.'.join(map(str,sys.version_info[:3])) != sys.argv[1])" $managedVersion 2>$null
                $healthy = ($LASTEXITCODE -eq 0)
            }
            catch { $healthy = $false }
        }
        if (-not $healthy) {
            $oldEnvironment = '.venv.previous-' + [guid]::NewGuid().ToString('N')
            Rename-Item -LiteralPath (Join-Path $workspaceRoot '.venv') -NewName $oldEnvironment
            Write-Host "Previous environment preserved as $oldEnvironment"
        }
    }
    Invoke-Checked $uv @('sync', '--locked', '--python', $managedVersion, '--no-python-downloads', '--link-mode', 'copy')
    Invoke-Checked $uv @('lock', '--check', '--python', $managedVersion, '--offline', '--no-python-downloads')
    Invoke-Checked $uv @('pip', 'check', '--python', $python)
    Invoke-Checked $python @('-c', "import ssl,sqlite3; from zoneinfo import ZoneInfo; print('Runtime OK:', ZoneInfo('Asia/Shanghai'))")
    Invoke-Checked $python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
    if ($CheckOnly) { Write-Host 'Dependency and offline checks passed. Bot NOT started.'; exit 0 }

    # Packaged .env is authoritative: never inherit another bot's connection.
    foreach ($name in @('ONEBOT_V11_WS_URLS', 'ONEBOT_V11_ACCESS_TOKEN', 'DRIVER', 'HOST', 'PORT', 'ENVIRONMENT')) {
        Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
    }
    $env:DRIVER = '~fastapi+~websockets'
    $env:HOST = '127.0.0.1'
    $env:ENVIRONMENT = 'prod'
    $envFile = Join-Path $workspaceRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        $template = [IO.File]::ReadAllText((Join-Path $workspaceRoot '.env.example'))
        $template = $template -replace '(?m)^ONEBOT_V11_WS_URLS=.*$', 'ONEBOT_V11_WS_URLS=[]'
        $template = $template -replace '(?m)^ONEBOT_V11_ACCESS_TOKEN=.*$', 'ONEBOT_V11_ACCESS_TOKEN='
        [IO.File]::WriteAllText($envFile, $template, (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Created .env with QQ connection disabled. Fill the existing NapCat URL/token, then save and close Notepad.'
        Write-Host 'Keeping [] starts local management and source polling without connecting QQ.'
        Start-Process -FilePath "$env:SystemRoot\System32\notepad.exe" -ArgumentList ('"' + $envFile + '"') -Wait
    }
    # Python reacquires the same lock and retains it through backup, confirmation
    # and the actual gateway lifecycle. A competing runtime wins safely or fails.
    $runtimeGuard.Dispose(); $runtimeGuard = $null
    Write-Host 'Starting deployment preflight. Keep this window open; Ctrl+C stops the bot.'
    Invoke-Checked $python @('-m', 'ff14_app.windows_deploy')
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if ($null -ne $runtimeGuard) { $runtimeGuard.Dispose() }
    if ($null -ne $setupLock) { $setupLock.Dispose() }
}
