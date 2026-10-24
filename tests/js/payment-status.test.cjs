const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/static/js/payment-status.js'), 'utf8');
function fixture() {
  let elapsed = 0;
  const now = '2026-10-24T12:00:00Z';
  const elements = {};
  for (const id of ['payment-dialog','payment-feedback','pix-instructions','pix-payable','pix-countdown','refresh-payment','pix-countdown-panel','copy-pix']) {
    elements[id] = {dataset: {}, hidden: false, textContent: '', handlers: {}, classList: {toggle() {}},
      addEventListener(name, fn) { this.handlers[name] = fn; }};
  }
  elements['payment-dialog'].dataset = {statusUrl:'/status/',serverNow:now};
  elements['pix-instructions'].dataset.expires = '2026-10-24T12:31:00Z';
  const button = {};
  elements['refresh-payment'].action = '/refresh/';
  elements['refresh-payment'].querySelector = () => button;
  const timeouts = [], intervals = [], requests = [], destinations = [];
  const document = {hidden:false,getElementById:id=>elements[id],addEventListener(){}};
  let response = {server_time:now,pix_expires_at:elements['pix-instructions'].dataset.expires,redirect_url:null,is_paid:false};
  let fail = false;
  vm.runInNewContext(source, {
    clearTimeout(){}, clearInterval(){}, document, performance:{now:()=>elapsed}, Date, Number, String,
    FormData: class {}, navigator:{clipboard:{writeText:async()=>{throw Error('should not copy');}}},
    fetch: async (url, options) => {requests.push({url,options}); if(fail) throw Error('offline'); return {ok:true,json:async()=>response};},
    window:{location:{assign:url=>destinations.push(url)},setTimeout:fn=>{timeouts.push(fn);return timeouts.length;},
      clearTimeout(){},setInterval:fn=>intervals.push(fn),clearInterval(){},addEventListener(){}}
  });
  return {elements,requests,destinations,document,advance(ms){elapsed=ms;intervals[0]();},
    response(data){response=data;},offline(){fail=true;},poll:()=>timeouts.at(-1)()};
}
test('countdown counts down from the server expiry', () => {
  const f=fixture(); assert.equal(f.elements['pix-countdown'].textContent,'31:00');
  f.advance(61000); assert.equal(f.elements['pix-countdown'].textContent,'29:59');
});
test('expiry hides payment code without claiming success and still accepts verified late approval', async () => {
  const f=fixture(); f.advance(31*60000);
  assert.equal(f.elements['pix-countdown'].textContent,'00:00');
  assert.equal(f.elements['pix-payable'].hidden,true); assert.deepEqual(f.destinations,[]);
  await f.elements['copy-pix'].handlers.click();
  f.response({is_paid:true,redirect_url:'/result/'}); await f.poll();
  assert.deepEqual(f.destinations,['/result/']);
  assert.equal(f.requests[0].url,'/refresh/'); assert.equal(f.requests[0].options.method,'POST');
});
test('pending payments and network failures never show success', async () => {
  const f=fixture(); await f.poll(); assert.deepEqual(f.destinations,[]);
  f.offline(); await f.poll(); assert.deepEqual(f.destinations,[]);
  assert.match(f.elements['payment-feedback'].textContent,/não faça outro pagamento/);
});
test('background tab does not poll the payment provider', async () => {
  const f=fixture(); f.document.hidden=true; await f.poll(); assert.equal(f.requests.length,0);
});
