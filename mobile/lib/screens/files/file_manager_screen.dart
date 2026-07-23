import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_picker/file_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:flutter_pdfview/flutter_pdfview.dart';
import 'package:share_plus/share_plus.dart';
import '../../providers/auth_provider.dart';
import '../../api/api_client.dart';
import '../../widgets/toast.dart';
import '../../utils/file_size_format.dart';

class FileManagerScreen extends ConsumerStatefulWidget {
  const FileManagerScreen({super.key});
  @override
  ConsumerState<FileManagerScreen> createState() => _FileManagerScreenState();
}

class _FileManagerScreenState extends ConsumerState<FileManagerScreen> {
  String _currentPath = '/';
  List<Map<String, dynamic>> _items = [];
  bool _loading = false;
  final _api = ApiClient();

  @override
  void initState() {
    super.initState();
    _loadFiles();
  }

  Future<void> _loadFiles() async {
    setState(() => _loading = true);
    try {
      final res = await _api.dio.get('/files', queryParameters: {'path': _currentPath});
      setState(() {
        _items = (res.data['items'] as List).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (e) {
      setState(() => _loading = false);
      if (mounted) Toast.show(context, 'Ошибка загрузки файлов', isError: true);
    }
  }

  void _navigateTo(String folderName) {
    setState(() {
      _currentPath = _currentPath.endsWith('/')
          ? '$_currentPath$folderName'
          : '$_currentPath/$folderName';
    });
    _loadFiles();
  }

  void _navigateBack() {
    if (_currentPath == '/') return;
    final parts = _currentPath.split('/');
    parts.removeLast();
    setState(() {
      _currentPath = parts.isEmpty ? '/' : parts.join('/');
      if (_currentPath.isEmpty) _currentPath = '/';
    });
    _loadFiles();
  }

  Future<void> _uploadMedia() async {
    final picker = ImagePicker();
    final files = await picker.pickMultiImage(imageQuality: 85);
    if (files.isEmpty) return;

    setState(() => _loading = true);
    try {
      const batchSize = 5;
      for (var i = 0; i < files.length; i += batchSize) {
        final batch = files.skip(i).take(batchSize).toList();
        final formData = FormData.fromMap({
          'path': _currentPath,
          'files': await Future.wait(batch.map((f) => MultipartFile.fromFile(f.path, filename: f.name))),
        });
        await _api.dio.post('/upload', data: formData);
      }
      await _loadFiles();
      if (mounted) Toast.success(context, 'Загружено');
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка загрузки', isError: true);
    }
    setState(() => _loading = false);
  }

  Future<void> _uploadFile() async {
    final result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result == null || result.files.isEmpty) return;

    setState(() => _loading = true);
    try {
      const batchSize = 5;
      final files = result.files.where((f) => f.path != null).toList();
      for (var i = 0; i < files.length; i += batchSize) {
        final batch = files.skip(i).take(batchSize).toList();
        final formData = FormData.fromMap({
          'path': _currentPath,
          'files': await Future.wait(batch.map((f) => MultipartFile.fromFile(f.path!, filename: f.name))),
        });
        await _api.dio.post('/upload', data: formData);
      }
      await _loadFiles();
      if (mounted) Toast.success(context, 'Загружено');
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка загрузки', isError: true);
    }
    setState(() => _loading = false);
  }

  Future<void> _createFolder() async {
    final controller = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Новая папка'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'Название папки'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()), child: const Text('Создать')),
        ],
      ),
    );
    if (name == null || name.isEmpty) return;

