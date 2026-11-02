import {chromium,expect} from '@playwright/test';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

// Read-only smoke test: never choose or export a real human label.
const browser=await chromium.launch();
const page=await browser.newPage();
const errors=[],external=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/*',route=>{
  if(route.request().url().startsWith('file:'))return route.continue();
  external.push(route.request().url());return route.abort();
});
try{
  await page.goto(pathToFileURL(process.argv[2]).href);
  const rows=await page.locator('#data').textContent().then(JSON.parse);
  assert.ok(rows.length>0);
  await expect(page.locator('article')).toHaveCount(rows.length);
  await expect(page.locator('blockquote')).toHaveCount(rows.reduce((n,r)=>n+r.evidence.length,0));
  const selects=await page.locator('select').all();
  for(const select of selects)await expect(select).toHaveValue('');
  await page.getByRole('button',{name:'导出已选择的人工结论'}).click();
  await expect(page.getByRole('status')).toHaveText('尚未选择结论，没有导出文件。');
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log(`PASS: offline preview ${rows.length} candidates, complete evidence, blank human labels, zero external requests, narrow layout`);
}finally{await browser.close()}
