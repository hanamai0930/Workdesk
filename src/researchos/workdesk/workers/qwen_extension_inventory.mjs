import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import net from 'node:net';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {pathToFileURL} from 'node:url';

const [sdk,workspace]=process.argv.slice(2);
const {DaemonClient}=await import(pathToFileURL(path.join(sdk,'dist/index.mjs')).href);
const lease=net.createServer();
await new Promise((resolve,reject)=>{lease.once('error',reject);lease.listen(0,'127.0.0.1',resolve);});
const port=lease.address().port;
await new Promise(resolve=>lease.close(resolve));
const token=randomBytes(24).toString('hex');
const child=spawn(process.execPath,[path.join(sdk,'dist/cli/cli.js'),'serve',
  '--hostname','127.0.0.1','--port',String(port),'--workspace',workspace,
  '--require-auth','--no-open','--no-web','--no-token-qr'],{
  cwd:workspace,env:{...process.env,QWEN_SERVER_TOKEN:token},windowsHide:true,
  stdio:['ignore','ignore','pipe']
});
let exited=false,childError,diagnostic='';
const closed=new Promise(resolve=>{
  child.once('error',error=>{childError=error;});
  child.once('close',()=>{exited=true;resolve();});
});
child.stderr.on('data',data=>{diagnostic=(diagnostic+data.toString()).slice(-1600);});
const client=new DaemonClient({baseUrl:'http://127.0.0.1:'+port,token,fetchTimeoutMs:3000});
let result;
try{
  const deadline=Date.now()+20000;
  let ready=false;
  while(Date.now()<deadline&&!ready){
    if(childError)throw childError;
    if(exited)throw Error('官方扩展查询工作端已退出。'+diagnostic.replaceAll(token,'[token]'));
    try{await client.health();ready=true;}catch{await delay(150);}
  }
  if(!ready)throw Error('官方扩展查询工作端未在20秒内就绪。');
  const capabilities=await client.capabilities();
  const projection=await client.workspaceByCwd(workspace).workspaceExtensions();
  const catalog=await client.extensionCatalog();
  const normalized=value=>process.platform==='win32'?path.resolve(value).toLowerCase():path.resolve(value);
  const samePath=value=>typeof value==='string'&&normalized(value)===normalized(workspace);
  if(projection.v!==1||!samePath(projection.workspaceCwd)||typeof projection.workspaceId!=='string'||
      typeof projection.trusted!=='boolean'||!Array.isArray(projection.extensions)||catalog.v!==1||
      !Array.isArray(catalog.extensions)||![projection.desiredGeneration,projection.appliedGeneration,catalog.generation]
        .every(value=>Number.isSafeInteger(value)&&value>=0)){
    throw Error('官方扩展回执的工作区或版本字段无法核对。');
  }
  if(projection.desiredGeneration!==catalog.generation)throw Error('查询期间扩展目录已变化，请重新查询。');
  for(const extension of projection.extensions){
    const installed=catalog.extensions.find(item=>item.id===extension.extensionId);
    if(typeof extension.extensionId!=='string'||!installed||installed.name!==extension.name||
        installed.version!==extension.version||!['enabled','disabled'].includes(extension.effectiveActivation)){
      throw Error('工作区扩展与官方目录中的身份或版本不匹配。');
    }
  }
  result={source:'QWEN_OFFICIAL_WORKSPACE',version:capabilities.qwenCodeVersion,projection,catalog};
}finally{
  client.dispose();
  if(!exited)child.kill();
  await Promise.race([closed,delay(3000,undefined,{ref:false})]);
}
process.stdout.write(JSON.stringify(result)+'\n');
