/* Small progressive enhancements: no scroll handlers, canvas or animation library. */
(() => {
  'use strict';
  const root = document.documentElement;
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  function update() {
    root.classList.toggle('motion-paused', preference.matches);
  }
  preference.addEventListener('change', update);
  document.addEventListener('visibilitychange', () => root.classList.toggle('page-inactive', document.hidden));
  update();
  if (!('IntersectionObserver' in window)) return;
  const reveals = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('trail-revealed');
      reveals.unobserve(entry.target);
    });
  }, { threshold: .12 });
  document.querySelectorAll('[data-trail-reveal]').forEach(element => reveals.observe(element));
  const stage = document.querySelector('.trail-hero');
  if (stage) {
    const visibility = new IntersectionObserver(entries => {
      root.classList.toggle('hero-offscreen', !entries[0].isIntersecting);
    });
    visibility.observe(stage);
  }
})();
