# Grandcom Forex Signals Pro - Desktop App Build Guide

## Overview
This Electron app wraps the Grandcom Forex Signals web application into a native desktop experience for Windows, macOS, and Linux.

## Build Status (March 2026)
- **Linux AppImage**: ✅ Built (`dist/Grandcom Forex Signals Pro-1.0.0-arm64.AppImage`)
- **Windows Portable**: ✅ Built (`dist/Grandcom-Forex-Signals-Pro-Windows-Portable.zip`)
- **Windows Installer**: ⚠️ Requires Windows build environment
- **macOS**: Requires macOS for building

## Quick Distribution

### Windows Users
1. Download `Grandcom-Forex-Signals-Pro-Windows-Portable.zip`
2. Extract to any folder
3. Run `Grandcom Forex Signals Pro.exe`

### Linux Users
1. Download `Grandcom Forex Signals Pro-1.0.0-arm64.AppImage`
2. Make executable: `chmod +x *.AppImage`
3. Run the AppImage

## Prerequisites

1. **Node.js** (v18 or higher)
2. **npm** or **yarn**
3. **For Windows builds**: Windows 10/11 or Wine on Linux/macOS
4. **For macOS builds**: macOS with Xcode Command Line Tools
5. **For Linux builds**: Linux with required build tools

## Quick Start (Development)

```bash
cd /app/desktop
npm install
npm start
```

This will launch the app in development mode pointing to the web app.

## Building for Production

### Windows (creates .exe installer)
```bash
npm run build:win
```
Output: `dist/Grandcom Forex Signals Pro Setup.exe`

### macOS (creates .dmg)
```bash
npm run build:mac
```
Output: `dist/Grandcom Forex Signals Pro.dmg`

### Linux (creates .AppImage)
```bash
npm run build:linux
```
Output: `dist/Grandcom Forex Signals Pro.AppImage`

### Build All Platforms
```bash
npm run build
```

## App Icons

Before building, you should add proper icons:

1. **Windows**: `assets/icon.ico` (256x256 minimum)
2. **macOS**: `assets/icon.icns`
3. **Linux/General**: `assets/icon.png` (512x512 recommended)

### Creating Icons from SVG

You can convert the included `assets/icon.svg` to other formats:

```bash
# Using ImageMagick
convert assets/icon.svg -resize 512x512 assets/icon.png
convert assets/icon.svg -resize 256x256 assets/icon.ico

# For macOS .icns, use iconutil or online converters
```

## Configuration

### Changing the Web App URL

Edit `main.js` and update the `APP_CONFIG` object:

```javascript
const APP_CONFIG = {
  webUrl: 'https://your-deployed-app-url.com',
  apiUrl: 'https://your-api-url.com'
};
```

### Environment Variables

You can also set URLs via environment variables:
- `WEB_URL` - The web application URL
- `API_URL` - The backend API URL

## Features

- **System Tray**: App minimizes to system tray instead of closing
- **Menu Bar**: Custom menu with quick access to features
- **Notifications**: Desktop notifications for new signals (when implemented)
- **Zoom Controls**: Ctrl/Cmd + Plus/Minus to zoom
- **Full Screen**: F11 to toggle full screen
- **Developer Tools**: Ctrl/Cmd + Shift + I

## Customization

### Adding Auto-Updates

Install electron-updater:
```bash
npm install electron-updater
```

Then add to `main.js`:
```javascript
const { autoUpdater } = require('electron-updater');
app.on('ready', () => {
  autoUpdater.checkForUpdatesAndNotify();
});
```

### Adding Signal Notifications

The app includes a notification handler. To use it from the renderer:
```javascript
window.electronAPI.showNotification('New Signal!', 'XAUUSD BUY at 3050.00');
```

## Distribution

### Code Signing (Recommended for Production)

For Windows, you need a code signing certificate. Update `package.json`:
```json
"build": {
  "win": {
    "certificateFile": "path/to/cert.pfx",
    "certificatePassword": "your-password"
  }
}
```

For macOS, you need an Apple Developer certificate and notarization.

## Troubleshooting

### App shows blank screen
- Check if the web URL is accessible
- Open Developer Tools (Ctrl+Shift+I) to see errors

### Build fails on Windows
- Ensure you have Visual Studio Build Tools installed
- Try running `npm cache clean --force` and reinstall

### Icons not showing
- Ensure icon files are in the correct format
- Windows: .ico must be 256x256 or larger
- macOS: .icns must be properly formatted

## File Structure

```
desktop/
├── main.js           # Main Electron process
├── preload.js        # Preload script for IPC
├── index.html        # Local fallback HTML
├── renderer.js       # Renderer process script
├── styles.css        # Local styles
├── package.json      # App configuration
├── assets/
│   ├── icon.svg      # Source icon
│   ├── icon.png      # Linux/general icon
│   ├── icon.ico      # Windows icon
│   └── icon.icns     # macOS icon
└── dist/             # Build output (created after build)
```

## Support

For issues with the desktop app, contact: grandcomfx@gmail.com
