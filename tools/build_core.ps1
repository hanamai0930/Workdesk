param(
  [string]$Output = '',
  [string]$GoExecutable = '',
  [string]$ToolchainManifest = ''
)

$ErrorActionPreference = 'Stop'
$repositoryDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$developmentDirectory = Split-Path -Parent $repositoryDirectory
$coreDirectory = Join-Path $repositoryDirectory 'core'
if (-not $ToolchainManifest) { $ToolchainManifest = Join-Path $developmentDirectory 'toolchains\go-toolchain.json' }
$toolchain = $null
if (Test-Path -LiteralPath $ToolchainManifest) {
  # Get-Content decodes the locally prepared UTF-8 file with or without its BOM.
  $toolchain = Get-Content -LiteralPath $ToolchainManifest -Raw -Encoding UTF8 | ConvertFrom-Json
}
if (-not $GoExecutable -and $toolchain) { $GoExecutable = $toolchain.executable }
if (-not $GoExecutable) {
  $goCommand = Get-Command go -ErrorAction SilentlyContinue
  if ($goCommand) { $GoExecutable = $goCommand.Source }
}
if (-not $GoExecutable -or -not (Test-Path -LiteralPath $GoExecutable -PathType Leaf)) {
  throw 'Go is not available. Prepare an official Go toolchain, then pass -GoExecutable or its local toolchain manifest.'
}
if (-not $Output) { $Output = Join-Path $developmentDirectory 'artifacts\stage1\workdesk-core.exe' }
$outputPath = [IO.Path]::GetFullPath($Output)
$outputDirectory = Split-Path -Parent $outputPath
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

$environmentNames = @('GOMODCACHE', 'GOCACHE', 'GOTOOLCHAIN', 'HTTPS_PROXY')
$previousEnvironment = @{}
foreach ($name in $environmentNames) { $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location $coreDirectory
try {
  $env:GOMODCACHE = Join-Path $developmentDirectory 'cache\go-mod'
  $env:GOCACHE = Join-Path $developmentDirectory 'cache\go-build'
  $env:GOTOOLCHAIN = 'local'
  if (-not $env:HTTPS_PROXY) {
    $moduleEndpoint = [Uri]'https://proxy.golang.org'
    $systemProxy = [System.Net.WebRequest]::DefaultWebProxy
    if ($systemProxy) {
      $proxyAddress = $systemProxy.GetProxy($moduleEndpoint)
      if ($proxyAddress -and $proxyAddress.Authority -ne $moduleEndpoint.Authority) { $env:HTTPS_PROXY = $proxyAddress.AbsoluteUri }
    }
  }
  $goVersion = & $GoExecutable version
  if ($LASTEXITCODE) { throw 'The configured Go executable could not report its version.' }
  & $GoExecutable build -trimpath -buildvcs=false -o $outputPath ./cmd/workdesk-core
  if ($LASTEXITCODE) { throw 'Go core build failed.' }
  $binaryHash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $sourceRevision = & git -C $repositoryDirectory rev-parse HEAD
  if ($LASTEXITCODE) { $sourceRevision = '' }
  $manifest = [ordered]@{
    schema = 1
    go_version = [string]$goVersion
    source_revision = [string]$sourceRevision
    binary = $outputPath
    sha256 = $binaryHash
    flags = @('-trimpath', '-buildvcs=false')
    built_utc = [DateTime]::UtcNow.ToString('o')
  }
  [IO.File]::WriteAllText(($outputPath + '.build.json'), ($manifest | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
  Write-Output ('Go core candidate built: ' + $outputPath)
  Write-Output ('SHA256: ' + $binaryHash)
} finally {
  Pop-Location
  foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process') }
}
