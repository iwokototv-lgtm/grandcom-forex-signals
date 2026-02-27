const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to renderer
contextBridge.exposeInMainWorld('electronAPI', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  showNotification: (title, body) => ipcRenderer.invoke('show-notification', { title, body }),
  platform: process.platform,
  version: process.versions.electron
});
