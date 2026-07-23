// ═══ Nebenan Messenger ═══

// ── Config & State ─────────────────────────────────────────────
// Server base URL (Mattermost-style). Empty string = same origin as this page.
// Stored normalized (scheme + host [+ port]), never with a trailing slash.
function getServerBase() {
    return localStorage.getItem('ch_server') || '';
}
function setServerBase(base) {
    const norm = normalizeServerBase(base);
    if (norm) localStorage.setItem('ch_server', norm);
    else localStorage.removeItem('ch_server');
    API = getServerBase() + '/api';
    return norm;
}
// Accepts "1.2.3.4", "1.2.3.4:8000", "example.com", "http(s)://host[:port]".
// Returns '' for empty/same-origin, otherwise a scheme+host[+port] with no trailing slash.
function normalizeServerBase(raw) {
    let s = (raw || '').trim();
    if (!s) return '';
    if (!/^https?:\/\//i.test(s)) {
        // Default scheme: match the page's scheme so PWA over https stays https.
        const scheme = location.protocol === 'https:' ? 'https' : 'http';
        s = `${scheme}://${s}`;
    }
    try {
        const u = new URL(s);
        return u.origin; // scheme://host[:port], no trailing slash
    } catch {
        return null; // invalid
    }
}
// Prefix a root-relative asset/media path (e.g. "/uploads/…") with the server base.
// Leaves absolute (http/data/blob) URLs untouched.
function srv(path) {
    if (!path) return path;
    if (/^(https?:|data:|blob:)/i.test(path)) return path;
    return getServerBase() + path;
}
let API = getServerBase() + '/api';
let token = localStorage.getItem('ch_access_token') || localStorage.getItem('ch_token') || '';
let refreshToken = localStorage.getItem('ch_refresh_token') || '';
let currentUser = localStorage.getItem('ch_user') || '';
let displayName = localStorage.getItem('ch_display') || '';
let userRole = localStorage.getItem('ch_role') || 'user';
let currentUserAvatar = '';
let currentChannel = null;
let channels = [];
let allUsers = [];
let onlineUsers = {};
let currentPage = 'messaging';
let currentPath = '';
let fileItems = [];
let ctxTarget = null;
let ws = null;
let wsReconnectTimer = null;
let livekitRoom = null;
let activeCallId = null;
let incomingCallData = null;
// ── Push Notifications (Web Push / PWA) ──────────────────────────────────────
let swRegistration = null;
let pushSubscription = null;
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'open_conversation' && event.data.conv_id) {
            openChannel(event.data.conv_id);
        }
    });
}
let ringtoneCtx = null;
let ringtoneInterval = null;
let ringtoneAudio = null;
let audioUnlocked = false;

// Unlock audio on first user gesture (required for iOS)
function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    ringtoneAudio = new Audio('/ringtone.wav');
    ringtoneAudio.loop = true;
    ringtoneAudio.volume = 1.0;
    // Play+pause immediately to unlock iOS audio
    ringtoneAudio.play().then(() => ringtoneAudio.pause()).catch(() => {});
}
document.addEventListener('touchstart', unlockAudio, { once: true });
document.addEventListener('click', unlockAudio, { once: true });

function startRingtone() {
    stopRingtone();
    if (!ringtoneAudio) {
        ringtoneAudio = new Audio('/ringtone.wav');
        ringtoneAudio.loop = true;
        ringtoneAudio.volume = 1.0;
    }
    ringtoneAudio.currentTime = 0;
    ringtoneAudio.play().catch(() => {});
}

function stopRingtone() {
    if (ringtoneAudio) {
        ringtoneAudio.pause();
        ringtoneAudio.currentTime = 0;
    }
}

let mentionUsers = [];
let mentionIndex = 0;
let typingTimeout = null;
let loadingOlderMessages = false;
let editingMessageId = null;
let replyToMsg = null;
let categories = [];
let fileViewMode = localStorage.getItem('ch_file_view') || 'list';
let fileGridSize = parseInt(localStorage.getItem('ch_file_grid_size') || '1');
let pinnedMessages = [];
let pinnedBarIndex = 0;
// ChaosTracker state
let taskProjects = [];
let currentTaskProject = null;
let currentTaskProjectSlug = null;
let taskProjectTasks = [];
let taskProjectLabels = [];
let taskProjectMembers = [];
let taskProjectIssueTypes = [];
let taskProjectCustomFields = [];
let currentTaskDetail = null;
let tasksViewMode = 'board';
let draggedTaskId = null;
let taskBreadcrumb = []; // for navigating back through subprojects
let projectCreationMembers = [];
let allUsersCache = [];
let submitTicketSlug = null;
let submitProjectData = null;
let createTaskFiles = [];
const STATUS_RU = { todo: 'К выполнению', in_progress: 'В работе', review: 'На проверке', done: 'Готово' };
const PRIORITY_RU = { low: 'Низкий', medium: 'Средний', high: 'Высокий', critical: 'Критический' };

// Task list column visibility
const TASK_COLUMNS = [
    { key: 'tl-type',     label: 'Тип',         fr: '0.8fr' },
    { key: 'tl-title',    label: 'Название',    fr: '3fr',   locked: true },
    { key: 'tl-status',   label: 'Статус',      fr: '1fr'   },
    { key: 'tl-priority', label: 'Приоритет',   fr: '1fr'   },
    { key: 'tl-assignee', label: 'Исполнитель', fr: '1fr'   },
    { key: 'tl-date',     label: 'Срок',        fr: '1fr'   },
];
let taskColVis = (() => { try { const s = JSON.parse(localStorage.getItem('ch_task_cols')); if (s && typeof s === 'object') return s; } catch {} const d = {}; TASK_COLUMNS.forEach(c => d[c.key] = true); return d; })();

// ── Client-side routing ─────────────────────────────────────────
let _skipPushState = false;

function pushRoute(path, replace = false) {
    if (_skipPushState) return;
    if (replace) {
        history.replaceState({ path }, '', path);
    } else {
        history.pushState({ path }, '', path);
    }
}

async function handleRoute(path) {
    _skipPushState = true;
    try {
        if (path.startsWith('/channel/')) {
            const slug = decodeURIComponent(path.slice('/channel/'.length));
            await openChannel(slug);
        } else if (path === '/files' || path.startsWith('/files/')) {
            const filePath = path === '/files' ? '' : decodeURIComponent(path.slice('/files/'.length));
            navigateToFiles();
            loadFileList(filePath);
        } else if (path === '/admin') {
            await navigateToAdmin();
        } else if (path.startsWith('/tracker/') && path.endsWith('/submit')) {
            const slug = decodeURIComponent(path.slice('/tracker/'.length, path.length - '/submit'.length));
            showPage('tasks');
            await openSubmitTicketPage(slug);
        } else if (path.startsWith('/tracker/')) {
            const slug = decodeURIComponent(path.slice('/tracker/'.length));
            showPage('tasks');
            taskBreadcrumb = [];
            await openTaskProject(slug);
        } else if (path === '/tracker') {
            await navigateToTasks();
        } else {
            // "/" or unknown → messaging
            showPage('messaging');
            if (channels.length > 0 && !currentChannel) {
                await openChannel(channels[0].slug);
            }
        }
    } finally {
        _skipPushState = false;
    }
}

window.addEventListener('popstate', (e) => {
    const path = (e.state && e.state.path) ? e.state.path : window.location.pathname;
    handleRoute(path);
});

// i18n
let currentLang = localStorage.getItem('ch_lang') || 'ru';
const I18N = {
    ru: {
        'login.subtitle': 'Мессенджер',
        'login.username': 'Имя пользователя',
        'login.password': 'Пароль',
        'login.submit': 'Войти',
        'login.serverTitle': 'Подключение к серверу',
        'login.serverHint': 'Введите адрес или IP сервера Nebenan',
        'login.serverPlaceholder': 'например, chat.example.com или 192.168.1.10',
        'login.serverConnect': 'Продолжить',
        'login.serverInvalid': 'Некорректный адрес сервера',
        'login.serverUnreachable': 'Сервер недоступен. Проверьте адрес',
        'login.changeServer': 'Сменить сервер',
        'login.serverPrefix': 'Сервер:',
        'sidebar.chats': 'Чаты',
        'sidebar.browse': 'Обзор каналов',
        'sidebar.searchPlaceholder': 'Поиск...',
        'msg.empty': 'Выберите чат или начните разговор',
        'msg.placeholder': 'Напишите сообщение...',
        'panel.members': 'Участники',
        'channel.create': 'Создать канал',
        'channel.name': 'Название канала',
        'channel.desc': 'Описание (необязательно)',
        'channel.membersHint': 'Добавить участников (для приватных каналов):',
        'channel.createBtn': 'Создать',
        'channel.browse': 'Обзор каналов',
        'channel.settings': 'Настройки канала',
        'channel.delete': 'Удалить',
        'channel.leave': 'Покинуть',
        'channel.leaveConfirm': 'Вы уверены, что хотите покинуть этот канал?',
        'channel.kick': 'Исключить',
        'channel.kickConfirm': 'Исключить {name} из канала?',
        'channel.save': 'Сохранить',
        'dm.new': 'Новое сообщение',
        'newchat.title': 'Новый чат',
        'newchat.dm': 'Сообщение',
        'newchat.channel': 'Канал',
        'profile.title': 'Профиль',
        'profile.changeAvatar': 'Изменить аватар',
        'profile.displayName': 'Отображаемое имя',
        'profile.nickname': 'Никнейм (@упоминание)',
        'profile.status': 'Статус',
        'profile.password': 'Новый пароль (оставьте пустым)',
        'profile.save': 'Сохранить',
        'admin.title': 'Панель администратора',
        'admin.createUser': 'Создать пользователя',
        'admin.create': 'Создать',
        'admin.username': 'Логин',
        'admin.password': 'Пароль',
        'admin.displayName': 'Отображаемое имя',
        'admin.import': 'Импорт из Excel',
        'admin.template': 'Шаблон',
        'admin.loginHint': 'Логин — латиница, цифры и _ (для входа и упоминаний @). Имя может быть кириллицей.',
        'admin.imported': 'Создано',
        'admin.skipped': 'Пропущено',
        'admin.errors': 'Ошибок',
        'admin.line': 'Строка',
        'admin.importDone': 'Импорт: создано {created}, пропущено {skipped}, ошибок {errors}',
        'settings.title': 'Настройки',
        'settings.theme': 'Тема',
        'settings.language': 'Язык',
        'settings.dark': 'Тёмная',
        'settings.light': 'Светлая',
        'settings.files': 'Файловый менеджер',
        'settings.admin': 'Панель администратора',
        'settings.logout': 'Выйти',
        'files.newFolder': 'Новая папка',
        'files.upload': 'Загрузить',
        'files.uploadMedia': 'Фото / Видео',
        'files.uploadFile': 'Файл',
        'files.uploaded': 'Загружено',
        'files.uploadError': 'Ошибка загрузки',
        'files.name': 'Название',
        'files.size': 'Размер',
        'files.date': 'Добавлено',
        'files.empty': 'Папка пуста',
        'files.uploading': 'Загрузка...',
        'files.download': 'Скачать',
        'files.folderName': 'Название папки',
        'files.createFolder': 'Создать',
        'search.placeholder': 'Поиск сообщений...',
        'call.title': 'Видеозвонок',
        'call.incoming': 'Видеозвонок...',
        'call.invite': 'Пригласить в звонок',
        'call.searchPlaceholder': 'Поиск по имени...',
        'call.inviteBtn': 'Пригласить',
        'admin.feedback': 'Обратная связь',
        'ctx.preview': 'Просмотр',
        'ctx.download': 'Скачать',
        'ctx.rename': 'Переименовать',
        'ctx.delete': 'Удалить',
        'ctx.shareToChat': 'Поделиться в чат',
        'ctx.copy': 'Копировать',
        'share.title': 'Поделиться в чат',
        'share.search': 'Поиск чата...',
        'share.empty': 'Нет доступных чатов',
        'share.success': 'Файл отправлен',
        'copy.title': 'Копировать в папку',
        'copy.search': 'Поиск папки...',
        'copy.root': 'Корневая папка',
        'copy.success': 'Файл скопирован',
        'copy.empty': 'Нет папок',
        'forward.title': 'Переслать сообщение',
        'forward.search': 'Поиск чата...',
        'forward.empty': 'Нет доступных чатов',
        'forward.success': 'Сообщение переслано',
        'feedback.title': 'Обратная связь',
        'feedback.hint': 'Помогите нам улучшить наш сервис! Пишите предложения и жалобы — они будут использованы только для улучшения качества нашего продукта.',
        'feedback.placeholder': 'Ваше сообщение...',
        'feedback.send': 'Отправить',
        'toast.msgSent': 'Сообщение отправлено',
        'toast.channelCreated': 'Канал создан',
        'toast.profileSaved': 'Профиль сохранён',
        'toast.copied': 'Скопировано',
        'msg.edited': '(ред.)',
        'msg.today': 'Сегодня',
        'msg.yesterday': 'Вчера',
        'msg.welcome': 'Начало канала',
        'msg.welcomeHint': 'Это самое начало канала. Напишите первое сообщение!',
        'msg.welcomeDm': 'Начало беседы',
        'msg.welcomeDmHint': 'Это начало вашей переписки.',
        'cat.uncategorized': 'Без категории',
        'cat.rename': 'Переименовать',
        'cat.delete': 'Удалить категорию',
        'cat.newName': 'Название категории:',
        'files.copyLink': 'Копировать ссылку',
        'userCard.sendMessage': 'Написать',
        'msg.reply': 'Ответить',
        'msg.reactions': 'Реакции',
        'msg.replies': 'ответов',
        'msg.reply1': 'ответ',
        'search.people': 'Люди',
        'search.channels': 'Каналы',
        'search.all': 'Все',
        'search.usersAndChannels': 'Поиск людей и каналов...',
        'call.inCall': 'В звонке',
        'panel.thread': 'Тред',
        'files.title': 'Файлы',
        'toast.error': 'Ошибка',
        'toast.networkError': 'Ошибка сети',
        'toast.accessDenied': 'Доступ запрещён',
        'toast.forwardError': 'Ошибка пересылки сообщения',
        'admin.userPassRequired': 'Введите логин и пароль',
        'admin.userCreated': 'Пользователь создан',
        'feedback.empty': 'Сначала напишите сообщение',
        'feedback.sent': 'Отзыв отправлен!',
        'channel.deleteConfirm': 'Удалить этот канал?',
        'call.alreadyInCall': 'Вы уже в звонке',
        'call.livekitNotLoaded': 'LiveKit не загружен',
        'call.connectionFailed': 'Не удалось подключиться к звонку',
    },
    en: {
        'login.subtitle': 'Messenger',
        'login.username': 'Username',
        'login.password': 'Password',
        'login.submit': 'Sign In',
        'login.serverTitle': 'Connect to server',
        'login.serverHint': 'Enter the address or IP of your Nebenan server',
        'login.serverPlaceholder': 'e.g. chat.example.com or 192.168.1.10',
        'login.serverConnect': 'Continue',
        'login.serverInvalid': 'Invalid server address',
        'login.serverUnreachable': 'Server unreachable. Check the address',
        'login.changeServer': 'Change server',
        'login.serverPrefix': 'Server:',
        'sidebar.chats': 'Chats',
        'sidebar.browse': 'Browse channels',
        'sidebar.searchPlaceholder': 'Search...',
        'msg.empty': 'Select a chat or start a conversation',
        'msg.placeholder': 'Write a message...',
        'panel.members': 'Members',
        'channel.create': 'Create Channel',
        'channel.name': 'Channel name',
        'channel.desc': 'Description (optional)',
        'channel.membersHint': 'Add members (for private channels):',
        'channel.createBtn': 'Create',
        'channel.browse': 'Browse Channels',
        'channel.settings': 'Channel Settings',
        'channel.delete': 'Delete',
        'channel.leave': 'Leave',
        'channel.leaveConfirm': 'Are you sure you want to leave this channel?',
        'channel.kick': 'Kick',
        'channel.kickConfirm': 'Kick {name} from channel?',
        'channel.save': 'Save',
        'dm.new': 'New Message',
        'newchat.title': 'New Chat',
        'newchat.dm': 'Message',
        'newchat.channel': 'Channel',
        'profile.title': 'Profile',
        'profile.changeAvatar': 'Change avatar',
        'profile.displayName': 'Display name',
        'profile.nickname': 'Nickname (@mention)',
        'profile.status': 'Status',
        'profile.password': 'New password (leave empty to keep)',
        'profile.save': 'Save',
        'admin.title': 'Admin Panel',
        'admin.createUser': 'Create User',
        'admin.create': 'Create',
        'admin.username': 'Username',
        'admin.password': 'Password',
        'admin.displayName': 'Display name',
        'admin.import': 'Import from Excel',
        'admin.template': 'Template',
        'admin.loginHint': 'Login — latin letters, digits and _ (used for sign-in and @mentions). Display name can be Cyrillic.',
        'admin.imported': 'Created',
        'admin.skipped': 'Skipped',
        'admin.errors': 'Errors',
        'admin.line': 'Row',
        'admin.importDone': 'Import: created {created}, skipped {skipped}, errors {errors}',
        'settings.title': 'Settings',
        'settings.theme': 'Theme',
        'settings.language': 'Language',
        'settings.dark': 'Dark',
        'settings.light': 'Light',
        'settings.files': 'File Manager',
        'settings.admin': 'Admin Panel',
        'settings.logout': 'Logout',
        'files.newFolder': 'New Folder',
        'files.upload': 'Upload',
        'files.uploadMedia': 'Photo / Video',
        'files.uploadFile': 'File',
        'files.uploaded': 'Uploaded',
        'files.uploadError': 'Upload failed',
        'files.name': 'Name',
        'files.size': 'Size',
        'files.date': 'Added',
        'files.empty': 'Folder is empty',
        'files.uploading': 'Uploading...',
        'files.download': 'Download',
        'files.folderName': 'Folder name',
        'files.createFolder': 'Create',
        'search.placeholder': 'Search messages...',
        'call.title': 'Video Call',
        'call.incoming': 'Video call...',
        'call.invite': 'Invite to call',
        'call.searchPlaceholder': 'Search by name...',
        'call.inviteBtn': 'Invite',
        'admin.feedback': 'Feedback',
        'ctx.preview': 'Preview',
        'ctx.download': 'Download',
        'ctx.rename': 'Rename',
        'ctx.delete': 'Delete',
        'ctx.shareToChat': 'Share to chat',
        'ctx.copy': 'Copy',
        'share.title': 'Share to chat',
        'share.search': 'Search chat...',
        'share.empty': 'No available chats',
        'share.success': 'File sent',
        'copy.title': 'Copy to folder',
        'copy.search': 'Search folder...',
        'copy.root': 'Root folder',
        'copy.success': 'File copied',
        'copy.empty': 'No folders',
        'forward.title': 'Forward message',
        'forward.search': 'Search chat...',
        'forward.empty': 'No available chats',
        'forward.success': 'Message forwarded',
        'feedback.title': 'Feedback',
        'feedback.hint': 'Help us improve! Share your suggestions or report issues — your feedback will only be used to make the product better.',
        'feedback.placeholder': 'Your message...',
        'feedback.send': 'Send',
        'toast.msgSent': 'Message sent',
        'toast.channelCreated': 'Channel created',
        'toast.profileSaved': 'Profile saved',
        'toast.copied': 'Copied',
        'msg.edited': '(edited)',
        'msg.today': 'Today',
        'msg.yesterday': 'Yesterday',
        'msg.welcome': 'Beginning of channel',
        'msg.welcomeHint': 'This is the very beginning of the channel. Write the first message!',
        'msg.welcomeDm': 'Beginning of conversation',
        'msg.welcomeDmHint': 'This is the beginning of your conversation.',
        'cat.uncategorized': 'Uncategorized',
        'cat.rename': 'Rename',
        'cat.delete': 'Delete category',
        'cat.newName': 'Category name:',
        'files.copyLink': 'Copy link',
        'userCard.sendMessage': 'Send message',
        'msg.reply': 'Reply',
        'msg.reactions': 'Reactions',
        'msg.replies': 'replies',
        'msg.reply1': 'reply',
        'search.people': 'People',
        'search.channels': 'Channels',
        'search.all': 'All',
        'search.usersAndChannels': 'Search people and channels...',
        'call.inCall': 'In call',
        'panel.thread': 'Thread',
        'files.title': 'Files',
        'toast.error': 'Error',
        'toast.networkError': 'Network error',
        'toast.accessDenied': 'Access denied',
        'toast.forwardError': 'Error forwarding message',
        'admin.userPassRequired': 'Username and password required',
        'admin.userCreated': 'User created',
        'feedback.empty': 'Write something first',
        'feedback.sent': 'Feedback sent!',
        'channel.deleteConfirm': 'Delete this channel?',
        'call.alreadyInCall': 'Already in a call',
        'call.livekitNotLoaded': 'LiveKit not loaded',
        'call.connectionFailed': 'Call connection failed',
    }
};

function t(key) { return (I18N[currentLang] || I18N.ru)[key] || key; }

function applyI18n() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        el.textContent = t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = t(key);
    });
}

// ── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    if (token) { checkAuth(); } else { showLogin(); }
    setupMsgInput();
    document.addEventListener('click', e => {
        hideContextMenu();
        const picker = document.getElementById('emoji-picker');
        if (picker && picker.style.display !== 'none') {
            if (!e.target.closest('.emoji-picker') && !e.target.closest('[onclick*="toggleEmojiPicker"]')) {
                picker.style.display = 'none';
            }
        }
        const colPicker = document.getElementById('tasks-col-picker');
        if (colPicker && colPicker.style.display !== 'none' && !e.target.closest('.tasks-col-picker-wrap')) {
            colPicker.style.display = 'none';
        }
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeAllModals();
    });
    applyI18n();
});

// ── API helper ─────────────────────────────────────────────────
let _refreshInFlight = null;

async function refreshAccessToken() {
    if (!refreshToken) return false;
    if (_refreshInFlight) return _refreshInFlight;

    _refreshInFlight = (async () => {
        try {
            const res = await fetch(API + '/auth/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });
            if (!res.ok) return false;
            const data = await res.json();
            const newAccess = data.access_token || data.token;
            const newRefresh = data.refresh_token;
            if (!newAccess || !newRefresh) return false;
            token = newAccess;
            refreshToken = newRefresh;
            localStorage.setItem('ch_access_token', token);
            localStorage.setItem('ch_refresh_token', refreshToken);
            localStorage.removeItem('ch_token'); // legacy cleanup
            return true;
        } catch (_) {
            return false;
        } finally {
            _refreshInFlight = null;
        }
    })();

    return _refreshInFlight;
}

async function apiFetch(url, opts = {}) {
    const requestOpts = { ...opts };
    const headers = { ...(requestOpts.headers || {}) };
    if (token && requestOpts.skipAuth !== true) headers['Authorization'] = `Bearer ${token}`;
    if (requestOpts.body && !(requestOpts.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        requestOpts.body = JSON.stringify(requestOpts.body);
    }
    const res = await fetch(API + url, { ...requestOpts, headers });
    if (res.status === 401) {
        const canRetry = !requestOpts._retry && requestOpts.skipAuth !== true;
        if (canRetry) {
            const refreshed = await refreshAccessToken();
            if (refreshed) {
                return apiFetch(url, { ...opts, _retry: true });
            }
        }
        logout();
        return null;
    }
    if (res.status === 403) { showToast(t('toast.accessDenied'), 'error'); return null; }
    return res;
}

// ── Auth ───────────────────────────────────────────────────────
function showLogin() {
    document.getElementById('login-screen').classList.add('active');
    document.getElementById('app-screen').classList.remove('active');
    // Mattermost-style: pick the step to show. If we already know the server,
    // jump straight to credentials; otherwise ask for the server address first.
    const saved = getServerBase();
    const input = document.getElementById('server-input');
    if (input && !input.value) input.value = saved;
    if (saved) showLoginStep('credentials');
    else showLoginStep('server');
}

// Toggle between the "server address" and "credentials" panels of the login card.
function showLoginStep(step) {
    const serverStep = document.getElementById('login-step-server');
    const credStep = document.getElementById('login-step-credentials');
    if (!serverStep || !credStep) return;
    const onServer = step === 'server';
    serverStep.style.display = onServer ? '' : 'none';
    credStep.style.display = onServer ? 'none' : '';
    // Show which server the credentials will be sent to.
    const label = document.getElementById('login-server-label');
    if (label) label.textContent = getServerBase() || location.host;
    const focusEl = document.getElementById(onServer ? 'server-input' : 'login-user');
    if (focusEl) setTimeout(() => focusEl.focus(), 50);
}

// Validate a server address by hitting its /api/health endpoint, then advance.
async function connectServer() {
    const raw = document.getElementById('server-input').value;
    const errEl = document.getElementById('server-error');
    const btn = document.getElementById('server-connect-btn');
    const norm = normalizeServerBase(raw);
    if (norm === null) {
        errEl.textContent = t('login.serverInvalid');
        return;
    }
    errEl.textContent = '';
    btn.disabled = true;
    btn.classList.add('loading');
    try {
        const res = await fetch((norm || '') + '/api/health', { method: 'GET' });
        if (!res.ok) throw new Error('bad status');
        const data = await res.json().catch(() => ({}));
        if (data.status !== 'ok') throw new Error('unexpected');
        setServerBase(norm);
        showLoginStep('credentials');
    } catch (e) {
        errEl.textContent = t('login.serverUnreachable');
    } finally {
        btn.disabled = false;
        btn.classList.remove('loading');
    }
}

// "Change server" link on the credentials panel.
function changeServer() {
    showLoginStep('server');
}

async function showApp() {
    document.getElementById('login-screen').classList.remove('active');
    document.getElementById('app-screen').classList.add('active');
    loadTheme();
    applyI18n();
    await loadChannels();
    await loadCategories();
    loadUsers();
    connectWebSocket();
    updateSidebarUser();
    initPushNotifications();
    if (userRole === 'admin') {
        document.getElementById('settings-admin-row').style.display = '';
        const adminBtn = document.getElementById('sidebar-admin-btn');
        if (adminBtn) adminBtn.style.display = '';
    }
    // Route based on current URL path
    const initPath = window.location.pathname;
    if (initPath && initPath !== '/') {
        history.replaceState({ path: initPath }, '', initPath);
        await handleRoute(initPath);
    } else if (channels.length > 0 && !currentChannel) {
        // Default: open first channel
        if (window.innerWidth <= 768) {
            const sb = document.getElementById('sidebar');
            if (!sb.classList.contains('open')) toggleSidebar();
        } else {
            openChannel(channels[0].slug);
        }
        history.replaceState({ path: '/' }, '', '/');
    }
}

async function checkAuth() {
    const res = await apiFetch('/me');
    if (!res || !res.ok) { logout(); return; }
    const data = await res.json();
    currentUser = data.username;
    displayName = data.display_name;
    userRole = data.role || 'user';
    currentUserAvatar = data.avatar_path || '';
    localStorage.setItem('ch_user', currentUser);
    localStorage.setItem('ch_display', displayName);
    localStorage.setItem('ch_role', userRole);
    showApp();
}

document.getElementById('login-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';
    const username = document.getElementById('login-user').value.trim();
    const password = document.getElementById('login-pass').value;
    let res, data;
    try {
        res = await fetch(API + '/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        data = await res.json();
    } catch (err) {
        // Network/CORS failure — usually a wrong server address or CORS not enabled.
        errEl.textContent = t('login.serverUnreachable');
        return;
    }
    const access = data.access_token || data.token;
    const refresh = data.refresh_token;
    if (res.ok && access && refresh) {
        token = access;
        refreshToken = refresh;
        currentUser = data.username;
        displayName = data.display_name;
        userRole = data.role || 'user';
        currentUserAvatar = data.avatar_path || '';
        localStorage.setItem('ch_access_token', token);
        localStorage.setItem('ch_refresh_token', refreshToken);
        localStorage.removeItem('ch_token'); // legacy cleanup
        localStorage.setItem('ch_user', currentUser);
        localStorage.setItem('ch_display', displayName);
        localStorage.setItem('ch_role', userRole);
        showApp();
    } else {
        errEl.textContent = data.detail || 'Login failed';
    }
});

function logout() {
    unsubscribePush();
    token = '';
    refreshToken = '';
    currentUser = '';
    displayName = '';
    localStorage.removeItem('ch_access_token');
    localStorage.removeItem('ch_refresh_token');
    localStorage.removeItem('ch_token');
    localStorage.removeItem('ch_user');
    localStorage.removeItem('ch_display');
    localStorage.removeItem('ch_role');
    localStorage.removeItem('ch_push_dismissed');
    if (ws) { ws.close(); ws = null; }
    if (livekitRoom) { livekitRoom.disconnect(); livekitRoom = null; }
    showLogin();
    closeAllModals();
}

// ── Push Notifications (Web Push / PWA) ──────────────────────────────────────

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
}

async function initPushNotifications() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    try {
        swRegistration = await navigator.serviceWorker.register('/service-worker.js');
    } catch (_) { return; }

    // If permission already granted — subscribe silently (no user gesture needed)
    if (Notification.permission === 'granted') {
        await _subscribePush();
        return;
    }

    // iOS requires user gesture to call requestPermission — show banner
    if (Notification.permission === 'default' && !localStorage.getItem('ch_push_dismissed')) {
        _showPushBanner();
    }
}

function _showPushBanner() {
    if (document.getElementById('push-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'push-banner';
    banner.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--accent,#6c63ff);color:#fff;padding:12px 18px;border-radius:12px;display:flex;align-items:center;gap:10px;z-index:9999;box-shadow:0 4px 20px rgba(0,0,0,.3);font-size:14px;max-width:90vw;';
    banner.innerHTML = `<span>🔔</span><span>${currentUser && navigator.language?.startsWith('ru') ? 'Включить уведомления о сообщениях?' : 'Enable message notifications?'}</span><button id="push-allow-btn" style="background:#fff;color:var(--accent,#6c63ff);border:none;border-radius:8px;padding:6px 14px;font-weight:600;cursor:pointer;">Да</button><button id="push-deny-btn" style="background:transparent;color:#fff;border:none;cursor:pointer;font-size:18px;line-height:1;">✕</button>`;
    document.body.appendChild(banner);

    document.getElementById('push-allow-btn').onclick = async () => {
        banner.remove();
        const permission = await Notification.requestPermission();
        if (permission === 'granted') await _subscribePush();
    };
    document.getElementById('push-deny-btn').onclick = () => {
        banner.remove();
        localStorage.setItem('ch_push_dismissed', '1');
    };
}

async function _subscribePush() {
    try {
        const res = await apiFetch('/push/vapid-public-key');
        if (!res || !res.ok) return;
        const { publicKey } = await res.json();
        if (!publicKey) return;
        pushSubscription = await swRegistration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicKey),
        });
        await apiFetch('/push/subscribe', {
            method: 'POST',
            body: {
                endpoint: pushSubscription.endpoint,
                keys: {
                    p256dh: btoa(String.fromCharCode(...new Uint8Array(pushSubscription.getKey('p256dh')))),
                    auth: btoa(String.fromCharCode(...new Uint8Array(pushSubscription.getKey('auth')))),
                },
            },
        });
    } catch (_) {}
}

