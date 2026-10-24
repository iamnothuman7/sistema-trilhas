(() => {
  const dialog = document.getElementById('payment-dialog');
  if (!dialog) return;
  const open = () => { if (!dialog.open) dialog.showModal(); };
  document.querySelectorAll('[data-open-payment]').forEach(button => button.addEventListener('click', open));
  dialog.querySelectorAll('[data-close-payment]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => document.querySelector('[data-open-payment]')?.focus());
  if (dialog.dataset.autoOpen !== 'false') open();
})();
