const sleep=ms=>new Promise(r=>setTimeout(r,ms));

function visible(el){
  if(!el) return false;
  const r=el.getBoundingClientRect();
  const s=getComputedStyle(el);
  return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&s.opacity!=='0';
}

async function waitFor(fn,timeout=25000,step=250){
  const end=Date.now()+timeout;
  while(Date.now()<end){
    try{
      const v=fn();
      if(v)return v;
    }catch(e){}
    await sleep(step);
  }
  return null;
}

function pageNeedsLogin(){
  const u=location.href.toLowerCase();
  if(u.includes('/checkpoint')) return 'checkpoint';

  if(
    u.includes('/login') ||
    document.querySelector('input[name="email"],input[name="pass"]')
  ) return 'login';

  return '';
}

function textOf(el){
  return (el?.innerText||el?.textContent||'')
    .replace(/\s+/g,' ')
    .trim();
}

function findComposerTrigger(){
  const needles=[
    'bạn viết gì đi',
    'bạn viết gì',
    'bạn đang nghĩ gì',
    'viết gì đó',
    'tạo bài viết',
    'write something',
    "what's on your mind",
    'create a public post',
    'create post'
  ];

  const els=[
    ...document.querySelectorAll(
      '[role="button"],button,div[tabindex="0"]'
    )
  ].filter(visible);

  for(const el of els){
    const t=(
      textOf(el)+' '+
      (el.getAttribute('aria-label')||'')
    ).toLowerCase();

    if(needles.some(n=>t.includes(n))){
      return el;
    }
  }

  return null;
}

function textboxCandidates(root=document){
  const selectors=[
    '[contenteditable="true"][data-lexical-editor="true"]',
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"][aria-label]',
    'div[contenteditable="true"]'
  ];

  const seen=new Set();
  const out=[];

  for(const sel of selectors){
    for(const el of root.querySelectorAll(sel)){
      if(seen.has(el)||!visible(el)) continue;

      seen.add(el);

      const r=el.getBoundingClientRect();

      // Bỏ qua những editor quá nhỏ như comment/reaction.
      if(r.width<120||r.height<20) continue;

      out.push(el);
    }
  }

  return out;
}

function findPostTextbox(root=document){
  const boxes=textboxCandidates(root);

  if(!boxes.length) return null;

  boxes.sort((a,b)=>{
    const score=el=>{
      let s=0;

      if(el.getAttribute('data-lexical-editor')==='true'){
        s+=1000000;
      }

      if(el.getAttribute('role')==='textbox'){
        s+=500000;
      }

      const label=(
        el.getAttribute('aria-label')||''
      ).toLowerCase();

      if(/bài viết|post|mind|viết|write/.test(label)){
        s+=250000;
      }

      const r=el.getBoundingClientRect();

      return s+r.width*r.height;
    };

    return score(b)-score(a);
  });

  return boxes[0];
}

function findDialog(){
  const dialogs=[
    ...document.querySelectorAll('div[role="dialog"]')
  ].filter(visible);

  const preferred=[];
  const fallback=[];

  for(const d of dialogs){
    const label=(
      (d.getAttribute('aria-label')||'')+
      ' '+
      textOf(d).slice(0,500)
    ).toLowerCase();

    const box=findPostTextbox(d);

    if(
      box &&
      /tạo bài viết|create post|bài viết|post/.test(label)
    ){
      preferred.push(d);
    }
    else if(box){
      fallback.push(d);
    }
  }

  return preferred.at(-1)||fallback.at(-1)||null;
}

async function openComposer(){

  // Không dùng nhầm dialog khác chỉ vì nó có contenteditable.
  let d=findDialog();

  if(d) return d;

  const trigger=await waitFor(
    findComposerTrigger,
    20000
  );

  if(!trigger){
    throw new Error(
      'Không tìm thấy ô tạo bài viết trong Group.'
    );
  }

  trigger.scrollIntoView({
    block:'center',
    inline:'center'
  });

  await sleep(250);

  trigger.click();

  d=await waitFor(
    findDialog,
    25000
  );

  if(d) return d;

  /*
   * Một số phiên bản Facebook không render
   * composer bằng role="dialog".
   */
  const box=await waitFor(
    ()=>findPostTextbox(document),
    8000
  );

  if(box){
    return (
      box.closest('[role="dialog"]') ||
      document.body
    );
  }

  throw new Error(
    'Không mở được cửa sổ Tạo bài viết.'
  );
}

