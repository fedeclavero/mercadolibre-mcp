@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title Instalar Mercado Libre MCP

set "origen=%~dp0mercadolibre-mcp.exe"
set "carpeta=%LOCALAPPDATA%\Programs\mercadolibre-mcp"
set "destino=%carpeta%\mercadolibre-mcp.exe"
if not exist "%origen%" (
    echo Extrae todos los archivos del ZIP y ejecuta Instalar.cmd desde esa carpeta.
    goto error
)

"%origen%" --version
if errorlevel 1 goto error
if not exist "%carpeta%\" mkdir "%carpeta%"
if errorlevel 1 goto error
if /i not "%origen%"=="%destino%" (
    call :copiar
    if errorlevel 1 goto error
)

"%destino%" onboard
if errorlevel 1 goto error
echo.
pause
exit /b 0

:error
echo.
pause
exit /b 1

:copiar
set "temporal=%destino%.%RANDOM%-%RANDOM%.tmp"
copy /y "%origen%" "%temporal%" >nul && move /y "%temporal%" "%destino%" >nul && exit /b 0
if exist "%temporal%" del /q "%temporal%"
echo Cerra por completo Claude Code, Codex y Antigravity y ejecuta Instalar.cmd otra vez.
exit /b 1
