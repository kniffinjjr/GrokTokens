@echo off
REM Silent launcher - no PowerShell window
cd /d "%~dp0"
wscript.exe //nologo "%~dp0Start-GrokTokens.vbs"
