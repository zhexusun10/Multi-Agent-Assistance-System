# Local Windows development only. Portable PostgreSQL, trust auth on loopback ONLY.
# Never expose these ports to a network; production needs real roles and passwords.
#
# Why not data/.runtime? PostgreSQL decodes its own install/data paths as UTF-8, so a
# non-ASCII project path (e.g. a Chinese Windows user directory) breaks initdb; and
# postgres refuses to run from a SUBST drive (startup DLL init failure 0xC0000142).
# So the portable install lives in an ASCII path under ProgramData; this script fully
# owns that folder and stop_postgres.ps1 / deleting the folder removes everything.
[CmdletBinding()]
param(
    [int]$Port = 5432,
    [string]$InstallDir = "C:\ProgramData\PropertyGuruPortablePg",
    [switch]$UseExisting
)
$ErrorActionPreference = "Stop"
$Zip    = "postgresql-16.10-1-windows-x64-binaries.zip"
$Url    = "https://get.enterprisedb.com/postgresql/$Zip"
# Recorded from the publisher's download; detects corruption, not a supply-chain audit.
$Sha256 = "ebb3b6af4fa69dea9951b66855bc4d42dc04e56ccb9aa7024ce3c58bd89d6b0c"
$PgHome = Join-Path $InstallDir "pgsql"
$DataDir = Join-Path $InstallDir "data"
$Log = Join-Path $InstallDir "postgres.log"

function Test-Postgres([int]$ProbePort) {
    $Psql = Join-Path $PgHome "bin\psql.exe"
    if (!(Test-Path $Psql)) { return $false }
    # Windows PowerShell 5.1 turns native stderr into a terminating error under
    # $ErrorActionPreference=Stop; a refused probe just means "not running yet".
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Psql -h 127.0.0.1 -p $ProbePort -U postgres -d postgres -tAc "SELECT 1" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $Previous }
}

