(() => {
  for (const button of document.querySelectorAll('[data-copy-manual-link]')) {
    button.addEventListener('click', async () => {
      const input = document.getElementById(button.dataset.copyManualLink);
      const status = document.getElementById('manual-copy-status');
      try {
        await navigator.clipboard.writeText(input.value);
        status.textContent = 'Link copiado. Envie somente ao responsável pela inscrição.';
      } catch {
        input.focus(); input.select();
        status.textContent = 'Link selecionado. Use Copiar no seu dispositivo.';
      }
    });
  }
  const form = document.querySelector('[data-manual-registration]');
  if (!form) return;
  const catalog = JSON.parse(document.getElementById('manual-catalog').textContent);
  const lot = form.querySelector('[name="lot"]');
  const shirt = form.querySelector('[name="shirt"]');
  const payment = form.querySelector('[name="payment_state"]');
  const received = form.querySelector('[data-received-fields]');
  const total = form.querySelector('[data-manual-total]');
  const format = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
  function refreshSizes() {
    const selected = catalog[lot.value];
    if (!selected) { total.textContent = 'Selecione o pacote para conferir o total.'; return; }
    const previous = shirt.value;
    shirt.replaceChildren();
    for (const size of selected.sizes) {
      const option = document.createElement('option');
      option.value = size.code;
      option.textContent = size.label + (size.extra ? ` (+ ${format.format(size.extra / 100)})` : '');
      shirt.append(option);
    }
    if (selected.sizes.some(size => size.code === previous)) shirt.value = previous;
    refreshTotal();
  }
  function refreshTotal() {
    const selected = catalog[lot.value];
    if (!selected) return;
    const size = selected.sizes.find(size => size.code === shirt.value);
    total.textContent = `Total da inscrição: ${format.format((selected.price + (size?.extra || 0)) / 100)}. Valor conferido novamente ao salvar.`;
  }
  function refreshPayment() {
    if (!received) return;
    received.hidden = payment.value !== 'received';
    received.style.display = received.hidden ? 'none' : '';
    for (const input of received.querySelectorAll('input, select, textarea')) input.disabled = received.hidden;
  }
  lot.addEventListener('change', refreshSizes);
  shirt.addEventListener('change', refreshTotal);
  payment.addEventListener('change', refreshPayment);
  refreshSizes(); refreshPayment();
})();
