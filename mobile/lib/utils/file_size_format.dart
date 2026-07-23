String formatFileSize(int? bytes) {
  if (bytes == null || bytes == 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  int i = 0; double size = bytes.toDouble();
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return '${size.toStringAsFixed(size < 10 ? 1 : 0)} ${units[i]}';
}
