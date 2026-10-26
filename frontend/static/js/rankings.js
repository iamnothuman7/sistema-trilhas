/* Small aggregate snapshot once per minute while visible; no participant records. */
(() => {
  'use strict';
  const root = document.querySelector('[data-ranking-url]');
  if (!root) return;
  const cards = Array.from(root.querySelectorAll('.event-ranking-card'));
  let visible = true, running = false, timer;
  const status = document.createElement('p');
  status.className = 'event-ranking-status';
  status.textContent = 'Atualização automática a cada minuto enquanto esta seção estiver visível.';
  root.appendChild(status);
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => { visible = entries[0].isIntersecting; }) : null;
  observer?.observe(root);
  function node(tag, className, text) {
    const item = document.createElement(tag); item.className = className; item.textContent = text; return item;
  }
  function render(rows, card, kind, total) {
    const list = card.querySelector('ol');
    const fragment = document.createDocumentFragment();
    rows.slice(0, 10).forEach(row => {
      if (typeof row.name !== 'string' || !Number.isSafeInteger(row.count) || row.count < 1 || !Number.isSafeInteger(row.rank)) return;
      const item = node('li', '', '');
      item.appendChild(node('span', 'event-ranking-position', row.rank + 'º'));
      const name = node('span', 'event-ranking-name', row.name);
      if (kind === 'city') name.appendChild(node('small', '', row.state || 'UF não informada'));
      item.appendChild(name);
      const count = node('span', 'event-ranking-count', String(row.count));
      count.appendChild(node('small', '', row.count === 1 ? 'inscrição' : 'inscrições'));
      item.appendChild(count); fragment.appendChild(item);
    });
    if (!fragment.childNodes.length) fragment.appendChild(node('li', 'event-ranking-empty', 'As confirmações aparecerão aqui após a aprovação das inscrições e dos pagamentos.'));
    list.replaceChildren(fragment);
    const label = kind === 'city' ? 'cidade' : 'equipe';
    card.querySelector('header > span').textContent = total + ' ' + label + (total === 1 ? '' : 's');
    let note = card.querySelector('.event-ranking-note');
    if (total > rows.length) {
      if (!note) { note = node('p', 'event-ranking-note', ''); card.appendChild(note); }
      note.textContent = 'As ' + rows.length + ' ' + label + 's com mais inscrições confirmadas.';
    } else if (note) note.remove();
  }
  async function update() {
    if (document.hidden || !visible || running) return;
    running = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(root.dataset.rankingUrl, {credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (!Array.isArray(data.teams) || !Array.isArray(data.cities)) throw new Error('invalid');
      render(data.teams,cards[0],'team',data.team_group_count);
      render(data.cities,cards[1],'city',data.city_group_count);
      status.textContent = 'Atualizado às ' + new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit'}).format(new Date()) + '. Somente inscrições confirmadas.';
    } catch (_) { status.textContent = 'Os últimos totais permanecem visíveis. A atualização será tentada novamente.'; }
    finally { window.clearTimeout(timeout); running = false; }
  }
  function start() { window.clearInterval(timer); timer = window.setInterval(update, 60000); }
  start();
  const lifecycle = new AbortController();
  window.trilhasOnPageLeave?.(() => { lifecycle.abort(); window.clearInterval(timer); observer?.disconnect(); });
  window.addEventListener('pageshow', start, {signal:lifecycle.signal});
  window.addEventListener('pagehide', () => window.clearInterval(timer), {signal:lifecycle.signal});
})();
