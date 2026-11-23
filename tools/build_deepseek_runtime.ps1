$ErrorActionPreference='Stop'
$taskBase=Split-Path $PSScriptRoot -Parent
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$destination=Join-Path $taskBase 'runtime\deepseek-cli'
if(-not (Test-Path -LiteralPath (Join-Path $destination 'node_modules\@deepseek-ai\dsh\lib\bin.js'))){throw 'Install the pinned official @deepseek-ai/dsh package first.'}
if(-not (Test-Path -LiteralPath (Join-Path $destination 'node_modules\@deepseek-ai\dsh-session-persistence-jsonl\lib\worker.cjs'))){throw 'Official persistence worker is missing.'}
& $compiler /nologo /target:exe /optimize+ "/out:$destination\Workdesk.DeepSeekRuntime.exe" (Join-Path $PSScriptRoot 'DeepSeekRuntimeLauncher.cs')
if($LASTEXITCODE){throw 'DeepSeek CLI launcher build failed.'}
Write-Output 'Official DeepSeek Node CLI argv launcher built.'
