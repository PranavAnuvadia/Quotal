import os
import subprocess
import sys

proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pythonw_path = os.path.join(proj_dir, ".venv", "Scripts", "pythonw.exe")
app_path = os.path.join(proj_dir, "app.py")
icon_path = os.path.join(proj_dir, "assets", "quotal.ico")

ps_script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop -or -not (Test-Path $desktop)) {{ $desktop = "$env:USERPROFILE\\Desktop" }}
$shortcutPath = Join-Path $desktop "Quotal.lnk"

# Remove old WinVoice shortcut if exists
$oldShortcut = Join-Path $desktop "WinVoice.lnk"
if (Test-Path $oldShortcut) {{ Remove-Item $oldShortcut -Force -ErrorAction SilentlyContinue }}

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = '{pythonw_path}'
$Shortcut.Arguments = '"{app_path}"'
$Shortcut.WorkingDirectory = '{proj_dir}'
$Shortcut.IconLocation = '{icon_path},0'
$Shortcut.Description = 'Quotal - Intelligent Voice Dictation'
$Shortcut.Save()

Write-Output "SUCCESS: Shortcut updated at $shortcutPath pointing to pythonw.exe"
"""

cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode == 0 and "SUCCESS:" in res.stdout:
    print(res.stdout.strip())
else:
    if res.stdout.strip():
        print(res.stdout.strip())
    if res.stderr.strip():
        print("Error:", res.stderr.strip())
