; Quotal installer (Inno Setup 6).
; Built by installer/build.py, which stamps {#Ver} from version.py.
; Produces QuotalSetup.exe — the exact asset name updater.py downloads for OTA.
;
; Design: per-user install (no admin/UAC), full offline payload
; (venv + bundled Hinglish model). Whisper Base/Small and the Qwen
; Neural-polish model download on demand from inside the dashboard.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "Quotal"
#define MyAppPublisher "Quotal Contributors"
#define MyAppURL "https://github.com/PranavAnuvadia/Quotal"
#define MyAppExe "Quotal.exe"

[Setup]
AppId={{8F4C2A1B-6D3E-4F7A-9C5B-2E8A0D4F6B1A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=QuotalSetup
SetupIconFile=..\assets\quotal.ico
UninstallDisplayIcon={app}\{#MyAppExe}
WizardStyle=modern
ArchitecturesAllowed=x64compatible
SolidCompression=yes
Compression=lzma2/fast
; fast (not max): DLL/model binaries barely compress further, but max costs
; 3-5x compile time. Keeps release-day builds sane.
DiskSpanning=no
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no
LicenseFile=..\LICENSE
InfoBeforeFile=
InfoAfterFile=
; OTA updates call the installer with /SILENT /NORESTART (see updater.py)

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "startup"; Description: "Launch at Windows startup (silent, in the tray)"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; .venv\Lib is installed by its own entry below: the broad "Lib,*" exclude
; (needed for the unreferenced root Lib\ duplicate) would otherwise nuke it.
Source: "..\.venv\Lib\*"; DestDir: "{app}\.venv\Lib"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "torch*,sympy*,mpmath*,networkx*,accelerate*,PyInstaller*,pyinstaller*,pip*,nvidia*"
; Cut (verified dead at runtime via import-block simulation + dep-chain audit):
;   torch + sympy/mpmath/networkx/torchgen (CPU-only wheel; code refuses CPU, CT2 needs no torch),
;   accelerate (never imported), PyInstaller (build tool), pip (updater/models use urllib/hf_hub).
; Everything else except dev/user/machine-specific files (see notes below).
Source: "..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".git\*,__pycache__\*,*.pyc,*.log,history.jsonl,settings.json,smart_enhancer_new.py,installer\*,Lib\*,download-page\*,nvidia\*,models\qwen2.5-0.5b\*,models\qwen2.5-0.5b-ct2\*,QUOTAL*.unk,rcedit.exe,tools_rcedit.exe,_create_shortcut.vbs"
; Excluded and why:
;   .git/__pycache__/*.pyc ... dev artifacts
;   *.log/history.jsonl/settings.json ... per-user runtime state (fresh defaults on first run)
;   installer/* ... our own build tooling and setup output (never recurse the setup into itself)
;   smart_enhancer_new.py ... dev backup copy (runtime uses smart_enhancer.py)
;   download-page ... marketing website, not part of the installed app
;   Lib ... unreferenced duplicate of .venv (runtime uses .venv only)
;   nvidia ... ~2GB CUDA wheels are an OPTIONAL dashboard download (gpu_pack.py),
;   so Intel/AMD laptops skip them; NVIDIA owners fetch QuotalGpuPack.zip on demand
;   models/qwen2.5-0.5b* ... ~2GB Neural-polish model, dashboard downloads it on demand
;   QUOTAL*.unk/rcedit/tools_rcedit ... build-time leftovers
; Bundled: full .venv (CPU+GPU wheels), code, dashboard, pills, Hinglish model.
; Whisper Base/Small live in the user's HF cache and download on demand.

[Registry]
; Mirror of win_startup.set_autostart(True): HKCU Run + Startup shortcut.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Quotal"; ValueData: """{app}\{#MyAppExe}"" --silent"; Flags: uninsdeletevalue; Tasks: startup

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\assets\quotal.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\assets\quotal.ico"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Parameters: "--silent"; WorkingDir: "{app}"; IconFilename: "{app}\assets\quotal.ico"; Tasks: startup

[Run]
; Always relaunch after install — including /SILENT OTA updates, where the
; updater quit the app first and the user expects it back in the tray.
Filename: "{app}\{#MyAppExe}"; Flags: nowait postinstall; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Kill any running instances upfront so file checks never prompt the user
  Exec('taskkill.exe', '/F /IM Quotal.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(400);
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  // Engine, dashboard and pill hosts all run under the Quotal.exe image.
  // Kill them before copying so locked files (venv DLLs) replace cleanly.
  if CurStep = ssInstall then
  begin
    Exec('taskkill.exe', '/F /IM Quotal.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(800);
  end;
end;
