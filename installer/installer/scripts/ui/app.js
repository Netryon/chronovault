// Chronovault UI Application Logic
// Version: 2024-01 - Updated endpoint to /action/run-backup-now

// Token normalization helper - always returns raw, trimmed token
function normalizeToken(t) {
    if (typeof t !== "string") return "";
    return t.trim();
}

// Bootstrap token from URL parameter ?t= or ?token=
function bootstrapToken() {
    // URLSearchParams.get() converts + to space, so we manually parse the query string
    const rawQuery = window.location.search;
    let token = "";
    
    const tMatch = rawQuery.match(/[?&]t=([^&]*)/);
    const tokenMatch = rawQuery.match(/[?&]token=([^&]*)/);
    const match = tMatch || tokenMatch;
    
    if (match && match[1]) {
        const rawValue = match[1];
        try {
            token = decodeURIComponent(rawValue);
        } catch (e) {
            token = rawValue;
        }
        
        if (token.includes(" ")) {
            console.warn("Bootstrap: Token contains spaces. If your token has '+' characters, encode them as '%2B' in the URL.");
            console.warn("Bootstrap: Example URL: ?t=wsW5pktw2n%2FduR%2FR0Vq8VnoHUL%2BFyepSJ3vtXvoxgM0%3D");
            console.warn("Bootstrap: The '+' must be encoded as '%2B', not left as '+'");
        }
    }

    if (!token) return;

    token = normalizeToken(token);

    if (token.includes("%2F") || token.includes("%3D") || token.includes("%2B")) {
        console.error("Bootstrap: Token looks URL-encoded. The token in the URL should be the RAW token, not encoded.");
        const url = new URL(window.location.href);
        url.searchParams.delete("t");
        url.searchParams.delete("token");
        window.history.replaceState({}, document.title, url.toString());
        return;
    }

    if (token.length < 20) {
        console.error("Bootstrap: Token too short. Expected full token from control.env.");
        const url = new URL(window.location.href);
        url.searchParams.delete("t");
        url.searchParams.delete("token");
        window.history.replaceState({}, document.title, url.toString());
        return;
    }

    localStorage.setItem("chronovault_token", token);

    const url = new URL(window.location.href);
    url.searchParams.delete("t");
    url.searchParams.delete("token");
    window.history.replaceState({}, document.title, url.toString());

    const el = document.getElementById("token-input");
    if (el) el.value = token;
}

// Helper function to get token from localStorage (raw, normalized)
function getToken() {
    return normalizeToken(localStorage.getItem("chronovault_token") || "");
}

// -------------------------
// API base URL handling
// -------------------------
// Get API base URL (empty means same-origin)
function getApiBaseUrl() {
    const cfg = window.CHRONOVAULT_UI_CONFIG || {};
    const raw = (cfg.API_BASE_URL || "").trim();
    return raw.replace(/\/+$/, ""); // remove trailing slashes
}

// Build full request URL for an endpoint
function buildApiUrl(endpointPath) {
    const base = getApiBaseUrl();
    if (!endpointPath.startsWith("/")) endpointPath = "/" + endpointPath;

    // Empty base => same-origin
    if (!base) return endpointPath;

    return base + endpointPath;
}

// Helper function that URL-encodes the token
// URLSearchParams does not encode '+' characters properly, so we use encodeURIComponent
function withToken(url) {
    const raw = normalizeToken(getToken());
    if (!raw) return url;

    // Build full URL first (handles relative URLs)
    const fullUrl = url.startsWith('/') 
        ? new URL(url, window.location.origin).toString()
        : url;
    
    const separator = fullUrl.includes('?') ? '&' : '?';
    const encodedToken = encodeURIComponent(raw);
    return `${fullUrl}${separator}t=${encodedToken}`;
}

class ChronovaultUI {
    constructor() {
        this.config = window.CHRONOVAULT_UI_CONFIG || {};
        this.token = '';
        this.pollInterval = null;
        this.fastPollTimeout = null;
        this.isFastPolling = false;
        this.lastStatus = null;
        this.isConnected = false;
        this.healthCheckTimeout = null;
        this.consecutiveFailures = 0;
        this.maxFailures = 3; // Mark disconnected after 3 consecutive failures
        this.lastRestorePointsFetch = 0; // Track last restore points fetch time to throttle
        this.backupInitiated = false; // Track if we just initiated a backup (waiting for backend to confirm)
        
        this.init();
    }

    init() {
        // Bootstrap token from URL first (before any API calls)
        bootstrapToken();

        // Validate config is loaded
        if (!this.config) {
            const errorMsg = '❌ CRITICAL: ui-config.js missing!\n\nFix:\n1) Ensure ui-config.js exists and is accessible\n2) Reload page';
            console.error('=== CONFIG MISSING ===');
            console.error(errorMsg);
            this.showError(errorMsg);
            // Show in UI
            const statusReason = document.getElementById('status-reason');
            if (statusReason) {
                statusReason.innerHTML = '<span style="color: #e74c3c; font-weight: bold;">⚠️ Config missing! Check ui-config.js</span>';
            }
        } else {
            // API_BASE_URL can be empty (same-origin) or set to a specific URL
            const apiBaseUrl = getApiBaseUrl();
            if (apiBaseUrl) {
                // Validate URL format if provided
                try {
                    new URL(apiBaseUrl);
                } catch (e) {
                    const errorMsg = `ERROR: Invalid API_BASE_URL format: "${apiBaseUrl}". Must be a valid URL with protocol (http:// or https://) and host (e.g., "http://<host>:<port>" or "https://<host>:<port>"), or empty string for same-origin`;
                    console.error(errorMsg);
                    this.showError(errorMsg);
                }
            }
        }

        // Check for file:// origin
        if (window.location.protocol === 'file:') {
            const errorMsg = '❌ CRITICAL: UI opened as file://. CORS will block all API requests!\n\nFix: Serve UI over HTTP:\n  cd <ui-directory>\n  python3 -m http.server <port>\nThen open: http://<server-ip>:<port>/';
            console.error('=== FILE:// ORIGIN DETECTED ===');
            console.error(errorMsg);
            this.showError(errorMsg);
            
            // Show in status banner too
            const statusReason = document.getElementById('status-reason');
            if (statusReason) {
                statusReason.innerHTML = '<span style="color: #e74c3c; font-weight: bold;">⚠️ UI opened as file:// - CORS will fail! Serve over HTTP.</span>';
            }
        }

        // Check for mixed content (HTTPS UI → HTTP API)
        const apiBase = getApiBaseUrl();
        if (window.location.protocol === 'https:' && apiBase && apiBase.startsWith('http:')) {
            const errorMsg = '❌ CRITICAL: Mixed content blocked!\n\nUI: HTTPS | API: HTTP\nBrowser blocks HTTPS→HTTP requests.\n\nFix options:\n1) Serve UI over HTTP\n2) Put API behind HTTPS (reverse proxy)\n3) Proxy API through same origin';
            console.error('=== MIXED CONTENT DETECTED ===');
            console.error(errorMsg);
            this.showError(errorMsg);
            
            // Show in status banner too
            const statusReason = document.getElementById('status-reason');
            if (statusReason) {
                statusReason.innerHTML = '<span style="color: #e74c3c; font-weight: bold;">⚠️ Mixed content: HTTPS UI cannot call HTTP API!</span>';
            }
        }

        // Set UI title from config (for page title, logo alt text)
        if (this.config.UI_TITLE) {
            document.title = this.config.UI_TITLE;
            const logoEl = document.getElementById('ui-logo');
            if (logoEl) {
                logoEl.alt = this.config.UI_TITLE;
            }
        }

        // Load token using unified getToken() function
        // This will check input, then storage based on REMEMBER_TOKEN config
        // bootstrapToken() already ran above, so token should be in localStorage if it came from URL
        this.token = this.getToken();
        if (this.token) {
            const tokenInput = document.getElementById('token-input');
            if (tokenInput) {
                tokenInput.value = this.token;
            }
            // Auto-check remember checkbox if token is in localStorage
            const rememberCheckbox = document.getElementById('remember-token-checkbox');
            if (rememberCheckbox && localStorage.getItem('chronovault_token')) {
                rememberCheckbox.checked = true;
            }
            // Token loaded (debug removed)
        } else {
            // No token found (debug removed)
        }

        // Set remember token checkbox
        const rememberCheckbox = document.getElementById('remember-token-checkbox');
        if (rememberCheckbox) {
            rememberCheckbox.checked = this.config.REMEMBER_TOKEN || false;
        }

        // Event listeners
        this.setupEventListeners();

        // Initial health check (only if we have a token)
        if (this.token) {
            this.checkHealth();
        } else {
            this.updateButtonStates();
        }
    }

