import {chromium} from '@playwright/test';
import {readFile,rm} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {failureEvidence} from './failure-evidence.mjs';

const browser=await chromium.launch(),context=await browser.newContext();
try{
  const capture=await failureEvidence(context,'capture-self-check'),page=await context.newPage();
  await page.setContent('<main>synthetic failure evidence</main>');
  // Exercise the failure collector without failing CI or starting an application.
  await capture(page);
  const png=await readFile('../test-artifacts/capture-self-check/failure.png');
  const trace=await readFile('../test-artifacts/capture-self-check/trace.zip');
  assert.equal(png.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
  assert.equal(trace.subarray(0,4).toString('hex'),'504b0304');
  assert.ok(png.length>100&&trace.length>100);
  await rm('../test-artifacts/capture-self-check',{recursive:true});
  console.log('PASS: synthetic failure collector writes PNG and trace ZIP');
}finally{await browser.close()}
