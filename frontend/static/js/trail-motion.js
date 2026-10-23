/* Motion stays local to visible sections; no scroll handlers or animation library. */
(() => {
  'use strict';
  const root = document.documentElement;
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const countdowns = [...document.querySelectorAll('[data-trail-countdown]')];
  let timer;
  const updatePreference = () => root.classList.toggle('motion-paused', preference.matches);
  preference.addEventListener('change', updatePreference);
  updatePreference();

  function updateCountdowns() {
    clearTimeout(timer);
    if (document.hidden) return;
    let pending = false;
    countdowns.forEach(element => {
      const end = Date.parse(element.dataset.trailCountdown);
      const remaining = end - Date.now();
      if (!Number.isFinite(end) || remaining <= 0) {
        element.hidden = true;
        return;
      }
      pending = true;
      const minutes = Math.floor(remaining / 60000);
      element.querySelector('[data-days]').textContent = String(Math.floor(minutes / 1440)).padStart(2, '0');
      element.querySelector('[data-hours]').textContent = String(Math.floor(minutes / 60) % 24).padStart(2, '0');
      element.querySelector('[data-minutes]').textContent = String(minutes % 60).padStart(2, '0');
      element.hidden = false;
    });
    if (pending) timer = setTimeout(updateCountdowns, 60000);
  }
  document.addEventListener('visibilitychange', () => {
    root.classList.toggle('page-inactive', document.hidden);
    updateCountdowns();
  });
  root.classList.toggle('page-inactive', document.hidden);
  updateCountdowns();

  if (!('IntersectionObserver' in window)) return;
  const reveals = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('trail-revealed');
      reveals.unobserve(entry.target);
    });
  }, { threshold: .08 });
  document.querySelectorAll('[data-trail-reveal]').forEach(element => reveals.observe(element));
  const motion = new IntersectionObserver(entries => {
    entries.forEach(entry => entry.target.classList.toggle('motion-outside', !entry.isIntersecting));
  }, { rootMargin: '80px' });
  document.querySelectorAll('[data-trail-motion], .trail-hero, .trail-featured-event').forEach(element => motion.observe(element));
})();
