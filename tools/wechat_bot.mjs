// Project-local MCP client. Test recipient is the QR-login owner, never a contact.
import { Client } from './wechat-mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from './wechat-mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const root = fileURLToPath(new URL('../', import.meta.url));
const server = path.join(root, 'tools/wechat-mcp/dist/mcp/server.js');
const env = { ...process.env, WECHAT_MCP_STATE_DIR: path.join(root, 'data/wechat-bot'),
  WECHAT_MCP_TMP_DIR: path.join(root, 'data/wechat-bot/tmp'), WECHAT_MCP_LOG_LEVEL: 'FATAL' };
const mode = process.argv[2] || 'check';
if (!['login', 'check', 'test', 'notify', 'notify-test'].includes(mode)) throw new Error('Unknown mode');
const machineMode = mode === 'notify' || mode === 'notify-test';
if (mode === 'login') {
  console.log('请用你本人的手机微信扫码。凭据仅保存在本项目 data/wechat-bot 中。');
  const result = spawnSync(process.execPath, [server, 'login'], { env, stdio: 'inherit' });
  process.exit(result.status ?? 1);
}
const transport = new StdioClientTransport({ command: process.execPath, args: [server], env, stderr: 'ignore' });
const client = new Client({ name: 'wechat-memory-test', version: '1.0.0' });
// End the MCP child before Python's outer timeout; never retry an uncertain send.
const watchdog = machineMode ? setTimeout(() => {
  console.log(JSON.stringify({state:'uncertain'}));
  const hardExit = setTimeout(() => process.exit(2), 3000);
  client.close().finally(() => { clearTimeout(hardExit); process.exit(2); });
}, 40000) : null;
async function call(name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  if (result.isError) {
    if (name === 'wechat_send') {
      const diagnostic = result.content.filter(c => c.type === 'text').map(c => c.text).join(' ')
        .match(/outcome uncertain (?:ret=(?:-?\d+|undefined|object|string|boolean) errcode=(?:-?\d+|undefined|object|string|boolean)|\(non-JSON response\))/)?.[0];
      if (diagnostic) console.error(diagnostic);
      throw new Error('wechat_send：接口未能确认发送结果。消息可能已到达，请检查手机，不自动重发。');
    }
    throw new Error(`${name} 未成功；请检查网络或重新登录，不自动重发。`);
  }
  return JSON.parse(result.content.find(c => c.type === 'text').text);
}
try {
  await client.connect(transport);
  const tools = await client.listTools();
  const { accounts } = await call('wechat_list_accounts');
  if (!machineMode) console.log(`MCP 已连接，可用工具 ${tools.tools.length} 个；已登录账号 ${accounts.length} 个。`);
  if (machineMode) {
    if (accounts.length !== 1 || !accounts[0].configured || !accounts[0].userId) throw new Error('需要先扫码登录且只绑定你本人一个账号。');
    const owner = accounts[0];
    if (!/^[a-zA-Z0-9_-]+$/.test(owner.accountId)) throw new Error('Invalid account id');
    const contextFile = path.join(env.WECHAT_MCP_STATE_DIR, 'openclaw-weixin/accounts', `${owner.accountId}.context-tokens.json`);
    const contexts = JSON.parse(fs.readFileSync(contextFile, 'utf8'));
    if (typeof contexts[owner.userId] !== 'string' || !contexts[owner.userId]) throw new Error('需要先向机器人发送通知测试以建立会话。');
    // No arbitrary text, recipient, media, or chat content is accepted from Python.
    const text = mode === 'notify-test'
      ? '[队列合成测试] 系统已生成一条测试提醒，并通过提醒队列发送到微信。不包含真实聊天内容。'
      : '聊天记忆助手：有一条待查看提醒。请在电脑工作台查看详情。本通知不包含聊天正文。';
    const sent = await call('wechat_send', { accountId: owner.accountId, to: owner.userId, text, filterMarkdown: false });
    console.log(JSON.stringify({state: sent.hadContextToken ? 'accepted' : 'uncertain'}));
  }
  if (mode === 'test') {
    if (accounts.length !== 1 || !accounts[0].configured || !accounts[0].userId) {
      throw new Error('需要先扫码登录且只绑定你本人一个账号。');
    }
    const owner = accounts[0];
    console.log('请在手机的机器人会话发送“通知测试”。等待最多 3 分钟，不读取好友聊天。');
    const deadline = Date.now() + 180000;
    let ready = false;
    while (Date.now() < deadline && !ready) {
      const result = await call('wechat_receive', { accountId: owner.accountId, timeoutMs: 15000, downloadMedia: false });
      if (result.sessionExpired || result.error) throw new Error('机器人会话失效或接收失败，请重新登录。');
      ready = result.messages.some(m => m.From === owner.userId && m.Body.trim() === '通知测试' && m.context_token);
      if (!ready) await new Promise(r => setTimeout(r, 1000));
    }
    if (!ready) throw new Error('未收到本人测试口令，本次未发送。');
    console.log('已收到本人测试口令，请锁屏手机。30 秒后发送一条合成提醒。');
    await new Promise(r => setTimeout(r, 30000));
    const result = await call('wechat_send', { accountId: owner.accountId, to: owner.userId,
      text: '[合成通知测试] 聊天记忆助手机器人通知。不包含真实聊天内容。', filterMarkdown: false });
    console.log(result.hadContextToken ? '接口已接受测试消息；请确认手机是否弹出通知。' : '上下文令牌缺失，结果待核实；不要重复发送。');
  }
} catch (error) {
  if (machineMode) console.log(JSON.stringify({state:'uncertain'}));
  console.error(error.message?.startsWith('wechat_') || /需要|未收到|会话失效/.test(error.message) ? error.message : '连接或测试失败；未自动重试发送。请检查网络与登录状态。');
  process.exitCode = 1;
} finally {
  if (watchdog) clearTimeout(watchdog);
  await client.close();
}