    try {
      await _api.dio.post('/files/mkdir', data: {'path': _currentPath, 'name': name});
      await _loadFiles();
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка', isError: true);
    }
  }

  Future<void> _deleteItem(Map<String, dynamic> item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Удалить?'),
        content: Text('Удалить "${item['name']}"?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Отмена')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Удалить'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      final filePath = _currentPath.endsWith('/')
          ? '$_currentPath${item['name']}'
          : '$_currentPath/${item['name']}';
      await _api.dio.delete('/files', data: {'path': filePath});
      await _loadFiles();
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка удаления', isError: true);
    }
  }

  String _itemPath(Map<String, dynamic> item) {
    return _currentPath.endsWith('/')
        ? '$_currentPath${item['name']}'
        : '$_currentPath/${item['name']}';
  }

  Future<void> _renameItem(Map<String, dynamic> item) async {
    final controller = TextEditingController(text: item['name'] ?? '');
    final newName = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Переименовать'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()), child: const Text('Сохранить')),
        ],
      ),
    );
    if (newName == null || newName.isEmpty || newName == item['name']) return;
    try {
      await _api.dio.put('/files/rename', data: {'path': _itemPath(item), 'new_name': newName});
      await _loadFiles();
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка', isError: true);
    }
  }

  Future<void> _copyItem(Map<String, dynamic> item) async {
    try {
      await _api.dio.post('/files/copy', data: {'path': _itemPath(item)});
      await _loadFiles();
      if (mounted) Toast.success(context, 'Скопировано');
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка копирования', isError: true);
    }
  }

  Future<void> _shareToChat(Map<String, dynamic> item) async {
    try {
      await _api.dio.post('/files/share-to-chat', data: {'path': _itemPath(item)});
      if (mounted) Toast.success(context, 'Готово к отправке в чат');
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка', isError: true);
    }
  }

  Future<void> _shareItem(Map<String, dynamic> item) async {
    final isDir = item['is_dir'] == true;
    final name = item['name'] ?? '';

    if (isDir) {
      // For directories: download all files inside and share them
      try {
        if (mounted) Toast.show(context, 'Подготовка файлов...');
        final dirPath = _itemPath(item);
        // Fetch files in the folder
        final res = await _api.dio.get('/files', queryParameters: {'path': dirPath});
        final items = (res.data['items'] as List).cast<Map<String, dynamic>>();
        final files = items.where((f) => f['is_dir'] != true).toList();

        if (files.isEmpty) {
          if (mounted) Toast.show(context, 'Папка пуста или содержит только подпапки');
          return;
        }

        // Download files to temp
        final dir = await getTemporaryDirectory();
        final xFiles = <XFile>[];
        for (final file in files) {
          final filePath = dirPath.endsWith('/') ? '$dirPath${file['name']}' : '$dirPath/${file['name']}';
          final savePath = '${dir.path}/${file['name']}';
          await _api.dio.download(
            '/files/download',
            savePath,
            queryParameters: {'path': filePath},
          );
          xFiles.add(XFile(savePath));
        }

        await Share.shareXFiles(xFiles, subject: name);
      } catch (e) {
        if (mounted) Toast.show(context, 'Ошибка расшаривания', isError: true);
      }
    } else {
      // For files: download and share
      try {
        if (mounted) Toast.show(context, 'Подготовка...');
        final savePath = await _downloadToTemp(_itemPath(item), name);
        await Share.shareXFiles([XFile(savePath)], subject: name);
      } catch (e) {
        if (mounted) Toast.show(context, 'Ошибка расшаривания', isError: true);
      }
    }
  }

  Future<String> _downloadToTemp(String path, String name) async {
    final dir = await getTemporaryDirectory();
    final savePath = '${dir.path}/$name';
    await _api.dio.download(
      '/files/download',
      savePath,
      queryParameters: {'path': path},
    );
    return savePath;
  }

  Future<void> _openFile(Map<String, dynamic> item) async {
    final path = item['path'] ?? _itemPath(item);
    final name = item['name'] ?? '';
    final ext = name.split('.').last.toLowerCase();

    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].contains(ext)) {
      // Image preview
      final imageUrl = '${ApiClient.baseUrl}/files/download?path=${Uri.encodeComponent(path)}';
      if (!mounted) return;
      showDialog(
        context: context,
        builder: (ctx) => Dialog(
          backgroundColor: Colors.black,
          insetPadding: const EdgeInsets.all(16),
          child: Stack(
            children: [
              InteractiveViewer(
                child: Image.network(
                  imageUrl,
                  headers: {'Authorization': 'Bearer ${_api.token}'},
                  fit: BoxFit.contain,
                  errorBuilder: (_, __, ___) => const Center(
                    child: Text('Не удалось загрузить', style: TextStyle(color: Colors.white)),
                  ),
                ),
              ),
              Positioned(
                top: 8, right: 8,
                child: IconButton(
                  icon: const Icon(Icons.close, color: Colors.white),
                  onPressed: () => Navigator.pop(ctx),
                ),
              ),
            ],
          ),
        ),
      );
    } else if (ext == 'pdf') {
      // PDF preview
      try {
        if (mounted) Toast.show(context, 'Загрузка PDF...');
        final savePath = await _downloadToTemp(path, name);
        if (!mounted) return;
        Navigator.push(context, MaterialPageRoute(
          builder: (_) => _PdfViewScreen(filePath: savePath, title: name),
        ));
      } catch (e) {
        if (mounted) Toast.show(context, 'Ошибка открытия PDF', isError: true);
      }
    } else if (['txt', 'md', 'csv', 'json', 'xml', 'yaml', 'yml', 'log', 'py', 'js', 'dart', 'html', 'css'].contains(ext)) {
      // Text file preview
      try {
        if (mounted) Toast.show(context, 'Загрузка...');
        final savePath = await _downloadToTemp(path, name);
        final content = await File(savePath).readAsString();
        if (!mounted) return;
        Navigator.push(context, MaterialPageRoute(
          builder: (_) => _TextViewScreen(content: content, title: name),
        ));
      } catch (e) {
        if (mounted) Toast.show(context, 'Ошибка открытия файла', isError: true);
      }
    } else {
      // Other files: download and open externally
      try {
        if (mounted) Toast.show(context, 'Скачивание...');
        final savePath = await _downloadToTemp(path, name);
        final uri = Uri.file(savePath);
        if (await canLaunchUrl(uri)) {
          await launchUrl(uri);
        } else {
          final webUrl = Uri.parse(
            '${ApiClient.baseUrl}/files/download?path=${Uri.encodeComponent(path)}',
          );
          await launchUrl(webUrl, mode: LaunchMode.externalApplication);
        }
      } catch (e) {
        if (mounted) Toast.show(context, 'Ошибка открытия файла', isError: true);
      }
    }
  }

  void _showItemMenu(Map<String, dynamic> item) {
    final isDir = item['is_dir'] == true;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.edit),
              title: const Text('Переименовать'),
              onTap: () { Navigator.pop(ctx); _renameItem(item); },
            ),
            if (!isDir) ...[
              ListTile(
                leading: const Icon(Icons.copy),
                title: const Text('Копировать'),
                onTap: () { Navigator.pop(ctx); _copyItem(item); },
              ),
              ListTile(
                leading: const Icon(Icons.chat),
                title: const Text('Поделиться в чат'),
                onTap: () { Navigator.pop(ctx); _shareToChat(item); },
              ),
            ],
            ListTile(
              leading: const Icon(Icons.share),
              title: Text(isDir ? 'Поделиться папкой' : 'Поделиться'),
              onTap: () { Navigator.pop(ctx); _shareItem(item); },
            ),
            ListTile(
              leading: Icon(Icons.delete, color: Theme.of(context).colorScheme.error),
              title: Text('Удалить', style: TextStyle(color: Theme.of(context).colorScheme.error)),
              onTap: () { Navigator.pop(ctx); _deleteItem(item); },
            ),
          ],
        ),
      ),
    );
  }

  IconData _getFileIcon(String name) {
    final ext = name.split('.').last.toLowerCase();
    switch (ext) {
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'webp':
        return Icons.image;
      case 'mp4':
      case 'mov':
      case 'avi':
      case 'mkv':
        return Icons.videocam;
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'aac':
        return Icons.audiotrack;
      case 'pdf':
        return Icons.picture_as_pdf;
      case 'doc':
      case 'docx':
        return Icons.description;
      case 'xls':
      case 'xlsx':
        return Icons.table_chart;
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return Icons.archive;
      default:
        return Icons.insert_drive_file;
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = ref.read(authProvider.notifier).t;
    final theme = Theme.of(context);

    // Build breadcrumb
    final pathParts = _currentPath.split('/').where((p) => p.isNotEmpty).toList();

    return Scaffold(
      appBar: AppBar(
        title: Text(t('settings.files')),
        actions: [
          IconButton(icon: const Icon(Icons.create_new_folder), onPressed: _createFolder, tooltip: 'Создать папку'),
        ],
      ),
      body: Column(
        children: [
          // Breadcrumb navigation
          if (pathParts.isNotEmpty)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              width: double.infinity,
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: () {
                        setState(() => _currentPath = '/');
                        _loadFiles();
                      },
                      child: Icon(Icons.home, size: 20, color: theme.colorScheme.primary),
                    ),
                    ...pathParts.asMap().entries.map((entry) {
                      return Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.chevron_right, size: 18, color: theme.colorScheme.onSurfaceVariant),
                          GestureDetector(
                            onTap: () {
                              final newPath = '/${pathParts.sublist(0, entry.key + 1).join('/')}';
                              setState(() => _currentPath = newPath);
                              _loadFiles();
                            },
                            child: Text(
                              entry.value,
                              style: TextStyle(
                                color: entry.key == pathParts.length - 1
                                    ? theme.colorScheme.onSurface
                                    : theme.colorScheme.primary,
                                fontWeight: entry.key == pathParts.length - 1 ? FontWeight.bold : FontWeight.normal,
                              ),
                            ),
                          ),
                        ],
                      );
                    }),
                  ],
                ),
              ),
            ),
          // File list
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _items.isEmpty
                    ? Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.folder_open, size: 64, color: theme.colorScheme.onSurfaceVariant),
                            const SizedBox(height: 16),
                            Text(t('files.empty'), style: theme.textTheme.bodyLarge),
                          ],
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: _loadFiles,
                        child: ListView.builder(
                          itemCount: (_currentPath != '/' ? 1 : 0) + _items.length,
                          itemBuilder: (context, index) {
                            // Back button
                            if (_currentPath != '/' && index == 0) {
                              return ListTile(
                                leading: const Icon(Icons.arrow_back),
                                title: const Text('..'),
                                onTap: _navigateBack,
                              );
                            }
                            final itemIndex = _currentPath != '/' ? index - 1 : index;
                            final item = _items[itemIndex];
                            final isDir = item['is_dir'] == true;
                            final name = item['name'] ?? '';

                            return ListTile(
                              leading: Icon(
                                isDir ? Icons.folder : _getFileIcon(name),
                                color: isDir ? Colors.amber.shade700 : theme.colorScheme.primary,
                                size: 32,
                              ),
                              title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
                              subtitle: !isDir && item['size'] != null
                                  ? Text(formatFileSize(item['size']), style: theme.textTheme.bodySmall)
                                  : null,
                              onTap: isDir ? () => _navigateTo(name) : () => _openFile(item),
                              onLongPress: () => _showItemMenu(item),
                            );
                          },
                        ),
                      ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          showModalBottomSheet(
            context: context,
            builder: (ctx) => SafeArea(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  ListTile(
                    leading: const Icon(Icons.photo_library),
                    title: const Text('Фото / Видео'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _uploadMedia();
                    },
                  ),
                  ListTile(
                    leading: const Icon(Icons.attach_file),
                    title: const Text('Файл'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _uploadFile();
                    },
                  ),
                  ListTile(
                    leading: const Icon(Icons.create_new_folder),
                    title: const Text('Новая папка'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _createFolder();
                    },
                  ),
                ],
              ),
            ),
          );
        },
        child: const Icon(Icons.add),
      ),
    );
  }
}