async function unsubscribePush() {
    if (!pushSubscription) return;
    const endpoint = pushSubscription.endpoint;
    try {
        await apiFetch('/push/subscribe', {
            method: 'DELETE',
            body: { endpoint },
        });
        await pushSubscription.unsubscribe();
    } catch (_) {}
    pushSubscription = null;
    if ('clearAppBadge' in navigator) navigator.clearAppBadge().catch(() => {});
}

function updateAppBadge(count) {
    if (swRegistration?.active) {
        swRegistration.active.postMessage({ type: 'set_badge', count });
    }
    if ('setAppBadge' in navigator) {
        count > 0 ? navigator.setAppBadge(count).catch(() => {}) : navigator.clearAppBadge().catch(() => {});
    }
}

// ── Categories / Folders (synced with server API) ─────────────
async function loadCategories() {
    try {
        const res = await apiFetch('/chat-folders');
        if (res && res.ok) {
            const folders = await res.json();
            // Convert server format to local categories format
            categories = folders.map(f => ({
                id: f.id,
                name: f.name,
                collapsed: false,
                channelIds: [], // Will map slugs to IDs below
                _channelSlugs: f.channel_slugs || [],
            }));
            // Map slugs to channel IDs using loaded channels
            _mapCategorySlugsToIds();
        }
    } catch (e) {
        console.error('[categories] loadCategories error:', e);
        // Fallback to localStorage
        try { categories = JSON.parse(localStorage.getItem('ch_categories') || '[]'); } catch { categories = []; }
    }
}
function _mapCategorySlugsToIds() {
    // Map server slugs to local channel IDs
    categories.forEach(cat => {
        if (cat._channelSlugs && cat._channelSlugs.length > 0) {
            cat.channelIds = cat._channelSlugs
                .map(slug => { const ch = channels.find(c => c.slug === slug); return ch ? ch.id : null; })
                .filter(Boolean);
        }
    });
}
function saveCategories() {
    // Save to localStorage as backup
    localStorage.setItem('ch_categories', JSON.stringify(categories));
}
function showCategoryPrompt() {
    // Remove existing inline input if any
    const existing = document.getElementById('inline-category-input');
    if (existing) { existing.remove(); return; }
    const chatList = document.getElementById('chat-list');
    const wrapper = document.createElement('div');
    wrapper.id = 'inline-category-input';
    wrapper.className = 'inline-input-row';
    wrapper.innerHTML = `<input type="text" class="inline-input" placeholder="${t('cat.newName')}" autofocus>
        <button class="btn-icon-xs inline-input-ok" onclick="confirmNewCategory()"><span class="material-icons-round">check</span></button>
        <button class="btn-icon-xs inline-input-cancel" onclick="cancelInlineInput()"><span class="material-icons-round">close</span></button>`;
    chatList.parentNode.insertBefore(wrapper, chatList);
    const inp = wrapper.querySelector('input');
    inp.focus();
    inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') confirmNewCategory();
        if (e.key === 'Escape') cancelInlineInput();
    });
}
async function confirmNewCategory() {
    const wrapper = document.getElementById('inline-category-input');
    if (!wrapper) return;
    const name = wrapper.querySelector('input').value.trim();
    wrapper.remove();
    if (!name) return;
    try {
        const res = await apiFetch('/chat-folders', { method: 'POST', body: JSON.stringify({ name }) });
        if (res && res.ok) {
            const folder = await res.json();
            categories.push({ id: folder.id, name: folder.name, collapsed: false, channelIds: [], _channelSlugs: [] });
            saveCategories();
            renderSidebar();
        }
    } catch (e) {
        console.error('[categories] create error:', e);
    }
}
function cancelInlineInput() {
    const el = document.getElementById('inline-category-input');
    if (el) el.remove();
}
function toggleCategoryCollapse(catId) {
    const cat = categories.find(c => c.id === catId);
    if (cat) { cat.collapsed = !cat.collapsed; saveCategories(); renderSidebar(); }
}
function showCategoryContextMenu(e, catId) {
    e.preventDefault();
    e.stopPropagation();
    const menu = document.getElementById('context-menu');
    menu.innerHTML = `
        <div class="ctx-item" onclick="renameCategory('${catId}')"><span class="material-icons-round">edit</span> ${t('cat.rename')}</div>
        <div class="ctx-item ctx-danger" onclick="deleteCategory('${catId}')"><span class="material-icons-round">delete</span> ${t('cat.delete')}</div>
    `;
    menu.style.display = 'block';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';
}
function renameCategory(catId) {
    hideContextMenu();
    const cat = categories.find(c => c.id === catId);
    if (!cat) return;
    // Find the category header in DOM and replace name with input
    const headers = document.querySelectorAll('.sidebar-category-header');
    for (const h of headers) {
        if (h.getAttribute('oncontextmenu')?.includes(catId)) {
            const nameSpan = h.querySelector('.sidebar-category-name');
            if (!nameSpan) return;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'inline-input inline-input-rename';
            input.value = cat.name;
            nameSpan.replaceWith(input);
            input.focus();
            input.select();
            const save = async () => {
                const val = input.value.trim();
                if (val && val !== cat.name) {
                    cat.name = val;
                    saveCategories();
                    try { await apiFetch(`/chat-folders/${catId}`, { method: 'PUT', body: JSON.stringify({ name: val }) }); } catch {}
                }
                renderSidebar();
            };
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter') save();
                if (e.key === 'Escape') renderSidebar();
            });
            input.addEventListener('blur', save);
            return;
        }
    }
}
async function deleteCategory(catId) {
    hideContextMenu();
    categories = categories.filter(c => c.id !== catId);
    saveCategories();
    renderSidebar();
    try { await apiFetch(`/chat-folders/${catId}`, { method: 'DELETE' }); } catch {}
}

// Drag & drop
function onDragStart(e, channelId) {
    e.dataTransfer.setData('text/plain', channelId);
    e.target.classList.add('dragging');
}
function onDragEnd(e) { e.target.classList.remove('dragging'); }
function onCategoryDragOver(e) { e.preventDefault(); e.currentTarget.classList.add('drag-over'); }
function onCategoryDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }
async function onCategoryDrop(e, catId) {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    const channelId = e.dataTransfer.getData('text/plain');
    if (!channelId) return;
    // Find channel slug
    const ch = channels.find(c => c.id === channelId);
    const slug = ch ? ch.slug : null;
    // Remove from all categories first
    categories.forEach(c => { c.channelIds = c.channelIds.filter(id => id !== channelId); });
    if (catId !== '__uncategorized__') {
        const cat = categories.find(c => c.id === catId);
        if (cat && !cat.channelIds.includes(channelId)) cat.channelIds.push(channelId);
        // Sync with server
        if (slug) {
            try { await apiFetch(`/chat-folders/${catId}/channels`, { method: 'POST', body: JSON.stringify({ channel_slug: slug }) }); } catch {}
        }
    } else {
        // Remove from all server folders
        if (slug) {
            for (const cat of categories) {
                try { await apiFetch(`/chat-folders/${cat.id}/channels/${slug}`, { method: 'DELETE' }); } catch {}
            }
        }
    }
    saveCategories();
    renderSidebar();
}

// ── Sidebar ────────────────────────────────────────────────────
function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    sb.classList.toggle('open');
    if (!overlay) {
        const ov = document.createElement('div');
        ov.className = 'sidebar-overlay' + (sb.classList.contains('open') ? ' active' : '');
        ov.onclick = () => toggleSidebar();
        document.getElementById('app-screen').querySelector('.app-layout').prepend(ov);
    } else {
        overlay.classList.toggle('active');
    }
}

function updateSidebarUser() {
    const nameEl = document.getElementById('sidebar-user-name');
    const avatarEl = document.getElementById('sidebar-user-avatar');
    if (nameEl) nameEl.textContent = displayName || currentUser;
    if (avatarEl) {
        if (currentUserAvatar) {
            avatarEl.innerHTML = `<img src="${srv(currentUserAvatar)}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">`;
        } else {
            avatarEl.innerHTML = '';
            avatarEl.textContent = (displayName || currentUser).charAt(0).toUpperCase();
        }
    }
}

async function loadChannels() {
    const res = await apiFetch('/channels');
    if (!res || !res.ok) return;
    channels = await res.json();
    // Re-map category slugs to channel IDs after channels are loaded
    _mapCategorySlugsToIds();
    renderSidebar();
}

function renderSidebar() {
    const chatList = document.getElementById('chat-list');
    if (!chatList) return;

    // Sort all channels: most recent message first
    const sorted = [...channels].sort((a, b) => {
        const ta = a.last_msg_timestamp || a.created_at || '';
        const tb = b.last_msg_timestamp || b.created_at || '';
        return tb.localeCompare(ta);
    });

    // Build categorized and uncategorized lists
    const categorizedIds = new Set();
    categories.forEach(cat => cat.channelIds.forEach(id => categorizedIds.add(id)));
    const uncategorized = sorted.filter(ch => !categorizedIds.has(ch.id));

    let html = '';

    // Render each category
    categories.forEach(cat => {
        const catChannels = sorted.filter(ch => cat.channelIds.includes(ch.id));
        const arrow = cat.collapsed ? 'chevron_right' : 'expand_more';
        html += `<div class="sidebar-category-header"
                      ondragover="onCategoryDragOver(event)"
                      ondragleave="onCategoryDragLeave(event)"
                      ondrop="onCategoryDrop(event, '${cat.id}')"
                      oncontextmenu="showCategoryContextMenu(event, '${cat.id}')">
            <span class="material-icons-round collapse-icon" onclick="toggleCategoryCollapse('${cat.id}')">${arrow}</span>
            <span class="sidebar-category-name" onclick="toggleCategoryCollapse('${cat.id}')">${esc(cat.name)}</span>
            <span class="sidebar-category-count">${catChannels.length}</span>
        </div>`;
        if (!cat.collapsed) {
            catChannels.forEach(ch => { html += renderSidebarItem(ch); });
        }
    });

    // Uncategorized section (only show header if there are categories)
    if (categories.length > 0 && uncategorized.length > 0) {
        html += `<div class="sidebar-category-header sidebar-uncategorized-header"
                      ondragover="onCategoryDragOver(event)"
                      ondragleave="onCategoryDragLeave(event)"
                      ondrop="onCategoryDrop(event, '__uncategorized__')">
            <span class="material-icons-round collapse-icon">expand_more</span>
            <span class="sidebar-category-name">${t('cat.uncategorized')}</span>
            <span class="sidebar-category-count">${uncategorized.length}</span>
        </div>`;
    }
    uncategorized.forEach(ch => { html += renderSidebarItem(ch); });

    chatList.innerHTML = html;

    // Update app icon badge (PWA home screen)
    const totalUnread = channels.reduce((s, ch) => s + (ch.unread_count || 0), 0);
    updateAppBadge(totalUnread);
}

function renderSidebarItem(ch) {
    const active = currentChannel && currentChannel.id === ch.id ? 'active' : '';
    const badge = ch.unread_count > 0 ? `<span class="sidebar-item-badge">${ch.unread_count}</span>` : '';
    const menuBtn = `<button class="sidebar-item-menu btn-icon-xs" onclick="event.stopPropagation(); showChannelContextMenu(event, '${ch.slug}')"><span class="material-icons-round">more_vert</span></button>`;

    if (ch.type === 'direct') {
        const other = (ch.members || []).find(m => m !== currentUser) || 'Unknown';
        const otherUser = allUsers.find(u => u.username === other);
        const name = otherUser ? otherUser.display_name : other;
        const isOnline = onlineUsers[other]?.online;
        const avatarContent = otherUser?.avatar_path
            ? `<img src="${srv(otherUser.avatar_path)}">`
            : name.charAt(0).toUpperCase();
        return `<div class="sidebar-item ${active}" draggable="true" data-channel-id="${ch.id}"
                     onclick="openChannel('${ch.slug}')"
                     ondragstart="onDragStart(event, '${ch.id}')" ondragend="onDragEnd(event)">
            <div class="sidebar-dm-avatar">${avatarContent}</div>
            <span class="sidebar-item-name">${esc(name)}</span>
            <span class="sidebar-dm-status ${isOnline ? 'online' : 'offline'}"></span>
            ${badge}
            ${menuBtn}
        </div>`;
    } else {
        const icon = ch.type === 'private' ? '🔒' : '#';
        const chAvatarContent = ch.avatar_path
            ? `<img class="sidebar-item-avatar" src="${srv(ch.avatar_path)}">`
            : `<span class="sidebar-item-icon">${icon}</span>`;
        return `<div class="sidebar-item ${active}" draggable="true" data-channel-id="${ch.id}"
                     onclick="openChannel('${ch.slug}')"
                     ondragstart="onDragStart(event, '${ch.id}')" ondragend="onDragEnd(event)">
            ${chAvatarContent}
            <span class="sidebar-item-name">${esc(ch.name)}</span>
            ${badge}
            ${menuBtn}
        </div>`;
    }
}

function showChannelContextMenu(e, slug) {
    e.preventDefault();
    e.stopPropagation();
    const ch = channels.find(c => c.slug === slug);
    if (!ch) return;

    const isOwner = ch.created_by === currentUser;
    const isAdmin = userRole === 'admin';
    const canDelete = isOwner || isAdmin;
    const isDM = ch.type === 'direct';

    let items = '';
    // Move to folder options
    if (categories.length > 0) {
        const currentCat = categories.find(c => c.channelIds.includes(ch.id));
        categories.forEach(cat => {
            const inCat = cat.channelIds.includes(ch.id);
            items += `<div class="ctx-item" onclick="moveChannelToFolder('${ch.id}', '${slug}', '${cat.id}')"><span class="material-icons-round">${inCat ? 'folder' : 'folder_open'}</span> ${inCat ? '✓ ' : ''}${esc(cat.name)}</div>`;
        });
        if (currentCat) {
            items += `<div class="ctx-item" onclick="removeChannelFromFolders('${ch.id}', '${slug}')"><span class="material-icons-round">folder_off</span> Remove from folder</div>`;
        }
        items += `<div class="ctx-divider"></div>`;
    }
    if (!isDM) {
        items += `<div class="ctx-item" onclick="openChannelSettings('${slug}')"><span class="material-icons-round">settings</span> ${t('channel.settings')}</div>`;
    }
    if (!isDM && !isOwner) {
        items += `<div class="ctx-item" onclick="leaveChannel('${slug}')"><span class="material-icons-round">logout</span> ${t('channel.leave')}</div>`;
    }
    if (canDelete) {
        items += `<div class="ctx-item ctx-danger" onclick="deleteChannel('${slug}')"><span class="material-icons-round">delete</span> ${t('channel.delete')}</div>`;
    }

    if (!items) return;
    const menu = document.getElementById('context-menu');
    menu.innerHTML = items;
    menu.style.display = 'block';
    const mx = e.clientX || 0;
    const my = e.clientY || 0;
    const mw = menu.offsetWidth;
    const mh = menu.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    menu.style.left = (mx + mw > vw ? Math.max(0, vw - mw - 8) : mx) + 'px';
    menu.style.top = (my + mh > vh ? Math.max(0, vh - mh - 8) : my) + 'px';
}

async function moveChannelToFolder(channelId, slug, folderId) {
    hideContextMenu();
    // Remove from all categories first
    categories.forEach(c => { c.channelIds = c.channelIds.filter(id => id !== channelId); });
    // Add to target folder
    const cat = categories.find(c => c.id === folderId);
    if (cat && !cat.channelIds.includes(channelId)) cat.channelIds.push(channelId);
    saveCategories();
    renderSidebar();
    // Sync with server
    try { await apiFetch(`/chat-folders/${folderId}/channels`, { method: 'POST', body: JSON.stringify({ channel_slug: slug }) }); } catch {}
}

async function removeChannelFromFolders(channelId, slug) {
    hideContextMenu();
    // Find which folders contain this channel and remove from server
    for (const cat of categories) {
        if (cat.channelIds.includes(channelId)) {
            try { await apiFetch(`/chat-folders/${cat.id}/channels/${slug}`, { method: 'DELETE' }); } catch {}
        }
    }
    categories.forEach(c => { c.channelIds = c.channelIds.filter(id => id !== channelId); });
    saveCategories();
    renderSidebar();
}

function openChannelSettings(slug) {
    hideContextMenu();
    openChannel(slug).then(() => showChannelSettingsDialog());
}

async function deleteChannel(slug) {
    hideContextMenu();
    if (!confirm(t('channel.delete') + '?')) return;
    await apiFetch(`/channels/${slug}`, { method: 'DELETE' });
    if (currentChannel && currentChannel.slug === slug) {
        currentChannel = null;
        document.getElementById('msg-empty-state').style.display = 'flex';
        document.getElementById('msg-messages').style.display = 'none';
        document.getElementById('msg-input-area').style.display = 'none';
    }
    await loadChannels();
}

async function leaveChannel(slug) {
    hideContextMenu();
    if (!confirm(t('channel.leaveConfirm'))) return;
    await apiFetch(`/channels/${slug}/leave`, { method: 'POST' });
    if (currentChannel && currentChannel.slug === slug) {
        currentChannel = null;
        document.getElementById('msg-empty-state').style.display = 'flex';
        document.getElementById('msg-messages').style.display = 'none';
        document.getElementById('msg-input-area').style.display = 'none';
    }
    await loadChannels();
}

async function loadUsers() {
    const res = await apiFetch('/users');
    if (!res || !res.ok) return;
    allUsers = await res.json();
    const pres = await apiFetch('/presence');
    if (pres && pres.ok) onlineUsers = await pres.json();
}

// ── Channel management ─────────────────────────────────────────
async function openChannel(slug) {
    const sb = document.getElementById('sidebar');
    if (sb.classList.contains('open')) toggleSidebar();
    pushRoute('/channel/' + encodeURIComponent(slug));
    showPage('messaging');
    const res = await apiFetch(`/channels/${slug}`);
    if (!res || !res.ok) return;
    currentChannel = await res.json();
    updateTopbar();
    document.getElementById('msg-empty-state').style.display = 'none';
    document.getElementById('msg-messages').style.display = 'flex';
    document.getElementById('msg-input-area').style.display = 'flex';
    await loadMessages();
    loadPinnedMessages();
    apiFetch(`/channels/${slug}/read`, { method: 'POST' });
    // Clear unread badge for the opened channel
    const openedCh = channels.find(c => c.slug === slug);
    if (openedCh) openedCh.unread_count = 0;
    renderSidebar();
    checkActiveCall();
    loadRightPanelMembers();
}

function updateTopbar() {
    if (!currentChannel) return;
    const iconEl = document.getElementById('topbar-channel-icon');
    const nameEl = document.getElementById('topbar-channel-name');
    const descEl = document.getElementById('topbar-channel-desc');
    const callBtn = document.getElementById('btn-call');
    const settingsBtn = document.getElementById('btn-channel-settings');
    const membersBtn = document.getElementById('btn-members');

    if (currentChannel.type === 'direct') {
        const other = (currentChannel.members || []).find(m => m !== currentUser) || '';
        const otherUser = allUsers.find(u => u.username === other);
        iconEl.textContent = '@';
        nameEl.textContent = otherUser ? otherUser.display_name : other;
        descEl.textContent = onlineUsers[other]?.online ? 'Online' : '';
        if (settingsBtn) settingsBtn.style.display = 'none';
        if (membersBtn) membersBtn.style.display = 'none';
    } else {
        if (currentChannel.avatar_path) {
            iconEl.innerHTML = `<img src="${srv(currentChannel.avatar_path)}" style="width:28px;height:28px;border-radius:50%;object-fit:cover;">`;
        } else {
            iconEl.textContent = currentChannel.type === 'private' ? '🔒' : '#';
        }
        nameEl.textContent = currentChannel.name;
        descEl.textContent = currentChannel.description || '';
        if (settingsBtn) settingsBtn.style.display = '';
        if (membersBtn) membersBtn.style.display = '';
    }
    if (callBtn) callBtn.style.display = '';
}

async function loadMessages(before = null) {
    if (!currentChannel) return;
    const url = `/channels/${currentChannel.slug}/messages` + (before ? `?before=${before}` : '');
    const res = await apiFetch(url);
    if (!res || !res.ok) return;
    const data = await res.json();
    const container = document.getElementById('msg-messages');
    if (!before) container.innerHTML = '';

    if (!before && data.messages.length === 0) {
        const isDm = currentChannel.type === 'direct';
        const icon = isDm ? 'chat_bubble_outline' : 'tag';
        const title = isDm ? t('msg.welcomeDm') : `${t('msg.welcome')} #${currentChannel.name}`;
        const hint = isDm ? t('msg.welcomeDmHint') : t('msg.welcomeHint');
        container.innerHTML = `<div class="msg-welcome">
            <span class="material-icons-round msg-welcome-icon">${icon}</span>
            <h3 class="msg-welcome-title">${esc(title)}</h3>
            <p class="msg-welcome-hint">${esc(hint)}</p>
        </div>`;
    }

    renderMessages(data.messages, !before);
    if (!before) { container.scrollTop = container.scrollHeight; setupScrollBtn(container); }

    if (!before) {
        container.onscroll = async () => {
            updateScrollBtn(container);
            if (container.scrollTop < 50 && !loadingOlderMessages && data.has_more) {
                const firstMsg = container.querySelector('.msg-row[data-id]');
                if (firstMsg) {
                    loadingOlderMessages = true;
                    const oldScrollHeight = container.scrollHeight;
                    const res2 = await apiFetch(`/channels/${currentChannel.slug}/messages?before=${firstMsg.dataset.id}`);
                    if (res2 && res2.ok) {
                        const data2 = await res2.json();
                        renderMessages(data2.messages, false, true);
                        container.scrollTop = container.scrollHeight - oldScrollHeight;
                        data.has_more = data2.has_more;
                    }
                    loadingOlderMessages = false;
                }
            }
        };
    }
}

function setupScrollBtn(container) {
    let btn = document.getElementById('msg-scroll-btn');
    if (!btn) {
        btn = document.createElement('button');
        btn.id = 'msg-scroll-btn';
        btn.className = 'msg-scroll-btn';
        btn.innerHTML = '<span class="material-icons-round">keyboard_arrow_down</span>';
        btn.onclick = () => { container.scrollTop = container.scrollHeight; btn.classList.remove('visible'); };
        container.parentElement.appendChild(btn);
    }
    btn.classList.remove('visible');
}
function updateScrollBtn(container) {
    const btn = document.getElementById('msg-scroll-btn');
    if (!btn) return;
    btn.classList.toggle('visible', container.scrollHeight - container.scrollTop - container.clientHeight > 200);
}

function renderMessages(messages, scroll = true, prepend = false) {
    const container = document.getElementById('msg-messages');
    let html = '';
    let lastDate = '';
    let lastSender = '';
    let lastTime = '';
    const existingIds = new Set();
    container.querySelectorAll('.msg-row[data-id]').forEach(el => existingIds.add(el.dataset.id));

    messages.forEach(msg => {
        if (existingIds.has(msg.id)) return;
        const date = formatDate(msg.timestamp);
        if (date !== lastDate) {
            html += `<div class="msg-date-sep">${date}</div>`;
            lastDate = date;
            lastSender = '';
        }
        if (msg.type === 'system' || msg.type === 'call') {
            html += `<div class="msg-system">${esc(msg.text)}</div>`;
            lastSender = '';
            return;
        }
        const time = formatTime(msg.timestamp);
        const isCompact = msg.sender === lastSender && time === lastTime;
        const isOwn = msg.sender === currentUser;
        const ownClass = isOwn ? ' msg-own' : '';
        const senderUser = allUsers.find(u => u.username === msg.sender);
        const avatarContent = senderUser?.avatar_path
            ? `<img src="${srv(senderUser.avatar_path)}">`
            : (msg.sender_name || msg.sender).charAt(0).toUpperCase();

        // Actions: reply + reaction + edit/delete
        let actionBtns = `<button class="msg-action-btn" onclick="startReply('${msg.id}','${esc(msg.sender_name || msg.sender).replace(/'/g,"\\'")}','${esc((msg.text||'').substring(0,80)).replace(/'/g,"\\'")}')"><span class="material-icons-round">reply</span></button>`;
        actionBtns += `<button class="msg-action-btn" onclick="showReactionPicker(event,'${msg.id}')"><span class="material-icons-round">add_reaction</span></button>`;
        actionBtns += `<button class="msg-action-btn" onclick="showForwardModal('${msg.id}')"><span class="material-icons-round">shortcut</span></button>`;
        const isPinned = msg.is_pinned || pinnedMessages.some(p => p.id === msg.id);
        actionBtns += `<button class="msg-action-btn" onclick="togglePin('${msg.id}')" title="${isPinned ? 'Unpin' : 'Pin'}"><span class="material-icons-round">${isPinned ? 'push_pin' : 'push_pin'}</span></button>`;
        if (isOwn) {
            actionBtns += `<button class="msg-action-btn" onclick="startEditMessage('${msg.id}')"><span class="material-icons-round">edit</span></button>`;
            actionBtns += `<button class="msg-action-btn" onclick="deleteMessage('${msg.id}')"><span class="material-icons-round">delete</span></button>`;
        } else if (userRole === 'admin') {
            actionBtns += `<button class="msg-action-btn" onclick="deleteMessage('${msg.id}')"><span class="material-icons-round">delete</span></button>`;
        }
        const actions = `<div class="msg-actions">${actionBtns}</div>`;

        // Checkmarks for own messages
        let checkmarks = '';
        if (isOwn) {
            const readBy = (msg.read_by || []).filter(u => u !== currentUser);
            const isDm = currentChannel && currentChannel.type === 'direct';
            if (readBy.length > 0) {
                const cls = isDm ? 'msg-checkmark read read-dm' : 'msg-checkmark read';
                checkmarks = `<span class="${cls}" data-msg-id="${msg.id}"><span class="material-icons-round">done_all</span></span>`;
            } else {
                checkmarks = `<span class="msg-checkmark" data-msg-id="${msg.id}"><span class="material-icons-round">done</span></span>`;
            }
        }

        // Reply preview
        let replyPreview = '';
        if (msg.reply_to_preview) {
            const rp = msg.reply_to_preview;
            replyPreview = `<div class="msg-reply-quote" onclick="scrollToMessage('${rp.id}')">
                <span class="msg-reply-sender">${esc(rp.sender_name)}</span>
                <span class="msg-reply-text">${esc(rp.text)}</span>
            </div>`;
        }

        // Reactions
        const reactions = renderReactions(msg);

        // Thread indicator
        const threadIndicator = (msg.reply_count > 0) ?
            `<div class="msg-thread-indicator" onclick="openThread('${msg.id}')">${msg.reply_count} ${msg.reply_count === 1 ? t('msg.reply1') : t('msg.replies')}</div>` : '';

        const editedBadge = msg.edited_at ? `<span class="msg-edited">${t('msg.edited')}</span>` : '';
        if (isCompact) {
            html += `<div class="msg-row msg-row-compact${ownClass}" data-id="${msg.id}">
                <div class="msg-body">
                    ${actions}
                    ${replyPreview}
                    <div class="msg-text" id="msg-text-${msg.id}">${formatMsgText(msg.text)} ${editedBadge}${checkmarks}</div>
                    ${renderMsgFile(msg)}
                    ${reactions}${threadIndicator}
                </div></div>`;
        } else {
            html += `<div class="msg-row${ownClass}" data-id="${msg.id}">
                <div class="msg-avatar">${avatarContent}</div>
                <div class="msg-body">
                    ${actions}
                    <div class="msg-header">
                        <span class="msg-sender">${esc(msg.sender_name || msg.sender)}</span>
                        <span class="msg-time">${time}</span>${editedBadge}
                    </div>
                    ${replyPreview}
                    <div class="msg-text" id="msg-text-${msg.id}">${formatMsgText(msg.text)}${checkmarks}</div>
                    ${renderMsgFile(msg)}
                    ${reactions}${threadIndicator}
                </div></div>`;
        }
        lastSender = msg.sender;
        lastTime = time;
    });

    if (prepend) container.insertAdjacentHTML('afterbegin', html);
    else container.insertAdjacentHTML('beforeend', html);
    if (scroll) container.scrollTop = container.scrollHeight;
}

function renderReactions(msg) {
    if (!msg.reactions || msg.reactions.length === 0) return '';
    return '<div class="msg-reactions">' + msg.reactions.map(r => {
        const isOwn = r.users.includes(currentUser);
        return `<button class="msg-reaction-chip${isOwn ? ' own' : ''}" onclick="toggleReaction('${msg.id}','${r.emoji}')">${r.emoji} ${r.count}</button>`;
    }).join('') + '</div>';
}

function scrollToMessage(msgId) {
    const el = document.querySelector(`.msg-row[data-id="${msgId}"]`);
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('msg-highlight');
        setTimeout(() => el.classList.remove('msg-highlight'), 2000);
    }
}

function renderMsgFile(msg) {
    if (!msg.file) return '';
    const f = msg.file;
    const fileUrl = srv(f.url);
    if (f.type === 'image') {
        return `<img class="msg-file-image" src="${fileUrl}" alt="${esc(f.name)}" onclick="window.open('${fileUrl}','_blank')">`;
    }
    const sizeStr = f.size ? formatFileSize(f.size) : '';
    return `<div class="msg-file-attach" onclick="window.open('${fileUrl}','_blank')">
        <span class="material-icons-round">attach_file</span>
        <span class="msg-file-name">${esc(f.name)}</span>
        <span class="msg-file-size">${sizeStr}</span>
    </div>`;
}

