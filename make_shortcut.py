import os

desktop = os.path.expanduser('~/Desktop')
shortcut_path = os.path.join(desktop, 'Quotal.lnk')
old_shortcut = os.path.join(desktop, 'WinVoice.lnk')
if os.path.exists(old_shortcut):
    try:
        os.remove(old_shortcut)
    except Exception:
        pass
proj_dir = os.path.dirname(os.path.abspath(__file__))
exe_path = os.path.join(proj_dir, 'Quotal.exe')
icon_path = os.path.join(proj_dir, 'quotal.ico')

vbs = f'''
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{shortcut_path}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{exe_path}"
oLink.WorkingDirectory = "{proj_dir}"
oLink.Description = "Quotal - Intelligent Voice Dictation"
oLink.IconLocation = "{icon_path},0"
oLink.Save
'''
with open('_tmp_sc.vbs', 'w', encoding='utf-8') as f:
    f.write(vbs)
os.system('cscript //nologo _tmp_sc.vbs')
if os.path.exists('_tmp_sc.vbs'):
    os.remove('_tmp_sc.vbs')
print('Desktop shortcut created successfully with custom icon at:', shortcut_path)
