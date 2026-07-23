#!/usr/bin/env bash
# Build the Nebenan iOS app pointed at a specific server.
# REQUIRES macOS with Xcode + CocoaPods — cannot be built on Windows/Linux.
#
#   ./build_ios.sh https://chat.example.com
#
# Notes:
#   - Bundle id: ru.nebenan.app, display name: Nebenan (already configured).
#   - For a device build you must set your Apple signing team in Xcode
#     (open ios/Runner.xcworkspace) or pass --export-options-plist.
#   - iOS Firebase push needs a real ios/Runner/GoogleService-Info.plist
#     (a .example placeholder is committed).
set -e

SERVER="${1:-}"
if [ -z "$SERVER" ]; then
  echo "Usage: ./build_ios.sh <server-url>   e.g. ./build_ios.sh https://chat.example.com"
  exit 1
fi
SERVER="${SERVER%/}"

cd "$(dirname "$0")"

if [ ! -f ios/Runner/GoogleService-Info.plist ]; then
  cp ios/Runner/GoogleService-Info.plist.example ios/Runner/GoogleService-Info.plist
  echo "[i] No GoogleService-Info.plist — using placeholder (push disabled)."
fi

flutter pub get
# Unsigned archive for inspection; add signing in Xcode for a real .ipa:
flutter build ios --release --no-codesign --dart-define=API_BASE_URL="$SERVER/api"

echo ""
echo "Built (unsigned). Open ios/Runner.xcworkspace in Xcode to sign & archive."
echo "Server: $SERVER"
