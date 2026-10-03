"""
win_startup.py - Configure WinVoice to start automatically with Windows.
"""

import os
import sys

STARTUP_DIR = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
SHORTCUT_PATH = os.path.join(STARTUP_DIR, "Quotal.lnk")
OLD_SHORTCUT_PATH = os.path.join(STARTUP_DIR, "WinVoice.lnk")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
EXE_PATH = os.path.join(PROJECT_DIR, "Quotal.exe")
ICON_PATH = os.path.join(PROJECT_DIR, "quotal.ico")


def is_autostart_enabled() -> bool:
    """Check if the startup shortcut exists."""
    return os.path.exists(SHORTCUT_PATH) or os.path.exists(OLD_SHORTCUT_PATH)


def set_autostart(enable: bool) -> bool:
    """Enable or disable start with Windows."""
    if enable:
        try:
            if os.path.exists(OLD_SHORTCUT_PATH):
                try:
                    os.remove(OLD_SHORTCUT_PATH)
                except Exception:
                    pass
            vbs_script = f'''
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{SHORTCUT_PATH}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{EXE_PATH}"
oLink.Arguments = "--silent"
oLink.WorkingDirectory = "{PROJECT_DIR}"
oLink.Description = "Quotal AutoStart"
oLink.IconLocation = "{ICON_PATH},0"
oLink.Save
'''
            temp_vbs = os.path.join(PROJECT_DIR, "_create_shortcut.vbs")
            with open(temp_vbs, "w", encoding="utf-8") as f:
                f.write(vbs_script)
            os.system(f'cscript //nologo "{temp_vbs}"')
            if os.path.exists(temp_vbs):
                os.remove(temp_vbs)
            return True
        except Exception as e:
            print(f"[AutoStart Error] {e}")
            return False
    else:
        try:
            if os.path.exists(SHORTCUT_PATH):
                os.remove(SHORTCUT_PATH)
            return True
        except Exception as e:
            print(f"[AutoStart Error] {e}")
            return False

if __name__ == "__main__":
    set_autostart(True)
