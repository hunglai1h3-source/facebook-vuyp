(()=>{
  const origin=location.origin;
  chrome.storage.local.set({serverOrigin:origin}).catch(()=>{});
  const ping=()=>{try{chrome.runtime.sendMessage({type:'POLL_NOW'}).catch(()=>{});}catch(e){}};
  ping();
  const timer=setInterval(()=>{if(document.visibilityState==='visible')ping();},3000);
  window.addEventListener('message',(event)=>{
    if(event.source!==window)return;
    const data=event.data||{};
    if(data.source!=='FBPOST_WEB'||data.type!=='PAIR_CONNECTOR')return;
    chrome.runtime.sendMessage({type:'PAIR_FROM_WEB',serverOrigin:origin,code:String(data.code||'')})
      .then(result=>window.postMessage({source:'FBPOST_EXTENSION',type:'PAIR_RESULT',result},origin))
      .catch(err=>window.postMessage({source:'FBPOST_EXTENSION',type:'PAIR_RESULT',result:{ok:false,error:String(err)}},origin));
  });
  window.addEventListener('beforeunload',()=>clearInterval(timer));
})();