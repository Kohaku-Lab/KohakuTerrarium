# KohakuTerrarium installer for Windows PowerShell.
#
#   irm https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/manifests-stable/install.ps1 | iex
#
# Installs, for this user only (no admin rights):
#   %USERPROFILE%\.kohakuterrarium\runtime\terminal\   a Python of its own and the launcher
#   %USERPROFILE%\.kohakuterrarium\runtime\versions\   the release, shared with the desktop app
#   %LOCALAPPDATA%\KohakuTerrarium\bin\                kt, kt-ui, kt-cli, kt-tui (put on PATH)
#
# Options as environment variables (irm | iex takes no arguments):
#   KT_CHANNEL        stable (default), beta or nightly
#   KT_INSTALL_BASE   where kt-runtime-win-x64.zip lives (default: the channel's release)
#   KT_FEED_URL       a custom release feed (a URL holding <channel>.json)
#   KT_CONFIG_DIR     another home than %USERPROFILE%\.kohakuterrarium
#   KT_NO_MODIFY_PATH set to leave the user PATH alone
# Update later with `kt self-update`; remove with `kt self-uninstall`.

function Install-KohakuTerrarium {
    # in the function: `irm | iex` runs in the caller's session, which keeps its own settings
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is far slower with its bar

    $repo = "Kohaku-Lab/KohakuTerrarium"
    $channel = if ($env:KT_CHANNEL) { $env:KT_CHANNEL } else { "stable" }
    if ($channel -notin @("stable", "beta", "nightly")) {
        throw "KT_CHANNEL must be stable, beta or nightly (got '$channel')"
    }
    $base = if ($env:KT_INSTALL_BASE) { $env:KT_INSTALL_BASE.TrimEnd("/") } else {
        "https://github.com/$repo/releases/download/manifests-$channel"
    }

    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if ($arch -ne "AMD64") {
        throw "KohakuTerrarium ships for 64-bit x86 Windows only (this is $arch)."
    }
    $platform = "win-x64"

    $home_dir = if ($env:KT_CONFIG_DIR) { $env:KT_CONFIG_DIR } else {
        Join-Path $env:USERPROFILE ".kohakuterrarium"
    }
    $runtime = Join-Path $home_dir "runtime"
    $terminal = Join-Path $runtime "terminal"
    $name = "kt-runtime-$platform.zip"
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null

    # downloaded and unpacked beside the runtime (same drive), swapped in once complete
    $staging = Join-Path $runtime "terminal.new"
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        Write-Host "Downloading the KohakuTerrarium runtime ($channel, $platform)..."
        $archive = Join-Path $staging $name
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $archive
        $sums = (Invoke-WebRequest -UseBasicParsing -Uri "$base/$name.sha256").Content
        if ($sums -is [byte[]]) { $sums = [System.Text.Encoding]::UTF8.GetString($sums) }
        $wanted = ($sums.Trim() -split "\s+")[0].ToLower()
        # .NET directly: Get-FileHash is missing where PSModulePath points elsewhere
        $stream = [System.IO.File]::OpenRead($archive)
        try {
            $bytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash($stream)
        } finally { $stream.Dispose() }
        $actual = -join ($bytes | ForEach-Object { $_.ToString("x2") })
        if ($actual -ne $wanted) {
            throw "checksum mismatch for $name (expected $wanted, got $actual)"
        }
        Expand-Archive -Path $archive -DestinationPath $staging
        $fresh = Join-Path $staging "kt-runtime"

        $old = Join-Path $runtime "terminal.old"
        if (Test-Path $old) { Remove-Item -Recurse -Force $old }
        $had = Test-Path $terminal
        if ($had) { Rename-Item $terminal "terminal.old" }
        try {
            Move-Item $fresh $terminal
        } catch {
            if ($had) { Rename-Item $old "terminal" }  # the previous runtime stays
            throw
        }
        if ($had) { Remove-Item -Recurse -Force $old -ErrorAction SilentlyContinue }
    } finally {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    }

    $python = Join-Path $terminal "python\python.exe"
    $bootstrap = Join-Path $terminal "bootstrap"
    $names = @("PYTHONPATH", "PYTHONNOUSERSITE", "KT_PYTHON", "KT_BOOTSTRAP_DIR", "KT_LAUNCHER_TERMINAL")
    $saved = @{}
    foreach ($n in $names) { $saved[$n] = [Environment]::GetEnvironmentVariable($n, "Process") }
    try {
        $env:PYTHONPATH = $bootstrap
        $env:PYTHONNOUSERSITE = "1"
        $env:KT_PYTHON = $python
        $env:KT_BOOTSTRAP_DIR = $bootstrap
        $env:KT_LAUNCHER_TERMINAL = "1"

        Write-Host "Installing the newest $channel release..."
        $install = @("-m", "kohakuterrarium.launcher", "--install", "--channel", $channel)
        if ($env:KT_FEED_URL) { $install += @("--feed-url", $env:KT_FEED_URL) }
        & $python @install
        if ($LASTEXITCODE -ne 0) { throw "the release did not install (see above)" }

        $bin = Join-Path $env:LOCALAPPDATA "KohakuTerrarium\bin"
        if ($env:KT_NO_MODIFY_PATH) {
            Write-Host "PATH left as it is (KT_NO_MODIFY_PATH): add $bin to use the commands."
        } else {
            Add-UserPath $bin
        }
        if (($env:Path -split ";") -notcontains $bin) { $env:Path = "$env:Path;$bin" }

        & $python -m kohakuterrarium.launcher shims install | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "the kt commands were not created" }
    } finally {
        foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $saved[$n], "Process") }
    }

    Write-Host ""
    Write-Host "KohakuTerrarium is installed. Start with:  kt-ui"
    Write-Host "(open a new terminal if 'kt-ui' is not found yet)"
}

function Add-UserPath([string] $dir) {
    # the raw value: entries like %USERPROFILE%\x stay as written, the value stays expandable
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
    try {
        $raw = [string] $key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $entries = @($raw -split ";" | Where-Object { $_ })
        if ($entries -contains $dir) { return }
        $key.SetValue("Path", (($entries + $dir) -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    } finally { $key.Close() }
    # new terminals see it: setting a user variable through .NET tells Windows it changed
    [Environment]::SetEnvironmentVariable("KT_INSTALL_PATH_CHANGED", "1", "User")
    [Environment]::SetEnvironmentVariable("KT_INSTALL_PATH_CHANGED", $null, "User")
    Write-Host "Added $dir to your PATH."
}

Install-KohakuTerrarium
