// Chronovault UI Configuration
// Edit this file to adapt the UI to your environment

window.CHRONOVAULT_UI_CONFIG = {
  // API base URL - empty string means same-origin (recommended for hostable UI)
  // If set, must include http:// or https://
  // Example: "" (same-origin) or "http://192.168.1.1:8787" or "https://your-subdomain.duckdns.org:8787"
  API_BASE_URL: "",
  
  // UI title displayed in header
  UI_TITLE: "Chronovault",
  
  // Normal polling interval in milliseconds (15-30 seconds recommended)
  POLL_INTERVAL_MS: 20000,
  
  // Faster polling after actions in milliseconds (5 seconds recommended)
  FAST_POLL_MS: 5000,
  
  // Remember token in sessionStorage (default: false for security)
  REMEMBER_TOKEN: false,
  
  // Enable restore UI (set to true when backend restore endpoint is ready)
  ENABLE_RESTORE_UI: true
};
