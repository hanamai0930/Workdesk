param([switch]$Probe)
$ErrorActionPreference='Stop'
$taskBase=Split-Path $PSScriptRoot -Parent
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sdk=Join-Path $taskBase 'runtime\webview2-sdk'
$destination=Join-Path $taskBase 'runtime\workdesk-desktop'
$productionRoot=$destination
if(-not $Probe){
  # Publish alongside an open window, whose executable/DLLs are locked.
  $hashParts=@('workdesk-ui\desktop\WorkdeskHost.cs','workdesk-ui\desktop\WorkdeskChatAccounts.cs','workdesk-ui\desktop\app.manifest','tools\build_desktop.ps1') | ForEach-Object {(Get-FileHash -LiteralPath (Join-Path $taskBase $_) -Algorithm SHA256).Hash}
  $hasher=[System.Security.Cryptography.SHA256]::Create()
  $version=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes(($hashParts -join ''))))).Replace('-','').ToLower().Substring(0,20)
  $destination=Join-Path $productionRoot "versions\$version"
}
if($Probe){$destination=Join-Path $taskBase 'build\chat-native-20261020\host'}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
if((-not $Probe) -and (Test-Path -LiteralPath (Join-Path $destination 'Workdesk.Desktop.exe'))){
  @{executable="versions/$version/Workdesk.Desktop.exe"} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $productionRoot 'current.json') -Encoding UTF8
  Write-Output 'Current Workdesk desktop build already published.'
  return
}
Copy-Item -LiteralPath (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'),(Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'),(Join-Path $sdk 'runtimes\win-x64\native\WebView2Loader.dll') -Destination $destination -Force
$sources=@("$taskBase\workdesk-ui\desktop\WorkdeskHost.cs","$taskBase\workdesk-ui\desktop\WorkdeskChatAccounts.cs")
if($Probe){$sources+=Join-Path $taskBase 'tools\ChatDesktopProbe.cs'}
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$destination\Workdesk.Desktop.exe" "/win32manifest:$taskBase\workdesk-ui\desktop\app.manifest" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll "/reference:$destination\Microsoft.Web.WebView2.Core.dll" "/reference:$destination\Microsoft.Web.WebView2.WinForms.dll" $sources
if($LASTEXITCODE){throw 'Desktop host build failed.'}
if(-not $Probe){
  $pointer=Join-Path $productionRoot 'current.json'
  @{executable="versions/$version/Workdesk.Desktop.exe"} | ConvertTo-Json | Set-Content -LiteralPath "$pointer.tmp" -Encoding UTF8
  Move-Item -LiteralPath "$pointer.tmp" -Destination $pointer -Force
}
Write-Output 'Workdesk WebView2 desktop host built.'