function formatMsgText(text) {
    if (!text) return '';
    let html = esc(text);
    html = html.replace(/@(\w+)/g, '<span class="mention">@$1</span>');
    // Replace URLs: show short filename for /uploads/ links, otherwise show domain+path
    html = html.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
        try {
            const url = new URL(match);
            if (url.pathname.startsWith('/uploads/')) {
                const fileName = decodeURIComponent(url.pathname.split('/').pop());
                return `<a href="${match}" target="_blank" rel="noopener" title="${match}">📎 ${esc(fileName)}</a>`;
            }
        } catch(e) {}
        return `<a href="${match}" target="_blank" rel="noopener">${match}</a>`;
    });
    return html;
}

// ── Message Input ──────────────────────────────────────────────
function setupMsgInput() {
    const input = document.getElementById('msg-input');
    if (!input) return;
    input.addEventListener('keydown', e => {
        const mentionList = document.getElementById('msg-mention-list');
        if (mentionList && mentionList.style.display !== 'none') {
            if (e.key === 'ArrowDown') { e.preventDefault(); moveMention(1); return; }
            if (e.key === 'ArrowUp') { e.preventDefault(); moveMention(-1); return; }
            if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); selectMention(); return; }
            if (e.key === 'Escape') { hideMentions(); return; }
        }
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            editingMessageId ? saveEditMessage() : sendMsg();
        }
    });
    input.addEventListener('input', () => {
        autoResizeTextarea(input);
        checkMentions(input);
        sendTyping(true);
    });
    // Paste files from clipboard
    input.addEventListener('paste', e => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.kind === 'file') {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) uploadChatFile([file]);
                return;
            }
        }
    });
    // Drag & drop files onto message area
    const msgArea = document.getElementById('main-area');
    if (msgArea) {
        msgArea.addEventListener('dragover', e => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            msgArea.classList.add('drag-over');
        });
        msgArea.addEventListener('dragleave', e => {
            if (!msgArea.contains(e.relatedTarget)) msgArea.classList.remove('drag-over');
        });
        msgArea.addEventListener('drop', e => {
            e.preventDefault();
            msgArea.classList.remove('drag-over');
            const files = e.dataTransfer?.files;
            if (files && files.length > 0 && currentChannel) {
                uploadChatFile(files);
            }
        });
    }
}

function autoResizeTextarea(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

async function sendMsg() {
    const input = document.getElementById('msg-input');
    const text = input.value.trim();
    if (!text || !currentChannel) return;
    const payload = { event: 'send_message', channel_id: currentChannel.id, text, type: 'text' };
    if (replyToMsg) payload.reply_to = replyToMsg.id;
    if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify(payload));
    } else {
        const body = { text, type: 'text' };
        if (replyToMsg) body.reply_to = replyToMsg.id;
        await apiFetch(`/channels/${currentChannel.slug}/messages`, { method: 'POST', body });
    }
    input.value = '';
    autoResizeTextarea(input);
    sendTyping(false);
    cancelReply();
}

// ── Message Edit / Delete ──────────────────────────────────────
function startEditMessage(msgId) {
    const textEl = document.getElementById(`msg-text-${msgId}`);
    if (!textEl) return;
    editingMessageId = msgId;
    const input = document.getElementById('msg-input');
    const raw = textEl.textContent.replace(/\s*\(ред\.\)\s*$/, '').replace(/\s*\(edited\)\s*$/, '').trim();
    input.value = raw;
    input.focus();
    autoResizeTextarea(input);
}

async function saveEditMessage() {
    if (!editingMessageId || !currentChannel) return;
    const input = document.getElementById('msg-input');
    const text = input.value.trim();
    if (!text) return;
    await apiFetch(`/channels/${currentChannel.slug}/messages/${editingMessageId}`, { method: 'PUT', body: { text } });
    editingMessageId = null;
    input.value = '';
    autoResizeTextarea(input);
}

async function deleteMessage(msgId) {
    if (!currentChannel) return;
    await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}`, { method: 'DELETE' });
}

// ── Reply ──────────────────────────────────────────────────────
function startReply(msgId, senderName, text) {
    replyToMsg = { id: msgId, sender_name: senderName, text };
    const preview = document.getElementById('reply-preview');
    if (!preview) {
        const area = document.getElementById('msg-input-area');
        const div = document.createElement('div');
        div.id = 'reply-preview';
        div.className = 'reply-preview';
        area.prepend(div);
    }
    const el = document.getElementById('reply-preview');
    el.innerHTML = `<div class="reply-preview-content">
        <span class="reply-preview-sender">${esc(senderName)}</span>
        <span class="reply-preview-text">${esc(text)}</span>
    </div><button class="reply-preview-close" onclick="cancelReply()"><span class="material-icons-round">close</span></button>`;
    el.style.display = 'flex';
    document.getElementById('msg-input').focus();
}
function cancelReply() {
    replyToMsg = null;
    const el = document.getElementById('reply-preview');
    if (el) el.style.display = 'none';
}

// ── Forward Message ───────────────────────────────────────────
let forwardMsgData = null;

function showForwardModal(msgId) {
    const row = document.querySelector(`.msg-row[data-id="${msgId}"]`);
    if (!row) return;
    const textEl = row.querySelector('.msg-text');
    const senderEl = row.querySelector('.msg-sender-name');
    const text = textEl ? textEl.textContent.trim() : '';
    const senderName = senderEl ? senderEl.textContent.trim() : '';
    forwardMsgData = { id: msgId, text, senderName };

    const list = document.getElementById('forward-channel-list');
    const available = channels.filter(ch => !currentChannel || ch.slug !== currentChannel.slug);

    if (available.length === 0) {
        list.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text3)">${t('forward.empty')}</div>`;
    } else {
        list.innerHTML = available.map(ch => {
            let icon, name;
            if (ch.type === 'direct') {
                const other = (ch.members || []).find(m => m !== currentUser) || '?';
                const otherUser = allUsers.find(u => u.username === other);
                name = otherUser ? otherUser.display_name : other;
                const avatarPath = otherUser?.avatar_path;
                icon = avatarPath
                    ? `<img src="${srv(avatarPath)}">`
                    : name.charAt(0).toUpperCase();
            } else {
                name = ch.name;
                icon = ch.type === 'private' ? '🔒' : '#';
            }
            return `<div class="forward-channel-item" data-name="${esc(name).toLowerCase()}" onclick="forwardToChannel('${ch.slug}')">
                <div class="forward-channel-icon">${icon}</div>
                <div class="forward-channel-name">${esc(name)}</div>
            </div>`;
        }).join('');
    }

    document.getElementById('forward-search').value = '';
    document.getElementById('forward-modal').style.display = 'flex';
}

function closeForwardModal() {
    document.getElementById('forward-modal').style.display = 'none';
    forwardMsgData = null;
}

function filterForwardList(query) {
    const q = query.toLowerCase().trim();
    document.querySelectorAll('#forward-channel-list .forward-channel-item').forEach(el => {
        el.style.display = (el.dataset.name || '').includes(q) ? '' : 'none';
    });
}

async function forwardToChannel(slug) {
    if (!forwardMsgData) return;
    const text = `↩ ${forwardMsgData.senderName}:\n${forwardMsgData.text}`;
    const res = await apiFetch(`/channels/${slug}/messages`, { method: 'POST', body: { text, type: 'text' } });
    if (res && res.ok) {
        showToast(t('forward.success'), 'success');
        closeForwardModal();
    } else {
        showToast(t('toast.forwardError'), 'error');
    }
}

// ── Reactions ──────────────────────────────────────────────────
const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '👏'];

function showReactionPicker(e, msgId) {
    e.stopPropagation();
    let picker = document.getElementById('reaction-picker');
    if (!picker) {
        picker = document.createElement('div');
        picker.id = 'reaction-picker';
        picker.className = 'reaction-picker';
        document.body.appendChild(picker);
    }
    picker.innerHTML = QUICK_REACTIONS.map(em =>
        `<button class="reaction-picker-btn" onclick="addReaction('${msgId}','${em}')">${em}</button>`
    ).join('');
    picker.style.display = 'flex';
    const rect = e.target.closest('.msg-row').getBoundingClientRect();
    picker.style.top = (rect.top - 50) + 'px';
    picker.style.left = Math.min(rect.left, window.innerWidth - 260) + 'px';
    const close = (ev) => { if (!ev.target.closest('.reaction-picker')) { picker.style.display = 'none'; document.removeEventListener('click', close); } };
    setTimeout(() => document.addEventListener('click', close), 0);
}

async function addReaction(msgId, emoji) {
    const picker = document.getElementById('reaction-picker');
    if (picker) picker.style.display = 'none';
    if (!currentChannel) return;
    await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/reactions`, { method: 'POST', body: { emoji } });
}

async function toggleReaction(msgId, emoji) {
    if (!currentChannel) return;
    // Check if already reacted
    const row = document.querySelector(`.msg-row[data-id="${msgId}"]`);
    const chip = row?.querySelector(`.msg-reaction-chip.own`);
    const isOwn = chip && chip.textContent.trim().startsWith(emoji);
    if (isOwn) {
        await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/reactions`, { method: 'DELETE', body: { emoji } });
    } else {
        await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/reactions`, { method: 'POST', body: { emoji } });
    }
}

function onReactionUpdated(data) {
    const row = document.querySelector(`.msg-row[data-id="${data.message_id}"]`);
    if (!row) return;
    let container = row.querySelector('.msg-reactions');
    if (!container) {
        const body = row.querySelector('.msg-body');
        container = document.createElement('div');
        container.className = 'msg-reactions';
        body.appendChild(container);
    }
    const reactions = data.reactions || [];
    if (reactions.length === 0) { container.remove(); return; }
    container.innerHTML = reactions.map(r => {
        const isOwn = r.users.includes(currentUser);
        return `<button class="msg-reaction-chip${isOwn ? ' own' : ''}" onclick="toggleReaction('${data.message_id}','${r.emoji}')">${r.emoji} ${r.count}</button>`;
    }).join('');
}

// ── Thread Panel ───────────────────────────────────────────────
async function openThread(msgId) {
    if (!currentChannel) return;
    const panel = document.getElementById('right-panel');
    panel.style.display = 'flex';
    const body = document.getElementById('right-panel-body');
    body.innerHTML = '<p style="padding:16px;color:var(--text2)">Loading...</p>';
    document.getElementById('right-panel-title').textContent = t('panel.thread');
    const res = await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/thread`);
    if (!res || !res.ok) return;
    const data = await res.json();
    let html = '';
    const allMsgs = [data.parent, ...data.replies];
    allMsgs.forEach(msg => {
        const isOwn = msg.sender === currentUser;
        const time = formatTime(msg.timestamp);
        html += `<div class="thread-msg${isOwn ? ' msg-own' : ''}">
            <div class="thread-msg-header">
                <span class="msg-sender">${esc(msg.sender_name || msg.sender)}</span>
                <span class="msg-time">${time}</span>
            </div>
            <div class="msg-text">${formatMsgText(msg.text)}</div>
        </div>`;
    });
    body.innerHTML = html;
}

// ── @Mention Autocomplete ──────────────────────────────────────
function checkMentions(input) {
    const text = input.value;
    const pos = input.selectionStart;
    const beforeCursor = text.substring(0, pos);
    const match = beforeCursor.match(/@(\w*)$/);
    if (match) {
        const query = match[1].toLowerCase();
        const filtered = allUsers.filter(u =>
            u.username !== currentUser &&
            (u.username.toLowerCase().includes(query) ||
             u.display_name.toLowerCase().includes(query) ||
             (u.nickname && u.nickname.toLowerCase().includes(query)))
        ).slice(0, 8);
        if (filtered.length > 0) { mentionUsers = filtered; mentionIndex = 0; showMentions(); return; }
    }
    hideMentions();
}
function showMentions() {
    const list = document.getElementById('msg-mention-list');
    list.style.display = 'block';
    list.innerHTML = mentionUsers.map((u, i) => {
        const av = u.avatar_path ? `<img src="${srv(u.avatar_path)}">` : u.display_name.charAt(0).toUpperCase();
        return `<div class="mention-item ${i === mentionIndex ? 'active' : ''}" onmousedown="selectMentionAt(${i})">
            <div class="mention-item-avatar">${av}</div>
            <span>${esc(u.display_name)} <span style="color:var(--text2)">@${esc(u.username)}</span></span>
        </div>`;
    }).join('');
}
function hideMentions() { document.getElementById('msg-mention-list').style.display = 'none'; mentionUsers = []; }
function moveMention(dir) { mentionIndex = Math.max(0, Math.min(mentionUsers.length - 1, mentionIndex + dir)); showMentions(); }
function selectMention() { selectMentionAt(mentionIndex); }
function selectMentionAt(idx) {
    const user = mentionUsers[idx];
    if (!user) return;
    const input = document.getElementById('msg-input');
    const text = input.value;
    const pos = input.selectionStart;
    const before = text.substring(0, pos);
    const after = text.substring(pos);
    const replaced = before.replace(/@\w*$/, `@${user.username} `);
    input.value = replaced + after;
    input.selectionStart = input.selectionEnd = replaced.length;
    input.focus();
    hideMentions();
}

// ── Emoji Picker ──────────────────────────────────────────────
const EMOJI_DATA = {
    'smileys': ['😀','😃','😄','😁','😆','😅','🤣','😂','🙂','😊','😇','🥰','😍','🤩','😘','😗','😋','😛','😜','🤪','😝','🤑','🤗','🤭','🫢','🤫','🤔','🫡','🤐','🤨','😐','😑','😶','🫥','😏','😒','🙄','😬','😮‍💨','🤥','😌','😔','😪','🤤','😴','😷','🤒','🤕','🤢','🤮','🥵','🥶','🥴','😵','🤯','🤠','🥳','🥸','😎','🤓','🧐','😕','🫤','😟','🙁','😮','😯','😲','😳','🥺','🥹','😦','😧','😨','😰','😥','😢','😭','😱','😖','😣','😞','😓','😩','😫','🥱','😤','😡','😠','🤬','😈','👿','💀','☠️','💩','🤡','👹','👺','👻','👽','👾','🤖'],
    'gestures': ['👋','🤚','🖐️','✋','🖖','🫱','🫲','🫳','🫴','👌','🤌','🤏','✌️','🤞','🫰','🤟','🤘','🤙','👈','👉','👆','🖕','👇','☝️','🫵','👍','👎','✊','👊','🤛','🤜','👏','🙌','🫶','👐','🤲','🤝','🙏','✍️','💅','🤳','💪','🦾','🦿','🦵','🦶','👂','🦻','👃','🧠','🫀','🫁','🦷','🦴','👀','👁️','👅','👄'],
    'people': ['👶','👧','🧒','👦','👩','🧑','👨','👩‍🦱','🧑‍🦱','👨‍🦱','👩‍🦰','🧑‍🦰','👨‍🦰','👱‍♀️','👱','👱‍♂️','👩‍🦳','🧑‍🦳','👨‍🦳','👩‍🦲','🧑‍🦲','👨‍🦲','🧔‍♀️','🧔','🧔‍♂️','👵','🧓','👴','👲','👳‍♀️','👳','👳‍♂️','🧕','👮‍♀️','👮','👮‍♂️','👷‍♀️','👷','👷‍♂️','💂‍♀️','💂','💂‍♂️','🕵️‍♀️','🕵️','🕵️‍♂️','👩‍⚕️','🧑‍⚕️','👨‍⚕️','👩‍🌾','🧑‍🌾','👨‍🌾'],
    'nature': ['🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐻‍❄️','🐨','🐯','🦁','🐮','🐷','🐸','🐵','🙈','🙉','🙊','🐒','🐔','🐧','🐦','🐤','🐣','🐥','🦆','🦅','🦉','🦇','🐺','🐗','🐴','🦄','🐝','🪱','🐛','🦋','🐌','🐞','🐜','🪰','🪲','🪳','🦟','🦗','🕷️','🦂','🐢','🐍','🦎','🦖','🦕','🐙','🦑','🦐','🦞','🦀','🐡','🐠','🐟','🐬','🐳','🐋','🦈','🪸','🐊','🐅','🐆','🦓','🦍','🦧','🐘','🦛','🦏','🐪','🐫','🦒'],
    'food': ['🍏','🍎','🍐','🍊','🍋','🍌','🍉','🍇','🍓','🫐','🍈','🍒','🍑','🥭','🍍','🥥','🥝','🍅','🍆','🥑','🥦','🥬','🥒','🌶️','🫑','🌽','🥕','🫒','🧄','🧅','🥔','🍠','🫘','🥐','🥯','🍞','🥖','🥨','🧀','🥚','🍳','🧈','🥞','🧇','🥓','🥩','🍗','🍖','🦴','🌭','🍔','🍟','🍕','🫓','🥪','🥙','🧆','🌮','🌯','🫔','🥗','🥘','🫕','🥫','🍝','🍜','🍲','🍛','🍣','🍱','🥟','🦪','🍤','🍙','🍚','🍘','🍥','🥠','🥮','🍢','🍡','🍧','🍨','🍦','🥧','🧁','🍰','🎂','🍮','🍭','🍬','🍫','🍿','🍩','🍪'],
    'activities': ['⚽','🏀','🏈','⚾','🥎','🎾','🏐','🏉','🥏','🎱','🪀','🏓','🏸','🏒','🏑','🥍','🏏','🪃','🥅','⛳','🪁','🏹','🎣','🤿','🥊','🥋','🎽','🛹','🛼','🛷','⛸️','🥌','🎿','⛷️','🏂','🪂','🏋️','🤸','⛹️','🤺','🏇','🧘','🏄','🏊','🤽','🚣','🧗','🚵','🚴','🏆','🥇','🥈','🥉','🏅','🎖️','🏵️','🎗️','🎪','🎭','🎨','🎬','🎤','🎧','🎼','🎹','🥁','🪘','🎷','🎺','🪗','🎸','🪕','🎻','🎲','♟️','🎯','🎳','🎮','🕹️'],
    'travel': ['🚗','🚕','🚙','🚌','🚎','🏎️','🚓','🚑','🚒','🚐','🛻','🚚','🚛','🚜','🛵','🏍️','🛺','🚲','🛴','🛹','🛼','🚏','🛣️','🛤️','🛞','⛽','🚨','🚥','🚦','🛑','🚧','⚓','🛟','⛵','🛶','🚤','🛳️','⛴️','🛥️','🚢','✈️','🛩️','🛫','🛬','🪂','💺','🚁','🚟','🚠','🚡','🛰️','🚀','🌍','🌎','🌏','🌐','🗺️','🧭','🏔️','⛰️','🌋','🗻','🏕️','🏖️','🏜️','🏝️','🏞️'],
    'objects': ['⌚','📱','📲','💻','⌨️','🖥️','🖨️','🖱️','🖲️','🕹️','🗜️','💾','💿','📀','📼','📷','📸','📹','🎥','📽️','🎞️','📞','☎️','📟','📠','📺','📻','🎙️','🎚️','🎛️','🧭','⏱️','⏲️','⏰','🕰️','⌛','⏳','📡','🔋','🔌','💡','🔦','🕯️','🪔','🧯','🛢️','💸','💵','💴','💶','💷','🪙','💰','💳','💎','⚖️','🪜','🧰','🪛','🔧','🔨','⚒️','🛠️','⛏️','🪚','🔩','⚙️','🪤','🧱','⛓️','🧲','🔫','💣','🧨','🪓','🔪','🗡️','⚔️','🛡️','🚬','⚰️','🪦','⚱️','🏺','🔮','📿','🧿','🪬','💈','⚗️','🔭','🔬','🕳️','🩹','🩺','🩻','🩼','💊','💉','🩸','🧬','🦠','🧫','🧪','🌡️','🧹','🪠','🧺','🧻','🚽','🚰','🚿','🛁','🛀','🪥','🪒','🧴','🧷','🧹','🧺','🔑','🗝️','🚪','🪑','🛋️','🛏️','🛌','🧸','🪆','🖼️','🪞','🪟','🛒','🎁','🎈','🎏','🎀','🪄','🪅','🎊','🎉','🎎','🏮','🎐','🧧','✉️','📩','📨','📧','💌','📥','📤','📦','🏷️','🪧','📪','📫','📬','📭','📮','📯','📜','📃','📄','📑','🧾','📊','📈','📉','🗒️','🗓️','📆','📅','🗑️','📇','🗃️','🗳️','🗄️','📋','📁','📂','🗂️','🗞️','📰','📓','📔','📒','📕','📗','📘','📙','📚','📖','🔖','🧷','🔗','📎','🖇️','📐','📏','🧮','📌','📍','✂️','🖊️','🖋️','✒️','🖌️','🖍️','📝','✏️','🔍','🔎','🔏','🔐','🔒','🔓'],
    'symbols': ['❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❤️‍🔥','❤️‍🩹','❣️','💕','💞','💓','💗','💖','💘','💝','💟','☮️','✝️','☪️','🕉️','☸️','✡️','🔯','🕎','☯️','☦️','🛐','⛎','♈','♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓','🆔','⚛️','🉑','☢️','☣️','📴','📳','🈶','🈚','🈸','🈺','🈷️','✴️','🆚','💮','🉐','㊙️','㊗️','🈴','🈵','🈹','🈲','🅰️','🅱️','🆎','🆑','🅾️','🆘','❌','⭕','🛑','⛔','📛','🚫','💯','💢','♨️','🚷','🚯','🚳','🚱','🔞','📵','🚭','❗','❕','❓','❔','‼️','⁉️','🔅','🔆','〽️','⚠️','🚸','🔱','⚜️','🔰','♻️','✅','🈯','💹','❇️','✳️','❎','🌐','💠','Ⓜ️','🌀','💤','🏧','🚾','♿','🅿️','🛗','🈳','🈂️','🛂','🛃','🛄','🛅','🚹','🚺','🚼','⚧️','🚻','🚮','🎦','📶','🈁','🔣','ℹ️','🔤','🔡','🔠','🆖','🆗','🆙','🆒','🆕','🆓','0️⃣','1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟','🔢','#️⃣','*️⃣','⏏️','▶️','⏸️','⏯️','⏹️','⏺️','⏭️','⏮️','⏩','⏪','⏫','⏬','◀️','🔼','🔽','➡️','⬅️','⬆️','⬇️','↗️','↘️','↙️','↖️','↕️','↔️','↪️','↩️','⤴️','⤵️','🔀','🔁','🔂','🔄','🔃','🎵','🎶','➕','➖','➗','✖️','🟰','♾️','💲','💱','™️','©️','®️','〰️','➰','➿','🔚','🔙','🔛','🔝','🔜','✔️','☑️','🔘','🔴','🟠','🟡','🟢','🔵','🟣','⚫','⚪','🟤','🔺','🔻','🔸','🔹','🔶','🔷','🔳','🔲','▪️','▫️','◾','◽','◼️','◻️','🟥','🟧','🟨','🟩','🟦','🟪','⬛','⬜','🟫','🔈','🔇','🔉','🔊','🔔','🔕','📣','📢','💬','💭','🗯️','♠️','♣️','♥️','♦️','🃏','🎴','🀄','🕐','🕑','🕒','🕓','🕔','🕕','🕖','🕗','🕘','🕙','🕚','🕛'],
};
const EMOJI_CAT_ICONS = { smileys:'😀', gestures:'👋', people:'👤', nature:'🐶', food:'🍎', activities:'⚽', travel:'🚗', objects:'💡', symbols:'❤️' };
const EMOJI_CAT_NAMES = { smileys:'Smileys', gestures:'Gestures', people:'People', nature:'Nature', food:'Food', activities:'Activities', travel:'Travel', objects:'Objects', symbols:'Symbols' };

function toggleEmojiPicker() {
    const picker = document.getElementById('emoji-picker');
    if (picker.style.display === 'none') {
        renderEmojiPicker();
        picker.style.display = 'flex';
    } else {
        picker.style.display = 'none';
    }
}

function renderEmojiPicker(filter = '', activeCat = null) {
    const picker = document.getElementById('emoji-picker');
    const recent = JSON.parse(localStorage.getItem('ch_recent_emoji') || '[]');

    let tabs = Object.keys(EMOJI_DATA).map(cat => {
        const active = activeCat === cat ? 'active' : '';
        return `<button class="emoji-cat-tab ${active}" onclick="filterEmojiCategory('${cat}')" title="${EMOJI_CAT_NAMES[cat]}">${EMOJI_CAT_ICONS[cat]}</button>`;
    }).join('');

    let grid = '';
    if (filter) {
        // Show all matching
        Object.values(EMOJI_DATA).flat().forEach(em => {
            grid += `<span class="emoji-item" onclick="insertEmoji('${em}')">${em}</span>`;
        });
    } else if (activeCat) {
        (EMOJI_DATA[activeCat] || []).forEach(em => {
            grid += `<span class="emoji-item" onclick="insertEmoji('${em}')">${em}</span>`;
        });
    } else {
        // Show recent + smileys
        if (recent.length > 0) {
            grid += '<div class="emoji-section-label">Recent</div>';
            recent.forEach(em => { grid += `<span class="emoji-item" onclick="insertEmoji('${em}')">${em}</span>`; });
            grid += '<div class="emoji-section-label">' + EMOJI_CAT_NAMES.smileys + '</div>';
        }
        EMOJI_DATA.smileys.forEach(em => {
            grid += `<span class="emoji-item" onclick="insertEmoji('${em}')">${em}</span>`;
        });
    }

    picker.innerHTML = `
        <div class="emoji-search-bar">
            <input type="text" class="emoji-search" placeholder="Search..." oninput="onEmojiSearch(this.value)">
        </div>
        <div class="emoji-tabs">${tabs}</div>
        <div class="emoji-grid">${grid}</div>
    `;
}

function filterEmojiCategory(cat) { renderEmojiPicker('', cat); }
function onEmojiSearch(q) {
    // Simple: just switch to showing category with most results — or show all
    renderEmojiPicker(q);
}

function insertEmoji(emoji) {
    const input = document.getElementById('msg-input');
    const pos = input.selectionStart;
    const text = input.value;
    input.value = text.substring(0, pos) + emoji + text.substring(pos);
    input.selectionStart = input.selectionEnd = pos + emoji.length;
    input.focus();
    // Add to recent
    let recent = JSON.parse(localStorage.getItem('ch_recent_emoji') || '[]');
    recent = [emoji, ...recent.filter(e => e !== emoji)].slice(0, 32);
    localStorage.setItem('ch_recent_emoji', JSON.stringify(recent));
    document.getElementById('emoji-picker').style.display = 'none';
}

// ── Typing ─────────────────────────────────────────────────────
function sendTyping(isTyping) {
    if (!ws || ws.readyState !== 1 || !currentChannel) return;
    if (isTyping) {
        if (!typingTimeout) ws.send(JSON.stringify({ event: 'typing', channel_id: currentChannel.id, is_typing: true }));
        clearTimeout(typingTimeout);
        typingTimeout = setTimeout(() => {
            ws.send(JSON.stringify({ event: 'typing', channel_id: currentChannel.id, is_typing: false }));
            typingTimeout = null;
        }, 3000);
    } else {
        clearTimeout(typingTimeout);
        typingTimeout = null;
        ws.send(JSON.stringify({ event: 'typing', channel_id: currentChannel.id, is_typing: false }));
    }
}

// ── File upload to chat ────────────────────────────────────────
function triggerChatFileUpload() { document.getElementById('chat-file-input').click(); }
async function uploadChatFile(files) {
    if (!files.length || !currentChannel) return;
    const formData = new FormData();
    formData.append('file', files[0]);
    const res = await apiFetch('/files/upload-to-chat', { method: 'POST', body: formData });
    if (!res || !res.ok) return;
    const data = await res.json();
    if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ event: 'send_message', channel_id: currentChannel.id, text: '', type: 'file', file: data.file }));
    } else {
        await apiFetch(`/channels/${currentChannel.slug}/messages`, { method: 'POST', body: { text: '', type: 'file', file: data.file } });
    }
    document.getElementById('chat-file-input').value = '';
}

// ── WebSocket ──────────────────────────────────────────────────
let wsPingInterval = null;

// Decode a JWT's exp claim; treat missing/malformed or <30s-to-expiry as "stale".
function isAccessTokenExpiring(jwt) {
    try {
        const payload = JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
        if (!payload.exp) return true;
        return payload.exp * 1000 < Date.now() + 30000;
    } catch (_) {
        return true;
    }
}

async function connectWebSocket() {
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    clearInterval(wsPingInterval); wsPingInterval = null;
    if (!token) return;

    // The access token is short-lived (30 min). Refresh it before (re)connecting
    // so an expired token doesn't get the WS handshake rejected (403) and then
    // retried forever with the same dead token. Mirrors the REST apiFetch flow.
    if (isAccessTokenExpiring(token)) {
        const ok = await refreshAccessToken();
        if (!ok) { logout(); return; }
    }

    const base = getServerBase();
    let wsHost, wsProto;
    if (base) {
        const u = new URL(base);
        wsProto = u.protocol === 'https:' ? 'wss:' : 'ws:';
        wsHost = u.host;
    } else {
        wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        wsHost = location.host;
    }
    ws = new WebSocket(`${wsProto}//${wsHost}/api/ws/messaging?token=${token}`);
    ws.onopen = () => {
        clearTimeout(wsReconnectTimer);
        wsPingInterval = setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ event: 'ping' })); }, 25000);
    };
    ws.onmessage = e => { handleWsEvent(JSON.parse(e.data)); };
    ws.onclose = () => { clearInterval(wsPingInterval); wsPingInterval = null; wsReconnectTimer = setTimeout(connectWebSocket, 3000); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

// Reconnect WebSocket when iOS PWA returns to foreground
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && token) {
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
            clearTimeout(wsReconnectTimer);
            connectWebSocket();
        }
        // Also reload unread counts when returning from background
        loadChannels();
    }
});

// Reconnect on network recovery
window.addEventListener('online', () => {
    if (token) connectWebSocket();
});

