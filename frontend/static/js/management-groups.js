/* Opening a group uses its rendered content; no request or financial data cache. */
(() => {
  'use strict';
  if (window.trilhasInstantGroups) return;
  const selections = new Map();
  let user = null;
  const main = () => document.getElementById('mainContent');
  const boards = () => [...document.querySelectorAll('[data-instant-groups]')];
  const keyFor = board => `${main()?.dataset.workspaceUser || ''}|${board.dataset.instantGroups}|${board.dataset.groupScope || ''}|${board.dataset.groupSelected || 'overview'}`;
  const own = (board, selector) => [...board.querySelectorAll(selector)].filter(el => el.closest('[data-instant-groups]') === board);
  function filter(board, selected) {
    const values = board.dataset.instantGroups === 'sponsors' ? {tier: selected || 'ALL'} : {
      status: ['PENDING', 'PAID', 'CANCELED', 'ALL'].includes(selected) ? selected : null,
      panel: ['LEDGER', 'HISTORY'].includes(selected) ? 'ledger' : null,
      ledger_mode: selected === 'HISTORY' ? 'history' : null,
    };
    main()?.querySelectorAll('[data-group-filter]').forEach(form => {
      if (form.dataset.groupFilter !== board.dataset.instantGroups) return;
      Object.entries(values).forEach(([name, value]) => {
        let field = [...form.querySelectorAll('input[type="hidden"]')].find(input => input.name === name);
        if (value === null) { field?.remove(); return; }
        if (!field) { field = document.createElement('input'); field.type = 'hidden'; field.name = name; form.appendChild(field); }
        field.value = value;
      });
    });
  }
  function select(board, selected, remember = true, immediate = false) {
    const panels = own(board, '[data-group-panel]');
    if (!panels.some(panel => panel.dataset.groupPanel === selected)) selected = '';
    own(board, '[data-group-toggle]').forEach(button => {
      const open = button.dataset.groupToggle === selected;
      button.setAttribute('aria-expanded', String(open));
      button.classList.toggle('is-expanded', open);
      const label = button.querySelector('[data-group-label]');
      if (label) {
        if (!label.dataset.collapsedLabel) label.dataset.collapsedLabel = label.dataset.groupCollapsedLabel || label.textContent.trim();
        label.textContent = open ? 'Recolher' : label.dataset.collapsedLabel;
      }
    });

    panels.forEach(panel => {
      const willOpen = panel.dataset.groupPanel === selected;
      if (immediate) {
        panel.hidden = !willOpen;
        panel.classList.toggle('is-open', willOpen);
      } else {
        if (willOpen) {
          panel.hidden = false;
          panel.classList.remove('is-open');
          void panel.offsetHeight; // Force reflow
          requestAnimationFrame(() => {
            panel.classList.add('is-open');
          });
        } else if (panel.classList.contains('is-open')) {
          panel.classList.remove('is-open');
          setTimeout(() => {
            if (!panel.classList.contains('is-open')) {
              panel.hidden = true;
            }
          }, 360);
        } else {
          panel.hidden = true;
          panel.classList.remove('is-open');
        }
      }
    });

    board.dataset.groupOpen = selected;
    filter(board, selected);
    if (remember) {
      const key = keyFor(board); selections.delete(key); selections.set(key, selected);
      while (selections.size > 48) selections.delete(selections.keys().next().value);
    }
  }
  function init() {
    const nextUser = main()?.dataset.workspaceUser || null;
    if (user !== nextUser) { selections.clear(); user = nextUser; }
    boards().forEach(board => {
      if (board.dataset.groupsReady) return;
      board.dataset.groupsReady = 'true';
      const labelled = new Set();
      own(board, '[data-group-toggle]').sort((a, b) => Number(Boolean(a.closest('[data-group-panel]'))) - Number(Boolean(b.closest('[data-group-panel]')))).forEach((button, index) => {
        const panel = own(board, '[data-group-panel]').find(item => item.dataset.groupPanel === button.dataset.groupToggle);
        if (!panel) return;
        if (!button.id) button.id = `${panel.id}-toggle-${index}`;
        button.setAttribute('aria-controls', panel.id);
        panel.setAttribute('role', 'region');
        if (!labelled.has(panel.id)) { panel.setAttribute('aria-labelledby', button.id); labelled.add(panel.id); }
      });
      const key = keyFor(board);
      select(board, selections.has(key) ? selections.get(key) : board.dataset.groupSelected || '', false, true);
    });
    window.trilhasWorkspace?.register('instant-groups', {
      capture: () => boards().map(board => [keyFor(board), board.dataset.groupOpen || '']),
      restore: saved => {
        const state = new Map(saved || []);
        boards().forEach(board => { if (state.has(keyFor(board))) select(board, state.get(keyFor(board)), false, true); });
      },
    });
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-group-toggle],[data-group-close]');
    const board = button?.closest('[data-instant-groups]');
    if (!board) return;
    event.preventDefault();
    const previous = board.dataset.groupOpen || '';
    const close = button.hasAttribute('data-group-close');
    const selected = close || previous === button.dataset.groupToggle ? '' : button.dataset.groupToggle;
    select(board, selected);
    if (close || button.closest('[data-group-panel]')?.hidden) {
      const focusKey = close ? previous : selected;
      own(board, '[data-group-toggle]').find(item => item.dataset.groupToggle === focusKey && !item.closest('[data-group-panel]'))?.focus({preventScroll: true});
    }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (link && new URL(link.href, location.href).pathname === '/logout/') selections.clear();
    if (link?.hasAttribute('data-workspace-reset')) boards().forEach(board => selections.delete(keyFor(board)));
  }, true);
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:load', init);
  document.addEventListener('htmx:historyRestore', init);
  window.trilhasInstantGroups = {init};
  if (document.readyState !== 'loading') init();
})();
