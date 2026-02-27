// Renderer process script
document.addEventListener('DOMContentLoaded', async () => {
  const loading = document.getElementById('loading');
  const connectionInfo = document.getElementById('connection-info');

  // Simulate connection check
  setTimeout(() => {
    loading.style.display = 'none';
    connectionInfo.style.display = 'block';
  }, 2000);

  // Get config from main process if available
  if (window.electronAPI) {
    try {
      const config = await window.electronAPI.getConfig();
      console.log('App Config:', config);
    } catch (e) {
      console.log('Running in browser mode');
    }
  }
});

// Handle navigation
function navigateTo(path) {
  window.location.href = path;
}
