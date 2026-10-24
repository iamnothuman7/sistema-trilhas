(() => {
  const dialog = document.getElementById('payment-dialog');
  if (!dialog?.dataset.statusUrl) return;
  const feedback = document.getElementById('payment-feedback');
  const instructions = document.getElementById('pix-instructions');
  const payable = document.getElementById('pix-payable');
  const timer = document.getElementById('pix-countdown');
  const form = document.getElementById('refresh-payment');
  const button = form.querySelector('button');
  let serverNow = Date.parse(dialog.dataset.serverNow), syncedAt = performance.now();
  let expiresAt = Date.parse(instructions?.dataset.expires || '');
  let expired = false, checking = false, leaving = false, failures = 0, pollTimeout;
  let lastProviderCheck = -Infinity;
  const expiredMessage = 'O prazo do Pix terminou. Não pague este código. Se já pagou, continuamos verificando a confirmação.';
  function tick() {
    if (!timer || !Number.isFinite(expiresAt) || !Number.isFinite(serverNow)) return;
    const seconds = Math.max(0, Math.ceil((expiresAt - serverNow - (performance.now() - syncedAt)) / 1000));
    timer.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    document.getElementById('pix-countdown-panel').classList.toggle('is-ending', seconds <= 300);
    if (!seconds && !expired) { expired = true; payable.hidden = true; feedback.textContent = expiredMessage; }
  }
  function apply(data) {
    // Only the server's verified payment state can lead to a success screen.
    if (data.redirect_url) { leaving = true; window.location.assign(data.redirect_url); return; }
    const timestamp = Date.parse(data.server_time);
    if (Number.isFinite(timestamp)) { serverNow = timestamp; syncedAt = performance.now(); }
    if (data.pix_expires_at) expiresAt = Date.parse(data.pix_expires_at);
    tick();
    feedback.textContent = expired ? expiredMessage : (data.message || 'Aguardando a confirmação do Mercado Pago…');
  }
  async function check(manual = false) {
    if (checking || leaving) return;
    checking = true;
    const consultProvider = manual || performance.now() - lastProviderCheck >= 30000;
    if (consultProvider) lastProviderCheck = performance.now();
    if (manual) { button.disabled = true; button.textContent = 'Consultando pagamento…'; }
    try {
      const response = await fetch(consultProvider ? form.action : dialog.dataset.statusUrl,
        consultProvider ? {method: 'POST', body: new FormData(form), credentials: 'same-origin', cache: 'no-store'}
          : {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('unavailable');
      apply(await response.json()); failures = 0;
    } catch (_) {
      failures++;
      feedback.textContent = 'Não foi possível atualizar agora. Tentaremos novamente. Se já pagou, não faça outro pagamento.';
    } finally {
      checking = false; button.disabled = false; button.textContent = 'Já paguei. Verificar agora';
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); check(true); });
  document.getElementById('copy-pix')?.addEventListener('click', async function () {
    tick(); if (expired) return;
    const code = document.getElementById('pix-code');
    try { await navigator.clipboard.writeText(code.value); this.textContent = 'Código copiado'; }
    catch (_) { document.getElementById('pix-code-details').open = true; code.focus(); code.select(); feedback.textContent = 'Selecione e copie o código exibido acima.'; }
  });
  async function poll() {
    if (!document.hidden) await check();
    clearTimeout(pollTimeout);
    if (!leaving) pollTimeout = window.setTimeout(poll, document.hidden ? 30000 : failures ? 15000 : 5000);
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !leaving) { tick(); clearTimeout(pollTimeout); poll(); }
  });
  tick(); const clockInterval = window.setInterval(tick, 1000);
  pollTimeout = window.setTimeout(poll, 3000);
  window.addEventListener('pagehide', () => { leaving = true; clearTimeout(pollTimeout); clearInterval(clockInterval); });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();
