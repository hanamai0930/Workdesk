/* Read-only detail projection for saved Chat records. */
(() => {
  'use strict';

  const providers={codex:'ChatGPT',deepseek:'DeepSeek',qwen:'Qwen'};
  const officialHosts={codex:'chatgpt.com',deepseek:'chat.deepseek.com',qwen:'chat.qwen.ai'};
  const states={
    COMPLETED:'已完成',RESERVED:'已登记',SEND_INTENT:'发送待核对',RUNNING:'回复中',
    INTERRUPTED:'已停止',STOPPED_RUNTIME:'已停止',CANCELLED:'已取消',FAILED:'请求失败',
    FAILED_NO_SEND:'发送前失败',SEND_UNKNOWN:'发送状态待核对',STOP_UNKNOWN:'停止状态待核对',
    ORPHANED_UNKNOWN:'重启后待核对'
  };
  const number=new Intl.NumberFormat('zh-CN');
  const size=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1});
  const tokenFields=[
    ['prompt_tokens','输入 Token'],
    ['prompt_cache_hit_tokens','缓存命中 Token'],
    ['prompt_cache_miss_tokens','未命中缓存 Token'],
    ['completion_tokens','输出 Token'],
    ['total_tokens','合计 Token']
  ];
  const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
  const text=value=>typeof value==='string'?value.trim():'';

  function urlOf(value){
    try{
      const url=new URL(value);
      if(!url.username&&!url.password&&['http:','https:'].includes(url.protocol))return url;
    }catch{ /* Invalid link from an older saved record. */ }
    return null;
  }

  function usageOf(receipt){
    const input=count(receipt?.prompt_tokens),output=count(receipt?.completion_tokens);
    let hit=count(receipt?.prompt_cache_hit_tokens)??count(receipt?.prompt_tokens_details?.cached_tokens);
    let miss=count(receipt?.prompt_cache_miss_tokens);
    if(input!==null){
      if(hit!==null&&miss===null&&hit<=input)miss=input-hit;
      if(hit===null&&miss!==null&&miss<=input)hit=input-miss;
      if(hit>input||miss>input||(hit!==null&&miss!==null&&hit+miss!==input)){
        hit=null;
        miss=null;
      }
    }
    return {
      prompt_tokens:input,prompt_cache_hit_tokens:hit,prompt_cache_miss_tokens:miss,
      completion_tokens:output,total_tokens:count(receipt?.total_tokens)??(input!==null&&output!==null?count(input+output):null)
    };
  }

  function savedReplies(messages){
    const byTurn=new Map();
    const replies=new Map();
    let nearestQuestion='';
    for(const [index,message] of messages.entries()){
      if(message.role==='human'){
        nearestQuestion=text(message.content);
        if(message.turn_id)byTurn.set(message.turn_id,nearestQuestion);
      }
      if(message.role==='assistant'){
        const identity=message.turn_id||message.id||index;
        replies.set(identity,{message,prompt:byTurn.get(message.turn_id)??nearestQuestion,usage:usageOf(message.usage)});
        nearestQuestion='';
      }
    }
    return [...replies.values()];
  }

  function usageRows(replies){
    const received=replies.filter(({usage})=>tokenFields.some(([key])=>usage[key]!==null));
    const rows=[{label:'用量回执',value:`${number.format(received.length)} / ${number.format(replies.length)} 个回复回合`}];
    for(const [key,label] of tokenFields){
      const entries=received.map(row=>row.usage[key]).filter(value=>value!==null);
      if(entries.length){
        const total=entries.reduce((sum,value)=>sum+BigInt(value),0n);
        const coverage=entries.length===received.length?'':' · '+number.format(entries.length)+' 个回合';
        rows.push({label,value:number.format(total)+coverage});
      }
    }
    return rows;
  }

  function assetSummaries(messages){
    const files=new Map(),sources=new Map();
    for(const message of messages){
      for(const file of Array.isArray(message.attachments)?message.attachments:[]){
        const name=text(file?.name),bytes=count(file?.size);
        if(!name)continue;
        const type=file.kind==='image'?'图片':file.kind==='text'?'文本':'聊天附件';
        const quantity=bytes===null?'':bytes>=1048576?size.format(bytes/1048576)+' MiB':
          bytes>=1024?size.format(bytes/1024)+' KiB':number.format(bytes)+' B';
        files.set(file.id||name,{label:name,value:[type,quantity].filter(Boolean).join(' · ')});
      }
      for(const source of Array.isArray(message.sources)?message.sources:[]){
        const url=urlOf(source?.url);
        if(url)sources.set(url.href,{
          label:text(source.title)||url.hostname,value:url.hostname,
          ...(url.protocol==='https:'?{href:url.href,external:true}:{})
        });
      }
    }
    return [
      ...(files.size?[{title:'聊天附件',rows:[...files.values()]}]:[]),
      ...(sources.size?[{title:'已保存的联网来源',rows:[...sources.values()]}]:[])
    ];
  }

  function excerpt(value){
    const body=text(value);
    return body.length>260?body.slice(0,260).trimEnd()+'…':body;
  }

  function runRows(replies){
    return replies.map(({message,prompt,usage},index)=>{
      const rows=tokenFields.filter(([key])=>usage[key]!==null)
        .map(([key,label])=>({label,value:number.format(usage[key])}));
      const quote=excerpt(prompt),reply=excerpt(message.content);
      return {
        title:'回复 '+number.format(index+1),
        subtitle:states[message.state]||'本地记录',
        body:[quote&&('提问　'+quote),reply&&('回复　'+reply)].filter(Boolean).join('\n\n'),
        ...(rows.length?{rows}:{}),
        ...(message.task_id?{href:'#control/task/'+encodeURIComponent(message.task_id)}:{})
      };
    });
  }

  async function load(id){
    const row=await api('v1/conversation?kind=chat&id='+encodeURIComponent(id));
    const config=row.configuration||{};
    const provider=config.provider||row.provider;
    const apiChat=config.route==='api',official=config.route==='web';
    const messages=(Array.isArray(row.messages)?row.messages:[]).filter(message=>message&&
      (message.turn_id||message.id||message.task_id||text(message.content)||message.attachments?.length));
    const model=text(config.config?.model),replies=savedReplies(messages);
    const metadata=[
      {label:'连接方式',value:apiChat?'API 连接':official?'官方网页':'本地保存的历史聊天'},
      ...(providers[provider]?[{label:'服务提供方',value:provider==='codex'&&!official?'Codex':providers[provider]}]:[]),
      ...(model?[{label:official?'本地保存的型号':'模型',value:model}]:[]),
      {label:'本地消息记录',value:number.format(messages.length)+' 条'}
    ];
    if(official){
      metadata.push({label:'显示范围',value:'这里只显示 Workdesk 已保存的内容'});
    }else{
      metadata.push({label:'回复回合',value:number.format(replies.length)+' 个'});
      if(apiChat&&typeof config.config?.thinking==='boolean'){
        metadata.push({label:'深度思考',value:config.config.thinking?'开启':'关闭'});
      }
    }
    const links=[],officialURL=official?urlOf(config.web_url):null;
    if(officialURL?.protocol==='https:'&&officialURL.host===officialHosts[provider]&&officialURL.pathname!=='/'){
      links.push({label:'打开已绑定的官网对话',href:officialURL.href,external:true});
    }
    const opening=official&&messages.find(message=>message.role==='human'&&text(message.content));
    const summaries=[
      ...(apiChat?[{title:'已保存的 API 用量',rows:usageRows(replies)}]:[]),
      ...assetSummaries(messages)
    ];
    return {kind:'chat',title:text(row.title)||'聊天详情',backHref:'#chat/'+encodeURIComponent(id),...(official?{runsTitle:'开场记录'}:{}),
      metadata,summaries,runs:official?(opening?[{title:'已保存的开场文字',body:text(opening.content)}]:[]):runRows(replies),links};
  }

  window.WorkdeskChatDetail=Object.freeze({load});
})();
