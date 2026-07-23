import 'package:flutter/material.dart';
import 'colors.dart';

class AppTheme {
  static ThemeData dark() => _build(AppColors.dark, Brightness.dark);
  static ThemeData light() => _build(AppColors.light, Brightness.light);

  static ThemeData _build(dynamic c, Brightness brightness) {
    final scheme = brightness == Brightness.dark
        ? ColorScheme.dark(
            primary: c.accent, secondary: c.accent, surface: c.surface,
            onPrimary: c.onAccent, error: c.danger)
        : ColorScheme.light(
            primary: c.accent, secondary: c.accent, surface: c.surface,
            onPrimary: c.onAccent, error: c.danger);

    return ThemeData(
      brightness: brightness,
      scaffoldBackgroundColor: c.bg,
      fontFamily: 'Inter',
      colorScheme: scheme,
      cardColor: c.bg2,
      appBarTheme: AppBarTheme(
        backgroundColor: c.bg2, foregroundColor: c.text, elevation: 0,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: TextStyle(color: c.text, fontSize: 17, fontWeight: FontWeight.w600, fontFamily: 'Inter'),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: c.bg2, elevation: 0,
        indicatorColor: c.accent.withOpacity(0.18),
        surfaceTintColor: Colors.transparent,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true, fillColor: c.bg3,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: c.border)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: c.border)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: c.accent, width: 2)),
        hintStyle: TextStyle(color: c.text3),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(style: ElevatedButton.styleFrom(
        backgroundColor: c.accent, foregroundColor: c.onAccent, elevation: 0,
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 15),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, fontFamily: 'Inter'),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      )),
      textButtonTheme: TextButtonThemeData(style: TextButton.styleFrom(foregroundColor: c.accent)),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: c.accent, foregroundColor: c.onAccent, elevation: 2,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      dividerTheme: DividerThemeData(color: c.border, thickness: 0.5, space: 0.5),
    );
  }
}