try {
    $Python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
    if (!(Test-Path $Python)) { $Python = "py" }
    if ($UseExisting -or (Test-Postgres $Port)) {
        if (!(Test-Postgres $Port)) { throw "-UseExisting: no reachable trust server on 127.0.0.1:$Port." }
        Write-Host "Reusing the reachable PostgreSQL on 127.0.0.1:$Port."
    } else {
        # 5432 occupied by something that is not a trust server: never touch it.
        $Occupied = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port }
        if ($Occupied -and $Port -eq 5432) {
            $Port = 54329
            Write-Warning "Port 5432 is used by another service. Starting portable PostgreSQL on 127.0.0.1:54329 instead."
        }
        New-Item -ItemType Directory -Force $InstallDir | Out-Null
        if (!(Test-Path (Join-Path $PgHome "bin\initdb.exe"))) {
            $Downloads = Join-Path $PSScriptRoot ".runtime\downloads"
            New-Item -ItemType Directory -Force $Downloads | Out-Null
            $ZipPath = Join-Path $Downloads $Zip
            if (!(Test-Path $ZipPath)) {
                Write-Host "Downloading portable PostgreSQL 16.10 (~300 MB, first run only)..."
                & curl.exe -fL --retry 2 --max-time 900 -o "$ZipPath" "$Url"
                if ($LASTEXITCODE -ne 0) { throw "PostgreSQL download failed. Retry, or install PostgreSQL yourself and pass -UseExisting." }
            }
            if ((Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
                throw "Checksum mismatch: $ZipPath. Remove the archive and retry."
            }
            & $Python -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$ZipPath" "$InstallDir"
            if ($LASTEXITCODE -ne 0) { throw "PostgreSQL extraction failed." }
        }
        if (!(Test-Path (Join-Path $DataDir "PG_VERSION"))) {
            & (Join-Path $PgHome "bin\initdb.exe") -D $DataDir -U postgres -A trust -E UTF8 --no-locale
            if ($LASTEXITCODE -ne 0) { throw "initdb failed (a broken VC++ runtime is the usual cause)." }
            Add-Content (Join-Path $DataDir "postgresql.conf") "`n# Local development only`nlisten_addresses = '127.0.0.1'`n"
        }
        if (Test-Path (Join-Path $DataDir "postmaster.pid")) {
            $Previous = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & (Join-Path $PgHome "bin\pg_ctl.exe") -D $DataDir stop -m fast *> $null
            $ErrorActionPreference = $Previous
        }
        # Start detached on its own hidden console: postgres.exe must not inherit
        # this console's handles (it would hold callers' pipes open and die with
        # their session). A single ArgumentList string keeps -o quoting intact.
        $PgCtl = Join-Path $PgHome "bin\pg_ctl.exe"
        $Arguments = "-D `"$DataDir`" -l `"$Log`" -o `"-p $Port`" -w start"
        $Helper = Start-Process -FilePath $PgCtl -ArgumentList $Arguments -WindowStyle Hidden -RedirectStandardOutput (Join-Path $InstallDir "pgctl.out.log") -RedirectStandardError (Join-Path $InstallDir "pgctl.err.log") -PassThru
        if (-not $Helper.WaitForExit(120000)) { throw "pg_ctl start timed out. See $Log" }
        if ($Helper.ExitCode -ne 0) { Write-Warning "pg_ctl exited with code $($Helper.ExitCode); verifying the server directly." }
        for ($i = 0; $i -lt 30; $i++) {
            if (Test-Postgres $Port) { break }
            Start-Sleep -Seconds 1
        }
        if (!(Test-Postgres $Port)) { throw "PostgreSQL did not start. See $Log and $InstallDir\pgctl.err.log" }
        Write-Host "Portable PostgreSQL 16.10 started: 127.0.0.1:$Port (trust, loopback only)."
    }

    $Psql = Join-Path $PgHome "bin\psql.exe"
    function Invoke-Sql($Sql) {
        & $Psql -h 127.0.0.1 -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c $Sql
        if ($LASTEXITCODE -ne 0) { throw "psql failed: $Sql" }
    }
    function Get-Scalar($Sql) {
        $Result = & $Psql -h 127.0.0.1 -p $Port -U postgres -d postgres -tAc $Sql
        if ($null -eq $Result) { return "" }
        return ([string](@($Result) -join "")).Trim()
    }
    foreach ($Role in "propertyguru_scraper", "multi_agent_assistance") {
        $Existing = Get-Scalar "SELECT 1 FROM pg_roles WHERE rolname = '$Role'"
        if ($Existing -ne "1") {
            Invoke-Sql "CREATE ROLE $Role LOGIN"
            Write-Host "Created role $Role (no password; trust auth)."
        }
    }
    $Databases = @{ "propertyguru" = "propertyguru_scraper"; "propertyguru_test" = "propertyguru_scraper";
                    "multi_agent_assistance" = "multi_agent_assistance"; "multi_agent_assistance_test" = "multi_agent_assistance" }
    foreach ($Db in $Databases.Keys) {
        $Existing = Get-Scalar "SELECT 1 FROM pg_database WHERE datname = '$Db'"
        if ($Existing -ne "1") {
            Invoke-Sql "CREATE DATABASE $Db OWNER $($Databases[$Db])"
            Write-Host "Created database $Db."
        }
    }
    if ($Port -ne 5432) {
        # Only rewrite local-template URLs; never touch a user's remote/custom server.
        $EnvFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
        if (Test-Path $EnvFile) {
            $Text = [IO.File]::ReadAllText($EnvFile)
            foreach ($Key in "DATABASE_URL", "TEST_DATABASE_URL", "PROPERTYGURU_DATABASE_URL",
                             "PROPERTYGURU_TEST_DATABASE_URL", "PROPERTYGURU_READ_DATABASE_URL") {
                if ($Text -match "(?m)^[ \t]*$Key[ \t]*=.*127\.0\.0\.1:5432") {
                    $Text = [regex]::Replace($Text, "(?m)^[ \t]*$Key[ \t]*=.*$", { param($m) $m.Value -replace '127\.0\.0\.1:5432', "127.0.0.1:$Port" })
                    Write-Host "Updated $Key in .env to port $Port (local dev URLs only)."
                }
            }
            [IO.File]::WriteAllText($EnvFile, $Text, (New-Object Text.UTF8Encoding($false)))
        }
    }
    Write-Host "PostgreSQL ready (trust, loopback only): psql -h 127.0.0.1 -p $Port -U postgres"
    Write-Host "Install/data: $InstallDir (remove the folder to uninstall; stop first with stop_postgres.ps1)."
} finally { }
