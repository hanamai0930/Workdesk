// Thin framing adapter around the official MCP SDK. No model or agent loop.
import readline from 'node:readline';
import {pathToFileURL} from 'node:url';
const root=process.argv[2],url=process.argv[3];
const {Client}=await import(pathToFileURL(root+'/dist/esm/client/index.js'));
const {StreamableHTTPClientTransport}=await import(pathToFileURL(root+'/dist/esm/client/streamableHttp.js'));
const client=new Client({name:'workdesk-extension-check',version:'1'},{capabilities:{}});
const lines=readline.createInterface({input:process.stdin});
for await(const line of lines){
  const msg=JSON.parse(line);if(msg.id===undefined)continue;
  try{
    let result;
    if(msg.method==='initialize'){await client.connect(new StreamableHTTPClientTransport(new URL(url)));result={protocolVersion:msg.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'workdesk-http-adapter',version:'1'}};}
    else if(msg.method==='tools/list')result=await client.listTools(msg.params);
    else if(msg.method==='tools/call')result=await client.callTool(msg.params);
    else throw Error('Unsupported explicit operation');
    process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:msg.id,result})+'\n');
  }catch(e){process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:msg.id,error:{code:-32000,message:String(e)}})+'\n');}
}
await client.close();