    setupEventListeners() {
        // Token input
        const tokenInput = document.getElementById('token-input');
        tokenInput.addEventListener('input', (e) => {
            // Get raw token value and normalize (trim)
            // Token will be URL-encoded automatically by withToken() helper
            this.token = normalizeToken(e.target.value);
            // Token updated (debug removed)
            
            // Reject URL-encoded tokens (common mistake)
            if (this.token.includes("%2F") || this.token.includes("%3D") || this.token.includes("%2B")) {
                this.showError("Token looks URL-encoded. Paste the RAW token from control.env (not the encoded one).");
                this.token = '';
                tokenInput.value = '';
                this.saveToken();
                this.updateButtonStates();
                return;
            }
            
            this.saveToken();
            this.updateButtonStates();
            this.updateDebugInfo(); // Update debug panel
            
            // Auto-connect when token is entered (debounced)
            if (this.token.length > 0) {
                clearTimeout(this.healthCheckTimeout);
                this.healthCheckTimeout = setTimeout(() => {
                    // Auto-connecting (debug removed)
                    this.checkHealth();
                }, 500); // Wait 500ms after user stops typing
            } else {
                this.setConnected(false);
            }
        });
        
        // Also listen for paste events
        tokenInput.addEventListener('paste', (e) => {
            setTimeout(() => {
                this.token = normalizeToken(tokenInput.value);
                // Token pasted (debug removed)
                
                // Reject URL-encoded tokens (common mistake)
                if (this.token.includes("%2F") || this.token.includes("%3D") || this.token.includes("%2B")) {
                    this.showError("Token looks URL-encoded. Paste the RAW token from control.env (not the encoded one).");
                    this.token = '';
                    tokenInput.value = '';
                    this.saveToken();
                    this.updateButtonStates();
                    return;
                }
                
                this.saveToken();
                this.updateButtonStates();
                this.updateDebugInfo();
                if (this.token.length > 0) {
                    this.checkHealth();
                }
            }, 10);
        });

        // Connect button
        document.getElementById('connect-btn').addEventListener('click', () => {
            const input = document.getElementById("token-input");
            const token = normalizeToken(input.value);

            // Reject URL-encoded tokens (common mistake)
            if (token.includes("%2F") || token.includes("%3D") || token.includes("%2B")) {
                this.showError("Token looks URL-encoded. Paste the RAW token from control.env (not the encoded one).");
                return;
            }

            if (token.length < 20) {
                this.showError("Token too short. Paste the full RAW token from control.env.");
                return;
            }

            // Store raw token
            localStorage.setItem("chronovault_token", token);
            this.token = token;
            input.value = token; // Update input with normalized token

            // Continue with existing connect logic
            this.checkHealth();
        });

        // Remember token checkbox
        const rememberCheckbox = document.getElementById('remember-token-checkbox');
        rememberCheckbox.addEventListener('change', (e) => {
            this.saveToken();
        });

        // Refresh status button
        document.getElementById('refresh-status-btn').addEventListener('click', () => {
            this.fetchStatus();
            this.fetchRestorePoints();
        });

        // Approve once button
        document.getElementById('approve-once-btn').addEventListener('click', () => {
            this.showConfirmModal(
                'Approve Once',
                'This is a safety override that will allow the next backup to proceed even if warnings are present. Are you sure?',
                () => this.approveOnce()
            );
        });

        // Run backup button
        document.getElementById('run-backup-btn').addEventListener('click', () => {
            this.showConfirmModal(
                'Run Backup Now',
                'This will trigger a backup immediately. Continue?',
                () => this.runBackupNow()
            );
        });

        // Toggle raw JSON
        document.getElementById('toggle-raw-json-btn').addEventListener('click', () => {
            const rawJson = document.getElementById('raw-json');
            const btn = document.getElementById('toggle-raw-json-btn');
            if (rawJson.style.display === 'none') {
                rawJson.style.display = 'block';
                btn.textContent = 'Hide Raw Status JSON';
                if (this.lastStatus) {
                    rawJson.textContent = JSON.stringify(this.lastStatus, null, 2);
                }
            } else {
                rawJson.style.display = 'none';
                btn.textContent = 'Show Raw Status JSON';
            }
        });

        // Toggle debug panel
        document.getElementById('show-debug-btn').addEventListener('click', () => {
            const debugPanel = document.getElementById('debug-panel');
            debugPanel.style.display = 'block';
            this.updateDebugInfo();
        });

        document.getElementById('toggle-debug-btn').addEventListener('click', () => {
            document.getElementById('debug-panel').style.display = 'none';
        });

        // Test connection button
        document.getElementById('test-connection-btn').addEventListener('click', () => {
            this.testConnection();
        });


        // Modal buttons
        document.getElementById('modal-cancel').addEventListener('click', () => {
            this.hideConfirmModal();
        });
        document.getElementById('modal-confirm').addEventListener('click', () => {
            const callback = this.modalCallback;
            this.hideConfirmModal();
            if (callback) callback();
        });

        // Restore approval button
        const restoreApproveBtn = document.getElementById('restore-approve-btn');
        if (restoreApproveBtn) {
            restoreApproveBtn.addEventListener('click', () => {
                this.approveRestore();
            });
        }

        // Restore confirm checkbox
        const restoreConfirmCheckbox = document.getElementById('restore-confirm-checkbox');
        if (restoreConfirmCheckbox) {
            restoreConfirmCheckbox.addEventListener('change', () => {
                this.updateRestoreButtonState();
            });
        }

        // Restore button
        const restoreBtn = document.getElementById('restore-btn');
        if (restoreBtn) {
            restoreBtn.addEventListener('click', () => {
                this.executeRestore();
            });
        }

        // Start polling
        this.startPolling();
    }

    getToken() {
        // Check localStorage first (for bootstrap token from URL)
        const bootstrapToken = normalizeToken(localStorage.getItem('chronovault_token') || '');
        if (bootstrapToken) {
            // Update input field if empty
            const tokenInput = document.getElementById('token-input');
            if (tokenInput && (!tokenInput.value || tokenInput.value.trim().length === 0)) {
                tokenInput.value = bootstrapToken;
            }
            return bootstrapToken;
        }
        
        // Then check input field
        const tokenInput = document.getElementById('token-input');
        if (tokenInput && tokenInput.value) {
            const inputToken = normalizeToken(tokenInput.value);
            if (inputToken) {
                return inputToken;
            }
        }
        
        // Finally check storage based on config
        const remember = !!this.config.REMEMBER_TOKEN;
        const store = remember ? localStorage : sessionStorage;
        const storedToken = normalizeToken(store.getItem('chronovault_token') || '');
        
        if (storedToken) {
            // Update input field if token is in storage
            if (tokenInput && (!tokenInput.value || tokenInput.value.trim().length === 0)) {
                tokenInput.value = storedToken;
            }
            return storedToken;
        }
        
        // Fallback: check the other storage
        const fallbackStore = remember ? sessionStorage : localStorage;
        const fallbackToken = normalizeToken(fallbackStore.getItem('chronovault_token') || '');
        
        if (fallbackToken) {
            // Update input field if token is in fallback storage
            if (tokenInput && (!tokenInput.value || tokenInput.value.trim().length === 0)) {
                tokenInput.value = fallbackToken;
            }
            return fallbackToken;
        }
        
        return '';
    }

    saveToken() {
        // Save token based on checkbox state
        const rememberCheckbox = document.getElementById('remember-token-checkbox');
        const shouldRemember = rememberCheckbox ? rememberCheckbox.checked : false;
        
        // Get current token (from input or storage) and normalize
        const token = normalizeToken(this.getToken());
        
        if (token && token.length > 0) {
            // Update this.token with normalized value
            this.token = token;
            
            if (shouldRemember) {
                // Save to localStorage (persists across reboots) - always store raw token
                localStorage.setItem('chronovault_token', token);
                sessionStorage.removeItem('chronovault_token'); // Clear sessionStorage
            } else {
                // Save to sessionStorage (persists until tab closed) - always store raw token
                sessionStorage.setItem('chronovault_token', token);
                localStorage.removeItem('chronovault_token'); // Clear localStorage
            }
        } else {
            // Clear both if token is empty
            sessionStorage.removeItem('chronovault_token');
            localStorage.removeItem('chronovault_token');
            this.token = '';
        }
    }



