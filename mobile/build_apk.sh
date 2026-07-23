#!/usr/bin/env bash
# Build the Nebenan Android APK pointed at a specific server.
#
#   ./build_apk.sh http://192.168.1.113        # LAN testing
#   ./build_apk.sh https://chat.example.com     # production domain
#
# The server URL is baked into the APK at build time (the mobile app has no
# runtime server field yet). Rebuild whenever the server address changes.
set -e

SERVER="${1:-}"
if [ -z "$SERVER" ]; then
  echo "Usage: ./build_apk.sh <server-url>"
  echo "  e.g. ./build_apk.sh http://192.168.1.113"
  exit 1
fi
SERVER="${SERVER%/}"   # strip trailing slash

cd "$(dirname "$0")"

# Firebase (Android push) config. If you don't have a real one, a placeholder
# is used so the build succeeds — push notifications are simply disabled.
if [ ! -f android/app/google-services.json ]; then
  cp android/app/google-services.json.example android/app/google-services.json
  echo "[i] No google-services.json — using placeholder (push disabled)."
fi

flutter pub get
flutter build apk --release --dart-define=API_BASE_URL="$SERVER/api"

echo ""
echo "Done. APK: mobile/build/app/outputs/flutter-apk/app-release.apk"
echo "Server:   $SERVER"
