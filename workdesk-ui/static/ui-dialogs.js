/* Shared confirmations take named text fields; business controllers own effects. */
window.WorkdeskDialogs = {
  confirm({title,message,details,tone='default',confirmLabel}) {
    return new Promise(resolve=>{
      let dialog=document.getElementById('wd-shared-confirm');
      if(!dialog){dialog=document.createElement('dialog');dialog.id='wd-shared-confirm';dialog.className='ctl-dialog';document.body.append(dialog);window.WorkdeskReact?.ownHost(dialog);}
      if(dialog.open){resolve(false);return;}
      const action=confirmLabel||String(title||'').replace(/[？?]$/,'')||'确认操作';
      dialog.setAttribute('aria-labelledby','wd-shared-confirm-title');
      dialog.dataset.tone=tone;
      dialog.innerHTML=`<h2 id="wd-shared-confirm-title">${esc(title)}</h2><p>${esc(message)}</p>${details?`<details class="wd-confirm-details"><summary>查看操作详情</summary><pre class="raw-page">${esc(details)}</pre></details>`:''}<div class="dialog-actions"><button type="button" class="button secondary" data-shared-confirm="cancel" autofocus>取消</button><button type="button" class="button ${tone==='danger'?'danger':'primary'}" data-shared-confirm="confirm">${esc(action)}</button></div>`;
      let settled=false;
      const finish=value=>{if(settled)return;settled=true;dialog.onclick=null;dialog.oncancel=null;dialog.onclose=null;if(dialog.open)dialog.close();resolve(value);};
      dialog.onclick=event=>{const button=event.target.closest('[data-shared-confirm]');if(button)finish(button.dataset.sharedConfirm==='confirm');};
      dialog.oncancel=event=>{event.preventDefault();finish(false);};dialog.onclose=()=>finish(false);
      dialog.showModal();
    });
  }
};