async function fillText(dialog,content){

  let box=await waitFor(
    ()=>findPostTextbox(dialog),
    12000
  );

  if(!box){
    box=await waitFor(
      ()=>findPostTextbox(document),
      6000
    );
  }

  if(!box){
    throw new Error(
      'Không tìm thấy ô nhập nội dung. Facebook có thể đã đổi giao diện.'
    );
  }

  box.scrollIntoView({
    block:'center'
  });

  box.focus();

  await sleep(250);

  /*
   * Facebook dùng Lexical Editor.
   * execCommand thường hoạt động ổn định hơn
   * việc gán trực tiếp innerText.
   */
  try{

    document.execCommand(
      'selectAll',
      false,
      null
    );

    document.execCommand(
      'delete',
      false,
      null
    );

    document.execCommand(
      'insertText',
      false,
      content
    );

  }catch(e){}

  /*
   * Nếu cách trên chưa nhập được chữ
   * thì thử lại bằng Range.
   */
  if(
    content &&
    !textOf(box).includes(
      content.slice(
        0,
        Math.min(20,content.length)
      )
    )
  ){

    box.focus();

    const sel=window.getSelection();

    const range=document.createRange();

    range.selectNodeContents(box);

    sel.removeAllRanges();

    sel.addRange(range);

    document.execCommand(
      'insertText',
      false,
      content
    );
  }

  box.dispatchEvent(
    new InputEvent(
      'input',
      {
        bubbles:true,
        inputType:'insertText',
        data:content
      }
    )
  );

  box.dispatchEvent(
    new Event(
      'change',
      {
        bubbles:true
      }
    )
  );

  await sleep(800);
}

function b64ToFile(item){

  const bin=atob(item.base64);

  const bytes=new Uint8Array(
    bin.length
  );

  for(let i=0;i<bin.length;i++){
    bytes[i]=bin.charCodeAt(i);
  }

  return new File(
    [bytes],
    item.name||'image.jpg',
    {
      type:item.mime||'image/jpeg'
    }
  );
}

async function attachImages(dialog,images){

  if(!images?.length) return;

  let input=
    dialog.querySelector(
      'input[type="file"]'
    ) ||
    document.querySelector(
      'input[type="file"][accept*="image"]'
    );

  if(!input){

    const controls=[
      ...dialog.querySelectorAll(
        '[role="button"],button,div[tabindex="0"]'
      )
    ].filter(visible);

    const btn=controls.find(
      el=>
        /ảnh\/?video|photo\/?video/i.test(
          textOf(el)+
          ' '+
          (el.getAttribute('aria-label')||'')
        )
    );

    if(btn){

      btn.click();

      await sleep(1200);

      input=
        dialog.querySelector(
          'input[type="file"]'
        ) ||
        document.querySelector(
          'input[type="file"][accept*="image"]'
        );
    }
  }

  if(!input){
    throw new Error(
      'Không tìm thấy ô upload ảnh.'
    );
  }

  const dt=new DataTransfer();

  for(const item of images){
    dt.items.add(
      b64ToFile(item)
    );
  }

  input.files=dt.files;

  input.dispatchEvent(
    new Event(
      'input',
      {
        bubbles:true
      }
    )
  );

  input.dispatchEvent(
    new Event(
      'change',
      {
        bubbles:true
      }
    )
  );

  await sleep(
    Math.max(
      4000,
      images.length*1800
    )
  );
}

async function clickPost(dialog){

  const candidates=[
    ...dialog.querySelectorAll(
      '[role="button"],button'
    )
  ].filter(visible);

  let btn=candidates.find(
    el=>
      /^(đăng|post)$/i.test(
        textOf(el)
      )
  );

  if(!btn){

    btn=candidates.find(
      el=>
        /\bđăng\b|\bpost\b/i.test(
          textOf(el)+
          ' '+
          (el.getAttribute('aria-label')||'')
        )
    );
  }

  if(!btn){
    throw new Error(
      'Không tìm thấy nút Đăng.'
    );
  }

  const ready=await waitFor(
    ()=>
      !btn.hasAttribute('disabled') &&
      btn.getAttribute('aria-disabled')!=='true',
    30000
  );

  if(!ready){
    throw new Error(
      'Nút Đăng chưa sẵn sàng.'
    );
  }

  btn.scrollIntoView({
    block:'center'
  });

  btn.click();

  const gone=await waitFor(
    ()=>
      !document.contains(dialog) ||
      !visible(dialog),
    60000,
    500
  );

  if(!gone){
    throw new Error(
      'Đã bấm Đăng nhưng cửa sổ tạo bài chưa đóng.'
    );
  }
}

async function postCurrentGroup(payload){

  const state=pageNeedsLogin();

  if(state==='checkpoint'){
    return {
      ok:false,
      code:'checkpoint',
      error:'Facebook yêu cầu checkpoint/xác minh.'
    };
  }

  if(state==='login'){
    return {
      ok:false,
      code:'login',
      error:'Facebook chưa đăng nhập.'
    };
  }

  await sleep(2200);

  const dialog=
    await openComposer();

  await fillText(
    dialog,
    payload.content||''
  );

  await attachImages(
    dialog,
    payload.images||[]
  );

  await clickPost(
    dialog
  );

  return {
    ok:true
  };
}

chrome.runtime.onMessage.addListener(
  (msg,sender,sendResponse)=>{

    if(msg?.type!=='FBPOST_POST'){
      return;
    }

    postCurrentGroup(msg)
      .then(sendResponse)
      .catch(
        e=>sendResponse({
          ok:false,
          error:
            e?.message||
            String(e)
        })
      );

    return true;
  }
);