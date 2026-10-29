import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sendMessage } from '../tools/wechat-mcp/dist/api/api.js';

test('send acceptance requires business success, not merely HTTP 200', async () => {
  const originalFetch = globalThis.fetch;
  try {
    for (const payload of [{ret:0}, {ret:-1}, {ret:0,errcode:42}, {}, {errcode:0}, {ret:false}, null, [], 'ok', {ret:'0'}]) {
      globalThis.fetch = async () => new Response(JSON.stringify(payload), {status:200});
      const operation = sendMessage({baseUrl:'https://example.invalid',token:'synthetic',body:{}});
      if (payload && !Array.isArray(payload) && typeof payload === 'object'
        && (payload.ret === undefined || payload.ret === 0)
        && (payload.errcode === undefined || payload.errcode === 0)) await operation;
      else await assert.rejects(operation);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
