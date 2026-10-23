// Registers the service worker (templates/sw.js), which only shows the offline page when the
// server can't be reached. The URL comes from data-sw, so the app also works under a path prefix.
(function () {
  var sw = document.currentScript && document.currentScript.dataset.sw;
  // Service workers need https (or localhost); elsewhere the app simply works without one
  if (!sw || !('serviceWorker' in navigator) || !window.isSecureContext) return;
  window.addEventListener('load', function () {
    navigator.serviceWorker.register(sw).catch(function () {});
  });
})();
