(() => {
  'use strict';
  const form = document.getElementById('registrationForm'); if (!form) return;
  const remember = document.getElementById('remember-registration'), clear = document.getElementById('clear-registration-draft');
  const key = `trilhas-registration-draft:${form.dataset.draftUser}:${form.dataset.draftEvent}`;
  const allowed = name => /^(purchaser_(name|email|phone|document)|participant_(name|document|email|phone|product|lot|shirt|team|city|state)\[\]|buyer_is_participant|coupon_code)$/.test(name);
  function save() {
    const fields = {};
    [...form.elements].filter(field => allowed(field.name)).forEach(field => {
      (fields[field.name] ||= []).push(field.type === 'checkbox' ? String(field.checked) : field.value.slice(0,320));
    });
    const value = JSON.stringify({fields, count:form.querySelectorAll('[data-participant]').length, expires:Date.now()+(remember.checked ? 7*86400000 : 8*3600000)});
    try {
      sessionStorage.setItem(key, value);
      if (remember.checked) localStorage.setItem(key, value); else localStorage.removeItem(key);
    } catch (_) {}
  }
  function restore() {
    try {
      const persistent=localStorage.getItem(key), value=sessionStorage.getItem(key)||persistent;
      if (!value || value.length>100000) return;
      const draft=JSON.parse(value);
      if (draft.expires<Date.now()) { localStorage.removeItem(key);sessionStorage.removeItem(key);return; }
      remember.checked=Boolean(persistent);
      if (form.dataset.draftBound==='true' || !draft.fields || draft.count>Number(form.dataset.maxParticipants)) return;
      while (form.querySelectorAll('[data-participant]').length<draft.count) document.getElementById('btnAddParticipant').click();
      const apply = () => Object.entries(draft.fields).forEach(([name,values]) => {
        if (!allowed(name) || !Array.isArray(values)) return;
        [...form.elements].filter(field=>field.name===name).forEach((field,index)=>{
          if (typeof values[index]!=='string') return;
          if (field.type==='checkbox') field.checked=values[index]==='true';
          else field.value=values[index].slice(0,320);
        });
      });
      apply(); form.querySelector('[name="participant_product[]"]')?.dispatchEvent(new Event('change',{bubbles:true}));
      apply(); document.getElementById('buyerIsParticipant')?.dispatchEvent(new Event('change',{bubbles:true}));
      document.getElementById('draft-status').textContent='Preenchimento anterior recuperado. Confira os dados antes de continuar.';
    } catch (_) {}
  }
  form.addEventListener('input',save);form.addEventListener('change',save);
  form.addEventListener('click',event=>{
    if(event.target.closest('#btnAddParticipant,[data-remove-participant]'))save();
  });
  clear.addEventListener('click',()=>{
    try {localStorage.removeItem(key);sessionStorage.removeItem(key);}catch(_){}
    remember.checked=false;
    document.getElementById('draft-status').textContent='Rascunho salvo removido deste dispositivo.';
  });
  if (document.readyState!=='complete') document.addEventListener('DOMContentLoaded',restore,{once:true}); else restore();
})();
