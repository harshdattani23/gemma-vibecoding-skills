#!/bin/sh
# Wrapper for POSIX-like systems with readlink and test -h support.
set -eu

SOURCE=$0
SYMLINK_HOPS=0
while [ -h "$SOURCE" ]; do
  SYMLINK_HOPS=$((SYMLINK_HOPS + 1))
  if [ "$SYMLINK_HOPS" -gt 40 ]; then
    echo "gemma-coder: symlink resolution exceeded 40 hops" >&2
    exit 2
  fi
  LINK_DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
  TARGET=$(readlink "$SOURCE")
  case "$TARGET" in
    /*) SOURCE=$TARGET ;;
    *) SOURCE=$LINK_DIR/$TARGET ;;
  esac
done
DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)

case "${1:-help}" in
  worker|generate)
    shift
    exec python3 "$DIR/scripts/gemma_worker.py" "$@"
    ;;
  batch)
    shift
    exec python3 "$DIR/scripts/gemma_batch.py" "$@"
    ;;
  setup|configure)
    shift
    exec python3 "$DIR/scripts/setup.py" "$@"
    ;;
  help|--help|-h)
    printf '%s\n' \
      'gemma-coder — an agent plans, a configured model writes code' \
      '' \
      'Commands:' \
      '  gemma-coder worker --task SPEC --out FILE [options]' \
      '  gemma-coder batch --manifest tasks/manifest.json [options]' \
      '  gemma-coder setup [--list | --save MODEL]' \
      '' \
      "Documentation: $DIR/README.md"
    ;;
  *)
    echo "Unknown command: $1" >&2
    echo "Usage: gemma-coder {worker|batch|setup|help}" >&2
    exit 2
    ;;
esac
