class JarvisWebSocketService {
  constructor() {
    this.socket = null;
    this.isConnected = false;
    this.reconnectTimer = null;
    this.reconnectDelay = 2000; // grows on each failure, capped at 15s
    this.listeners = new Set();
    this.statusListeners = new Set();
    // Optional transport token; set via jarvisWS.setAuthToken() when the backend
    // enforces JARVIS_AUTH_TOKEN.
    this.authToken = null;
  }

  setAuthToken(token) {
    this.authToken = token || null;
  }

  /**
   * Resolves the WebSocket URL.
   * Same-origin "/ws" works through the Vite dev proxy AND in production builds.
   */
  resolveUrl() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const base = `${proto}://${window.location.host}/ws`;
    return this.authToken ? `${base}?token=${encodeURIComponent(this.authToken)}` : base;
  }

  /**
   * Establishes the WebSocket connection to the backend.
   */
  connect() {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const wsUrl = this.resolveUrl();
    console.log(`[J.A.R.V.I.S. WS] Connecting to ${wsUrl}...`);
    this.socket = new WebSocket(wsUrl);

    this.socket.onopen = () => {
      console.log('[J.A.R.V.I.S. WS] Connected successfully.');
      this.isConnected = true;
      this.reconnectDelay = 2000; // reset backoff after a healthy connection
      this.notifyStatusListeners(true);

      if (this.reconnectTimer) {
        clearInterval(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    };

    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.notifyListeners(data);
      } catch (error) {
        console.error('[J.A.R.V.I.S. WS] Failed to parse incoming message:', event.data);
      }
    };

    this.socket.onclose = () => {
      const wasConnected = this.isConnected;
      this.isConnected = false;
      this.notifyStatusListeners(false);
      if (wasConnected) {
        console.warn('[J.A.R.V.I.S. WS] Connection closed. Attempting reconnect...');
      }
      this.handleReconnect();
    };

    this.socket.onerror = () => {
      // onclose always follows onerror; avoid double-close side effects here.
      console.error('[J.A.R.V.I.S. WS] Connection error encountered.');
    };
  }

  /**
   * Handles automatic reconnection with capped exponential backoff.
   */
  handleReconnect() {
    if (!this.reconnectTimer) {
      this.reconnectTimer = setInterval(() => {
        console.log(`[J.A.R.V.I.S. WS] Reconnecting in ${this.reconnectDelay / 1000}s...`);
        this.connect();
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 15000);
      }, this.reconnectDelay);
    }
  }

  /**
   * Sends a structured command or action payload to the backend pipeline.
   */
  send(action, payload = {}) {
    if (!this.isConnected || !this.socket) {
      console.error('[J.A.R.V.I.S. WS] Cannot send message, socket is not connected.');
      return false;
    }

    const message = JSON.stringify({ action, payload, timestamp: new Date().toISOString() });
    this.socket.send(message);
    return true;
  }

  /**
   * Registers a listener for incoming messages (tasks, verification, agent updates).
   */
  addMessageListener(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  /**
   * Registers a listener for connection status changes.
   */
  addStatusListener(callback) {
    this.statusListeners.add(callback);
    callback(this.isConnected); // Initial state push
    return () => this.statusListeners.delete(callback);
  }

  notifyListeners(data) {
    this.listeners.forEach((callback) => callback(data));
  }

  notifyStatusListeners(status) {
    this.statusListeners.forEach((callback) => callback(status));
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearInterval(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.isConnected = false;
    this.notifyStatusListeners(false);
  }
}

// Export as a singleton instance across the frontend
export const jarvisWS = new JarvisWebSocketService();