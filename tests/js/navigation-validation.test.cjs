const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../frontend/static/js/navigation.js'),'utf8');
function navigation(){
 const listeners=new Map();
 const body={className:'management-page',querySelectorAll:()=>[]};
 const document={body,documentElement:{classList:{add(){},remove(){}}},
  addEventListener(name,callback){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(callback);}};
 const window={addEventListener(){}};
 class DOMParser {parseFromString(html){return {body:{className:html.match(/<body[^>]*class="([^"]*)"/i)?.[1]||''}};}}
 vm.runInNewContext(source,{document,window,location:{pathname:'/gestao/eventos/',href:'https://local.example/gestao/eventos/',origin:'https://local.example'},URL,DOMParser});
 return {window,body,emit(name,event){for(const callback of listeners.get(name)||[])callback(event);}};
}
test('server validation and conflict HTML replaces the form with its specific errors',()=>{
 for(const status of [400,409,422]){
  const nav=navigation();let cleanupCount=0;
  nav.window.trilhasOnPageLeave(()=>cleanupCount++);
  const event={detail:{xhr:{status},target:nav.body,shouldSwap:false,isError:true}};
  nav.emit('htmx:beforeSwap',event);
  assert.equal(event.detail.shouldSwap,true,`validation ${status} must show server errors`);
  assert.equal(event.detail.isError,false,`validation ${status} must not show the network failure toast`);
  assert.equal(cleanupCount,1);
  assert.equal(nav.body.className,'');
 }
});

test('boosted navigation uses the destination body classes for its layout',()=>{
 const nav=navigation();
 for(const destination of ['public-site','participant-page','management-page','']){
  const response=`<!doctype html><html><head><title>Destino</title></head><body class="${destination}"><main>Conteúdo</main></body></html>`;
  nav.emit('htmx:beforeSwap',{detail:{xhr:{status:200,responseText:response},target:nav.body,shouldSwap:true}});
  assert.equal(nav.body.className,destination);
 }
});
test('server failures and missing resources preserve the existing page for normal error handling',()=>{
 for(const status of [404,500,503]){
  const nav=navigation();let cleanupCount=0;
  nav.window.trilhasOnPageLeave(()=>cleanupCount++);
  const event={detail:{xhr:{status},target:nav.body,shouldSwap:false,isError:true}};
  nav.emit('htmx:beforeSwap',event);
  assert.equal(event.detail.shouldSwap,false);
  assert.equal(event.detail.isError,true);
  assert.equal(cleanupCount,0);
  assert.equal(nav.body.className,'management-page');
 }
});
