import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'app.dart';
import 'api/api_client.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Load the saved server address (if any) before the first frame so routing
  // can decide between the server-address screen and login. Guarded with a
  // timeout so a slow storage read never freezes the splash.
  try {
    await ApiClient().loadServer().timeout(const Duration(seconds: 4));
  } catch (_) {}

  SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
  ]);
  runApp(const ProviderScope(child: NebenanApp()));
}
