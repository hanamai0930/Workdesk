/* Read-only template shared by Chat and Independent Work conversation details. */
(()=>{
  'use strict';

  const isText=value=>typeof value==='string'||(typeof value==='number'&&Number.isFinite(value));
  const escape=value=>String(value??'').replace(/[&<>"']/g,character=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[character]);

  function linkTarget(href,external){
    if(typeof href!=='string'||/[\x00-\x1f\x7f]/.test(href))return null;
    if(href.startsWith('#'))return {href};
    if(!external)return null;
    try{
      const url=new URL(href);
      return url.protocol==='https:'&&!url.username&&!url.password?{href:url.href,external:true}:null;
    }catch{return null;}
  }

  function link(label,href,external,cssClass=''){
    const target=linkTarget(href,external);
    if(!target||!isText(label)||label==='')return '';
    return `<a${cssClass?` class="${cssClass}"`:''} href="${escape(target.href)}"${target.external?' target="_blank" rel="noopener noreferrer"':''}>${escape(label)}</a>`;
  }

  function facts(rows){
    const items=(rows||[]).filter(row=>isText(row?.label)&&row.label!==''&&isText(row.value)&&row.value!=='');
    if(!items.length)return '';
    return `<dl class="wd-detail-facts">${items.map(row=>`<div>
      <dt>${escape(row.label)}</dt>
      <dd>${link(row.value,row.href,row.external)||escape(row.value)}</dd>
    </div>`).join('')}</dl>`;
  }

  function runEntry(run,work){
    if(!isText(run?.title)||run.title==='')return '';
    const label=`<span class="wd-detail-run-label"><strong>${escape(run.title)}</strong>
      ${isText(run.subtitle)&&run.subtitle!==''?`<small>${escape(run.subtitle)}</small>`:''}</span>`;
    const metrics=facts(run.rows);
    const result=isText(run.body)&&run.body!==''?`<p class="wd-detail-run-body">${escape(run.body)}</p>`:'';
    const action=link(run.hrefLabel||(work?'查看这次执行':'查看这轮对话'),run.href,run.external,'wd-detail-run-link');
    if(!metrics&&!result&&!action)return `<article class="wd-detail-run wd-detail-run-static">${label}</article>`;
    return `<details class="wd-detail-run">
      <summary>${label}<span class="wd-detail-chevron" aria-hidden="true"></span></summary>
      <div class="wd-detail-run-content">${metrics}${result}${action}</div>
    </details>`;
  }

  function render(data={}){
    const work=data.kind==='work';
    const name=work?'工作详情':'聊天详情';
    const backLabel=data.backLabel||(work?'返回 Work 对话':'返回对话');
    const back=linkTarget(data.backHref,false)?.href||(work?'#work':'#chat');
    const title=isText(data.title)&&data.title!==''?data.title:name;
    const tabs=(data.tabs||[]).map(tab=>{
      const target=linkTarget(tab.href,false);
      return target?`<a href="${escape(target.href)}"${tab.active?' aria-current="page"':''}>${escape(tab.label)}</a>`:'';
    }).join('');
    const header=`<header class="wd-detail-header">
      <div class="wd-detail-back-links">
        <a class="wd-detail-back" href="${escape(back)}"><span aria-hidden="true">‹</span>${escape(backLabel)}</a>
        ${link(data.alternateBack?.label,data.alternateBack?.href,false,'wd-detail-related-link')}
      </div>
      <h1>${escape(title)}</h1>
      ${isText(data.subtitle)&&data.subtitle!==''?`<p>${escape(data.subtitle)}</p>`:''}
    </header>`;
    let content;
    if(data.error){
      content=`<section class="wd-detail-panel wd-detail-status" role="alert">
        <p>${escape(data.error)}</p><button type="button" data-conversation-detail-retry>重试</button>
      </section>`;
    }else if(data.status){
      content=`<p class="wd-detail-panel wd-detail-status" role="status">${escape(data.status)}</p>`;
    }else if(typeof data.panelContent==='string'){
      content=data.panelContent;
    }else{
      const info=facts(data.metadata);
      const summaries=(data.summaries||[]).map(summary=>{
        const rows=facts(summary?.rows);
        if(!rows)return '';
        return `<section class="wd-detail-panel wd-detail-summary">
          ${summary.title?`<h2>${escape(summary.title)}</h2>`:''}${rows}
        </section>`;
      }).join('');
      const runs=(data.runs||[]).map(run=>runEntry(run,work)).join('');
      const links=(data.links||[]).map(item=>link(item?.label,item?.href,item?.external)).filter(Boolean);
      content=`
        ${info?`<section class="wd-detail-panel" aria-label="基本信息">${info}</section>`:''}
        ${summaries}
        ${runs?`<section class="wd-detail-panel wd-detail-runs">
          <h2>${escape(data.runsTitle||(work?'工作回合':'回合记录'))}</h2>${runs}
        </section>`:''}
        ${links.length?`<nav class="wd-detail-links" aria-label="查看相关记录">${links.join('')}</nav>`:''}`;
    }
    return `<section class="wd-conversation-detail" data-conversation-detail="${work?'work':'chat'}" aria-label="${name}">
      ${header}${tabs?`<nav class="wd-independent-tabs" aria-label="独立 Work 任务详情">${tabs}</nav>`:''}${content}</section>`;
  }

  window.WorkdeskConversationDetail={render};
})();
