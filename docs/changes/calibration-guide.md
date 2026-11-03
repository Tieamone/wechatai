# 一次性校准与只读报告

`python -m tools.calibration_page` 只读生成 private/calibration/校准标注.html，读取 active 且未获当前版本人工确认的记忆。页面含摘要与原文，按本人/对方标注，必须留本机。它没有外部资源或请求，使用 CSP 和 nonce；正文以 textContent 渲染，JSON 转义小于号。

主结论一键六选：长期有价值、短期有价值、小事、有误、已过期、不确定。先看摘要和证据，必要时在可选维度填写证据支持、有效期、敏感类别，再点击主结论，会进入下一条；上一条可修改。健康或财务勾选默认关闭，未勾不等于已确认不敏感。所有维度默认空白，不替用户给结论。

浏览器存储可用时自动保存；不可用时先点“保存续填进度 JSON”，下次重新打开并导入。最终导出只含 ID、版本、主结论、勾选及用户明确填写的独立维度，不含摘要或原文。文件头包含证据快照指纹；不同快照、重复 ID、版本变化、未知字段或值会拒绝整个导入，不能把旧答案套给新原文。保存的进度文件也须留本机。

`python -m tools.calibration_agreement reviewer.json calibration-labels.json` 读取明确给出的审核员输出和人工结果，分别计算证据支持、类别、有效期、敏感的一致数/可比较数和混淆表。输入格式见 review-v1 草案；审核员输出必须包含相同 snapshot_sha256，逐条映射到 memory_id/memory_version。长期/短期/小事只提供类别标签，不能推断准确性；其他缺失、不确定或版本不匹配不计一致率，覆盖数单列。小样本不能用于自动模型选择、开启提醒或产品决策。

`python -m tools.workload_report` 只读统计当前 active/paused 历史范围，未真实提炼窗口包含 prepared、failed、blocked_privacy 及合成完成，排除 validated_model/cancelled 和自动范围。阻断窗口不等于可立即付费运行。以 v2 近似每窗 2600 Token 估算，仅用于预算规划，不含未来精修、敏感双审核或重试成本。

当前精确快照在北京的公开原价：忙时输入/输出每百万 Token 2/8 元，闲时 1/4 元，输出包含推理。使用输入/输出极端配比给金额上下界，不把所有 Token 都按输出价算成精确金额；不计缓存和折扣，不代表真实账单或账号免费额度。一个 1M 总 Token 预算约覆盖 floor(1000000/2600)=384 个窗口，按 90% 留余量约 346 个。价格于 2026-11-03 核对：[官方价格](https://help.aliyun.com/zh/model-studio/model-pricing)、[精确快照说明](https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash)。不写入价格设置。

未人工确认记忆当前仍参与普通搜索、召回与提醒。代码依据：backend/memories.py 的 listing 默认 active、review 只是分类；backend/associations.py 的 rank_candidates 遍历全部 active，context 核验证据和版本；backend/notifications.py 的 WATCH_VALID 与 _deliver 不读取 memory_review；普通提醒的 confirmed_model 是关联判断结果，不是人工确认。仍须满足现有自动开关、隐私、时序、通知策略，未确认本身不会直接造成发送。

temporal_kind 现有 preference/plan/past_event/fact/unknown 仅是事项类型，出现在 MemoryDraft/Replacement/Extraction、指纹、创建/更正、前端标签中。当前没有基于该字段的生命周期权重；长期/短期应新增独立维度，不能直接把 plan 当短期或 preference 当长期。完整迁移与业务设计见 two-stage-pipeline.md。
