param([Parameter(Mandatory)][ValidateSet('graph','retrieval','recommendation','web','data-api')][string]$Name)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$recordPath=Join-Path $projectRoot 'data\.runtime\app-processes.json'
$record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$entry=$record.processes | Where-Object name -eq $Name
$port=@{graph=3001;retrieval=8091;recommendation=8092;web=8010;'data-api'=8089}[$Name]
if($entry){
  $process=Get-Process -Id $entry.id -ErrorAction SilentlyContinue
  if($process){
    $expected=if($entry.started -is [DateTime]){$entry.started.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$entry.started).UtcDateTime}
    if([Math]::Abs(($process.StartTime.ToUniversalTime()-$expected).TotalSeconds)-ge 2){throw 'PID was reused; refusing to stop an unrelated process'}
    & taskkill /PID $entry.id /T /F | Out-Null
    $process.WaitForExit(10000) | Out-Null
  }
}
if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){throw "Port $port still occupied"}
$env:PYTHONUTF8='1'
if($Name -eq 'graph'){$exe=(Get-Command node).Source;$arguments=@('--env-file=.env','dist/server.js')}
else{$exe=Join-Path $projectRoot '.venv\Scripts\python.exe';$module=if($Name -eq 'retrieval'){'retrieval.app:app'}elseif($Name -eq 'recommendation'){'recommendation.app:app'}elseif($Name -eq 'data-api'){if([System.IO.File]::ReadAllText((Join-Path $projectRoot '.env')) -match '(?m)^DATA_MODE=full\s*$'){'scripts.full_data_api:app'}else{'scripts.sample_data_api:app'}}else{'api.main:app'};$arguments=@('-m','uvicorn',$module,'--host','127.0.0.1','--port',"$port")}
$runtime=Join-Path $projectRoot 'data\.runtime'
$p=Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime "$Name.stdout.log") -RedirectStandardError (Join-Path $runtime "$Name.stderr.log")
$record.processes=@($record.processes | Where-Object name -ne $Name)+[pscustomobject]@{name=$Name;id=$p.Id;started=$p.StartTime.ToUniversalTime().ToString('o');executable=$exe}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $recordPath -Encoding utf8
$deadline=(Get-Date).AddSeconds(45)
do{try{$r=Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2;if($r.status -eq 'ok'){Write-Output "$Name ready at port $port";exit 0}}catch{};Start-Sleep -Seconds 1}while((Get-Date)-lt $deadline)
throw "Component failed to start; inspect data/.runtime/$Name.stderr.log"
