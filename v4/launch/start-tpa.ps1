# TPA HQ 4.0.11 + Road Traffic, Bus, Aircraft and Train Engines - PC test launcher.
# Started by START-TPA.bat. Runs on this PC only; nothing is put online.
#
# Each key or login (TomTom, BODS, Network Rail email + password):
#  - is typed or pasted here, hidden like a password;
#  - is given ONLY to its own engine program (never to HQ, the other engine, files or logs);
#  - can optionally be remembered for next time, encrypted by Windows so only this
#    Windows user on this PC can unlock it. FORGET-SAVED-KEY.bat removes saved keys.
# Works with Windows PowerShell 5.1 (built into Windows) and PowerShell 7. ASCII only.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$keyDir = [IO.Path]::Combine($(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $HOME }), 'TPA', 'keys')
$hqPort = 8778
$roadPort = 8782
$busPort = 8781
$airPort = 8783
$railPort = 8784

function Say([string]$text, [string]$colour = 'Gray') { Write-Host $text -ForegroundColor $colour }

function Find-Python {
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        if (Get-Command $candidate[0] -ErrorAction SilentlyContinue) { return ,$candidate }
    }
    return $null
}

function SecureToPlain([Security.SecureString]$secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Get-Key([string]$label, [string]$fileName, [string]$testVar, [string]$what = 'key') {
    # Returns the key/email/password as plain text for one engine, or '' to run that layer without it.
    # $what is the word shown to the owner: 'key', 'login email' or 'password'.
    $keyFile = Join-Path $keyDir $fileName
    $secure = $null
    $testValue = [Environment]::GetEnvironmentVariable($testVar)
    if ($testValue) {
        # Automated tests only (a test robot cannot type into the hidden prompt).
        $secure = ConvertTo-SecureString $testValue -AsPlainText -Force
        [Environment]::SetEnvironmentVariable($testVar, $null)
    }
    elseif ($env:TPA_LAUNCHER_TEST_NOKEY) { return '' }
    elseif (Test-Path -LiteralPath $keyFile) {
        $answer = Read-Host "Your $label $what is saved on this PC. Use it? Press Enter for YES, or type N then Enter"
        if ($answer -notmatch '^\s*[nN]') {
            try { $secure = Import-Clixml -LiteralPath $keyFile }
            catch { Say "The saved $label $what could not be opened. Please type or paste it again." Yellow; $secure = $null }
        }
    }
    if (-not $secure) {
        Say ''
        Say "Type or paste your $label $what below, then press Enter." White
        Say '(It is hidden like a password: you will see stars, or nothing at all. Both are normal.)' DarkGray
        Say "(Right-click pastes in this window. To skip $label, just press Enter.)" DarkGray
        $secure = Read-Host "$label $what" -AsSecureString
        if ($secure -and $secure.Length -gt 0) {
            $remember = Read-Host "Remember this $label $what on this PC? Type Y then Enter for yes, or just press Enter for no"
            if ($remember -match '^\s*[yY]') {
                New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
                $secure | Export-Clixml -LiteralPath $keyFile
                Say "Saved, encrypted for your Windows login only." Green
            }
        }
    }
    if ($secure -and $secure.Length -gt 0) {
        $plain = (SecureToPlain $secure).Trim()
        if ($plain) { Say "$label $what received (hidden)." Green; return $plain }
    }
    Say "No $label $what - that layer will say it is not set up." Yellow
    return ''
}

function Start-Engine([hashtable]$secrets, [string]$portVar, [int]$port, [string[]]$scriptPath) {
    # Each secret is put into this window's environment only for the instant its own
    # engine starts (the engine inherits it), then removed before anything else starts.
    [Environment]::SetEnvironmentVariable($portVar, "$port")
    foreach ($k in $secrets.Keys) { [Environment]::SetEnvironmentVariable($k, [string]$secrets[$k]) }
    $args2 = @()
    if ($python.Count -gt 1) { $args2 += $python[1..($python.Count - 1)] }
    # Quoted so folders with spaces (e.g. 'TPA MASTER') work in Windows PowerShell 5.1.
    $args2 += '"' + [IO.Path]::Combine([string[]](@($root) + $scriptPath)) + '"'
    try { return Start-Process -FilePath $python[0] -ArgumentList $args2 -WorkingDirectory $root -PassThru }
    finally { foreach ($k in $secrets.Keys) { [Environment]::SetEnvironmentVariable($k, $null) } }
}

Clear-Host
Say '==============================================================' Cyan
Say '  TPA HQ 4.0.11 + ROAD + BUSES + AIRCRAFT + TRAINS 0.2 - test on this PC' Cyan
Say '==============================================================' Cyan

$python = Find-Python
if (-not $python) {
    Say 'PROBLEM: Python is not installed on this PC, so TPA cannot start.' Red
    Say 'Please tell Claude you saw this message.' Red
    exit 1
}
# Never pass on any key that might already be in this window's environment.
foreach ($v in 'TOMTOM_API_KEY', 'TPA_BODS_API_KEY', 'TPA_ADSBLOL_API_KEY', 'TPA_NR_USERNAME', 'TPA_NR_PASSWORD') { [Environment]::SetEnvironmentVariable($v, $null) }

$tomtom = Get-Key 'TomTom' 'tomtom-key.xml' 'TPA_LAUNCHER_TEST_TOMTOM'
$bods = Get-Key 'BODS (buses)' 'bods-key.xml' 'TPA_LAUNCHER_TEST_BODS'
$nrUser = Get-Key 'Network Rail' 'nr-email.xml' 'TPA_LAUNCHER_TEST_NRUSER' 'login email'
$nrPass = Get-Key 'Network Rail' 'nr-password.xml' 'TPA_LAUNCHER_TEST_NRPASS' 'password'
Say ''
Say 'Starting the road traffic, bus, aircraft and train engines (four extra windows)...' Cyan
$roadProc = Start-Engine @{ 'TOMTOM_API_KEY' = $tomtom } 'TPA_ROAD_ENGINE_PORT' $roadPort @('v4', 'engines', 'road', 'road_engine.py')
$tomtom = $null
$busProc = Start-Engine @{ 'TPA_BODS_API_KEY' = $bods } 'TPA_BUS_ENGINE_PORT' $busPort @('v4', 'engines', 'bus', 'bus_engine.py')
$bods = $null
# Aircraft (ADSB.lol) needs no key today.
$airProc = Start-Engine @{} 'TPA_AIR_ENGINE_PORT' $airPort @('v4', 'engines', 'air', 'air_engine.py')
$railProc = Start-Engine @{ 'TPA_NR_USERNAME' = $nrUser; 'TPA_NR_PASSWORD' = $nrPass } 'TPA_RAIL_ENGINE_PORT' $railPort @('v4', 'engines', 'rail', 'rail_engine.py')
$nrUser = $null
$nrPass = $null

# ---- Start HQ in this window (it never receives any key) ----------------------
$env:TPA_HQ_PORT = "$hqPort"
$env:TPA_HQ_LAN_TEST = '0'
$env:TPA_HQ_PARKED = 'sea'
$env:TPA_HQ_ENGINES = "road=http://127.0.0.1:$roadPort,bus=http://127.0.0.1:$busPort,air=http://127.0.0.1:$airPort,rail=http://127.0.0.1:$railPort"
$env:TPA_HQ_OPEN_BROWSER = '1'
Remove-Item Env:\TPA_HQ_FIXTURE_DIR -ErrorAction SilentlyContinue
Say "Opening the map in your browser:  http://127.0.0.1:$hqPort/" Cyan
Say 'KEEP THIS WINDOW OPEN while you look. Close it when you are finished.' Cyan
Say ''
$hqArgs = @()
if ($python.Count -gt 1) { $hqArgs += $python[1..($python.Count - 1)] }
$hqArgs += [IO.Path]::Combine($root, 'v4', 'hq', 'serve_hq.py')
try {
    & $python[0] @hqArgs
}
finally {
    # Closing TPA also closes all four engines.
    foreach ($p in @($roadProc, $busProc, $airProc, $railProc)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -ErrorAction SilentlyContinue }
    }
}
