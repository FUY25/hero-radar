# Engineer Prompt

你要实现这个计划：

`docs/superpowers/plans/2026-05-31-pre-layer2-decision-pipeline.md`

请按 TDD 执行：每个 task 先写/跑 failing test，再做最小实现，再跑到通过，再提交。计划里的代码块是参考实现和行为约束，不是必须逐字照抄；如果真实代码结构或测试需要调整，可以改，但不要改变计划定义的外部行为、表结构、API contract 和 scope。

本 slice 只做 pre-Layer2：deterministic entity resolution、rules/evidence/candidate pool、bounded GitHub backfill、candidate API、最小 Candidate Pool UI、HF card GitHub-link enrichment。不要实现 Layer2 Daily Feed selection、Kimi deepdive、chatbot、cron、X LLM classifier、npm backfill 或规则编辑。

如果可以，用 subagents 并行开发，但要按文件所有权切开，避免冲突。例如 schema/entity/rules 一组，backfill/API 一组，React shell 一组，HF enrichment 一组。每组先跑自己的测试，合并前再跑全量测试。

UI 相关只做计划里明确的最小 shell。凡是涉及视觉风格、Feed 卡片形态、布局取舍、交互设计、文案层级的非平凡决定，都先停下来用中文问我确认。非 UI 的工程问题可以按计划和现有代码风格直接判断，不要反复问。

和我沟通请用中文，简洁汇报：正在做哪个 task、卡在哪里、需要我决定什么。不要重复计划正文。不要打印 token/secrets，不要改不相关文件，不要 revert 现有未提交改动。外部调用保持 bounded；测试里使用 fake client，不要在测试里打真实付费/LLM API。
