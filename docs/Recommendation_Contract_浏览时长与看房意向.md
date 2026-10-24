# Recommendation Contract：完整请求、浏览时长与 Book Viewing Listing

更新：2026-10-23。供江山对接。本文分为完整请求、浏览时长、看房意向三部分。

`【新增】`表示此次新增字段；`【已有】`表示此前已经存在。浏览时长采集、后端校验及原生时长反馈此前已有，本次补强可见性，并新增明确的累计摘要；Book Viewing Listing是本次新增。会话删除是独立的前后端功能，不作为推荐条件。

## 一、完整 Recommendation Contract

实际应用入口为 `POST http://127.0.0.1:8092/app/recommend`，需内部 `x-recommendation-token`。不能让浏览器自行提交行为上下文；由应用后端从当前会话的有效记录构建。

### 1.1 请求顶层全部字段

| 字段 | 类型 | 必填/默认 | 状态 | 含义 |
| --- | --- | --- | --- | --- |
| contract_version | string | 默认1.0 | 已有 | 传输契约版本保持1.0 |
| request_id | string | 必填 | 已有 | 每次显式重新推荐的唯一请求；相同ID重试必须保留同一请求体 |
| conversation_id | string | 必填 | 已有 | 当前会话ID；行为仅使用这个会话的数据 |
| requirements_version | integer ≥1 | 必填 | 已有 | 原生版本；适配器使用应用需求版本+1 |
| requirements | object | 必填 | 已有 | 显式需求，结构见下表 |
| feedback | array | 默认[] | 已有 | 已满足质量门槛、尚未消费的浏览时长事件 |
| previous_state_id | string/null | 默认null | 已有 | 原生上一状态；应用入口继续保留既有原生head适配与重放机制 |
| result_limit | integer 1–5 | 默认5 | 已有字段，2026-10-24调整上限 | 正式推荐最多5套；不足不凑数；资料待确认列表独立返回（最多4套） |
| behavior_context | object/null | 老客户端可省略；本应用每次提供 | **新增** | 两类行为的完整只读摘要，与显式需求并列，结构见下表 |

这两类行为是“推荐输入中的用户上下文”，不是用户必须满足的房屋条件，因此放在 `behavior_context`，不塞进 `requirements.criteria`，不让LLM再从文本提取，不增加显式需求版本。

### 1.2 requirements 全部字段

| 字段 | 类型 | 约定 | 状态 |
| --- | --- | --- | --- |
| requirements.listing_type | string | RENT | 已有 |
| requirements.criteria | Criterion[] | 硬条件及软偏好，criterion_id唯一 | 已有 |

每条 Criterion 的全部字段如下；本次没有新增筛选字段。

| 字段 | 类型 | 约定 |
| --- | --- | --- |
| criterion_id | string | 唯一条件ID |
| field | enum | price、bedrooms、floor_area_sqft、district_code、property_type、distance、commute、furnishing、availability、lease_months |
| operator | enum | eq、in、gte、lte、between |
| value | number/string/number[]/string[] | 与字段及操作一致；between为两个有序边界，in为非空集合 |
| unit | string/null | price为SGD/month，bedrooms为null，面积sqft，distance为m，commute为minutes，lease_months为months |
| strength | enum | hard或soft |
| weight | number >0 | 默认1；原生契约字段保留 |
| utility | enum | threshold、proximity、minimize、maximize，默认threshold；由既有适配和推荐规则处理 |
| destination_id | string/null | 具体地点ID，可选 |
| destination_name | string/null | 地点名，可选 |
| category | string/null | 地点类别，可选 |
| distance_type | enum/null | straight_line、walking、transit |
| transport_mode | enum/null | walk、transit、drive |

应用内部字段与原生名称不同，例如district→district_code、place_proximity→distance、commute_time→commute。独卫等原生不能表达的条件继续保留为待核实，不因为新增行为字段而静默删除或升级成通过。原有地理/路线能力缺口也没有在此变成已支持。

### 1.3 已有 feedback 的完整结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| event_id | string | 幂等事件ID，真实网页反馈形如web:segment_id |
| conversation_id | string | 必须等于请求conversation_id |
| recommendation_request_id | string | 用户当时看到的原推荐请求 |
| listing_id | string | 原房源ID，数字字符串 |
| event_type | string | listing_view_duration |
| source | enum | web_detail为真实网页采集；sample仅为已有人工样例接口 |
| occurred_at | 带时区ISO时间 | 完成时间 |
| payload.view_session_id | string | 唯一反馈段标识；应用适配器使用segment_id，防止重复消费 |
| payload.duration_seconds | number ≥0 | 从校验后的active_duration_ms除1000，单位秒 |

应用内部 `feedback` 还允许save、shortlist、explicit_attribute_feedback，但当前原生反馈契约只接收上表的时长事件；收藏不会伪装成浏览时长。