function handleWsEvent(data) {
    switch (data.event) {
        case 'new_message': onNewMessage(data.message); break;
        case 'message_edited': onMessageEdited(data); break;
        case 'message_deleted': onMessageDeleted(data); break;
        case 'user_typing': onTyping(data); break;
        case 'presence': onPresence(data); break;
        case 'messages_read': onMessagesRead(data); break;
        case 'reaction_updated': onReactionUpdated(data); break;
        case 'federation_request': onFederationRequest(data); break;
        case 'federation_linked': case 'federation_declined': loadFederation(); break;
        case 'channel_created': case 'channel_updated': case 'channel_deleted': loadChannels(); break;
        case 'member_joined': case 'member_left':
            if (data.event === 'member_left' && data.username === currentUser && currentChannel && currentChannel.id === data.channel_id) {
                currentChannel = null;
                document.getElementById('msg-empty-state').style.display = 'flex';
                document.getElementById('msg-messages').style.display = 'none';
                document.getElementById('msg-input-area').style.display = 'none';
                closeChannelSettingsDialog();
            } else if (currentChannel && currentChannel.id === data.channel_id) {
                currentChannel.member_details = null;
                loadRightPanelMembers();
            }
            loadChannels(); break;
        case 'call_invite': onCallStarted(data); break;
        case 'call_started': onCallStarted(data); break; // legacy
        case 'call_ended': onCallEnded(data); break;
        case 'call_answered': onCallAnswered(data); break;
        case 'call_media_updated': onCallMediaUpdated(data); break;
        case 'user_updated': onUserUpdated(data); break;
        case 'message_pinned': onMessagePinned(data); break;
        case 'message_unpinned': onMessageUnpinned(data); break;
        case 'task_created': case 'task_updated': case 'task_deleted':
        case 'task_status_changed': case 'subtask_updated':
        case 'task_comment_added': case 'project_created':
        case 'project_updated': case 'project_deleted':
        case 'project_member_added': case 'project_member_removed':
            handleTaskWsEvent(data); break;
        case 'pong': break;
    }
}

function onNewMessage(msg) {
    const ch = channels.find(c => c.id === msg.channel_id);
    if (ch) {
        ch.last_msg_timestamp = msg.timestamp;
        if (currentChannel && currentChannel.id === msg.channel_id) {
            renderMessages([msg], true);
            if (msg.sender !== currentUser) ws.send(JSON.stringify({ event: 'mark_read', channel_id: msg.channel_id }));
        } else {
            ch.unread_count = (ch.unread_count || 0) + 1;
        }
    } else { loadChannels(); }
    renderSidebar();
}
function onMessageEdited(data) {
    const el = document.getElementById(`msg-text-${data.message_id}`);
    if (el) el.innerHTML = formatMsgText(data.text) + ` <span class="msg-edited">${t('msg.edited')}</span>`;
}
function onMessageDeleted(data) {
    const row = document.querySelector(`.msg-row[data-id="${data.message_id}"]`);
    if (row) row.remove();
}
let typingUsers = {};
function onTyping(data) {
    if (!currentChannel || data.channel_id !== currentChannel.id) return;
    if (data.username === currentUser) return;
    if (data.is_typing) {
        typingUsers[data.username] = data.display_name;
        clearTimeout(typingUsers['_timer_' + data.username]);
        typingUsers['_timer_' + data.username] = setTimeout(() => { delete typingUsers[data.username]; delete typingUsers['_timer_' + data.username]; updateTypingIndicator(); }, 5000);
    } else {
        delete typingUsers[data.username];
        clearTimeout(typingUsers['_timer_' + data.username]);
        delete typingUsers['_timer_' + data.username];
    }
    updateTypingIndicator();
}
function updateTypingIndicator() {
    const el = document.getElementById('msg-typing');
    const textEl = document.getElementById('msg-typing-text');
    const names = Object.entries(typingUsers).filter(([k]) => !k.startsWith('_timer_')).map(([, v]) => v);
    if (names.length === 0) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    if (names.length === 1) textEl.textContent = `${names[0]} is typing...`;
    else if (names.length === 2) textEl.textContent = `${names[0]} and ${names[1]} are typing...`;
    else textEl.textContent = `${names.length} people are typing...`;
}
function onPresence(data) {
    onlineUsers[data.username] = { online: data.online, last_seen: data.last_seen };
    renderSidebar();
    if (currentChannel) { updateTopbar(); loadRightPanelMembers(); }
}
function onUserUpdated(data) {
    const updatedUser = data.user;
    if (!updatedUser) return;
    // Update in allUsers array
    const idx = allUsers.findIndex(u => u.username === updatedUser.username);
    if (idx >= 0) {
        allUsers[idx] = { ...allUsers[idx], ...updatedUser };
    } else {
        allUsers.push(updatedUser);
    }
    // If this is the current user, update global state
    if (updatedUser.username === currentUser) {
        if (updatedUser.display_name) {
            displayName = updatedUser.display_name;
            localStorage.setItem('ch_display', displayName);
        }
        if (updatedUser.avatar_path !== undefined) {
            currentUserAvatar = updatedUser.avatar_path || '';
        }
        updateSidebarUser();
    }
    // Update member_details in current channel if applicable
    if (currentChannel && currentChannel.member_details) {
        const mi = currentChannel.member_details.findIndex(m => m.username === updatedUser.username);
        if (mi >= 0) {
            currentChannel.member_details[mi] = { ...currentChannel.member_details[mi], ...updatedUser };
        }
    }
    // Refresh UI
    renderSidebar();
    if (currentChannel) { updateTopbar(); loadRightPanelMembers(); }
}
function onMessagesRead(data) {
    // Update checkmarks for messages that were read
    const reader = data.username;
    if (reader === currentUser) return; // We don't need to update our own reads
    const isDm = currentChannel && currentChannel.type === 'direct';
    const msgIds = data.message_ids || [];
    msgIds.forEach(id => {
        const el = document.querySelector(`.msg-checkmark[data-msg-id="${id}"]`);
        if (el) {
            el.querySelector('.material-icons-round').textContent = 'done_all';
            el.classList.add('read');
            if (isDm) el.classList.add('read-dm');
        }
    });
    // Also update for channel-level read event (no specific message_ids)
    if (!msgIds.length && data.channel_id && currentChannel && data.channel_id === currentChannel.id) {
        document.querySelectorAll('.msg-checkmark').forEach(el => {
            el.querySelector('.material-icons-round').textContent = 'done_all';
            el.classList.add('read');
            if (isDm) el.classList.add('read-dm');
        });
    }
}

// ── Pinned Messages ────────────────────────────────────────────
async function loadPinnedMessages() {
    if (!currentChannel) return;
    try {
        const res = await apiFetch(`/channels/${currentChannel.slug}/pinned`);
        if (res && res.ok) {
            pinnedMessages = await res.json();
            pinnedBarIndex = 0;
            updatePinnedBar();
        }
    } catch (e) { console.error('loadPinnedMessages:', e); }
}

function updatePinnedBar() {
    const bar = document.getElementById('msg-pinned-bar');
    if (!bar) return;
    if (pinnedMessages.length === 0) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    const msg = pinnedMessages[pinnedBarIndex % pinnedMessages.length];
    const label = pinnedMessages.length > 1
        ? `Pinned (${(pinnedBarIndex % pinnedMessages.length) + 1}/${pinnedMessages.length})`
        : 'Pinned message';
    bar.querySelector('.pinned-label').textContent = label;
    document.getElementById('pinned-bar-text').textContent = msg.text || msg.sender_name || '';
}

function onPinnedBarClick() {
    if (pinnedMessages.length === 0) return;
    const msg = pinnedMessages[pinnedBarIndex % pinnedMessages.length];
    scrollToMessage(msg.id);
    pinnedBarIndex = (pinnedBarIndex + 1) % pinnedMessages.length;
    updatePinnedBar();
}

