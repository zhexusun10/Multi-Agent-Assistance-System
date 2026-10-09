# Stop only processes recorded by this project's portable startup script.
# This terminates the local process trees; Neo4j recovers committed transactions on restart.
[CmdletBinding()]
param([switch]$WebOnly)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Names = @("visualization")
if (!$WebOnly) { $Names += "official-browser", "neo4j-launcher" }
foreach ($Name in $Names) {
    $PidFile = Join-Path $PSScriptRoot ".runtime/$Name.pid"
    if (!(Test-Path $PidFile)) { continue }
    $PidToStop = [int]([IO.File]::ReadAllText($PidFile).Trim())
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$PidToStop" -ErrorAction SilentlyContinue
    if ($null -eq $Process) { Remove-Item $PidFile; continue }
    $Owned = $Process.ExecutablePath -and $Process.ExecutablePath.StartsWith($Root + "\", [StringComparison]::OrdinalIgnoreCase)
    $Expected = switch ($Name) {
        "visualization" { "knowledge_graph.py.*serve" }
        "official-browser" { "knowledge_graph.py.*browser.*--auto-connect" }
        default { "org.neo4j.server.startup.Neo4jCommand.*console" }
    }
    if (!$Owned -or $Process.CommandLine -notmatch $Expected) {
        Write-Warning "Refusing to stop reused/unrecognized PID $PidToStop ($Name)."
        continue
    }
    & taskkill.exe /PID $PidToStop /T /F
    if ($LASTEXITCODE -ne 0) { throw "Unable to stop $Name (PID $PidToStop)." }
    Remove-Item $PidFile
    Write-Host "Stopped $Name. Data and logs in data/.runtime are preserved."
}
