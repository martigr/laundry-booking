// Push button on the profile page: subscribes this browser at its push service and hands the
// subscription to the server (profile.push_subscribe), or switches it off again. The server only
// sends to subscriptions it knows (data-endpoints), so "active" means: this browser has one and
// the server has it too.
(function () {
  var box = document.getElementById('push-device');
  if (!box) return;
  var button = document.getElementById('push-toggle');
  var status = document.getElementById('push-status');
  var form = box.closest('form');
  var known = JSON.parse(box.dataset.endpoints);
  var registration = null;
  var current = null;  // this browser's subscription, if any

  function show(text, label) {
    status.textContent = text;
    button.hidden = !label;
    if (label) button.textContent = label;
  }

  if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window) ||
      !('Notification' in window)) {
    show('Dieser Browser kann keine Push-Mitteilungen empfangen.');
    return;
  }

  function serverKey() {
    var b64 = box.dataset.key.replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(b64 + '==='.slice((b64.length + 3) % 4));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }

  // A subscription made with another server key (the operator replaced it) can't be used any more
  function sameKey(subscription) {
    var key = subscription.options && subscription.options.applicationServerKey;
    if (!key) return false;
    var a = new Uint8Array(key), b = serverKey();
    return a.length === b.length && a.every(function (v, i) { return v === b[i]; });
  }

  function post(url, fields) {
    var body = new URLSearchParams(fields);
    body.append('csrf_token', form.elements.csrf_token.value);
    // redirect: 'error' so an expired login (redirect to /login) counts as a failure
    return fetch(url, {method: 'POST', body: body, credentials: 'same-origin', redirect: 'error'})
      .then(function (response) {
        if (response.status !== 204) throw new Error('HTTP ' + response.status);
      });
  }

  function render() {
    if (Notification.permission === 'denied') {
      show('Mitteilungen sind für diese Seite blockiert. Erlaube sie in den Einstellungen des Browsers.');
    } else if (current && known.indexOf(current.endpoint) >= 0) {
      var any = form.querySelector('input[name=reminder_push]:checked, input[name=freed_push]:checked');
      show(any ? 'Auf diesem Gerät aktiv.'
               : 'Auf diesem Gerät aktiv. Wähle unten, wofür du Push willst, und speichere.', 'Push deaktivieren');
    } else {
      show('', 'Push aktivieren');
    }
  }

  function enable() {
    // Must run directly in the click handler: browsers only ask for permission after a user action
    return Notification.requestPermission().then(function (permission) {
      if (permission !== 'granted') return;
      return (current && !sameKey(current) ? current.unsubscribe() : Promise.resolve())
        .then(function () {
          return registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: serverKey()});
        })
        .then(function (subscription) {
          var keys = subscription.toJSON().keys;
          return post(box.dataset.subscribe, {endpoint: subscription.endpoint, p256dh: keys.p256dh, auth: keys.auth})
            .then(function () {
              current = subscription;
              known.push(subscription.endpoint);
            });
        });
    });
  }

  function disable() {
    var endpoint = current.endpoint;
    return post(box.dataset.unsubscribe, {endpoint: endpoint})
      .then(function () { return current.unsubscribe(); })
      .then(function () {
        known = known.filter(function (e) { return e !== endpoint; });
        current = null;
      });
  }

  button.addEventListener('click', function () {
    var active = current && known.indexOf(current.endpoint) >= 0;
    button.disabled = true;
    (active ? disable() : enable())
      .then(render, function () { show('Das hat nicht geklappt. Bitte lade die Seite neu und versuche es nochmals.'); })
      .then(function () { button.disabled = false; });
  });

  // pwa.js registers the service worker; push needs it to be active
  navigator.serviceWorker.ready.then(function (reg) {
    registration = reg;
    return reg.pushManager.getSubscription();
  }).then(function (subscription) {
    current = subscription;
    render();
  });
})();
