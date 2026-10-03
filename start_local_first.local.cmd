@echo off
setlocal
chcp 65001 >nul

set "LOCALFIRST_NODE=node.exe"
where node.exe >nul 2>nul
if not errorlevel 1 goto run
set "LOCALFIRST_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if exist "%LOCALFIRST_NODE%" goto run
echo [ERROR] 找不到可用的 Node 執行環境，本機入口尚未啟動。
echo 請確認已具備的執行環境後重試。
pause
exit /b 1

:run
echo 固定本機入口（合成資料測試）；網址將在服務成功啟動後顯示。
"%LOCALFIRST_NODE%" "%~dp0local-first\serve.mjs"
set "LOCALFIRST_RESULT=%ERRORLEVEL%"
if "%LOCALFIRST_RESULT%"=="0" exit /b 0
echo [ERROR] 本機入口未啟動或已中止。請查看上方錯誤，不要改用其他連接埠。
pause
exit /b %LOCALFIRST_RESULT%
