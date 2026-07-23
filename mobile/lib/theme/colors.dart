import 'package:flutter/material.dart';

/// Nebenan brand palette — warm dual theme.
/// Forest and Amber swap accent/status roles between dark and light.
abstract class AppPalette {
  Color get bg;
  Color get bg2;
  Color get bg3;
  Color get surface;
  Color get border;
  Color get text;
  Color get text2;
  Color get text3;
  Color get accent;
  Color get accentHover;
  Color get onAccent;
  Color get danger;
  Color get success;
  Color get warning;
  Color get notification;
  Color get online;
  Color get sidebarBg;
  Color get sidebarActive;
}

class AppColors {
  static const AppPalette dark = _DarkColors();
  static const AppPalette light = _LightColors();

  static AppPalette of(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark ? dark : light;
}

class _DarkColors implements AppPalette {
  const _DarkColors();
  @override Color get bg => const Color(0xFF33302C);
  @override Color get bg2 => const Color(0xFF3A3631);
  @override Color get bg3 => const Color(0xFF423D37);
  @override Color get surface => const Color(0xFF47423B);
  @override Color get border => const Color(0xFF544E45);
  @override Color get text => const Color(0xFFF0E8DC);
  @override Color get text2 => const Color(0xFFBCAF9D);
  @override Color get text3 => const Color(0xFF8A7D6B);
  @override Color get accent => const Color(0xFF3A5A48); // Forest
  @override Color get accentHover => const Color(0xFF486B56);
  @override Color get onAccent => const Color(0xFFFFFFFF);
  @override Color get danger => const Color(0xFFC0392B);
  @override Color get success => const Color(0xFF5F9576);
  @override Color get warning => const Color(0xFFE8A93F);
  @override Color get notification => const Color(0xFFE8A93F); // Amber
  @override Color get online => const Color(0xFF5F9576);
  @override Color get sidebarBg => const Color(0xFF2B2823);
  @override Color get sidebarActive => const Color(0xFF3A5A48);
}

class _LightColors implements AppPalette {
  const _LightColors();
  @override Color get bg => const Color(0xFFF5EEE3); // Cream
  @override Color get bg2 => const Color(0xFFEFE7D8);
  @override Color get bg3 => const Color(0xFFE8DDC9);
  @override Color get surface => const Color(0xFFEFE7D8);
  @override Color get border => const Color(0xFFE1D4BD);
  @override Color get text => const Color(0xFF33302C); // Charcoal
  @override Color get text2 => const Color(0xFF6D6459);
  @override Color get text3 => const Color(0xFF9C9184);
  @override Color get accent => const Color(0xFFE0A458); // Amber
  @override Color get accentHover => const Color(0xFFD0923F);
  @override Color get onAccent => const Color(0xFF33302C);
  @override Color get danger => const Color(0xFFB23A2A);
  @override Color get success => const Color(0xFF3A5A48);
  @override Color get warning => const Color(0xFFCF8A2A);
  @override Color get notification => const Color(0xFFCF8A2A);
  @override Color get online => const Color(0xFF3A5A48); // Forest
  @override Color get sidebarBg => const Color(0xFF33302C);
  @override Color get sidebarActive => const Color(0xFF3A5A48);
}
