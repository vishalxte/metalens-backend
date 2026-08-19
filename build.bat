@echo off
REM ====================================================================
REM  Metalens backend - Nuitka build (Docker Desktop, Linux containers)
REM
REM  Run this from Windows. It does two things:
REM
REM    1. Compiles app\ into ONE file, build\app.cpython-311-x86_64-
REM       linux-gnu.so, and copies it out of the build container onto
REM       this disk so you can see it / archive it / ship it.
REM    2. Builds the runtime image metalens-backend:nuitka, which
REM       contains that .so and NO application .py source at all.
REM
REM  Usage:
REM      build.bat            normal build (uses the Docker layer cache)
REM      build.bat clean      ignore the cache and rebuild everything
REM ====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "NOCACHE="
if /i "%~1"=="clean" set "NOCACHE=--no-cache"

REM BuildKit is what makes `--output type=local` work. Docker Desktop
REM enables it by default, but an old DOCKER_BUILDKIT=0 in the user
REM environment would silently break step 1, so force it on.
set DOCKER_BUILDKIT=1

echo.
echo ============================================================
echo  Checking Docker
echo ============================================================
docker version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not reachable.
    echo         Start Docker Desktop, wait for the whale icon to go
    echo         steady, and make sure it is in Linux containers mode
    echo         ^(right-click tray icon - "Switch to Linux containers"^).
    exit /b 1
)
echo Docker OK.

echo.
echo ============================================================
echo  [1/3] Compiling app\ into a single .so with Nuitka
echo ============================================================
if not exist "build" mkdir "build"
docker build %NOCACHE% -f docker\Dockerfile --target export --output type=local,dest=build .
if errorlevel 1 (
    echo [ERROR] Nuitka compilation failed. Scroll up for the compiler output.
    exit /b 1
)

echo.
echo Produced:
dir /b build\*.so
if errorlevel 1 (
    echo [ERROR] No .so was produced.
    exit /b 1
)

echo.
echo ============================================================
echo  [2/3] Building the runtime image ^(metalens-backend:nuitka^)
echo ============================================================
docker compose build %NOCACHE%
if errorlevel 1 (
    echo [ERROR] Runtime image build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  [3/3] Verifying the image has no application .py source
echo ============================================================
docker run --rm --entrypoint sh metalens-backend:nuitka -c "ls -la /app && echo '--- any app .py left? ---' && (find /app -name '*.py' -not -path '/app/alembic/*' | grep . && echo 'FOUND .py (unexpected)' || echo 'none - source is fully compiled')"

echo.
echo ============================================================
echo  Build complete.
echo.
echo    .so on this disk : build\
echo    runtime image    : metalens-backend:nuitka
echo.
echo  Start it with:   docker compose up -d
echo  Then open:       http://localhost:8000/docs
echo ============================================================
endlocal