### 1.4 新增 behavior_context 的全部字段

| 字段 | 类型 | 状态 | 说明 |
| --- | --- | --- | --- |
| version | string | **新增** | 固定1.0，行为子协议版本 |
| as_of | 带时区ISO时间 | **新增** | 本次推荐请求的行为截点；取用户发起本轮搜索的操作创建时间 |
| listing_view_durations | array | **新增** | 每套房一个累计浏览摘要，初次为[]；详见第二部分 |
| book_viewing_listings | array | **新增** | 已保存有效看房草稿的房源摘要，按房源去重，初次为[]；详见第三部分 |

两张明细表中所有子字段也均为本次新增的摘要字段。它们不是原有feedback事件的重命名：feedback是增量可消费的评分事件，behavior_context是截至as_of的累计事实快照，不应把两者再加起来算一次时长。

### 1.5 报文示例

以下为合成示例，不是已确认的真实预约。

```json
{
  "contract_version": "1.0",
  "request_id": "f56a4e2b-9765-4482-a172-805e43cb5a03",
  "conversation_id": "2bc2b1ec-d323-43e3-a6cb-991e4cbdac09",
  "requirements_version": 2,
  "requirements": {
    "listing_type": "RENT",
    "criteria": [
      {"criterion_id":"rent","field":"price","operator":"lte","value":3000,"unit":"SGD/month","strength":"hard","weight":1,"utility":"threshold"}
    ]
  },
  "feedback": [],
  "previous_state_id": null,
  "result_limit": 4,
  "behavior_context": {
    "version": "1.0",
    "as_of": "2026-10-23T08:00:00Z",
    "listing_view_durations": [
      {
        "listing_id":"500285985","duration_seconds":45.25,"view_count":1,
        "last_viewed_at":"2026-10-23T07:58:00Z",
        "source_request_ids":["a028cc35-820d-43f6-92ef-7875ccff67e0"],
        "interest_eligible":false
      }
    ],
    "book_viewing_listings": [
      {
        "listing_id":"500285985","action_id":"e4287655-c28c-4a97-ad7f-70cf0cb30911",
        "source_request_id":"a028cc35-820d-43f6-92ef-7875ccff67e0","requirements_version":1,
        "starts_at":"2026-10-24T06:00:00Z","ends_at":"2026-10-24T07:00:00Z",
        "time_zone":"Asia/Singapore","recorded_at":"2026-10-23T07:59:00Z",
        "signal":"viewing_draft_saved","appointment_confirmed":false
      }
    ]
  }
}
```

初次推荐时两个数组都是[]。例如仅有一套45秒浏览还没有达到至少两套、总计60秒的既有评分门槛，所以示例interest_eligible=false，feedback也为空，但累计记录仍完整交给接入层。

### 1.6 当前接收与算法边界

- 应用请求定义：`src/contracts/recommendation-v1.ts`与`src/contracts/behavior-context.ts`。
- 应用入口的完整可执行Schema：`contracts/releases/recommendation-app-request-1.0.schema.json`；Python类型：`recommendation/app_contract.py`。
- 2026-10-24已合并原生Recommendation 2.0：`/recommend`与`/app/recommend`均接收behavior_context和cold_start，后者保留应用同源传输、身份绑定及完整存档。同一request_id变更行为内容仍报409，V2传输绑定独立保存，旧数据不删除。
- 行为现在由江山的新算法原生计分：`listing_view_durations`的会话累计摘要参与browsing分支，`interest_eligible`仍由应用既有质量门槛控制；`book_viewing_listings`中有效的`viewing_draft_saved`参与appointment分支。该信号仍不是已确认看房。feedback增量事件只校验和记录，不再重复计分。默认总分为0.6软需求+0.4行为，行为内为0.6看房意向+0.4浏览；实际以原生配置及原生返回为准，应用不重算分数。
- 原生Response升级为2.0，增加pool、resolution、selection、每套selection_reason/score_rank及新的软需求/浏览/看房意向分支。应用保存原始报文，并投影为原有工作台契约供Explanation与其他Agent使用。`selection.cold_start.active`现用于需求补充卡片；详细迁移见[2.0合并与真实追问](Recommendation_2.0_合并与真实需求追问.md)。

## 二、查看时长 listing_view_durations

### 2.1 字段明细（本表全部新增）

| 字段 | 类型/单位 | 含义 |
| --- | --- | --- |
| listing_id | 数字字符串 | 房源原ID |
| duration_seconds | number，秒 | 当前会话截至as_of已完成、后端接受的详情有效时长之和；毫秒/1000，可有小数 |
| view_count | 正整数 | 不同详情打开view_session_id数量；同一次打开因隐藏/恢复拆段不重复算一次打开 |
| last_viewed_at | 带时区ISO时间 | 最新纳入摘要的完成时间 |
| source_request_ids | UUID字符串数组 | 本会话中这些记录对应的原推荐请求，去重 |
| interest_eligible | boolean | 已验证房源的合格时长达到既有整组与单套门槛；不是喜欢程度或成交概率 |

