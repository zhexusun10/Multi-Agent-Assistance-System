# Local Windows development only. Installs nothing globally; runtime stays in data/.runtime.
[CmdletBinding()]
param(
    [string]$SqlitePath = "",
    [ValidateSet("Postgres", "SQLite")][string]$Source = "Postgres",
    [ValidateRange(1,65535)][int]$Port = 8088,
    [switch]$SkipImport,
    [switch]$SkipDependencies,
    [switch]$UseExistingNeo4j,
    [switch]$WithPostgres,
    [switch]$NoAuth,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
if ($WithPostgres -and $Source -eq "SQLite") { throw "-WithPostgres requires -Source Postgres. Use -Source SQLite without it for offline exploration." }
if (!$SqlitePath) { $SqlitePath = Join-Path $PSScriptRoot "propertyguru.db" }
$Root = Split-Path $PSScriptRoot -Parent
Push-Location $Root
try {
    $Runtime = Join-Path $PSScriptRoot ".runtime"
    New-Item -ItemType Directory -Force $Runtime | Out-Null
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    if (!(Test-Path $Python)) {
        & py -3 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11+ and create .venv first." }
    }
    if (!$SkipDependencies) {
        $Requirements = Join-Path $PSScriptRoot "graph_browser_requirements.txt"
        & $Python -m pip install -r $Requirements
        if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
    }
    if (!(Test-Path ".env")) {
        $Template = [IO.File]::ReadAllText((Join-Path $Root ".env.example"))
        [IO.File]::WriteAllText((Join-Path $Root ".env"), $Template, (New-Object Text.UTF8Encoding($false)))
        Write-Host "Created ignored .env from the local-development template. Existing .env is never replaced."
    }
    if ($WithPostgres) {
        # Create .env BEFORE the PG helper may rewrite a conflicting local port.
        & (Join-Path $PSScriptRoot "start_postgres.ps1")
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL bootstrap failed." }
    }
    $SourceArgs = @()
    if ($Source -eq "SQLite") {
        if (!(Test-Path $SqlitePath -PathType Leaf)) { throw "Fixed SQLite snapshot not found: $SqlitePath. Obtain the team's 2026-10-09 snapshot." }
        $SourceArgs = @('--sqlite', $SqlitePath)
    } else {
        & $Python data/knowledge_graph.py prepare-postgres --sqlite-path "$SqlitePath"
        if ($LASTEXITCODE -ne 0) { throw "PG snapshot preparation failed. Existing nonempty data is never overwritten automatically; check the dedicated URL or resume a known partial migration manually." }
    }
    if ($NoAuth) {
        # Change only Neo4j auth/password keys; preserve model and SQL configuration.
        $EnvFile = Join-Path $Root ".env"
        $EnvText = [IO.File]::ReadAllText($EnvFile)
        if ($EnvText -match '(?m)^[ \t]*NEO4J_AUTH[ \t]*=') {
            $EnvText = [regex]::Replace($EnvText, '(?m)^[ \t]*NEO4J_AUTH[ \t]*=.*$', 'NEO4J_AUTH=none')
        } else { $EnvText = $EnvText.TrimEnd() + "`nNEO4J_AUTH=none`n" }
        if ($EnvText -match '(?m)^[ \t]*NEO4J_PASSWORD[ \t]*=') {
            $EnvText = [regex]::Replace($EnvText, '(?m)^[ \t]*NEO4J_PASSWORD[ \t]*=.*$', 'NEO4J_PASSWORD=')
        } else { $EnvText = $EnvText.TrimEnd() + "`nNEO4J_PASSWORD=`n" }
        if ($EnvText -notmatch '(?m)^[ \t]*NEO4J_BROWSER_URL[ \t]*=') {
            $EnvText = $EnvText.TrimEnd() + "`nNEO4J_BROWSER_URL=http://127.0.0.1:7474/browser/`n"
        }
        [IO.File]::WriteAllText($EnvFile, $EnvText, (New-Object Text.UTF8Encoding($false)))
        $env:NEO4J_AUTH = "none"
        $env:NEO4J_PASSWORD = $null
    }
    # dotenv handles quoting and environment precedence; do not evaluate .env as a script.
    $ConfigCode = "import sys,os,json; from data.graph.source import load_graph_env; from data.graph.sync import graph_auth; load_graph_env(); a=graph_auth(); print(json.dumps({'NEO4J_AUTH':'none' if a is None else 'password','NEO4J_PASSWORD':a[1] if a else '', 'NEO4J_USER':a[0] if a else 'neo4j','NEO4J_URI':os.getenv('NEO4J_URI','bolt://127.0.0.1:7687'),'NEO4J_DATABASE':os.getenv('NEO4J_DATABASE','neo4j')}))"
    $ConfigJson = & $Python -c $ConfigCode
    if ($LASTEXITCODE -ne 0) { throw "Unable to read Neo4j configuration." }
    $Config = $ConfigJson | ConvertFrom-Json
    $AuthEnabled = $Config.NEO4J_AUTH -ne "none"
    if ($AuthEnabled -and ($Config.NEO4J_PASSWORD.Length -lt 8 -or $Config.NEO4J_PASSWORD -eq "change-this-local-password")) {
        throw "Set a unique NEO4J_PASSWORD (at least 8 characters) in .env first."
    }
    if (!$AuthEnabled) { Write-Warning "Neo4j authentication is disabled. Loopback access only; never expose ports 7474/7687 to a network." }

    function Install-PortableZip($Name, $Url, $Hash, $ExpectedFile) {
        if (Test-Path $ExpectedFile) { return }
        $Downloads = Join-Path $Runtime "downloads"
        New-Item -ItemType Directory -Force $Downloads | Out-Null
        $Zip = Join-Path $Downloads $Name
        if (!(Test-Path $Zip)) {
            Write-Host "Downloading $Name ..."
            & curl.exe -fL --retry 2 --max-time 300 -o "$Zip" "$Url"
            if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
        }
        if ((Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) {
            throw "Checksum mismatch: $Zip. Remove the bad archive and retry."
        }
        # Python handles Unicode paths and extracts much faster than Expand-Archive.
        & $Python -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$Zip" "$Runtime"
        if ($LASTEXITCODE -ne 0) { throw "Archive extraction failed: $Zip" }
    }
    function Test-Neo4j {
        & $Python data/knowledge_graph.py check --quiet
        return $LASTEXITCODE -eq 0
    }
    function Wait-Neo4j {
        for ($Attempt=0; $Attempt -lt 60; $Attempt++) {
            if (Test-Neo4j) { return }
            Start-Sleep -Seconds 2
        }
        throw "Neo4j did not become ready. Check data/.runtime/neo4j.stderr.log and Neo4j logs. SQL source is unchanged."
    }
    $NeoHome = Join-Path $Runtime "neo4j-community-5.26.0"
    $NeoConf = Join-Path $NeoHome "conf/neo4j.conf"
    $AuthValue = if ($AuthEnabled) { "true" } else { "false" }
    function Set-PortableAuth($Text) {
        $WithoutAuth = [regex]::Replace($Text, '(?m)^[ \t]*#?[ \t]*dbms\.security\.auth_enabled[ \t]*=.*(?:\r?\n|$)', '')
        return $WithoutAuth.TrimEnd() + "`ndbms.security.auth_enabled=$AuthValue`n"
    }
    if (!$UseExistingNeo4j -and (Test-Path $NeoConf)) {
        $CurrentConf = [IO.File]::ReadAllText($NeoConf)
        $CurrentAuth = $CurrentConf -notmatch '(?m)^[ \t]*dbms\.security\.auth_enabled[ \t]*=[ \t]*false[ \t]*\r?$'
        if ($CurrentAuth -ne $AuthEnabled) {
            # An auth change needs a restart. Never stop or reconfigure an external server.
            $Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 7474,7687 }
            if ($Listeners) {
                $PidFile = Join-Path $Runtime "neo4j-launcher.pid"
                $Owned = $false
                if (Test-Path $PidFile) {
                    $LauncherId = [int]([IO.File]::ReadAllText($PidFile).Trim())
                    $Launcher = Get-CimInstance Win32_Process -Filter "ProcessId=$LauncherId" -ErrorAction SilentlyContinue
                    $Owned = $Launcher -and $Launcher.ExecutablePath -and $Launcher.ExecutablePath.StartsWith($Root + "\", [StringComparison]::OrdinalIgnoreCase) -and $Launcher.CommandLine -match 'org.neo4j.server.startup.Neo4jCommand.*console'
                }
                if (!$Owned) { throw "An external Neo4j is running. Configure its authentication separately and use -UseExistingNeo4j." }
            }
            & (Join-Path $PSScriptRoot "stop_knowledge_graph.ps1")
            [IO.File]::WriteAllText($NeoConf, (Set-PortableAuth $CurrentConf), (New-Object Text.UTF8Encoding($false)))
        }
    }
    if ($UseExistingNeo4j) {
        if (!(Test-Neo4j)) { throw "Existing Neo4j is unavailable or auth mode/credentials do not match. -NoAuth does not reconfigure external servers." }
    } elseif (!(Test-Neo4j)) {
        if ($Config.NEO4J_USER -ne "neo4j" -or $Config.NEO4J_URI -ne "bolt://127.0.0.1:7687" -or $Config.NEO4J_DATABASE -ne "neo4j") {
            throw "Portable mode needs the template's local URI/user/database. For another server use -UseExistingNeo4j."
        }
        $Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 7474,7687 }
        if ($Listeners) { throw "Ports 7474/7687 are occupied. Check existing Neo4j credentials; do not reset its data." }
        $JavaHome = Join-Path $Runtime "zulu21.40.17-ca-jre21.0.6-win_x64"
        Install-PortableZip "neo4j-community-5.26.0-windows.zip" "https://dist.neo4j.org/neo4j-community-5.26.0-windows.zip" "ea9c98111b72310dab8ae00b7ae4d43df59a1ea82a490579b886bffb3e30de8f" (Join-Path $NeoHome "bin/neo4j.bat")
        Install-PortableZip "zulu21.40.17-ca-jre21.0.6-win_x64.zip" "https://cdn.azul.com/zulu/bin/zulu21.40.17-ca-jre21.0.6-win_x64.zip" "274aa5717e28b9d722cb4b9c2864d8bbe2537203270d022beefc11e76e74ce85" (Join-Path $JavaHome "bin/java.exe")
        $Java = Join-Path $JavaHome "bin/java.exe"
        $Conf = [IO.File]::ReadAllText($NeoConf)
        if (!$Conf.Contains("# PropertyGuru local configuration")) {
            $Conf += "`n# PropertyGuru local configuration`nserver.default_listen_address=127.0.0.1`nserver.default_advertised_address=127.0.0.1`nserver.memory.heap.initial_size=512m`nserver.memory.heap.max_size=1g`nserver.memory.pagecache.size=256m`ndbms.usage_report.enabled=false`n"
        }
        [IO.File]::WriteAllText($NeoConf, (Set-PortableAuth $Conf), (New-Object Text.UTF8Encoding($false)))
        $OldJavaHome = $env:JAVA_HOME
        try {
            $env:JAVA_HOME = $JavaHome
            if ($AuthEnabled -and !(Test-Path (Join-Path $NeoHome "data/dbms/auth.ini")) -and !(Test-Path (Join-Path $NeoHome "data/databases/system"))) {
                # Neo4j's Windows launcher escapes non-ASCII absolute arguments.
                # Relative home/classpath arguments keep Chinese user paths usable.
                Push-Location $NeoHome
                try {
                    & $Java -cp 'lib/*' '-Dbasedir=.' org.neo4j.server.startup.Neo4jAdminCommand dbms set-initial-password "$($Config.NEO4J_PASSWORD)" --require-password-change=false
                    if ($LASTEXITCODE -ne 0) { throw "Neo4j initial password setup failed." }
                } finally { Pop-Location }
            }
            $Neo = Start-Process -FilePath $Java -ArgumentList @('-cp', 'lib/*', '-Dbasedir=.', 'org.neo4j.server.startup.Neo4jCommand', 'console') -WorkingDirectory $NeoHome -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Runtime "neo4j.stdout.log") -RedirectStandardError (Join-Path $Runtime "neo4j.stderr.log") -PassThru
            $Neo.Id | Set-Content (Join-Path $Runtime "neo4j-launcher.pid")
            Wait-Neo4j
        } finally { $env:JAVA_HOME = $OldJavaHome }
    }
    if (!$SkipImport) {
        & $Python data/knowledge_graph.py import @SourceArgs --report (Join-Path $Runtime "import-report.json")
        if ($LASTEXITCODE -ne 0) { throw "Import failed. Earlier Neo4j batches remain; rerun safely after fixing the connection." }
    }
    $ApiUrl = "http://127.0.0.1:$Port"
    $Listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port }
    $ExpectedStore = if ($Source -eq "SQLite") { "sqlite" } else { "postgresql" }
    if ($Listening) {
        $Compatible = $false
        try {
            $Health = Invoke-RestMethod "$ApiUrl/ready" -TimeoutSec 10
            $Capabilities = Invoke-RestMethod "$ApiUrl/api/v1/capabilities" -TimeoutSec 10
            $Compatible = $Health.status -eq "ok" -and $Health.sql_source -eq $ExpectedStore -and $Capabilities.listing_snapshot.date -eq "2026-10-09"
        } catch { }
        if (!$Compatible) {
            # Upgrade/switch only a recorded project-owned API on this port, never an external service.
            $ApiPidFile = Join-Path $Runtime "visualization.pid"
            $OwnedApi = $null
            if (Test-Path $ApiPidFile) {
                $ApiId = [int]([IO.File]::ReadAllText($ApiPidFile).Trim())
                $OwnedApi = Get-CimInstance Win32_Process -Filter "ProcessId=$ApiId" -ErrorAction SilentlyContinue
            }
            $PortPattern = "--port\s+`"?$Port(?:`"|\s|$)"
            if (!$OwnedApi -or !$OwnedApi.ExecutablePath -or !$OwnedApi.ExecutablePath.StartsWith($Root + "\", [StringComparison]::OrdinalIgnoreCase) -or $OwnedApi.CommandLine -notmatch 'knowledge_graph.py.*serve' -or $OwnedApi.CommandLine -notmatch $PortPattern) {
                throw "Port $Port is used by an unrecognized or incompatible API. Stop it yourself or choose -Port; it will not be reset automatically."
            }
            & (Join-Path $PSScriptRoot "stop_knowledge_graph.ps1") -WebOnly
            $Listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port }
            if ($Listening) { throw "The old API still owns port $Port; retry after it exits." }
        } else { Write-Host "Reusing the ready $ExpectedStore API; full snapshot acceptance follows." }
    }
    if (!$Listening) {
        $ApiArguments = @('data/knowledge_graph.py','serve','--port',"$Port")
        if ($Source -eq "SQLite") { $ApiArguments += @('--sqlite', ('"' + $SqlitePath + '"')) }
        $Api = Start-Process -FilePath $Python -ArgumentList $ApiArguments -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Runtime "visualization.stdout.log") -RedirectStandardError (Join-Path $Runtime "visualization.stderr.log") -PassThru
        $Api.Id | Set-Content (Join-Path $Runtime "visualization.pid")
        $Ready = $false
        for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
            try { $Health = Invoke-RestMethod "$ApiUrl/ready" -TimeoutSec 5; if ($Health.status -eq "ok" -and $Health.sql_source -eq $ExpectedStore) { $Ready=$true; break } } catch { Start-Sleep -Seconds 1 }
        }
        if (!$Ready) { throw "Read-only API/SQL readiness failed. See data/.runtime/visualization.stderr.log." }
    }
    & $Python data/knowledge_graph.py verify @SourceArgs --api-url $ApiUrl
    if ($LASTEXITCODE -ne 0) { throw "SQL/Neo4j/API snapshot acceptance failed. Fix the reported mismatch; no data is deleted automatically." }
    $OfficialUrl = & $Python data/knowledge_graph.py browser --print-url
    if ($LASTEXITCODE -ne 0) { throw "Unable to build the official Browser URL." }
    Write-Host "Official Neo4j Browser (connection/database/query prefilled): $OfficialUrl"
    Write-Host "Read-only HTTP API / Swagger: $ApiUrl/docs"
    Write-Host "Logs and import report: $Runtime"
    if (!$NoBrowser) {
        if ($AuthEnabled) { Start-Process $OfficialUrl }
        else {
            # A separate Chrome session can select No authentication and show
            # the graph automatically; no personal browser profile is touched.
            $BrowserPidFile = Join-Path $Runtime "official-browser.pid"
            $BrowserRunning = $false
            if (Test-Path $BrowserPidFile) {
                $BrowserId = [int]([IO.File]::ReadAllText($BrowserPidFile).Trim())
                $Existing = Get-CimInstance Win32_Process -Filter "ProcessId=$BrowserId" -ErrorAction SilentlyContinue
                $BrowserRunning = $Existing -and $Existing.ExecutablePath -and $Existing.ExecutablePath.StartsWith($Root + "\", [StringComparison]::OrdinalIgnoreCase) -and $Existing.CommandLine -match 'knowledge_graph.py.*browser.*--auto-connect'
            }
            if (!$BrowserRunning) {
                $Browser = Start-Process -FilePath $Python -ArgumentList @('data/knowledge_graph.py','browser','--auto-connect') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Runtime "official-browser.stdout.log") -RedirectStandardError (Join-Path $Runtime "official-browser.stderr.log") -PassThru
                $Browser.Id | Set-Content $BrowserPidFile
            } else { Write-Host "The isolated official Browser window is already running." }
        }
    }
} finally { Pop-Location }
