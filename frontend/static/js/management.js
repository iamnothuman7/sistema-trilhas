(function () {
    const sidebar = document.getElementById('managementSidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    const toggles = document.querySelectorAll('[data-menu-toggle]');
    const mobile = window.matchMedia('(max-width: 767px)');
    let opener = null;
    function closeMenu() {
        sidebar.classList.remove('is-open');
        backdrop.classList.remove('is-visible');
        document.body.classList.remove('menu-open');
        toggles.forEach(button => button.setAttribute('aria-expanded', 'false'));
        if (mobile.matches) sidebar.setAttribute('inert', '');
        if (opener) { opener.focus(); opener = null; }
    }
    function openMenu(button) {
        opener = button;
        sidebar.removeAttribute('inert');
        sidebar.classList.add('is-open');
        backdrop.classList.add('is-visible');
        document.body.classList.add('menu-open');
        toggles.forEach(control => control.setAttribute('aria-expanded', 'true'));
        sidebar.querySelector('a').focus();
    }
    toggles.forEach(button => button.addEventListener('click', () => sidebar.classList.contains('is-open') ? closeMenu() : openMenu(button)));
    backdrop.addEventListener('click', closeMenu);
    sidebar.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { if (mobile.matches) closeMenu(); }));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            if (sidebar.classList.contains('is-open')) closeMenu();
            document.querySelectorAll('.user-menu[open]').forEach(menu => menu.removeAttribute('open'));
        }
        if (event.key === 'Tab' && mobile.matches && sidebar.classList.contains('is-open')) {
            const focusables = [...sidebar.querySelectorAll('a,button')].filter(el => el.getClientRects().length);
            const first = focusables[0], last = focusables[focusables.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    function adaptMenu() {
        closeMenu();
        if (!mobile.matches) sidebar.removeAttribute('inert');
    }
    mobile.addEventListener('change', adaptMenu);
    if (mobile.matches) sidebar.setAttribute('inert', '');
    const pin = document.getElementById('sidebarPin');
    try { if (localStorage.getItem('morcego-sidebar-pinned') === 'true') sidebar.classList.add('is-expanded'); } catch (_) {}
    pin.setAttribute('aria-pressed', String(sidebar.classList.contains('is-expanded')));
    pin.addEventListener('click', () => {
        const expanded = sidebar.classList.toggle('is-expanded');
        pin.setAttribute('aria-pressed', String(expanded));
        try { localStorage.setItem('morcego-sidebar-pinned', String(expanded)); } catch (_) {}
    });
    const theme = document.getElementById('themeToggle');
    function themeLabel() {
        const dark = document.documentElement.dataset.theme === 'dark';
        theme.setAttribute('aria-label', dark ? 'Ativar tema claro' : 'Ativar tema escuro');
        theme.setAttribute('aria-pressed', String(dark));
        theme.innerHTML = '<i data-lucide="' + (dark ? 'sun' : 'moon') + '"></i>';
        window.renderTrilhasIcons(theme);
    }
    theme.addEventListener('click', () => {
        const value = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = value;
        try { localStorage.setItem('morcego-theme', value); } catch (_) {}
        themeLabel();
    });
    themeLabel();
    document.addEventListener('click', event => document.querySelectorAll('.user-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.removeAttribute('open'); }));
    document.querySelectorAll('[data-auto-submit]').forEach(select => select.addEventListener('change', () => select.form.requestSubmit()));
})();
