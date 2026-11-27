/* Continue a verified workbook through the real UI of the isolated check instance. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const fixture = path.resolve(process.argv[2]);
const state = JSON.parse(fs.readFileSync(path.join(fixture,'user/runtime/ui/instance-core.json'),'utf8'));
const config = JSON.parse(fs.readFileSync(path.join(fixture,'instance.json'),'utf8'));
assert.equal(state.instance_id,config.instance_id);
assert.equal(path.resolve(config.user_root),path.join(fixture,'user'));
const {chromium} = require(require.resolve('playwright',{paths:[
  __dirname,path.join(process.env.USERPROFILE||'', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
]}));
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1360,height:940}});
    page.setDefaultTimeout(25000);
    await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#projects');
    const card=page.locator('[data-project-card="'+config.default_project_id+'"]');
    await card.getByRole('button',{name:'文档工具',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'文档工具'});
    await dialog.getByRole('button',{name:'以此版本继续修改'}).first().click();
    await dialog.getByLabel('成果文件名（可选）').fill('第二版.xlsx');
    await dialog.getByLabel('写入位置 1',{exact:true}).fill('B3');
    await dialog.getByLabel('值或公式',{exact:true}).fill('100');
    await dialog.getByLabel('核对位置 1',{exact:true}).fill('B4');
    await dialog.getByLabel('预期数值',{exact:true}).fill('220');
    await dialog.getByRole('button',{name:'授权并生成新版本'}).click();
    const file=dialog.locator('.dt-file').filter({has:page.getByText('第二版.xlsx',{exact:true})});
    await file.getByRole('button',{name:'验收',exact:true}).click({timeout:145000});
    await file.getByRole('button',{name:'确认验收此版本'}).click();
    await file.getByText('已验收',{exact:true}).waitFor();
    await page.screenshot({path:path.join(fixture,'document-ui.png')});
  }finally{await browser.close();}
})().catch(error=>{console.error(String(error.message).replace(/\/s\/[^/]+\//g,'/s/…/'));process.exitCode=1});
