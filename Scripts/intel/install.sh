#!/usr/bin/env bash
# Sign the x86_64 app that CI built with a local certificate and install it, keeping what it replaces.
# It never launches the app or changes a privacy permission.
#
#   Scripts/intel/install.sh <run-id>                 download that run's app and install it
#   Scripts/intel/install.sh <artifact-directory>     install an already downloaded artifact
#   Scripts/intel/install.sh --rollback <backup-directory>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IDENTITY="${OMNIWM_SIGNING_IDENTITY:-OmniWM Intel}"
REPOSITORY="${OMNIWM_REPOSITORY:-shuheykoyama/OmniWM}"
APPLICATIONS="${OMNIWM_APPLICATIONS_DIR:-/Applications}"
BACKUPS="${OMNIWM_BACKUP_DIR:-$HOME/Library/Application Support/OmniWM Intel Backups}"
APP="$APPLICATIONS/OmniWM.app"
STAGED="$APPLICATIONS/.OmniWM.installing.app"
# Settings and state, relative to the home directory.
SETTINGS=(.config/omniwm .local/state/omniwm)

fail() {
  echo "install: $*" >&2
  exit 1
}

require_stopped() {
  if pgrep -x OmniWM >/dev/null; then
    fail "OmniWM is running; quit it first"
  fi
}

# Move the installed app aside and copy the settings, so that a rollback can bring both back.
back_up() {
  local directory path
  mkdir -p "$BACKUPS"
  directory="$(mktemp -d "$BACKUPS/$(date +%Y%m%d-%H%M%S).XXXX")"
  for path in "${SETTINGS[@]}"; do
    if [ -e "$HOME/$path" ]; then
      ditto "$HOME/$path" "$directory/$path"
    fi
  done
  if [ -e "$APP" ]; then
    mv "$APP" "$directory/OmniWM.app"
  fi
  echo "$directory"
}

stage() {
  rm -rf "$STAGED"
  ditto "$1" "$STAGED"
  codesign --verify --deep --strict "$STAGED"
}

rollback() {
  local source="$1" replaced path
  [ -d "$source/OmniWM.app" ] || fail "$source holds no OmniWM.app"
  require_stopped
  stage "$source/OmniWM.app"
  replaced="$(back_up)"
  mv "$STAGED" "$APP"
  for path in "${SETTINGS[@]}"; do
    if [ -e "$source/$path" ]; then
      # The settings being replaced are already copied into the new backup.
      rm -rf "${HOME:?}/$path"
      ditto "$source/$path" "$HOME/$path"
    fi
  done
  echo "Restored the app and settings from $source. What they replaced is in $replaced."
}

# Not local: the cleanup trap runs after the function has returned.
work=""

install_artifact() {
  local source="$1" artifact app replaced target
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  if [ ! -d "$source" ]; then
    [[ "$source" =~ ^[0-9]+$ ]] || fail "expected a run id or an artifact directory, got: $source"
    gh run download "$source" --repo "$REPOSITORY" --name omniwm-x86_64 --dir "$work/download"
    source="$work/download"
  fi
  artifact="$source/intel-app"
  [ -f "$artifact/SHA256SUMS" ] || artifact="$source"
  [ -f "$artifact/SHA256SUMS" ] || fail "no SHA256SUMS in $source"
  (cd "$artifact" && shasum -a 256 -c SHA256SUMS) || fail "the archive does not match its checksum"
  ditto -x -k "$artifact/OmniWM-x86_64.zip" "$work/app"
  app="$work/app/OmniWM.app"
  codesign --verify --deep --strict "$app"
  [ "$(lipo -archs "$app/Contents/MacOS/OmniWM")" = x86_64 ] || fail "the app is not x86_64"

  security find-identity -v -p codesigning | grep -qF "\"$IDENTITY\"" ||
    fail "no code signing identity named \"$IDENTITY\"; see CONTRIBUTING.md, Optional Signing Certificate"
  # Inside out, with the hardened runtime and entitlements of the published build. A stable identity keeps
  # the granted permissions across updates.
  for target in "$app/Contents/Frameworks/libOmniWMNowPlaying.dylib" "$app/Contents/MacOS/omniwmctl"; do
    codesign --force --options runtime --sign "$IDENTITY" "$target"
  done
  for target in "$app/Contents/MacOS/OmniWM" "$app"; do
    codesign --force --options runtime --entitlements "$ROOT/OmniWM.entitlements" --sign "$IDENTITY" "$target"
  done

  require_stopped
  stage "$app"
  replaced="$(back_up)"
  mv "$STAGED" "$APP"
  if [ -f "$artifact/provenance.json" ]; then
    cp "$artifact/provenance.json" "$replaced/installed-provenance.json"
  fi
  echo "Installed $APP. What it replaced is in $replaced."
  echo "Start it with: open \"$APP\""
}

case "${1:-}" in
  --rollback)
    [ "$#" = 2 ] || fail "usage: install.sh --rollback <backup-directory>"
    rollback "$2"
    ;;
  "" | -*)
    fail "usage: install.sh <run-id | artifact-directory> | --rollback <backup-directory>"
    ;;
  *)
    [ "$#" = 1 ] || fail "usage: install.sh <run-id | artifact-directory>"
    install_artifact "$1"
    ;;
esac
