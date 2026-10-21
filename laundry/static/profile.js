// Show a mismatch while typing; profile.index() checks it again on the server
(function () {
  var pw = document.getElementById('new_password');
  var again = document.getElementById('new_password2');
  var msg = document.getElementById('pw-mismatch');
  function check() {
    var text = again.value && again.value !== pw.value ? 'Die Passwörter stimmen nicht überein.' : '';
    again.setCustomValidity(text);
    msg.textContent = text;
  }
  pw.addEventListener('input', check);
  again.addEventListener('input', check);
})();

// Copy the calendar link; the button stays hidden where the clipboard isn't available (plain http)
(function () {
  var input = document.getElementById('feed-url');
  var button = document.getElementById('copy-feed-url');
  if (!input || !button) return;
  input.addEventListener('focus', function () { input.select(); });
  if (!navigator.clipboard) return;
  button.hidden = false;
  button.addEventListener('click', function () {
    navigator.clipboard.writeText(input.value).then(function () {
      button.textContent = 'Kopiert';
      setTimeout(function () { button.textContent = 'Kopieren'; }, 2000);
    });
  });
})();
