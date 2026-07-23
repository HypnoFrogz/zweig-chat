import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_picker/file_picker.dart';
import '../theme/colors.dart';

class MessageInput extends StatefulWidget {
  final Function(String text) onSend;
  final Function(String filePath, String fileName)? onAttach;
  final Function(bool isTyping)? onTyping;
  final String? replyToName;
  final VoidCallback? onCancelReply;
  final String placeholder;

  /// Members available for @-mentions. Each map should contain at least
  /// 'username' and (optionally) 'display_name'. Selecting a suggestion
  /// inserts @login into the text.
  final List<Map<String, dynamic>> mentionCandidates;

  const MessageInput({
    super.key,
    required this.onSend,
    this.onAttach,
    this.onTyping,
    this.replyToName,
    this.onCancelReply,
    this.placeholder = 'Напишите сообщение...',
    this.mentionCandidates = const [],
  });

  @override
  State<MessageInput> createState() => _MessageInputState();
}

class _MessageInputState extends State<MessageInput> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  bool _isTyping = false;

  // @-mention state
  List<Map<String, dynamic>> _mentionMatches = [];
  int _mentionStart = -1; // index of the '@' being edited, -1 when inactive

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _handleTextChanged(String text) {
    final typing = text.trim().isNotEmpty;
    if (typing != _isTyping) {
      _isTyping = typing;
      widget.onTyping?.call(typing);
    }
    _updateMentions();
  }

  /// Detects an in-progress "@query" at the cursor and updates suggestions.
  void _updateMentions() {
    if (widget.mentionCandidates.isEmpty) return;
    final sel = _controller.selection;
    final text = _controller.text;
    if (!sel.isValid || sel.baseOffset != sel.extentOffset) {
      _clearMentions();
      return;
    }
    final caret = sel.baseOffset;
    final before = text.substring(0, caret);
    // '@' must be at start or preceded by whitespace; query has no spaces/@.
    final match = RegExp(r'(?:^|\s)@([^\s@]*)$').firstMatch(before);
    if (match == null) {
      _clearMentions();
      return;
    }
    final query = match.group(1)!.toLowerCase();
    final atIndex = caret - query.length - 1;
    final matches = widget.mentionCandidates.where((m) {
      final uname = (m['username'] ?? '').toString().toLowerCase();
      final dname = (m['display_name'] ?? '').toString().toLowerCase();
      if (uname.isEmpty) return false;
      if (query.isEmpty) return true;
      return uname.contains(query) || dname.contains(query);
    }).take(8).toList();
    if (matches.isEmpty) {
      _clearMentions();
      return;
    }
    setState(() {
      _mentionMatches = matches;
      _mentionStart = atIndex;
    });
  }

  void _clearMentions() {
    if (_mentionStart == -1 && _mentionMatches.isEmpty) return;
    setState(() {
      _mentionMatches = [];
      _mentionStart = -1;
    });
  }

  /// Replaces the in-progress "@query" with "@login ".
  void _selectMention(Map<String, dynamic> member) {
    final uname = (member['username'] ?? '').toString();
    if (uname.isEmpty || _mentionStart < 0) {
      _clearMentions();
      return;
    }
    final text = _controller.text;
    final caret = _controller.selection.baseOffset;
    final safeCaret = (caret >= 0 && caret <= text.length) ? caret : text.length;
    final insert = '@$uname ';
    final newText = text.substring(0, _mentionStart) + insert + text.substring(safeCaret);
    final newCaret = _mentionStart + insert.length;
    _controller.value = TextEditingValue(
      text: newText,
      selection: TextSelection.collapsed(offset: newCaret),
    );
    _clearMentions();
  }

  void _send() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    widget.onSend(text);
    _controller.clear();
    _isTyping = false;
    widget.onTyping?.call(false);
  }

  void _showAttachMenu() {
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: 8),
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Theme.of(ctx).dividerColor,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(height: 8),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('Медиа'),
              subtitle: const Text('Фото и видео из галереи'),
              onTap: () {
                Navigator.pop(ctx);
                _pickMedia();
              },
            ),
            ListTile(
              leading: const Icon(Icons.folder_open),
              title: const Text('Файлы'),
              subtitle: const Text('Выбрать файл из хранилища'),
              onTap: () {
                Navigator.pop(ctx);
                _pickFile();
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _pickMedia() async {
    final picker = ImagePicker();
    // Show choice: photo or video
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: 8),
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Theme.of(ctx).dividerColor,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(height: 8),
            ListTile(
              leading: const Icon(Icons.image),
              title: const Text('Фото'),
              onTap: () async {
                Navigator.pop(ctx);
                final files = await picker.pickMultiImage(imageQuality: 85);
                for (final f in files) {
                  widget.onAttach?.call(f.path, f.name);
                }
              },
            ),
            ListTile(
              leading: const Icon(Icons.videocam),
              title: const Text('Видео'),
              onTap: () async {
                Navigator.pop(ctx);
                final video = await picker.pickVideo(source: ImageSource.gallery);
                if (video != null) {
                  widget.onAttach?.call(video.path, video.name);
                }
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _pickFile() async {
    final result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result != null) {
      for (final f in result.files) {
        if (f.path != null) widget.onAttach?.call(f.path!, f.name);
      }
    }
  }

  Widget _buildMentionList(dynamic c) {
    return Container(
      constraints: const BoxConstraints(maxHeight: 220),
      decoration: BoxDecoration(
        color: c.bg2,
        border: Border(top: BorderSide(color: c.border, width: 0.5)),
      ),
      child: ListView.builder(
        shrinkWrap: true,
        padding: EdgeInsets.zero,
        itemCount: _mentionMatches.length,
        itemBuilder: (_, i) {
          final m = _mentionMatches[i];
          final uname = (m['username'] ?? '').toString();
          final dname = (m['display_name'] ?? '').toString();
          final title = dname.isNotEmpty ? dname : uname;
          return ListTile(
            dense: true,
            leading: CircleAvatar(
              radius: 16,
              backgroundColor: c.accent,
              child: Text(
                title.isNotEmpty ? title[0].toUpperCase() : '?',
                style: TextStyle(color: c.onAccent, fontSize: 13, fontWeight: FontWeight.bold),
              ),
            ),
            title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(color: c.text, fontSize: 14)),
            subtitle: Text('@$uname', maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(color: c.text2, fontSize: 12)),
            onTap: () => _selectMention(m),
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = theme.brightness == Brightness.dark ? AppColors.dark : AppColors.light;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.replyToName != null)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: c.bg2,
            child: Row(
              children: [
                Icon(Icons.reply_rounded, size: 18, color: c.accent),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Ответ для ${widget.replyToName}',
                    style: TextStyle(fontSize: 12.5, color: c.accent),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                GestureDetector(
                  onTap: widget.onCancelReply,
                  child: Icon(Icons.close_rounded, size: 18, color: c.text2),
                ),
              ],
            ),
          ),
        if (_mentionMatches.isNotEmpty) _buildMentionList(c),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
          decoration: BoxDecoration(
            color: c.bg2,
            border: Border(top: BorderSide(color: c.border, width: 0.5)),
          ),
          child: SafeArea(
            top: false,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                IconButton(
                  icon: const Icon(Icons.attach_file_rounded),
                  onPressed: _showAttachMenu,
                  color: c.text2,
                  tooltip: 'Прикрепить',
                ),
                Expanded(
                  child: TextField(
                    controller: _controller,
                    focusNode: _focusNode,
                    onChanged: _handleTextChanged,
                    onSubmitted: (_) => _send(),
                    maxLines: 5,
                    minLines: 1,
                    textCapitalization: TextCapitalization.sentences,
                    style: TextStyle(color: c.text, fontSize: 15),
                    decoration: InputDecoration(
                      hintText: widget.placeholder,
                      hintStyle: TextStyle(color: c.text3),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(22),
                        borderSide: BorderSide.none,
                      ),
                      filled: true,
                      fillColor: c.bg3,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 6),
                Material(
                  color: c.accent,
                  shape: const CircleBorder(),
                  child: InkWell(
                    customBorder: const CircleBorder(),
                    onTap: _send,
                    child: Padding(
                      padding: const EdgeInsets.all(9),
                      child: Icon(Icons.send_rounded, size: 20, color: c.onAccent),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
