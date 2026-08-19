#!/usr/bin/env bash
# Same as build.bat, for Git Bash / WSL / Linux / macOS.
#
#   ./build.sh          normal build
#   ./build.sh clean    ignore the Docker layer cache
set -euo pipefail

cd "$(dirname "$0")"

NOCACHE=""
if [ "${1:-}" = "clean" ]; then
    NOCACHE="--no-cache"
fi

export DOCKER_BUILDKIT=1

echo "============================================================"
echo " Checking Docker"
echo "============================================================"
if ! docker version >/dev/null 2>&1; then
    echo "[ERROR] Docker is not reachable. Start Docker Desktop and make"
    echo "        sure it is in Linux containers mode." >&2
    exit 1
fi
echo "Docker OK."

echo
echo "============================================================"
echo " [1/3] Compiling app/ into a single .so with Nuitka"
echo "============================================================"
mkdir -p build
docker build ${NOCACHE} -f docker/Dockerfile --target export --output type=local,dest=build .

echo
echo "Produced:"
ls -la build/*.so

echo
echo "============================================================"
echo " [2/3] Building the runtime image (metalens-backend:nuitka)"
echo "============================================================"
docker compose build ${NOCACHE}

echo
echo "============================================================"
echo " [3/3] Verifying the image has no application .py source"
echo "============================================================"
docker run --rm --entrypoint sh metalens-backend:nuitka -c \
  "ls -la /app && echo '--- any app .py left? ---' && (find /app -name '*.py' -not -path '/app/alembic/*' | grep . && echo 'FOUND .py (unexpected)' || echo 'none - source is fully compiled')"

echo
echo "============================================================"
echo " Build complete."
echo
echo "   .so on this disk : build/"
echo "   runtime image    : metalens-backend:nuitka"
echo
echo " Start it with:   docker compose up -d"
echo " Then open:       http://localhost:8000/docs"
echo "============================================================"
