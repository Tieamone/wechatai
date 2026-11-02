# 质量与成本改动

## 价值反馈

记忆卡提供“准确但不值得保留”。该反馈只写入现有 feedback 表，绑定当前记忆版本和证据 ID、修订号及片段范围，不保存文本，不改变记忆或准确性确认，不写入 suppressed_evidence。原文修订后原标签失效。评估材料将有效的 low_value 作为负向价值标签；确认准确性始终不等于有价值，没有正向价值标签时收益指标保持未知。本项不升级数据库。

## 诊断前缀

只有 D102、D106 保留 HTTP 状态与 content-type，前缀最多 2 KiB。可解析 JSON 先经过可见 content 和数值 usage 白名单；无法可靠排除推理正文的损坏 JSON、非对象响应不留前缀并记录原因。其他失败不新增前缀。HTTP 诊断接口只返回元数据，不返回前缀。诊断沿用原来的文件数量和总大小上限，不新增数据库字段。

## 旧留存转换

`python -m tools.sanitize_rejections` 默认只读，输出文件名、数量、前后字节数和 SHA256，不输出返回内容。用户审阅 dry-run 并明确授权后，停掉全部写入方，再传 `--apply-approved-manifest 清单SHA256` 执行。清单或文件变化会拒绝执行。只转换 rejection-*.json 中 HTTP JSON 的 raw，不处理数据库、人工备份或其他文件；保留 content、finish_reason、数值 usage，移除推理正文和未知字段。此实现交付时不执行真实改写。