### 2.2 采集和停止

展开详情并成功加载后才开始；仅列表卡片曝光不计时。需详情确实在可见区域、页面未隐藏、浏览器有焦点、用户在详情区域活动。切换标签/窗口、最小化导致的hidden/blur、详情移出视口、返回列表、切换会话、页面关闭、发起新搜索都会暂停或结束相应段。

继续保留30秒无操作暂停、超过5秒计时器空档不补算、每段最多120秒的保守规则。以performance.now单调时钟累计，不用打开时间减关闭时间。这里测量的是页面可观测的有效浏览，不是眼动注意力，也不能检测操作系统没有通知网页的所有物理遮挡。

每10秒心跳；结束包携带累计毫秒。IndexedDB暂存断网重试，event_id与序号保证不重复累计，pagehide增加立即keepalive补发。崩溃或浏览器拒绝最终请求时，只保留最后确认的心跳，不凭空补足离线时间。

后端核对会话所有者、实际展示过的房源、原推荐与需求版本、段身份、单调序号、真实经过时间。所有者级租约防止两个标签/会话同时重复计时。未验证推荐中的房源也可留存有效浏览事实，但不伪装成旧原生评分可消费的合格记录。

### 2.3 什么时候传给推荐

仅用户明确重新推荐、修改条件重新搜索，或提交需求补充卡片时构建请求。当前显示列表不会因为浏览而自动重排。按as_of固定截点，之后发生的行为留到下一轮；累计摘要不重复累加到feedback中。

现有评分门槛仍是：单套累计至少15秒、至少两套、合格总时长至少60秒；门槛总计每套最多计120秒，浏览摘要本身保留实际已接受的累计值。

## 三、Book Viewing Listing 看房意向记录

### 3.1 什么操作才计入

点“联系经纪人”只创建默认草稿，不计入。打开时间选择器、只选时间但没保存、只点重新起草、没有时间的普通咨询也不计入。

必须是VIEWING草稿、正文非空、选择Asia/Singapore的有效未来起止时间、结束晚于开始且不超过4小时，并点击“保存当前草稿”且后端保存成功，才记录。这个门槛比单纯点击更能表达主动看房意向，同时不要求用户真的在外部平台发出了消息。

取消动作、保存时移除有效时间会产生withdrawn记录，下轮不再输出该动作。采用追加事件日志，修改前的历史不会覆盖；重试旧revision不会重复计入。每套房只输出最新仍有效的动作，若另有有效动作则仍可保留。过去日期的历史意向可以保留为兴趣事实，不是当前预约日程。

### 3.2 字段明细（本表全部新增）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| listing_id | 数字字符串 | 选择看房的房源 |
| action_id | UUID字符串 | 用户实际保存的草稿动作 |
| source_request_id | UUID字符串 | 当时从哪轮推荐选择这套房 |
| requirements_version | 非负整数 | 当时的应用需求版本；不是外层原生版本的+1换算值 |
| starts_at | 带时区ISO时间 | 用户保存的候选开始时间 |
| ends_at | 带时区ISO时间 | 用户保存的候选结束时间 |
| time_zone | string | Asia/Singapore |
| recorded_at | 带时区ISO时间 | 后端记录这次有效保存的时间 |
| signal | string | 固定viewing_draft_saved，明确触发门槛 |
| appointment_confirmed | boolean | 固定false；本字段只描述草稿意向，不能冒充双方确认的预约 |

`Book Viewing Listing`沿用对接名称，当前语义是“已保存带时间的看房意向”，不是“已预约成功”。真正双方确认仍走已有的独立确认/日历流程，不由本行为字段推断。江山可把该信号作为强于浏览的兴趣参考，但不能据此自动收紧硬条件或认为经纪人已同意。

### 3.3 实现及验证入口

`migrations/006-session-behavior-context.sql`存储事件；`src/domain/actions.ts`在保存/取消时调用`recordViewingIntent`；`src/domain/behavior-context.ts`按截点汇总；`src/domain/workflow.ts`发新推荐请求时传入；`src/adapters/native-recommendation.ts`保留原值送到应用推荐入口。

本轮验证使用本地单元测试、隔离PostgreSQL、Python脚本推荐控制器、浏览器模拟业务接口，不调用付费模型。检查了初次空数组、时间保存门槛、取消/去重、时长重试、请求截点、跨用户删除防护、隐藏/失焦/移出视口暂停和推荐顺序不变。浏览器hidden/blur采用事件模拟，不把它说成真实桌面遮挡检测。报告位于evaluation/reports/session-behavior-db.json和session-behavior-ui.json。