class _PdfViewScreen extends StatefulWidget {
  final String filePath;
  final String title;
  const _PdfViewScreen({required this.filePath, required this.title});

  @override
  State<_PdfViewScreen> createState() => _PdfViewScreenState();
}

class _PdfViewScreenState extends State<_PdfViewScreen> {
  int _totalPages = 0;
  int _currentPage = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title, overflow: TextOverflow.ellipsis),
        actions: [
          if (_totalPages > 0)
            Center(
              child: Padding(
                padding: const EdgeInsets.only(right: 16),
                child: Text('${_currentPage + 1} / $_totalPages',
                    style: const TextStyle(fontSize: 14)),
              ),
            ),
        ],
      ),
      body: PDFView(
        filePath: widget.filePath,
        enableSwipe: true,
        swipeHorizontal: false,
        autoSpacing: true,
        pageFling: true,
        onRender: (pages) => setState(() => _totalPages = pages ?? 0),
        onPageChanged: (page, _) => setState(() => _currentPage = page ?? 0),
      ),
    );
  }
}


class _TextViewScreen extends StatelessWidget {
  final String content;
  final String title;
  const _TextViewScreen({required this.content, required this.title});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title, overflow: TextOverflow.ellipsis)),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: SelectableText(
          content,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 13, height: 1.5),
        ),
      ),
    );
  }
}
