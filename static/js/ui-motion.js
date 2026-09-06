/* Presentation lifecycle only: no fetch, storage, form or extension hooks.
   CSS owns reduced-motion support and remains usable when this file is blocked. */
(() => {
  'use strict';
  const root = document.documentElement;
  if (root.dataset.uiMotionReady === 'true') return;
  root.dataset.uiMotionReady = 'true';

  const syncVisibility = () => {
    root.toggleAttribute('data-ui-paused', document.visibilityState === 'hidden');
  };
  document.addEventListener('visibilitychange', syncVisibility);
  syncVisibility();
})();
