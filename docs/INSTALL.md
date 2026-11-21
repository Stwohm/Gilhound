# Install Gilhound on Windows

1. Open [Gilhound Releases](https://github.com/Stwohm/Gilhound/releases/latest) and download the file named `Gilhound-<version>-win64.zip`.
2. Right-click the zip and choose **Extract All**. Open the extracted folder and double-click **Gilhound.exe**, or right-click **install.ps1** and choose **Run with PowerShell**. If Windows SmartScreen appears, confirm you downloaded the release from the link above, then select **More info** → **Run anyway**.
3. Next time, open **Gilhound** from the Start menu or desktop. The installer uses your Windows account and needs no administrator access.

Gilhound uses Microsoft Edge WebView2 to display its window. If prompted, install the **Evergreen WebView2 Runtime** for Windows x64, then reopen Gilhound. [Microsoft WebView2 download](https://developer.microsoft.com/microsoft-edge/webview2/)

## Updates and support

If you installed 0.1.4 or earlier, close Gilhound and run **Gilhound.exe**
from the newly extracted release once to replace the older launcher.

When the app offers a newer release, download it and choose the restart option. Gilhound checks the download before switching versions. If the new app fails during startup, the launcher reopens the previous version.

Settings and the database live in `%APPDATA%\Gilhound`. The app and its startup log live in `%LOCALAPPDATA%\Gilhound`. To send a support report, choose **Export diagnostics** in the app and share the resulting zip. It contains logs and version information with personal data and the Discord webhook excluded.

## Optional in-game travel

Scans and position tracking read public market APIs and local plugin files.
To let **Go** move your character, enable the optional bridge in Restocky
0.3.9 or later. Each explicit click on **Go** requests only
`/li <world> market`, once per click, for the selected world.
If the bridge is unavailable, **Go** copies the command to your clipboard.
You can also choose **Copy route** at any time, then paste and run it in game.

