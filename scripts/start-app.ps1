[CmdletBinding()]
param([ValidateSet('Auto','Sample','Full')][string]$DataMode='Auto',[switch]$SkipDataImport)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $runtime=Join-Path $projectRoot 'data\.runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $python=Join-Path $projectRoot '.venv\Scripts\python.exe'
    if(-not(Test-Path -LiteralPath $python)){throw 'Create .venv and install requirements first. See IMPLEMENTATION.md.'}
    if(-not(Test-Path -LiteralPath '.env')){Copy-Item -LiteralPath '.env.example' -Destination '.env'}
    $englishSettings=@{}
    foreach($line in Get-Content -LiteralPath '.env'){if($line -match '^([A-Z_]+)=(.*)$'){$englishSettings[$matches[1]]=$matches[2]}}
    $graphPort=if($englishSettings['GRAPH_PORT']){$englishSettings['GRAPH_PORT']}else{'3011'}
    $dataPort=if($englishSettings['DATA_API_PORT']){$englishSettings['DATA_API_PORT']}else{'8099'}
    $retrievalPort=if($englishSettings['RETRIEVAL_PORT']){$englishSettings['RETRIEVAL_PORT']}else{'8101'}
    $recommendationPort=if($englishSettings['RECOMMENDATION_PORT']){$englishSettings['RECOMMENDATION_PORT']}else{'8102'}
    # Export this project's configuration to Python helpers, without changing the Chinese app.
    foreach($entry in $englishSettings.GetEnumerator()){[Environment]::SetEnvironmentVariable($entry.Key,$entry.Value,'Process')}
    function Assert-SharedData {
        foreach($name in @('real-estate-agent-postgres-1','real-estate-agent-neo4j-1','real-estate-agent-chroma-1')){
            $running=docker inspect --format '{{.State.Running}}' $name 2>$null
            if($LASTEXITCODE -ne 0){throw "Required shared data container $name is missing. Initialise the listing data using LOCAL_DEPLOYMENT.md first."}
            if($running -ne 'true'){docker start $name | Out-Null;if($LASTEXITCODE -ne 0){throw "Could not start $name"}}
        }
    }
    Assert-SharedData
    if($DataMode -eq 'Auto'){
        $DataMode=if([System.IO.File]::ReadAllText((Join-Path $projectRoot '.env')) -match '(?m)^DATA_MODE=full\s*$'){'Full'}else{'Sample'}
    }
    function Set-LocalEnv([string]$Key,[string]$Value){
        $content=[System.IO.File]::ReadAllText((Join-Path $projectRoot '.env'))
        if($content -match "(?m)^$Key="){$content=[regex]::Replace($content,"(?m)^$Key=.*$",{param($m) "$Key=$Value"})}
        else{$content+="`n$Key=$Value`n"}
        [System.IO.File]::WriteAllText((Join-Path $projectRoot '.env'),$content,[System.Text.UTF8Encoding]::new($false))
    }
    $existing=Get-Content -LiteralPath (Join-Path $runtime 'app-processes.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if($existing -and $existing.processes){
        $running=@($existing.processes | Where-Object { $_ -and $_.id -and (Get-Process -Id $_.id -ErrorAction SilentlyContinue) })
        if($running.Count){Write-Output 'Existing app processes detected. Use scripts/stop-app.ps1 before restarting or changing datasets.';return}
    }
    Set-LocalEnv 'BUSINESS_ENABLED' 'true'
    if(-not([System.IO.File]::ReadAllText((Join-Path $projectRoot '.env')) -match '(?m)^LLM_MODE=')){Set-LocalEnv 'LLM_MODE' 'auto'}
    if(-not([System.IO.File]::ReadAllText((Join-Path $projectRoot '.env')) -match '(?m)^EXTERNAL_ACTION_MODE=')){Set-LocalEnv 'EXTERNAL_ACTION_MODE' 'simulation'}
    Set-LocalEnv 'KNOWLEDGE_MODE' 'hybrid_rrf'
    if($DataMode -eq 'Sample'){
        Set-LocalEnv 'DATA_MODE' 'sample'
        Set-LocalEnv 'DATASET_VERSION' 'historical-csv-main-0c45bf3'
        Set-LocalEnv 'DATA_API_URL' "http://127.0.0.1:$dataPort"
        Set-LocalEnv 'DATA_BUNDLE_MANIFEST_PATH' './data/.runtime/sample-data-bundle.json'
        docker compose --project-name real-estate-agent --env-file .env -f data/compose.yaml -f data/compose.local.yaml --profile sample up -d --wait --wait-timeout 300
        if($LASTEXITCODE -ne 0){throw 'Docker database startup failed'}
        $exists=docker exec real-estate-agent-postgres-1 psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='propertyguru_sample'"
        if(-not $exists){docker exec real-estate-agent-postgres-1 createdb -U postgres -O propertyguru_scraper propertyguru_sample;if($LASTEXITCODE -ne 0){throw 'Sample database creation failed'}}
        if(-not $SkipDataImport -or -not(Test-Path -LiteralPath 'data/.runtime/sample-data-bundle.json')){
            & $python scripts/bootstrap_sample.py
            if($LASTEXITCODE -ne 0){throw 'Historical sample import failed'}
        }
        $dataModule='scripts.sample_data_api:app'
    }else{
        # Full-mode URLs, version, recommendation provider and verified manifest must be set deliberately in .env.
        if(-not([System.IO.File]::ReadAllText((Join-Path $projectRoot '.env')) -match '(?m)^DATA_MODE=full\s*$')){throw 'Configure the full deployment profile in .env before starting Full mode.'}
        # Existing shared listing containers were checked above; no graph import is performed here.
        $global:LASTEXITCODE=0
        if($LASTEXITCODE -ne 0){throw 'Full database startup failed'}
        & '.\scripts\local-data.ps1' -Action Verify
        if($LASTEXITCODE -ne 0){throw 'Full snapshot verification failed'}
        $dataModule='scripts.full_data_api:app'
    }
    if($DataMode -eq 'Sample'){
        node --env-file=.env --import tsx scripts/project-locations.ts
        if($LASTEXITCODE -ne 0){throw 'Official location projection failed'}
    }
    # Chroma is shared; English intent templates use a separate collection.
    $global:LASTEXITCODE=0
    if($LASTEXITCODE -ne 0){throw 'Chroma Docker startup failed'}
    npm run build
    if($LASTEXITCODE -ne 0){throw 'TypeScript build failed'}
    $appSettings=@{}
    foreach($line in Get-Content -LiteralPath '.env'){if($line -match '^([A-Z_]+)=(.*)$'){$appSettings[$matches[1]]=$matches[2]}}
    $appPort=if($appSettings['APP_PORT']){$appSettings['APP_PORT']}else{'8020'}
    foreach($port in @($appPort,$graphPort,$dataPort,$retrievalPort,$recommendationPort)){if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){throw "Port $port is occupied. Stop the owner or configure a separate environment."}}
    if(-not $appSettings['RECOMMENDATION_MODE']){Set-LocalEnv 'RECOMMENDATION_MODE' 'native';$appSettings['RECOMMENDATION_MODE']='native'}
    if($appSettings['RECOMMENDATION_MODE'] -eq 'native'){
        if(-not $appSettings['RECOMMENDATION_SERVICE_TOKEN']){Set-LocalEnv 'RECOMMENDATION_SERVICE_TOKEN' ([Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N'))}
        if(-not $appSettings['RECOMMENDATION_API_URL']){Set-LocalEnv 'RECOMMENDATION_API_URL' "http://127.0.0.1:$recommendationPort"}
        if($DataMode -eq 'Sample'){Set-LocalEnv 'PROPERTYGURU_READ_DATABASE_URL' 'postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample';Set-LocalEnv 'NEO4J_URI' 'bolt://127.0.0.1:7688'}
        else{Set-LocalEnv 'PROPERTYGURU_READ_DATABASE_URL' 'postgresql+psycopg2://propertyguru_scraper@127.0.0.1:5432/propertyguru';Set-LocalEnv 'NEO4J_URI' 'bolt://127.0.0.1:7687'}
    }
    $env:PYTHONUTF8='1'
    $env:GRAPH_SERVICE_URL="http://127.0.0.1:$graphPort"
    $processes=@()
    function Start-Helper([string]$Name,[string]$Executable,[string[]]$Arguments){
        $p=Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime "$Name.stdout.log") -RedirectStandardError (Join-Path $runtime "$Name.stderr.log")
        return [PSCustomObject]@{name=$Name;id=$p.Id;started=$p.StartTime.ToUniversalTime().ToString('o');executable=$Executable}
    }
    function Wait-Ready([string]$Url){$until=(Get-Date).AddSeconds(45);do{try{$response=Invoke-RestMethod -Uri $Url -TimeoutSec 2;if($response.status -eq 'ok'){return}}catch{};Start-Sleep -Seconds 1}while((Get-Date) -lt $until);throw "Service not ready: $Url. Inspect data/.runtime/*.stderr.log"}
    function Save-Processes{[PSCustomObject]@{data_mode=$DataMode;processes=$processes} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtime 'app-processes.json') -Encoding utf8}
    node --import tsx scripts/export-intent-templates.ts
    if($LASTEXITCODE -ne 0){throw 'Intent template export failed'}
    & $python -m retrieval.app
    if($LASTEXITCODE -ne 0){throw 'Chroma intent template indexing failed'}
    & $python -m retrieval.index
    if($LASTEXITCODE -ne 0){throw 'Chroma rental knowledge indexing failed'}
    $processes+=Start-Helper 'retrieval' $python @('-m','uvicorn','retrieval.app:app','--host','127.0.0.1','--port',$retrievalPort)
    Save-Processes
    Wait-Ready "http://127.0.0.1:$retrievalPort/health"
    $processes+=Start-Helper 'data-api' $python @('-m','uvicorn',$dataModule,'--host','127.0.0.1','--port',$dataPort)
    Save-Processes
    Wait-Ready "http://127.0.0.1:$dataPort/health"
    if($appSettings['RECOMMENDATION_MODE'] -eq 'native'){
        $processes+=Start-Helper 'recommendation' $python @('-m','uvicorn','recommendation.app:app','--host','127.0.0.1','--port',$recommendationPort)
        Save-Processes
        Wait-Ready "http://127.0.0.1:$recommendationPort/health"
    }
    $processes+=Start-Helper 'graph' (Get-Command node).Source @('scripts/with-project-env.mjs','dist/server.js')
    Save-Processes
    Wait-Ready "http://127.0.0.1:$graphPort/health"
    $processes+=Start-Helper 'web' $python @('-m','uvicorn','api.main:app','--host','127.0.0.1','--port',$appPort)
    Save-Processes
    Wait-Ready "http://127.0.0.1:$appPort/health"
    Write-Output "Ready: http://127.0.0.1:$appPort (see /api/config for dataset and messaging mode)"
}finally{Pop-Location}