async function togglePin(msgId) {
    if (!currentChannel) return;
    const isPinned = pinnedMessages.some(p => p.id === msgId);
    if (isPinned) {
        await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/pin`, { method: 'DELETE' });
    } else {
        await apiFetch(`/channels/${currentChannel.slug}/messages/${msgId}/pin`, { method: 'POST' });
    }
}

function onMessagePinned(data) {
    if (!currentChannel || data.channel_id !== currentChannel.id) return;
    if (data.message && !pinnedMessages.some(p => p.id === data.message.id)) {
        pinnedMessages.push(data.message);
    }
    updatePinnedBar();
}

function onMessageUnpinned(data) {
    if (!currentChannel || data.channel_id !== currentChannel.id) return;
    pinnedMessages = pinnedMessages.filter(p => p.id !== data.message_id);
    if (pinnedBarIndex >= pinnedMessages.length) pinnedBarIndex = 0;
    updatePinnedBar();
}

// ── Channel Avatar Upload ──────────────────────────────────────
async function uploadChannelAvatarFile(files) {
    if (!files || !files.length || !currentChannel) return;
    const formData = new FormData();
    formData.append('file', files[0]);
    try {
        const res = await fetch(`${API}/channels/${currentChannel.slug}/avatar`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData,
        });
        if (res.ok) {
            const data = await res.json();
            currentChannel.avatar_path = data.avatar_path;
            // Update preview
            const preview = document.getElementById('ch-avatar-preview');
            if (preview) preview.innerHTML = `<img src="${srv(data.avatar_path)}" style="width:100%;height:100%;object-fit:cover;">`;
            updateTopbar();
            await loadChannels();
        }
    } catch (e) { console.error('uploadChannelAvatar:', e); }
}

// ── New Chat Dialog (unified) ──────────────────────────────────
function showNewChatDialog() {
    document.getElementById('new-chat-dialog').style.display = 'flex';
    document.getElementById('newchat-channel-name').value = '';
    document.getElementById('newchat-channel-desc').value = '';
    document.getElementById('newchat-slug-preview').textContent = '-';
    const list = document.getElementById('newchat-members-list');
    const searchHtml = `<input type="text" class="newchat-search-input" placeholder="${t('search.people')}" oninput="filterNewChatMemberList(this.value)">`;
    const membersHtml = allUsers.filter(u => u.username !== currentUser).map(u =>
        `<label class="member-check-item" data-username="${u.username}" data-name="${(u.display_name||'').toLowerCase()} ${(u.nickname||'').toLowerCase()}"><input type="checkbox" value="${u.username}"> ${esc(u.display_name)} (@${u.username})</label>`
    ).join('');
    list.innerHTML = searchHtml + `<div id="newchat-member-items">${membersHtml}</div>`;
    document.getElementById('newchat-channel-name').oninput = async function() {
        const name = this.value.trim();
        if (!name) { document.getElementById('newchat-slug-preview').textContent = '-'; return; }
        const res = await apiFetch(`/slugify?name=${encodeURIComponent(name)}`);
        if (res && res.ok) { const d = await res.json(); document.getElementById('newchat-slug-preview').textContent = d.slug || '-'; }
    };
}
function closeNewChatDialog() { document.getElementById('new-chat-dialog').style.display = 'none'; }
function renderDmUserList(users) {
    return users.map(u => {
        const av = u.avatar_path ? `<img src="${srv(u.avatar_path)}">` : u.display_name.charAt(0).toUpperCase();
        return `<div class="dm-user-item" onclick="startDMFromNewChat('${u.username}')">
            <div class="dm-user-avatar">${av}</div>
            <span>${esc(u.display_name)} <span style="color:var(--text2)">@${u.username}</span></span>
        </div>`;
    }).join('');
}
function filterNewChatDmList(query) {
    const q = query.toLowerCase();
    const filtered = allUsers.filter(u => u.username !== currentUser && (
        u.username.toLowerCase().includes(q) ||
        u.display_name.toLowerCase().includes(q) ||
        (u.nickname && u.nickname.toLowerCase().includes(q))
    ));
    document.getElementById('newchat-dm-items').innerHTML = renderDmUserList(filtered);
}
function filterNewChatMemberList(query) {
    const q = query.toLowerCase();
    document.querySelectorAll('#newchat-member-items .member-check-item').forEach(el => {
        const name = el.dataset.name || '';
        const username = el.dataset.username || '';
        el.style.display = (name.includes(q) || username.includes(q)) ? '' : 'none';
    });
}
async function startDMFromNewChat(username) {
    const res = await apiFetch('/channels', { method: 'POST', body: { type: 'direct', participant: username } });
    if (!res || !res.ok) return;
    const ch = await res.json();
    closeNewChatDialog();
    await loadChannels();
    openChannel(ch.slug);
}
async function createChannelFromNewChat() {
    const name = document.getElementById('newchat-channel-name').value.trim();
    if (!name) return;
    const desc = document.getElementById('newchat-channel-desc').value.trim();
    const members = Array.from(document.querySelectorAll('#newchat-members-list input:checked')).map(c => c.value);
    const type = members.length > 0 ? 'private' : 'public';
    const res = await apiFetch('/channels', { method: 'POST', body: { name, description: desc, type, members } });
    if (!res || !res.ok) return;
    const ch = await res.json();
    closeNewChatDialog();
    await loadChannels();
    openChannel(ch.slug);
    showToast(t('toast.channelCreated'), 'success');
}

// ── Legacy channel/DM dialogs (kept for backward compat) ──────
function showCreateChannelDialog() {
    document.getElementById('create-channel-dialog').style.display = 'flex';
    document.getElementById('channel-name-input').value = '';
    document.getElementById('channel-desc-input').value = '';
    document.getElementById('channel-slug-preview').textContent = '-';
    const list = document.getElementById('channel-members-list');
    list.innerHTML = allUsers.filter(u => u.username !== currentUser).map(u =>
        `<label class="member-check-item"><input type="checkbox" value="${u.username}"> ${esc(u.display_name)} (@${u.username})</label>`
    ).join('');
    document.getElementById('channel-name-input').oninput = async function() {
        const name = this.value.trim();
        if (!name) { document.getElementById('channel-slug-preview').textContent = '-'; return; }
        const res = await apiFetch(`/slugify?name=${encodeURIComponent(name)}`);
        if (res && res.ok) { const d = await res.json(); document.getElementById('channel-slug-preview').textContent = d.slug || '-'; }
    };
}
function closeCreateChannelDialog() { document.getElementById('create-channel-dialog').style.display = 'none'; }
async function createChannel() {
    const name = document.getElementById('channel-name-input').value.trim();
    if (!name) return;
    const desc = document.getElementById('channel-desc-input').value.trim();
    const type = document.querySelector('input[name="channel-type"]:checked').value;
    const members = Array.from(document.querySelectorAll('#channel-members-list input:checked')).map(c => c.value);
    const res = await apiFetch('/channels', { method: 'POST', body: { name, description: desc, type, members } });
    if (!res || !res.ok) return;
    const ch = await res.json();
    closeCreateChannelDialog();
    await loadChannels();
    openChannel(ch.slug);
    showToast(t('toast.channelCreated'), 'success');
}
function showBrowseChannelsDialog() {
    document.getElementById('browse-channels-dialog').style.display = 'flex';
    loadBrowseChannels();
}
function closeBrowseChannelsDialog() { document.getElementById('browse-channels-dialog').style.display = 'none'; }
async function loadBrowseChannels() {
    const res = await apiFetch('/channels/public');
    if (!res || !res.ok) return;
    const pubChannels = await res.json();
    document.getElementById('browse-channels-body').innerHTML = pubChannels.map(ch => {
        const btn = ch.is_member
            ? `<button class="btn-action" onclick="openChannel('${ch.slug}'); closeBrowseChannelsDialog();">Open</button>`
            : `<button class="btn-action btn-accent" onclick="joinChannel('${ch.slug}')">Join</button>`;
        return `<div class="browse-channel-item">
            <div class="browse-channel-info">
                <div class="browse-channel-name"># ${esc(ch.name)}</div>
                <div class="browse-channel-meta">${ch.member_count} members${ch.description ? ' · ' + esc(ch.description) : ''}</div>
            </div>${btn}
        </div>`;
    }).join('') || '<p style="color:var(--text2)">No public channels</p>';
}
async function joinChannel(slug) {
    const res = await apiFetch(`/channels/${slug}/join`, { method: 'POST' });
    if (!res || !res.ok) return;
    closeBrowseChannelsDialog();
    await loadChannels();
    openChannel(slug);
}
function showNewDMDialog() {
    document.getElementById('new-dm-dialog').style.display = 'flex';
    const list = document.getElementById('dm-users-list');
    list.innerHTML = allUsers.filter(u => u.username !== currentUser).map(u => {
        const av = u.avatar_path ? `<img src="${srv(u.avatar_path)}">` : u.display_name.charAt(0).toUpperCase();
        return `<div class="dm-user-item" onclick="startDM('${u.username}')">
            <div class="dm-user-avatar">${av}</div>
            <span>${esc(u.display_name)} <span style="color:var(--text2)">@${u.username}</span></span>
        </div>`;
    }).join('');
}
function closeNewDMDialog() { document.getElementById('new-dm-dialog').style.display = 'none'; }
async function startDM(participant) {
    const res = await apiFetch('/channels', { method: 'POST', body: { type: 'direct', participant } });
    if (!res || !res.ok) return;
    const ch = await res.json();
    closeNewDMDialog();
    await loadChannels();
    openChannel(ch.slug);
}

// ── Right Panel ────────────────────────────────────────────────
function toggleRightPanel() {
    const panel = document.getElementById('right-panel');
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') loadRightPanelMembers();
}
async function loadRightPanelMembers() {
    if (!currentChannel) return;
    const body = document.getElementById('right-panel-body');
    if (!currentChannel.member_details || !currentChannel.member_details.length) {
        const res = await apiFetch(`/channels/${currentChannel.slug}/members`);
        if (res && res.ok) currentChannel.member_details = await res.json();
    }
    const memberList = currentChannel.member_details || [];
    const myMember = memberList.find(m => m.username === currentUser);
    const myChRole = myMember ? myMember.role : 'member';
    const canKick = myChRole === 'owner' || myChRole === 'admin' || userRole === 'admin';
    const isChannel = currentChannel.type !== 'direct';

    body.innerHTML = memberList.map(m => {
        const isOnline = onlineUsers[m.username]?.online;
        const av = m.avatar_path ? `<img src="${srv(m.avatar_path)}">` : m.display_name.charAt(0).toUpperCase();
        const roleBadge = m.role === 'owner' ? ' <span class="admin-badge admin">owner</span>' : (m.role === 'admin' ? ' <span class="admin-badge admin">admin</span>' : '');
        const kickBtn = (canKick && isChannel && m.role !== 'owner' && m.username !== currentUser)
            ? `<button class="btn-icon member-kick-btn" onclick="event.stopPropagation(); kickMember('${m.username}')" title="${t('channel.kick')}"><span class="material-icons-round" style="font-size:18px;color:var(--danger)">person_remove</span></button>`
            : '';
        return `<div class="member-row" onclick="showUserCard('${m.username}')">
            <div class="member-avatar">${av}<span class="member-online-dot ${isOnline ? 'online' : 'offline'}"></span></div>
            <div class="member-info">
                <div class="member-name">${esc(m.display_name)}${roleBadge}</div>
                <div class="member-role">@${m.username}</div>
            </div>
            ${kickBtn}
        </div>`;
    }).join('');
}

async function kickMember(username) {
    if (!currentChannel) return;
    const member = allUsers.find(u => u.username === username);
    const name = member ? member.display_name : username;
    if (!confirm(t('channel.kickConfirm').replace('{name}', name))) return;
    const res = await apiFetch(`/channels/${currentChannel.slug}`, {
        method: 'PUT',
        body: { remove_members: [username] }
    });
    if (!res || !res.ok) return;
    currentChannel.member_details = null;
    loadRightPanelMembers();
}

// ── User Card ──────────────────────────────────────────────────
let userCardTarget = null;

function showUserCard(username) {
    const user = allUsers.find(u => u.username === username);
    if (!user) return;
    userCardTarget = username;
    const isOnline = onlineUsers[username]?.online;
    const av = user.avatar_path
        ? `<img src="${srv(user.avatar_path)}">`
        : user.display_name.charAt(0).toUpperCase();
    document.getElementById('user-card-avatar').innerHTML = av;
    document.getElementById('user-card-online').textContent = isOnline ? 'Online' : 'Offline';
    document.getElementById('user-card-online').className = 'user-card-online ' + (isOnline ? 'online' : 'offline');
    document.getElementById('user-card-name').textContent = user.display_name;
    document.getElementById('user-card-username').textContent = '@' + user.username;
    document.getElementById('user-card-status').textContent = user.status_text || '';
    // Hide "Send message" button if viewing own card
    const dmBtn = document.getElementById('user-card-dm-btn');
    dmBtn.style.display = username === currentUser ? 'none' : 'flex';
    document.getElementById('user-card-modal').style.display = 'flex';
}

function closeUserCard() {
    document.getElementById('user-card-modal').style.display = 'none';
    userCardTarget = null;
}

async function userCardSendMessage() {
    if (!userCardTarget || userCardTarget === currentUser) return;
    const target = userCardTarget;
    closeUserCard();
    // Close right panel (important on mobile where it's an overlay)
    document.getElementById('right-panel').style.display = 'none';
    // Create/open DM
    const res = await apiFetch('/channels', { method: 'POST', body: { type: 'direct', participant: target } });
    if (!res || !res.ok) return;
    const ch = await res.json();
    await loadChannels();
    openChannel(ch.slug);
}

function onTopbarInfoClick() {
    if (currentPage !== 'chat' || !currentChannel) return;
    if (currentChannel.type === 'direct') {
        const other = (currentChannel.members || []).find(m => m !== currentUser);
        if (other) showUserCard(other);
    }
}

// ── Channel Settings ───────────────────────────────────────────
function showChannelSettingsDialog() {
    if (!currentChannel || currentChannel.type === 'direct') return;
    document.getElementById('channel-settings-dialog').style.display = 'flex';
    document.getElementById('ch-settings-name').value = currentChannel.name;
    document.getElementById('ch-settings-desc').value = currentChannel.description || '';

    // Channel avatar preview
    const avatarPreview = document.getElementById('ch-avatar-preview');
    if (avatarPreview) {
        if (currentChannel.avatar_path) {
            avatarPreview.innerHTML = `<img src="${srv(currentChannel.avatar_path)}" style="width:100%;height:100%;object-fit:cover;">`;
        } else {
            avatarPreview.textContent = (currentChannel.name || '#').charAt(0).toUpperCase();
        }
    }

    // Show/hide Delete and Leave based on permissions
    const myMember = (currentChannel.member_details || []).find(m => m.username === currentUser);
    const myChRole = myMember ? myMember.role : 'member';
    const canDelete = myChRole === 'owner' || userRole === 'admin';
    const deleteBtn = document.getElementById('ch-settings-delete-btn');
    if (deleteBtn) deleteBtn.style.display = canDelete ? '' : 'none';
    const leaveBtn = document.getElementById('ch-settings-leave-btn');
    if (leaveBtn) leaveBtn.style.display = myChRole !== 'owner' ? '' : 'none';
}
function closeChannelSettingsDialog() { document.getElementById('channel-settings-dialog').style.display = 'none'; }
async function saveChannelSettings() {
    if (!currentChannel) return;
    const name = document.getElementById('ch-settings-name').value.trim();
    const description = document.getElementById('ch-settings-desc').value.trim();
    const res = await apiFetch(`/channels/${currentChannel.slug}`, { method: 'PUT', body: { name, description } });
    if (!res || !res.ok) return;
    closeChannelSettingsDialog();
    await loadChannels();
    openChannel(currentChannel.slug);
}
async function deleteCurrentChannel() {
    if (!currentChannel || !confirm(t('channel.deleteConfirm'))) return;
    await apiFetch(`/channels/${currentChannel.slug}`, { method: 'DELETE' });
    closeChannelSettingsDialog();
    currentChannel = null;
    document.getElementById('msg-empty-state').style.display = 'flex';
    document.getElementById('msg-messages').style.display = 'none';
    document.getElementById('msg-input-area').style.display = 'none';
    await loadChannels();
}
async function leaveCurrentChannel() {
    if (!currentChannel || !confirm(t('channel.leaveConfirm'))) return;
    await apiFetch(`/channels/${currentChannel.slug}/leave`, { method: 'POST' });
    closeChannelSettingsDialog();
    currentChannel = null;
    document.getElementById('msg-empty-state').style.display = 'flex';
    document.getElementById('msg-messages').style.display = 'none';
    document.getElementById('msg-input-area').style.display = 'none';
    await loadChannels();
}

// ── Profile ────────────────────────────────────────────────────
async function showProfileDialog() {
    document.getElementById('profile-dialog').style.display = 'flex';
    const res = await apiFetch('/me');
    if (!res || !res.ok) return;
    const user = await res.json();
    document.getElementById('profile-display-name').value = user.display_name || '';
    document.getElementById('profile-nickname').value = user.nickname || '';
    document.getElementById('profile-status').value = user.status_text || '';
    document.getElementById('profile-password').value = '';
    const avatarEl = document.getElementById('profile-avatar');
    avatarEl.innerHTML = user.avatar_path ? `<img src="${srv(user.avatar_path)}">` : '';
    if (!user.avatar_path) avatarEl.textContent = (user.display_name || user.username).charAt(0).toUpperCase();
}
function closeProfileDialog() { document.getElementById('profile-dialog').style.display = 'none'; }
async function saveProfile() {
    const data = {
        display_name: document.getElementById('profile-display-name').value.trim(),
        nickname: document.getElementById('profile-nickname').value.trim(),
        status_text: document.getElementById('profile-status').value.trim(),
    };
    const pw = document.getElementById('profile-password').value;
    if (pw) data.password = pw;
    const res = await apiFetch('/me', { method: 'PUT', body: data });
    if (!res || !res.ok) return;
    const user = await res.json();
    displayName = user.display_name;
    currentUserAvatar = user.avatar_path || currentUserAvatar;
    localStorage.setItem('ch_display', displayName);
    // Update allUsers
    const uidx = allUsers.findIndex(u => u.username === currentUser);
    if (uidx >= 0) { allUsers[uidx].display_name = displayName; allUsers[uidx].avatar_path = currentUserAvatar; }
    closeProfileDialog();
    updateSidebarUser();
    renderSidebar();
    if (currentChannel) updateTopbar();
    showToast(t('toast.profileSaved'), 'success');
}
async function uploadAvatar(files) {
    if (!files.length) return;
    const formData = new FormData();
    formData.append('file', files[0]);
    const res = await apiFetch('/me/avatar', { method: 'POST', body: formData });
    if (!res || !res.ok) return;
    const data = await res.json();
    const avatarPath = data.avatar_path;
    // Update profile dialog
    document.getElementById('profile-avatar').innerHTML = `<img src="${srv(avatarPath)}">`;
    // Update global state
    currentUserAvatar = avatarPath;
    // Update allUsers
    const idx = allUsers.findIndex(u => u.username === currentUser);
    if (idx >= 0) allUsers[idx].avatar_path = avatarPath;
    // Refresh all UI
    updateSidebarUser();
    renderSidebar();
    if (currentChannel) { updateTopbar(); loadRightPanelMembers(); }
}

// ── Settings / Admin ──────────────────────────────────────────
function showSettingsMenu() { document.getElementById('settings-menu').style.display = 'flex'; }
function closeSettingsMenu() { document.getElementById('settings-menu').style.display = 'none'; }
async function navigateToAdmin() {
    closeSettingsMenu();
    const sb = document.getElementById('sidebar');
    if (sb.classList.contains('open')) toggleSidebar();
    pushRoute('/admin');
    showPage('admin');
    await loadAdminUsers();
    loadFederation();
    loadAdminFeedback();
}
function showCreateUserForm() { document.getElementById('admin-create-user-form').style.display = 'flex'; }
async function adminCreateUser() {
    const username = document.getElementById('admin-new-username').value.trim();
    const password = document.getElementById('admin-new-password').value;
    const display_name = document.getElementById('admin-new-display').value.trim();
    if (!username || !password) { showToast(t('admin.userPassRequired'), 'error'); return; }
    try {
        const res = await apiFetch('/admin/users', { method: 'POST', body: { username, password, display_name } });
        if (!res) return;
        if (!res.ok) {
            const err = await res.json().catch(() => null);
            showToast(err?.detail || 'Failed to create user', 'error');
            return;
        }
        document.getElementById('admin-create-user-form').style.display = 'none';
        document.getElementById('admin-new-username').value = '';
        document.getElementById('admin-new-password').value = '';
        document.getElementById('admin-new-display').value = '';
        await loadAdminUsers();
        await loadUsers();
        showToast(t('admin.userCreated'), 'success');
    } catch (e) {
        console.error('adminCreateUser error:', e);
        showToast(t('toast.networkError') + ': ' + e.message, 'error');
    }
}

// ── Bulk import users from Excel/CSV ──────────────────────────────
async function downloadImportTemplate() {
    const res = await apiFetch('/admin/users/import/template');
    if (!res || !res.ok) { showToast(t('toast.error'), 'error'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'nebenan_users_template.xlsx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

async function adminImportUsers(files) {
    if (!files || !files.length) return;
    const input = document.getElementById('admin-import-input');
    const fd = new FormData();
    fd.append('file', files[0]);
    if (input) input.value = ''; // allow re-selecting the same file later
    try {
        const res = await apiFetch('/admin/users/import', { method: 'POST', body: fd });
        if (!res) return;
        const data = await res.json().catch(() => null);
        if (!res.ok) { showToast(data?.detail || t('toast.error'), 'error'); return; }
        renderImportResult(data);
        await loadAdminUsers();
        await loadUsers();
        const msg = t('admin.importDone')
            .replace('{created}', data.created_count)
            .replace('{skipped}', data.skipped_count)
            .replace('{errors}', data.error_count);
        showToast(msg, data.error_count ? 'error' : 'success');
    } catch (e) {
        showToast(t('toast.networkError') + ': ' + e.message, 'error');
    }
}

function renderImportResult(data) {
    const box = document.getElementById('admin-import-result');
    if (!box) return;
    let html = `<div class="air-summary">
        <span class="air-ok">${t('admin.imported')}: ${data.created_count}</span>
        <span class="air-skip">${t('admin.skipped')}: ${data.skipped_count}</span>
        <span class="air-err">${t('admin.errors')}: ${data.error_count}</span>
        <button class="btn-icon-xs" onclick="document.getElementById('admin-import-result').style.display='none'"><span class="material-icons-round">close</span></button>
    </div>`;
    if (data.errors && data.errors.length) {
        html += '<ul class="air-errors">' + data.errors.map(e =>
            `<li>${t('admin.line')} ${e.line}${e.username ? ' (@' + esc(e.username) + ')' : ''}: ${esc(e.error)}</li>`
        ).join('') + '</ul>';
    }
    box.innerHTML = html;
    box.style.display = 'block';
}

// Incoming link request from another server — admins only get this event.
function onFederationRequest(data) {
    showToast(`Сервер ${data.domain} просит подключиться. Откройте админ-панель`, 'info');
    if (document.getElementById('page-admin')?.classList.contains('active')) loadFederation();
}

// ── Federation: linking peer servers ─────────────────────────────
const FED_STATUS = {
    active:      { label: 'Подключён',              cls: 'ok' },
    pending_in:  { label: 'Входящая заявка',        cls: 'warn' },
    pending_out: { label: 'Ожидает подтверждения',  cls: 'muted' },
    declined:    { label: 'Отклонён',               cls: 'err' },
    revoked:     { label: 'Отключён',               cls: 'err' },
};

async function loadFederation() {
    const res = await apiFetch('/admin/federation/servers');
    if (!res || !res.ok) return;
    const data = await res.json();

    const hint = document.getElementById('fed-self-hint');
    if (hint) {
        hint.textContent = data.server_domain
            ? `Этот сервер: ${data.server_domain}. Другой админ подтвердит заявку у себя.`
            : 'Федерация выключена: не задан SERVER_DOMAIN в .env на этом сервере.';
    }

    const box = document.getElementById('fed-list');
    if (!box) return;
    if (!data.servers.length) {
        box.innerHTML = '<div class="admin-user-meta" style="padding:8px 0">Подключённых серверов пока нет.</div>';
        return;
    }

    box.innerHTML = data.servers.map(s => {
        const st = FED_STATUS[s.status] || { label: s.status, cls: 'muted' };
        let actions = '';
        if (s.status === 'pending_in') {
            actions = `
                <button class="btn-action btn-accent" onclick="fedApprove('${esc(s.domain)}')">Добавить</button>
                <button class="btn-action" onclick="fedDecline('${esc(s.domain)}')">Отклонить</button>`;
        } else {
            actions = `<button class="btn-icon" onclick="fedRemove('${esc(s.domain)}')" title="Удалить" style="color:var(--danger)">
                    <span class="material-icons-round">delete</span>
                </button>`;
        }
        const err = s.last_error
            ? `<div class="admin-user-meta" style="color:var(--danger)">${esc(s.last_error)}</div>` : '';
        return `<div class="admin-user-row">
            <div class="admin-user-info">
                <div class="admin-user-name">${esc(s.domain)} <span class="fed-chip ${st.cls}">${st.label}</span></div>
                <div class="admin-user-meta">${s.direction === 'incoming' ? 'Запрос от них' : 'Запрос от нас'}${s.requested_by ? ' · ' + esc(s.requested_by) : ''}</div>
                ${err}
            </div>
            <div class="admin-user-actions">${actions}</div>
        </div>`;
    }).join('');
}

async function fedAddServer() {
    const input = document.getElementById('fed-domain-input');
    const domain = input.value.trim();
    if (!domain) { showToast('Введите домен сервера', 'error'); return; }
    const res = await apiFetch('/admin/federation/servers', { method: 'POST', body: { domain } });
    if (!res) return;
    const data = await res.json().catch(() => null);
    if (!res.ok) { showToast(data?.detail || t('toast.error'), 'error'); return; }
    input.value = '';
    await loadFederation();
    showToast('Заявка отправлена. Ждём подтверждения администратора того сервера', 'success');
}

async function fedApprove(domain) {
    const res = await apiFetch(`/admin/federation/servers/${encodeURIComponent(domain)}/approve`, { method: 'POST' });
    if (!res) return;
    const data = await res.json().catch(() => null);
    if (!res.ok) { showToast(data?.detail || t('toast.error'), 'error'); return; }
    await loadFederation();
    showToast(`Сервер ${domain} подключён`, 'success');
}

async function fedDecline(domain) {
    const res = await apiFetch(`/admin/federation/servers/${encodeURIComponent(domain)}/decline`, { method: 'POST' });
    if (!res) return;
    await loadFederation();
    showToast(`Заявка от ${domain} отклонена`, 'info');
}

async function fedRemove(domain) {
    if (!confirm(`Удалить сервер ${domain}?`)) return;
    const res = await apiFetch(`/admin/federation/servers/${encodeURIComponent(domain)}`, { method: 'DELETE' });
    if (!res || !res.ok) { showToast(t('toast.error'), 'error'); return; }
    await loadFederation();
}

async function loadAdminUsers() {
    const res = await apiFetch('/admin/users');
    if (!res || !res.ok) return;
    const users = await res.json();
    document.getElementById('admin-users-list').innerHTML = users.map(u => {
        const badges = [];
        if (u.role === 'admin') badges.push('<span class="admin-badge admin">admin</span>');
        if (u.blocked) badges.push('<span class="admin-badge blocked">blocked</span>');
        return `<div class="admin-user-row">
            <div class="admin-user-info">
                <div class="admin-user-name">${esc(u.display_name)} (@${u.username}) ${badges.join(' ')}</div>
                <div class="admin-user-meta">Created: ${u.created_at ? new Date(u.created_at).toLocaleDateString() : 'N/A'}</div>
            </div>
            <div class="admin-user-actions">
                ${u.username !== currentUser ? `
                    <button class="btn-icon" onclick="adminToggleRole('${u.username}', '${u.role === 'admin' ? 'user' : 'admin'}')" title="${u.role === 'admin' ? 'Снять админа' : 'Выдать админа'}"${u.role === 'admin' ? ' style="color:var(--accent)"' : ''}>
                        <span class="material-icons-round">${u.role === 'admin' ? 'remove_moderator' : 'add_moderator'}</span>
                    </button>
                    <button class="btn-icon" onclick="adminToggleBlock('${u.username}', ${!u.blocked})" title="${u.blocked ? 'Разблокировать' : 'Заблокировать'}">
                        <span class="material-icons-round">${u.blocked ? 'lock_open' : 'block'}</span>
                    </button>
                    <button class="btn-icon" onclick="adminResetPassword('${u.username}')" title="Сбросить пароль">
                        <span class="material-icons-round">key</span>
                    </button>
                    <button class="btn-icon" onclick="adminDeleteUser('${u.username}')" title="Удалить пользователя" style="color:var(--danger)">
                        <span class="material-icons-round">delete</span>
                    </button>
                ` : ''}
            </div>
        </div>`;
    }).join('');
}
async function adminToggleBlock(username, block) { await apiFetch(`/admin/users/${username}/block`, { method: 'PUT', body: { blocked: block } }); await loadAdminUsers(); }
async function adminToggleRole(username, role) {
    const res = await apiFetch(`/admin/users/${username}/role`, { method: 'PUT', body: { role } });
    if (!res) return;
    if (!res.ok) {
        const err = await res.json().catch(() => null);
        showToast(err?.detail || t('toast.error'), 'error');
        return;
    }
    await loadAdminUsers();
    await loadUsers();
    showToast(role === 'admin' ? 'Пользователь назначен админом' : 'Права админа сняты', 'success');
}
function adminResetPassword(username) {
    // Find the row for this user and add an inline input
    const rows = document.querySelectorAll('.admin-user-row');
    for (const row of rows) {
        if (row.querySelector('.admin-user-name')?.textContent.includes(`@${username}`)) {
            // Check if already has an input
            if (row.querySelector('.inline-pw-row')) return;
            const pwRow = document.createElement('div');
            pwRow.className = 'inline-pw-row inline-input-row';
            pwRow.style.marginTop = '6px';
            pwRow.innerHTML = `<input type="password" class="inline-input" placeholder="New password..." autofocus>
                <button class="btn-icon-xs inline-input-ok" onclick="confirmResetPassword('${username}', this)"><span class="material-icons-round">check</span></button>
                <button class="btn-icon-xs inline-input-cancel" onclick="this.closest('.inline-pw-row').remove()"><span class="material-icons-round">close</span></button>`;
            row.appendChild(pwRow);
            const inp = pwRow.querySelector('input');
            inp.focus();
            inp.addEventListener('keydown', e => {
                if (e.key === 'Enter') confirmResetPassword(username, inp);
                if (e.key === 'Escape') pwRow.remove();
            });
            return;
        }
    }
}
async function confirmResetPassword(username, el) {
    const row = el.closest('.inline-pw-row');
    const pw = row.querySelector('input').value;
    if (!pw) return;
    await apiFetch(`/admin/users/${username}/reset-password`, { method: 'PUT', body: { password: pw } });
    row.remove();
    showToast('Пароль сброшен');
}
async function adminDeleteUser(username) {
    if (!confirm(`Удалить пользователя ${username}? Все его сообщения, задачи и данные будут потеряны.`)) return;
    const res = await apiFetch(`/admin/users/${username}`, { method: 'DELETE' });
    if (!res || !res.ok) return showToast('Ошибка удаления пользователя', 'error');
    await loadAdminUsers();
    showToast('Пользователь удалён');
}

// ── Feedback ──────────────────────────────────────────────────
function showFeedbackModal() {
    document.getElementById('feedback-text').value = '';
    document.getElementById('feedback-modal').style.display = 'flex';
}
function closeFeedbackModal() { document.getElementById('feedback-modal').style.display = 'none'; }
async function submitFeedback() {
    const text = document.getElementById('feedback-text').value.trim();
    if (!text) { showToast(t('feedback.empty'), 'error'); return; }
    try {
        const res = await apiFetch('/admin/feedback', { method: 'POST', body: { text } });
        if (res && res.ok) {
            showToast(t('feedback.sent'), 'success');
            closeFeedbackModal();
        } else if (res) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Failed to send feedback', 'error');
        }
    } catch (e) {
        showToast(t('toast.networkError'), 'error');
    }
}
async function loadAdminFeedback() {
    const res = await apiFetch('/admin/feedback');
    if (!res || !res.ok) return;
    const items = await res.json();
    document.getElementById('admin-feedback-list').innerHTML = items.length === 0
        ? '<div style="padding:12px;color:var(--text3)">No feedback yet</div>'
        : items.map(f => `<div class="admin-feedback-item">
            <div class="admin-feedback-header">
                <span class="admin-feedback-author">${esc(f.display_name)} (@${f.username})</span>
                <span class="admin-feedback-date">${new Date(f.created_at).toLocaleString()}</span>
            </div>
            <div class="admin-feedback-text">${esc(f.text)}</div>
          </div>`).join('');
}

// ── Calls ──────────────────────────────────────────────────────
let callParticipants = new Map(); // identity → { name, isMuted, hasVideo }

async function startVideoCall() {
    if (!currentChannel) return;
    if (livekitRoom) { showToast(t('call.alreadyInCall'), 'error'); return; }
    if (currentChannel.type !== 'direct') {
        showToast('Адресный звонок доступен только в личном чате', 'error');
        return;
    }
    const members = currentChannel.members || [];
    const callee = members.find(m => m !== currentUser);
    if (!callee) {
        showToast('Для звонка нужен конкретный абонент', 'error');
        return;
    }
    const res = await apiFetch('/calls/start', {
        method: 'POST',
        body: { channel_slug: currentChannel.slug, callee_username: callee, mode: 'audio' },
    });
    if (!res) {
        showToast('Ошибка сети при старте звонка', 'error');
        return;
    }
    if (!res.ok) {
        try {
            const err = await res.json();
            showToast(err?.detail || `Ошибка начала звонка (${res.status})`, 'error');
        } catch (_) {
            showToast(`Ошибка начала звонка (${res.status})`, 'error');
        }
        return;
    }
    const data = await res.json();
    activeCallId = data.call_id || null;
    await joinLivekitRoom(data.livekit_token, data.room_name);
}
async function checkActiveCall() {
    if (!currentChannel) return;
    const res = await apiFetch(`/channels/${currentChannel.slug}/call`);
    if (!res || !res.ok) return;
    const data = await res.json();
    const joinBtn = document.getElementById('btn-call-join');
    if (data.active) { joinBtn.style.display = ''; joinBtn.dataset.token = data.livekit_token; joinBtn.dataset.room = data.room_name; }
    else joinBtn.style.display = 'none';
}
async function joinActiveCall() {
    const joinBtn = document.getElementById('btn-call-join');
    if (joinBtn.dataset.token && joinBtn.dataset.room) {
        if (livekitRoom) { livekitRoom.disconnect(); livekitRoom = null; }
        await joinLivekitRoom(joinBtn.dataset.token, joinBtn.dataset.room);
    }
}
function onCallStarted(data) {
    const caller = data.caller_username || data.started_by;
    if (caller === currentUser || livekitRoom) return;
    setIncomingCallData(data);
}
function onCallEnded(data) {
    const incomingCallId = incomingCallData && incomingCallData.call_id ? incomingCallData.call_id : null;
    const endedIncoming = !!(incomingCallId && data.call_id && incomingCallId === data.call_id);
    const endedByCallId = !!(data.call_id && activeCallId && data.call_id === activeCallId);
    const endedByRoom = !!(livekitRoom && data.room_name && livekitRoom.name === data.room_name);
    if (endedByCallId || endedByRoom) closeCall();
    if (endedIncoming) clearIncomingCallUI();
    document.getElementById('btn-call-join').style.display = 'none';
}
function onCallAnswered(data) {
    // Someone answered the call on another device/tab — dismiss incoming call UI
    if (incomingCallData && !livekitRoom) {
        const incomingCallId = incomingCallData.call_id || null;
        const answeredCallId = data.call_id || null;
        if (incomingCallId && answeredCallId && incomingCallId === answeredCallId) {
            clearIncomingCallUI();
        }
    }
}
function onCallMediaUpdated(data) {
    if (!data || !activeCallId || data.call_id !== activeCallId) return;
    if (typeof data.speaker_enabled === 'boolean') {
        speakerEnabled = data.speaker_enabled;
        const speakerBtn = document.getElementById('btn-toggle-speaker');
        if (speakerBtn) {
            speakerBtn.querySelector('.material-icons-round').textContent = speakerEnabled ? 'volume_up' : 'hearing';
            speakerBtn.classList.toggle('muted', !speakerEnabled);
        }
    }
}
async function acceptIncomingCall() {
    if (!incomingCallData) return;
    const callId = incomingCallData.call_id;
    clearIncomingCallUI();
    if (livekitRoom) { livekitRoom.disconnect(); livekitRoom = null; }
    if (callId) {
        try {
            const res = await apiFetch('/calls/answer', { method: 'POST', body: { call_id: callId } });
            if (res && res.ok) {
                const data = await res.json();
                activeCallId = callId;
                await joinLivekitRoom(data.livekit_token, data.room_name);
            } else if (res) {
                const err = await res.json().catch(() => ({}));
                showToast(err?.detail || `Ошибка принятия звонка (${res.status})`, 'error');
            } else {
                showToast('Ошибка сети при принятии звонка', 'error');
            }
        } catch (e) {
            showToast('Ошибка сети при принятии звонка', 'error');
        }
    } else {
        showToast('Некорректные данные входящего звонка', 'error');
    }
}
async function declineIncomingCall() {
    const callId = incomingCallData ? incomingCallData.call_id : null;
    clearIncomingCallUI();
    if (callId) {
        try { await apiFetch('/calls/reject', { method: 'POST', body: { call_id: callId } }); } catch(e) {}
    }
}

function setIncomingCallData(data) {
    if (incomingCallData && incomingCallData.call_id && data.call_id && incomingCallData.call_id === data.call_id) {
        scheduleIncomingCallTimeout(data);
        return;
    }
    const caller = data.caller_username || data.started_by || 'Call';
    incomingCallData = data;
    document.getElementById('incoming-call-name').textContent = data.caller_name || data.started_by_name || caller;
    document.getElementById('incoming-call').style.display = 'flex';
    startRingtone();
    scheduleIncomingCallTimeout(data);
}

function clearIncomingCallUI() {
    if (incomingCallTimeoutTimer) {
        clearTimeout(incomingCallTimeoutTimer);
        incomingCallTimeoutTimer = null;
    }
    stopRingtone();
    document.getElementById('incoming-call').style.display = 'none';
    incomingCallData = null;
}

function scheduleIncomingCallTimeout(data) {
    if (incomingCallTimeoutTimer) {
        clearTimeout(incomingCallTimeoutTimer);
        incomingCallTimeoutTimer = null;
    }
    const raw = data && data.expires_at ? data.expires_at : null;
    if (!raw) return;
    const expiresAt = new Date(raw);
    if (Number.isNaN(expiresAt.getTime())) return;
    const delay = expiresAt.getTime() - Date.now();
    if (delay <= 0) {
        clearIncomingCallUI();
        return;
    }
    const callId = data.call_id || null;
    incomingCallTimeoutTimer = setTimeout(() => {
        if (!incomingCallData) return;
        if (!callId || incomingCallData.call_id === callId) {
            clearIncomingCallUI();
        }
    }, delay);
}

async function joinLivekitRoom(tk, roomName) {
    if (!window.LivekitClient) { showToast(t('call.livekitNotLoaded'), 'error'); return; }
    const livekitUrl = `wss://${location.host}/livekit/`;
    const room = new LivekitClient.Room();
    livekitRoom = room;
    livekitRoom.name = roomName;

    // Show call panel, hide members panel
    document.getElementById('right-panel').style.display = 'none';
    document.getElementById('call-panel').style.display = 'flex';
    document.getElementById('call-banner').style.display = 'none';
    document.getElementById('call-participants').innerHTML = '';
    callParticipants.clear();
    startCallTimer();

    // Set initial button states: mic ON, camera OFF
    const camBtn = document.getElementById('btn-toggle-cam');
    camBtn.querySelector('.material-icons-round').textContent = 'videocam_off';
    camBtn.classList.add('muted');
    const micBtn = document.getElementById('btn-toggle-mic');
    micBtn.querySelector('.material-icons-round').textContent = 'mic';
    micBtn.classList.remove('muted');
    const speakerBtn = document.getElementById('btn-toggle-speaker');
    if (speakerBtn) {
        speakerEnabled = false;
        speakerBtn.querySelector('.material-icons-round').textContent = 'hearing';
        speakerBtn.classList.add('muted');
    }

    // Track events
    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, pub, participant) => {
        attachTrack(track, participant);
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
        document.querySelectorAll(`[data-track-sid="${track.sid}"]`).forEach(el => el.remove());
        // Clean up empty video containers
        document.querySelectorAll('.call-video-item').forEach(item => {
            if (!item.querySelector('video')) item.remove();
        });
        updateVideoGrid();
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.ParticipantConnected, (participant) => {
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.ParticipantDisconnected, (participant) => {
        // Remove video elements for this participant
        const container = document.getElementById(`video-${participant.identity}`);
        if (container) container.remove();
        updateVideoGrid();
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.TrackMuted, (pub, participant) => {
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.TrackUnmuted, (pub, participant) => {
        renderCallParticipants();
    });
    room.on(LivekitClient.RoomEvent.Disconnected, () => closeCall());

    try {
        await room.connect(livekitUrl, tk);
        await room.localParticipant.setMicrophoneEnabled(true);

        // Attach existing tracks from remote participants
        room.remoteParticipants.forEach(p => {
            p.trackPublications.forEach(pub => {
                if (pub.track) attachTrack(pub.track, p);
            });
        });
        // Attach own audio tracks (hidden)
        room.localParticipant.audioTrackPublications.forEach(pub => {
            if (pub.track) attachTrack(pub.track, room.localParticipant);
        });

        renderCallParticipants();
    } catch (err) {
        console.error('LiveKit connect error:', err);
        showToast(t('call.connectionFailed'), 'error');
        closeCall();
    }
}

function attachTrack(track, participant) {
    if (track.kind === 'audio') {
        // Attach audio to body (hidden)
        const el = track.attach();
        el.dataset.trackSid = track.sid;
        el.style.display = 'none';
        document.body.appendChild(el);
    } else if (track.kind === 'video') {
        // Add video to call panel grid
        const grid = document.getElementById('call-video-grid');
        let container = document.getElementById(`video-${participant.identity}`);
        if (!container) {
            container = document.createElement('div');
            container.className = 'call-video-item';
            container.id = `video-${participant.identity}`;
            container.innerHTML = `<div class="call-video-item-name">${esc(participant.name || participant.identity)}</div>`;
            grid.appendChild(container);
        }
        const el = track.attach();
        el.dataset.trackSid = track.sid;
        container.appendChild(el);
        updateVideoGrid();
    }
}

function renderCallParticipants() {
    if (!livekitRoom) return;
    const container = document.getElementById('call-participants');
    container.innerHTML = '';

    const participants = [];
    // Add local participant first
    const lp = livekitRoom.localParticipant;
    if (lp) {
        participants.push({
            identity: lp.identity,
            name: lp.name || lp.identity,
            isMuted: !lp.isMicrophoneEnabled,
            isLocal: true
        });
    }
    // Add remote participants
    livekitRoom.remoteParticipants.forEach(p => {
        let isMuted = true;
        p.audioTrackPublications.forEach(pub => {
            if (pub.track && !pub.isMuted) isMuted = false;
        });
        participants.push({
            identity: p.identity,
            name: p.name || p.identity,
            isMuted: isMuted,
            isLocal: false
        });
    });

    participants.forEach(p => {
        const row = document.createElement('div');
        row.className = 'call-participant';
        const initial = (p.name || '?').charAt(0).toUpperCase();
        const micIcon = p.isMuted ? 'mic_off' : 'mic';
        const micClass = p.isMuted ? 'call-participant-mic mic-muted material-icons-round' : 'call-participant-mic material-icons-round';
        const youLabel = p.isLocal ? ' (you)' : '';
        row.innerHTML = `
            <div class="call-participant-avatar">${esc(initial)}</div>
            <div class="call-participant-info">
                <div class="call-participant-name">${esc(p.name)}${youLabel}</div>
            </div>
            <span class="${micClass}">${micIcon}</span>
        `;
        container.appendChild(row);
    });
}

function updateVideoGrid() {
    const grid = document.getElementById('call-video-grid');
    const panel = document.getElementById('call-panel');
    const count = grid.children.length;
    if (count === 0) {
        grid.classList.remove('active', 'two-videos');
        panel.classList.remove('has-video');
    } else {
        grid.classList.add('active');
        panel.classList.add('has-video');
        if (count >= 2) {
            grid.classList.add('two-videos');
        } else {
            grid.classList.remove('two-videos');
        }
    }
}

// Keep backward compat aliases
function updateVideoOverlay() { updateVideoGrid(); }
function closeVideoOverlay() { /* no-op, old overlay removed */ }

function toggleMic() {
    if (!livekitRoom) return;
    const enabled = livekitRoom.localParticipant.isMicrophoneEnabled;
    livekitRoom.localParticipant.setMicrophoneEnabled(!enabled);
    const btn = document.getElementById('btn-toggle-mic');
    btn.querySelector('.material-icons-round').textContent = enabled ? 'mic_off' : 'mic';
    btn.classList.toggle('muted', enabled);
    renderCallParticipants();
}
async function toggleSpeaker() {
    if (!livekitRoom) return;
    speakerEnabled = !speakerEnabled;
    const btn = document.getElementById('btn-toggle-speaker');
    if (btn) {
        btn.querySelector('.material-icons-round').textContent = speakerEnabled ? 'volume_up' : 'hearing';
        btn.classList.toggle('muted', !speakerEnabled);
    }
    // Browser does not expose a reliable "earpiece/speakerphone" route API.
    // We still sync user intent with backend for cross-client state consistency.
    if (activeCallId) {
        try {
            await apiFetch('/calls/update-media', {
                method: 'POST',
                body: {
                    call_id: activeCallId,
                    video_enabled: !!livekitRoom.localParticipant.isCameraEnabled,
                    speaker_enabled: speakerEnabled,
                },
            });
        } catch (e) {}
    }
}
async function toggleCamera() {
    if (!livekitRoom) return;
    const enabled = livekitRoom.localParticipant.isCameraEnabled;
    await livekitRoom.localParticipant.setCameraEnabled(!enabled);
    const btn = document.getElementById('btn-toggle-cam');
    btn.querySelector('.material-icons-round').textContent = enabled ? 'videocam_off' : 'videocam';
    btn.classList.toggle('muted', enabled);

    if (!enabled) {
        // Camera was just turned ON — attach local video to grid
        const lp = livekitRoom.localParticipant;
        lp.videoTrackPublications.forEach(pub => {
            if (pub.track) attachTrack(pub.track, lp);
        });
    } else {
        // Camera was just turned OFF — remove local video from grid
        const container = document.getElementById(`video-${livekitRoom.localParticipant.identity}`);
        if (container) container.remove();
        updateVideoGrid();
    }
    if (activeCallId) {
        try {
            await apiFetch('/calls/update-media', {
                method: 'POST',
                body: {
                    call_id: activeCallId,
                    video_enabled: !enabled,
                    speaker_enabled: speakerEnabled,
                },
            });
        } catch (e) {}
    }
}
async function endCall() {
    if (!livekitRoom || !currentChannel) return;
    if (activeCallId) {
        await apiFetch('/calls/end', { method: 'POST', body: { call_id: activeCallId } });
    } else {
        console.warn('No active call id for endCall');
    }
    closeCall();
}
let callTimerInterval = null;
let callStartTime = null;

function closeCall() {
    activeCallId = null;
    if (livekitRoom) { livekitRoom.disconnect(); livekitRoom = null; }
    // Hide call panel and banner
    const panel = document.getElementById('call-panel');
    panel.style.display = 'none';
    panel.classList.remove('has-video');
    document.getElementById('call-banner').style.display = 'none';
    document.getElementById('call-participants').innerHTML = '';
    // Stop call timer
    if (callTimerInterval) { clearInterval(callTimerInterval); callTimerInterval = null; }
    callStartTime = null;
    // Clear video grid
    const videoGrid = document.getElementById('call-video-grid');
    videoGrid.innerHTML = '';
    videoGrid.classList.remove('active', 'two-videos');
    // Clear old overlay (backward compat)
    document.getElementById('video-overlay-grid').innerHTML = '';
    // Remove any audio elements attached to body
    document.querySelectorAll('audio[data-track-sid]').forEach(el => el.remove());
    // Reset join button
    document.getElementById('btn-call-join').style.display = 'none';
    // Reset control buttons
    const micBtn = document.getElementById('btn-toggle-mic');
    micBtn.querySelector('.material-icons-round').textContent = 'mic';
    micBtn.classList.remove('muted');
    const camBtn = document.getElementById('btn-toggle-cam');
    camBtn.querySelector('.material-icons-round').textContent = 'videocam_off';
    camBtn.classList.remove('muted');
    callParticipants.clear();
    // Reset timer display
    const timerEl = document.getElementById('call-panel-timer');
    if (timerEl) timerEl.textContent = '0:00';
}

function minimizeCallPanel() {
    document.getElementById('call-panel').style.display = 'none';
    if (livekitRoom) {
        document.getElementById('call-banner').style.display = 'flex';
    }
}

function toggleCallPanel() {
    const panel = document.getElementById('call-panel');
    if (panel.style.display === 'none') {
        panel.style.display = 'flex';
        document.getElementById('call-banner').style.display = 'none';
    } else {
        minimizeCallPanel();
    }
}

function startCallTimer() {
    callStartTime = Date.now();
    if (callTimerInterval) clearInterval(callTimerInterval);
    callTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        document.getElementById('call-banner-time').textContent = timeStr;
        const panelTimer = document.getElementById('call-panel-timer');
        if (panelTimer) panelTimer.textContent = timeStr;
    }, 1000);
}

async function showInviteToCall() {
    if (!currentChannel || !livekitRoom) return;

    // Load channel members if not cached
    if (!currentChannel.member_details || !currentChannel.member_details.length) {
        const res = await apiFetch(`/channels/${currentChannel.slug}/members`);
        if (res && res.ok) currentChannel.member_details = await res.json();
    }
    const members = currentChannel.member_details || [];

    // Get identities of users already in the call
    const inCall = new Set();
    inCall.add(livekitRoom.localParticipant.identity);
    livekitRoom.remoteParticipants.forEach(p => inCall.add(p.identity));

    // Render member list (excluding those already in call)
    const list = document.getElementById('invite-call-list');
    const available = members.filter(m => !inCall.has(m.username));
    if (available.length === 0) {
        list.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text3)">All members are already in the call</div>';
    } else {
        list.innerHTML = available.map(m =>
            `<label class="member-check-item" data-username="${m.username}" data-name="${(m.display_name||'').toLowerCase()} ${(m.nickname||'').toLowerCase()}"><input type="checkbox" value="${m.username}"> ${esc(m.display_name)} (@${m.username})</label>`
        ).join('');
    }

    document.getElementById('invite-call-search').value = '';
    document.getElementById('invite-call-modal').style.display = 'flex';
}
function closeInviteCallModal() {
    document.getElementById('invite-call-modal').style.display = 'none';
}
function filterInviteCallList(query) {
    const q = query.toLowerCase().trim();
    document.querySelectorAll('#invite-call-list .member-check-item').forEach(el => {
        const name = el.dataset.name || '';
        const uname = el.dataset.username || '';
        el.style.display = (name.includes(q) || uname.includes(q)) ? '' : 'none';
    });
}
async function sendCallInvites() {
    showToast('Адресный режим: приглашение участников отключено', 'error');
}

// ── Ringtone ───────────────────────────────────────────────────
function startRingtone() {
    stopRingtone();
    try {
        ringtoneCtx = new (window.AudioContext || window.webkitAudioContext)();
        const playTone = () => {
            if (!ringtoneCtx) return;
            const osc1 = ringtoneCtx.createOscillator(); const osc2 = ringtoneCtx.createOscillator();
            const gain = ringtoneCtx.createGain();
            osc1.frequency.value = 440; osc2.frequency.value = 523.25; gain.gain.value = 0.15;
            osc1.connect(gain); osc2.connect(gain); gain.connect(ringtoneCtx.destination);
            const now = ringtoneCtx.currentTime;
            osc1.start(now); osc2.start(now);
            gain.gain.setValueAtTime(0.15, now); gain.gain.exponentialRampToValueAtTime(0.001, now + 0.8);
            osc1.stop(now + 0.8); osc2.stop(now + 0.8);
        };
        playTone();
        ringtoneInterval = setInterval(playTone, 2000);
    } catch (e) { }
}
function stopRingtone() {
    if (ringtoneInterval) { clearInterval(ringtoneInterval); ringtoneInterval = null; }
    if (ringtoneCtx) { ringtoneCtx.close().catch(() => {}); ringtoneCtx = null; }
}

// ── Search ─────────────────────────────────────────────────────
function toggleSearch() {
    const panel = document.getElementById('search-panel');
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') {
        const input = document.getElementById('search-input');
        input.value = ''; input.focus();
        input.oninput = debounce(doSearch, 400);
    }
}
async function doSearch() {
    const q = document.getElementById('search-input').value.trim();
    if (!q) { document.getElementById('search-results').innerHTML = ''; return; }
    const res = await apiFetch(`/messages/search?q=${encodeURIComponent(q)}`);
    if (!res || !res.ok) return;
    const results = await res.json();
    document.getElementById('search-results').innerHTML = results.map(r => {
        const chName = r.channel_name || r.channel_slug;
        return `<div class="search-result-item" onclick="openChannel('${r.channel_slug}')">
            <div class="search-result-channel"># ${esc(chName)}</div>
            <span class="search-result-sender">${esc(r.sender_name)}</span>
            <span class="search-result-time">${formatTime(r.timestamp)}</span>
            <div class="search-result-text">${esc(r.text).substring(0, 200)}</div>
        </div>`;
    }).join('') || '<p style="padding:12px; color:var(--text2);">No results</p>';
}

