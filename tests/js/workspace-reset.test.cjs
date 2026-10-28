const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../frontend/static/js/management-state.js'),'utf8');
function workspace(){
 const listeners=new Map(),nav={scrollTop:0};let main;
 const body={};
 const document={body,getElementById:()=>main,querySelector:s=>s==='.sidebar-nav'?nav:null,
  addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);}};
 const location={href:'https://local.example/gestao/eventos/1/editar/',origin:'https://local.example',pathname:'/gestao/eventos/1/editar/'};
 const window={addEventListener(){},scrollTo(){}};
 vm.runInNewContext(source,{document,window,location,URL,Map,Event:class{constructor(type){this.type=type}},scrollY:0,scrollX:0,requestAnimationFrame:fn=>fn()});
 function emit(name,event={}){for(const fn of listeners.get(name)||[])fn(event);}
 function page(url,values,method='post',version='v1'){
  const versionField={value:version};const form={getAttribute:name=>name==='method'?method:null,querySelector:()=>versionField};
  const fields=Object.entries(values).map(([id,value])=>({id,name:id,type:'text',value,defaultValue:value,readOnly:false,form,closest:()=>null,dispatchEvent(){},hasAttribute:()=>false}));
  form.elements=fields;
  main={dataset:{workspaceUser:'owner',workspaceEvent:'1',workspaceModule:'event_edit',workspaceUrl:url},
   querySelectorAll:selector=>selector==='input,select,textarea'?fields:selector==='form'?[form]:[]};
  location.href=location.origin+url;location.pathname=url.split('?')[0];
  return {fields,versionField};
 }
 function swap(url,values,options={}){
  emit('htmx:beforeRequest',{detail:{target:body}});
  emit('htmx:beforeSwap',{detail:{target:body,shouldSwap:true,xhr:{status:200}}});
  const result=page(url,values,options.method||'post',options.version||'v2');
  emit('htmx:afterSettle',{detail:{target:body}});return result;
 }
 function reset(url){const link={href:location.origin+url,hasAttribute:name=>name==='data-workspace-reset',dataset:{}};
  emit('click',{target:{closest:()=>link},button:0});}
 return {page,swap,reset,emit};
}
test('ordinary tab navigation preserves the draft and its original optimistic version',()=>{
 const w=workspace(),form=w.page('/gestao/eventos/1/editar/',{title:'Original'});w.emit('DOMContentLoaded');form.fields[0].value='Rascunho';
 w.swap('/gestao/eventos/1/equipe/',{name:'Equipe'});
 const restored=w.swap('/gestao/eventos/1/editar/',{title:'Atualizado no servidor'},{version:'v2'});
 assert.equal(restored.fields[0].value,'Rascunho');assert.equal(restored.versionField.value,'v1');
});
test('explicit reopen discards stale draft and version before rendering fresh server data',()=>{
 const w=workspace(),form=w.page('/gestao/eventos/1/editar/',{title:'Original'});w.emit('DOMContentLoaded');form.fields[0].value='Rascunho antigo';
 w.reset('/gestao/eventos/1/editar/');const fresh=w.swap('/gestao/eventos/1/editar/',{title:'Alterado em outra aba'},{version:'v2'});
 assert.equal(fresh.fields[0].value,'Alterado em outra aba');assert.equal(fresh.versionField.value,'v2');
});
test('clear filters discards both the filtered page and the earlier unfiltered draft',()=>{
 const w=workspace(),form=w.page('/gestao/eventos/1/relatorios/',{date_start:''},'get');w.emit('DOMContentLoaded');form.fields[0].value='2026-10-01';
 w.swap('/gestao/eventos/1/relatorios/?date_start=2026-10-01',{date_start:'2026-10-01'},{method:'get'});
 w.reset('/gestao/eventos/1/relatorios/');const cleared=w.swap('/gestao/eventos/1/relatorios/',{date_start:''},{method:'get'});
 assert.equal(cleared.fields[0].value,'');
});
test('a failed explicit reload does not disable future draft capture',()=>{
 const w=workspace(),form=w.page('/gestao/eventos/1/editar/',{title:'Original'});w.emit('DOMContentLoaded');w.reset('/gestao/eventos/1/editar/');
 w.emit('htmx:afterRequest',{detail:{failed:true}});form.fields[0].value='Preservado após falha';
 w.swap('/gestao/eventos/1/equipe/',{name:'Equipe'});
 const restored=w.swap('/gestao/eventos/1/editar/',{title:'Original'});
 assert.equal(restored.fields[0].value,'Preservado após falha');
});
