# 📱 Build Android APK - Grandcom Forex Signals Pro

## Quick Start (5 Steps)

### Step 1: Create Free Expo Account
1. Go to https://expo.dev/signup
2. Sign up with email or GitHub
3. Verify your email

### Step 2: Install EAS CLI (on your computer)
```bash
npm install -g eas-cli
```

### Step 3: Login to Expo
```bash
eas login
```
Enter your Expo credentials

### Step 4: Download the Project
- Use the "Download Code" button in Emergent
- Extract the ZIP file
- Open terminal in the `frontend` folder

### Step 5: Build APK
```bash
cd frontend
eas build -p android --profile preview
```

**First time only:** It will ask to create a new project - say YES

⏱️ **Build time:** ~15-20 minutes

### Step 6: Download APK
- When build completes, you'll get a download link
- Or go to https://expo.dev → Your Projects → Builds → Download

---

## Install on Android Phone

### Option A: Direct Download
1. Open the download link on your Android phone
2. Tap "Download"
3. Open the APK file
4. Tap "Install" (may need to enable "Install from unknown sources")

### Option B: Transfer from Computer
1. Download APK to computer
2. Connect phone via USB
3. Copy APK to phone
4. Open file manager on phone
5. Find and install the APK

---

## Configuration Files Created

### eas.json
```json
{
  "build": {
    "preview": {
      "distribution": "internal",
      "android": {
        "buildType": "apk"
      }
    },
    "production": {
      "android": {
        "buildType": "apk"
      }
    }
  }
}
```

### Build Profiles
- **preview**: For testing (creates APK)
- **production**: For release (creates APK)

---

## Important Notes

### Backend URL
The app connects to: `https://gold-signal-debug.preview.emergentagent.com`

After deploying your backend to production, update `frontend/.env`:
```
EXPO_PUBLIC_BACKEND_URL=https://your-production-url.com
```

### App Signing
- EAS automatically handles app signing
- Keystore is stored securely in Expo's cloud
- Same keystore used for all future builds

### Update the App
To release a new version:
1. Update version in `app.json`
2. Run `eas build -p android --profile preview`
3. Distribute new APK to users

---

## Troubleshooting

### "eas: command not found"
```bash
npm install -g eas-cli
```

### Build fails with dependency error
```bash
cd frontend
rm -rf node_modules
npm install
eas build -p android --profile preview
```

### App crashes on launch
- Check that backend URL is correct and accessible
- Verify the backend is deployed and running

---

## Cost Summary
- ✅ Expo account: FREE
- ✅ EAS Build (30/month): FREE
- ✅ APK distribution: FREE
- ❌ Google Play Store: $25 one-time (optional)
