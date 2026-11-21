# Install Gilhound on Windows

1. Open [Gilhound Releases](https://github.com/Stwohm/Gilhound/releases/latest) and download the file named `Gilhound-<version>-win64.zip`.
2. Right-click the zip and choose **Extract All**. Open the extracted folder and double-click **Gilhound.exe**, or right-click **install.ps1** and choose **Run with PowerShell**. If Windows SmartScreen appears, confirm you downloaded the release from the link above, then select **More info** → **Run anyway**.
3. Next time, open **Gilhound** from the Start menu or desktop. The installer uses your Windows account and needs no administrator access.

Gilhound uses Microsoft Edge WebView2 to display its window. If prompted, install the **Evergreen WebView2 Runtime** for Windows x64, then reopen Gilhound. [Microsoft WebView2 download](https://developer.microsoft.com/microsoft-edge/webview2/)

## Updates and support

If you installed 0.1.4 or earlier, close Gilhound and run **Gilhound.exe**
from the newly extracted release once to replace the older launcher.

From 0.1.5, use the in-app update and restart. The verified 0.1.6 download
includes a launcher inside the staged app folder. When the new window opens,
the app updates the installed launcher, including when the old launcher is
still running. Your existing shortcuts keep working.

If the 0.1.5 launcher already shows “Gilhound could not start” after staging
0.1.6, open `%LOCALAPPDATA%\Gilhound\app-0.1.6\Gilhound.exe` once.
This finishes the staged update using the new launcher. If you still need
to stage it, open `app-0.1.5\gilhound-app.exe` in the install folder and
use the in-app updater first.

The launcher promotes a staged update when the new window acknowledges
startup. If startup fails, it reopens the last working version. It keeps
the current and previous bundles and removes older, unused bundles. If the
current pointer is damaged, it recovers from the newest complete bundle
other than the recorded failed version.

Settings and the database live in `%APPDATA%\Gilhound`. The app and its startup log live in `%LOCALAPPDATA%\Gilhound`. To send a support report, choose **Export diagnostics** in the app and share the resulting zip. It contains logs and version information with personal data and the Discord webhook excluded.

## Optional in-game travel

Scans and position tracking read public market APIs and local plugin files.
To let **Go** move your character, enable the optional bridge in Restocky
0.3.9 or later. Each explicit click on **Go** requests only
`/li <world> market`, once per click, for the selected world.
If the bridge is unavailable, **Go** copies the command to your clipboard.
You can also choose **Copy route** at any time, then paste and run it in game.

