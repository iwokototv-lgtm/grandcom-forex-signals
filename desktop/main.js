const { app, BrowserWindow, Menu, Tray, shell, ipcMain, Notification } = require('electron');
const path = require('path');

// Keep a global reference to prevent garbage collection
let mainWindow;
let tray = null;

// App configuration
const APP_CONFIG = {
  name: 'Grandcom Forex Signals Pro',
  width: 1200,
  height: 800,
  minWidth: 400,
  minHeight: 600,
  // Backend API URL - Update this when deployed
  apiUrl: process.env.API_URL || 'https://grandcom-alerts.preview.emergentagent.com',
  // Web app URL
  webUrl: process.env.WEB_URL || 'https://grandcom-alerts.preview.emergentagent.com'
};

function createWindow() {
  // Create the browser window
  mainWindow = new BrowserWindow({
    width: APP_CONFIG.width,
    height: APP_CONFIG.height,
    minWidth: APP_CONFIG.minWidth,
    minHeight: APP_CONFIG.minHeight,
    title: APP_CONFIG.name,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    show: false, // Don't show until ready
    backgroundColor: '#1a1a2e'
  });

  // Load the web app or local HTML
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadFile('index.html');
  } else {
    mainWindow.loadURL(APP_CONFIG.webUrl);
  }

  // Show window when ready
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Handle window close - minimize to tray instead
  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
    return false;
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Create application menu
  createMenu();
}

function createMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Refresh',
          accelerator: 'CmdOrCtrl+R',
          click: () => mainWindow.reload()
        },
        { type: 'separator' },
        {
          label: 'Exit',
          accelerator: 'CmdOrCtrl+Q',
          click: () => {
            app.isQuitting = true;
            app.quit();
          }
        }
      ]
    },
    {
      label: 'View',
      submenu: [
        {
          label: 'Zoom In',
          accelerator: 'CmdOrCtrl+Plus',
          click: () => {
            const currentZoom = mainWindow.webContents.getZoomFactor();
            mainWindow.webContents.setZoomFactor(currentZoom + 0.1);
          }
        },
        {
          label: 'Zoom Out',
          accelerator: 'CmdOrCtrl+-',
          click: () => {
            const currentZoom = mainWindow.webContents.getZoomFactor();
            mainWindow.webContents.setZoomFactor(Math.max(0.5, currentZoom - 0.1));
          }
        },
        {
          label: 'Reset Zoom',
          accelerator: 'CmdOrCtrl+0',
          click: () => mainWindow.webContents.setZoomFactor(1)
        },
        { type: 'separator' },
        {
          label: 'Toggle Full Screen',
          accelerator: 'F11',
          click: () => mainWindow.setFullScreen(!mainWindow.isFullScreen())
        },
        { type: 'separator' },
        {
          label: 'Developer Tools',
          accelerator: 'CmdOrCtrl+Shift+I',
          click: () => mainWindow.webContents.toggleDevTools()
        }
      ]
    },
    {
      label: 'Signals',
      submenu: [
        {
          label: 'View All Signals',
          click: () => mainWindow.loadURL(`${APP_CONFIG.webUrl}/signals`)
        },
        {
          label: 'Analytics',
          click: () => mainWindow.loadURL(`${APP_CONFIG.webUrl}/analytics`)
        },
        { type: 'separator' },
        {
          label: 'Open Telegram Channel',
          click: () => shell.openExternal('https://t.me/grandcomsignals')
        }
      ]
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About',
          click: () => {
            const { dialog } = require('electron');
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: 'About Grandcom Forex Signals Pro',
              message: 'Grandcom Forex Signals Pro',
              detail: 'Version 1.0.0\n\nProfessional AI-Powered Trading Signals\n\n© 2025 Grandcom Trading'
            });
          }
        },
        {
          label: 'Help & Support',
          click: () => mainWindow.loadURL(`${APP_CONFIG.webUrl}/help`)
        },
        { type: 'separator' },
        {
          label: 'Privacy Policy',
          click: () => mainWindow.loadURL(`${APP_CONFIG.webUrl}/privacy`)
        },
        {
          label: 'Terms of Service',
          click: () => mainWindow.loadURL(`${APP_CONFIG.webUrl}/terms`)
        }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

function createTray() {
  // Create tray icon (use a placeholder if icon doesn't exist)
  const iconPath = path.join(__dirname, 'assets', 'icon.png');
  
  try {
    tray = new Tray(iconPath);
  } catch (e) {
    // If icon fails, skip tray
    console.log('Tray icon not available');
    return;
  }

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show App',
      click: () => {
        mainWindow.show();
      }
    },
    {
      label: 'Open Telegram',
      click: () => shell.openExternal('https://t.me/grandcomsignals')
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.isQuitting = true;
        app.quit();
      }
    }
  ]);

  tray.setToolTip('Grandcom Forex Signals Pro');
  tray.setContextMenu(contextMenu);

  tray.on('double-click', () => {
    mainWindow.show();
  });
}

// App Events
app.whenReady().then(() => {
  createWindow();
  createTray();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  app.isQuitting = true;
});

// IPC handlers for renderer process
ipcMain.handle('get-config', () => {
  return APP_CONFIG;
});

ipcMain.handle('show-notification', (event, { title, body }) => {
  new Notification({ title, body }).show();
});
