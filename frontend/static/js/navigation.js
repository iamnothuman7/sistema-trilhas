(() => {
  'use strict';
  if (window.trilhasNavigationReady) return;
  window.trilhasNavigationReady = true;
  let cleanups = [];
  window.trilhasOnPageLeave = cleanup => cleanups.push(cleanup);
  function clearDrafts(eventId) {
    ['localStorage','sessionStorage'].forEach(name=>{
      try {const storage=window[name];Object.keys(storage).filter(key=>key.startsWith('trilhas-registration-draft:')&&(!eventId||key.endsWith(':'+eventId))).forEach(key=>storage.removeItem(key));}catch(_){}
    });
    if (!eventId) {try {indexedDB.deleteDatabase('trilhas-photo-drafts');}catch(_){}}
  }
  function clearCompletedDraft() {const marker=document.querySelector('[data-clear-registration-draft]');if(marker)clearDrafts(marker.dataset.clearRegistrationDraft);}
  document.addEventListener('DOMContentLoaded',clearCompletedDraft);
  const nativePath = path => (path.startsWith('/gestao/')
    ? /\/(exportar|documentos)(\/|$)/.test(path)
    : /^\/(checkout|login|logout|cadastro|compra|recuperar-senha|senha-atualizada|admin|api|integracoes)(\/|$)/.test(path)
    || /\/(credenciamento|molduras|equipes-confirmadas|mercadopago|mercado-pago|exportar|documentos|comprovante)(\/|$)/.test(path)
    ) || /\.(pdf|csv|zip|png|jpe?g|webp)$/i.test(path);
  function prepare(root = document.body) {
    if (!root?.querySelectorAll) return;
    const nativePage = nativePath(location.pathname);
    const elements=[...root.querySelectorAll('a[href],form')];
    if(root.matches?.('a[href],form'))elements.unshift(root);
    elements.forEach(element => {
      const isForm = element.tagName === 'FORM';
      const url = new URL(isForm ? element.getAttribute('action') || location.href : element.getAttribute('href'), location.href);
      if (nativePage || url.origin !== location.origin || nativePath(url.pathname)
          || url.searchParams.has('export') || (isForm && element.querySelector('[name="export"]'))
          || element.hasAttribute('download') || element.target === '_blank') {
        element.setAttribute('hx-boost', 'false');
      }
      if (isForm && element.enctype === 'multipart/form-data') element.setAttribute('hx-encoding', 'multipart/form-data');
      if (!nativePage && location.pathname.startsWith('/gestao/') && isForm && element.method.toLowerCase() === 'get') element.setAttribute('hx-push-url', 'true');
    });
  }
  document.addEventListener('htmx:beforeProcessNode', event => prepare(event.detail.elt));
  document.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.requestConfig?.boosted) document.documentElement.classList.add('is-navigating');
  });
  document.addEventListener('htmx:beforeSwap', event => {
    if ([400, 422].includes(event.detail.xhr.status)) { event.detail.shouldSwap = true; event.detail.isError = false; }
    if (event.detail.shouldSwap && event.detail.target === document.body) {
      cleanups.forEach(cleanup => { try { cleanup(); } catch (_) {} }); cleanups = [];
      document.body.className = '';
    }
  });
  document.addEventListener('htmx:afterRequest', () => document.documentElement.classList.remove('is-navigating'));
  function failure() {
    document.documentElement.classList.remove('is-navigating');
    let notice = document.getElementById('navigation-feedback');
    if (!notice) { notice = document.createElement('div'); notice.id = 'navigation-feedback'; notice.className = 'navigation-feedback'; notice.setAttribute('role', 'alert'); document.body.appendChild(notice); }
    notice.textContent = 'Não foi possível concluir. Confira sua conexão e tente novamente.';
    setTimeout(() => notice.remove(), 8000);
  }
  ['htmx:sendError','htmx:timeout','htmx:responseError'].forEach(name => document.addEventListener(name, failure));
  document.addEventListener('htmx:load', event => {
    prepare(event.detail.elt); window.renderTrilhasIcons?.();
    clearCompletedDraft();
    document.querySelector('[autofocus]')?.focus({preventScroll:true});
  });
  // Public navigation uses delegation so links and buttons work after a swap.
  function closeMenu() {
    const menu=document.getElementById('mobileMenuDrawer'), button=document.getElementById('mobileMenuBtn');
    if (menu) menu.hidden=true;
    button?.setAttribute('aria-expanded','false'); button?.setAttribute('aria-label','Abrir menu');
  }
  document.addEventListener('click', event => {
    const link=event.target.closest('a[href]');
    if (link && new URL(link.href,location.href).pathname==='/logout/') clearDrafts();
    const button=event.target.closest('#mobileMenuBtn'); if (!button) return;
    const menu=document.getElementById('mobileMenuDrawer'); if (!menu) return;
    if (!menu.hidden) { closeMenu(); return; }
    menu.hidden=false; button.setAttribute('aria-expanded','true'); button.setAttribute('aria-label','Fechar menu');
    menu.style.setProperty('--mobile-menu-viewport-top',Math.max(0,menu.getBoundingClientRect().top)+'px');
  });
  document.addEventListener('keydown', event => { if (event.key==='Escape') closeMenu(); });
  window.addEventListener('resize', () => { if (innerWidth >= 961) closeMenu(); });
  prepare();
})();
