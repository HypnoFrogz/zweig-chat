import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'dart:io';
import '../models/message.dart';
import '../api/api_client.dart';
import '../theme/colors.dart';
import 'avatar.dart';

class MessageBubble extends StatelessWidget {
  final Message message;
  final bool isMine;
  final bool showAvatar;
  final bool showName;
  final String? senderAvatar;
  final VoidCallback? onLongPress;
  final VoidCallback? onDoubleTap;
  final VoidCallback? onReply;
  final Function(String emoji)? onReaction;

  const MessageBubble({
    super.key,
    required this.message,
    required this.isMine,
    this.showAvatar = true,
    this.showName = true,
    this.senderAvatar,
    this.onLongPress,
    this.onDoubleTap,
    this.onReply,
    this.onReaction,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = theme.brightness == Brightness.dark ? AppColors.dark : AppColors.light;
    // On the own (solid accent) bubble, text sits on --on-accent; otherwise on surface.
    final Color bubbleText = isMine ? c.onAccent : c.text;
    final Color bubbleMeta = isMine ? c.onAccent.withOpacity(0.7) : c.text3;

    if (message.isSystem) {
      return _buildSystemMessage(context);
    }

    return Padding(
      padding: EdgeInsets.only(
        left: isMine ? 48 : 8,
        right: isMine ? 8 : 48,
        top: showName ? 8 : 2,
        bottom: 2,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        mainAxisAlignment: isMine ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isMine && showAvatar)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: UserAvatar(
                name: message.senderName ?? message.sender,
                avatarPath: senderAvatar,
                size: 32,
              ),
            )
          else if (!isMine)
            const SizedBox(width: 40),
          Flexible(
            child: GestureDetector(
              onLongPress: onLongPress,
              onDoubleTap: onDoubleTap,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: isMine ? c.accent : c.surface,
                  borderRadius: BorderRadius.only(
                    topLeft: const Radius.circular(16),
                    topRight: const Radius.circular(16),
                    bottomLeft: Radius.circular(isMine ? 16 : 4),
                    bottomRight: Radius.circular(isMine ? 4 : 16),
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (showName && !isMine)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 2),
                        child: Text(
                          message.senderName ?? message.sender,
                          style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                            color: theme.colorScheme.primary,
                          ),
                        ),
                      ),
                    // Reply preview
                    if (message.replyToPreview != null)
                      _buildReplyPreview(context),
                    // File attachment
                    if (message.isFile) _buildFileAttachment(context),
                    // Message text
                    if (message.text.isNotEmpty)
                      Text(
                        message.text,
                        style: TextStyle(
                          fontSize: 15,
                          height: 1.35,
                          color: bubbleText,
                        ),
                      ),
                    // Time + edited
                    const SizedBox(height: 4),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          _formatTime(message.timestamp),
                          style: TextStyle(fontSize: 11, color: bubbleMeta),
                        ),
                        if (message.isEdited)
                          Text(
                            ' (ред.)',
                            style: TextStyle(
                              fontSize: 11,
                              color: bubbleMeta,
                              fontStyle: FontStyle.italic,
                            ),
                          ),
                        if (isMine) ...[
                          const SizedBox(width: 4),
                          Icon(
                            message.readBy.length > 1 ? Icons.done_all : Icons.done,
                            size: 14,
                            color: message.readBy.length > 1
                                ? c.onAccent
                                : c.onAccent.withOpacity(0.6),
                          ),
                        ],
                      ],
                    ),
                    // Reactions
                    if (message.reactions.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Wrap(
                          spacing: 4,
                          runSpacing: 4,
                          children: message.reactions.map((r) {
                            return GestureDetector(
                              onTap: () => onReaction?.call(r.emoji),
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                decoration: BoxDecoration(
                                  color: isMine ? c.onAccent.withOpacity(0.15) : c.bg3,
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(
                                    color: isMine ? c.onAccent.withOpacity(0.25) : c.border,
                                    width: 0.5,
                                  ),
                                ),
                                child: Text(
                                  '${r.emoji} ${r.count}',
                                  style: TextStyle(fontSize: 13, color: bubbleText),
                                ),
                              ),
                            );
                          }).toList(),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildReplyPreview(BuildContext context) {
    final theme = Theme.of(context);
    final c = theme.brightness == Brightness.dark ? AppColors.dark : AppColors.light;
    final accentLine = isMine ? c.onAccent : c.accent;
    final bg = isMine ? c.onAccent.withOpacity(0.12) : c.accent.withOpacity(0.08);
    final subText = isMine ? c.onAccent.withOpacity(0.75) : c.text2;
    final preview = message.replyToPreview!;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        border: Border(left: BorderSide(color: accentLine, width: 3)),
        color: bg,
        borderRadius: const BorderRadius.only(
          topRight: Radius.circular(8),
          bottomRight: Radius.circular(8),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            preview['sender_name'] ?? preview['sender'] ?? '',
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: accentLine),
          ),
          Text(
            preview['text'] ?? '',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12, color: subText),
          ),
        ],
      ),
    );
  }

  Widget _buildFileAttachment(BuildContext context) {
    final theme = Theme.of(context);
    final file = message.file!;
    final fileName = file['original_name'] ?? file['name'] ?? 'file';
    final fileType = file['type']?.toString() ?? '';
    final isImage = fileType == 'image' || (file['mime_type'] ?? '').toString().startsWith('image/');
    
    if (isImage) {
      final filePath = file['url'] ?? file['path'] ?? '';
      final url = '${ApiClient.baseUrl.replaceAll('/api', '')}$filePath';
      return GestureDetector(
        onTap: () => _openFullScreenImage(context, url, fileName),
        child: Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.network(
              url,
              width: 250,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => _buildFileIcon(theme, fileName, 'image'),
            ),
          ),
        ),
      );
    }

    final isVideo = fileType == 'video';
    if (isVideo) {
      return GestureDetector(
        onTap: () {
          final filePath = file['url'] ?? file['path'] ?? '';
          final videoUrl = '${ApiClient.baseUrl.replaceAll('/api', '')}$filePath';
          _openFullScreenImage(context, videoUrl, fileName);
        },
        child: Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.play_circle_fill, size: 36, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Flexible(
                child: Text(fileName, style: TextStyle(fontSize: 13, color: theme.colorScheme.primary, decoration: TextDecoration.underline), overflow: TextOverflow.ellipsis),
              ),
            ],
          ),
        ),
      );
    }

    // Generic file — tappable, opens in browser / system handler
    final filePath = file['url'] ?? file['path'] ?? '';
    final fileUrl = '${ApiClient.baseUrl.replaceAll('/api', '')}$filePath';
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: GestureDetector(
        onTap: () => _openFileUrl(context, fileUrl, fileName),
        child: _buildFileIcon(theme, fileName, fileType),
      ),
    );
  }

  Future<void> _openFileUrl(BuildContext context, String url, String fileName) async {
    // Show progress snackbar
    ScaffoldMessengerState? messenger;
    if (context.mounted) {
      messenger = ScaffoldMessenger.of(context);
      messenger.showSnackBar(SnackBar(
        content: Row(children: [
          const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)),
          const SizedBox(width: 12),
          Text('Загрузка $fileName...'),
        ]),
        duration: const Duration(seconds: 30),
      ));
    }

    try {
      final dir = await getTemporaryDirectory();
      final filePath = '${dir.path}/$fileName';
      final file = File(filePath);

      // If already cached — open immediately
      if (!await file.exists()) {
        final token = ApiClient().token;
        await Dio().download(
          url,
          filePath,
          options: Options(headers: token != null ? {'Authorization': 'Bearer $token'} : {}),
        );
      }

      if (context.mounted) {
        messenger?.hideCurrentSnackBar();
        final result = await OpenFilex.open(filePath);
        if (result.type != ResultType.done && context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Не удалось открыть файл: ${result.message}')),
          );
        }
      }
    } catch (e) {
      if (context.mounted) {
        messenger?.hideCurrentSnackBar();
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Ошибка загрузки файла')),
        );
      }
    }
  }

  Widget _buildFileIcon(ThemeData theme, String fileName, [String fileType = '']) {
    IconData icon;
    Color color = theme.colorScheme.primary;
    switch (fileType) {
      case 'pdf':
        icon = Icons.picture_as_pdf;
        color = Colors.red.shade400;
        break;
      case 'document':
        icon = Icons.description;
        color = Colors.blue.shade400;
        break;
      case 'audio':
        icon = Icons.audiotrack;
        color = Colors.purple.shade400;
        break;
      case 'video':
        icon = Icons.videocam;
        color = Colors.orange.shade400;
        break;
      default:
        icon = Icons.insert_drive_file;
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 32, color: color),
        const SizedBox(width: 8),
        Flexible(
          child: Text(
            fileName,
            style: TextStyle(
              fontSize: 13,
              color: theme.colorScheme.primary,
              decoration: TextDecoration.underline,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  Widget _buildSystemMessage(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 32),
        child: Text(
          message.text,
          style: TextStyle(
            fontSize: 13,
            color: theme.colorScheme.onSurfaceVariant,
            fontStyle: FontStyle.italic,
          ),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }

  void _openFullScreenImage(BuildContext context, String url, String title) {
    Navigator.push(context, MaterialPageRoute(
      builder: (_) => _FullScreenImageView(url: url, title: title),
    ));
  }

  String _formatTime(String timestamp) {
    try {
      final dt = DateTime.parse(timestamp);
      return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }
}

class _FullScreenImageView extends StatelessWidget {
  final String url;
  final String title;

  const _FullScreenImageView({required this.url, required this.title});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(title, style: const TextStyle(fontSize: 16)),
      ),
      body: Center(
        child: InteractiveViewer(
          minScale: 0.5,
          maxScale: 4.0,
          child: Image.network(
            url,
            fit: BoxFit.contain,
            loadingBuilder: (_, child, progress) {
              if (progress == null) return child;
              return Center(
                child: CircularProgressIndicator(
                  value: progress.expectedTotalBytes != null
                      ? progress.cumulativeBytesLoaded / progress.expectedTotalBytes!
                      : null,
                  color: Colors.white,
                ),
              );
            },
            errorBuilder: (_, __, ___) => const Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.broken_image, color: Colors.white54, size: 64),
                SizedBox(height: 16),
                Text('Не удалось загрузить изображение', style: TextStyle(color: Colors.white54)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
