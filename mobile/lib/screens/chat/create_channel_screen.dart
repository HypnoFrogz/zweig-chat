import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/auth_provider.dart';
import '../../providers/channels_provider.dart';
import '../../api/channels_api.dart';
import '../../widgets/toast.dart';

class CreateChannelScreen extends ConsumerStatefulWidget {
  const CreateChannelScreen({super.key});
  @override
  ConsumerState<CreateChannelScreen> createState() => _CreateChannelScreenState();
}

class _CreateChannelScreenState extends ConsumerState<CreateChannelScreen> {
  final _nameController = TextEditingController();
  final _descController = TextEditingController();
  String _channelType = 'public';
  bool _loading = false;

  @override
  void dispose() {
    _nameController.dispose();
    _descController.dispose();
    super.dispose();
  }

  Future<void> _createChannel() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) return;

    setState(() => _loading = true);
    try {
      await ChannelsApi().createChannel({
        'name': name,
        'description': _descController.text.trim(),
        'type': _channelType,
      });
      await ref.read(channelsProvider.notifier).loadChannels();
      if (mounted) {
        Toast.success(context, ref.read(authProvider.notifier).t('toast.channelCreated'));
        Navigator.pop(context);
      }
    } catch (e) {
      if (mounted) Toast.show(context, 'Ошибка создания канала', isError: true);
    }
    setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final t = ref.read(authProvider.notifier).t;

    return Scaffold(
      appBar: AppBar(
        title: Text(t('newchat.channel')),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _nameController,
              decoration: InputDecoration(
                labelText: t('channel.name'),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _descController,
              maxLines: 3,
              decoration: InputDecoration(
                labelText: t('channel.desc'),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 16),
            // Channel type selector
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'public', icon: Icon(Icons.tag), label: Text('Публичный')),
                ButtonSegment(value: 'private', icon: Icon(Icons.lock), label: Text('Приватный')),
              ],
              selected: {_channelType},
              onSelectionChanged: (v) => setState(() => _channelType = v.first),
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton(
                onPressed: _loading ? null : _createChannel,
                child: _loading
                    ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : Text(t('channel.createBtn')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