// ── Files Page ─────────────────────────────────────────────────
function navigateToFiles() {
    closeSettingsMenu();
    const sb = document.getElementById('sidebar');
    if (sb.classList.contains('open')) toggleSidebar();
    pushRoute('/files');
    showPage('files');
    loadFileList('');
    applyFileViewMode();
}
function showPage(page) {
    currentPage = page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');
    document.getElementById(`page-${page}`).style.display = 'flex';
    document.querySelectorAll('.page:not(.active)').forEach(p => p.style.display = 'none');
    if (page === 'files') {
        document.getElementById('topbar-channel-icon').textContent = '📁';
        document.getElementById('topbar-channel-name').textContent = t('files.title');
        document.getElementById('topbar-channel-desc').textContent = '';
        const callBtn = document.getElementById('btn-call');
        if (callBtn) callBtn.style.display = 'none';
        const settingsBtn = document.getElementById('btn-channel-settings');
        if (settingsBtn) settingsBtn.style.display = 'none';
    } else if (page === 'admin') {
        document.getElementById('topbar-channel-icon').textContent = '⚙️';
        document.getElementById('topbar-channel-name').textContent = t('settings.admin').replace('⚙️ ', '');
        document.getElementById('topbar-channel-desc').textContent = '';
        const callBtn = document.getElementById('btn-call');
        if (callBtn) callBtn.style.display = 'none';
        const settingsBtn = document.getElementById('btn-channel-settings');
        if (settingsBtn) settingsBtn.style.display = 'none';
    } else if (page === 'tasks') {
        document.getElementById('topbar').style.display = 'none';
        document.getElementById('right-panel').style.display = 'none';
    }
    // Restore topbar for non-task pages
    if (page !== 'tasks') {
        document.getElementById('topbar').style.display = '';
    }
}

function setFileView(mode) {
    fileViewMode = mode;
    localStorage.setItem('ch_file_view', mode);
    applyFileViewMode();
    renderFileList();
}
function setGridSize(val) {
    fileGridSize = parseInt(val);
    localStorage.setItem('ch_file_grid_size', String(fileGridSize));
    const sizes = ['120px', '160px', '220px'];
    document.getElementById('file-list').style.setProperty('--file-card-size', sizes[fileGridSize] || '160px');
}
function applyFileViewMode() {
    document.querySelectorAll('.file-view-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.view === fileViewMode));
    const slider = document.getElementById('file-grid-size');
    slider.style.display = fileViewMode === 'grid' ? '' : 'none';
    slider.value = fileGridSize;
    const header = document.getElementById('file-list-header');
    header.style.display = fileViewMode === 'list' ? '' : 'none';
    const sizes = ['120px', '160px', '220px'];
    document.getElementById('file-list').style.setProperty('--file-card-size', sizes[fileGridSize] || '160px');
}

async function loadFileList(path) {
    currentPath = path;
    pushRoute(path ? '/files/' + encodeURIComponent(path) : '/files', true);
    const res = await apiFetch(`/files?path=${encodeURIComponent(path)}`);
    if (!res || !res.ok) return;
    const data = await res.json();
    fileItems = data.items || [];
    renderBreadcrumb(path);
    renderFileList();
}

function renderBreadcrumb(path) {
    const el = document.getElementById('breadcrumb');
    let html = `<a onclick="loadFileList('')">Home</a>`;
    if (path) {
        const parts = path.split('/');
        let p = '';
        parts.forEach((part, i) => {
            p += (i > 0 ? '/' : '') + part;
            html += ` <span>/</span> <a onclick="loadFileList('${p}')">${esc(part)}</a>`;
        });
    }
    el.innerHTML = html;
}

function renderFileList() {
    const list = document.getElementById('file-list');
    const empty = document.getElementById('empty-state');
    if (fileItems.length === 0) { list.innerHTML = ''; empty.style.display = 'flex'; return; }
    empty.style.display = 'none';

    if (fileViewMode === 'grid') {
        list.className = 'file-grid';
        list.innerHTML = fileItems.map(item => {
            const isImage = !item.is_dir && ['image'].includes(item.type);
            const thumbUrl = isImage ? srv(`/uploads/${currentUser}/${item.path}`) : '';
            const icon = item.is_dir ? 'folder' : getFileIcon(item.type);
            const iconSize = fileGridSize === 0 ? '36px' : (fileGridSize === 2 ? '64px' : '48px');
            const preview = isImage
                ? `<img src="${thumbUrl}" loading="lazy">`
                : `<span class="material-icons-round" style="font-size:${iconSize}">${icon}</span>`;
            const size = item.is_dir ? '' : formatFileSize(item.size);
            return `<div class="file-card" onclick="onFileClick('${esc(item.path)}', ${item.is_dir})"
                         oncontextmenu="showFileContextMenu(event, '${esc(item.path)}', ${item.is_dir})">
                <button class="file-card-menu btn-icon" onclick="event.stopPropagation(); showFileContextMenu(event, '${esc(item.path)}', ${item.is_dir})">
                    <span class="material-icons-round">more_vert</span>
                </button>
                <div class="file-card-preview">${preview}</div>
                <div class="file-card-name" title="${esc(item.name)}">${esc(item.name)}</div>
                ${size ? `<div class="file-card-meta">${size}</div>` : ''}
            </div>`;
        }).join('');
    } else {
        list.className = 'file-list';
        list.innerHTML = fileItems.map(item => {
            const icon = item.is_dir ? 'folder' : getFileIcon(item.type);
            const size = item.is_dir ? '' : formatFileSize(item.size);
            const date = item.added ? new Date(item.added).toLocaleDateString() : '';
            return `<div class="file-row" onclick="onFileClick('${esc(item.path)}', ${item.is_dir})"
                         oncontextmenu="showFileContextMenu(event, '${esc(item.path)}', ${item.is_dir})">
                <div class="file-name-cell">
                    <span class="material-icons-round">${icon}</span>
                    <span class="file-name">${esc(item.name)}</span>
                </div>
                <span class="file-size">${size}</span>
                <span class="file-date">${date}</span>
                <span class="file-actions">
                    <button class="btn-icon" onclick="event.stopPropagation(); showFileContextMenu(event, '${esc(item.path)}', ${item.is_dir})">
                        <span class="material-icons-round" style="font-size:18px;">more_vert</span>
                    </button>
                </span>
            </div>`;
        }).join('');
    }
}

function onFileClick(path, isDir) { isDir ? loadFileList(path) : previewFile(path); }

async function uploadFiles(files) {
    if (!files.length) return;
    const BATCH = 5;
    const total = files.length;
    const progress = document.getElementById('upload-progress');
    const fill = document.getElementById('upload-bar-fill');
    const status = document.getElementById('upload-status');
    progress.style.display = 'block';
    fill.style.width = '0%';
    let uploaded = 0;
    let failed = 0;
    for (let i = 0; i < total; i += BATCH) {
        const batch = Array.from(files).slice(i, i + BATCH);
        const formData = new FormData();
        for (const f of batch) formData.append('files', f);
        formData.append('path', currentPath);
        try {
            const res = await apiFetch('/upload', { method: 'POST', body: formData });
            if (res && res.ok) uploaded += batch.length;
            else failed += batch.length;
        } catch (e) { failed += batch.length; }
        const pct = Math.round(((i + batch.length) / total) * 100);
        fill.style.width = pct + '%';
        status.textContent = `${i + batch.length} / ${total}`;
    }
    setTimeout(() => { progress.style.display = 'none'; status.textContent = ''; }, 800);
    if (uploaded > 0) { loadFileList(currentPath); showToast(`${t('files.uploaded')}: ${uploaded}/${total}`, failed ? 'warning' : 'success'); }
    if (failed > 0 && uploaded === 0) showToast(t('files.uploadError'), 'error');
    document.getElementById('file-input').value = '';
    document.getElementById('file-input-media').value = '';
}

function showNewFolderDialog() { document.getElementById('folder-dialog').style.display = 'flex'; document.getElementById('folder-name-input').value = ''; }
function closeFolderDialog() { document.getElementById('folder-dialog').style.display = 'none'; }
async function createFolder() {
    const name = document.getElementById('folder-name-input').value.trim();
    if (!name) return;
    await apiFetch('/folders', { method: 'POST', body: { path: currentPath, name } });
    closeFolderDialog();
    loadFileList(currentPath);
}

function previewFile(path) {
    const ext = path.split('.').pop().toLowerCase();
    const url = srv(`/uploads/${currentUser}/${path}`);
    document.getElementById('preview-title').textContent = path.split('/').pop();
    document.getElementById('preview-download').href = API + `/files/download?path=${encodeURIComponent(path)}`;
    const body = document.getElementById('preview-body');
    if (['jpg','jpeg','png','gif','webp','svg'].includes(ext)) body.innerHTML = `<img src="${url}" style="max-width:100%;max-height:70vh;">`;
    else if (['mp4','webm','mov'].includes(ext)) body.innerHTML = `<video src="${url}" controls style="max-width:100%;max-height:70vh;"></video>`;
    else if (['mp3','wav','ogg'].includes(ext)) body.innerHTML = `<audio src="${url}" controls></audio>`;
    else body.innerHTML = '<p style="padding:20px;color:var(--text2);">Preview not available</p>';
    document.getElementById('preview-modal').style.display = 'flex';
}
function closePreview() { document.getElementById('preview-modal').style.display = 'none'; }

// Context menu (universal)
function showFileContextMenu(e, path, isDir) {
    e.preventDefault();
    e.stopPropagation();
    ctxTarget = { path, isDir };
    const menu = document.getElementById('context-menu');
    const previewItem = isDir ? '' : `<div class="ctx-item" onclick="ctxPreview()"><span class="material-icons-round">visibility</span> ${t('ctx.preview')}</div>`;
    const shareItem = isDir ? '' : `<div class="ctx-item" onclick="ctxShareToChat()"><span class="material-icons-round">send</span> ${t('ctx.shareToChat')}</div>`;
    menu.innerHTML = `
        ${previewItem}
        <div class="ctx-item" onclick="ctxDownload()"><span class="material-icons-round">download</span> ${t('ctx.download')}</div>
        ${shareItem}
        <div class="ctx-item" onclick="ctxCopyFile()"><span class="material-icons-round">file_copy</span> ${t('ctx.copy')}</div>
        <div class="ctx-item" onclick="ctxCopyLink()"><span class="material-icons-round">link</span> ${t('files.copyLink')}</div>
        <div class="ctx-item" onclick="ctxRename()"><span class="material-icons-round">edit</span> ${t('ctx.rename')}</div>
        <div class="ctx-item ctx-danger" onclick="ctxDelete()"><span class="material-icons-round">delete</span> ${t('ctx.delete')}</div>
    `;
    menu.style.display = 'block';
    // Position so menu doesn't overflow the viewport
    const mx = e.clientX || 0;
    const my = e.clientY || 0;
    const mw = menu.offsetWidth;
    const mh = menu.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    menu.style.left = (mx + mw > vw ? Math.max(0, vw - mw - 8) : mx) + 'px';
    menu.style.top = (my + mh > vh ? Math.max(0, vh - mh - 8) : my) + 'px';
}
function hideContextMenu() { document.getElementById('context-menu').style.display = 'none'; }
function ctxPreview() { hideContextMenu(); if (ctxTarget && !ctxTarget.isDir) previewFile(ctxTarget.path); }
function ctxDownload() { hideContextMenu(); if (ctxTarget) window.open(API + `/files/download?path=${encodeURIComponent(ctxTarget.path)}`, '_blank'); }
function ctxCopyLink() {
    hideContextMenu();
    if (!ctxTarget) return;
    const origin = getServerBase() || location.origin;
    let url;
    if (ctxTarget.isDir) {
        url = `${origin}/api/files/download?path=${encodeURIComponent(ctxTarget.path)}`;
    } else {
        const encodedPath = ctxTarget.path.split('/').map(p => encodeURIComponent(p)).join('/');
        url = `${origin}/uploads/${currentUser}/${encodedPath}`;
    }
    navigator.clipboard.writeText(url).then(() => showToast(t('toast.copied'), 'success'));
}

// ── Share file to chat ────────────────────────────────────────
let shareFileData = null;

function ctxShareToChat() {
    hideContextMenu();
    if (!ctxTarget || ctxTarget.isDir) return;
    shareFileData = { path: ctxTarget.path, name: ctxTarget.path.split('/').pop() };

    const list = document.getElementById('share-channel-list');
    const available = channels;

    if (available.length === 0) {
        list.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text3)">${t('share.empty')}</div>`;
    } else {
        list.innerHTML = available.map(ch => {
            let icon, name;
            if (ch.type === 'direct') {
                const other = (ch.members || []).find(m => m !== currentUser) || '?';
                const otherUser = allUsers.find(u => u.username === other);
                name = otherUser ? otherUser.display_name : other;
                const avatarPath = otherUser?.avatar_path;
                icon = avatarPath ? `<img src="${srv(avatarPath)}">` : name.charAt(0).toUpperCase();
            } else {
                name = ch.name;
                icon = ch.type === 'private' ? '🔒' : '#';
            }
            return `<div class="forward-channel-item" data-name="${esc(name).toLowerCase()}" onclick="shareFileToChannel('${ch.slug}')">
                <div class="forward-channel-icon">${icon}</div>
                <div class="forward-channel-name">${esc(name)}</div>
            </div>`;
        }).join('');
    }

    document.getElementById('share-search').value = '';
    document.getElementById('share-file-modal').style.display = 'flex';
}

function closeShareFileModal() {
    document.getElementById('share-file-modal').style.display = 'none';
    shareFileData = null;
}

function filterShareList(query) {
    const q = query.toLowerCase().trim();
    document.querySelectorAll('#share-channel-list .forward-channel-item').forEach(el => {
        el.style.display = (el.dataset.name || '').includes(q) ? '' : 'none';
    });
}

async function shareFileToChannel(slug) {
    if (!shareFileData) return;
    const res = await apiFetch('/files/share-to-chat', { method: 'POST', body: { path: shareFileData.path } });
    if (!res || !res.ok) { showToast(t('toast.error'), 'error'); return; }
    const data = await res.json();
    const msgRes = await apiFetch(`/channels/${slug}/messages`, { method: 'POST', body: { text: '', type: 'file', file: data.file } });
    if (msgRes && msgRes.ok) {
        showToast(t('share.success'), 'success');
        closeShareFileModal();
    } else {
        showToast(t('toast.error'), 'error');
    }
}

// ── Copy file to folder ───────────────────────────────────────
let copyFileData = null;

function ctxCopyFile() {
    hideContextMenu();
    if (!ctxTarget) return;
    copyFileData = { path: ctxTarget.path, name: ctxTarget.path.split('/').pop() };
    loadCopyFolderList();
    document.getElementById('copy-search').value = '';
    document.getElementById('copy-file-modal').style.display = 'flex';
}

async function loadCopyFolderList() {
    const list = document.getElementById('copy-folder-list');
    list.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text3)">...</div>';

    // Recursively fetch all folders
    const folders = [];
    async function fetchFolders(path, depth) {
        const res = await apiFetch(`/files?path=${encodeURIComponent(path)}`);
        if (!res || !res.ok) return;
        const data = await res.json();
        for (const item of (data.items || [])) {
            if (item.is_dir) {
                folders.push({ name: item.name, path: item.path, depth });
                if (depth < 3) await fetchFolders(item.path, depth + 1);
            }
        }
    }
    await fetchFolders('', 0);

    // Build list with root + all folders
    const currentDir = copyFileData ? copyFileData.path.split('/').slice(0, -1).join('/') : '';
    let html = `<div class="copy-folder-item" data-name="${t('copy.root').toLowerCase()}" onclick="copyToFolder('')">
        <span class="material-icons-round">home</span>
        <span>${t('copy.root')}</span>
    </div>`;

    for (const f of folders) {
        if (f.path === currentDir) continue; // Skip current folder
        const indent = f.depth * 16;
        html += `<div class="copy-folder-item" data-name="${esc(f.name).toLowerCase()}" onclick="copyToFolder('${esc(f.path)}')" style="padding-left:${12 + indent}px">
            <span class="material-icons-round">folder</span>
            <span>${esc(f.name)}</span>
        </div>`;
    }

    if (folders.length === 0) {
        html += `<div style="padding:12px;text-align:center;color:var(--text3);font-size:13px">${t('copy.empty')}</div>`;
    }

    list.innerHTML = html;
}

function closeCopyFileModal() {
    document.getElementById('copy-file-modal').style.display = 'none';
    copyFileData = null;
}

function filterCopyList(query) {
    const q = query.toLowerCase().trim();
    document.querySelectorAll('#copy-folder-list .copy-folder-item').forEach(el => {
        el.style.display = (el.dataset.name || '').includes(q) ? '' : 'none';
    });
}

async function copyToFolder(destPath) {
    if (!copyFileData) return;
    const res = await apiFetch('/files/copy', { method: 'POST', body: { path: copyFileData.path, dest: destPath } });
    if (res && res.ok) {
        showToast(t('copy.success'), 'success');
        closeCopyFileModal();
        loadFileList(currentPath);
    } else {
        showToast(t('toast.error'), 'error');
    }
}

async function ctxRename() {
    hideContextMenu();
    if (!ctxTarget) return;
    const oldName = ctxTarget.path.split('/').pop();
    const targetPath = ctxTarget.path;
    // Find the file name element in the DOM
    const cards = document.querySelectorAll('.file-card-name, .file-name');
    for (const el of cards) {
        if (el.textContent.trim() === oldName) {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'inline-input inline-input-rename';
            input.value = oldName;
            input.style.width = '100%';
            el.textContent = '';
            el.appendChild(input);
            input.focus();
            input.select();
            const save = async () => {
                const val = input.value.trim();
                if (val && val !== oldName) {
                    await apiFetch('/files/rename', { method: 'PUT', body: { path: targetPath, new_name: val } });
                }
                loadFileList(currentPath);
            };
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter') { e.preventDefault(); save(); }
                if (e.key === 'Escape') loadFileList(currentPath);
            });
            input.addEventListener('blur', save);
            return;
        }
    }
    // Fallback if DOM element not found
    loadFileList(currentPath);
}
function ctxDelete() {
    hideContextMenu();
    if (!ctxTarget) return;
    const targetPath = ctxTarget.path;
    const targetName = targetPath.split('/').pop();
    // Find the file element and add confirm buttons inline
    const allNames = document.querySelectorAll('.file-card-name, .file-name');
    for (const el of allNames) {
        if (el.textContent.trim() === targetName) {
            const row = el.closest('.file-card') || el.closest('.file-row');
            if (!row || row.querySelector('.delete-confirm')) return;
            const confirm = document.createElement('div');
            confirm.className = 'delete-confirm';
            confirm.innerHTML = `
                <button class="btn-icon delete-confirm-yes" onclick="event.stopPropagation(); confirmFileDelete('${esc(targetPath)}')">
                    <span class="material-icons-round">check</span>
                </button>
                <button class="btn-icon delete-confirm-no" onclick="event.stopPropagation(); cancelFileDelete(this)">
                    <span class="material-icons-round">close</span>
                </button>`;
            row.style.position = 'relative';
            row.appendChild(confirm);
            return;
        }
    }
}
async function confirmFileDelete(path) {
    await apiFetch('/files', { method: 'DELETE', body: { path } });
    loadFileList(currentPath);
}
function cancelFileDelete(btn) {
    const confirm = btn.closest('.delete-confirm');
    if (confirm) confirm.remove();
}

// ── Theme ──────────────────────────────────────────────────────
function loadTheme() {
    setTheme(localStorage.getItem('ch_theme') || 'dark', false);
    setLanguage(localStorage.getItem('ch_lang') || 'ru', false);
}
function setTheme(theme, save = true) {
    if (theme === 'dark') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
    if (save) { localStorage.setItem('ch_theme', theme); apiFetch('/preferences', { method: 'PUT', body: { theme, language: currentLang } }); }
    document.querySelectorAll('.settings-option[data-theme]').forEach(btn => btn.classList.toggle('active', btn.dataset.theme === theme));
}
function setLanguage(lang, save = true) {
    currentLang = lang;
    if (save) { localStorage.setItem('ch_lang', lang); apiFetch('/preferences', { method: 'PUT', body: { theme: localStorage.getItem('ch_theme') || 'dark', language: lang } }); }
    applyI18n();
    document.querySelectorAll('.settings-option[data-lang]').forEach(btn => btn.classList.toggle('active', btn.dataset.lang === lang));
}

