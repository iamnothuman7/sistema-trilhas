/* Workspace drafts stay in this tab's memory, never in cookies or persistent storage. */
(() => {
  'use strict';
  if (window.trilhasWorkspace) return;
  const pages = new Map(), tabs = new Map();
  let hooks = new Map(), current = null, restoring = false, skipCapture = false;
  const root = () => document.getElementById('mainContent');
  const sensitive = /password|senha|token|secret|credential|public_key|csrf|confirm|aceite/i;
  const canonical = value => { const url = new URL(value, location.href); url.hash = ''; url.searchParams.sort(); return url.pathname + url.search; };
  function context() {
    const main = root();
    if (!main?.dataset.workspaceUser) return null;
    return {user: main.dataset.workspaceUser, event: main.dataset.workspaceEvent,
      module: main.dataset.workspaceModule, url: main.dataset.workspaceUrl || location.pathname + location.search};
  }
  const pageKey = ctx => `${ctx.user}|${canonical(ctx.url)}`;
  const tabKey = (ctx, module = ctx.module) => `${ctx.user}|${ctx.event}|${module}`;
  function controls() {
    return [...(root()?.querySelectorAll('input,select,textarea') || [])].filter(el =>
      !el.readOnly && !['hidden','password','submit','button','reset'].includes(el.type) &&
      !sensitive.test(el.name + ' ' + el.id) && !el.closest('.management-event-switcher,[data-workspace-private]'));
  }
  function controlKey(el, index) { return el.id ? `id:${el.id}` : `${el.form?.getAttribute('action') || ''}|${el.name}|${index}`; }
  function valueOf(el) {
    if (el.type === 'file') return {files: [...el.files]};
    if (['checkbox','radio'].includes(el.type)) return {checked:el.checked};
    if (el.multiple) return {values:[...el.selectedOptions].map(o => o.value)};
    return {value:el.value};
  }
  function changed(el) {
    if (!el.form || el.form.method.toLowerCase() === 'get') return true;
    if (el.type === 'file') return Boolean(el.files.length);
    if (['checkbox','radio'].includes(el.type)) return el.checked !== el.defaultChecked;
    if (el.tagName === 'SELECT') return [...el.options].some(o => o.selected !== o.defaultSelected);
    return el.value !== el.defaultValue;
  }
  function capture() {
    if (restoring || skipCapture || !current || !root()) return;
    const fields = new Map(), versions = [];
    controls().forEach((el, index) => { if (changed(el)) fields.set(controlKey(el,index), valueOf(el)); });
    for (const [index, form] of [...root().querySelectorAll('form')].entries()) {
      if (form.method.toLowerCase() !== 'post' || ![...form.elements].some(el => fields.has(controlKey(el,controls().indexOf(el))))) continue;
      const version = form.querySelector('input[name="version"]');
      if (version) versions.push({index,value:version.value}); // Preserve optimistic locking with the draft.
    }
    const custom = new Map();
    hooks.forEach((hook,name) => { try { custom.set(name,hook.capture()); } catch (_) {} });
    const state = {fields,versions,custom,scrollY:scrollY,scrollX:scrollX,
      details:[...root().querySelectorAll('details')].map(el => el.open),
      dialogs:[...root().querySelectorAll('dialog[open]')].map(el => el.id),
      tables:[...root().querySelectorAll('.table-responsive')].map(el => ({x:el.scrollLeft,y:el.scrollTop})),
      sidebar:document.querySelector('.sidebar-nav')?.scrollTop || 0};
    const key = pageKey(current); pages.delete(key); pages.set(key,state);
    tabs.set(tabKey(current),current.url);
    while (pages.size > 24) pages.delete(pages.keys().next().value);
  }
  function applyFields(state) {
    restoring = true;
    controls().forEach((el,index) => {
      const value = state.fields.get(controlKey(el,index)); if (!value) return;
      if (value.files) { try { const transfer = new DataTransfer(); value.files.forEach(file=>transfer.items.add(file)); el.files=transfer.files; } catch (_) {} }
      else if ('checked' in value) el.checked=value.checked;
      else if (value.values) [...el.options].forEach(o=>o.selected=value.values.includes(o.value));
      else el.value=value.value;
      // Derived totals/visibility update; auto-submit controls must not submit on restore.
      if (!el.hasAttribute('data-auto-submit')) {
        el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
      }
    });
    state.versions.forEach(({index,value}) => { const field=root().querySelectorAll('form')[index]?.querySelector('input[name="version"]'); if(field)field.value=value; });
    [...root().querySelectorAll('details')].forEach((el,i)=>{ if(i<state.details.length)el.open=state.details[i]; });
    state.dialogs.forEach(id=>{const el=document.getElementById(id);if(el?.tagName==='DIALOG'&&!el.open)el.showModal();});
    [...root().querySelectorAll('.table-responsive')].forEach((el,i)=>{const p=state.tables[i];if(p){el.scrollLeft=p.x;el.scrollTop=p.y;}});
    restoring = false;
  }
  function mount() {
    const next = context();
    if (!next) { current=null; hooks.clear(); pages.clear(); tabs.clear(); return; }
    if (current && current.user !== next.user) { pages.clear();tabs.clear();hooks.clear(); }
    current=next;
    const state=pages.get(pageKey(current));
    if (state) {
      applyFields(state);
      hooks.forEach((hook,name)=>{if(state.custom.has(name))hook.restore(state.custom.get(name));});
      requestAnimationFrame(()=>{
        window.scrollTo(state.scrollX,state.scrollY);
        const nav=document.querySelector('.sidebar-nav');if(nav)nav.scrollTop=state.sidebar;
      });
    }
    tabs.set(tabKey(current),current.url);
  }
  window.trilhasWorkspace = {
    register(name,hook) {
      hooks.set(name,hook);
      const ctx=context(),state=ctx&&pages.get(pageKey(ctx));
      if (state?.custom.has(name)) hook.restore(state.custom.get(name));
    },
    clear() { pages.clear();tabs.clear();hooks.clear(); },
  };
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');if(!link)return;
    if(new URL(link.href).pathname==='/logout/')window.trilhasWorkspace.clear();
    if(!current||!link.dataset.workspaceTab||event.ctrlKey||event.metaKey||event.shiftKey||event.button>0)return;
    capture();
    const saved=tabs.get(tabKey(current,link.dataset.workspaceTab));
    if(saved)link.href=saved;
  },true);
  document.addEventListener('htmx:beforeRequest',event=>{
    if(event.detail.target===document.body)capture();
  });
  document.addEventListener('htmx:configRequest',event=>{
    const link=event.detail.elt;
    // Boosted links remember their original href; use the tab's current destination.
    if(link?.dataset.workspaceTab&&event.detail.verb==='get'){
      const url=new URL(link.href,location.href);
      if(url.origin===location.origin)event.detail.path=url.pathname+url.search;
    }
  });
  document.addEventListener('htmx:beforeOnLoad',event=>{
    const {xhr,requestConfig}=event.detail;
    if(requestConfig?.verb!=='get'&&xhr.status>=200&&xhr.status<300&&xhr.getResponseHeader('HX-Location')){
      if(current){pages.delete(pageKey(current));tabs.delete(tabKey(current));}
      skipCapture=true;
    }
  });
  document.addEventListener('htmx:beforeSwap',event=>{
    if(event.detail.shouldSwap&&event.detail.target===document.body){
      capture();hooks=new Map();skipCapture=false;
      // Server validation errors must remain authoritative for the submitted form.
      if(event.detail.xhr.status>=400&&current)pages.delete(pageKey(current));
    }
  });
  document.addEventListener('htmx:afterSettle',event=>{if(event.detail.target===document.body)mount();});
  document.addEventListener('htmx:historyRestore',mount);
  window.addEventListener('popstate',capture);
  document.addEventListener('DOMContentLoaded',mount,{once:true});
})();
