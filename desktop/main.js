// Nebenan Desktop — wraps the exact web frontend (frontend/) in an Electron shell.
//
// The frontend is served over a fixed loopback port so the renderer has a
// stable http origin. A stable origin matters for two reasons:
//   1. localStorage (saved server address + auth tokens) persists across launches.
//   2. the service worker registers (it needs a secure/localhost context).
// The user enters the server address on first launch (same two-step login as web);
// all API / WebSocket / media requests then go to that server cross-origin,
// which the backend allows via its CORS middleware.

const { app, BrowserWindow, shell, Menu } = require('electron');
const path = require('path');
const http = require('http');
const fs = require('fs');
const { URL } = require('url');

// Candidate fixed ports — the first free one is used. Keeping it stable
// preserves the renderer origin (and therefore localStorage) between runs.
const PORT_CANDIDATES = [17673, 17674, 17675, 17676];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.wav': 'audio/wav',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

function frontendDir() {
  // Packaged: resources/frontend (see extraResources). Dev: ../frontend.
  const packaged = path.join(process.resourcesPath || '', 'frontend');
  if (fs.existsSync(path.join(packaged, 'index.html'))) return packaged;
  return path.join(__dirname, '..', 'frontend');
}

function startServer() {
  const root = path.resolve(frontendDir());

  const server = http.createServer((req, res) => {
    let pathname;
    try {
      pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    } catch {
      res.writeHead(400);
      res.end('Bad request');
      return;
    }
    if (pathname === '/') pathname = '/index.html';

    const filePath = path.resolve(path.join(root, pathname));
    // Block path traversal outside the frontend root.
    if (filePath !== root && !filePath.startsWith(root + path.sep)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }
      const ext = path.extname(filePath).toLowerCase();
      res.writeHead(200, {
        'Content-Type': MIME[ext] || 'application/octet-stream',
        'Cache-Control': 'no-cache',
      });
      res.end(data);
    });
  });

  // Try each candidate port until one binds.
  return new Promise((resolve, reject) => {
    let i = 0;
    const tryNext = () => {
      if (i >= PORT_CANDIDATES.length) {
        reject(new Error('No free loopback port available'));
        return;
      }
      const port = PORT_CANDIDATES[i++];
      server.once('error', (e) => {
        if (e.code === 'EADDRINUSE') tryNext();
        else reject(e);
      });
      server.listen(port, '127.0.0.1', () => resolve(port));
    };
    tryNext();
  });
}

async function createWindow() {
  let port;
  try {
    port = await startServer();
  } catch (e) {
    // Fall back to loading the file directly if the server can't start.
    console.error('Static server failed:', e);
  }

  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 760,
    minHeight: 560,
    title: 'Nebenan',
    backgroundColor: '#33302c',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  Menu.setApplicationMenu(null);

  // External links (attachments, http links) open in the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  if (port) {
    win.loadURL(`http://127.0.0.1:${port}/`);
  } else {
    win.loadFile(path.join(frontendDir(), 'index.html'));
  }
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
