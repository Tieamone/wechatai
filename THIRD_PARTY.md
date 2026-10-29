# 第三方源码

本项目保留以下第三方源码及各自 LICENSE；根目录许可证不替代第三方许可证。

| 本地目录 | 上游 | 基准提交 |
| --- | --- | --- |
| tools/wechat-mcp | https://github.com/jimingyuan7/wechat-mcp | b89f9d6d581f4c1c44e71c86902ba7d90a1c3c8c |
| tools/wechatauto-replica | https://github.com/fanyuantaier/wechatauto-replica | 6e603974bbac3bda93d88bbd6995510ae2547f5c |

微信 MCP 包含本地 sendMessage 业务回执校验修复，测试位于 tests/wechat_bot_protocol.test.mjs。重新获取上游时不要覆盖该修复。

第三方源码以普通文件纳入，不是 Git 子模块；未上传其嵌套 Git 元数据、依赖目录、编译产物和演示媒体。请分别查看对应目录 LICENSE。采集业务只加载数据库读取模块，不代表启用整个第三方包的自动发送或 GUI 功能。

安装主项目依赖使用 setup.cmd；机器人依赖还需在 tools/wechat-mcp 中执行 npm ci --ignore-scripts 和 npm run build。账号绑定、密钥和登录凭据不会随源码提供。
