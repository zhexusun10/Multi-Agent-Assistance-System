# Orchestrator 与 RAG 重构记录

## 第一步：三路意图识别（已通过组件验收）

参考本机 EchoMind/core/intent_recognizer.py、intent_embedding.py、tooling/knowledge_base.py 以及对应学习文档和教程，按真实源码而非旧说明迁移。

| 参数 | EchoMind 源码 | 本项目 |
|---|---|---|
| LLM / 向量 / 规则权重 | 0.7 / 0.2 / 0.1 | 相同，用户已确认 |
| 每意图模板 | 3条 | 14类共42条，每类3条 |
| LLM few-shot | 每类取第1条 | 相同 |
| 历史上下文 | 最近3条消息 | 相同，另携带当前需求版本和推荐存在状态 |
| 温度 / 输出预算 / 超时 | 0.1 / 256 tokens / 8秒 | 相同 |
| 置信阈值 | 0.5 | 相同，降级后路由也执行该阈值 |
| 同组粗细融合 | 先加领域分，再选细分类 | 相同 |
| 规则细化条件 | 规则≥0.5、组分<0.8 | 相同 |
| 缓存 | 1000条、满时清500条、常规300秒 | 相同，键额外包含需求及推荐上下文 |
| 向量后端 | 意图BGE-M3或字符哈希；知识库Chroma | 按用户要求意图也使用Docker Chroma；无哈希伪装语义向量 |

房地产的顶层意图为找房、知识、解释、跟进，细分更新条件、联系、看房、推荐总结、会话总结和跟进状态；低置信度或不相关请求澄清，不默认搜房。识别及路由代码在 `src/agents/orchestrator/`；`src/domain/intents.ts` 仅兼容导出，不含旧关键词单路判断。

嵌入模型使用本地多语言 MiniLM ONNX，缓存保存在 `data/.runtime/models/`，无需新增API密钥。初测英文MiniLM的中文模板领域识别约52%，因此替换为多语版本。模型身份进入集合名，避免不同模型的同维向量混用；Docker仅绑定127.0.0.1:8002。

LLM和向量并行，规则同步；工具权限、房源选择、需求版本和发送审批仍由代码控制。远程识别不占用数据库事务锁；请求先记录到arrival-order操作表，较早找房请求不能覆盖较新搜索，未完成识别可在服务恢复后重试。

验收：`evaluation/reports/orchestrator-intent-live.json`，42条独立回归问句，实际DeepSeek与Chroma三路全部成功；业务路由准确率和宏F1均1.00。该结果限于本回归集，曾发现的错误已修正，不是泛化准确率声明。

## 第二步：政策语料与混合RAG（已通过检索、回答及网页验收）

按用户要求新增Markdown资料、离线切片、Chroma向量、中文BM25、RRF与引用答案。EchoMind原实现没有BM25/RRF，这两项是本项目按用户要求新增；文档切片500字符、无重叠、原始查询加3条改写、Top-K默认5等参数参照其知识库及工具管理器。

### 语料及证据范围

`knowledge/rental/` 含8篇正文、40个块：私人住宅、HDB租客与卧室出租、租约条款、印花税、经纪人与防骗、交接与争议，以及推荐证据、手动跟进两篇项目说明。六篇政策资料分别链接URA、HDB、IRAS、CEA、SPF和新加坡法院。正文为中文整理与项目操作建议，不冒充官方网站逐字翻译；明确分开官方规则与协商清单。每篇含稳定ID、标题、scope、revision、checked_at、review_after、sources。整理复核日2026-10-20，下次复核截止2027-01-20；截止后不继续作为有效政策依据。

主要来源：

