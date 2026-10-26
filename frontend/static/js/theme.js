/* Apply the saved preference before paint; default to the team's dark theme. */
(function () {
    'use strict';
    const key = 'morcego-theme';
    const root = document.documentElement;
    function apply(value) {
        const theme = value === 'light' ? 'light' : 'dark';
        root.dataset.theme = theme;
        root.style.colorScheme = theme;
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f5f1ec' : '#171411');
        document.querySelectorAll('[data-theme-toggle]').forEach(button => {
            const label = theme === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro';
            button.setAttribute('aria-label', label);
            button.setAttribute('title', label);
            button.setAttribute('aria-pressed', String(theme === 'light'));
        });
    }
    let saved;
    try { saved = localStorage.getItem(key); } catch (_) { /* Private mode still supports a session preference. */ }
    saved ||= document.cookie.split('; ').find(item => item.startsWith('morcego_theme='))?.split('=')[1];
    apply(saved);
    document.addEventListener('DOMContentLoaded', () => apply(root.dataset.theme));
    document.addEventListener('htmx:load', () => apply(root.dataset.theme));
    document.addEventListener('click', event => {
        if (!event.target.closest('[data-theme-toggle]')) return;
        const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply(next);
        try { localStorage.setItem(key, next); } catch (_) {}
        document.cookie = `morcego_theme=${next}; Max-Age=31536000; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    });
    window.addEventListener('storage', event => {
        if (event.key === key) apply(event.newValue);
    });
}());
