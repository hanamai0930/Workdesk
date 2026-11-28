/* Verify the project tool entry through the real UI of the isolated check instance. */
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
    const batch=process.argv[3]||'B';
    if(batch==='B'){
      const card=page.locator('[data-project-card="'+config.default_project_id+'"]');
      await card.getByRole('button',{name:'电脑与开发工具',exact:true}).click();
      const dialog=page.getByRole('dialog',{name:'电脑与开发工具'});
      await dialog.getByLabel('操作',{exact:true}).selectOption('file.read');
      await dialog.getByLabel('项目内文件路径').fill('notes.txt');
      await dialog.getByRole('button',{name:'授权并执行本次操作'}).click();
      const entry=dialog.locator('article').filter({hasText:'已完成 file.read'}).first();
      const output=entry.locator('div').filter({has:page.locator('a[download]').filter({hasText:'output.txt'})}).first();
      await output.getByRole('button',{name:'查看输出'}).click();
      await output.locator('pre').filter({hasText:'updated by the project tool'}).waitFor();
      await page.screenshot({path:path.join(fixture,'computer-tools.png')});
    }else if(batch==='C'){
      await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#resources/knowledge');
      const panel=page.getByRole('region',{name:'项目知识检索'});
      await panel.getByLabel('检索项目').selectOption(config.default_project_id);
      await panel.getByLabel('检索方式').selectOption('keyword');
      await panel.getByLabel('检索内容').fill('报销');
      await panel.getByRole('button',{name:'检索',exact:true}).click();
      const source=panel.locator('[data-knowledge-source]').first();
      await source.getByRole('button',{name:'核对引用'}).click();
      await page.screenshot({path:path.join(fixture,'knowledge.png')});
      await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#resources/memory?project_id='+config.default_project_id);
      await page.getByText('样本决定汇总',{exact:true}).first().waitFor();
      const handoff=page.locator('details > summary').filter({hasText:'· 接续材料'}).first();
      await handoff.click();
      await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#projects');
      await page.locator('[data-project-card="'+config.default_project_id+'"]').getByRole('button',{name:'项目版本归档'}).click();
      const archive=page.getByRole('dialog',{name:'项目版本归档'});
      await archive.getByText(/已重开/).first().waitFor();
      await page.screenshot({path:path.join(fixture,'archive.png')});
    }else if(batch==='D'){
      const card=page.locator('[data-project-card="'+config.default_project_id+'"]');
      await card.getByRole('button',{name:'工具预约',exact:true}).click();
      const dialog=page.getByRole('dialog',{name:'项目工具预约'});
      await dialog.getByLabel('预约操作').selectOption('env.inspect');
      const date=new Date(Date.now()+48*3600*1000).toISOString().slice(0,16);
      await dialog.getByLabel('首次执行时间').fill(date);
      await dialog.getByLabel('执行时区').fill('UTC');
      await dialog.getByRole('button',{name:'授权并保存预约'}).click();
      const saved=dialog.locator('article').filter({hasText:'检查开发环境'}).filter({hasText:'UTC'});
      await saved.getByRole('button',{name:'取消后续轮次'}).first().click();
      await saved.filter({hasText:'已取消'}).first().waitFor();
      await dialog.getByRole('button',{name:'关闭',exact:true}).click();
      await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#settings/usage');
      const budget=page.getByRole('region',{name:'项目预算与用量'});
      await budget.getByLabel('预算项目').selectOption(config.default_project_id);
      await budget.getByRole('button',{name:'保存项目预算'}).waitFor();
      const round=process.argv[4];
      assert.ok(round,'missing tested occurrence');
      await page.goto('http://127.0.0.1:'+state.port+'/s/'+state.token+'/#settings/notifications');
      const inbox=page.getByRole('region',{name:'任务收件箱'});
      const link=inbox.locator('a').filter({hasText:'打开原轮次'}).and(page.locator('a[href*="'+round+'"]'));
      await link.click();
      await dialog.getByRole('heading',{name:/原作业/}).waitFor();
      await page.screenshot({path:path.join(fixture,'schedule-job.png')});
    }else throw Error('未知批次');
  }finally{await browser.close();}
})().catch(error=>{console.error(String(error.message).replace(/\/s\/[^/]+\//g,'/s/…/'));process.exitCode=1});