    async checkHealth() {
        // Always get fresh token
        this.token = this.getToken();
        
        if (!this.token || this.token.length === 0) {
            console.warn('checkHealth called but token is empty');
            this.setConnected(false);
            this.showError('Please enter a token first');
            return;
        }

        const connectBtn = document.getElementById('connect-btn');
        const originalText = connectBtn.textContent;
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';

        // Get token for debugging - ensure we're using the class token
        const rawToken = getToken();
        const classToken = this.getToken();
        // Health check debug removed
        
        // Use class token for API call (ensure consistency)
        // Build URL manually using class token to ensure we use the right one
        // IMPORTANT: encodeURIComponent() properly encodes +, /, = and all special chars
        const endpoint = buildApiUrl('/health');
        const baseUrl = endpoint.startsWith('/') 
            ? new URL(endpoint, window.location.origin).toString()
            : endpoint;
        // Manually append encoded token to avoid URLSearchParams '+' encoding issue
        const separator = baseUrl.includes('?') ? '&' : '?';
        const encodedToken = encodeURIComponent(this.token);
        const apiUrl = `${baseUrl}${separator}t=${encodedToken}`;
        
        // URL debug removed
        
        // Log the actual token being sent (for debugging)
        // Extract token from URL for verification
        const urlObj = new URL(apiUrl);
        const sentToken = urlObj.searchParams.get('t');
        // Token debug removed

        try {
            // Simple fetch like the working test code
            const response = await fetch(apiUrl, { method: 'GET' });
            
            // Health check response (debug removed)
            
            // Get text first (like the working code)
            const text = await response.text();
            // Health check response text (debug removed)
            
            if (!response.ok) {
                this.setConnected(false);
                
                // Special handling for 404
                if (response.status === 404) {
                    const fullUrl = apiUrl.replace(/t=[^&]+/, 't=***');
                    const errorMsg = `Health endpoint not found (404).\n\nCalled URL: ${fullUrl}\n\nCheck:\n1) Backend server is running on port 8787\n2) /health endpoint is implemented\n3) Backend routing is correct\n4) Try: curl "${apiUrl.replace(/t=[^&]+/, 't=YOUR_TOKEN')}"`;
                    this.showError(errorMsg);
                    console.error('=== 404 Error Details ===');
                    console.error('Full URL called:', fullUrl);
                    console.error('Base URL config:', getApiBaseUrl() || 'same-origin (relative)');
                    console.error('Response status:', response.status);
                    console.error('Response headers:', [...response.headers.entries()]);
                } else {
                    this.showError(`Health check failed: HTTP ${response.status} - ${text.substring(0, 200) || response.statusText}`);
                }
                return;
            }

            // Try to parse as JSON, but be flexible
            let data;
            try {
                data = JSON.parse(text);
            } catch (e) {
                // If not JSON, check if it's just "ok" or similar
                if (text.trim().toLowerCase() === 'ok' || text.trim() === '') {
                    data = { status: 'ok' };
                } else {
                    // Try to extract status from text
                    console.warn('Health endpoint returned non-JSON, treating as success:', text);
                    data = { status: 'ok' }; // Assume success if we got 200 OK
                }
            }
            
            // Check if status is ok (flexible matching)
            if (data.status === 'ok' || data.status === 'OK' || response.status === 200) {
                this.setConnected(true);
                this.consecutiveFailures = 0; // Reset failure counter
                // Fetch status after successful health check
                this.fetchStatus();
                // Fetch restore points (will be throttled by fetchStatus)
            } else {
                this.consecutiveFailures++;
                if (this.consecutiveFailures >= this.maxFailures) {
                    this.setConnected(false);
                }
                this.showError(`Health check failed: Invalid response - ${text}`);
            }
        } catch (error) {
            console.error('Health check error:', error);
            this.setConnected(false);
            
            // Detect specific error types
            let errorMsg = error.message;
            let diagnosticMsg = '';
            
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                // This is usually CORS, network, or mixed content
                const currentOrigin = window.location.origin;
                let apiOrigin = 'unknown';
                try {
                    const apiBase = getApiBaseUrl();
                    if (apiBase) {
                        // Auto-fix missing protocol for origin check
                        let apiUrl = apiBase;
                        if (!apiUrl.startsWith('http://') && !apiUrl.startsWith('https://')) {
                            apiUrl = 'http://' + apiUrl;
                        }
                        apiOrigin = new URL(apiUrl).origin;
                    } else {
                        // Same-origin
                        apiOrigin = currentOrigin;
                    }
                } catch (e) {
                    apiOrigin = 'Invalid URL';
                }
                
                if (window.location.protocol === 'file:') {
                    errorMsg = 'CORS Error: UI opened as file://. Serve UI over HTTP.';
                    diagnosticMsg = `Origin: file:// | API: ${apiOrigin} | Fix: Use a web server (python -m http.server, nginx, etc.)`;
                } else if (window.location.protocol === 'https:') {
                    const apiBase = getApiBaseUrl();
                    if (apiBase && apiBase.startsWith('http:')) {
                        errorMsg = 'Mixed Content Error: HTTPS UI cannot call HTTP API.';
                        diagnosticMsg = `UI: ${currentOrigin} (HTTPS) | API: ${apiOrigin} (HTTP) | Fix: Use HTTPS for API or serve UI over HTTP`;
                    } else if (currentOrigin !== apiOrigin) {
                        errorMsg = 'CORS Error: UI and API are on different origins.';
                        diagnosticMsg = `UI Origin: ${currentOrigin} | API Origin: ${apiOrigin} | Fix: Backend must allow CORS from UI origin`;
                    } else {
                        errorMsg = `Network Error: Cannot connect to API`;
                        diagnosticMsg = `Check: 1) Server running? 2) Correct IP/port? 3) Firewall? 4) CORS headers?`;
                    }
                } else if (currentOrigin !== apiOrigin) {
                    errorMsg = 'CORS Error: UI and API are on different origins.';
                    diagnosticMsg = `UI Origin: ${currentOrigin} | API Origin: ${apiOrigin} | Fix: Backend must allow CORS from UI origin`;
                } else {
                    errorMsg = `Network Error: Cannot connect to API`;
                    diagnosticMsg = `Check: 1) Server running? 2) Correct IP/port? 3) Firewall? 4) CORS headers?`;
                }
            } else if (error.message.includes('CORS')) {
                errorMsg = 'CORS Error: Backend is blocking cross-origin requests.';
                diagnosticMsg = `Backend must send: Access-Control-Allow-Origin header`;
            }
            
            const fullError = diagnosticMsg ? `${errorMsg}\n${diagnosticMsg}` : errorMsg;
            console.error('Diagnostic:', diagnosticMsg);
            this.showError(fullError);
        } finally {
            connectBtn.disabled = false;
            connectBtn.textContent = originalText;
        }
    }

    setConnected(connected) {
        this.isConnected = connected;
        const indicator = document.getElementById('status-indicator');
        const text = document.getElementById('connection-text');
        
        if (connected) {
            indicator.className = 'status-indicator connected';
            text.textContent = 'Connected';
        } else {
            indicator.className = 'status-indicator disconnected';
            text.textContent = 'Disconnected';
        }
        
        this.updateButtonStates();
        this.updateDebugInfo();
    }

    updateDebugInfo() {
        const debugPanel = document.getElementById('debug-panel');
        if (debugPanel && debugPanel.style.display !== 'none') {
            const apiBase = getApiBaseUrl();
            const apiUrl = apiBase || 'same-origin (relative)';
            const uiOrigin = window.location.origin || window.location.protocol + '//' + window.location.host;
            const apiOrigin = apiBase ? (() => {
                try {
                    return new URL(apiBase).origin;
                } catch (e) {
                    return 'Invalid URL';
                }
            })() : uiOrigin;
            
            document.getElementById('debug-api-url').textContent = apiUrl;
            document.getElementById('debug-token-length').textContent = this.token ? this.token.length : 0;
            document.getElementById('debug-connection').textContent = this.isConnected ? 'Connected' : 'Disconnected';
            document.getElementById('debug-last-check').textContent = new Date().toLocaleTimeString();
            
            // Add origin info if available
            let originInfo = document.getElementById('debug-origin-info');
            if (!originInfo) {
                const debugInfo = document.querySelector('.debug-info');
                originInfo = document.createElement('div');
                originInfo.id = 'debug-origin-info';
                debugInfo.appendChild(originInfo);
            }
            originInfo.innerHTML = `<strong>UI Origin:</strong> ${uiOrigin} (${window.location.protocol === 'file:' ? '⚠️ file:// - CORS will fail!' : '✓'})<br>
                <strong>API Origin:</strong> ${apiOrigin}<br>
                <strong>Same Origin:</strong> ${uiOrigin === apiOrigin ? '✓ Yes' : '✗ No (CORS required)'}`;
        }
    }

    updateButtonStates() {
        const hasToken = this.token.length > 0;
        const connected = this.isConnected;
        const enabled = hasToken && connected;

        document.getElementById('approve-once-btn').disabled = !enabled;
        document.getElementById('refresh-status-btn').disabled = !enabled;
        document.getElementById('connect-btn').disabled = !hasToken;

        // IMPORTANT: The backup button state (disabled/text) is primarily controlled
        // by renderStatus() and runBackupNow() based on the actual backup state
        // (RUNNING/OK/ERROR) and the backupInitiated flag. We only touch it here
        // when there is clearly no backup running so we don't accidentally re-enable
        // it while a backup is in progress.
        const backupBtn = document.getElementById('run-backup-btn');
        if (backupBtn) {
            const isRunning =
                (this.lastStatus && this.lastStatus.state === 'RUNNING') ||
                this.backupInitiated;
            if (!isRunning && backupBtn.textContent !== 'Backup Running...') {
                backupBtn.disabled = !enabled;
            }
        }
    }

    // Helper function to fetch status data without rendering (for pre-checks)
    async fetchStatusData() {
        this.token = this.getToken();
        if (!this.token) return null;

        try {
            const apiUrl = withToken(buildApiUrl('/status'));
            const response = await fetch(apiUrl, { 
                method: 'GET',
                cache: 'no-store',
                headers: {
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache'
                }
            });
            
            if (!response.ok) {
                return null;
            }

            const text = await response.text();
            return JSON.parse(text);
        } catch (error) {
            return null;
        }
    }

    async fetchStatus() {
        // Always get fresh token
        this.token = this.getToken();
        if (!this.token) return;

        try {
            const apiUrl = withToken(buildApiUrl('/status'));
            // Add cache-busting headers to ensure we always get fresh status
            const response = await fetch(apiUrl, { 
                method: 'GET',
                cache: 'no-store',
                headers: {
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache'
                }
            });
            
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP ${response.status}: ${errorText || response.statusText}`);
            }

            // Get text first, then parse JSON (like the working test code)
            const text = await response.text();
            const data = JSON.parse(text);
            
            // Check for stale RUNNING state - if state is RUNNING but last_attempt_time is more than 5 minutes old,
            // it's likely stale (backup should not take that long, or it completed but status wasn't updated)
            if (data.state && data.state.toUpperCase() === 'RUNNING' && data.last_attempt_time) {
                try {
                    const lastAttempt = new Date(data.last_attempt_time);
                    const now = new Date();
                    const minutesSinceAttempt = (now - lastAttempt) / (1000 * 60);
                    
                    // If RUNNING state is more than 5 minutes old, it's likely stale
                    if (minutesSinceAttempt > 5) {
                        console.warn(`Stale RUNNING state detected: last attempt was ${minutesSinceAttempt.toFixed(1)} minutes ago. Forcing status refresh...`);
                        // Force another fetch after a short delay to get updated status
                        setTimeout(() => {
                            this.fetchStatus();
                        }, 1000);
                        return; // Don't render stale state
                    }
                } catch (e) {
                    console.warn('Could not parse last_attempt_time for stale check:', e);
                }
            }
            
            this.lastStatus = data;
            this.renderStatus(data);
            this.setConnected(true);
            this.consecutiveFailures = 0; // Reset failure counter on success
            
            // Only fetch restore points occasionally (not on every status poll)
            // Track last restore points fetch time
            const now = Date.now();
            if (!this.lastRestorePointsFetch || (now - this.lastRestorePointsFetch) > 30000) {
                // Fetch restore points at most once every 30 seconds
                this.lastRestorePointsFetch = now;
                this.fetchRestorePoints();
            }
            
            // Update raw JSON if visible
            const rawJson = document.getElementById('raw-json');
            if (rawJson.style.display !== 'none') {
                rawJson.textContent = JSON.stringify(data, null, 2);
            }
        } catch (error) {
            console.error('Fetch status error:', error);
            this.consecutiveFailures++;
            // Only mark disconnected after multiple consecutive failures
            if (this.consecutiveFailures >= this.maxFailures) {
                this.setConnected(false);
                this.showError(`Connection lost after ${this.consecutiveFailures} failures: ${error.message}`);
            } else {
                // Just log, don't show error for single failures
                console.warn(`Status fetch failed (${this.consecutiveFailures}/${this.maxFailures}):`, error.message);
            }
        }
    }

    async fetchRestorePoints() {
        // Always get fresh token
        const token = this.getToken();
        this.token = token; // Update instance variable
        
        if (!token || token.length === 0) {
            console.warn('fetchRestorePoints: No token available');
            const select = document.getElementById('restore-point-select');
            if (select) {
                select.innerHTML = '<option value="">No token - enter token first</option>';
                select.disabled = true;
            }
            return;
        }
        
        // fetchRestorePoints debug removed

        // Show restore points card if enabled, even if fetch fails
        if (this.config.ENABLE_RESTORE_UI) {
            const card = document.getElementById('restore-points-card');
            if (card) {
                card.style.display = 'block';
            }
        } else {
            return;
        }

        try {
            // Verify token is still available
            const currentToken = this.getToken();
            if (!currentToken) {
                throw new Error('Token lost during request preparation');
            }
            
            const apiUrl = withToken(buildApiUrl('/restore-points'));
            // Restore points fetch debug removed
            
            // Verify token is in URL
            if (!apiUrl.includes('?t=') && !apiUrl.includes('&t=')) {
                console.error('ERROR: Token parameter missing from URL!');
                console.error('Full URL:', apiUrl);
                throw new Error('Token not included in request URL');
            }
            
            const response = await fetch(apiUrl, { method: 'GET' });
            
            if (!response.ok) {
                const errorText = await response.text();
                let errorData = {};
                try {
                    errorData = JSON.parse(errorText);
                } catch (e) {
                    // Not JSON
                }
                
                if (response.status === 404) {
                    console.warn('Restore points endpoint not found (404). This is OK if backend doesn\'t implement it yet.');
                    const select = document.getElementById('restore-point-select');
                    if (select) {
                        select.innerHTML = '<option value="">Endpoint not found (404)</option>';
                        select.disabled = true;
                    }
                    return;
                }
                
                if (response.status === 403) {
                    console.error('Restore points endpoint returned 403 Forbidden');
                    console.error('Error detail:', errorData.detail || errorText);
                    const select = document.getElementById('restore-point-select');
                    if (select) {
                        select.innerHTML = '<option value="">403 Forbidden - Check token permissions</option>';
                        select.disabled = true;
                    }
                    // Show error to user
                    this.showError(`Restore points access denied (403). Your token may not have permission to access /restore-points endpoint.`);
                    return;
                }
                
                throw new Error(errorText || `HTTP ${response.status}: ${response.statusText}`);
            }

            const text = await response.text();
            const data = JSON.parse(text);
            this.renderRestorePoints(data);
        } catch (error) {
            console.error('Fetch restore points error:', error);
            // Show error in the restore points card
            const card = document.getElementById('restore-points-card');
            if (card) {
                const select = document.getElementById('restore-point-select');
                if (select) {
                    select.innerHTML = `<option value="">Error loading restore points: ${error.message}</option>`;
                    select.disabled = true;
                }
            }
        }
    }

    renderStatus(data) {
        // Backend state (authoritative)
        const backendStateRaw = data.state || 'UNKNOWN';
        const backendState = backendStateRaw.toUpperCase();

        // UI state may temporarily override to RUNNING right after click,
        // but we MUST use backendState to decide when backup is actually done
        let uiState = backendState;
        if (this.backupInitiated && backendState !== 'ERROR' && backendState !== 'OK') {
            uiState = 'RUNNING';
        }

        // Status badge with icon
        const badge = document.getElementById('status-badge');
        const stateIcons = {
            'OK': '✓',
            'RUNNING': '⏳',
            'WARNING': '⚠',
            'ERROR': '✗',
            'FROZEN': '⏸',
            'UNKNOWN': '?'
        };
        const icon = stateIcons[uiState] || '?';
        badge.innerHTML = `<span class="status-icon">${icon}</span> ${uiState}`;
        badge.className = `status-badge status-${uiState.toLowerCase()}`;

        // Status reason with better formatting
        const reason = document.getElementById('status-reason');
        const reasonText = data.reason || 'No reason provided';
        reason.innerHTML = `<span class="status-label">Status:</span> ${this.escapeHtml(reasonText)}`;

        // Times with better formatting
        const lastAttempt = document.getElementById('last-attempt');
        const attemptTime = this.formatTimestamp(data.last_attempt_time);
        lastAttempt.innerHTML = `<span class="time-label">🕐 Last Attempt:</span> <span class="time-value">${attemptTime}</span>`;

        const lastSuccess = document.getElementById('last-success');
        const successTime = this.formatTimestamp(data.last_success_time);
        lastSuccess.innerHTML = `<span class="time-label">✓ Last Success:</span> <span class="time-value">${successTime}</span>`;

        // Frozen indicator
        const frozenIndicator = document.getElementById('frozen-indicator');
        if (data.frozen === true) {
            frozenIndicator.style.display = 'block';
        } else {
            frozenIndicator.style.display = 'none';
        }

        // Warnings
        const warningsList = document.getElementById('warnings-list');
        if (data.warnings && Array.isArray(data.warnings) && data.warnings.length > 0) {
            warningsList.innerHTML = data.warnings.map(w => 
                `<div class="warning-item">${this.escapeHtml(w)}</div>`
            ).join('');
        } else {
            warningsList.innerHTML = '<div class="no-warnings">No warnings</div>';
        }

        // Change metrics with color coding
        const globalChange = data.global_change_pct || 0;
        const globalChangeEl = document.getElementById('global-change-pct');
        globalChangeEl.textContent = this.formatPercent(globalChange);
        globalChangeEl.className = 'metric-value ' + (Math.abs(globalChange) > 10 ? 'metric-warning' : 'metric-normal');
        
        const immichDelete = data.immich_delete_pct || 0;
        const immichDeleteEl = document.getElementById('immich-delete-pct');
        immichDeleteEl.textContent = this.formatPercent(immichDelete);
        immichDeleteEl.className = 'metric-value ' + (Math.abs(immichDelete) > 5 ? 'metric-warning' : 'metric-normal');
        
        const nextcloudDelete = data.nextcloud_delete_pct || 0;
        const nextcloudDeleteEl = document.getElementById('nextcloud-delete-pct');
        nextcloudDeleteEl.textContent = this.formatPercent(nextcloudDelete);
        nextcloudDeleteEl.className = 'metric-value ' + (Math.abs(nextcloudDelete) > 5 ? 'metric-warning' : 'metric-normal');

        // Mirror info with better formatting
        const mirrorState = data.mirror_state || '-';
        const mirrorStateEl = document.getElementById('mirror-state');
        mirrorStateEl.textContent = mirrorState;
        mirrorStateEl.className = mirrorState === 'OK' ? 'status-ok' : mirrorState === 'ERROR' ? 'status-error' : '';
        
        document.getElementById('mirror-reason').textContent = data.mirror_reason || '-';
        
        const mirrorAttempt = this.formatTimestamp(data.mirror_last_attempt_time);
        document.getElementById('mirror-last-attempt').innerHTML = 
            mirrorAttempt !== '-' ? `<span class="time-value">${mirrorAttempt}</span>` : '-';
            
        const mirrorSuccess = this.formatTimestamp(data.mirror_last_success_time);
        document.getElementById('mirror-last-success').innerHTML = 
            mirrorSuccess !== '-' ? `<span class="time-value">${mirrorSuccess}</span>` : '-';
        
        const rsyncExit = data.mirror_rsync_exit_code !== undefined ? data.mirror_rsync_exit_code : '-';
        const rsyncExitEl = document.getElementById('mirror-rsync-exit');
        rsyncExitEl.textContent = rsyncExit;
        rsyncExitEl.className = rsyncExit === 0 ? 'exit-code-ok' : rsyncExit !== '-' ? 'exit-code-error' : '';
        
        document.getElementById('mirror-source').textContent = data.mirror_source_path || '-';
        document.getElementById('mirror-dest').textContent = data.mirror_dest_path || '-';

        // Snapshots
        // Debug: Log raw snapshot timestamps to check format
        // Snapshot timestamps debug removed
        
        // Snapshots with better formatting (date only, no time)
        const dailyCreated = this.formatDateOnly(data.daily_snapshot_created);
        document.getElementById('daily-created').innerHTML = 
            dailyCreated !== '-' ? `<span class="time-value">${dailyCreated}</span>` : '<span class="no-data">No snapshots</span>';
        
        const dailyCount = data.daily_snapshot_count !== undefined ? data.daily_snapshot_count : 0;
        const dailyCountEl = document.getElementById('daily-count');
        dailyCountEl.textContent = dailyCount;
        dailyCountEl.className = dailyCount > 0 ? 'count-value' : 'count-zero';
        
        const dailyOldest = this.formatDateOnly(data.oldest_daily_snapshot);
        document.getElementById('daily-oldest').innerHTML = 
            dailyOldest !== '-' ? `<span class="time-value">${dailyOldest}</span>` : '-';

        const weeklyCreated = this.formatDateOnly(data.weekly_snapshot_created);
        document.getElementById('weekly-created').innerHTML = 
            weeklyCreated !== '-' ? `<span class="time-value">${weeklyCreated}</span>` : '<span class="no-data">No snapshots</span>';
        
        const weeklyCount = data.weekly_snapshot_count !== undefined ? data.weekly_snapshot_count : 0;
        const weeklyCountEl = document.getElementById('weekly-count');
        weeklyCountEl.textContent = weeklyCount;
        weeklyCountEl.className = weeklyCount > 0 ? 'count-value' : 'count-zero';
        
        const weeklyOldest = this.formatDateOnly(data.oldest_weekly_snapshot);
        document.getElementById('weekly-oldest').innerHTML = 
            weeklyOldest !== '-' ? `<span class="time-value">${weeklyOldest}</span>` : '-';

        // Restore history
        document.getElementById('last-restore-time').textContent = 
            this.formatTimestamp(data.last_restore_time);
        document.getElementById('last-restore-type').textContent = 
            data.last_restore_type || '-';
        document.getElementById('last-restore-source').textContent = 
            data.last_restore_source || '-';
        
        // Update backup button state based on current status
        // Use uiState for display/disable, backendState to know when backup really finished
        const effectiveState = uiState;
        const backupBtn = document.getElementById('run-backup-btn');
        const resultDiv = document.getElementById('action-result');
        
        if (backupBtn) {
            if (effectiveState === 'RUNNING') {
                backupBtn.disabled = true;
                backupBtn.textContent = 'Backup Running...';
                
                // Show "Backup already running" message
                if (resultDiv) {
                    resultDiv.className = 'action-result';
                    resultDiv.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 20px;">⏳</span>
                            <div>
                                <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                                <div style="font-size: 13px; opacity: 0.8;">A backup operation is currently in progress. Please wait for it to complete.</div>
                            </div>
                        </div>
                    `;
                }
                
                // Only clear the flag when backend actually reports OK or ERROR
                if (backendState === 'OK' || backendState === 'ERROR') {
                    this.backupInitiated = false;
                }
            } else {
                // Backup is not running - clear the flag and reset UI
                // Only clear flag if we're sure backup isn't running (backend OK or ERROR)
                if (backendState === 'OK' || backendState === 'ERROR') {
                    this.backupInitiated = false;
                }
                
                // Only enable if connected and has token
                backupBtn.disabled = !(this.isConnected && this.token.length > 0);
                backupBtn.textContent = 'Run Backup Now';
                
                // Clear "Backup already running" message when backup completes
                if (resultDiv && (backendState === 'OK' || backendState === 'ERROR')) {
                    const currentText = resultDiv.textContent || '';
                    const currentHtml = resultDiv.innerHTML || '';
                    if (currentText.includes('Backup Already Running') || 
                        currentText.includes('Backup already running') ||
                        currentHtml.includes('Backup Already Running')) {
                        resultDiv.textContent = '';
                        resultDiv.className = 'action-result';
                    }
                }
            }
        }
    }

    renderRestorePoints(data) {
        if (!this.config.ENABLE_RESTORE_UI) {
            return;
        }

        const card = document.getElementById('restore-points-card');
        if (!card) {
            return;
        }
        card.style.display = 'block';

        const select = document.getElementById('restore-point-select');
        if (!select) {
            return;
        }
        select.innerHTML = '<option value="">Select a restore point...</option>';

        // Store restore points data for later use
        this.restorePoints = data;

        // Combine daily and weekly restore points
        const allPoints = [];
        
        // Processing restore points
        
        // Helper function to normalize restore point data
        // Handles both formats:
        // 1. Simple: "2026-01-22" (string)
        // 2. Full: {id: "daily-2026-01-22", type: "daily", ...} (object)
        const normalizePoint = (item, type) => {
            if (typeof item === 'string') {
                // Simple format: convert string to object
                // Backend returns date strings like "2026-01-22" for both daily and weekly
                const dateStr = item;
                return {
                    id: `${type}-${dateStr}`,
                    type: type,
                    date: dateStr, // Always set date (API expects this for both daily and weekly)
                    week: type === 'weekly' ? dateStr : undefined, // Also set week for weekly (for display)
                    verified: true, // Assume verified if backend returns it
                    snapshot_path: `/mnt/backup/chronovault/snapshots/${type}/${dateStr}`,
                    created_at: new Date().toISOString() // Fallback, backend should provide this
                };
            } else if (item && typeof item === 'object' && item.id) {
                // Full format: use as-is, but ensure date is set
                if (!item.date && item.week) {
                    item.date = item.week; // Use week as date if date is missing
                }
                return item;
            }
            return null;
        };
        
        if (data.daily && Array.isArray(data.daily)) {
            data.daily.forEach((item) => {
                const point = normalizePoint(item, 'daily');
                if (point && point.verified !== false) {
                    allPoints.push(point);
                }
            });
        }
        
        if (data.weekly && Array.isArray(data.weekly)) {
            data.weekly.forEach((item) => {
                const point = normalizePoint(item, 'weekly');
                if (point && point.verified !== false) {
                    allPoints.push(point);
                }
            });
        }
        
        // Total restore points after filtering

        // Sort by date/week (newest first)
        // For simple format, sort by date string; for full format, sort by created_at
        allPoints.sort((a, b) => {
            // Try created_at first (full format)
            if (a.created_at && b.created_at) {
                const dateA = new Date(a.created_at);
                const dateB = new Date(b.created_at);
                if (!isNaN(dateA.getTime()) && !isNaN(dateB.getTime())) {
                    return dateB.getTime() - dateA.getTime();
                }
            }
            // Fallback: sort by date/week string (newest first)
            const dateA = a.date || a.week || '';
            const dateB = b.date || b.week || '';
            return dateB.localeCompare(dateA); // Newest date string first
        });

        // Populate select
        if (allPoints.length === 0) {
            select.innerHTML = '<option value="">No restore points available</option>';
            console.warn('No restore points to display after filtering');
        } else {
            allPoints.forEach(point => {
                const option = document.createElement('option');
                option.value = point.id;
                const label = point.type === 'daily' 
                    ? `${point.type} - ${point.date || point.id}`
                    : `${point.type} - ${point.week || point.id}`;
                option.textContent = label;
                option.dataset.point = JSON.stringify(point);
                select.appendChild(option);
            });
            // Populated dropdown with restore points
        }

        select.disabled = false;

        // Add change listener
        if (!this.restorePointSelectListener) {
            select.addEventListener('change', (e) => {
                this.onRestorePointSelected(e.target.value);
            });
            this.restorePointSelectListener = true;
        }
    }

    onRestorePointSelected(pointId) {
        const select = document.getElementById('restore-point-select');
        const selectedOption = select.options[select.selectedIndex];
        
        if (!pointId || !selectedOption) {
            this.hideRestoreUI();
            return;
        }

        const point = JSON.parse(selectedOption.dataset.point);
        this.selectedRestorePoint = point;

        // Show restore point details
        const detailsDiv = document.getElementById('restore-point-details');
        let detailsHtml = '<div class="restore-point-info">';
        detailsHtml += `<div><strong>Type:</strong> ${point.type}</div>`;
        detailsHtml += `<div><strong>ID:</strong> ${point.id}</div>`;
        if (point.date) {
            detailsHtml += `<div><strong>Date:</strong> ${point.date}</div>`;
        }
        if (point.week) {
            detailsHtml += `<div><strong>Week:</strong> ${point.week}</div>`;
        }
        detailsHtml += `<div><strong>Created:</strong> ${this.formatTimestamp(point.created_at)}</div>`;
        detailsHtml += `<div><strong>Verified:</strong> ${point.verified ? '✓ Yes' : '✗ No'}</div>`;
        
        // Show database dump info if available (informational only)
        if (point.db_dumps) {
            detailsHtml += '<div><strong>Database Dumps Available:</strong></div>';
            if (point.db_dumps.immich) {
                detailsHtml += `<div style="margin-left: 20px;">• Immich: ${point.db_dumps.immich.db || 'N/A'}</div>`;
            }
            if (point.db_dumps.nextcloud) {
                detailsHtml += `<div style="margin-left: 20px;">• Nextcloud: ${point.db_dumps.nextcloud.db || 'N/A'}</div>`;
            }
        } else {
            detailsHtml += '<div><strong>Database Dumps:</strong> Information not available</div>';
        }
        detailsHtml += '<div style="margin-top: 10px;"><strong>Restore includes:</strong> Filesystem + Databases + Permissions</div>';
        
        detailsHtml += '</div>';
        detailsDiv.innerHTML = detailsHtml;
        detailsDiv.style.display = 'block';

        // Show restore mode selection
        document.getElementById('restore-mode-selection').style.display = 'block';
        
        // Show warnings
        document.getElementById('restore-warnings').style.display = 'block';
        
        // Show confirmations
        document.getElementById('restore-confirmations').style.display = 'block';
        
        // Show actions
        document.getElementById('restore-actions').style.display = 'block';

        // Reset approval state
        this.restoreApproved = false;
        document.getElementById('restore-approve-btn').disabled = false;
        document.getElementById('restore-approval-status').textContent = '';
        document.getElementById('restore-confirm-checkbox').checked = false;
        document.getElementById('restore-btn').disabled = true;

        // All restore app options are always available (no need to check db_dumps)
        // Reset to "both" as default
        const bothRadio = document.querySelector('input[name="restore-apps"][value="both"]');
        if (bothRadio) {
            bothRadio.checked = true;
        }
    }

    hideRestoreUI() {
        document.getElementById('restore-point-details').style.display = 'none';
        document.getElementById('restore-mode-selection').style.display = 'none';
        document.getElementById('restore-warnings').style.display = 'none';
        document.getElementById('restore-confirmations').style.display = 'none';
        document.getElementById('restore-actions').style.display = 'none';
        this.selectedRestorePoint = null;
        this.restoreApproved = false;
    }

    async approveOnce() {
        const btn = document.getElementById('approve-once-btn');
        btn.disabled = true;
        btn.textContent = 'Processing...';

        try {
            const apiUrl = withToken(buildApiUrl('/action/approve-once'));
            // Approve once request
            
            const response = await fetch(apiUrl, { method: 'POST' });

            const resultDiv = document.getElementById('action-result');
            
            // Approve once response status
            
            if (response.ok) {
                const responseText = await response.text();
                // Approve once response
                
                resultDiv.className = 'action-result success';
                resultDiv.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 20px;">✓</span>
                        <div>
                            <div style="font-weight: 600; margin-bottom: 4px;">Approval Flag Created</div>
                            <div style="font-size: 13px; opacity: 0.8;">Next backup will proceed automatically...</div>
                        </div>
                    </div>
                `;
                this.startFastPolling(20000); // 20 seconds
                setTimeout(() => this.fetchStatus(), 1000);
            } else {
                const errorText = await response.text();
                console.error('Approve once failed, status:', response.status, 'body:', errorText);
                
                // Try to parse JSON error
                let errorMsg = errorText;
                try {
                    const errorJson = JSON.parse(errorText);
                    if (errorJson.detail) {
                        errorMsg = errorJson.detail;
                    } else if (errorJson.message) {
                        errorMsg = errorJson.message;
                    } else {
                        errorMsg = JSON.stringify(errorJson);
                    }
                } catch (e) {
                    // Not JSON, use text as-is
                }
                
                if (response.status === 404) {
                    errorMsg = `Endpoint not found (404). Check if backend implements POST /action/approve-once`;
                }
                
                throw new Error(errorMsg || `HTTP ${response.status}`);
            }
        } catch (error) {
            console.error('Approve once error:', error);
            this.showError(`Approve once failed: ${error.message}`);
            const resultDiv = document.getElementById('action-result');
            resultDiv.className = 'action-result error';
            resultDiv.textContent = `Error: ${error.message}`;
        } finally {
            btn.disabled = false;
            btn.textContent = 'Approve Once';
        }
    }

    async runBackupNow(retryCount = 0) {
        const btn = document.getElementById('run-backup-btn');
        const resultDiv = document.getElementById('action-result');
        
        // On first attempt, immediately set UI to "running" state
        if (retryCount === 0) {
            // Mark that we've initiated a backup
            this.backupInitiated = true;
            
            // Immediately disable button and show running state
            btn.disabled = true;
            btn.textContent = 'Backup Running...';
            
            // Immediately show "Backup already running" message
            resultDiv.className = 'action-result';
            resultDiv.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 20px;">⏳</span>
                    <div>
                        <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                        <div style="font-size: 13px; opacity: 0.8;">A backup operation is currently in progress. Please wait for it to complete.</div>
                    </div>
                </div>
            `;
            
            // Trigger immediate status refresh to update UI
            setTimeout(() => this.fetchStatus(), 500);
        }
        
        // On first attempt, check if backup is already running
        if (retryCount === 0) {
            // Check current status to see if backup is already running
            try {
                const statusData = await this.fetchStatusData();
                if (statusData && statusData.state === 'RUNNING') {
                    resultDiv.className = 'action-result';
                    resultDiv.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 20px;">⏳</span>
                            <div>
                                <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                                <div style="font-size: 13px; opacity: 0.8;">A backup operation is currently in progress. Please wait for it to complete.</div>
                            </div>
                        </div>
                    `;
                    // Keep button disabled
                    btn.disabled = true;
                    btn.textContent = 'Backup Running...';
                    // Start polling to detect when backup completes
                    this.startFastPolling(60000);
                    setTimeout(() => this.fetchStatus(), 2000);
                    return;
                }
            } catch (e) {
                console.warn('Could not check backup status before starting:', e);
                // Continue anyway - backend will reject if backup is running
            }
        }
        
        // On first attempt, ensure backend is ready by checking health first
        if (retryCount === 0 && !this.isConnected) {
            resultDiv.textContent = 'Checking backend connection...';
            try {
                await this.checkHealth();
                // Small delay to ensure backend is fully initialized
                await new Promise(resolve => setTimeout(resolve, 500));
            } catch (e) {
                console.warn('Health check before backup failed, proceeding anyway:', e);
            }
        }
        
        resultDiv.textContent = 'Backing up...';

        try {
            // Build the URL
            const endpoint = '/action/run-backup-now';
            const apiUrl = withToken(buildApiUrl(endpoint));
            
            // Detailed logging
            // RUN BACKUP DEBUG removed
            
            // Show status in UI
            resultDiv.textContent = 'Backing up...';
            resultDiv.className = 'action-result';
            
            const startTime = Date.now();
            // Fetch request started (debug removed)
            let response;
            try {
                // Add timeout to prevent hanging (5 minutes max - backups can take time)
                // Use longer timeout for backup operations which can legitimately take minutes
                const fetchPromise = fetch(apiUrl, { 
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json'
                    }
                });
                
                const timeoutMs = 300000; // 5 minutes for backup operations
                const timeoutPromise = new Promise((_, reject) => 
                    setTimeout(() => reject(new Error(`Fetch timeout after ${timeoutMs/1000} seconds`)), timeoutMs)
                );
                
                response = await Promise.race([fetchPromise, timeoutPromise]);
                // Fetch completed (debug removed)
            } catch (fetchError) {
                console.error(`[Attempt ${retryCount + 1}] Fetch error:`, fetchError);
                console.error(`[Attempt ${retryCount + 1}] Fetch error name:`, fetchError.name);
                console.error(`[Attempt ${retryCount + 1}] Fetch error message:`, fetchError.message);
                
                // If it's a timeout, don't retry (backup is likely still running on backend)
                // Just show a message that the request is taking longer than expected
                if (fetchError.message.includes('timeout')) {
                    resultDiv.className = 'action-result';
                    resultDiv.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 20px;">⏳</span>
                            <div>
                                <div style="font-weight: 600; margin-bottom: 4px;">Backup Request Sent</div>
                                <div style="font-size: 13px; opacity: 0.8;">Request is taking longer than expected. The backup may still be running in the background. Check status below.</div>
                            </div>
                        </div>
                    `;
                    // Trigger status refresh to see if backup completed
                    setTimeout(() => this.fetchStatus(), 2000);
                    return; // Exit early, don't throw error
                }
                
                throw fetchError;
            }
            const duration = Date.now() - startTime;
            
            // Response received (debug removed)
            
            // Get response text
            let responseText;
            try {
                responseText = await response.text();
                // Response body (debug removed)
            } catch (textError) {
                console.error(`[Attempt ${retryCount + 1}] Error reading response text:`, textError);
                throw textError;
            }
            
            // Update UI with debug info
            resultDiv.innerHTML = `
                <div style="font-size: 12px; margin-bottom: 5px;">
                    <strong>Debug:</strong> Status ${response.status} | Time: ${duration}ms
                </div>
            `;
            
            if (response.ok) {
                // Backup request successful (debug removed)
                
                // Try to parse response
                let responseData = responseText;
                try {
                    responseData = JSON.parse(responseText);
                    // Response JSON (debug removed)
                } catch (e) {
                    // Response is not JSON (debug removed)
                }
                
                // Backup request successful - keep the "Backup Already Running" message
                // The status polling will update it when status becomes RUNNING
                // Don't change the message here - keep it as "Backup Already Running"
                resultDiv.className = 'action-result';
                resultDiv.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 20px;">⏳</span>
                        <div>
                            <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                            <div style="font-size: 13px; opacity: 0.8;">A backup operation is currently in progress. Please wait for it to complete.</div>
                        </div>
                    </div>
                `;
                
                // Keep button disabled
                btn.disabled = true;
                btn.textContent = 'Backup Running...';
                
                // Starting fast polling (debug removed)
                // Start fast polling to watch for status changes
                this.startFastPolling(60000); // Poll for 60 seconds
                setTimeout(() => this.fetchStatus(), 1000);
                // Success path completed (debug removed)
                return; // Explicit return to prevent falling through
                } else {
                    // Check if error is due to backup already running (409 Conflict)
                    if (response.status === 409) {
                        // Backup is running - show info message and keep button disabled
                        resultDiv.className = 'action-result';
                        try {
                            const errorData = JSON.parse(responseText);
                            resultDiv.innerHTML = `
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span style="font-size: 20px;">⏳</span>
                                    <div>
                                        <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                                        <div style="font-size: 13px; opacity: 0.8;">${errorData.detail?.message || 'A backup operation is currently in progress. Please wait for it to complete.'}</div>
                                    </div>
                                </div>
                            `;
                        } catch (e) {
                            resultDiv.innerHTML = `
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span style="font-size: 20px;">⏳</span>
                                    <div>
                                        <div style="font-weight: 600; margin-bottom: 4px;">Backup Already Running</div>
                                        <div style="font-size: 13px; opacity: 0.8;">A backup operation is currently in progress. Please wait for it to complete.</div>
                                    </div>
                                </div>
                            `;
                        }
                        // Keep button disabled and update text
                        btn.disabled = true;
                        btn.textContent = 'Backup Running...';
                        // Start polling to detect when backup completes
                        this.startFastPolling(60000); // Poll for 60 seconds
                        setTimeout(() => this.fetchStatus(), 2000);
                        return;
                    }
                    
                    // On 500 error, check if backup is actually running (might be a transient error)
                    if (response.status === 500) {
                        // Check status to see if backup is actually running
                        try {
                            const statusData = await this.fetchStatusData();
                            if (statusData && statusData.state === 'RUNNING') {
                                // Backup is actually running despite 500 error - treat as success
                                resultDiv.className = 'action-result';
                                resultDiv.innerHTML = `
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span style="font-size: 20px;">⏳</span>
                                        <div>
                                            <div style="font-weight: 600; margin-bottom: 4px;">Backup Running</div>
                                            <div style="font-size: 13px; opacity: 0.8;">Backup is in progress. Status will update automatically...</div>
                                        </div>
                                    </div>
                                `;
                                // Keep button disabled
                                btn.disabled = true;
                                btn.textContent = 'Backup Running...';
                                // Start polling to detect when backup completes
                                this.startFastPolling(60000);
                                setTimeout(() => this.fetchStatus(), 2000);
                                return;
                            }
                        } catch (e) {
                            // Couldn't check status, continue with error handling
                        }
                    }
                    
                    // Don't log 409 Conflict as an error - it's expected behavior when backup is already running
                    if (response.status !== 409) {
                        console.error(`[Attempt ${retryCount + 1}] ✗ Backup request failed`);
                        console.error(`[Attempt ${retryCount + 1}] Status:`, response.status);
                        console.error(`[Attempt ${retryCount + 1}] Status text:`, response.statusText);
                        console.error(`[Attempt ${retryCount + 1}] Response body:`, responseText);
                    }
                
                // Try to parse JSON error
                let errorMsg = responseText;
                let errorDetails = {};
                let errorTextForDetection = responseText; // For error pattern matching
                try {
                    const errorJson = JSON.parse(responseText);
                    errorDetails = errorJson;
                    
                    // Extract error message intelligently
                    if (errorJson.detail) {
                        if (typeof errorJson.detail === 'string') {
                            errorMsg = errorJson.detail;
                            errorTextForDetection = errorJson.detail;
                        } else if (typeof errorJson.detail === 'object') {
                            // If detail is an object, check for stdout/stderr fields (common in backend script responses)
                            if (errorJson.detail.stdout) {
                                errorTextForDetection = String(errorJson.detail.stdout);
                                errorMsg = errorTextForDetection;
                            } else if (errorJson.detail.stderr) {
                                errorTextForDetection = String(errorJson.detail.stderr);
                                errorMsg = errorTextForDetection;
                            } else if (errorJson.detail.message) {
                                errorTextForDetection = String(errorJson.detail.message);
                                errorMsg = errorTextForDetection;
                            } else {
                                // Fallback: stringify the whole detail object
                                errorMsg = JSON.stringify(errorJson.detail);
                                errorTextForDetection = errorMsg;
                            }
                        } else {
                            errorMsg = String(errorJson.detail);
                            errorTextForDetection = errorMsg;
                        }
                    } else if (errorJson.message) {
                        errorMsg = typeof errorJson.message === 'string' ? errorJson.message : JSON.stringify(errorJson.message);
                        errorTextForDetection = String(errorMsg);
                    } else {
                        errorMsg = JSON.stringify(errorJson);
                        errorTextForDetection = errorMsg;
                    }
                } catch (e) {
                    // Error response is not JSON
                }
                
                // Ensure errorMsg is always a string before using string methods
                errorMsg = String(errorMsg || '');
                errorTextForDetection = String(errorTextForDetection || '');
                
                // Debug logging for retry detection
                // RETRY DETECTION DEBUG removed
                
                // Handle 500 errors with retry logic (common on first request after restart)
                // Allow up to 4 retries (5 total attempts) to give backend time to initialize
                if (response.status === 500 && retryCount < 4) {
                    const lowerErrorText = errorTextForDetection.toLowerCase();
                    const isIdentityError = lowerErrorText.includes('identity') || 
                                          lowerErrorText.includes('folder') ||
                                          lowerErrorText.includes('missing') ||
                                          lowerErrorText.includes('mounted but') ||
                                          lowerErrorText.includes('wrong disk');
                    
                    // Retry decision (debug removed)
                    
                    // Always retry on first attempt, or if it's an identity/mount error
                    if (isIdentityError || retryCount === 0) {
                        // Progressive delays: 3s, 6s, 9s, 12s to give backend more time to initialize
                        const delaySeconds = (retryCount + 1) * 3;
                        // 500 error detected, retrying (debug removed)
                        resultDiv.className = 'action-result';
                        resultDiv.textContent = 'Backing up...';
                        
                        await new Promise(resolve => setTimeout(resolve, delaySeconds * 1000));
                        
                        // About to retry (debug removed)
                        // Retry the backup - await the recursive call to ensure it completes
                        const retryResult = await this.runBackupNow(retryCount + 1);
                        // Recursive retry completed (debug removed)
                        return retryResult;
                    } else {
                        // Not retrying (debug removed)
                    }
                } else {
                    // Not retrying (debug removed)
                }
                
                // Build detailed error message
                let fullErrorMsg = errorMsg;
                if (response.status === 404) {
                    fullErrorMsg = `Endpoint not found (404)\n\n`;
                    fullErrorMsg += `Called: POST ${apiUrl.replace(/t=[^&]+/, 't=***')}\n\n`;
                    fullErrorMsg += `Check:\n`;
                    fullErrorMsg += `1) Backend implements POST /action/run-backup-now\n`;
                    fullErrorMsg += `2) Endpoint path is correct\n`;
                    fullErrorMsg += `3) Backend server is running\n\n`;
                    fullErrorMsg += `Response: ${responseText}`;
                } else {
                    fullErrorMsg = `HTTP ${response.status}: ${errorMsg}`;
                    if (Object.keys(errorDetails).length > 0) {
                        fullErrorMsg += `\n\nDetails: ${JSON.stringify(errorDetails, null, 2)}`;
                    }
                }
                
                console.error('Full error message:', fullErrorMsg);
                
                resultDiv.className = 'action-result error';
                resultDiv.innerHTML = `
                    <div><strong>Error:</strong> ${errorMsg}</div>
                    <div style="font-size: 11px; margin-top: 5px; color: #666;">
                        Status: ${response.status} | Response: ${responseText.substring(0, 150)}
                    </div>
                    <div style="font-size: 10px; margin-top: 5px; color: #999;">
                        Check browser console (F12) for full details
                    </div>
                `;
                
                throw new Error(fullErrorMsg);
            }
        } catch (error) {
            console.error(`[Attempt ${retryCount + 1}] === RUN BACKUP EXCEPTION ===`);
            console.error(`[Attempt ${retryCount + 1}] Error type:`, error.name);
            console.error(`[Attempt ${retryCount + 1}] Error message:`, error.message);
            console.error(`[Attempt ${retryCount + 1}] Error stack:`, error.stack);
            
            // Check if it's a network/CORS error
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                const networkError = `Network error: ${error.message}\n\nCheck:\n1) Backend server is running\n2) CORS is configured\n3) Network connectivity`;
                console.error('Network/CORS error detected');
                this.showError(networkError);
                resultDiv.className = 'action-result error';
                resultDiv.textContent = `Network Error: ${error.message}`;
            } else {
                this.showError(`Run backup failed: ${error.message}`);
                if (resultDiv.className !== 'action-result error') {
                    resultDiv.className = 'action-result error';
                    resultDiv.textContent = `Error: ${error.message}`;
                }
            }
        } finally {
            // Don't reset button state in finally block - let renderStatus() handle it
            // The button state will be managed by renderStatus() based on actual status
            // Only clear the flag if we got an error that's not recoverable
            if (retryCount === 0) {
                // If we got a non-409 error and status is not RUNNING, clear the flag
                // (409 means backup is running, so keep the flag)
                try {
                    const statusData = await this.fetchStatusData();
                    if (!statusData || statusData.state !== 'RUNNING') {
                        // Only clear flag if we're sure backup isn't running
                        // But keep it if we're not sure (let renderStatus handle it)
                        // The flag will be cleared when status becomes RUNNING or OK/ERROR
                    }
                } catch (e) {
                    // If we can't check status, keep the flag - renderStatus will handle it
                }
            }
            // END RUN BACKUP DEBUG removed
        }
    }

    showConfirmModal(title, message, callback) {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-message').textContent = message;
        document.getElementById('confirm-modal').style.display = 'flex';
        this.modalCallback = callback;
    }

    hideConfirmModal() {
        document.getElementById('confirm-modal').style.display = 'none';
        this.modalCallback = null;
    }

    startPolling() {
        const interval = this.config.POLL_INTERVAL_MS || 20000;
        
        this.pollInterval = setInterval(() => {
            if (this.token && this.isConnected) {
                this.checkHealth();
                if (!this.isFastPolling) {
                    this.fetchStatus();
                }
            }
        }, interval);
    }

    startFastPolling(duration) {
        if (this.isFastPolling) return;
        
        this.isFastPolling = true;
        const fastInterval = this.config.FAST_POLL_MS || 5000;
        
        const fastPoll = setInterval(() => {
            if (this.token && this.isConnected) {
                this.fetchStatus();
            }
        }, fastInterval);

        this.fastPollTimeout = setTimeout(() => {
            clearInterval(fastPoll);
            this.isFastPolling = false;
        }, duration);
    }

    formatTimestamp(timestamp) {
        if (!timestamp) return '-';
        try {
            // Handle date-only strings (YYYY-MM-DD) - treat as local time, not UTC
            let date;
            if (typeof timestamp === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(timestamp.trim())) {
                // Date-only format: parse as local date to avoid timezone issues
                const parts = timestamp.trim().split('-');
                date = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
            } else if (typeof timestamp === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(timestamp.trim())) {
                // ISO format without timezone - treat as local time
                const parts = timestamp.trim().split('T');
                const dateParts = parts[0].split('-');
                const timeParts = parts[1].split(':');
                date = new Date(
                    parseInt(dateParts[0]),
                    parseInt(dateParts[1]) - 1,
                    parseInt(dateParts[2]),
                    parseInt(timeParts[0]),
                    parseInt(timeParts[1]),
                    parseInt(timeParts[2])
                );
            } else {
                // Try standard Date parsing
                date = new Date(timestamp);
            }
            
            if (isNaN(date.getTime())) {
                console.warn('Invalid timestamp format:', timestamp);
                return timestamp; // Return as-is if not a valid date
            }
            
            // Format consistently: DD/MM/YYYY, HH:MM:SS
            const day = String(date.getDate()).padStart(2, '0');
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const year = date.getFullYear();
            const hours = String(date.getHours()).padStart(2, '0');
            const minutes = String(date.getMinutes()).padStart(2, '0');
            const seconds = String(date.getSeconds()).padStart(2, '0');
            
            return `${day}/${month}/${year}, ${hours}:${minutes}:${seconds}`;
        } catch (e) {
            console.warn('Error formatting timestamp:', timestamp, e);
            return timestamp;
        }
    }

    formatDateOnly(timestamp) {
        if (!timestamp) return '-';
        try {
            // Handle date-only strings (YYYY-MM-DD) - treat as local time, not UTC
            let date;
            if (typeof timestamp === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(timestamp.trim())) {
                // Date-only format: parse as local date to avoid timezone issues
                const parts = timestamp.trim().split('-');
                date = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
            } else if (typeof timestamp === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(timestamp.trim())) {
                // ISO format without timezone - treat as local time
                const parts = timestamp.trim().split('T');
                const dateParts = parts[0].split('-');
                const timeParts = parts[1].split(':');
                date = new Date(
                    parseInt(dateParts[0]),
                    parseInt(dateParts[1]) - 1,
                    parseInt(dateParts[2]),
                    parseInt(timeParts[0]),
                    parseInt(timeParts[1]),
                    parseInt(timeParts[2])
                );
            } else {
                // Try standard Date parsing
                date = new Date(timestamp);
            }
            
            if (isNaN(date.getTime())) {
                console.warn('Invalid timestamp format:', timestamp);
                return timestamp; // Return as-is if not a valid date
            }
            
            // Format consistently: DD/MM/YYYY (date only, no time)
            const day = String(date.getDate()).padStart(2, '0');
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const year = date.getFullYear();
            
            return `${day}/${month}/${year}`;
        } catch (e) {
            console.warn('Error formatting date:', timestamp, e);
            return timestamp;
        }
    }

    formatPercent(value) {
        if (value === undefined || value === null) return '-';
        return `${value}%`;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async testConnection() {
        const resultDiv = document.getElementById('connection-test-result');
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = '<p>Testing connection...</p>';
        
        const tokenInput = document.getElementById('token-input');
        const token = tokenInput ? tokenInput.value.trim() : '';
        const apiBase = getApiBaseUrl();
        const apiUrl = apiBase || 'same-origin (relative)';
        const uiOrigin = window.location.origin || window.location.protocol + '//' + window.location.host;
        
        let report = '<h4>Connection Test Report</h4><ul>';
        
        // Test 1: Config loaded
        const configOk = this.config;
        report += `<li><strong>Config loaded:</strong> ${configOk ? '✓ Yes' : '✗ No - CRITICAL!'}</li>`;
        if (configOk) {
            report += `<li><strong>API Base URL:</strong> ${apiUrl} ${apiBase ? '' : '(same-origin)'}</li>`;
        } else {
            report += `<li><strong>Fix:</strong> Create ui-config.js (API_BASE_URL can be empty for same-origin)</li>`;
        }
        
        // Test 2: Origin check
        const isFileProtocol = window.location.protocol === 'file:';
        report += `<li><strong>UI Origin:</strong> ${uiOrigin} ${isFileProtocol ? '❌ (file:// - CORS WILL FAIL!)' : '✓'}</li>`;
        if (isFileProtocol) {
            report += `<li><strong>Fix file://:</strong> Serve UI over HTTP using a web server (e.g., python3 -m http.server <port>)<br>Then open: http://<server-ip>:<port>/</li>`;
        }
        
        if (configOk) {
            if (apiBase) {
                // Auto-fix missing protocol for display
                let testUrl = apiBase;
                if (!testUrl.startsWith('http://') && !testUrl.startsWith('https://')) {
                    testUrl = 'http://' + testUrl;
                    report += `<li><strong>⚠️ API URL missing protocol:</strong> Auto-fixed to ${testUrl}</li>`;
                }
                
                try {
                    const apiOrigin = new URL(testUrl).origin;
                    const sameOrigin = uiOrigin === apiOrigin;
                    report += `<li><strong>API Origin:</strong> ${apiOrigin}</li>`;
                    report += `<li><strong>Same Origin:</strong> ${sameOrigin ? '✓ Yes (no CORS needed)' : '✗ No (CORS required)'}</li>`;
                    if (!sameOrigin && !isFileProtocol) {
                        report += `<li><strong>CORS Required:</strong> Backend must send: Access-Control-Allow-Origin header</li>`;
                    }
                } catch (e) {
                    report += `<li><strong>API URL Parse:</strong> ✗ Invalid URL format - ${e.message}</li>`;
                    report += `<li><strong>Fix:</strong> API_BASE_URL must include protocol (http:// or https://) or be empty for same-origin</li>`;
                }
            } else {
                // Same-origin mode
                report += `<li><strong>API Origin:</strong> ${uiOrigin} (same-origin)</li>`;
                report += `<li><strong>Same Origin:</strong> ✓ Yes (no CORS needed)</li>`;
            }
        }
        
        // Test 3: Mixed content
        const isMixedContent = window.location.protocol === 'https:' && apiBase && apiBase.startsWith('http:');
        if (isMixedContent) {
            report += `<li><strong>Mixed Content:</strong> ❌ HTTPS UI → HTTP API (BLOCKED by browser)</li>`;
            report += `<li><strong>Fix:</strong> Serve UI over HTTP or put API behind HTTPS</li>`;
        } else {
            report += `<li><strong>Mixed Content:</strong> ✓ OK</li>`;
        }
        
        // Test 4: Token
        report += `<li><strong>Token provided:</strong> ${token ? `✓ Yes (${token.length} chars)` : '✗ No - enter token first'}</li>`;
        
        // Test 5: Actual fetch
        if (token && apiUrl !== 'Not set' && configOk && !isFileProtocol && !isMixedContent) {
            try {
                const testUrl = withToken(buildApiUrl('/health'));
                report += `<li><strong>Testing fetch to:</strong> ${testUrl.replace(/t=[^&]+/, 't=***')}</li>`;
                
                const startTime = Date.now();
                const response = await fetch(testUrl, { method: 'GET' });
                const duration = Date.now() - startTime;
                const text = await response.text();
                
                report += `<li><strong>Response Status:</strong> ${response.status} ${response.statusText}</li>`;
                report += `<li><strong>Response Time:</strong> ${duration}ms</li>`;
                report += `<li><strong>Response Body:</strong> ${text.substring(0, 150)}${text.length > 150 ? '...' : ''}</li>`;
                
                // Check CORS headers
                const corsHeader = response.headers.get('Access-Control-Allow-Origin');
                const corsMethods = response.headers.get('Access-Control-Allow-Methods');
                if (corsHeader) {
                    report += `<li><strong>CORS Header:</strong> ✓ Present (${corsHeader})</li>`;
                    if (corsHeader === '*' || corsHeader === uiOrigin) {
                        report += `<li><strong>CORS Config:</strong> ✓ Allows this origin</li>`;
                    } else {
                        report += `<li><strong>CORS Config:</strong> ⚠️ May not allow ${uiOrigin}</li>`;
                    }
                } else {
                    report += `<li><strong>CORS Header:</strong> ✗ Missing (CORS will fail if different origins)</li>`;
                }
                
                if (response.ok) {
                    report += `<li><strong>Connection:</strong> <span style="color: green; font-weight: bold;">✓ SUCCESS</span></li>`;
                } else if (response.status === 404) {
                    report += `<li><strong>Connection:</strong> ✗ 404 - Endpoint not found</li>`;
                    report += `<li><strong>Check:</strong> Backend must implement GET /health endpoint</li>`;
                } else {
                    report += `<li><strong>Connection:</strong> ✗ Failed (HTTP ${response.status})</li>`;
                }
            } catch (error) {
                report += `<li><strong>Fetch Error:</strong> ✗ ${error.message}</li>`;
                if (error.message.includes('CORS') || error.message.includes('Failed to fetch')) {
                    report += `<li><strong>Likely Cause:</strong> CORS blocked or network error</li>`;
                    report += `<li><strong>Check:</strong> 1) Backend CORS headers 2) Server running 3) Firewall</li>`;
                }
            }
        } else {
            if (isFileProtocol) {
                report += `<li><strong>Fetch Test:</strong> ⏭️ Skipped (file:// origin blocks all requests)</li>`;
            } else if (isMixedContent) {
                report += `<li><strong>Fetch Test:</strong> ⏭️ Skipped (mixed content blocked)</li>`;
            } else if (!token) {
                report += `<li><strong>Fetch Test:</strong> ⏭️ Skipped (no token provided)</li>`;
            } else {
                report += `<li><strong>Fetch Test:</strong> ⏭️ Skipped (config issue)</li>`;
            }
        }
        
        report += '</ul>';
        resultDiv.innerHTML = report;
    }

    async approveRestore() {
        // First create approve-once flag
        try {
            const apiUrl = withToken(buildApiUrl('/action/approve-once'));
            // Creating approve-once for restore
            
            const response = await fetch(apiUrl, { method: 'POST' });
            
            if (response.ok) {
                this.restoreApproved = true;
                document.getElementById('restore-approve-btn').disabled = true;
                document.getElementById('restore-approval-status').textContent = '✓ Approved';
                document.getElementById('restore-approval-status').className = 'approval-status approved';
                this.updateRestoreButtonState();
                // Restore approved successfully
            } else {
                const errorText = await response.text();
                throw new Error(errorText || `HTTP ${response.status}`);
            }
        } catch (error) {
            console.error('Approve restore error:', error);
            this.showError(`Failed to approve restore: ${error.message}`);
        }
    }

    updateRestoreButtonState() {
        const confirmChecked = document.getElementById('restore-confirm-checkbox').checked;
        const restoreBtn = document.getElementById('restore-btn');
        
        if (this.restoreApproved && confirmChecked && this.selectedRestorePoint) {
            restoreBtn.disabled = false;
        } else {
            restoreBtn.disabled = true;
        }
    }

    async executeRestore() {
        if (!this.selectedRestorePoint) {
            this.showError('No restore point selected');
            return;
        }

        if (!this.restoreApproved) {
            this.showError('Restore must be approved first');
            return;
        }

        const confirmChecked = document.getElementById('restore-confirm-checkbox').checked;
        if (!confirmChecked) {
            this.showError('Please confirm you understand the risks');
            return;
        }

        // Get selected apps
        const appsRadio = document.querySelector('input[name="restore-apps"]:checked');
        if (!appsRadio) {
            this.showError('Please select which applications to restore');
            return;
        }

        const apps = appsRadio.value; // "both", "immich", or "nextcloud"
        const point = this.selectedRestorePoint;
        
        // Extract type and date from restore point
        const type = point.type; // "daily" or "weekly"
        // For daily: use point.date, for weekly: use point.week (which may be a date string or week format)
        // The API expects date as YYYY-MM-DD, so use whichever is available
        const date = point.date || point.week; // Date string (YYYY-MM-DD)
        
        if (!type || !date) {
            this.showError(`Invalid restore point: missing type (${type}) or date (${date})`);
            console.error('Restore point data:', point);
            return;
        }
        
        // Validate type
        if (type !== 'daily' && type !== 'weekly') {
            this.showError(`Invalid restore point type: ${type}. Must be "daily" or "weekly".`);
            return;
        }
        
        // Validate apps
        if (apps !== 'both' && apps !== 'immich' && apps !== 'nextcloud') {
            this.showError(`Invalid apps selection: ${apps}. Must be "both", "immich", or "nextcloud".`);
            return;
        }

        // Final confirmation - simple Yes/No dialog
        const appsLabel = apps === 'both' ? 'Both (Immich + Nextcloud)' : 
                         apps === 'immich' ? 'Immich Only' : 'Nextcloud Only';
        
        const confirmed = confirm(
            `⚠️ FINAL CONFIRMATION ⚠️\n\n` +
            `You are about to restore:\n` +
            `• Type: ${type}\n` +
            `• Date: ${date}\n` +
            `• Applications: ${appsLabel}\n\n` +
            `This will:\n` +
            `• Overwrite all live data\n` +
            `• Stop all containers\n` +
            `• Restore filesystem + databases + permissions\n` +
            `• Restart containers\n\n` +
            `This operation is IRREVERSIBLE!\n\n` +
            `Click OK to proceed or Cancel to abort.`
        );

        if (!confirmed) {
            return; // User cancelled
        }

        const restoreBtn = document.getElementById('restore-btn');
        const statusDiv = document.getElementById('restore-status');
        
        restoreBtn.disabled = true;
        restoreBtn.textContent = 'Restoring...';
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = '<p>Starting restore operation...</p>';

        try {
            // Build API URL with properly encoded token
            // POST /action/restore?t=<encoded_token>
            const apiUrl = withToken(buildApiUrl('/action/restore'));
            const token = this.getToken();
            if (!token) {
                throw new Error('No token available');
            }
            
            // Request body must match exact format: {"type":"daily","date":"2026-01-23","apps":"both"}
            const requestBody = {
                type: type,
                date: date,
                apps: apps
            };
            
            // Restore request
            // Restore endpoint debug removed
            
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            const responseText = await response.text();
            // Restore response

            if (response.ok) {
                statusDiv.innerHTML = `
                    <div class="restore-status-success">
                        <strong>✓ Restore started successfully</strong>
                        <p>Restore operation is running in the background. Polling for status updates...</p>
                    </div>
                `;
                
                // Start fast polling to watch restore progress
                this.startFastPolling(120000); // Poll for 2 minutes
                setTimeout(() => this.fetchStatus(), 2000);
                
                // Reset restore UI after a delay
                setTimeout(() => {
                    this.hideRestoreUI();
                    document.getElementById('restore-point-select').value = '';
                }, 5000);
            } else {
                let errorMsg = responseText;
                try {
                    const errorJson = JSON.parse(responseText);
                    
                    // Try to extract the most useful error message
                    if (errorJson.detail) {
                        errorMsg = typeof errorJson.detail === 'string' ? errorJson.detail : JSON.stringify(errorJson.detail);
                    } else if (errorJson.message) {
                        errorMsg = typeof errorJson.message === 'string' ? errorJson.message : JSON.stringify(errorJson.message);
                    } else if (errorJson.stderr) {
                        // Backend returns stderr - extract the key error
                        const stderr = errorJson.stderr;
                        // Look for RuntimeError or the last meaningful error line
                        const runtimeErrorMatch = stderr.match(/RuntimeError: (.+?)(?:\n|$)/);
                        if (runtimeErrorMatch) {
                            errorMsg = runtimeErrorMatch[1];
                        } else {
                            // Get the last non-empty line from stderr
                            const stderrLines = stderr.split('\n').filter(line => line.trim());
                            if (stderrLines.length > 0) {
                                errorMsg = stderrLines[stderrLines.length - 1];
                            } else {
                                errorMsg = stderr.substring(0, 200); // First 200 chars
                            }
                        }
                    } else if (errorJson.stdout) {
                        // Look for ERROR: lines in stdout
                        const stdout = errorJson.stdout;
                        const errorLines = stdout.split('\n').filter(line => line.includes('ERROR:'));
                        if (errorLines.length > 0) {
                            // Get the last ERROR line
                            const lastError = errorLines[errorLines.length - 1];
                            errorMsg = lastError.replace(/^.*ERROR:\s*/, '').substring(0, 300);
                        } else {
                            errorMsg = `Exit code: ${errorJson.rc || 'unknown'}`;
                        }
                    } else if (errorJson.rc !== undefined) {
                        errorMsg = `Restore script failed with exit code ${errorJson.rc}`;
                    } else {
                        // If it's an object but no detail/message, stringify the whole thing
                        errorMsg = JSON.stringify(errorJson);
                    }
                } catch (e) {
                    // Not JSON, use responseText as-is
                    errorMsg = responseText || `HTTP ${response.status}: ${response.statusText}`;
                }
                
                throw new Error(errorMsg || `HTTP ${response.status}`);
            }
        } catch (error) {
            console.error('Restore error:', error);
            
            // Safely extract error message
            let errorMessage = 'Unknown error';
            if (error && typeof error === 'object') {
                if (error.message && typeof error.message === 'string') {
                    errorMessage = error.message;
                } else if (error.detail && typeof error.detail === 'string') {
                    errorMessage = error.detail;
                } else {
                    // Last resort: stringify the error object
                    try {
                        errorMessage = JSON.stringify(error);
                    } catch (e) {
                        errorMessage = String(error);
                    }
                }
            } else if (error) {
                errorMessage = String(error);
            }
            
            statusDiv.innerHTML = `
                <div class="restore-status-error">
                    <strong>✗ Restore failed</strong>
                    <p>${this.escapeHtml(errorMessage)}</p>
                </div>
            `;
            this.showError(`Restore failed: ${errorMessage}`);
        } finally {
            restoreBtn.disabled = false;
            restoreBtn.textContent = 'Execute Restore';
        }
    }

    showError(message) {
        const errorDisplay = document.getElementById('error-display');
        errorDisplay.textContent = `Error: ${message}`;
        errorDisplay.style.display = 'block';
        console.error('UI Error:', message);
        
        // Update debug info if visible
        const debugLastError = document.getElementById('debug-last-error');
        if (debugLastError) {
            debugLastError.textContent = message.substring(0, 100); // Truncate long messages
        }
        
        // Auto-hide after 15 seconds (longer for debugging)
        setTimeout(() => {
            errorDisplay.style.display = 'none';
        }, 15000);
    }
}

// Initialize UI when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.chronovaultUI = new ChronovaultUI();
    });
} else {
    window.chronovaultUI = new ChronovaultUI();
}
