/* Lucide is served locally. Dynamic screens can call renderTrilhasIcons(). */
window.renderTrilhasIcons = function (root) {
    if (!window.lucide) return;
    window.lucide.createIcons({
        root: root || document,
        attrs: { 'aria-hidden': 'true', 'focusable': 'false', 'stroke-width': 1.8 }
    });
};
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.renderTrilhasIcons());
} else {
    window.renderTrilhasIcons();
}
