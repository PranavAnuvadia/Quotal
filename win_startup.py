"""
win_startup.py - Configure Quotal to start automatically with Windows.
Uses standard HKCU Run registry key (official Windows method for desktop apps)
plus Startup folder shortcut fallback.
"""

import os
import sys
import winreg

STARTUP_DIR = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
SHORTCUT_PATH = os.path.join(STARTUP_DIR, "Quotal.lnk")
OLD_SHORTCUT_PATH = os.path.join(STARTUP_DIR, "WinVoice.lnk")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
EXE_PATH = os.path.join(PROJECT_DIR, "Quotal.exe")
ICON_PATH = os.path.join(PROJECT_DIR, "quotal.ico")

REG_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VAL_NAME = "Quotal"
OLD_REG_VAL_NAME = "WinVoice"


def is_autostart_enabled() -> bool:
    """Check if autostart is configured via Registry or Startup folder."""
    # 1. Check HKCU Run registry key
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REG_VAL_NAME)
            if val:
                return True
    except (FileNotFoundError, OSError):
        pass

    # 2. Check Startup folder shortcut
    return os.path.exists(SHORTCUT_PATH) or os.path.exists(OLD_SHORTCUT_PATH)


def _create_shortcut():
    """Create or update Startup folder shortcut as fallback."""
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
    except Exception:
        pass


def set_autostart(enable: bool) -> bool:
    """Enable or disable start with Windows."""
    if enable:
        success = False
        # 1. Primary: HKCU Run registry key (standard Windows desktop app startup)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                cmd = f'"{EXE_PATH}" --silent'
                winreg.SetValueEx(key, REG_VAL_NAME, 0, winreg.REG_SZ, cmd)
                try:
                    winreg.DeleteValue(key, OLD_REG_VAL_NAME)
                except (FileNotFoundError, OSError):
                    pass
            success = True
        except Exception as e:
            print(f"[AutoStart Registry Error] {e}")

        # 2. Secondary: Startup folder shortcut
        _create_shortcut()

        return success or os.path.exists(SHORTCUT_PATH)
    else:
        # 1. Remove from HKCU Run registry
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, REG_VAL_NAME)
                except (FileNotFoundError, OSError):
                    pass
                try:
                    winreg.DeleteValue(key, OLD_REG_VAL_NAME)
                except (FileNotFoundError, OSError):
                    pass
        except Exception as e:
            print(f"[AutoStart Registry Remove Error] {e}")

        # 2. Remove Startup folder shortcuts
        for path in (SHORTCUT_PATH, OLD_SHORTCUT_PATH):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        return True


if __name__ == "__main__":
    set_autostart(True)
    print("Autostart enabled:", is_autostart_enabled())
