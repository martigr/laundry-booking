// Styled confirmation dialog for forms with a data-confirm attribute.
// Attributes: data-confirm (question), data-confirm-title, data-confirm-ok (button text),
// data-confirm-danger (red button). Without JavaScript the form submits directly;
// the server checks every permission anyway.
(function () {
  var dialog = document.getElementById('confirm-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') {
    document.addEventListener('submit', function (e) {
      var msg = e.target.getAttribute('data-confirm');
      if (msg && !window.confirm(msg)) e.preventDefault();
    });
    return;
  }
  var title = document.getElementById('confirm-title');
  var text = document.getElementById('confirm-text');
  var ok = document.getElementById('confirm-ok');
  var pending = null;

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.hasAttribute('data-confirm') || form === pending) return;
    e.preventDefault();
    pending = form;
    title.textContent = form.getAttribute('data-confirm-title') || 'Bist du sicher?';
    text.textContent = form.getAttribute('data-confirm');
    ok.textContent = form.getAttribute('data-confirm-ok') || 'OK';
    ok.className = 'btn ' + (form.hasAttribute('data-confirm-danger') ? 'btn-danger' : 'btn-primary');
    dialog.returnValue = '';
    dialog.showModal();
    ok.focus();
  });

  dialog.addEventListener('close', function () {
    var form = pending;
    if (form && dialog.returnValue === 'ok') {
      // requestSubmit() fires the submit event again (scroll.js saves the position), which this
      // listener lets through because the form is still `pending`; submit() for older browsers
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }
    pending = null;
  });

  // Click on the backdrop closes the dialog like "Abbrechen"
  dialog.addEventListener('click', function (e) {
    if (e.target === dialog) dialog.close();
  });
})();
