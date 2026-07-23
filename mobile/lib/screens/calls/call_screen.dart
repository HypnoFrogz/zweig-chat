import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'dart:async';
import 'dart:io';
import '../../api/calls_api.dart';
import '../../widgets/user_picker_sheet.dart';
import '../../widgets/toast.dart';

class CallScreen extends StatefulWidget {
  final String token;
  final String url;
  final String roomName;
  final String? callId;
  final CallsApi? callsApi;
  final String channelName;
  /// Если задан — это групповой звонок в канале. Выход = покинуть комнату,
  /// звонок продолжается для остальных участников.
  final String? groupChannelSlug;

  const CallScreen({
    super.key,
    required this.token,
    required this.url,
    required this.roomName,
    this.callId,
    this.callsApi,
    required this.channelName,
    this.groupChannelSlug,
  });

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  Room? _room;
  LocalParticipant? _localParticipant;
  final List<RemoteParticipant> _remoteParticipants = [];
  bool _connecting = true;
  bool _cameraEnabled = false;
  bool _micEnabled = true;
  bool _speakerOn = false;
  bool _isFrontCamera = true;
  String? _error;
  Timer? _timer;
  int _seconds = 0;
  bool _controlsVisible = true;

  @override
  void initState() {
    super.initState();
    WakelockPlus.enable(); // не давать экрану гаснуть во время звонка
    _connect();
  }

