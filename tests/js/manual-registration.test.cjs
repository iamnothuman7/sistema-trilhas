const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/static/js/manual-registration.js'), 'utf8');

function setup() {
  const element = (value='') => ({value, handlers: {}, addEventListener(name, fn) {this.handlers[name]=fn;}});
  const lot=element('lot1'), shirt=element('M'), payment=element('pending');
  shirt.options=[];
  shirt.replaceChildren=()=>{shirt.options=[];shirt.value='';};
  shirt.append=option=>{shirt.options.push(option);if(shirt.options.length===1)shirt.value=option.value;};
  const fields=[{disabled:false},{disabled:false}], received={style:{},querySelectorAll:()=>fields}, total={};
  const form={querySelector(selector) {return {'[name="lot"]':lot,'[name="shirt"]':shirt,'[name="payment_state"]':payment,'[data-received-fields]':received,'[data-manual-total]':total}[selector];}};
  const catalog={lot1:{price:12000,sizes:[{code:'M',label:'M',extra:0},{code:'GG',label:'GG especial',extra:1500}]},lot2:{price:9000,sizes:[{code:'',label:'Pacote sem camiseta',extra:0}]}};
  vm.runInNewContext(source,{document:{querySelectorAll:()=>[],querySelector:()=>form,
    getElementById:()=>({textContent:JSON.stringify(catalog)}),createElement:()=>({})},Intl});
  return {lot,shirt,payment,received,fields,total};
}

test('total follows only the selected lot and its shirt extra, including a no-shirt package',()=>{
  const ui=setup();assert.match(ui.total.textContent,/120,00/);
  ui.shirt.value='GG';ui.shirt.handlers.change();assert.match(ui.total.textContent,/135,00/);
  ui.lot.value='lot2';ui.lot.handlers.change();assert.equal(ui.shirt.value,'');
  assert.equal(ui.shirt.options.length,1);assert.match(ui.total.textContent,/90,00/);
});
test('pending payment hides and disables receipt fields; received enables explicit confirmation',()=>{
  const ui=setup();assert.equal(ui.received.hidden,true);assert.equal(ui.received.style.display,'none');
  assert.ok(ui.fields.every(field=>field.disabled));
  ui.payment.value='received';ui.payment.handlers.change();
  assert.equal(ui.received.hidden,false);assert.ok(ui.fields.every(field=>!field.disabled));
});
test('copy link uses the clipboard and reports a selectable fallback when unavailable',async()=>{
  let handler, copied, focused=false, selected=false;
  const button={dataset:{copyManualLink:'link'},addEventListener(name,fn){handler=fn;}};
  const input={value:'https://example.test/private-link',focus(){focused=true;},select(){selected=true;}};
  const status={};
  const clipboard={async writeText(value){copied=value;}};
  vm.runInNewContext(source,{document:{querySelectorAll:()=>[button],querySelector:()=>null,
    getElementById:id=>id==='link'?input:status},navigator:{clipboard}});
  await handler();assert.equal(copied,input.value);assert.match(status.textContent,/copiado/);
  clipboard.writeText=async()=>{throw new Error('blocked');};
  await handler();assert.ok(focused&&selected);assert.match(status.textContent,/selecionado/);
});
