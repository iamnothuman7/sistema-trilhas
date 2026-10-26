(() => {
  'use strict';
  const database = () => new Promise((resolve,reject) => {
    const request=indexedDB.open('trilhas-photo-drafts',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('photos');
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
  async function operation(key, value, remove=false) {
    const db=await database();
    try { return await new Promise((resolve,reject)=>{
      const tx=db.transaction('photos',value!==undefined||remove?'readwrite':'readonly'), store=tx.objectStore('photos');
      const request=remove?store.delete(key):value!==undefined?store.put(value,key):store.get(key);
      let result;request.onsuccess=()=>{result=request.result;};
      tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error);
    }); } finally {db.close();}
  }
  window.TrilhasPhotoDraft={
    async load(key) {try {const value=await operation(key);if(value?.expires>Date.now())return value.blob;if(value)await operation(key,undefined,true);}catch(_){}return null;},
    async save(key,blob) {try {await operation(key,{blob,expires:Date.now()+7*86400000});return true;}catch(_){return false;}},
    async remove(key) {try {await operation(key,undefined,true);}catch(_){}},
  };
})();
