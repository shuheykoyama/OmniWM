#!/usr/bin/env bash
# Make Rosetta available and show that x86_64 processes, xctest among them, can run translated.
set -euo pipefail

fail() {
  echo "rosetta: $*" >&2
  exit 1
}

slices_of() {
  local slices
  slices="$(lipo -archs "$1")" || fail "cannot read the architectures of $1"
  echo "$slices"
}

[ "$(uname -m)" = arm64 ] || fail "expected an Apple silicon host"
if ! arch -x86_64 /usr/bin/true 2>/dev/null; then
  echo "installing Rosetta"
  sudo softwareupdate --install-rosetta --agree-to-license
fi

translated="$(arch -x86_64 /usr/sbin/sysctl -n sysctl.proc_translated)"
echo "host architecture: $(uname -m)"
echo "translated shell: $(arch -x86_64 /usr/bin/uname -m), proc_translated=$translated"
[ "$translated" = 1 ] || fail "x86_64 processes are not running under Rosetta"

xctest="$(xcrun --sdk macosx --find xctest)"
slices="$(slices_of "$xctest")"
echo "xctest slices: $slices ($xctest)"
case " $slices " in
  *" x86_64 "*) ;;
  *) fail "$xctest has no x86_64 slice, so it cannot load the test bundle" ;;
esac

# Recorded only: the tests are listed and selected without SwiftPM's helper.
helper="$(dirname "$(xcrun --find swift-build)")/../libexec/swift/pm/swiftpm-xctest-helper"
if [ -e "$helper" ]; then
  echo "swiftpm-xctest-helper slices: $(slices_of "$helper") ($helper)"
else
  echo "swiftpm-xctest-helper: absent ($helper)"
fi
