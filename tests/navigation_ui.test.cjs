const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('campos chamados method não quebram a navegação HTMX nem mudam POST para GET',()=>{
  function form(method){const attrs={method,action:'/gestao/teste/'};return {tagName:'FORM',method:{name:'method',value:'PIX_MANUAL'},
    getAttribute(name){return attrs[name]??null;},setAttribute(name,value){attrs[name]=value;},hasAttribute(name){return name in attrs;},
    querySelector(){return null;},attrs};}
  const post=form('POST'),get=form('GET'),implicit=form(null);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/static/js/navigation.js'),'utf8'),{
    window:{addEventListener(){}},document:{body:{querySelectorAll(){return [post,get,implicit];}},addEventListener(){}},
    location:{pathname:'/gestao/teste/',href:'https://trilhas.example/gestao/teste/',origin:'https://trilhas.example'},URL,
  });
  assert.equal(post.attrs['hx-push-url'],undefined);
  assert.equal(get.attrs['hx-push-url'],'true');assert.equal(implicit.attrs['hx-push-url'],'true');
  assert.equal(post.method.value,'PIX_MANUAL');
});
