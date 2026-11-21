$ErrorActionPreference = 'Stop'
# Both install entry points create Start menu and desktop shortcuts through the
# launcher, using the installed Gilhound.exe as their icon source.
$launcher = Join-Path $PSScriptRoot 'Gilhound.exe'
$process = Start-Process -FilePath $launcher -WorkingDirectory $PSScriptRoot -PassThru
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw 'Gilhound installation failed. See launcher.log.' }