  @override
  void dispose() {
    WakelockPlus.disable(); // снимаем блокировку сна при выходе из звонка
    _timer?.cancel();
    _room?.disconnect();
    _room?.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    try {
      // permission_handler не поддерживает macOS/Windows/Linux — запрашиваем только на мобильных
      if (!kIsWeb && (Platform.isAndroid || Platform.isIOS)) {
        final micStatus = await Permission.microphone.request();
        await Permission.camera.request();
        if (!micStatus.isGranted) {
          if (mounted) {
            setState(() {
              _connecting = false;
              _error = 'Необходимо разрешение на микрофон для звонка';
            });
          }
          return;
        }
      }

      var url = widget.url;
      if (!url.startsWith('ws://') && !url.startsWith('wss://')) {
        url = 'wss://$url';
      }

      // Audio-first default: use normal earpiece route, not loud speaker.
      await Hardware.instance.setSpeakerphoneOn(false);

      final room = Room();
      room.addListener(_onRoomEvent);

      await room.connect(url, widget.token, roomOptions: const RoomOptions(
        adaptiveStream: true,
        dynacast: true,
        defaultAudioPublishOptions: AudioPublishOptions(
          dtx: true,
        ),
        defaultVideoPublishOptions: VideoPublishOptions(
          simulcast: true,
        ),
        defaultAudioCaptureOptions: AudioCaptureOptions(
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
        ),
      ));

      await room.localParticipant?.setCameraEnabled(false);
      await room.localParticipant?.setMicrophoneEnabled(true);

      if (mounted) {
        setState(() {
          _room = room;
          _localParticipant = room.localParticipant;
          _remoteParticipants.clear();
          _remoteParticipants.addAll(room.remoteParticipants.values);
          _connecting = false;
        });
      }

      _timer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() => _seconds++);
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _connecting = false;
          _error = e.toString();
        });
      }
    }
  }

  void _onRoomEvent() {
    if (_room == null || !mounted) return;
    setState(() {
      _remoteParticipants.clear();
      _remoteParticipants.addAll(_room!.remoteParticipants.values);
    });
  }

  void _toggleCamera() async {
    if (_localParticipant == null) return;
    _cameraEnabled = !_cameraEnabled;
    await _localParticipant!.setCameraEnabled(_cameraEnabled);
    if (widget.callId != null && widget.callsApi != null) {
      widget.callsApi!.updateMedia(
        callId: widget.callId!,
        videoEnabled: _cameraEnabled,
        speakerEnabled: _speakerOn,
      ).catchError((_) {});
    }
    if (mounted) setState(() {});
  }

  void _toggleMic() async {
    if (_localParticipant == null) return;
    _micEnabled = !_micEnabled;
    await _localParticipant!.setMicrophoneEnabled(_micEnabled);
    if (mounted) setState(() {});
  }

  void _toggleSpeaker() async {
    _speakerOn = !_speakerOn;
    await Hardware.instance.setSpeakerphoneOn(_speakerOn);
    if (widget.callId != null && widget.callsApi != null) {
      widget.callsApi!.updateMedia(
        callId: widget.callId!,
        videoEnabled: _cameraEnabled,
        speakerEnabled: _speakerOn,
      ).catchError((_) {});
    }
    if (mounted) setState(() {});
  }

  void _flipCamera() async {
    if (_localParticipant == null || !_cameraEnabled) return;
    final pubs = _localParticipant!.videoTrackPublications
        .where((p) => p.source == TrackSource.camera && p.track != null);
    if (pubs.isEmpty) return;
    final track = pubs.first.track as LocalVideoTrack;
    try {
      await track.setCameraPosition(
        _isFrontCamera ? CameraPosition.back : CameraPosition.front,
      );
      if (mounted) setState(() => _isFrontCamera = !_isFrontCamera);
    } catch (_) {}
  }

  void _hangUp() {
    // Групповой звонок: просто покидаем комнату, для остальных звонок продолжается.
    // Адресный (1:1) звонок: завершаем полностью.
    if (widget.groupChannelSlug == null &&
        widget.callId != null &&
        widget.callsApi != null) {
      widget.callsApi!.endCall(widget.callId!).catchError((_) {});
    }
    _room?.disconnect();
    if (mounted) Navigator.pop(context);
  }

  String _formatTime(int s) {
    final m = (s ~/ 60).toString().padLeft(2, '0');
    final sec = (s % 60).toString().padLeft(2, '0');
    return '$m:$sec';
  }

  /// Пригласить новых людей в идущий групповой звонок.
  Future<void> _inviteToCall() async {
    final slug = widget.groupChannelSlug;
    if (slug == null || widget.callsApi == null) return;
    // Исключаем тех, кто уже в комнате
    final inCall = <String>{
      if (_localParticipant != null) _localParticipant!.identity,
      ..._remoteParticipants.map((p) => p.identity),
    };
    final selected = await showUserPickerSheet(
      context,
      title: 'Пригласить в звонок',
      confirmLabel: 'Пригласить',
      excludeUsernames: inCall,
    );
    if (selected == null || selected.isEmpty) return;
    try {
      await widget.callsApi!.inviteToCall(slug, selected.toList());
      if (mounted) Toast.show(context, 'Приглашено: ${selected.length}');
    } catch (e) {
      if (mounted) Toast.show(context, 'Не удалось пригласить', isError: true);
    }
  }

  VideoTrack? _getVideoTrack(Participant p) {
    final pub = p.videoTrackPublications
        .where((t) => t.track != null && t.source == TrackSource.camera)
        .firstOrNull;
    return pub?.track as VideoTrack?;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0d0d1a),
      body: SafeArea(
        top: false,
        child: _connecting
            ? _buildConnecting()
            : _error != null
                ? _buildError()
                : _buildCallUI(),
      ),
    );
  }

  // ─── Connecting ───

  Widget _buildConnecting() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(
            width: 48, height: 48,
            child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
          ),
          const SizedBox(height: 20),
          const Text('Подключение...', style: TextStyle(color: Colors.white, fontSize: 18)),
          const SizedBox(height: 8),
          Text(widget.channelName, style: const TextStyle(color: Colors.white54, fontSize: 14)),
        ],
      ),
    );
  }

  // ─── Error ───

  Widget _buildError() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, color: Colors.red, size: 56),
            const SizedBox(height: 16),
            const Text('Ошибка подключения', style: TextStyle(color: Colors.white, fontSize: 18)),
            const SizedBox(height: 8),
            Text(_error!, style: const TextStyle(color: Colors.white54, fontSize: 13), textAlign: TextAlign.center),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => Navigator.pop(context),
              style: ElevatedButton.styleFrom(backgroundColor: Colors.white24),
              child: const Text('Закрыть', style: TextStyle(color: Colors.white)),
            ),
          ],
        ),
      ),
    );
  }

  // ─── Main Call UI ───

  Widget _buildCallUI() {
    final hasRemote = _remoteParticipants.isNotEmpty;
    final isOneOnOne = _remoteParticipants.length == 1;

    return GestureDetector(
      onTap: hasRemote ? () => setState(() => _controlsVisible = !_controlsVisible) : null,
      child: Stack(
        fit: StackFit.expand,
        children: [
          // Background / video area
          if (!hasRemote)
            _buildWaitingBackground()
          else if (isOneOnOne)
            _buildRemoteFullscreen(_remoteParticipants.first)
          else
            _buildGrid(),

          // Waiting overlay text
          if (!hasRemote)
            Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 72, height: 72,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: Colors.white.withOpacity(0.1),
                    ),
                    child: const Icon(Icons.person_outline, color: Colors.white70, size: 36),
                  ),
                  const SizedBox(height: 16),
                  Text(widget.channelName, style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w500)),
                  const SizedBox(height: 8),
                  const Text('Ожидание собеседника...', style: TextStyle(color: Colors.white54, fontSize: 14)),
                  const SizedBox(height: 4),
                  Text(_formatTime(_seconds), style: const TextStyle(color: Colors.white38, fontSize: 13)),
                ],
              ),
            ),

          // Local PIP for 1:1
          if (isOneOnOne && _cameraEnabled && _localParticipant != null)
            Positioned(
              right: 16,
              bottom: 150,
              child: _buildLocalPip(),
            ),

          // Top bar
          if (_controlsVisible || !hasRemote)
            Positioned(
              top: 0, left: 0, right: 0,
              child: _buildTopBar(),
            ),

          // Bottom controls
          if (_controlsVisible || !hasRemote)
            Positioned(
              bottom: 0, left: 0, right: 0,
              child: _buildBottomControls(),
            ),
        ],
      ),
    );
  }

  // ─── Background when waiting ───

  Widget _buildWaitingBackground() {
    if (_cameraEnabled && _localParticipant != null) {
      final track = _getVideoTrack(_localParticipant!);
      if (track != null) {
        return VideoTrackRenderer(track, fit: VideoViewFit.cover, mirrorMode: VideoViewMirrorMode.mirror);
      }
    }
    return Container(color: const Color(0xFF0d0d1a));
  }

  // ─── Remote fullscreen (1:1) ───

  Widget _buildRemoteFullscreen(RemoteParticipant remote) {
    final track = _getVideoTrack(remote);
    final name = remote.name.isNotEmpty ? remote.name : remote.identity.toString();

    if (track != null) {
      return VideoTrackRenderer(track, fit: VideoViewFit.cover, mirrorMode: VideoViewMirrorMode.off);
    }

    // No video — show avatar
    return Container(
      color: const Color(0xFF0d0d1a),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircleAvatar(
              radius: 48,
              backgroundColor: const Color(0xFF2d2d44),
              child: Text(
                name.isNotEmpty ? name[0].toUpperCase() : '?',
                style: const TextStyle(fontSize: 36, color: Colors.white),
              ),
            ),
            const SizedBox(height: 12),
            Text(name, style: const TextStyle(color: Colors.white, fontSize: 18)),
          ],
        ),
      ),
    );
  }

  // ─── Local PIP ───

  Widget _buildLocalPip() {
    final track = _getVideoTrack(_localParticipant!);
    if (track == null) return const SizedBox.shrink();

    return Container(
      width: 100, height: 140,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Colors.black45, width: 2),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.5), blurRadius: 10, offset: const Offset(0, 4))],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: VideoTrackRenderer(track, fit: VideoViewFit.cover, mirrorMode: VideoViewMirrorMode.mirror),
      ),
    );
  }

  // ─── Grid (group call) ───

  Widget _buildGrid() {
    final all = <Participant>[if (_localParticipant != null) _localParticipant!, ..._remoteParticipants];

    return GridView.builder(
      padding: const EdgeInsets.all(4),
      gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: all.length <= 1 ? 1 : 2,
        mainAxisSpacing: 4, crossAxisSpacing: 4,
        childAspectRatio: all.length <= 2 ? 3 / 4 : 1,
      ),
      itemCount: all.length,
      itemBuilder: (_, i) {
        final p = all[i];
        final track = _getVideoTrack(p);
        final isLocal = p is LocalParticipant;
        final name = p.name.isNotEmpty ? p.name : p.identity.toString();

        return ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Container(
            color: const Color(0xFF1a1a2e),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (track != null)
                  VideoTrackRenderer(track, fit: VideoViewFit.cover, mirrorMode: isLocal ? VideoViewMirrorMode.mirror : VideoViewMirrorMode.off)
                else
                  Center(
                    child: CircleAvatar(
                      radius: 32, backgroundColor: const Color(0xFF2d2d44),
                      child: Text(name.isNotEmpty ? name[0].toUpperCase() : '?', style: const TextStyle(fontSize: 24, color: Colors.white)),
                    ),
                  ),
                Positioned(
                  bottom: 6, left: 6,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                    decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
                    child: Text(isLocal ? 'Вы' : name, style: const TextStyle(color: Colors.white, fontSize: 11)),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  // ─── Top Bar ───

  Widget _buildTopBar() {
    final topPad = MediaQuery.of(context).padding.top;
    return Container(
      padding: EdgeInsets.only(top: topPad + 8, left: 16, right: 16, bottom: 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter, end: Alignment.bottomCenter,
          colors: [Colors.black.withOpacity(0.6), Colors.transparent],
        ),
      ),
      child: Row(
        children: [
          // Back / hang up
          GestureDetector(
            onTap: _hangUp,
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(color: Colors.white.withOpacity(0.15), borderRadius: BorderRadius.circular(10)),
              child: const Icon(Icons.arrow_back_ios_new, color: Colors.white, size: 16),
            ),
          ),
          const SizedBox(width: 12),
          // Channel name + timer
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(widget.channelName, style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Container(width: 6, height: 6, decoration: const BoxDecoration(shape: BoxShape.circle, color: Colors.green)),
                    const SizedBox(width: 5),
                    Text(_formatTime(_seconds), style: const TextStyle(color: Colors.white60, fontSize: 12)),
                  ],
                ),
              ],
            ),
          ),
          // Пригласить в звонок (только групповой)
          if (widget.groupChannelSlug != null) ...[
            GestureDetector(
              onTap: _inviteToCall,
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: Colors.white.withOpacity(0.15), borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.person_add_alt_1, color: Colors.white, size: 20),
              ),
            ),
            const SizedBox(width: 10),
          ],
          // Flip camera
          if (_cameraEnabled)
            GestureDetector(
              onTap: _flipCamera,
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: Colors.white.withOpacity(0.15), borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.cameraswitch_rounded, color: Colors.white, size: 20),
              ),
            ),
        ],
      ),
    );
  }

  // ─── Bottom Controls ───

  Widget _buildBottomControls() {
    final bottomPad = MediaQuery.of(context).padding.bottom;
    return Container(
      padding: EdgeInsets.only(top: 20, bottom: bottomPad + 20, left: 20, right: 20),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter, end: Alignment.topCenter,
          colors: [Colors.black.withOpacity(0.7), Colors.transparent],
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          // Mic
          _controlBtn(
            icon: _micEnabled ? Icons.mic_rounded : Icons.mic_off_rounded,
            active: _micEnabled,
            onTap: _toggleMic,
          ),
          // Camera
          _controlBtn(
            icon: _cameraEnabled ? Icons.videocam_rounded : Icons.videocam_off_rounded,
            active: _cameraEnabled,
            onTap: _toggleCamera,
          ),
          // Hang up
          GestureDetector(
            onTap: _hangUp,
            child: Container(
              width: 60, height: 60,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: const LinearGradient(colors: [Color(0xFFFF4444), Color(0xFFCC0000)]),
                boxShadow: [BoxShadow(color: Colors.red.withOpacity(0.4), blurRadius: 12, offset: const Offset(0, 4))],
              ),
              child: const Icon(Icons.call_end_rounded, color: Colors.white, size: 28),
            ),
          ),
          // Speaker
          _controlBtn(
            icon: _speakerOn ? Icons.volume_up_rounded : Icons.hearing_rounded,
            active: _speakerOn,
            onTap: _toggleSpeaker,
          ),
          // Flip
          _controlBtn(
            icon: Icons.cameraswitch_rounded,
            active: true,
            onTap: _flipCamera,
            enabled: _cameraEnabled,
          ),
        ],
      ),
    );
  }

  Widget _controlBtn({required IconData icon, required bool active, required VoidCallback onTap, bool enabled = true}) {
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Container(
        width: 50, height: 50,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: !enabled
              ? Colors.white.withOpacity(0.05)
              : !active
                  ? Colors.white.withOpacity(0.3)
                  : Colors.white.withOpacity(0.15),
          border: Border.all(
            color: !enabled ? Colors.white10 : Colors.white.withOpacity(active ? 0.2 : 0.4),
            width: 1.5,
          ),
        ),
        child: Icon(icon, color: enabled ? Colors.white : Colors.white24, size: 22),
      ),
    );
  }
}
