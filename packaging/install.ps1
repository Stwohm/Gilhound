$ErrorActionPreference = 'Stop'
# The extracted launcher owns installation and shortcut creation for both entry points.
$launcher = Join-Path $PSScriptRoot 'Gilhound.exe'
$process = Start-Process -FilePath $launcher -WorkingDirectory $PSScriptRoot -PassThru
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw 'Gilhound installation failed. See launcher.log.' }

