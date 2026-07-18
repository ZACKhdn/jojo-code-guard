: << 'CMDBLOCK'
@echo off
REM Cross-platform Claude hook launcher.
if "%~1"=="" exit /b 1
set "HOOK_DIR=%~dp0"
if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    if errorlevel 1 exit /b 1
    exit /b 0
)
where bash >nul 2>nul
if errorlevel 1 goto :jojo_no_bash
bash "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
if errorlevel 1 exit /b 1
exit /b 0
:jojo_no_bash
echo jojo-code-guard: bash was not found. >&2
REM Claude can still use the native Skill; only automatic context injection is skipped.
exit /b 0
CMDBLOCK
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$1"
shift
exec bash "${SCRIPT_DIR}/${SCRIPT_NAME}" "$@"
