# Hero Radar：更早发现下一个 AI 应用层爆发项目

## 1. 简介

题目是：更早监控到下一个 Hermes 或 OpenClaw。

我的解法是 Hero Radar：一个面向 AI 产品判断的 early-signal intelligence dashboard。

产品逻辑只有两步：

- 先用数据找异常：从公开 source 里抓 daily signal，用 deterministic rules 找 acceleration、burst 和 cross-source resonance。
- 再用模型做判断：把同一项目的证据合并成候选，只让 Kimi scorer 判断少量候选是否代表 workflow breakthrough。

Primary user 是需要持续判断 AI 应用层机会的产品负责人和 devtools builder。Secondary user 是投资研究者和创业者。

他们每天真正要回答的问题是：

> 哪个项目已经出现工作流突破，但还没有完全成为共识？

Hero Radar 的目标是更早捕捉产品先机。

这个系统关注三类信号并筛选值得关注的产品：

- 增长信号：stars、forks、downloads、rank、mentions 的变化速度，通过 deterministic rules 做第一层筛选。
- 交叉信号：同一个项目是否在多个 source 里同时出现，通过 entity grouping 和 classifier 合并证据。
- 产品信号：项目是否真的改变了一个使用场景或工作流，通过 agentic LLM 做打分和分析。

最终用户每天看到的结果：

- 今天最值得看的项目。
- 它为什么值得看。
- 支撑证据来自哪里。

## 2. 产品前期调查

### 2.1 市场分析

公开市场里已经有很多原始信号：

- 项目发布平台：GitHub、Product Hunt。
- 科技讨论平台：HN、X。
- 包和模型平台：npm、PyPI、Hugging Face。

也有一些工具在做趋势判断，比如 RepoFOMO、Trending Repos、Trendshift。

真实替代方案通常有五类：

| 真实替代方案 | 用户怎么用 | 缺口 |
| --- | --- | --- |
| 手刷 GitHub / HN / X / Product Hunt | 高频打开多个平台，看今天有什么新项目 | 消耗注意力，同一项目在不同 source 里的证据很难合并 |
| GitHub trending / RepoFOMO | 看 repo 增长和 trending rank | source 偏单一，容易只看到 GitHub 热度 |
| Product Hunt / HN trending | 看发布和社区讨论 | 更接近共识形成阶段，早期 acceleration 不够突出 |
| Social listening | 看 X mentions、KOL 推荐和讨论扩散 | noisy，缺少 projectness 判断和 source-native evidence |
| Package / model platform browsing | 看 npm、PyPI、Hugging Face 的新增包和资源 | 容易看到 raw artifact，难判断产品或工作流意义 |

这些方案更像信号展示层，离产品判断还有一层距离。

Hero Radar 的差异点是：

- 以项目 entity 为中心，避免把单个平台的 source item 当成最终对象。
- 用 deterministic rules 先找变化，再用 LLM 判断变化背后的产品含义。
- 用 Hermes 和 OpenClaw 这类已知爆发项目做回测，校准入池 threshold 和早期信号。

核心价值是把分散信号变成可解释的产品判断。

### 2.2 回测研究

市场上有很多信号，但产品问题还是要回到一句话：

> 什么项目算好项目？

为了把这个问题具体化，我用题目里的 Hermes 和 OpenClaw 做回测。方法是收集这两个项目爆发前后的多渠道历史数据，观察它们在成为共识前，各个 source 发生了什么。

#### 回测方法

- 选取 Hermes 和 OpenClaw 作为已知爆发项目。
- 收集项目爆发前后的 GitHub、HN、Product Hunt、npm、Hugging Face 等历史数据。
- 对比信号出现时间、增长加速度、跨 source 共振、alias 迁移。
- 用当时可见的数据反推：系统能否在项目成为共识前把它放进候选池。

#### 回测数据

