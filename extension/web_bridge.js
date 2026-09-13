(() => {
  const origin = location.origin;
  const VERSION = (() => {
    try { return chrome.runtime.getManifest().version || '1.0.0'; } catch (e) { return '1.0.0'; }
  })();

  // 1. Mark DOM for instant synchronous presence detection
  try {
    document.documentElement.setAttribute('data-fbpostpro-connector', 'installed');
    document.documentElement.setAttribute('data-fbpostpro-version', VERSION);
  } catch (e) {}

  // 2. Announce presence to web page
  const notifyReady = () => {
    try {
      window.postMessage({
        source: 'FBPOST_EXTENSION',
        type: 'CONNECTOR_READY',
        version: VERSION
      }, origin);
    } catch (e) {}
  };
  notifyReady();

  // Save current serverOrigin if not yet configured
  chrome.storage.local.get(['serverOrigin', 'deviceId', 'token']).then(c => {
    if (!c.deviceId || !c.token) {
      chrome.storage.local.set({ serverOrigin: origin }).catch(() => {});
    }
  }).catch(() => {});

  const ping = () => {
    try {
      chrome.runtime.sendMessage({ type: 'POLL_NOW' }).catch(() => {});
    } catch (e) {}
  };
  ping();

  const timer = setInterval(() => {
    if (document.visibilityState === 'visible') ping();
  }, 5000);

  // 3. Listen for requests from the Web Page (FBPOST_WEB)
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data || {};
    if (data.source !== 'FBPOST_WEB') return;

    // A. Ping / Status Check
    if (data.type === 'PING_CONNECTOR' || data.type === 'GET_STATUS') {
      chrome.runtime.sendMessage({ type: 'GET_CONNECTOR_STATUS' })
        .then(result => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'CONNECTOR_PONG',
            result: result || { ok: true, installed: true, paired: false, version: VERSION }
          }, origin);
        })
        .catch(err => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'CONNECTOR_PONG',
            result: { ok: false, installed: true, error: String(err), version: VERSION }
          }, origin);
        });
      return;
    }

    // B. One-Click Pairing
    if (data.type === 'PAIR_CONNECTOR') {
      const code = String(data.code || '').trim().toUpperCase();
      chrome.runtime.sendMessage({
        type: 'PAIR_FROM_WEB',
        serverOrigin: origin,
        code: code
      })
        .then(result => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'PAIR_RESULT',
            result: result
          }, origin);
        })
        .catch(err => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'PAIR_RESULT',
            result: { ok: false, error: String(err) }
          }, origin);
        });
      return;
    }

    // C. Reconnect / Force Heartbeat
    if (data.type === 'RECONNECT_CONNECTOR' || data.type === 'POLL_NOW') {
      chrome.runtime.sendMessage({ type: 'POLL_NOW' })
        .then(result => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'RECONNECT_RESULT',
            result: result
          }, origin);
        })
        .catch(err => {
          window.postMessage({
            source: 'FBPOST_EXTENSION',
            type: 'RECONNECT_RESULT',
            result: { ok: false, error: String(err) }
          }, origin);
        });
      return;
    }
  });

  window.addEventListener('beforeunload', () => clearInterval(timer));
})();
