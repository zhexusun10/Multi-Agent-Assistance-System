[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status', 'Places', 'ImportSnapshot', 'Verify')]
    [string]$Action = 'Status',
    [string]$SnapshotPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$docker = (Get-Command docker -ErrorAction Stop).Source
$composeArgs = @('compose', '--project-name', 'real-estate-agent',
    '--env-file', (Join-Path $projectRoot '.env'),
    '-f', (Join-Path $projectRoot 'data\compose.yaml'),
    '-f', (Join-Path $projectRoot 'data\compose.local.yaml'))

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable"
    }
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    Invoke-Checked $docker ($composeArgs + @('config', '--quiet'))
    Invoke-Checked $docker @('info', '--format', '{{.ServerVersion}}')
    if ($Action -in @('Places', 'ImportSnapshot', 'Verify')) {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw 'Create .venv and install data/graph_requirements.txt first. See LOCAL_DEPLOYMENT.md.'
        }
    }
    switch ($Action) {
        'Start' {
            Invoke-Checked $docker ($composeArgs + @('up', '--detach', '--wait', '--wait-timeout', '300'))
        }
        'Stop' {
            # Keep named volumes and container configuration for the next start.
            Invoke-Checked $docker ($composeArgs + @('stop'))
        }
        'Status' {
            Invoke-Checked $docker ($composeArgs + @('ps', '--all'))
        }
        'Places' {
            # Uses the reviewed, committed OSM snapshot; does not crawl or refresh it.
            Invoke-Checked $python @('data/knowledge_graph.py', 'enrich-places')
            Invoke-Checked $python @('data/knowledge_graph.py', 'stats')
        }
        'ImportSnapshot' {
            if (-not $SnapshotPath) {
                $SnapshotPath = Join-Path $projectRoot 'data\propertyguru.db'
            }
            if (-not (Test-Path -LiteralPath $SnapshotPath -PathType Leaf)) {
                throw "The team's complete SQLite snapshot is missing: $SnapshotPath"
            }
            $resolvedSnapshot = (Resolve-Path -LiteralPath $SnapshotPath).Path
            # The upstream command validates all four expected table counts and
            # refuses to overwrite a nonempty database with different counts.
            Invoke-Checked $python @('data/knowledge_graph.py', 'prepare-postgres', '--sqlite-path', $resolvedSnapshot)
            Invoke-Checked $python @('data/knowledge_graph.py', 'import')
            Invoke-Checked $python @('data/knowledge_graph.py', 'verify')
        }
        'Verify' {
            # This is full-snapshot verification. A Places-only deployment must fail it.
            Invoke-Checked $python @('data/knowledge_graph.py', 'verify')
        }
    }
}
finally {
    Pop-Location
}
