#!/usr/bin/env bash
# Full verification: Roblox type-check (luau-lsp + Roblox API definitions),
# Lune unit tests, and a Rojo build of the place file.
#
#   bash scripts/check.sh                 # check everything
#   bash scripts/check.sh src/server/Services/RunnerService.luau ...   # type-check only these files
#   SPEC_FILTER=economy bash scripts/check.sh ...                      # only run tests/*economy*.spec.luau
#
# Needs: rojo, lune, luau-lsp on PATH. Downloads Roblox type definitions once.
set -uo pipefail
cd "$(dirname "$0")/.."

DEFS=tools/globalTypes.d.luau
if [ ! -s "$DEFS" ]; then
  mkdir -p tools
  curl -sSfL -o "$DEFS" https://raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.d.luau || { echo "could not download $DEFS"; exit 2; }
fi

SM=$(mktemp --suffix=.json)
trap 'rm -f "$SM"' EXIT
rojo sourcemap default.project.json -o "$SM" >/dev/null || { echo "rojo sourcemap failed"; exit 2; }

if [ "$#" -gt 0 ]; then TARGETS=("$@"); else TARGETS=(src); fi

echo "== luau-lsp analyze (${TARGETS[*]})"
OUT=$(luau-lsp analyze --sourcemap="$SM" --definitions=@roblox="$DEFS" --no-strict-dm-types "${TARGETS[@]}" 2>&1 | grep -v -E '^\[(INFO|WARN)\]')
if [ -n "$OUT" ]; then
  echo "$OUT"
  ERRORS=$(echo "$OUT" | grep -c -E 'TypeError|SyntaxError|Error' || true)
  echo "-- $ERRORS problem line(s)"
  TYPE_OK=0
else
  echo "-- no problems"
  TYPE_OK=1
fi

echo "== lune tests"
lune run tests/run ${SPEC_FILTER:-}; TEST_RC=$?

echo "== rojo build"
mkdir -p build
TMP_BUILD=$(mktemp --suffix=.rbxlx)
rojo build default.project.json -o "$TMP_BUILD" >/dev/null; BUILD_RC=$?
if [ "$BUILD_RC" = 0 ]; then
  # Pre-build the static map into the place so it is visible in Studio's editor.
  lune run scripts/bake "$TMP_BUILD" || echo "-- WARNING: map bake failed (the map will still be built when the game starts)"
  mv -f "$TMP_BUILD" build/ArmyRunTycoon.rbxlx; chmod 644 build/ArmyRunTycoon.rbxlx; echo "-- built build/ArmyRunTycoon.rbxlx"
else
  rm -f "$TMP_BUILD"
fi

if [ "$TYPE_OK" = 1 ] && [ "$TEST_RC" = 0 ] && [ "$BUILD_RC" = 0 ]; then
  echo "ALL CHECKS PASSED"; exit 0
fi
echo "CHECKS FAILED (types_ok=$TYPE_OK tests_rc=$TEST_RC build_rc=$BUILD_RC)"; exit 1
