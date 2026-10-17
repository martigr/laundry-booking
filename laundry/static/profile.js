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
