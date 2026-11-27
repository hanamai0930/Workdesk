param(
  [Parameter(Mandatory=$true)][string]$Catalog,
  [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$catalogPath = (Resolve-Path -LiteralPath $Catalog).Path
if (-not $OutputDirectory) { $OutputDirectory = Split-Path -Parent (Split-Path -Parent $catalogPath) }
$destination = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($destination) | Out-Null
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$refs = @('System.dll','System.Core.dll','System.Xaml.dll','System.Web.Extensions.dll',
          'WPF\PresentationCore.dll','WPF\PresentationFramework.dll','WPF\WindowsBase.dll',
          'WPF\UIAutomationProvider.dll','WPF\UIAutomationTypes.dll')
$compilerArgs = @('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001')
foreach ($ref in $refs) { $compilerArgs += '/reference:' + (Join-Path $framework $ref) }
$compilerArgs += '/resource:' + $catalogPath + ',catalog.json'
$compilerArgs += '/resource:' + (Join-Path $PSScriptRoot 'Selector.xaml') + ',selector.xaml'
$program = Join-Path $destination 'Workdesk-Setup-Preview.exe'
$compilerArgs += '/out:' + $program
$compilerArgs += Join-Path $PSScriptRoot 'Selector.cs'
& (Join-Path $framework 'csc.exe') @compilerArgs
if ($LASTEXITCODE -ne 0) { throw '编译选装预览失败。' }
Write-Output $program