// ── Utilities ──────────────────────────────────────────────────
function esc(str) { if (!str) return ''; const div = document.createElement('div'); div.textContent = str; return div.innerHTML; }
function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}
function formatDate(iso) {
    const d = new Date(iso);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diff = today - msgDay;
    if (diff === 0) return t('msg.today');
    if (diff === 86400000) return t('msg.yesterday');
    return d.toLocaleDateString(currentLang === 'ru' ? 'ru-RU' : 'en-US', { day: 'numeric', month: 'long', year: 'numeric' });
}
function formatTime(iso) {
    return new Date(iso).toLocaleTimeString(currentLang === 'ru' ? 'ru-RU' : 'en-US', { hour: '2-digit', minute: '2-digit' });
}
function getFileIcon(type) {
    return { image: 'image', video: 'movie', audio: 'audiotrack', pdf: 'picture_as_pdf', document: 'article', folder: 'folder' }[type] || 'insert_drive_file';
}
function debounce(fn, ms) { let timer; return function(...args) { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; }
function closeAllModals() { document.querySelectorAll('.modal').forEach(m => m.style.display = 'none'); cancelReply(); forwardMsgData = null; shareFileData = null; copyFileData = null; }

// ── Sidebar Search ─────────────────────────────────────────────
function filterSidebarSearch(value) {
    const dropdown = document.getElementById('sidebar-search-dropdown');
    const q = (value || '').toLowerCase().trim();
    if (!q) { dropdown.style.display = 'none'; return; }
    let html = '';
    // People
    const users = allUsers.filter(u => u.username !== currentUser && (
        u.username.toLowerCase().includes(q) ||
        (u.display_name || '').toLowerCase().includes(q) ||
        (u.nickname && u.nickname.toLowerCase().includes(q))
    )).slice(0, 5);
    if (users.length > 0) {
        html += '<div class="sidebar-search-section">People</div>';
        users.forEach(u => {
            const av = u.avatar_path ? `<img src="${srv(u.avatar_path)}">` : (u.display_name || u.username).charAt(0).toUpperCase();
            const online = onlineUsers[u.username]?.online ? ' ●' : '';
            html += `<div class="sidebar-search-item" onclick="closeSidebarSearch(); startDMFromNewChat('${u.username}')">
                <div class="sidebar-search-item-avatar">${av}</div>
                <div class="sidebar-search-item-info">
                    <div class="sidebar-search-item-name">${esc(u.display_name)}${online}</div>
                    <div class="sidebar-search-item-meta">@${esc(u.username)}${u.nickname ? ' · ' + esc(u.nickname) : ''}</div>
                </div>
            </div>`;
        });
    }
    // Channels
    const chs = channels.filter(ch => ch.type !== 'direct' && (
        ch.name.toLowerCase().includes(q) ||
        ch.slug.toLowerCase().includes(q) ||
        (ch.description && ch.description.toLowerCase().includes(q))
    )).slice(0, 5);
    if (chs.length > 0) {
        html += '<div class="sidebar-search-section">Channels</div>';
        chs.forEach(ch => {
            const icon = ch.type === 'private' ? '🔒' : '#';
            html += `<div class="sidebar-search-item" onclick="closeSidebarSearch(); openChannel('${ch.slug}')">
                <div class="sidebar-search-item-icon">${icon}</div>
                <div class="sidebar-search-item-info">
                    <div class="sidebar-search-item-name">${esc(ch.name)}</div>
                    <div class="sidebar-search-item-meta">${esc(ch.description || ch.slug)}</div>
                </div>
            </div>`;
        });
    }
    if (!html) html = '<div class="sidebar-search-empty">Nothing found</div>';
    dropdown.innerHTML = html;
    dropdown.style.display = 'block';
}
function closeSidebarSearch() {
    document.getElementById('sidebar-search-input').value = '';
    document.getElementById('sidebar-search-dropdown').style.display = 'none';
}
document.addEventListener('click', function(e) {
    const search = document.querySelector('.sidebar-search');
    if (search && !search.contains(e.target)) closeSidebarSearch();
});

// ── Global Search Modal ────────────────────────────────────────
function showGlobalSearchModal() {
    document.getElementById('global-search-modal').style.display = 'flex';
    const input = document.getElementById('global-search-input');
    input.value = '';
    input.focus();
    switchGlobalSearchTab('all');
}
function closeGlobalSearchModal() { document.getElementById('global-search-modal').style.display = 'none'; }
let globalSearchTab = 'all';
function switchGlobalSearchTab(tab) {
    globalSearchTab = tab;
    document.querySelectorAll('.global-search-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    filterGlobalSearch();
}
function filterGlobalSearch() {
    const q = (document.getElementById('global-search-input')?.value || '').toLowerCase().trim();
    const results = document.getElementById('global-search-results');
    let html = '';
    // People
    if (globalSearchTab === 'all' || globalSearchTab === 'people') {
        const users = allUsers.filter(u => u.username !== currentUser && (
            u.username.toLowerCase().includes(q) ||
            u.display_name.toLowerCase().includes(q) ||
            (u.nickname && u.nickname.toLowerCase().includes(q))
        )).slice(0, 20);
        if (users.length > 0 && globalSearchTab === 'all') html += `<div class="global-search-section">${t('search.people')}</div>`;
        users.forEach(u => {
            const av = u.avatar_path ? `<img src="${srv(u.avatar_path)}">` : u.display_name.charAt(0).toUpperCase();
            const isOnline = onlineUsers[u.username]?.online;
            html += `<div class="global-search-item" onclick="closeGlobalSearchModal(); startDMFromNewChat('${u.username}')">
                <div class="global-search-avatar">${av}<span class="member-online-dot ${isOnline ? 'online' : 'offline'}"></span></div>
                <div class="global-search-info"><div class="global-search-name">${esc(u.display_name)}</div><div class="global-search-meta">@${esc(u.username)}${u.nickname ? ' · ' + esc(u.nickname) : ''}</div></div>
            </div>`;
        });
    }
    // Channels
    if (globalSearchTab === 'all' || globalSearchTab === 'channels') {
        const chs = channels.filter(ch => ch.type !== 'direct' && (
            ch.name.toLowerCase().includes(q) ||
            ch.slug.toLowerCase().includes(q) ||
            (ch.description && ch.description.toLowerCase().includes(q))
        )).slice(0, 20);
        if (chs.length > 0 && globalSearchTab === 'all') html += `<div class="global-search-section">${t('search.channels')}</div>`;
        chs.forEach(ch => {
            const icon = ch.type === 'private' ? '🔒' : '#';
            html += `<div class="global-search-item" onclick="closeGlobalSearchModal(); openChannel('${ch.slug}')">
                <div class="global-search-icon">${icon}</div>
                <div class="global-search-info"><div class="global-search-name">${esc(ch.name)}</div><div class="global-search-meta">${esc(ch.description || '')}</div></div>
            </div>`;
        });
    }
    results.innerHTML = html || '<p style="padding:16px;color:var(--text2);text-align:center">No results</p>';
}

// ═══════════════════════════════════════════════════════════════
// CHAOSTRACKER
// ═══════════════════════════════════════════════════════════════

async function navigateToTasks() {
    closeSettingsMenu();
    const sb = document.getElementById('sidebar');
    if (sb && sb.classList.contains('open')) toggleSidebar();
    pushRoute('/tracker');
    showPage('tasks');
    taskBreadcrumb = [];
    await loadTaskProjects();
}

// ── Projects ────────────────────────────────────────────────
async function loadTaskProjects(parentId) {
    const url = parentId ? `/tasks/projects?parent_id=${parentId}` : '/tasks/projects';
    const res = await apiFetch(url);
    if (!res || !res.ok) return;
    taskProjects = await res.json();
    renderTaskProjects();
}

const PROJECT_ROLE_RU = { owner: 'Владелец', lead: 'Руководитель', member: 'Участник', viewer: 'Наблюдатель' };

// Dashboard config
const DASHBOARD_WIDGETS = [
    { key: 'total_status', label: 'Все задачи по статусам' },
    { key: 'per_project', label: 'Задачи по проектам' },
    { key: 'my_tasks', label: 'Мои задачи' },
    { key: 'overdue', label: 'Просроченные задачи' },
];
let dashboardConfig = (() => { try { const s = JSON.parse(localStorage.getItem('ch_dashboard_cfg')); if (s && typeof s === 'object') return s; } catch {} const d = {}; DASHBOARD_WIDGETS.forEach(w => d[w.key] = true); return d; })();

function renderTaskProjects() {
    document.getElementById('tasks-project-list').style.display = 'flex';
    document.getElementById('tasks-board-view').style.display = 'none';
    const tbody = document.getElementById('tasks-projects-tbody');
    if (!taskProjects.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="tasks-empty-td"><span class="material-icons-round">assignment</span>Проектов пока нет. Создайте первый!</td></tr>';
        renderTasksDashboard();
        return;
    }
    tbody.innerHTML = '';
    taskProjects.forEach(p => {
        const tr = document.createElement('tr');
        tr.className = 'tasks-project-row';
        tr.onclick = () => openTaskProject(p.slug);
        const dateStr = (p.created_at || '').slice(0, 10);
        const visIcon = p.visibility === 'public' ? '<span class="material-icons-round vis-icon vis-public">public</span> Публичный' : '<span class="material-icons-round vis-icon vis-private">lock</span> Приватный';
        tr.innerHTML = `<td class="tp-name">${esc(p.name)}</td>
            <td class="tp-desc">${esc(p.description || '—')}</td>
            <td class="tp-creator">${esc(p.creator_name || p.created_by || '')}</td>
            <td class="tp-visibility">${visIcon}</td>
            <td class="tp-members">${p.member_count || 0}</td>
            <td class="tp-tasks">${p.task_count || 0}</td>
            <td class="tp-children">${p.child_count || 0}</td>
            <td class="tp-role">${PROJECT_ROLE_RU[p.my_role] || p.my_role || ''}</td>
            <td class="tp-date">${dateStr}</td>`;
        tbody.appendChild(tr);
    });
    renderTasksDashboard();
}

// ── Dashboard ────────────────────────────────────────────────
async function renderTasksDashboard() {
    const container = document.getElementById('tasks-dashboard');
    if (!container) return;
    const res = await apiFetch('/tasks/dashboard');
    if (!res || !res.ok) { container.style.display = 'none'; return; }
    const stats = await res.json();
    container.style.display = 'block';
    container.innerHTML = '';
    // Header with config
    const header = document.createElement('div');
    header.className = 'dashboard-header';
    header.innerHTML = `<h3 class="dashboard-title">Статистика</h3>
        <button class="btn-icon" onclick="toggleDashboardConfig()" title="Настроить виджеты"><span class="material-icons-round">tune</span></button>`;
    container.appendChild(header);
    // Config panel
    const cfg = document.createElement('div');
    cfg.id = 'dashboard-config-panel';
    cfg.className = 'dashboard-config-panel';
    cfg.style.display = 'none';
    DASHBOARD_WIDGETS.forEach(w => {
        const label = document.createElement('label');
        label.className = 'dashboard-config-item';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = dashboardConfig[w.key] !== false;
        cb.onchange = () => { dashboardConfig[w.key] = cb.checked; localStorage.setItem('ch_dashboard_cfg', JSON.stringify(dashboardConfig)); renderTasksDashboard(); };
        label.appendChild(cb);
        label.appendChild(document.createTextNode(' ' + w.label));
        cfg.appendChild(label);
    });
    container.appendChild(cfg);
    // Widgets grid
    const grid = document.createElement('div');
    grid.className = 'dashboard-widgets';
    container.appendChild(grid);
    if (dashboardConfig.total_status !== false) grid.appendChild(buildStatusWidget('Все задачи', stats.totals));
    if (dashboardConfig.per_project !== false && taskProjects.length) grid.appendChild(buildProjectBarsWidget(taskProjects));
    if (dashboardConfig.my_tasks !== false) grid.appendChild(buildStatusWidget('Мои задачи', stats.my_tasks));
    if (dashboardConfig.overdue !== false) grid.appendChild(buildOverdueWidget(stats.overdue_count));
}

function buildStatusWidget(title, data) {
    const w = document.createElement('div');
    w.className = 'dashboard-widget';
    const total = data.total || 0, todo = data.todo || 0, ip = data.in_progress || 0, rev = data.review || 0, done = data.done || 0;
    const bar = total > 0 ? `<div class="status-bar">
        <div class="status-bar-seg" style="width:${todo/total*100}%;background:var(--text3)" title="К выполнению: ${todo}"></div>
        <div class="status-bar-seg" style="width:${ip/total*100}%;background:#4a90d9" title="В работе: ${ip}"></div>
        <div class="status-bar-seg" style="width:${rev/total*100}%;background:#f39c12" title="На проверке: ${rev}"></div>
        <div class="status-bar-seg" style="width:${done/total*100}%;background:#2ecc71" title="Готово: ${done}"></div>
    </div>` : '<div class="status-bar-empty">Нет задач</div>';
    w.innerHTML = `<div class="dashboard-widget-title">${title}</div>
        <div class="dashboard-widget-number">${total}</div>${bar}
        <div class="status-legend">
            <span><span class="legend-dot" style="background:var(--text3)"></span>К выполнению: ${todo}</span>
            <span><span class="legend-dot" style="background:#4a90d9"></span>В работе: ${ip}</span>
            <span><span class="legend-dot" style="background:#f39c12"></span>На проверке: ${rev}</span>
            <span><span class="legend-dot" style="background:#2ecc71"></span>Готово: ${done}</span>
        </div>`;
    return w;
}

function buildProjectBarsWidget(projects) {
    const w = document.createElement('div');
    w.className = 'dashboard-widget dashboard-widget-wide';
    const maxT = Math.max(...projects.map(p => p.task_count || 0), 1);
    let bars = '';
    projects.forEach(p => {
        const total = p.task_count || 0, done = p.done_count || 0;
        const pct = total / maxT * 100, donePct = total > 0 ? done / total * 100 : 0;
        bars += `<div class="project-bar-row">
            <span class="project-bar-label">${esc(p.name)}</span>
            <div class="project-bar-track"><div class="project-bar-fill" style="width:${pct}%"><div class="project-bar-done" style="width:${donePct}%"></div></div></div>
            <span class="project-bar-count">${done}/${total}</span></div>`;
    });
    w.innerHTML = `<div class="dashboard-widget-title">Задачи по проектам</div><div class="project-bars">${bars}</div>`;
    return w;
}

function buildOverdueWidget(count) {
    const w = document.createElement('div');
    w.className = 'dashboard-widget';
    const color = count > 0 ? '#e74c3c' : '#2ecc71';
    w.innerHTML = `<div class="dashboard-widget-title">Просроченные</div>
        <div class="dashboard-widget-number" style="color:${color}">${count || 0}</div>
        <div class="dashboard-widget-hint">${count > 0 ? 'Задачи с истекшим сроком' : 'Нет просроченных задач'}</div>`;
    return w;
}

function toggleDashboardConfig() {
    const p = document.getElementById('dashboard-config-panel');
    if (p) p.style.display = p.style.display === 'none' ? 'flex' : 'none';
}

async function showCreateProjectDialog() {
    document.getElementById('create-project-dialog').style.display = 'flex';
    document.getElementById('project-name-input').value = '';
    document.getElementById('project-prefix-input').value = '';
    document.getElementById('project-desc-input').value = '';
    projectCreationMembers = [];
    renderProjectCreationChips();
    // Load users for member picker
    if (!allUsersCache.length) {
        const res = await apiFetch('/users');
        if (res && res.ok) allUsersCache = await res.json();
    }
    const sel = document.getElementById('project-member-select');
    sel.innerHTML = '<option value="">Добавить участника...</option>';
    allUsersCache.forEach(u => {
        if (u.username === currentUser) return;
        const opt = document.createElement('option');
        opt.value = u.username;
        opt.textContent = u.display_name || u.nickname || u.username;
        sel.appendChild(opt);
    });
    document.getElementById('project-name-input').focus();
}
function closeCreateProjectDialog() { document.getElementById('create-project-dialog').style.display = 'none'; }

function addProjectCreationMember() {
    const sel = document.getElementById('project-member-select');
    const username = sel.value;
    if (!username) return;
    const role = document.getElementById('project-member-role').value;
    if (projectCreationMembers.find(m => m.username === username)) return;
    const user = allUsersCache.find(u => u.username === username);
    projectCreationMembers.push({ username, role, display_name: user?.display_name || user?.nickname || username, avatar_path: user?.avatar_path || '' });
    sel.querySelector(`option[value="${username}"]`).remove();
    sel.value = '';
    renderProjectCreationChips();
}

function removeProjectCreationMember(username) {
    const member = projectCreationMembers.find(m => m.username === username);
    projectCreationMembers = projectCreationMembers.filter(m => m.username !== username);
    if (member) {
        const sel = document.getElementById('project-member-select');
        const opt = document.createElement('option');
        opt.value = username;
        opt.textContent = member.display_name;
        sel.appendChild(opt);
    }
    renderProjectCreationChips();
}

function renderProjectCreationChips() {
    const container = document.getElementById('project-members-chips');
    container.innerHTML = '';
    projectCreationMembers.forEach(m => {
        const chip = document.createElement('span');
        chip.className = 'member-chip';
        const avatar = m.avatar_path ? `<img src="${srv(m.avatar_path)}" alt="">` : `<span class="material-icons-round" style="font-size:20px">person</span>`;
        const CHIP_ROLE_RU = { member: 'участник', lead: 'руководитель', viewer: 'наблюдатель' };
        chip.innerHTML = `${avatar} <span class="chip-name"></span> <span class="chip-role">${CHIP_ROLE_RU[m.role] || m.role}</span><span class="remove-chip material-icons-round" onclick="removeProjectCreationMember('${m.username}')">close</span>`;
        chip.querySelector('.chip-name').textContent = m.display_name;
        container.appendChild(chip);
    });
}

async function createProject() {
    const name = document.getElementById('project-name-input').value.trim();
    if (!name) return showToast('Введите название проекта', 'error');
    const prefix = document.getElementById('project-prefix-input').value.trim().toUpperCase();
    const desc = document.getElementById('project-desc-input').value.trim();
    const members = projectCreationMembers.map(m => ({ username: m.username, role: m.role }));
    try {
        const res = await apiFetch('/tasks/projects', { method: 'POST', body: { name, description: desc, members, prefix } });
        if (!res) return showToast('Ошибка сети', 'error');
        if (!res.ok) {
            const err = await res.text().catch(() => '');
            return showToast('Ошибка создания: ' + (err || res.status), 'error');
        }
        closeCreateProjectDialog();
        await loadTaskProjects();
        showToast('Проект создан');
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

function showCreateSubprojectDialog() {
    if (!currentTaskProject) return;
    document.getElementById('create-subproject-dialog').style.display = 'flex';
    document.getElementById('subproject-name-input').value = '';
    document.getElementById('subproject-prefix-input').value = '';
    document.getElementById('subproject-name-input').focus();
}
function closeCreateSubprojectDialog() {
    document.getElementById('create-subproject-dialog').style.display = 'none';
}

async function createSubproject() {
    const name = document.getElementById('subproject-name-input').value.trim();
    if (!name) return showToast('Введите название подпроекта', 'error');
    const prefix = document.getElementById('subproject-prefix-input').value.trim().toUpperCase();
    const res = await apiFetch('/tasks/projects', {
        method: 'POST',
        body: { name, description: '', parent_id: currentTaskProject.id, prefix },
    });
    if (!res || !res.ok) return showToast('Ошибка создания подпроекта', 'error');
    closeCreateSubprojectDialog();
    showToast('Подпроект создан');
    await openTaskProject(currentTaskProjectSlug);
}

function tasksShowProjectList() {
    currentTaskProject = null;
    currentTaskProjectSlug = null;
    taskProjectTasks = [];
    taskBreadcrumb = [];
    pushRoute('/tracker');
    document.getElementById('tasks-project-list').style.display = 'flex';
    document.getElementById('tasks-board-view').style.display = 'none';
    document.getElementById('tasks-submit-view').style.display = 'none';
}

function tasksGoBack() {
    if (taskBreadcrumb.length > 0) {
        const parent = taskBreadcrumb.pop();
        openTaskProject(parent.slug);
    } else {
        tasksShowProjectList();
    }
}

async function openTaskProject(slug) {
    pushRoute('/tracker/' + encodeURIComponent(slug));
    currentTaskProjectSlug = slug;
    const res = await apiFetch(`/tasks/projects/${slug}`);
    if (!res || !res.ok) return;
    currentTaskProject = await res.json();
    taskProjectMembers = currentTaskProject.members || [];

    // Non-member of public project → redirect to submit page
    if (!currentTaskProject.is_member && currentTaskProject.visibility === 'public') {
        await openSubmitTicketPage(slug);
        return;
    }

    // Set breadcrumb from server data
    if (currentTaskProject.breadcrumb && currentTaskProject.breadcrumb.length > 0) {
        taskBreadcrumb = currentTaskProject.breadcrumb;
    }

    const hasChildren = (currentTaskProject.children || []).length > 0;

    // Always load labels, issue-types, fields (needed for settings dialog)
    // Only skip tasks for parent projects with sub-projects
    if (!hasChildren) {
        const [tasksRes, labelsRes, typesRes, fieldsRes] = await Promise.all([
            apiFetch(`/tasks/projects/${slug}/tasks`),
            apiFetch(`/tasks/projects/${slug}/labels`),
            apiFetch(`/tasks/projects/${slug}/issue-types`),
            apiFetch(`/tasks/projects/${slug}/fields`),
        ]);
        taskProjectTasks = tasksRes && tasksRes.ok ? await tasksRes.json() : [];
        taskProjectLabels = labelsRes && labelsRes.ok ? await labelsRes.json() : [];
        taskProjectIssueTypes = typesRes && typesRes.ok ? await typesRes.json() : [];
        taskProjectCustomFields = fieldsRes && fieldsRes.ok ? await fieldsRes.json() : [];
    } else {
        const [labelsRes, typesRes, fieldsRes] = await Promise.all([
            apiFetch(`/tasks/projects/${slug}/labels`),
            apiFetch(`/tasks/projects/${slug}/issue-types`),
            apiFetch(`/tasks/projects/${slug}/fields`),
        ]);
        taskProjectTasks = [];
        taskProjectLabels = labelsRes && labelsRes.ok ? await labelsRes.json() : [];
        taskProjectIssueTypes = typesRes && typesRes.ok ? await typesRes.json() : [];
        taskProjectCustomFields = fieldsRes && fieldsRes.ok ? await fieldsRes.json() : [];
    }

    document.getElementById('tasks-project-list').style.display = 'none';
    document.getElementById('tasks-board-view').style.display = 'flex';
    document.getElementById('tasks-submit-view').style.display = 'none';
    document.getElementById('tasks-board-title').textContent = currentTaskProject.name;
    document.getElementById('tasks-board-desc').textContent = currentTaskProject.description || '';

    // Hide task-related UI when project has sub-projects
    const createTaskBtn = document.querySelector('.btn-action.btn-accent[onclick="showCreateTaskDialog()"]');
    const filterBar = document.getElementById('tasks-filter-bar');
    const viewToggle = document.querySelector('.tasks-view-toggle');
    if (hasChildren) {
        if (createTaskBtn) createTaskBtn.style.display = 'none';
        if (filterBar) filterBar.style.display = 'none';
        if (viewToggle) viewToggle.style.display = 'none';
        document.getElementById('tasks-kanban').style.display = 'none';
        document.getElementById('tasks-list').style.display = 'none';
    } else {
        if (createTaskBtn) createTaskBtn.style.display = '';
        if (filterBar) filterBar.style.display = '';
        if (viewToggle) viewToggle.style.display = '';
    }

    // Render breadcrumb
    renderTasksBreadcrumb();

    // Render sub-projects (table for parent, cards hidden for leaf)
    renderSubprojects();

    if (!hasChildren) {
        populateTaskDropdowns();
        renderTaskBoard();
    }
}

function renderTasksBreadcrumb() {
    const bc = document.getElementById('tasks-breadcrumb');
    const crumbs = currentTaskProject.breadcrumb || [];
    if (crumbs.length === 0) {
        bc.style.display = 'none';
        return;
    }
    bc.style.display = 'flex';
    let html = `<span class="breadcrumb-item" onclick="tasksShowProjectList();loadTaskProjects()">Проекты</span>`;
    crumbs.forEach(c => {
        html += ` <span class="breadcrumb-sep">›</span> <span class="breadcrumb-item" onclick="openTaskProject('${esc(c.slug)}')">${esc(c.name)}</span>`;
    });
    html += ` <span class="breadcrumb-sep">›</span> <span class="breadcrumb-current">${esc(currentTaskProject.name)}</span>`;
    bc.innerHTML = html;
}

function renderSubprojects() {
    const container = document.getElementById('tasks-subprojects');
    const children = currentTaskProject.children || [];
    if (children.length === 0) {
        container.style.display = 'none';
        return;
    }
    container.style.display = 'block';
    container.innerHTML = '';

    // Render as a table
    const table = document.createElement('div');
    table.className = 'subprojects-table';
    // Header
    const header = document.createElement('div');
    header.className = 'subprojects-table-header';
    header.innerHTML = `<span class="sp-col-name">Подпроект</span><span class="sp-col-prefix">Префикс</span><span class="sp-col-tasks">Задачи</span>`;
    table.appendChild(header);
    // Rows
    children.forEach(c => {
        const row = document.createElement('div');
        row.className = 'subprojects-table-row';
        row.onclick = () => {
            taskBreadcrumb.push({ slug: currentTaskProjectSlug, name: currentTaskProject.name });
            openTaskProject(c.slug);
        };
        row.innerHTML = `<span class="sp-col-name"><span class="material-icons-round" style="font-size:16px;color:var(--accent);vertical-align:middle">account_tree</span> ${esc(c.name)}</span><span class="sp-col-prefix">${esc(c.prefix || '—')}</span><span class="sp-col-tasks">${c.task_count || 0}</span>`;
        table.appendChild(row);
    });
    container.appendChild(table);
}

function populateTaskDropdowns() {
    // Assignee dropdowns
    const assigneeSelectors = ['task-assignee-input', 'task-detail-assignee', 'tasks-filter-assignee'];
    assigneeSelectors.forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const val = sel.value;
        sel.innerHTML = '<option value="">Не назначен</option>';
        if (id === 'tasks-filter-assignee') sel.innerHTML = '<option value="">Все исполнители</option>';
        taskProjectMembers.forEach(m => {
            sel.innerHTML += `<option value="${esc(m.username)}">${esc(m.display_name || m.username)}</option>`;
        });
        sel.value = val;
    });
    // Creator dropdown for filter
    const creatorSel = document.getElementById('tasks-filter-creator');
    if (creatorSel) {
        const val = creatorSel.value;
        creatorSel.innerHTML = '<option value="">Все авторы</option>';
        taskProjectMembers.forEach(m => {
            creatorSel.innerHTML += `<option value="${esc(m.username)}">${esc(m.display_name || m.username)}</option>`;
        });
        creatorSel.value = val;
    }
    // Issue type dropdowns
    const typeSelectors = ['task-issue-type-input', 'task-detail-issue-type', 'tasks-filter-type'];
    typeSelectors.forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const val = sel.value;
        sel.innerHTML = '<option value="">Без типа</option>';
        if (id === 'tasks-filter-type') sel.innerHTML = '<option value="">Все типы</option>';
        taskProjectIssueTypes.forEach(it => {
            sel.innerHTML += `<option value="${esc(it.id)}">${it.icon} ${esc(it.name)}</option>`;
        });
        sel.value = val;
    });
    // Label filter
    const labelSel = document.getElementById('tasks-filter-label');
    if (labelSel) {
        const val = labelSel.value;
        labelSel.innerHTML = '<option value="">Все метки</option>';
        taskProjectLabels.forEach(l => {
            labelSel.innerHTML += `<option value="${esc(l.id)}">${esc(l.name)}</option>`;
        });
        labelSel.value = val;
    }
    // Custom field filters (for select-type fields)
    renderCustomFieldFilters();
}

// ── Kanban Board ────────────────────────────────────────────
function renderTaskBoard() {
    if (tasksViewMode === 'board') {
        document.getElementById('tasks-kanban').style.display = 'flex';
        document.getElementById('tasks-list').style.display = 'none';
        renderKanban();
    } else {
        document.getElementById('tasks-kanban').style.display = 'none';
        document.getElementById('tasks-list').style.display = 'flex';
        renderTaskList();
    }
}

function renderCustomFieldFilters() {
    const container = document.getElementById('tasks-filter-custom');
    if (!container) return;
    container.innerHTML = '';
    // Only render select-type custom fields as filters
    taskProjectCustomFields.filter(f => f.field_type === 'select').forEach(f => {
        const sel = document.createElement('select');
        sel.id = 'tasks-filter-cf-' + f.id;
        sel.className = 'tasks-filter-input';
        sel.onchange = () => applyTaskFilters();
        sel.innerHTML = `<option value="">${esc(f.name)}</option>`;
        const opts = typeof f.options === 'string' ? JSON.parse(f.options) : (f.options || []);
        opts.forEach(o => { sel.innerHTML += `<option value="${esc(o)}">${esc(o)}</option>`; });
        container.appendChild(sel);
    });
}

function getFilteredTasks() {
    let tasks = taskProjectTasks;
    const fa = document.getElementById('tasks-filter-assignee');
    const fp = document.getElementById('tasks-filter-priority');
    const fc = document.getElementById('tasks-filter-creator');
    const ft = document.getElementById('tasks-filter-type');
    const fl = document.getElementById('tasks-filter-label');
    const fs = document.getElementById('tasks-filter-search');
    const fdf = document.getElementById('tasks-filter-date-from');
    const fdt = document.getElementById('tasks-filter-date-to');
    if (fa && fa.value) tasks = tasks.filter(t => t.assignee === fa.value);
    if (fp && fp.value) tasks = tasks.filter(t => t.priority === fp.value);
    if (fc && fc.value) tasks = tasks.filter(t => t.created_by === fc.value);
    if (ft && ft.value) tasks = tasks.filter(t => t.issue_type_id === ft.value);
    if (fl && fl.value) tasks = tasks.filter(t => (t.labels || []).some(l => l.id === fl.value));
    if (fs && fs.value) {
        const q = fs.value.toLowerCase();
        tasks = tasks.filter(t => t.title.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q));
    }
    if (fdf && fdf.value) tasks = tasks.filter(t => t.due_date && t.due_date >= fdf.value);
    if (fdt && fdt.value) tasks = tasks.filter(t => t.due_date && t.due_date <= fdt.value);
    // Custom field filters (select-type)
    taskProjectCustomFields.filter(f => f.field_type === 'select').forEach(f => {
        const cfSel = document.getElementById('tasks-filter-cf-' + f.id);
        if (cfSel && cfSel.value) {
            tasks = tasks.filter(t => {
                const cv = (t.custom_values || []).find(v => v.field_id === f.id);
                return cv && cv.value === cfSel.value;
            });
        }
    });
    return tasks;
}

function clearTaskFilters() {
    ['tasks-filter-assignee', 'tasks-filter-priority', 'tasks-filter-creator', 'tasks-filter-type', 'tasks-filter-label'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    ['tasks-filter-search', 'tasks-filter-date-from', 'tasks-filter-date-to'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    // Clear custom field filters
    taskProjectCustomFields.filter(f => f.field_type === 'select').forEach(f => {
        const el = document.getElementById('tasks-filter-cf-' + f.id);
        if (el) el.value = '';
    });
    applyTaskFilters();
}

function renderKanban() {
    const statuses = ['todo', 'in_progress', 'review', 'done'];
    const filtered = getFilteredTasks();
    statuses.forEach(status => {
        const col = document.getElementById(`kanban-${status}`);
        const countEl = document.getElementById(`kanban-count-${status}`);
        const tasks = filtered.filter(t => t.status === status).sort((a, b) => a.position - b.position);
        countEl.textContent = tasks.length;
        col.innerHTML = '';
        tasks.forEach(task => col.appendChild(renderKanbanCard(task)));
    });
}

function renderKanbanCard(task) {
    const card = document.createElement('div');
    card.className = 'kanban-card';
    card.draggable = true;
    card.dataset.taskId = task.id;
    card.ondragstart = (e) => { draggedTaskId = task.id; e.dataTransfer.effectAllowed = 'move'; card.classList.add('dragging'); };
    card.ondragend = () => { card.classList.remove('dragging'); draggedTaskId = null; };
    card.onclick = () => openTaskDetail(task.id);
    const pColors = { critical: '#e74c3c', high: '#f39c12', medium: 'var(--accent)', low: 'var(--text3)' };
    const labels = (task.labels || []).map(l => `<span class="kanban-label" style="background:${l.color}">${esc(l.name)}</span>`).join('');
    let assigneeHtml = '';
    if (task.assignee_name) {
        const avatarUrl = task.assignee_avatar ? (API.replace('/api', '') + task.assignee_avatar) : '';
        assigneeHtml = `<div class="kanban-card-assignee"><div class="kanban-assignee-avatar">${avatarUrl ? `<img src="${avatarUrl}">` : (task.assignee_name[0] || '?')}</div></div>`;
    }
    const typeTag = task.issue_type ? `<span class="kanban-type-tag" style="color:${task.issue_type.color}">${task.issue_type.icon} ${esc(task.issue_type.name)}</span>` : '';
    const keyTag = task.task_key ? `<span class="task-key">${esc(task.task_key)}</span>` : '';
    card.innerHTML = `${labels ? `<div class="kanban-card-labels">${labels}</div>` : ''}
        ${typeTag}
        ${keyTag}
        <div class="kanban-card-title">${esc(task.title)}</div>
        <div class="kanban-card-meta">
            <span class="kanban-priority" style="color:${pColors[task.priority] || pColors.medium}">${PRIORITY_RU[task.priority] || task.priority}</span>
            ${task.due_date ? `<span>📅 ${task.due_date}</span>` : ''}
            ${task.subtask_total ? `<span>☑ ${task.subtask_done}/${task.subtask_total}</span>` : ''}
            ${task.comment_count ? `<span>💬 ${task.comment_count}</span>` : ''}
            ${task.attachment_count ? `<span>📎 ${task.attachment_count}</span>` : ''}
        </div>${assigneeHtml}`;
    return card;
}

function onKanbanDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; e.currentTarget.classList.add('kanban-drop-target'); }
function onKanbanDragLeave(e) { e.currentTarget.classList.remove('kanban-drop-target'); }

async function onKanbanDrop(e, newStatus) {
    e.preventDefault();
    e.currentTarget.classList.remove('kanban-drop-target');
    if (!draggedTaskId) return;
    const task = taskProjectTasks.find(t => t.id === draggedTaskId);
    if (!task || task.status === newStatus) return;
    const oldStatus = task.status;
    task.status = newStatus;
    renderTaskBoard();
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${draggedTaskId}/status`, { method: 'PATCH', body: { status: newStatus } });
    if (!res || !res.ok) { task.status = oldStatus; renderTaskBoard(); showToast('Ошибка обновления статуса', 'error'); }
}

// ── Column Picker ───────────────────────────────────────────
function toggleColumnPicker() {
    const p = document.getElementById('tasks-col-picker');
    if (!p) return;
    const show = p.style.display === 'none';
    p.style.display = show ? 'block' : 'none';
    if (show) buildColumnPickerOptions();
}
function buildColumnPickerOptions() {
    const p = document.getElementById('tasks-col-picker');
    if (!p) return;
    p.innerHTML = '<div class="tasks-col-picker-title">Столбцы</div>';
    TASK_COLUMNS.forEach(col => {
        const label = document.createElement('label');
        label.className = 'tasks-col-option' + (col.locked ? ' tasks-col-option-locked' : '');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = taskColVis[col.key] !== false;
        cb.disabled = !!col.locked;
        if (!col.locked) cb.onchange = () => { taskColVis[col.key] = cb.checked; localStorage.setItem('ch_task_cols', JSON.stringify(taskColVis)); applyTaskColVisibility(); };
        label.appendChild(cb);
        label.appendChild(document.createTextNode(' ' + col.label));
        p.appendChild(label);
    });
}
function applyTaskColVisibility() {
    const visible = TASK_COLUMNS.filter(c => taskColVis[c.key] !== false);
    const grid = visible.map(c => c.fr).join(' ') + ' auto';
    const header = document.querySelector('.tasks-list-header');
    if (header) header.style.gridTemplateColumns = grid;
    document.querySelectorAll('.tasks-list-row').forEach(r => r.style.gridTemplateColumns = grid);
    TASK_COLUMNS.forEach(col => {
        const show = taskColVis[col.key] !== false;
        document.querySelectorAll('.' + col.key).forEach(el => el.style.display = show ? '' : 'none');
    });
}

// ── List View ───────────────────────────────────────────────
function renderTaskList() {
    const body = document.getElementById('tasks-list-body');
    body.innerHTML = '';
    const filtered = getFilteredTasks();
    const order = { critical: 0, high: 1, medium: 2, low: 3 };
    filtered.sort((a, b) => (order[a.priority] ?? 2) - (order[b.priority] ?? 2));
    filtered.forEach(task => {
        const row = document.createElement('div');
        row.className = 'tasks-list-row';
        row.onclick = () => openTaskDetail(task.id);
        const typeHtml = task.issue_type ? `<span style="color:${task.issue_type.color}">${task.issue_type.icon} ${esc(task.issue_type.name)}</span>` : '<span style="color:var(--text3)">—</span>';
        const keyHtml = task.task_key ? `<span class="task-key">${esc(task.task_key)}</span> ` : '';
        row.innerHTML = `<span class="tl-type">${typeHtml}</span>
            <span class="tl-title">${keyHtml}${esc(task.title)}</span>
            <span class="tl-status"><span class="status-badge status-${task.status}">${STATUS_RU[task.status] || task.status}</span></span>
            <span class="tl-priority"><span class="priority-badge priority-${task.priority}">${PRIORITY_RU[task.priority] || task.priority}</span></span>
            <span class="tl-assignee">${esc(task.assignee_name || 'Не назначен')}</span>
            <span class="tl-date">${task.due_date || '-'}</span>`;
        body.appendChild(row);
    });
    applyTaskColVisibility();
}

function setTasksView(mode) {
    tasksViewMode = mode;
    document.querySelectorAll('.tasks-view-btn').forEach(b => b.classList.toggle('active', b.dataset.view === mode));
    renderTaskBoard();
}

function applyTaskFilters() { renderTaskBoard(); }

// ── Create Task ─────────────────────────────────────────────
function showCreateTaskDialog() {
    document.getElementById('create-task-dialog').style.display = 'flex';
    document.getElementById('task-title-input').value = '';
    document.getElementById('task-desc-input').value = '';
    document.getElementById('task-priority-input').value = 'medium';
    document.getElementById('task-assignee-input').value = '';
    document.getElementById('task-due-input').value = '';
    const typeSel = document.getElementById('task-issue-type-input');
    if (typeSel) typeSel.value = taskProjectIssueTypes.length > 0 ? taskProjectIssueTypes[0].id : '';
    populateTaskDropdowns();
    renderLabelChips('task-labels-select', []);
    renderCustomFieldsForCreate();
    document.getElementById('task-title-input').focus();
}
function closeCreateTaskDialog() {
    document.getElementById('create-task-dialog').style.display = 'none';
    createTaskFiles = [];
    renderCreateTaskFiles();
}

function onCreateTaskIssueTypeChange() {
    renderCustomFieldsForCreate();
}

function renderCustomFieldsForCreate() {
    const container = document.getElementById('task-custom-fields-create');
    if (!container) return;
    container.innerHTML = '';
    const typeId = document.getElementById('task-issue-type-input')?.value;
    // Show all project custom fields (in future we can filter by issue type binding)
    taskProjectCustomFields.forEach(f => {
        container.appendChild(renderCustomFieldInput(f, ''));
    });
}

function renderCustomFieldInput(field, value) {
    const div = document.createElement('div');
    div.className = 'task-custom-field-row';
    div.dataset.fieldId = field.id;
    const label = document.createElement('label');
    label.textContent = field.name + (field.required ? ' *' : '');
    label.style.cssText = 'font-size:12px;color:var(--text2);display:block;margin-bottom:2px;';
    div.appendChild(label);

    let input;
    switch (field.field_type) {
        case 'select': {
            input = document.createElement('select');
            input.innerHTML = '<option value="">—</option>';
            (field.options || []).forEach(opt => {
                input.innerHTML += `<option value="${esc(opt)}" ${opt === value ? 'selected' : ''}>${esc(opt)}</option>`;
            });
            break;
        }
        case 'checkbox': {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = value === 'true' || value === '1';
            break;
        }
        case 'date': {
            input = document.createElement('input');
            input.type = 'date';
            input.value = value || '';
            break;
        }
        case 'number': {
            input = document.createElement('input');
            input.type = 'number';
            input.value = value || '';
            break;
        }
        case 'url': {
            input = document.createElement('input');
            input.type = 'url';
            input.value = value || '';
            input.placeholder = 'https://...';
            break;
        }
        default: {
            input = document.createElement('input');
            input.type = 'text';
            input.value = value || '';
            break;
        }
    }
    input.className = 'custom-field-input';
    input.dataset.fieldId = field.id;
    input.dataset.fieldType = field.field_type;
    div.appendChild(input);
    return div;
}

function getCustomFieldValues(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return {};
    const values = {};
    container.querySelectorAll('.custom-field-input').forEach(input => {
        const fid = input.dataset.fieldId;
        if (input.dataset.fieldType === 'checkbox') {
            values[fid] = input.checked ? 'true' : 'false';
        } else if (input.value) {
            values[fid] = input.value;
        }
    });
    return values;
}

function renderLabelChips(containerId, selectedIds) {
    const c = document.getElementById(containerId);
    c.innerHTML = '';
    taskProjectLabels.forEach(l => {
        const chip = document.createElement('span');
        chip.className = 'task-label-chip' + (selectedIds.includes(l.id) ? ' selected' : '');
        chip.style.background = l.color;
        chip.textContent = l.name;
        chip.onclick = () => { chip.classList.toggle('selected'); };
        chip.dataset.labelId = l.id;
        c.appendChild(chip);
    });
}

function getSelectedLabelIds(containerId) {
    return [...document.querySelectorAll(`#${containerId} .task-label-chip.selected`)].map(c => c.dataset.labelId);
}

function addCreateTaskFiles(fileList) {
    if (!fileList) return;
    for (const f of fileList) createTaskFiles.push(f);
    document.getElementById('task-create-file-input').value = '';
    renderCreateTaskFiles();
}

function removeCreateTaskFile(idx) {
    createTaskFiles.splice(idx, 1);
    renderCreateTaskFiles();
}

function renderCreateTaskFiles() {
    const list = document.getElementById('task-create-file-list');
    if (!list) return;
    list.innerHTML = '';
    createTaskFiles.forEach((f, i) => {
        const size = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + ' MB' : (f.size / 1024).toFixed(0) + ' KB';
        const item = document.createElement('div');
        item.className = 'task-attachment-item';
        item.innerHTML = `<span class="material-icons-round" style="font-size:16px">attach_file</span>
            <span style="flex:1;font-size:13px">${esc(f.name)}</span>
            <span class="task-attachment-size">${size}</span>
            <button class="btn-icon" onclick="removeCreateTaskFile(${i})"><span class="material-icons-round" style="font-size:16px">close</span></button>`;
        list.appendChild(item);
    });
}

async function createTask() {
    const title = document.getElementById('task-title-input').value.trim();
    if (!title) return showToast('Введите название задачи', 'error');
    const body = {
        title,
        description: document.getElementById('task-desc-input').value.trim(),
        priority: document.getElementById('task-priority-input').value,
        assignee: document.getElementById('task-assignee-input').value || null,
        due_date: document.getElementById('task-due-input').value || null,
        issue_type_id: document.getElementById('task-issue-type-input')?.value || null,
        label_ids: getSelectedLabelIds('task-labels-select'),
        custom_values: getCustomFieldValues('task-custom-fields-create'),
    };
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks`, { method: 'POST', body });
    if (!res || !res.ok) return showToast('Ошибка создания задачи', 'error');
    const task = await res.json();
    // Upload attached files
    for (const f of createTaskFiles) {
        const fd = new FormData();
        fd.append('file', f);
        await fetch(API + `/tasks/projects/${currentTaskProjectSlug}/tasks/${task.id}/attachments`, {
            method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: fd,
        });
    }
    createTaskFiles = [];
    taskProjectTasks.push(task);
    closeCreateTaskDialog();
    renderTaskBoard();
    showToast('Задача создана');
}

// ── Task Detail ─────────────────────────────────────────────
async function openTaskDetail(taskId) {
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${taskId}`);
    if (!res || !res.ok) return;
    currentTaskDetail = await res.json();
    const titleEl = document.getElementById('task-detail-title');
    if (currentTaskDetail.task_key) {
        titleEl.innerHTML = `<span class="task-key">${esc(currentTaskDetail.task_key)}</span> ${esc(currentTaskDetail.title)}`;
    } else {
        titleEl.textContent = currentTaskDetail.title;
    }
    document.getElementById('task-detail-desc').textContent = currentTaskDetail.description || 'No description';
    document.getElementById('task-detail-status').value = currentTaskDetail.status;
    document.getElementById('task-detail-priority').value = currentTaskDetail.priority;
    populateTaskDropdowns();
    document.getElementById('task-detail-assignee').value = currentTaskDetail.assignee || '';
    document.getElementById('task-detail-due').value = currentTaskDetail.due_date || '';
    // Issue type
    const itSel = document.getElementById('task-detail-issue-type');
    if (itSel) itSel.value = currentTaskDetail.issue_type_id || '';
    document.getElementById('task-detail-created').textContent = 'Создано: ' + (currentTaskDetail.created_at || '').slice(0, 10);
    document.getElementById('task-detail-author').textContent = 'Автор: ' + (currentTaskDetail.created_by || '');
    // Permission: hide delete if not owner/lead
    const myRole = currentTaskProject ? currentTaskProject.my_role : '';
    document.getElementById('task-delete-btn').style.display = (myRole === 'owner' || myRole === 'lead') ? '' : 'none';
    renderTaskSubtasks(currentTaskDetail.subtasks || []);
    renderTaskComments(currentTaskDetail.comments || []);
    renderTaskAttachments(currentTaskDetail.attachments || []);
    renderTaskDetailLabels(currentTaskDetail.labels || []);
    renderTaskDetailCustomFields();
    document.getElementById('task-detail-modal').style.display = 'flex';
}

