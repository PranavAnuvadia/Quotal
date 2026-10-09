@echo off
REM Builds bloomcs_host.exe with the inbox C# compiler (no SDK / no NuGet needed).
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /optimize /platform:x64 /target:exe /out:"%~dp0bloomcs_host.exe" "%~dp0bloomcs_host.cs"
if errorlevel 1 ( echo BUILD FAILED & exit /b 1 )
echo BUILD OK: %~dp0bloomcs_host.exe
