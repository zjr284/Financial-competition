// 读取归档结果以制作截图；不创建 Run、不写人工决定、不拦截 API。
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn,execFileSync} from 'node:child_process';
import {chromium} from 'playwright';

const [runtime, inventoryPath, outputArg] = process.argv.slice(2);
if (!runtime || !inventoryPath || !outputArg) throw Error('参数：RUNTIME INVENTORY_JSON NEW_OUTPUT');
const output=path.resolve(outputArg), root=path.resolve(import.meta.dirname,'../..');
const revision=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8',windowsHide:true}).trim();
assert.equal(execFileSync('git',['status','--porcelain'],{cwd:root,encoding:'utf8',windowsHide:true}).trim(),'','先提交再截取固定版本');
const inventory=JSON.parse(await fs.readFile(inventoryPath,'utf8'));
await fs.mkdir(output,{recursive:false});
const service=spawn(process.env.PYTHON||'python',['-m','demo.live','--data-dir',path.resolve(runtime),
  '--facts',path.resolve(runtime,'reviewed_facts.json'),'--api-port','0','--web-port','0'],
  {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONUNBUFFERED:'1',
    FINSCOPE_MODEL_BACKEND:'offline-rule',FINSCOPE_EXTRACTION_BACKEND:'rules'}});
let stdout='',stderr='',browser;
service.stdout.on('data',b=>stdout+=b.toString());service.stderr.on('data',b=>stderr+=b.toString());
const stopped=new Promise(resolve=>service.on('exit',resolve));
try {
  const deadline=Date.now()+60000;
  while(!stdout.includes('（选择连接团队 API）')) {
    if(service.exitCode!==null||Date.now()>deadline)throw Error('服务启动失败：'+stderr);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  const api=stdout.match(/API：(http:\/\/[^；]+)/)[1],site=stdout.match(/页面：(http:\/\/[^（]+)/)[1];
  browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],mutations=[],captures=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().startsWith(api)&&r.method()!=='GET')mutations.push(r.method());});
  await page.goto(site);await page.locator('#live').click();
  await page.locator('#api-base').fill(api);await page.locator('#health').click();
  await page.getByText('团队 API 已连接',{exact:true}).waitFor();
  for(const item of inventory) {
    assert.match(item.name,/^[a-z][a-z0-9_-]*$/);
    const response=await fetch(`${api}/runs/${encodeURIComponent(item.run_id)}/result`);
    assert.ok(response.ok);const expected=await response.json();
    await page.locator('#saved-run-id').fill(item.run_id);await page.locator('#load-existing').click();
    await page.waitForFunction(id=>{try{return JSON.parse(document.querySelector('#raw').textContent).run_id===id;}catch{return false;}},item.run_id);
    await page.waitForFunction(()=>!document.querySelector('#load-existing').disabled,null,{timeout:60000});
    assert.deepEqual(JSON.parse(await page.locator('#raw').textContent()),expected);
    await page.locator('#results').evaluate(e=>e.scrollIntoView({block:'start'}));
    if(item.source_button) {
      let scope=page;
      if(item.source_field) {
        assert.match(item.source_field,/^[a-z_]+$/);
        scope=page.locator('.disclosure-record').nth(item.record_index||0)
          .locator('.field-card[data-field="'+item.source_field+'"]');
      }
      await scope.getByRole('button',{name:item.source_button,exact:true}).first().click();
      await page.locator('#location-note').filter({hasText:'固定 PDF 原件已校验哈希'}).waitFor({timeout:60000});
      assert.ok(await page.locator('#bbox').isVisible());
      await page.locator('#pdf-canvas').evaluate(e=>e.scrollIntoView({block:'start'}));
    }
    if(item.capture_selector) {
      await page.locator(item.capture_selector).first().screenshot({path:path.join(output,item.name+'.png')});
    } else {
      await page.screenshot({path:path.join(output,item.name+'.png')});
    }
    captures.push({name:item.name,run_id:item.run_id,task_type:expected.task_type,
      source_field:item.source_field||null,capture_selector:item.capture_selector||null});
  }
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  await fs.writeFile(path.join(output,'capture-proof.json'),JSON.stringify({code_version:revision,requests:'actual_http_get_only',errors,mutations,captures},null,2));
  console.log('归档截图通过：'+captures.length+'；实际 HTTP GET，无任务或复核写入');
} finally {if(browser)await browser.close();service.kill();await stopped;}
