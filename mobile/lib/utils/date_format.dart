import '../i18n/translations.dart';

String formatMessageDate(String isoDate, String lang) {
  final date = DateTime.tryParse(isoDate);
  if (date == null) return '';
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final msgDay = DateTime(date.year, date.month, date.day);
  if (msgDay == today) return Translations.t('msg.today', lang);
  if (msgDay == today.subtract(const Duration(days: 1))) return Translations.t('msg.yesterday', lang);
  return '${date.day}.${date.month.toString().padLeft(2, '0')}.${date.year}';
}

String formatMessageTime(String isoDate) {
  final date = DateTime.tryParse(isoDate);
  if (date == null) return '';
  final local = date.toLocal();
  return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
}
