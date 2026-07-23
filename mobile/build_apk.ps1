# Build the Nebenan Android APK pointed at a specific server (Windows PowerShell).
#
#   .\build_apk.ps1 http://192.168.1.113        # LAN testing
#   .\build_apk.ps1 https://chat.example.com     # production domain
#
# The server URL is baked into the APK at build time. Rebuild when it changes.
param([Parameter(Mandatory=$true)][string]$Server)

$ErrorActionPreference = "Stop"
$Server = $Server.TrimEnd('/')
Set-Location $PSScriptRoot

if (-not (Test-Path "android/app/google-services.json")) {
    Copy-Item "android/app/google-services.json.example" "android/app/google-services.json"
    Write-Host "[i] No google-services.json - using placeholder (push disabled)."
}

flutter pub get
flutter build apk --release --dart-define="API_BASE_URL=$Server/api"

Write-Host ""
Write-Host "Done. APK: mobile/build/app/outputs/flutter-apk/app-release.apk"
Write-Host "Server:   $Server"