- [URA 私人住宅出租](https://www.ura.gov.sg/guidelines/property-and-business-owners/property/renting-property/)
- [HDB 卧室出租规定](https://www.hdb.gov.sg/managing-my-home/home-ownership/renting-out-a-flat-or-bedrooms/renting-out-bedrooms/regulations)
- [HDB 卧室出租条款与资格](https://www.hdb.gov.sg/apply-manage-and-renew-rental-of-bedroom/terms-and-conditions)
- [IRAS 租赁印花税](https://www.iras.gov.sg/taxes/stamp-duty/for-property/renting-a-property)
- [IRAS 出租物业及盖印期限](https://www.iras.gov.sg/taxes/property-tax/property-owners/your-situation/renting-out-my-property)
- [CEA 租赁交易、协议及佣金](https://www.cea.gov.sg/consumers/engaging-a-property-agent/renting-or-renting-out/)
- [SPF 冒充经纪人的租房诈骗提示](https://www.police.gov.sg/media-hub/news/2024/20241121_police_advisory_on_rental_scams_involving_the_impersonation_of_property_agents)
- [新加坡法院 SCT 受理范围](https://www.judiciary.gov.sg/civil/cases-eligible-small-claim)

### 运行链路与代码

1. `retrieval/index.py` 读取Markdown元数据，按标题分段、按句子与换行打包500字符块，无重叠。内容、参数和模型身份共同决定generation，chunk ID含内容哈希。整个新Chroma集合写全并校验ID后，原子替换本地manifest；不会先清空旧集合。相同内容重复导入复用现有向量。
2. `retrieval/embeddings.py` 用本地384维多语言MiniLM ONNX；向量L2归一化，拒绝非有限值。意图与知识集合分开；模型名进入集合身份。首次需要下载模型，之后本地推理，无额外embedding API Key。
3. `src/domain/knowledge.ts` 使用原问题加最多3条模型改写。改写max_tokens=512、temperature=0.3、超时10秒，原问题永不丢失。配置模型失败时仍可使用原问题。
4. `retrieval/hybrid.py` 并行执行Chroma余弦召回和BM25。`topics.py` 单独维护中英词汇扩展，jieba切词；BM25对完整正文统计打分，文档没有手填keywords数组。每路每问召回至少5个；各路先对改写去重融合，再以RRF `sum(1/(60+rank))` 合并两路，避免改写数量改变通道权重。候选上限20。
5. 模型从候选选最多5个相关块，验证ID与去重；失败保留RRF顺序。答案逐句绑定chunk和原样摘录。代码校验出处、审核期、摘录连续匹配和数字；引证错误最多修正一次，再做独立语义支持检查。通过的引用才展示。无支持时说明，不能以常识补政策或具体房源当前事实。
6. 模型故障时仅展示注明降级的资料摘录；Chroma连接故障时仍可用BM25并标记`vector_unavailable`。全部检索失败返回不可用，不假报空结果。来源超期不能继续引用。答案缓存含generation，300秒、1000条上限及清除500条；资料更新不会误复用旧答案。
7. 回答及citation元数据存入PostgreSQL会话；前端`app.js`用textContent渲染，可展开原文摘录、复核日及官方链接，刷新后保留。系统说明以项目来源标识，不伪造政府链接。

### 本地部署与复现

Docker使用`chromadb/chroma:0.5.23`，仅暴露本机8002，命名卷持久化；Python客户端同版本。`fastembed==0.7.4`、`rank-bm25==0.2.2`、`jieba==0.42.1`及兼容numpy/posthog版本记录在`requirements-dev.txt`。已有PostgreSQL和Neo4j配置不变。配置`KNOWLEDGE_MODE=hybrid_rrf`，检索服务8091，网页8010。

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
# 安装依赖后，start-app统一启动Docker、导出意图模板、离线建立两套索引及服务。
.\scripts\start-app.ps1 -DataMode Sample -SkipDataImport
# 修改知识正文后单独重建；manifest原子切换，检索服务按请求读取新版本。
.\.venv\Scripts\python.exe -m retrieval.index
# 代码有变更再构建及重启指定组件。
npm run build
.\scripts\restart-component.ps1 -Name graph
.\scripts\restart-component.ps1 -Name retrieval
```

已有服务运行时不要重复start-app；先用stop-app统一停止，或仅重启指定组件。Full模式仍需原数据完整验收与队友Recommendation服务，本次不将样本宣布为完整市场。知识语料不是全面法律数据库，也不自动抓取更新；维护者应按来源页面复核并更新revision/日期后重建索引。

### 验收记录与复现命令

- 意图：42条真实DeepSeek+Chroma问句，业务路由准确率、宏F1均1.00，见`evaluation/reports/orchestrator-intent-live.json`。
- 召回：40条独立中英文问题，期望按文档和具体段落标注；不使用模型改写或重排的消融结果，段落Recall@5：向量0.95、BM25 0.95、RRF 1.00；MRR分别0.7975、0.8842、0.8833。MRR没有在所有指标上优于单路，不作夸大。见`rag-retrieval.json`。
- 回答：22条真实知识业务问题全部通过，逐条核验租期、资格、费用、印花税、诈骗、法院范围、地理证据和发送状态语义；无依据未来政策不编造，房源/退款保证明确不能承诺。摘录完整性全部通过。见`rag-answer-live.json`。评价是本组回归结果，不代表泛化100%。
- 部署工作流：知识→找房→解释→看房草稿→联系押金草稿→会话总结→知识问答；验证直接跳转、需求/推荐版本保持、LLM与向量实际执行、用户隔离。见`orchestrator-rag-workflow.json`。
- Chrome：真实输入知识问题、展开官方来源/摘录、刷新持久化、390px手机适配，无JavaScript错误。见`orchestrator-rag-ui.json`及同名desktop/mobile截图。没有向任何经纪人发送消息。
- 本地回归：137个Node测试通过，15个需显式启用的集成测试默认跳过；Python API、Webhook及检索共21个测试通过；TypeScript构建通过。另显式执行并通过新的部署工作流和原business-live/advanced-live两组真实SQL、Neo4j、快速修改、请求重放测试。超长未完成上下文明确失败并持久化错误，不作为routing状态无限重试。

```powershell
npm test
npm run typecheck
.\.venv\Scripts\python.exe -m unittest tests.test_retrieval
.\.venv\Scripts\python.exe -m scripts.evaluate_retrieval
$env:INTENT_LIVE='1'; node --env-file=.env --import tsx --test tests/orchestrator-intent-live.test.ts
$env:RAG_LIVE='1'; node --env-file=.env --import tsx --test tests/knowledge-live.test.ts
$env:ORCHESTRATOR_LIVE='1'; node --env-file=.env --import tsx --test tests/orchestrator-rag-live.test.ts
.\.venv\Scripts\python.exe scripts/test_orchestrator_rag_ui.py
```

### 发现并修复的业务边界

看房询问与一般联系、防骗咨询与代联系不再由某一个关键词决定。区域零米的关键回答曾因模型引文多出一字而被校验丢弃，现先请求精确引证修正再核验。待核实候选也能按页面序号解释，不只支持已验证组。集成测试另发现“新加坡国立大学三公里内”未出现“附近”时被原抽取器漏掉，已补齐具名地点加半径的识别，并排除面积及泛类别距离以保持原120条租房话术语义。
