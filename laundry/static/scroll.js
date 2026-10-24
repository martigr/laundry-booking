// Keep the scroll position when a form sends you back to the same page: calendar forms (they
// carry `next`: book, cancel, note) and forms inside [data-keep-scroll] (the profile).
// The redirect would otherwise start at the top, which on a phone means scrolling down again.
// The flash message then floats at the top of the screen instead of sitting off-screen.
// Without JavaScript (or sessionStorage) the page simply starts at the top as before.
(function () {
  var KEY = 'laundry-scroll';
  function here() { return location.pathname + location.search; }

  // On window, so this runs after confirm.js (on document) had its chance to cancel the submit
  window.addEventListener('submit', function (e) {
    var form = e.target;
    if (e.defaultPrevented || form.method !== 'post') return;
    if (!form.querySelector('input[name="next"]') && !form.closest('[data-keep-scroll]')) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ path: here(), y: window.scrollY }));
    } catch (err) { /* private mode or storage blocked: nothing to restore */ }
  });

  var saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY));
    sessionStorage.removeItem(KEY);
  } catch (err) { return; }
  if (!saved || saved.path !== here() || !(saved.y > 0)) return;

  window.scrollTo(0, saved.y);
  var flashes = document.querySelector('.flashes');
  if (!flashes || !flashes.children.length) return;
  flashes.classList.add('floating');
  function hide() { flashes.classList.add('gone'); }
  flashes.addEventListener('click', hide);
  setTimeout(hide, 5000);
})();
