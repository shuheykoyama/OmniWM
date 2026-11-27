#!/usr/bin/env bash
# Make Rosetta available, show which architecture each part of a test run uses, and export the
# pinned toolchain's swift so a caller can start it translated.
set -euo pipefail

fail() {
  echo "rosetta: $*" >&2
  exit 1
}

[ "$(uname -m)" = arm64 ] || fail "expected an Apple silicon host"
if ! arch -x86_64 /usr/bin/true 2>/dev/null; then
  echo "installing Rosetta"
  sudo softwareupdate --install-rosetta --agree-to-license
fi

resources="$(swift -print-target-info | jq -er .paths.runtimeResourcePath)"
swift_bin="${resources%/lib/swift}/bin"
translated="$(arch -x86_64 /usr/sbin/sysctl -n sysctl.proc_translated)"

echo "host architecture: $(uname -m)"
echo "translated shell: $(arch -x86_64 /usr/bin/uname -m), proc_translated=$translated"
[ "$translated" = 1 ] || fail "x86_64 processes are not running under Rosetta"
# SwiftPM lists tests with the helper next to its own binaries, and falls back to Xcode's.
helper="$swift_bin/../libexec/swift/pm/swiftpm-xctest-helper"
[ -e "$helper" ] || helper="$(dirname "$(xcrun --find swift-build)")/../libexec/swift/pm/swiftpm-xctest-helper"
for tool in "$swift_bin/swift" "$swift_bin/swift-package" "$helper" "$(xcrun --find xctest)"; do
  slices="$(lipo -archs "$tool")" || fail "cannot read the architectures of $tool"
  echo "$(basename "$tool") slices: $slices ($tool)"
  case " $slices " in
    *" x86_64 "*) ;;
    *) fail "$tool has no x86_64 slice, so it cannot run translated" ;;
  esac
done
# SwiftPM starts xctest itself, so it has to run translated for xctest to load an x86_64 bundle.
arch -x86_64 "$swift_bin/swift" --version

if [ -n "${GITHUB_ENV:-}" ]; then
  echo "OMNIWM_X86_SWIFT=$swift_bin/swift" >>"$GITHUB_ENV"
fi
