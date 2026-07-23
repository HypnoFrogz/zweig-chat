import 'package:flutter/material.dart';
import '../../services/diagnostics_service.dart';

class PermissionsScreen extends StatefulWidget {
  /// If true, shows a "Skip" button and a brief intro header
  final bool isOnboarding;

  const PermissionsScreen({super.key, this.isOnboarding = false});

  @override
  State<PermissionsScreen> createState() => _PermissionsScreenState();
}

class _PermissionsScreenState extends State<PermissionsScreen> {
  List<DiagnosticItem>? _items;
  bool _loading = true;
  final Map<String, bool> _fixing = {};

  @override
  void initState() {
    super.initState();
    _runChecks();
  }

  Future<void> _runChecks() async {
    setState(() => _loading = true);
    final items = await DiagnosticsService().runAll();
    if (mounted) setState(() { _items = items; _loading = false; });
  }

  Future<void> _fix(DiagnosticItem item) async {
    if (item.fix == null) return;
    setState(() => _fixing[item.id] = true);
    await item.fix!();
    // Re-run checks after attempting the fix
    await _runChecks();
    setState(() => _fixing.remove(item.id));
  }

  // ─── UI helpers ───────────────────────────────────────────────────────────

  Color _statusColor(DiagnosticStatus s, ColorScheme cs) => switch (s) {
    DiagnosticStatus.ok      => Colors.green,
    DiagnosticStatus.warning => Colors.orange,
    DiagnosticStatus.error   => cs.error,
  };

  IconData _statusIcon(DiagnosticStatus s) => switch (s) {
    DiagnosticStatus.ok      => Icons.check_circle_rounded,
    DiagnosticStatus.warning => Icons.warning_amber_rounded,
    DiagnosticStatus.error   => Icons.cancel_rounded,
  };

  String _statusLabel(DiagnosticStatus s) => switch (s) {
    DiagnosticStatus.ok      => 'OK',
    DiagnosticStatus.warning => 'Внимание',
    DiagnosticStatus.error   => 'Требуется',
  };

  // ─── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final items = _items;

    final errorCount   = items?.where((i) => i.status == DiagnosticStatus.error).length   ?? 0;
    final warningCount = items?.where((i) => i.status == DiagnosticStatus.warning).length ?? 0;
    final allOk        = items != null && errorCount == 0 && warningCount == 0;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Разрешения'),
        actions: [
          if (!_loading)
            IconButton(
              onPressed: _runChecks,
              icon: const Icon(Icons.refresh_rounded),
              tooltip: 'Перепроверить',
            ),
          if (widget.isOnboarding)
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Пропустить'),
            ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : CustomScrollView(
              slivers: [
                // ── Summary banner ──
                SliverToBoxAdapter(
                  child: _SummaryBanner(
                    allOk: allOk,
                    errorCount: errorCount,
                    warningCount: warningCount,
                    isOnboarding: widget.isOnboarding,
                  ),
                ),

                // ── Item list ──
                SliverList.separated(
                  itemCount: items!.length,
                  separatorBuilder: (_, __) => const Divider(height: 1, indent: 72),
                  itemBuilder: (ctx, i) {
                    final item = items[i];
                    final color = _statusColor(item.status, cs);
                    final fixing = _fixing[item.id] == true;

                    return ListTile(
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 8),
                      leading: Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: color.withOpacity(0.12),
                          shape: BoxShape.circle,
                        ),
                        child: Icon(_statusIcon(item.status),
                            color: color, size: 24),
                      ),
                      title: Row(
                        children: [
                          Expanded(
                            child: Text(item.title,
                                style: theme.textTheme.titleSmall?.copyWith(
                                    fontWeight: FontWeight.w600)),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: color.withOpacity(0.12),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Text(
                              _statusLabel(item.status),
                              style: TextStyle(
                                  fontSize: 11,
                                  color: color,
                                  fontWeight: FontWeight.w600),
                            ),
                          ),
                        ],
                      ),
                      subtitle: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const SizedBox(height: 4),
                          Text(item.description,
                              style: theme.textTheme.bodySmall?.copyWith(
                                  color: cs.onSurfaceVariant)),
                          if (item.fix != null) ...[
                            const SizedBox(height: 10),
                            SizedBox(
                              height: 34,
                              child: FilledButton.tonal(
                                onPressed: fixing ? null : () => _fix(item),
                                style: FilledButton.styleFrom(
                                  backgroundColor: color.withOpacity(0.15),
                                  foregroundColor: color,
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 16),
                                  textStyle: const TextStyle(
                                      fontSize: 13,
                                      fontWeight: FontWeight.w600),
                                ),
                                child: fixing
                                    ? SizedBox(
                                        width: 16,
                                        height: 16,
                                        child: CircularProgressIndicator(
                                            strokeWidth: 2, color: color))
                                    : Text(item.fixLabel),
                              ),
                            ),
                          ],
                        ],
                      ),
                    );
                  },
                ),

                // ── Bottom padding ──
                const SliverToBoxAdapter(child: SizedBox(height: 32)),
              ],
            ),

      // ── "All done" bottom button for onboarding ──
      bottomNavigationBar: (widget.isOnboarding && allOk)
          ? SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
                child: FilledButton.icon(
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.check_rounded),
                  label: const Text('Всё готово!'),
                  style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(50)),
                ),
              ),
            )
          : null,
    );
  }
}

// ─── Summary banner widget ───────────────────────────────────────────────────

class _SummaryBanner extends StatelessWidget {
  final bool allOk;
  final int errorCount;
  final int warningCount;
  final bool isOnboarding;

  const _SummaryBanner({
    required this.allOk,
    required this.errorCount,
    required this.warningCount,
    required this.isOnboarding,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    if (allOk) {
      return Container(
        margin: const EdgeInsets.all(16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.green.withOpacity(0.1),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.green.withOpacity(0.3)),
        ),
        child: Row(
          children: [
            const Icon(Icons.verified_rounded, color: Colors.green, size: 32),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Всё настроено!',
                      style: theme.textTheme.titleSmall?.copyWith(
                          color: Colors.green,
                          fontWeight: FontWeight.bold)),
                  const SizedBox(height: 2),
                  Text('Приложение будет работать корректно.',
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: Colors.green.shade700)),
                ],
              ),
            ),
          ],
        ),
      );
    }

    final problems = <String>[];
    if (errorCount > 0) problems.add('$errorCount критичных');
    if (warningCount > 0) problems.add('$warningCount предупреждений');
    final summary = problems.join(', ');

    return Container(
      margin: const EdgeInsets.all(16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: (errorCount > 0 ? cs.error : Colors.orange).withOpacity(0.1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
            color: (errorCount > 0 ? cs.error : Colors.orange).withOpacity(0.4)),
      ),
      child: Row(
        children: [
          Icon(
            errorCount > 0
                ? Icons.error_outline_rounded
                : Icons.warning_amber_rounded,
            color: errorCount > 0 ? cs.error : Colors.orange,
            size: 32,
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  isOnboarding
                      ? 'Нужно настроить приложение'
                      : 'Найдено проблем: $summary',
                  style: theme.textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 2),
                Text(
                  'Исправьте отмеченные пункты для стабильной работы уведомлений и звонков.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: cs.onSurfaceVariant),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