OpenClaw 的外部共识窗口我用 `2026-01-29` 到 `2026-01-30` 作为锚点：`2026-01-29` [Axios](https://www.axios.com/2026/01/29/moltbot-cybersecurity-ai-agent-risks) 已经把 Moltbot/Clawdbot 作为硅谷新一轮 AI agent 关注对象报道；`2026-01-30` [外部报道](https://hyperight.com/openclaw-ai-assistant-rebrand/)开始出现 viral 和 100k stars 叙述。

| 项目 | 早期信号 | 关键数字 | 发现 |
| --- | --- | --- | --- |
| Hermes | GitHub acceleration 先出现，HN 有弱 corroboration | acceleration T0：`2026-03-11`；star band 中位约 `4.45k`；后续 velocity peak：`2026-04-06`；star band 中位约 `30.5k` | 如果在 acceleration 阶段入池，可以比后续共识阶段早约 26 天看到 |
| OpenClaw | 旧 alias `clawdbot` 先在 npm、HN、Hugging Face 出现，外部共识窗口在 `2026-01-29` 到 `2026-01-30` | npm downloads：`2026-01-25` 为 `70,768`，`2026-01-26` 为 `106,024`；`@clawdbot/*` 4 个 scoped packages 在 `2026-01-25` 同步发布；HN 旧 alias 在 `2026-01-25` 到 `2026-01-27` 约 42 条 stories | 系统可以在 `2026-01-25` 通过 npm floor + package burst + HN 旧 alias 入池，比外部共识窗口早约 4-5 天 |

#### 回测 implication

- Candidate rules 要关注 acceleration。total popularity 适合作为结果确认，acceleration 更适合早期入池。
- Entity grouping 要支持 repo、domain、package、alias、source link、canonical URL 的合并。OpenClaw 这种 case 不能只靠项目名。
- Cross-source evidence 要进入候选逻辑。多个弱信号在短时间内同时出现，比单个 source 的绝对热度更有参考价值。
- LLM scorer 要判断项目本身的产品含义。deterministic rules 能发现变化，LLM 负责判断这个变化是否代表产品或工作流突破。

## 3. 产品考量和解法

### 3.1 HN 和 X 的信号有价值，但噪声比较多，需要清洗

问题：HN 和 X 往往最早出现讨论，但 raw item 里混着文章、教程、泛概念、公司新闻、段子和无链接推荐。  
如果直接把这些内容送进候选池，系统会把“有人讨论 AI”误判成“某个项目在爆发”。

解法：

- 先用 classifier 做 projectness 判断：这条信号是否指向一个具体项目，是否能提取 name、link、summary 和 confidence。
- 再做 entity-level 聚合：同一个项目是否被多个 story、tweet、author、link 或 alias 稳定指向；无 citation、弱绑定、泛概念信号降级或丢弃。

### 3.2 模型成本高，需要分层处理

问题：每天抓到的 source item 很多。  
如果全量交给强模型打分，成本高、延迟高，也会让模型在大量低质量 item 里消耗注意力。

解法：

- 第一层用 deterministic rules 和 entity grouping 做高召回候选池，把几千条 raw signal 压到少量候选项目。
- 第二层再使用 LLM：classifier 用更便宜的模型清理 noisy source，Kimi scorer 对少量候选做 agentic investigation、评分和 Daily Feed brief。

## 4. 产品视图和用户路径

### 4.1 User journey

目标用户每天要完成一个任务：用很短时间判断今天有没有值得关注的 AI 应用层机会。

用户路径按任务展开：

| 用户任务 | 对应视图 | 用户要做的判断 |
| --- | --- | --- |
| 今天有没有值得看的项目 | Daily Feed | 是否深入研究、是否加入个人 watchlist |
| 各平台从 data/rules 看最强的候选是什么 | Candidate Pool | 哪些项目从数据上值得继续看 |
| 各平台今天原始抓到了什么项目 | Sources | 浏览 GitHub、HN、PH、npm、HF、X 的 source-native items，也可以回查某个判断的原始 evidence |
| 当前雷达参数是否要调整 | Settings | source、关键词、模型、预算、X/Twitter 账号是否要调整 |

这条路径的设计原则是先给结论，再给候选，再给证据，最后给控制权。

### 4.2 产品视图和功能

#### a. Daily Feed

Daily Feed 是用户每天真正消费的页面。  
它把大量 source noise 压缩成少量 Kimi scorer picked items，让用户先看到结论，再决定是否下钻。

用户能看到：

- 今日重点项目。
- 候选信号。
- 完整评分记录。
- 项目分数和关键维度。
- 支撑证据和 source links。
- brief、use case、caveat。

Kimi scorer 主要按四个产品维度打分：

- Workflow shift：项目是否改变了某个具体工作流。
- Technical substance：项目是否有足够技术或产品实质。
- Product-market fit：是否有清晰用户、场景和采用路径。
- Momentum：当前增长和跨源证据是否可信。

这里有两个层面的 brief：

- 评分层 brief：所有被 Kimi scorer 看过的项目都会有结构化分数和简短判断，方便用户快速扫。
- 重点层 brief：进入今日重点的项目会有更完整的中文分析，解释它改变的工作流、适合关注的 use case、以及主要 caveat。

Feed 里也需要处理权重问题。

例如 OpenAI、Anthropic、Google、Microsoft 这类大厂项目可能很重要，但它们容易长期占据注意力。  
所以这类项目可以被打分和留档，同时标记为大厂或 score-only，避免挤占早期项目的今日重点位置。

用户能做的操作：

- 快速判断今天是否有值得深入看的项目。
- 打开项目链接或 source evidence。
- 查看项目为什么被打分。
- 对 Feed 结果给反馈，后续沉淀成个人偏好。

![Daily Feed](assets/hero-radar-feed.png){width=100%}

#### b. Candidate Pool

Candidate Pool 是高召回候选池。  
它保存还没有进入重点推荐、但已经出现足够信号的项目，是 Daily Feed 前面的注意力缓冲层。

这个视图回答的问题是：

> 为什么这个项目值得进入观察？

用户能看到：

- 项目来自哪些 source。
- 哪些 signal 让它入池。
- 是 acceleration、rank、download burst、HN 讨论、X mention，还是跨源共振。
- classifier 发现的 alias、link、canonical name。
- 当前 level：high potential、potential、edge watch。
- 项目的 context preview 和原始链接。

Candidate Pool 的价值是保留早期不确定性。

有些项目还没有足够证据进入 Daily Feed，但已经不应该被丢掉。  
Candidate Pool 给用户一个更宽的观察面。

用户能做的操作：

- 按 source 或 level 筛选候选。
- 查看候选项目的入池原因。
- 对比同一个项目在多个 source 里的证据。
- 从候选池继续打开项目主页、repo 或原始讨论。

![Candidate Pool](assets/hero-radar-candidates.png){width=100%}

#### c. Sources

Sources 是原始事实层。  
它的作用是让判断可追溯，也让用户能验证系统是否看漏了某个 source。

用户能看到不同 source 的 source-native facts：

- GitHub repo 的 stars、forks、velocity、rank。
- HN 的 title、points、comments、time。
- Product Hunt 的 rank、votes、comments。
- npm / PyPI 的 package、downloads、release 信息。
- Hugging Face 的 models、spaces、datasets。
- X 的 tweets、authors、mentions。

Sources 适合两种场景：

- 用户想验证 Feed 或 Candidate Pool 的判断。
- 用户想直接浏览某个 source 今天发生了什么。

这里尽量保留原始数据形态。  
模型判断放在 Feed 和 Candidate Pool，Sources 负责提供证据底座。

![Sources](assets/hero-radar-sources.png){width=100%}

#### d. Settings

Settings 是定义雷达边界的地方。  
它决定系统今天看哪里、抓多少、让模型花多少钱、重点追踪哪些关键词和账号。

用户能控制：

- 哪些 source 开启。
- 每个 source 抓多少。
- GitHub / HN / npm / X 搜什么关键词。
- X 追踪哪些账号或 seed network。
- Kimi scorer 使用哪个模型。
- Kimi scorer 能花多少预算。
- 是否允许 web search。
- API key 和 source 状态。

Settings 的产品意义是透明和可控。

它告诉用户：今天这个雷达看了哪里，没看哪里，花了多少模型预算，判断建立在哪些 source 上。

![Settings](assets/hero-radar-settings.png){width=100%}

## 5. 项目亮点

- 更广：同时看 GitHub、HN、Product Hunt、npm、PyPI、Hugging Face、X，覆盖 repo 增长、社区讨论、产品发布、包使用、模型/demo 发布和社交传播。
- 更早：Hermes 可在 `2026-03-11` acceleration 阶段入池，早于 `2026-04-06` velocity peak 约 26 天；OpenClaw 可在 `2026-01-25` 通过 `clawdbot` npm/HN 信号入池，早于 `2026-01-29` 到 `2026-01-30` 外部共识窗口约 4-5 天。
- 更准：deterministic rules 先找变化，Kimi scorer 再判断 workflow shift、technical substance、product-market fit 和 momentum，降低普通新闻、教程、wrapper 对 Daily Feed 的干扰。
- 更完整：同一个项目可能用 repo、package、domain、alias、source link 多种形态出现，系统会把这些证据合并到同一个候选项目上。
- 更可查：Feed 和 Candidate Pool 里的判断都能回到原始 repo、HN story、Product Hunt launch、npm package、HF resource、X tweet。
- 更可控：用户可以调整 source 开关、抓取量、GitHub/HN/npm/X 关键词、X/Twitter 账号、Kimi scorer 预算和 web search 权限。
- 更可扩展：未来可以通过 MCP 把 candidates、evidence、feed、settings 暴露给用户自己的 agent，作为外部 agent 的 intelligence layer。

## 6. 技术实现

### 6.1 数据采集层

Pipeline 每天从多个 source 拉取数据，并统一成 source item。

主要 source 包括：

- GitHub Trending / Search。
- Trending Repos / RepoFOMO。
- HN Algolia / Firebase。
- Product Hunt。
- Hugging Face。
- npm / PyPI。
- X seed accounts / tweets。

每条 source item 保留 source、external id、name、url、heat、velocity seed、rank、description、metadata 和 raw data。

### 6.2 Entity resolution / grouping

系统把不同 source 里的同一项目合并成 entity。

优先使用确定性 key：

- GitHub repo。
- domain。
- package link。
- canonical URL。
- source metadata。

名字相似只作为弱线索。

这个阶段解决的问题是：同一个项目可能在不同 source 里以不同名字出现。

### 6.3 Candidate rules

Candidate rules 根据 evidence 选择候选。

规则包括：

- GitHub acceleration / velocity。
- Trending Repos / RepoFOMO 增长。
- HN points / story count。
- Product Hunt rank。
- Hugging Face resources。
- npm download burst / package family。
- X social evidence。
- 48 小时内多个弱信号共振。

候选分为 high potential、potential 和 edge watch。

### 6.4 Classifier pipeline

Classifier pipeline 是低成本 LLM 层，处理 HN 和 X 这类 noisy source。

它只回答一个问题：

> 这条 raw signal 是否能变成可合并的 project evidence？

HN classifier 判断 projectness，并提取 canonical name、links、summary 和 confidence。

X classifier 分两阶段：

1. tweet-level triage。
2. entity-level aggregation。

Classifier 的 harness 约束四件事：

- 输入边界：只给当前 story/tweet 及必要上下文，不把整天的 raw feed 塞给模型。
- 输出 schema：必须输出 projectness、canonical name、links、summary、confidence、negative reason。
- confidence gate：低 confidence、无 citation、纯观点、泛概念信号降级或丢弃。
- merge proposal：只有当 name、link、domain、repo、package 或 alias 有稳定证据时，才提出 entity merge。

Classifier 输出 evidence rows、alias links 和 merge proposals，供 grouping、candidate selection 和 Kimi scorer 使用。

## 7. Kimi scorer mechanism

Kimi scorer 是一个受控的 agentic workflow。

它的产品任务是：把已经入池的候选项目，从“有信号”推进到“值得不值得今天看”。

设计重点是给模型主动调查空间，同时把调查范围、上下文、预算、输出结构和 eval 全部收住。

### 7.1 ReAct-style scoring loop

Kimi scorer 的每个候选项目独立运行。  
每个 run 可以理解成一个小型 research loop：

基本流程：

1. 输入 compact candidate context：canonical name、candidate level、source coverage、evidence summary、metadata、已知 links。
2. Kimi 先判断当前证据是否足够评分。
3. 如果证据不足，输出 `use_tools`，并说明要补哪类证据。
4. 系统执行工具，把结果压缩成 observation。
5. Kimi 读取 observation 后继续判断。
6. 证据足够或预算耗尽后，输出 `final`：score、supporting evidence、negative evidence、known gaps、should print 和中文 brief。

这个 loop 的核心 contract 是：

| State | Model decision | System responsibility |
| --- | --- | --- |
| `score_ready?` | 当前证据是否足够评分 | 提供 compact candidate context 和 rubric |
| `use_tools` | 需要补哪类证据、调用哪个工具 | 执行 tool、限流、做 URL/path 校验、压缩 observation |
| `continue` | observation 是否改变判断 | 更新当前 candidate state，不暴露完整 raw trace |
| `final` | 输出 score、evidence、known gaps 和 brief | 做 schema 校验、repair、落库、进入 feed selection |

模型每一步只做一个决策：

> 当前证据够不够？如果不够，最小必要补证据是什么？

当前预算设计：

- 最多 3 个 investigation turns。
- 每个候选最多 8 次 tool calls。
- web search、repo file、homepage/docs 都有单独上限。
- 候选之间独立执行，单个失败不会影响整次 feed run。

### 7.2 Scoring rubric

Kimi scorer 主要看四个产品维度，每个维度回答一个产品问题：

- Workflow shift：这个项目是否改变了一个具体工作流？
- Technical substance：它是否有真实技术或产品实质？
- Product-market fit：它是否有清晰用户、场景和采用路径？
- Momentum：当前增长和跨源证据是否可信？

这样做的原因是：项目早期热度本身不够，模型需要判断热度背后的产品含义。

评分时优先看用户和工作流，再看热度。  
一个项目即使增长快，如果只是普通 wrapper、营销新闻、资源列表或无明确采用路径，也会被压低。

实现上还有一组硬规则和 route 规则：

- 新闻、教程、纯模型发布、资源列表会被压低。
- 大厂常规动态可以 score-only，不挤占今日重点。
- 证据不足时要求模型降低确定性，并写出 known gaps。

### 7.3 Tool design 和 guardrails

Kimi scorer 能用的工具很少，都是 primitive tools。  
工具的目标是补证据，不让模型自由浏览整个互联网或整个数据库。

| Tool | 用途 | Guardrail |
| --- | --- | --- |
| `read_evidence_rows` | 读取结构化 evidence，避免只看摘要 | 按 entity id 读取，有条数上限 |
| `fetch_github_readme` | 判断 repo 定位、安装方式、使用场景 | 字符数上限，结果压缩 |
| `fetch_github_file` | 读取少量关键文件，例如 `package.json`、`pyproject.toml`、`README` | path 白名单 |
| `fetch_homepage_or_docs` | 验证官网、文档、use case | URL 安全校验和字符数上限 |
| `web_search` | 外部补证据 | 每个候选默认最多 1 次 |

Execution harness 负责运行工具和记录 trace：

- 每个工具有 timeout 和错误记录。
- tool result 会被压缩成 observation。
- 失败以 structured error 回到模型，不中断整次 feed run。
- tool call 次数上限。
- investigation turn 上限。
- web search 次数上限。

Output harness 负责落库前校验：

- `final` 必须符合 JSON schema。
- score、evidence、known gaps、brief 必须可解析。
- schema 不合法时进入 repair；repair 失败则记录 scorer error。

### 7.4 Context management 和 compaction

Kimi scorer 不能直接吃下所有 raw data。  
这里的核心是 context engineering：让模型看到当前决策需要的信息，把其余内容留在数据库和 tool trace 里。

我把上下文分成三类：

- Always included：rubric、route rules、candidate context、evidence summary、source coverage、candidate level。
- Retrieved on demand：evidence rows、README、package metadata、homepage/docs、web search result。
- Excluded from prompt：完整 raw source table、无关 source rows、完整 tool trace、历史 run logs。

具体机制：

- Candidate context 是入口：Grouping 先把 GitHub、HN、npm、HF、X evidence 合成一个候选，只把项目级 compact context 交给 Kimi。
- Tool retrieval 是按需发生：模型必须说明需要补什么证据，系统再调用对应工具。
- Observation compaction 会把工具结果压成判断字段：title、url、snippet、README 摘要、package metadata、key claims。
- Trace separation 把完整 source items、evidence rows、turn trace、tool trace 和 final score 存在 SQLite，prompt 里只保留当前轮需要的 observation。
- Candidate isolation 让每个候选独立执行，避免一个项目的 research dead end 污染另一个项目。

这个 loop 更接近 `Research -> Compact -> Continue -> Persist`：

- Research：允许 Kimi 用工具补最小必要证据。
- Compact：把工具结果压缩成 high-density observation。
- Continue：下一轮只带 compact observation 和必要 state。
- Persist：完整 trace 留在 SQLite，用于复盘和 eval。

目标是用更少 token 保留更高的信息密度，让模型的注意力集中在产品判断上。

### 7.5 Eval

Eval 的目标很简单：固定系统的判断边界。

我主要做三组 case：

| Case 类型 | 样本 | 期望结果 |
| --- | --- | --- |
| Golden cases | OpenClaw、Hermes Agent、HeyClicky | 应该被识别成高价值早期机会，进入高分区 |
| Negative cases | funding news、普通教程、模型发布、generic chatbot、普通 dashboard/editor/calculator | 不应该进入今日重点，分数要被压低 |
| Borderline case | screen-aware spreadsheet operator | 有真实 workflow utility，但还不一定是爆发项目，用来校准中间区间 |

这组 eval 回答的是一个具体问题：

> Kimi scorer 和 deterministic rules 能不能稳定地区分“早期产品机会”和“看起来热闹的噪音”？

当前 deterministic scorer eval 是 `9/9` 通过。Kimi scorer 用同样的 case 思路做 smoke run，重点看高价值项目是否能被选出来、普通噪音是否被压下去、中文 brief 是否说清楚产品价值。

## 8. 未来方向

### 8.1 MCP as intelligence layer

Hero Radar 更适合先作为信息聚合层开放出去。

产品主入口继续保持 dashboard。  
这个产品的主要消费场景是每天看 brief、看候选、看证据，用 chat box 作为主入口价值不高。

未来更合理的方向是 MCP。

- 查询今天的高分项目。
- 读取某个项目的 evidence rows。
- 请求 Kimi scorer brief。
- 把项目加入 watchlist。
- 修改 source、关键词、预算、X/Twitter 账号等设定。
- Explore 本地 database，让用户自己的 agent 做后续 research。

MCP 更适合这个场景。

- 用户自己的 agent 需要 dedicated tools。
- Evidence、candidate、feed、settings 都可以做成稳定 tool surface。
- Dashboard 负责人看，MCP 负责 agent 用。

### 8.2 Feedback-driven self-evolving harness

未来用户看到打分和 brief 后，可以直接反馈。

交互可以很简单：

- thumbs up / thumbs down。
- 一个 reason box，写为什么这个项目好或不好。

当前 harness 还没有 memory 模块。  
未来需要新增一个 memory layer，把用户反馈沉淀成可调用的偏好和 eval 数据。

这些反馈进入 self-evolving harness：

- 好项目和坏项目会沉淀成新的 eval case。
- 用户的理由会被转成 preference notes。
- Memory module 记录用户长期偏好，例如更关注 devtools、prosumer workflow、enterprise adoption，或更反感普通 wrapper。
- Eval case 会反过来校准 Kimi scorer 的 rubric、权重和打分规则。
- 高频负反馈会影响 source 权重、candidate ranking 和 brief 关注点。

这里需要保留一个防偏机制：

- 用户反馈先进入 preference notes 和 eval case，不直接改全局 scorer。
- 高频反馈需要通过 human review 或 batch eval 后再进入权重调整。
- 个性化 preference 和全局 quality rubric 分开存储，避免用户偏好把系统调得过窄。
- 每次规则更新都保留 version 和 before/after eval 结果，方便回滚。

最终目标是让 Hero Radar 越来越接近用户自己的项目判断标准。
