// Zweig Service Worker — Push Notifications + Badge

const CACHE_VERSION = 'zweig-sw-v1';

// ── Push event: show notification ────────────────────────────────────────────
self.addEventListener('push', event => {
    if (!event.data) return;

    let payload;
    try {
        payload = event.data.json();
    } catch {
        payload = { title: 'Zweig', body: event.data.text(), type: 'message' };
    }

    const { title, body, type, conv_id, badge } = payload;

    const options = {
        body: body || '',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        tag: type === 'call' ? 'incoming-call' : `msg-${conv_id || 'general'}`,
        renotify: true,
        requireInteraction: type === 'call',
        data: { conv_id, type },
        vibrate: type === 'call' ? [200, 100, 200, 100, 200] : [200],
    };

    // Update app badge
    if (typeof badge === 'number') {
        if (badge > 0) {
            self.registration.setAppBadge?.(badge).catch(() => {});
        } else {
            self.registration.clearAppBadge?.().catch(() => {});
        }
    }

    event.waitUntil(
        self.registration.showNotification(title || 'Zweig', options)
    );
});

// ── Notification click: open / focus app ──────────────────────────────────────
self.addEventListener('notificationclick', event => {
    event.notification.close();

    const { conv_id, type } = event.notification.data || {};
    const targetUrl = conv_id ? `/#messaging` : '/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            // Focus existing window if open
            for (const client of windowClients) {
                if (client.url.includes(self.location.origin)) {
                    client.focus();
                    if (conv_id) {
                        client.postMessage({ type: 'open_conversation', conv_id });
                    }
                    return;
                }
            }
            // Otherwise open new window
            return clients.openWindow(targetUrl);
        })
    );
});

// ── Message from frontend: update badge ───────────────────────────────────────
self.addEventListener('message', event => {
    if (event.data?.type === 'set_badge') {
        const count = event.data.count || 0;
        if (count > 0) {
            self.registration.setAppBadge?.(count).catch(() => {});
        } else {
            self.registration.clearAppBadge?.().catch(() => {});
        }
    }
});