function renderTaskDetailCustomFields() {
    const container = document.getElementById('task-detail-custom-fields');
    if (!container) return;
    container.innerHTML = '';
    const existingValues = {};
    (currentTaskDetail.custom_values || []).forEach(cv => {
        existingValues[cv.field_id] = cv.value;
    });
    taskProjectCustomFields.forEach(f => {
        const val = existingValues[f.id] || '';
        const row = renderCustomFieldInput(f, val);
        // Add save on change
        const input = row.querySelector('.custom-field-input');
        if (input) {
            const saveHandler = async () => {
                const newVal = input.dataset.fieldType === 'checkbox' ? (input.checked ? 'true' : 'false') : input.value;
                await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}`, {
                    method: 'PUT',
                    body: { custom_values: { [f.id]: newVal } },
                });
            };
            input.addEventListener('change', saveHandler);
        }
        container.appendChild(row);
    });
}
function closeTaskDetail() { document.getElementById('task-detail-modal').style.display = 'none'; currentTaskDetail = null; }

async function updateTaskField(field, value) {
    if (!currentTaskDetail) return;
    const body = {};
    body[field] = value;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}`, { method: 'PUT', body });
    if (!res || !res.ok) return showToast('Ошибка обновления задачи', 'error');
    const updated = await res.json();
    // Update in local list
    const idx = taskProjectTasks.findIndex(t => t.id === updated.id);
    if (idx >= 0) taskProjectTasks[idx] = updated;
    currentTaskDetail = { ...currentTaskDetail, ...updated };
    renderTaskBoard();
}

async function deleteCurrentTask() {
    if (!currentTaskDetail) return;
    if (!confirm('Удалить эту задачу?')) return;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}`, { method: 'DELETE' });
    if (!res || !res.ok) return showToast('Ошибка удаления задачи', 'error');
    taskProjectTasks = taskProjectTasks.filter(t => t.id !== currentTaskDetail.id);
    closeTaskDetail();
    renderTaskBoard();
    showToast('Задача удалена');
}

// ── Subtasks ────────────────────────────────────────────────
function renderTaskSubtasks(subtasks) {
    const list = document.getElementById('task-subtask-list');
    list.innerHTML = '';
    const done = subtasks.filter(s => s.completed).length;
    document.getElementById('task-subtask-progress').textContent = subtasks.length ? `(${done}/${subtasks.length})` : '';
    subtasks.forEach(s => {
        const item = document.createElement('div');
        item.className = 'task-subtask-item' + (s.completed ? ' completed' : '');
        item.innerHTML = `<input type="checkbox" ${s.completed ? 'checked' : ''} onchange="toggleSubtask('${s.id}', this.checked)">
            <span style="flex:1">${esc(s.title)}</span>
            <button class="btn-icon" onclick="deleteSubtask('${s.id}')" title="Удалить"><span class="material-icons-round" style="font-size:16px">close</span></button>`;
        list.appendChild(item);
    });
}

async function addSubtask() {
    const input = document.getElementById('task-subtask-input');
    const title = input.value.trim();
    if (!title || !currentTaskDetail) return;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/subtasks`, { method: 'POST', body: { title } });
    if (!res || !res.ok) return;
    const sub = await res.json();
    if (!currentTaskDetail.subtasks) currentTaskDetail.subtasks = [];
    currentTaskDetail.subtasks.push(sub);
    renderTaskSubtasks(currentTaskDetail.subtasks);
    input.value = '';
}

async function toggleSubtask(subId, completed) {
    if (!currentTaskDetail) return;
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/subtasks/${subId}`, { method: 'PUT', body: { completed } });
    const s = (currentTaskDetail.subtasks || []).find(x => x.id === subId);
    if (s) s.completed = completed ? 1 : 0;
    renderTaskSubtasks(currentTaskDetail.subtasks || []);
}

async function deleteSubtask(subId) {
    if (!currentTaskDetail) return;
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/subtasks/${subId}`, { method: 'DELETE' });
    currentTaskDetail.subtasks = (currentTaskDetail.subtasks || []).filter(s => s.id !== subId);
    renderTaskSubtasks(currentTaskDetail.subtasks);
}

// ── Comments ────────────────────────────────────────────────
function renderTaskComments(comments) {
    const list = document.getElementById('task-comment-list');
    list.innerHTML = '';
    comments.forEach(c => {
        const item = document.createElement('div');
        item.className = 'task-comment-item';
        const initial = (c.display_name || c.username || '?')[0].toUpperCase();
        const time = (c.created_at || '').slice(0, 16).replace('T', ' ');
        item.innerHTML = `<div class="task-comment-avatar">${initial}</div>
            <div class="task-comment-body"><span class="task-comment-name">${esc(c.display_name || c.username)}</span><span class="task-comment-time">${time}</span><div class="task-comment-text">${esc(c.text)}</div></div>`;
        list.appendChild(item);
    });
    list.scrollTop = list.scrollHeight;
}

async function addTaskComment() {
    const input = document.getElementById('task-comment-input');
    const text = input.value.trim();
    if (!text || !currentTaskDetail) return;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/comments`, { method: 'POST', body: { text } });
    if (!res || !res.ok) return;
    const comment = await res.json();
    if (!currentTaskDetail.comments) currentTaskDetail.comments = [];
    currentTaskDetail.comments.push(comment);
    renderTaskComments(currentTaskDetail.comments);
    input.value = '';
}

// ── Attachments ─────────────────────────────────────────────
function renderTaskAttachments(attachments) {
    const list = document.getElementById('task-attachment-list');
    list.innerHTML = '';
    (attachments || []).forEach(a => {
        const size = a.filesize > 1048576 ? (a.filesize / 1048576).toFixed(1) + ' MB' : (a.filesize / 1024).toFixed(0) + ' KB';
        const item = document.createElement('div');
        item.className = 'task-attachment-item';
        item.innerHTML = `<span class="material-icons-round" style="font-size:16px;color:var(--text3)">attach_file</span>
            <a href="${a.filepath}" target="_blank">${esc(a.filename)}</a>
            <span class="task-attachment-size">${size}</span>
            <button class="btn-icon" onclick="deleteTaskAttachment('${a.id}')" title="Удалить"><span class="material-icons-round" style="font-size:16px">close</span></button>`;
        list.appendChild(item);
    });
}

async function uploadTaskAttachment(files) {
    if (!files || !files.length || !currentTaskDetail) return;
    const formData = new FormData();
    formData.append('file', files[0]);
    const res = await fetch(API + `/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/attachments`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: formData
    });
    if (!res || !res.ok) return showToast('Ошибка загрузки файла', 'error');
    const att = await res.json();
    if (!currentTaskDetail.attachments) currentTaskDetail.attachments = [];
    currentTaskDetail.attachments.push(att);
    renderTaskAttachments(currentTaskDetail.attachments);
    document.getElementById('task-attach-input').value = '';
    showToast('Файл загружен');
}

async function deleteTaskAttachment(attId) {
    if (!currentTaskDetail) return;
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}/attachments/${attId}`, { method: 'DELETE' });
    currentTaskDetail.attachments = (currentTaskDetail.attachments || []).filter(a => a.id !== attId);
    renderTaskAttachments(currentTaskDetail.attachments);
}

// ── Labels in task detail ───────────────────────────────────
function renderTaskDetailLabels(labels) {
    const c = document.getElementById('task-detail-labels');
    c.innerHTML = '';
    taskProjectLabels.forEach(l => {
        const isSelected = labels.some(x => x.id === l.id);
        const chip = document.createElement('span');
        chip.className = 'task-label-chip' + (isSelected ? ' selected' : '');
        chip.style.background = l.color;
        chip.textContent = l.name;
        chip.onclick = async () => {
            chip.classList.toggle('selected');
            const ids = getSelectedLabelIds('task-detail-labels');
            await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/tasks/${currentTaskDetail.id}`, { method: 'PUT', body: { label_ids: ids } });
        };
        chip.dataset.labelId = l.id;
        c.appendChild(chip);
    });
}

// ── Project Settings ────────────────────────────────────────
async function showProjectSettingsDialog() {
    if (!currentTaskProject) return;
    // Ensure allUsers is loaded (may be empty if user opened tracker directly)
    if (!allUsers.length) {
        const usersRes = await apiFetch('/users');
        if (usersRes && usersRes.ok) allUsers = await usersRes.json();
    }
    document.getElementById('project-settings-name').value = currentTaskProject.name;
    document.getElementById('project-settings-desc').value = currentTaskProject.description || '';
    document.getElementById('project-settings-prefix').value = currentTaskProject.prefix || '';
    // Visibility toggle
    const visToggle = document.getElementById('project-settings-visibility');
    if (visToggle) visToggle.checked = currentTaskProject.visibility === 'public';
    // Show/hide visibility toggle (only owner can change)
    const visSection = document.getElementById('project-visibility-toggle');
    if (visSection) visSection.style.display = currentTaskProject.my_role === 'owner' ? '' : 'none';
    // Default assignee dropdown
    const daSelect = document.getElementById('project-settings-default-assignee');
    if (daSelect) {
        daSelect.innerHTML = '<option value="">Не назначен</option>';
        taskProjectMembers.forEach(m => {
            const sel = currentTaskProject.default_assignee === m.username ? 'selected' : '';
            daSelect.innerHTML += `<option value="${esc(m.username)}" ${sel}>${esc(m.display_name || m.username)}</option>`;
        });
    }
    // Render members
    const membersList = document.getElementById('project-members-list');
    membersList.innerHTML = '';
    const isOwner = currentTaskProject.my_role === 'owner';
    taskProjectMembers.forEach(m => {
        const row = document.createElement('div');
        row.className = 'project-member-row';
        const canManage = isOwner && m.username !== currentUser;
        const ROLE_RU = { owner: 'Владелец', lead: 'Руководитель', member: 'Участник', viewer: 'Наблюдатель' };
        const roleHtml = canManage
            ? `<select class="project-member-role-select" onchange="updateMemberRole('${esc(m.username)}', this.value)">
                <option value="lead" ${m.role==='lead'?'selected':''}>Руководитель</option>
                <option value="member" ${m.role==='member'?'selected':''}>Участник</option>
                <option value="viewer" ${m.role==='viewer'?'selected':''}>Наблюдатель</option>
               </select>`
            : `<span class="project-member-role">${ROLE_RU[m.role] || m.role}</span>`;
        row.innerHTML = `<span class="project-member-name">${esc(m.display_name || m.username)}</span>
            ${roleHtml}
            ${canManage ? `<button class="btn-icon" onclick="removeProjectMember('${m.username}')" title="Удалить"><span class="material-icons-round" style="font-size:16px">close</span></button>` : ''}`;
        membersList.appendChild(row);
    });
    // Populate add-member dropdown with all users not yet in project
    const addSelect = document.getElementById('project-add-member-select');
    addSelect.innerHTML = '<option value="">Выберите пользователя...</option>';
    const memberUsernames = taskProjectMembers.map(m => m.username);
    allUsers.forEach(u => {
        if (!memberUsernames.includes(u.username)) {
            addSelect.innerHTML += `<option value="${esc(u.username)}">${esc(u.display_name || u.username)}</option>`;
        }
    });
    // Render labels
    renderProjectLabels();
    // Render issue types
    renderProjectIssueTypes();
    // Render custom fields
    renderProjectCustomFields();
    // Show/hide field options based on type
    setupFieldTypeToggle();
    // Show/hide owner-only controls
    document.getElementById('project-delete-btn').style.display = isOwner ? '' : 'none';
    document.getElementById('project-settings-dialog').style.display = 'flex';
}
function closeProjectSettings() { document.getElementById('project-settings-dialog').style.display = 'none'; }

function setupFieldTypeToggle() {
    const typeSelect = document.getElementById('project-field-type');
    const optionsRow = document.getElementById('project-field-options-row');
    if (typeSelect && optionsRow) {
        typeSelect.onchange = () => {
            optionsRow.style.display = typeSelect.value === 'select' ? 'flex' : 'none';
        };
    }
}

function renderProjectIssueTypes() {
    const list = document.getElementById('project-issue-types-list');
    if (!list) return;
    list.innerHTML = '';
    taskProjectIssueTypes.forEach(it => {
        const item = document.createElement('div');
        item.className = 'project-issue-type-item';
        item.innerHTML = `<span style="color:${it.color}">${it.icon}</span>
            <span style="flex:1;font-size:13px">${esc(it.name)}</span>
            <button class="btn-icon" onclick="deleteIssueType('${it.id}')" title="Удалить"><span class="material-icons-round" style="font-size:16px">close</span></button>`;
        list.appendChild(item);
    });
}

async function createIssueType() {
    const name = document.getElementById('project-issue-type-name').value.trim();
    if (!name) return showToast('Введите название типа', 'error');
    const icon = document.getElementById('project-issue-type-icon').value.trim() || '📋';
    const color = document.getElementById('project-issue-type-color').value;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/issue-types`, {
        method: 'POST', body: { name, icon, color },
    });
    if (!res || !res.ok) return showToast('Ошибка создания типа', 'error');
    const it = await res.json();
    taskProjectIssueTypes.push(it);
    renderProjectIssueTypes();
    document.getElementById('project-issue-type-name').value = '';
    showToast('Тип заявки создан');
}

async function deleteIssueType(typeId) {
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/issue-types/${typeId}`, { method: 'DELETE' });
    taskProjectIssueTypes = taskProjectIssueTypes.filter(t => t.id !== typeId);
    renderProjectIssueTypes();
}

function renderProjectCustomFields() {
    const list = document.getElementById('project-custom-fields-list');
    if (!list) return;
    list.innerHTML = '';
    taskProjectCustomFields.forEach(f => {
        const item = document.createElement('div');
        item.className = 'project-custom-field-item';
        const reqBadge = f.required ? '<span class="cf-required">*</span>' : '';
        const typeBadge = `<span class="cf-type-badge">${f.field_type}</span>`;
        item.innerHTML = `<span style="flex:1;font-size:13px">${esc(f.name)}${reqBadge}</span>
            ${typeBadge}
            <button class="btn-icon" onclick="deleteCustomField('${f.id}')" title="Удалить"><span class="material-icons-round" style="font-size:16px">close</span></button>`;
        list.appendChild(item);
    });
}

async function createCustomField() {
    const name = document.getElementById('project-field-name').value.trim();
    if (!name) return showToast('Введите название поля', 'error');
    const fieldType = document.getElementById('project-field-type').value;
    const required = document.getElementById('project-field-required').checked;
    let options = [];
    if (fieldType === 'select') {
        const optStr = document.getElementById('project-field-options').value.trim();
        options = optStr ? optStr.split(',').map(s => s.trim()).filter(Boolean) : [];
    }
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/fields`, {
        method: 'POST', body: { name, field_type: fieldType, required, options },
    });
    if (!res || !res.ok) return showToast('Ошибка создания поля', 'error');
    const field = await res.json();
    taskProjectCustomFields.push(field);
    renderProjectCustomFields();
    document.getElementById('project-field-name').value = '';
    document.getElementById('project-field-required').checked = false;
    document.getElementById('project-field-options').value = '';
    showToast('Поле создано');
}

async function deleteCustomField(fieldId) {
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/fields/${fieldId}`, { method: 'DELETE' });
    taskProjectCustomFields = taskProjectCustomFields.filter(f => f.id !== fieldId);
    renderProjectCustomFields();
}

function renderProjectLabels() {
    const list = document.getElementById('project-labels-list');
    list.innerHTML = '';
    taskProjectLabels.forEach(l => {
        const item = document.createElement('span');
        item.className = 'project-label-item';
        item.style.background = l.color;
        item.innerHTML = `${esc(l.name)} <button class="btn-icon" onclick="deleteProjectLabel('${l.id}')"><span class="material-icons-round">close</span></button>`;
        list.appendChild(item);
    });
}

async function saveProjectSettings() {
    const name = document.getElementById('project-settings-name').value.trim();
    const desc = document.getElementById('project-settings-desc').value.trim();
    if (!name) return showToast('Введите название проекта', 'error');
    const prefix = document.getElementById('project-settings-prefix').value.trim().toUpperCase();
    const visCheck = document.getElementById('project-settings-visibility');
    const visibility = visCheck && visCheck.checked ? 'public' : 'private';
    const daElem = document.getElementById('project-settings-default-assignee');
    const default_assignee = daElem ? daElem.value : '';
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}`, { method: 'PUT', body: { name, description: desc, visibility, default_assignee, prefix } });
    if (!res || !res.ok) return showToast('Ошибка сохранения проекта', 'error');
    currentTaskProject.name = name;
    currentTaskProject.description = desc;
    currentTaskProject.visibility = visibility;
    currentTaskProject.default_assignee = default_assignee;
    currentTaskProject.prefix = prefix;
    document.getElementById('tasks-board-title').textContent = name;
    document.getElementById('tasks-board-desc').textContent = desc;
    closeProjectSettings();
    showToast('Проект обновлён');
}

async function deleteCurrentProject() {
    if (!currentTaskProject) return;
    if (!confirm('Удалить проект и все его задачи?')) return;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}`, { method: 'DELETE' });
    if (!res || !res.ok) return showToast('Ошибка удаления проекта', 'error');
    closeProjectSettings();
    tasksShowProjectList();
    await loadTaskProjects();
    showToast('Проект удалён');
}

async function addProjectMember() {
    const username = document.getElementById('project-add-member-select').value;
    const role = document.getElementById('project-add-member-role').value;
    if (!username) return showToast('Выберите пользователя', 'error');
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/members`, { method: 'POST', body: { username, role } });
    if (!res || !res.ok) return showToast('Ошибка добавления участника', 'error');
    showToast('Участник добавлен');
    // Reload project
    await openTaskProject(currentTaskProjectSlug);
    await showProjectSettingsDialog();
}

async function removeProjectMember(username) {
    if (!confirm(`Удалить ${username} из проекта?`)) return;
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/members/${username}`, { method: 'DELETE' });
    if (!res || !res.ok) return showToast('Ошибка удаления участника', 'error');
    showToast('Участник удалён');
    await openTaskProject(currentTaskProjectSlug);
    await showProjectSettingsDialog();
}

async function updateMemberRole(username, role) {
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/members/${username}`, {
        method: 'PUT', body: { role }
    });
    if (!res || !res.ok) return showToast('Ошибка изменения роли', 'error');
    showToast('Роль обновлена');
    await openTaskProject(currentTaskProjectSlug);
    await showProjectSettingsDialog();
}

// ── Submit Ticket (public page) ────────────────────────────

async function openSubmitTicketPage(slug) {
    pushRoute('/tracker/' + encodeURIComponent(slug) + '/submit');
    document.getElementById('tasks-project-list').style.display = 'none';
    document.getElementById('tasks-board-view').style.display = 'none';
    document.getElementById('tasks-submit-view').style.display = 'flex';

    const res = await apiFetch(`/tasks/projects/${slug}/submit-info`);
    if (!res || !res.ok) {
        showToast('Проект не найден или не является публичным', 'error');
        document.getElementById('tasks-submit-view').style.display = 'none';
        document.getElementById('tasks-project-list').style.display = 'flex';
        return;
    }
    submitProjectData = await res.json();

    document.getElementById('submit-project-name').textContent = submitProjectData.name;
    document.getElementById('submit-project-desc').textContent = submitProjectData.description || '';

    const container = document.getElementById('submit-subprojects');
    const noSub = document.getElementById('submit-no-subprojects');
    const children = submitProjectData.children || [];

    if (children.length === 0) {
        container.style.display = 'none';
        noSub.style.display = 'flex';
        document.querySelector('.tasks-submit-hint').style.display = 'none';
        return;
    }

    noSub.style.display = 'none';
    document.querySelector('.tasks-submit-hint').style.display = 'flex';
    container.style.display = 'flex';
    container.innerHTML = '';

    children.forEach(c => {
        const card = document.createElement('div');
        card.className = 'subproject-card submit-subproject-card';
        card.onclick = () => openSubmitTicketDialog(c);
        card.innerHTML = `<span class="material-icons-round" style="font-size:24px;color:var(--accent)">account_tree</span>
            <div class="submit-subproject-info">
                <span class="submit-subproject-name">${esc(c.name)}</span>
                <span class="submit-subproject-desc">${esc(c.description || '')}</span>
            </div>
            <span class="submit-subproject-count">${c.task_count || 0} задач</span>`;
        container.appendChild(card);
    });
}

function openSubmitTicketDialog(target) {
    submitTicketSlug = target.slug;
    document.getElementById('submit-ticket-target-name').textContent = target.name;
    document.getElementById('submit-ticket-title').value = '';
    document.getElementById('submit-ticket-desc').value = '';
    document.getElementById('submit-ticket-priority').value = 'medium';
    document.getElementById('submit-ticket-dialog').style.display = 'flex';
    document.getElementById('submit-ticket-title').focus();
}

function closeSubmitTicketDialog() {
    document.getElementById('submit-ticket-dialog').style.display = 'none';
    submitTicketSlug = null;
}

async function submitTicket() {
    const title = document.getElementById('submit-ticket-title').value.trim();
    if (!title) return showToast('Введите название тикета', 'error');

    const body = {
        title,
        description: document.getElementById('submit-ticket-desc').value.trim(),
        priority: document.getElementById('submit-ticket-priority').value,
    };

    try {
        const res = await apiFetch(`/tasks/projects/${submitTicketSlug}/submit-ticket`, {
            method: 'POST', body,
        });
        if (!res || !res.ok) {
            const err = await res?.json().catch(() => ({}));
            return showToast(err.detail || 'Ошибка отправки тикета', 'error');
        }
        closeSubmitTicketDialog();
        showToast('Тикет отправлен');
    } catch (e) {
        showToast('Ошибка отправки тикета', 'error');
    }
}

async function createProjectLabel() {
    const name = document.getElementById('project-label-name').value.trim();
    const color = document.getElementById('project-label-color').value;
    if (!name) return showToast('Введите название метки', 'error');
    const res = await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/labels`, { method: 'POST', body: { name, color } });
    if (!res || !res.ok) return showToast('Ошибка создания метки', 'error');
    const label = await res.json();
    taskProjectLabels.push(label);
    renderProjectLabels();
    document.getElementById('project-label-name').value = '';
    showToast('Метка создана');
}

async function deleteProjectLabel(labelId) {
    await apiFetch(`/tasks/projects/${currentTaskProjectSlug}/labels/${labelId}`, { method: 'DELETE' });
    taskProjectLabels = taskProjectLabels.filter(l => l.id !== labelId);
    renderProjectLabels();
}

// ── WS Event Handler ────────────────────────────────────────
function handleTaskWsEvent(data) {
    // Events that affect the project list (main screen)
    switch (data.event) {
        case 'project_created':
        case 'project_member_added':
        case 'project_member_removed':
            // Reload project list if on main screen
            if (!currentTaskProject) loadTaskProjects();
            return;
        case 'project_deleted':
            if (currentTaskProject && currentTaskProject.id === data.project_id) {
                tasksShowProjectList();
            }
            loadTaskProjects();
            return;
    }

    // Events that affect the current project board
    if (!currentTaskProject || currentTaskProject.id !== data.project_id) return;
    switch (data.event) {
        case 'task_created':
            if (!taskProjectTasks.find(t => t.id === data.task.id)) taskProjectTasks.push(data.task);
            renderTaskBoard();
            break;
        case 'task_updated': {
            const idx = taskProjectTasks.findIndex(t => t.id === data.task.id);
            if (idx >= 0) taskProjectTasks[idx] = data.task;
            renderTaskBoard();
            if (currentTaskDetail && currentTaskDetail.id === data.task.id) openTaskDetail(data.task.id);
            break;
        }
        case 'task_deleted':
            taskProjectTasks = taskProjectTasks.filter(t => t.id !== data.task_id);
            renderTaskBoard();
            if (currentTaskDetail && currentTaskDetail.id === data.task_id) closeTaskDetail();
            break;
        case 'task_status_changed': {
            const t = taskProjectTasks.find(t => t.id === data.task_id);
            if (t) t.status = data.new_status;
            renderTaskBoard();
            break;
        }
        case 'subtask_updated':
        case 'task_comment_added':
            if (currentTaskDetail && currentTaskDetail.id === data.task_id) openTaskDetail(data.task_id);
            break;
        case 'project_updated':
            if (data.project) {
                Object.assign(currentTaskProject, data.project);
                const titleEl = document.getElementById('tasks-board-title');
                if (titleEl) titleEl.textContent = data.project.name || currentTaskProject.name;
            }
            loadTaskProjects(); // Also refresh project list for table stats
            break;
    }
}
