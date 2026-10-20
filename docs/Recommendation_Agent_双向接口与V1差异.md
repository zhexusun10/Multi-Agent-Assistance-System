# Recommendation Agent 双向接口对接说明（当前实现，含原V1差异）

> 版本说明：本文记录接入前的应用兼容接口与最初V1差异，作为历史交接依据保留。当前默认已接入江山原生版本，跨服务接口以 [原生接入说明](Recommendation_Agent_原生接入说明.md) 和 `recommendation/contracts/` 为准；不能直接按本文旧报文调用原生服务。

文档日期：2026-10-20。业务范围：新加坡住宅租赁。线上传输字段 `contract_version` 仍为 **`1.0`**；本文是基于当前周侧实现整理的联调说明，尚不代表江山已确认或双方已完成远程联调。

## 0. 阅读方式、依据和职责

### 附录：原生接入兼容元数据

下列字段为本次接入新增，均非原始V1字段；只存在于应用内部兼容响应，不会注入江山的原生 `/recommend` Response。完整的新契约和映射见 [原生接入说明](Recommendation_Agent_原生接入说明.md)。其余历史表格继续描述应用兼容模型；顶层`native`为可选字段，baseline报文无需提供。

| 新增字段 | 类型 | 含义 |
| --- | --- | --- |
| response.native | object，可选 | 原生来源和兼容投影的关联元数据 |
| response.native.provider | literal | jiangshan-native |
| response.native.upstream_commit | string | 本次来源907cfa8 |
| response.native.exchange_ref | UUID | 当前会话的完整原生request/response存档 |
| response.native.status | enum | 不改写的原生status |
| response.native.state_id | string/null | 不改写的原生state_id |
| response.native.ranking | string[] | 不改写的原生已验证推荐ID顺序 |
| response.native.scores | 按listing_id的Record | 完整原生ScoreBreakdown，包含显式/隐式分、贡献、覆盖、停用特征 |
| response.native.criterion_results | 按listing_id的Record | 原始pass/fail/unknown/unsupported结果 |
| response.native.unsupported_criterion_ids | string[] | A中保留、原生契约不能表达的条件ID |
| response.native.unscored_listing_ids | string[] | 原生未评分的待核实ID，不把兼容占位0当作原生得分 |

本文件只描述两个业务接口：

```text
周侧 Requirement Agent 产出结构化需求
  → Orchestrator 合并完整快照、添加请求标识及反馈
  → RecommendationRequest → 江山 Recommendation Agent
  ← RecommendationResponse
  → Orchestrator 进行格式、业务一致性及过期检查
  → 周侧 Explanation Agent 解释已返回的事实与排序
```

原始文档：[2026-10-13 Recommendation Agent Integration Contract](../contracts/references/recommendation_agent_contract_v1.original.md)，与用户最初提供的 GC1 原文逐字节一致，SHA-256：`4366d57bbb77e1957a442bf421264467aa5f82d5e8af7c45be46c3264e8da552`。

当前权威代码：[Zod模型与业务校验器](../src/contracts/recommendation-v1.ts)；请求组装和结果转交：[workflow.ts](../src/domain/workflow.ts)；远程HTTP网关与本地基线：[recommendation.ts](../src/adapters/recommendation.ts)；解释生成：[explanation.ts](../src/domain/explanation.ts)。本文对应实现提交 `501fb6c`。

**差异标识统一如下；“新增”均指相对原始文档，不是本次修改了运行接口。**

- **原有**：原文已列出该字段，或明确描述其对应含义。
- **【细化】**：原文要求了这类信息，但没有明确该子字段的JSON键名、层级或类型；当前实现将其具体化。这些也属于原文没有给出完整定义的补充字段，不能误认为原文已有完整Schema。
- **【新增】**：当前实现增加、原文未明确要求的字段或信息。
- **【登记新增】**：新增的是 `criteria[].field` 的可选值，**不是增加请求顶层属性**。
- **【内部包装新增】**：周侧进程内部任务字段，不要求江山作为HTTP响应返回。

“必需”表示JSON键是否需要存在；“可为null”表示键存在时能否填null。可省略字段不等于可以填null。空列表用 `[]`，不要改成null。除明确开放的对象外，请只发送本文列出的字段；顶层与大多数对象会拒绝额外字段。

顶层字段对比结论：**输入8个、输出14个顶层字段均沿用原V1，没有增加新的业务顶层字段。** 主要补充在字段登记表、证据细节、房源展示资料和错误重试提示。

### 0.1 运行数据和模块边界

当前实际数据源为2026-10-09完整快照：21,431条RENT房源、4,657条独立经纪人资料、418,700条图片URL记录；PostgreSQL保存原始结构化资料，正式Neo4j保存房源关系、OSM周边与校园证据。URA区域与LTA站点出口另有官方位置目录。12,800条房源没有坐标。

当前本机B是明确标记的 `baseline`，不是江山最终推荐实现。江山负责检索/重用/验证/排序；A负责解释用户需求并给出完整需求快照；C负责忠实解释返回的证据，不重排、不补造事实。独立卫浴未知不意味着不能跟进，后续可生成核实草稿。

当前请求没有 `dataset_version` 顶层字段。双方应在部署时配置同一快照，B在响应的 `state.coverage.dataset_version` 和每条证据的 `dataset_version` 返回实际版本。**不要未经修改模型便向请求增加 dataset_version、query、user_profile 或 result 等顶层字段**。

## 1. 方向一：Requirement / Orchestrator → RecommendationRequest


### 1.1 请求顶层

路径：`request`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `contract_version` | `"1.0"` | 是；不可为null | 固定字符串1.0；不是需求版本，也不是内部任务schema_version。 | 原有 |
| `request_id` | `string` | 是；不可为null | 逻辑推荐请求ID，非空字符串；当前生产端生成UUID。相同请求重试必须保持ID和完整body不变。 | 原有 |
| `conversation_id` | `string` | 是；不可为null | 非空会话ID；B的状态、反馈、候选池必须按此隔离。 | 原有 |
| `requirements_version` | `integer` | 是；不可为null | 非负整数；完整结构化需求真正改变时递增。只新增反馈可以不变，但换request_id。 | 原有 |
| `requirements` | `object` | 是；不可为null | 完整当前需求快照；不是仅发送这次改动的字段。 | 原有 |
| `feedback` | `array<object>` | 可省略，默认[]；发送方统一给出 | 新反馈数组。Zod接收端省略时默认[]；导出的JSON Schema是输出视图，将本字段列为required，因此发送端统一提供[]。 | 原有 |
| `previous_state_id` | `string` | 否；不可为null | 上次response.state.state_id；第一次调用省略。过期状态应允许B重新检索，不代表可跳过当前条件核查。 | 原有 |
| `result_limit` | `integer` | 是；不可为null | 整数1–20；周侧当前固定5。仅约束recommendations数量，不是全部候选扫描上限。 | 原有 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "contract_version": "1.0",
  "request_id": "10000000-0000-4000-8000-000000000001",
  "conversation_id": "handoff-demo-session",
  "requirements_version": 1,
  "requirements": {
    "listing_type": "RENT",
    "criteria": [
      {
        "criterion_id": "monthly-rent",
        "field": "price",
        "operator": "lte",
        "value": 3000,
        "unit": "SGD/month",
        "strength": "hard"
      },
      {
        "criterion_id": "minimum-bedrooms",
        "field": "bedrooms",
        "operator": "gte",
        "value": 2,
        "unit": "count",
        "strength": "hard"
      },
      {
        "criterion_id": "preferred-district",
        "field": "district",
        "operator": "in",
        "value": [
          "D05"
        ],
        "strength": "hard"
      },
      {
        "criterion_id": "near-supermarket",
        "field": "place_proximity",
        "operator": "lte",
        "value": 800,
        "unit": "m",
        "strength": "soft",
        "weight": 3,
        "target": {
          "category": "Supermarket"
        },
        "distance_type": "straight_line"
      }
    ]
  },
  "feedback": [],
  "result_limit": 5
}
```


### 1.2 完整需求快照

路径：`request.requirements`。

A内部的 upsert、remove_fields、sources、location_intents、interpretation_notes、clarifications 等是抽取草稿，经过合并后才生成此对象；这些草稿字段不直接发送给江山。Knowledge Graph关系是B内部证据获取手段，也不能因此给请求增加cypher、relation_type等未登记字段。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `listing_type` | `"RENT"` | 是；不可为null | 仅支持RENT；不能传SALE。 | 原有 |
| `criteria` | `array<object>` | 是；不可为null | 最多40项；criterion_id不得重复。无条件时可为空数组；不能把数组误写为SQL where字符串。 | 原有 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "listing_type": "RENT",
  "criteria": [
    {
      "criterion_id": "monthly-rent",
      "field": "price",
      "operator": "lte",
      "value": 3000,
      "unit": "SGD/month",
      "strength": "hard"
    },
    {
      "criterion_id": "minimum-bedrooms",
      "field": "bedrooms",
      "operator": "gte",
      "value": 2,
      "unit": "count",
      "strength": "hard"
    },
    {
      "criterion_id": "preferred-district",
      "field": "district",
      "operator": "in",
      "value": [
        "D05"
      ],
      "strength": "hard"
    },
    {
      "criterion_id": "near-supermarket",
      "field": "place_proximity",
      "operator": "lte",
      "value": 800,
      "unit": "m",
      "strength": "soft",
      "weight": 3,
      "target": {
        "category": "Supermarket"
      },
      "distance_type": "straight_line"
    }
  ]
}
```


### 1.3 单个需求条件

路径：`request.requirements.criteria[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_id` | `string` | 是；不可为null | 非空，最长100字符。同一条件改值保留ID；独立卫浴与泳池必须各有自己的条件ID。 | 原有 |
| `field` | `string` | 是；不可为null | 只能使用1.5节登记的21个字段；不能自行添加bathroom_type等字段。 | 原有 |
| `operator` | `eq / in / gte / lte / between` | 是；不可为null | eq / in / gte / lte / between；还必须符合该field的允许操作。 | 原有 |
| `value` | `any(JSON)` | 是；不可为null | 由field决定类型；between为长度2的递增区间，in为非空同类型数组；数字非负、有限、≤1e15，整数型字段必须整数。 | 原有 |
| `unit` | `string` | 否；不可为null | 有登记单位的字段必须精确匹配，区分SGD/month、m、min、sqft等；无单位字段可省略。 | 原有 |
| `strength` | `hard / soft` | 是；不可为null | hard=必须满足；soft=排序偏好。 | 原有 |
| `weight` | `number` | 否；不可为null | soft时必须给有限正数；hard时禁止提供。用户未指定相对重要性，由A在请求发出前补默认1。 | 原有 |
| `target` | `object` | 否；不可为null | place_proximity必须提供，且place_id/category至少一个非空；其他字段当前模型允许附带，但不代表工具会支持。 | 原有 |
| `distance_type` | `straight_line / walking / driving / transit / platform_reported` | 否；不可为null | 枚举straight_line / walking / driving / transit / platform_reported。place_proximity必需；两个mrt_字段必须是platform_reported。 | 原有 |
| `transport_mode` | `string` | 否；不可为null | 可选字符串；周侧常用transit、driving、walking、unspecified；当前Schema不是封闭枚举。不是用来替代distance_type的字段。 | 原有 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_id": "near-clementi-mrt",
  "field": "place_proximity",
  "operator": "lte",
  "value": 1000,
  "unit": "m",
  "strength": "soft",
  "weight": 1,
  "target": {
    "place_id": "LTA:MRT:CLEMENTI",
    "name": "CLEMENTI MRT STATION"
  },
  "distance_type": "straight_line"
}
```


### 1.4 地点目标 Target

路径：`request.requirements.criteria[].target`、`response.evidence[].target`。

这张Target表同时适用于输入条件和输出证据。只提供name而没有place_id或category的place_proximity不合法。类别是字符串开放类型，但真正可计算的类别以双方地点目录为准。MRTStation属于周侧位置目录扩展，不在现有data API places/search的PlaceCategory枚举内，不能直接照搬成该端点的category。江山可以读取项目knowledge/locations/lta-stations.json；具名URA/LTA实体也可通过周侧GET /api/locations/{id}读取，GET /api/locations/catalog查看目录。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `place_id` | `string` | 否；不可为null | 已解析的稳定地点ID；不是数据库listing_id，也不是Neo4j elementId。常见LTA:MRT:CLEMENTI、URA:AREA:CLEMENTI或OSM:*。 | 【细化】 |
| `category` | `string` | 否；不可为null | 类别查询，如Supermarket、School、Park、FoodCourt、Mall、MRTStation。通用MRT代表最近的MRT，不要求用户指定站名。 | 原有 |
| `name` | `string` | 否；不可为null | 供解释和界面显示；不作为替代place_id/category的唯一检索键。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "place_id": "LTA:MRT:CLEMENTI",
  "name": "CLEMENTI MRT STATION"
}
```


例：不指定站名的MRT需求可使用以下目标；不能误填一个随机具体站点。

```json
{"category":"MRTStation","name":"最近的MRT地铁站"}
```

### 1.5 `field` 全部登记值、数据映射和可执行能力

下表列出的是 `criteria[].field` 的取值，不能把这些名称直接平铺到请求顶层。“可评估”不保证每条房源都有值；缺失/无效值仍为unknown。“未支持”表示当前 `FIELD_RULES.unsupported=true`，请求合法，但结果不能声称pass或fail。

| field值 | 值类型 | 允许operator | unit | 当前数据来源 / 能力 | 相对原V1 |
| --- | --- | --- | --- | --- | --- |
| `price` | `number` | eq / gte / lte / between | `SGD/month` | properties.price；仅RENT且currency=SGD时为有效月租 | 原有示例 |
| `bedrooms` | `integer` | eq / gte / lte / between | `count` | properties.bedrooms；负数哨兵作未知，不能由房型推断整租范围 | 原有示例 |
| `bathrooms` | `integer` | eq / gte / lte / between | `count` | properties.bathrooms；数量不能证明专用使用权 | 【登记新增】 |
| `floor_area_sqft` | `number` | eq / gte / lte / between | `sqft` | properties.floor_area_sqft，室内面积 | 【登记新增】 |
| `land_area_sqft` | `number` | eq / gte / lte / between | `sqft` | properties.land_area_sqft，土地面积 | 【登记新增】 |
| `build_year` | `integer` | eq / gte / lte / between | `year` | properties.build_year，建成年份 | 【登记新增】 |
| `district` | `string` | eq / in | `无` | 映射properties.district_code；D01–D28 | 原有示例 |
| `region_code` | `string` | eq / in | `无` | properties.region_code；只接受CCR/RCR/OCR，不是URA五大规划区名称 | 【登记新增】 |
| `postal_code` | `string` | eq / in | `无` | properties.postal_code；六位数字字符串，保留前导0 | 【登记新增】 |
| `tenure_category` | `string` | eq / in | `无` | properties.tenure_category；Freehold / 99-year Leasehold等，非租客租期 | 【登记新增】 |
| `property_type` | `string` | eq / in | `无` | properties.property_type；例如Condominium / HDB Flat / Apartment，按规范源值精确比较 | 【登记新增】 |
| `place_proximity` | `number` | lte / gte / between | `m` | 房源坐标+目标几何；当前只可证明straight_line；没有坐标则未知 | 原有示例 |
| `mrt_distance_m` | `number` | lte / gte / between | `m` | properties.mrt_distance_m；挂牌标注，不是路线API实测 | 【登记新增】 |
| `mrt_walking_mins` | `number` | lte / gte / between | `min` | properties.mrt_walking_mins；挂牌标注，不代表到任意目的地的通勤 | 【登记新增】 |
| `lease_months` | `integer` | eq / between | `month` | 未支持；没有租客租期字段，不得从tenure推断 | 【登记新增】 |
| `furnishing` | `string` | eq / in | `无` | 未支持；约定fully_furnished / partially_furnished / unfurnished | 【登记新增】 |
| `rental_scope` | `string` | eq / in | `无` | 未支持；约定whole_unit / room | 【登记新增】 |
| `move_in_date` | `date` | eq / gte / lte | `无` | 未支持；YYYY-MM-DD真实公历日期 | 【登记新增】 |
| `amenities` | `string` | eq | `无` | 未支持；每项设施一个条件，例如private_bathroom，见下方值表 | 【登记新增】 |
| `commute_time` | `number` | lte / between | `min` | 未支持；无实际通勤路线工具；min单位，目的地可附target | 【登记新增】 |
| `current_availability` | `boolean` | eq | `无` | 未支持；true表示要求当前可租，历史挂牌不能证明 | 【登记新增】 |

例：同时表达月租、面积、恰好1B1B和独立卫浴；后两种卫浴要求必须分开。

```json
[
  {"criterion_id":"rent","field":"price","operator":"lte","value":2000,"unit":"SGD/month","strength":"hard"},
  {"criterion_id":"area","field":"floor_area_sqft","operator":"gte","value":500,"unit":"sqft","strength":"hard"},
  {"criterion_id":"beds","field":"bedrooms","operator":"eq","value":1,"unit":"count","strength":"hard"},
  {"criterion_id":"baths","field":"bathrooms","operator":"eq","value":1,"unit":"count","strength":"hard"},
  {"criterion_id":"private-bath","field":"amenities","operator":"eq","value":"private_bathroom","strength":"hard"}
]
```

amenities当前规范值（字符串不是Schema封闭枚举）：`private_bathroom` 独立卫浴、`master_room` 主卧、`common_room` 普通房、`swimming_pool` 泳池、`pet_policy` 可养宠物、`cooking_allowed` 可做饭、`air_conditioning` 空调、`no_live_in_landlord` 不与房东同住。不同设施可同时存在，不能互相覆盖。

**七个unsupported字段不是输入错误。** 当前校验器会拒绝B对它们返回已知pass/fail；即使江山以后取得新证据，也应先共同修改FIELD_RULES与证据适配，不能只在响应中改成pass。

### 1.6 模糊附近、硬半径与距离语义

用户只说“金文泰附近”时，A解析地区/站点，不要求用户查询坐标或提供公里数。URA区域与MRT站点是不同目标：`URA:AREA:CLEMENTI` 为规划区；`LTA:MRT:CLEMENTI` 为地铁出口集合。单说地区，当前目录通常优先解析为URA区域。

- 无明确半径：只建立soft条件。当前 `value=1000` 是距离评分尺度，不是用户声明的硬边界。
- 明确“3公里内”：建立hard `lte 3000`，同时保留soft就近偏好；先满足硬条件，再在范围内排序。
- 区域目标：区域内距离0，区域外取到边界最近直线距离；0不等于距地铁站或区域中心0米。区域内房源可并列。
- 站点目标：取最近已收录出口的直线距离。通用MRT不包含LRT，当前使用541个MRT出口。
- 普通OSM地点：房源坐标到已收录参考点的球面距离。缺少坐标/目标依据时为null，不补0。
- walking/driving/transit不是straight_line的别名，不能通过除以步速把直线距离变成已核实通勤。

例：“Clementi MRT附近3公里，越近越好”会形成两个条件：

```json
[
  {"criterion_id":"clementi-radius","field":"place_proximity","operator":"lte","value":3000,"unit":"m","strength":"hard","target":{"place_id":"LTA:MRT:CLEMENTI","name":"CLEMENTI MRT STATION"},"distance_type":"straight_line"},
  {"criterion_id":"clementi-nearer","field":"place_proximity","operator":"lte","value":1000,"unit":"m","strength":"soft","weight":1,"target":{"place_id":"LTA:MRT:CLEMENTI","name":"CLEMENTI MRT STATION"},"distance_type":"straight_line"}
]
```

当前基线soft距离效用是 `pivot / (pivot + distance)`。距离2,000米、pivot=1,000时效用为1/3；soft条件的机械比较状态仍是fail（2,000>1,000），但**不能因此排除房源**。界面将此项呈现为就近排序。不能为了让软偏好好看而把这个比较结果伪造为pass。


### 1.7 反馈事件

路径：`request.feedback[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `event_id` | `string` | 是；不可为null | 事件唯一ID，用于幂等去重；重试不能重新生成同一事件ID。 | 原有 |
| `listing_id` | `string` | 是；不可为null | 数字字符串；PropertyGuru原生房源ID，不是界面序号。 | 原有 |
| `occurred_at` | `string(date-time)` | 是；不可为null | 带时区ISO 8601时间；例如2026-10-20T15:00:00+08:00。 | 原有 |
| `event_type` | `active_view / save / shortlist / explicit_attribute_feedback` | 是；不可为null | 枚举active_view / save / shortlist / explicit_attribute_feedback。 | 原有 |
| `payload` | `object` | 是；不可为null | 开放JSON对象；按事件类型满足下表。 | 原有 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "event_id": "evt-view-001",
  "listing_id": "900000000001",
  "occurred_at": "2026-10-20T15:00:00+08:00",
  "event_type": "active_view",
  "payload": {
    "active_duration_ms": 45000
  }
}
```


### 1.8 `feedback[].payload` 按事件类型的字段

payload允许扩展键，下表是当前实际约定；原文没有给出这些payload的完整JSON结构。

| event_type | payload字段 | 类型 / 必需 | 含义与约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| active_view | active_duration_ms | number，必需 | 有限且>0，最大3,600,000；单位毫秒，不是页面打开后的自然时长 | 【细化】 |
| save | 无固定子字段 | object，通常{} | 收藏事件本身即信号；不要捏造停留时间 | 【细化】 |
| shortlist | 无固定子字段 | object，通常{} | 加入候选清单事件 | 【细化】 |
| explicit_attribute_feedback | attribute | string，必需 | 反馈对象，例如floor_area_sqft；当前模型只检查string，接入双方宜规范为登记值 | 【细化】 |
| explicit_attribute_feedback | sentiment | positive / negative，必需 | 明确正负反馈；提问次数不自动视为喜欢 | 【细化】 |
| explicit_attribute_feedback | quote | string，建议给出 | 用户原话引用；当前payload开放结构未强制此子键类型 | 【新增】 |

例：
```json
[
  {
    "event_id": "evt-view-001",
    "listing_id": "900000000001",
    "occurred_at": "2026-10-20T15:00:00+08:00",
    "event_type": "active_view",
    "payload": {
      "active_duration_ms": 45000
    }
  },
  {
    "event_id": "evt-save-001",
    "listing_id": "900000000001",
    "occurred_at": "2026-10-20T15:01:00+08:00",
    "event_type": "save",
    "payload": {}
  },
  {
    "event_id": "evt-shortlist-001",
    "listing_id": "900000000001",
    "occurred_at": "2026-10-20T15:02:00+08:00",
    "event_type": "shortlist",
    "payload": {}
  },
  {
    "event_id": "evt-negative-001",
    "listing_id": "900000000001",
    "occurred_at": "2026-10-20T15:03:00+08:00",
    "event_type": "explicit_attribute_feedback",
    "payload": {
      "attribute": "floor_area_sqft",
      "sentiment": "negative",
      "quote": "第一套面积太小"
    }
  }
]
```

当前UI收藏只保存事件，不保证立刻重排；下一次推荐请求会带上尚未消费的反馈。当前baseline保存/传输反馈，但不使用行为画像改分，并返回FEEDBACK_NOT_RERANKED提示。江山的行为兴趣模型可以另行实现，但明确条件优先，事件必须去重，不能重复累计同一停留事件。

## 2. 方向二：Recommendation → Orchestrator → Explanation


### 2.1 响应顶层

路径：`response`。

所有14个顶层键都必须存在，包括空数组、空trace和error=null；不要加一层data或result封装。这里的“已验证推荐”仅指请求中的hard条件都通过，不代表实地核验或当前仍可租。下面完整示例是合成接口样例，不是真实房源执行结果。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `contract_version` | `"1.0"` | 是；不可为null | 固定1.0。 | 原有 |
| `request_id` | `string` | 是；不可为null | 原样回显本次逻辑推荐请求ID，不能生成新的。 | 原有 |
| `conversation_id` | `string` | 是；不可为null | 原样回显会话ID。 | 原有 |
| `requirements_version` | `integer` | 是；不可为null | 原样回显请求中的整数版本。 | 原有 |
| `status` | `success / needs_clarification / no_match / error` | 是；不可为null | success / needs_clarification / no_match / error；语义见2.11。 | 原有 |
| `recommendations` | `array<object>` | 是；不可为null | 只含全部hard条件pass的候选；条数≤result_limit。 | 原有 |
| `unverified_candidates` | `array<object>` | 是；不可为null | 无hard fail，且至少一个hard unknown；独立列表，不能混进recommendations。当前基线最多5，响应校验最多20。 | 原有 |
| `evidence` | `array<object>` | 是；不可为null | 本次结果、分数及解释引用的证据对象集合；不能只给没有定义的evidence_id。 | 原有 |
| `clarifications` | `array<object>` | 是；不可为null | 澄清问题列表；没有问题用[]。 | 原有 |
| `relaxation_suggestions` | `array<object>` | 是；不可为null | 只提出调整建议，不直接改需求或混入不符合硬条件的房源。 | 原有 |
| `warnings` | `array<object>` | 是；不可为null | 覆盖、历史快照、算法能力等限制；没有提示用[]。 | 原有 |
| `state` | `object` | 是；不可为null | 不透明状态ID、候选池引用、覆盖和计数。 | 原有 |
| `execution` | `object` | 是；不可为null | 实际执行策略、追踪和评分元数据；不是内部思维链。 | 原有 |
| `error` | `object / null` | 是；可为null | 非错误时必须是null；status=error时必须给错误对象。 | 原有 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "contract_version": "1.0",
  "request_id": "10000000-0000-4000-8000-000000000001",
  "conversation_id": "handoff-demo-session",
  "requirements_version": 1,
  "status": "success",
  "recommendations": [
    {
      "listing": {
        "listing_id": "900000000001",
        "title": "合成示例房源 A",
        "price": 2800,
        "price_unit": "SGD/month",
        "bedrooms": 2,
        "district": "D05",
        "source_url": null,
        "availability_status": "unknown",
        "floor_area_sqft": 800,
        "property_type": "Condominium",
        "thumbnail_url": null
      },
      "rank": 1,
      "score": 0.5517241379310345,
      "criterion_results": [
        {
          "criterion_id": "monthly-rent",
          "status": "pass",
          "observed_value": 2800,
          "unit": "SGD/month",
          "evidence_ids": [
            "fixture-price-a"
          ]
        },
        {
          "criterion_id": "minimum-bedrooms",
          "status": "pass",
          "observed_value": 2,
          "unit": "count",
          "evidence_ids": [
            "fixture-bedrooms-a"
          ]
        },
        {
          "criterion_id": "preferred-district",
          "status": "pass",
          "observed_value": "D05",
          "evidence_ids": [
            "fixture-district-a"
          ]
        },
        {
          "criterion_id": "near-supermarket",
          "status": "pass",
          "observed_value": 650,
          "unit": "m",
          "evidence_ids": [
            "fixture-supermarket-a"
          ]
        }
      ],
      "score_breakdown": [
        {
          "criterion_id": "near-supermarket",
          "origin": "stated_preference",
          "utility": 0.5517241379310345,
          "weight": 3,
          "normalized_weight": 1,
          "contribution": 0.5517241379310345,
          "evidence_ids": [
            "fixture-supermarket-a"
          ]
        }
      ],
      "explanation_evidence": {
        "strengths": [
          {
            "criterion_ids": [
              "monthly-rent",
              "minimum-bedrooms",
              "preferred-district"
            ],
            "evidence_ids": [
              "fixture-price-a",
              "fixture-bedrooms-a",
              "fixture-district-a"
            ]
          }
        ],
        "compromises": [],
        "unknowns": [
          {
            "field": "availability_status",
            "evidence_ids": [
              "fixture-availability-a"
            ]
          }
        ]
      }
    }
  ],
  "unverified_candidates": [],
  "evidence": [
    {
      "evidence_id": "fixture-price-a",
      "listing_id": "900000000001",
      "field_path": "price",
      "value": 2800,
      "unit": "SGD/month",
      "source_kind": "fixture",
      "source_ref": "fixture-design-v1/listing-a/price",
      "dataset_version": "fixture-design-v1",
      "retrieved_at": "2026-10-19T06:00:00Z",
      "verified_at": null
    },
    {
      "evidence_id": "fixture-bedrooms-a",
      "listing_id": "900000000001",
      "field_path": "bedrooms",
      "value": 2,
      "unit": "count",
      "source_kind": "fixture",
      "source_ref": "fixture-design-v1/listing-a/bedrooms",
      "dataset_version": "fixture-design-v1",
      "retrieved_at": "2026-10-19T06:00:00Z",
      "verified_at": null
    },
    {
      "evidence_id": "fixture-district-a",
      "listing_id": "900000000001",
      "field_path": "district",
      "value": "D05",
      "source_kind": "fixture",
      "source_ref": "fixture-design-v1/listing-a/district",
      "dataset_version": "fixture-design-v1",
      "retrieved_at": "2026-10-19T06:00:00Z",
      "verified_at": null
    },
    {
      "evidence_id": "fixture-supermarket-a",
      "listing_id": "900000000001",
      "field_path": "place_proximity",
      "value": 650,
      "unit": "m",
      "distance_type": "straight_line",
      "target": {
        "category": "Supermarket"
      },
      "source_kind": "fixture",
      "source_ref": "fixture-design-v1/listing-a/supermarket-straight-line",
      "dataset_version": "fixture-design-v1",
      "retrieved_at": "2026-10-19T06:00:00Z",
      "verified_at": null,
      "raw_distance_type": "geodesic",
      "route_verified": false,
      "measurement_method": "haversine.WGS84_spherical"
    },
    {
      "evidence_id": "fixture-availability-a",
      "listing_id": "900000000001",
      "field_path": "availability_status",
      "value": null,
      "source_kind": "fixture",
      "source_ref": "fixture-design-v1/listing-a/availability",
      "dataset_version": "fixture-design-v1",
      "retrieved_at": "2026-10-19T06:00:00Z",
      "verified_at": null
    }
  ],
  "clarifications": [],
  "relaxation_suggestions": [],
  "warnings": [
    {
      "code": "FIXTURE_ONLY",
      "message": "合成设计样例，不是真实房源或推荐执行结果。"
    },
    {
      "code": "AVAILABILITY_UNVERIFIED",
      "message": "当前可租状态未核实。",
      "listing_ids": [
        "900000000001"
      ]
    },
    {
      "code": "DATA_COVERAGE_LIMITED",
      "message": "仅有一个合成候选，不表示市场覆盖。"
    }
  ],
  "state": {
    "state_id": "fixture-rec-state-001",
    "candidate_pool_ref": "fixture-design-v1/candidate-pool-001",
    "coverage": {
      "dataset_version": "fixture-design-v1",
      "dataset_scope": "one synthetic listing",
      "retrieval_scope": "all fixture candidates",
      "complete_within_dataset": true,
      "truncated": false
    },
    "counts": {
      "assessed": 1,
      "eligible": 1,
      "unverified": 0,
      "excluded": 0
    }
  },
  "execution": {
    "strategy": "fixture_only",
    "trace_ref": null,
    "tool_calls": [],
    "timing": {
      "total_ms": null
    },
    "scoring_version": "baseline-explicit-soft-v2-geospatial-pivot",
    "score_semantics": "illustrative_preference_fit_not_probability"
  },
  "error": null
}
```


### 2.2 单个候选 Candidate

路径：`response.recommendations[]`、`response.unverified_candidates[]`。

两类候选使用同一个完整对象结构。前端可把两个组按全局序号显示，但不能要求江山把unverified_candidates的rank接在已验证组之后。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `listing` | `object` | 是；不可为null | 房源摘要，见2.3；不是整行raw_json。 | 【细化】 |
| `rank` | `integer` | 是；不可为null | 正整数；每个数组内部必须按1、2、3连续排名；两个数组各自从1开始。 | 原有 |
| `score` | `number` | 是；不可为null | 0–1偏好匹配分，非成交概率、满意概率或置信度。 | 原有 |
| `criterion_results` | `array<object>` | 是；不可为null | 对请求每个criterion_id恰好返回一次，包括soft与unsupported字段。 | 【细化】 |
| `score_breakdown` | `array<object>` | 是；不可为null | 评分分项；无soft偏好时基线为[]且score=0。 | 【细化】 |
| `explanation_evidence` | `object` | 是；不可为null | 优点、妥协与未知项的结构化索引；不是自由生成的营销理由。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "listing": {
    "listing_id": "900000000001",
    "title": "合成示例房源 A",
    "price": 2800,
    "price_unit": "SGD/month",
    "bedrooms": 2,
    "district": "D05",
    "source_url": null,
    "availability_status": "unknown",
    "floor_area_sqft": 800,
    "property_type": "Condominium",
    "thumbnail_url": null
  },
  "rank": 1,
  "score": 0.5517241379310345,
  "criterion_results": [
    {
      "criterion_id": "monthly-rent",
      "status": "pass",
      "observed_value": 2800,
      "unit": "SGD/month",
      "evidence_ids": [
        "fixture-price-a"
      ]
    },
    {
      "criterion_id": "minimum-bedrooms",
      "status": "pass",
      "observed_value": 2,
      "unit": "count",
      "evidence_ids": [
        "fixture-bedrooms-a"
      ]
    },
    {
      "criterion_id": "preferred-district",
      "status": "pass",
      "observed_value": "D05",
      "evidence_ids": [
        "fixture-district-a"
      ]
    },
    {
      "criterion_id": "near-supermarket",
      "status": "pass",
      "observed_value": 650,
      "unit": "m",
      "evidence_ids": [
        "fixture-supermarket-a"
      ]
    }
  ],
  "score_breakdown": [
    {
      "criterion_id": "near-supermarket",
      "origin": "stated_preference",
      "utility": 0.5517241379310345,
      "weight": 3,
      "normalized_weight": 1,
      "contribution": 0.5517241379310345,
      "evidence_ids": [
        "fixture-supermarket-a"
      ]
    }
  ],
  "explanation_evidence": {
    "strengths": [
      {
        "criterion_ids": [
          "monthly-rent",
          "minimum-bedrooms",
          "preferred-district"
        ],
        "evidence_ids": [
          "fixture-price-a",
          "fixture-bedrooms-a",
          "fixture-district-a"
        ]
      }
    ],
    "compromises": [],
    "unknowns": [
      {
        "field": "availability_status",
        "evidence_ids": [
          "fixture-availability-a"
        ]
      }
    ]
  }
}
```


### 2.3 房源摘要 Listing

路径：`response.recommendations[].listing`、`response.unverified_candidates[].listing`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `listing_id` | `string` | 是；不可为null | 必须是仅数字组成的字符串；不能传number或界面第几套。 | 原有 |
| `title` | `string` | 是；不可为null | 房源标题字符串。 | 原有 |
| `price` | `number / null` | 是；可为null | 非负数字或null；快照中的月租，不代表今天报价。 | 原有 |
| `price_unit` | `"SGD/month"` | 是；不可为null | 固定SGD/month；不能把买卖总价混入。 | 【细化】 |
| `bedrooms` | `number / null` | 是；可为null | 非负number或null。语义上通常为整数；本摘要Schema没有integer限制，条件值bedrooms仍必须integer。 | 原有 |
| `district` | `string / null` | 是；可为null | D05等邮区或null；摘要层只校验string，发送方应使用源值。 | 原有 |
| `source_url` | `string / null` | 是；可为null | 原挂牌URL或null；不代表API已经验证当前可租。 | 【细化】 |
| `availability_status` | `"unknown"` | 是；不可为null | 当前只能为unknown；B不能直接返回available或true。 | 【细化】 |
| `floor_area_sqft` | `number / null` | 否；可为null | 可选面积或null，供卡片摘要；当前摘要Schema未限制正负，源哨兵值应规范为null。 | 【新增】 |
| `property_type` | `string / null` | 否；可为null | 可选源房型或null，例如Condominium。 | 【新增】 |
| `thumbnail_url` | `string / null` | 否；可为null | 可选图片链接或null；不是图片二进制。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "listing_id": "900000000001",
  "title": "合成示例房源 A",
  "price": 2800,
  "price_unit": "SGD/month",
  "bedrooms": 2,
  "district": "D05",
  "source_url": null,
  "availability_status": "unknown",
  "floor_area_sqft": 800,
  "property_type": "Condominium",
  "thumbnail_url": null
}
```


### 2.4 逐项条件结果

路径：`response.recommendations[].criterion_results[]`、`response.unverified_candidates[].criterion_results[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_id` | `string` | 是；不可为null | 必须对应本次请求的条件ID，不是field名的替代品。 | 【细化】 |
| `status` | `pass / fail / unknown` | 是；不可为null | pass / fail / unknown；hard分类优先fail，再unknown，再全部pass。 | 【细化】 |
| `observed_value` | `any(JSON)` | 是；可为null | 该房源实际记录/计算值，不是用户目标阈值；基线缺少依据时为null。 | 【细化】 |
| `unit` | `string` | 否；不可为null | 使用与该条件/证据一致的单位；无单位时省略。 | 【细化】 |
| `evidence_ids` | `array<string>` | 是；不可为null | 非空数组；每个ID都必须存在于response.evidence，且属于同一房源、同一field。 | 【细化】 |
| `reason` | `string` | 否；不可为null | 可选判断依据或未知原因；不应把缺数据说成用户没说清。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_id": "amenities:private_bathroom",
  "status": "unknown",
  "observed_value": null,
  "evidence_ids": [
    "fixture-private-bath-a"
  ],
  "reason": "快照没有独立卫浴专用使用权记录；卫浴数量不能证明是否共用。"
}
```


### 2.5 分数分解

路径：`response.recommendations[].score_breakdown[]`、`response.unverified_candidates[].score_breakdown[]`。

数值一致性容差1e-6。当前基线的缺失soft项效用为0，但仍计入总权重分母；不因缺失而给其余偏好重新分配全部权重。无soft时所有已通过硬条件的房源分数都可能为0，这不表示匹配失败。相同分数基线按月租升序、数字房源ID升序破并列。江山可使用自己的评分算法，但必须解释scoring_version、score_semantics及行为项定义。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_id` | `string` | 是；不可为null | 评分项标识；明确偏好应对应请求criterion_id。推断兴趣的ID命名仍需双方约定，不能伪装成用户明确条件。 | 【细化】 |
| `origin` | `stated_preference / inferred_interest` | 是；不可为null | stated_preference或inferred_interest；当前baseline只有前者。 | 【细化】 |
| `utility` | `number` | 是；不可为null | 0–1的单项效用；不是条件pass/fail的数字别名。 | 【细化】 |
| `weight` | `number` | 是；不可为null | 该项正权重；相对值不必加起来为1。 | 【细化】 |
| `normalized_weight` | `number` | 是；不可为null | 0–1归一化权重；基线为本项weight / 所有soft权重之和。 | 【细化】 |
| `contribution` | `number` | 是；不可为null | 0–1，必须等于utility*normalized_weight；所有项贡献之和等于score。 | 【细化】 |
| `evidence_ids` | `array<string>` | 是；不可为null | 分数所依据的证据ID；都必须属于该房源。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_id": "near-supermarket",
  "origin": "stated_preference",
  "utility": 0.5517241379310345,
  "weight": 3,
  "normalized_weight": 1,
  "contribution": 0.5517241379310345,
  "evidence_ids": [
    "fixture-supermarket-a"
  ]
}
```


### 2.6 解释证据索引

路径：`response.recommendations[].explanation_evidence`、`response.unverified_candidates[].explanation_evidence`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `strengths` | `array<object>` | 是；不可为null | 已满足条件的索引数组；具体子字段见下表。 | 【细化】 |
| `compromises` | `array<object>` | 是；不可为null | 未满足的软偏好等妥协索引；不能用来容纳hard fail后仍推荐的房源。 | 【细化】 |
| `unknowns` | `array<object>` | 是；不可为null | 尚无可靠依据的字段索引；和当前可租未知分别说明。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "strengths": [
    {
      "criterion_ids": [
        "monthly-rent",
        "minimum-bedrooms",
        "preferred-district"
      ],
      "evidence_ids": [
        "fixture-price-a",
        "fixture-bedrooms-a",
        "fixture-district-a"
      ]
    }
  ],
  "compromises": [],
  "unknowns": [
    {
      "field": "availability_status",
      "evidence_ids": [
        "fixture-availability-a"
      ]
    },
    {
      "field": "amenities",
      "evidence_ids": [
        "fixture-private-bath-a"
      ]
    }
  ]
}
```


### 2.6.1 strengths / compromises中的元素

路径：`response.recommendations[].explanation_evidence.strengths[]`、`response.recommendations[].explanation_evidence.compromises[]`、`response.unverified_candidates[].explanation_evidence.strengths[]`、`response.unverified_candidates[].explanation_evidence.compromises[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_ids` | `array<string>` | 是；不可为null | 关联条件ID列表；必须属于该候选的criterion_results。 | 【细化】 |
| `evidence_ids` | `array<string>` | 是；不可为null | 关联证据ID列表；必须属于该房源。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_ids": [
    "monthly-rent",
    "minimum-bedrooms",
    "preferred-district"
  ],
  "evidence_ids": [
    "fixture-price-a",
    "fixture-bedrooms-a",
    "fixture-district-a"
  ]
}
```


### 2.6.2 unknowns中的元素

路径：`response.recommendations[].explanation_evidence.unknowns[]`、`response.unverified_candidates[].explanation_evidence.unknowns[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `field` | `string` | 是；不可为null | 未知属性，例如amenities或availability_status；后者是房源摘要状态，不是请求field登记值。 | 【细化】 |
| `evidence_ids` | `array<string>` | 是；不可为null | 未知结论的依据索引；不能跨房源。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "field": "amenities",
  "evidence_ids": [
    "fixture-private-bath-a"
  ]
}
```


### 2.7 证据 Evidence

路径：`response.evidence[]`。

表中fixture示例只用于离线格式核对。源事实来自数据API，并不意味着远程B可以把任意自己的引用标为data_api；该枚举在当前周侧还有“引用由谁管理”的实际语义。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `evidence_id` | `string` | 是；不可为null | 本次响应中唯一；供条件结果、评分、解释和建议引用。 | 【细化】 |
| `listing_id` | `string` | 是；不可为null | 仅数字字符串，必须是证据所属房源。 | 【细化】 |
| `field_path` | `string` | 是；不可为null | 当前校验要求等于对应criterion.field，例如price；不能擅自写properties.price替代price。 | 【细化】 |
| `value` | `any(JSON)` | 是；可为null | 事实或计算值；与引用它的criterion_results.observed_value严格一致；基线未知为null。 | 【细化】 |
| `unit` | `string` | 否；不可为null | 与criterion_results.unit一致；有值与省略也需一致。 | 【细化】 |
| `distance_type` | `string` | 否；不可为null | 证据口径。当前已知place_proximity必须是straight_line；不能拿walking冒充。mrt_证据若附此字段，应明确platform_reported。 | 【细化】 |
| `raw_distance_type` | `string` | 否；不可为null | 保存底层术语，例如geodesic，区分对外straight_line与源数据称呼。 | 【新增】 |
| `target` | `object` | 否；不可为null | 采用1.4的Target结构；应与所评估的地理目标一致。 | 【细化】 |
| `source_kind` | `data_api / fixture / recommendation_service` | 是；不可为null | data_api / fixture / recommendation_service；生产不能把合成fixture当成真实房源。引用路由见第4节。 | 【细化】 |
| `source_ref` | `string` | 是；不可为null | 非空可解析引用；不是随便填一个说明字符串。当前data_api要求是周侧已保存的payload引用。 | 【细化】 |
| `dataset_version` | `string` | 是；不可为null | 实际快照版本；当前正式房源快照为2026-10-09；位置目录可有独立版本并记录于原始来源。 | 【细化】 |
| `retrieved_at` | `string(date-time)` | 是；不可为null | 本次取得这条证据的带时区时间，不能冒充原抓取日期或实地核实日期。 | 【细化】 |
| `verified_at` | `string(date-time) / null` | 是；可为null | 必需但可为null；记录真实核实时间，没有核实就填null。 | 【新增】 |
| `route_verified` | `boolean` | 否；不可为null | 可选布尔值；本项目直线距离和挂牌交通均不证明实际路线，当前应为false。 | 【新增】 |
| `measurement_method` | `string` | 否；不可为null | 可选计算方法标识，如haversine.WGS84_spherical、haversine.nearest_recorded_exit、turf.geodesic.distance_to_area_zero_inside。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "evidence_id": "fixture-supermarket-a",
  "listing_id": "900000000001",
  "field_path": "place_proximity",
  "value": 650,
  "unit": "m",
  "distance_type": "straight_line",
  "target": {
    "category": "Supermarket"
  },
  "source_kind": "fixture",
  "source_ref": "fixture-design-v1/listing-a/supermarket-straight-line",
  "dataset_version": "fixture-design-v1",
  "retrieved_at": "2026-10-19T06:00:00Z",
  "verified_at": null,
  "raw_distance_type": "geodesic",
  "route_verified": false,
  "measurement_method": "haversine.WGS84_spherical"
}
```


### 2.8 推荐状态 State

路径：`response.state`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `state_id` | `string` | 是；不可为null | B返回的不透明状态ID；下次原样作为previous_state_id。可用自有UUID/命名空间；不需要周侧理解内部结构。 | 原有 |
| `candidate_pool_ref` | `string` | 是；不可为null | 完整已评估候选池的引用，不仅是展示前5条；需要约定解析、权限与保留期。 | 【细化】 |
| `coverage` | `object` | 是；不可为null | 覆盖范围、数据版本及是否截断。 | 【细化】 |
| `counts` | `object` | 是；不可为null | 去重后的候选池数量；没有评估不能冒填0。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "state_id": "fixture-rec-state-001",
  "candidate_pool_ref": "fixture-design-v1/candidate-pool-001",
  "coverage": {
    "dataset_version": "fixture-design-v1",
    "dataset_scope": "one synthetic listing",
    "retrieval_scope": "all fixture candidates",
    "complete_within_dataset": true,
    "truncated": false
  },
  "counts": {
    "assessed": 1,
    "eligible": 1,
    "unverified": 0,
    "excluded": 0
  }
}
```


### 2.8.1 覆盖范围

路径：`response.state.coverage`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `dataset_version` | `string` | 是；不可为null | 当前房源快照版本，正式为2026-10-09。 | 【细化】 |
| `dataset_scope` | `string` | 是；不可为null | 被宣称覆盖的数据集范围，例如2026-10-09快照。 | 【细化】 |
| `retrieval_scope` | `string` | 是；不可为null | 本次实际检索范围，例如完整SQL租赁扫描，或受限召回池。 | 【细化】 |
| `complete_within_dataset` | `boolean` | 是；不可为null | 是否在声明的数据集和检索范围内完整；不代表覆盖全新加坡市场。 | 【细化】 |
| `truncated` | `boolean` | 是；不可为null | 是否因分页/预算等截断；不能和complete=true一起宣称完整。当前校验器未自动验证这对布尔值的全部业务含义。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "dataset_version": "2026-10-09",
  "dataset_scope": "2026-10-09 PropertyGuru RENT snapshot",
  "retrieval_scope": "Complete canonical SQL RENT scan; no hard pushdown; nulls retained",
  "complete_within_dataset": true,
  "truncated": false
}
```


### 2.8.2 候选池计数

路径：`response.state.counts`。

四项均为非负整数或null。全部已知时assessed=eligible+unverified+excluded。例子数字来自全量快照下“NUS直线3公里、预算3000”的一次已验收查询；其他合成报文使用自己的fixture计数，不能混用。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `assessed` | `integer / null` | 是；可为null | 去重后的已评估候选总数；非展示卡片数。 | 【细化】 |
| `eligible` | `integer / null` | 是；可为null | 全部hard pass的候选总数；可以远大于result_limit。 | 【细化】 |
| `unverified` | `integer / null` | 是；可为null | 无hard fail但存在hard unknown的总数。 | 【细化】 |
| `excluded` | `integer / null` | 是；可为null | 至少一个hard fail的总数，即使其他hard unknown也归此组。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "assessed": 21431,
  "eligible": 246,
  "unverified": 3947,
  "excluded": 17238
}
```


### 2.9 执行元数据

路径：`response.execution`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `strategy` | `string` | 是；不可为null | 实际策略标识，基线是deterministic_local_baseline；远程应返回自己的真实策略。 | 【细化】 |
| `trace_ref` | `string / null` | 是；可为null | 追踪引用或null；当前基线复用candidate_pool_ref，详细工具轨迹另存周侧tool_traces。 | 【细化】 |
| `tool_calls` | `array<any(JSON)>` | 是；不可为null | 开放数组，当前Schema中元素为any。基线返回name/source_refs/count等摘要，不要求每项都是同一种固定结构。 | 【细化】 |
| `timing` | `object` | 是；不可为null | 执行耗时对象。 | 【细化】 |
| `scoring_version` | `string` | 是；不可为null | 评分函数版本；基线当前为baseline-explicit-soft-v2-geospatial-pivot。 | 【细化】 |
| `score_semantics` | `string` | 是；不可为null | 分数的业务含义，不是概率；原文有语义要求，此处固定为显式字段。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "strategy": "fixture_only",
  "trace_ref": null,
  "tool_calls": [],
  "timing": {
    "total_ms": null
  },
  "scoring_version": "baseline-explicit-soft-v2-geospatial-pivot",
  "score_semantics": "illustrative_preference_fit_not_probability"
}
```


### 2.9.1 耗时

路径：`response.execution.timing`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `total_ms` | `number / null` | 是；可为null | 非负毫秒数或null；不得没有计时却伪填0。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "total_ms": 182
}
```


tool_calls元素没有一个可穷举的强制子字段清单。基线的实际示例是：

```json
[
  {"name":"listings/search","source_refs":["example-page-reference"]},
  {"name":"canonical_assessment_scan","count":22}
]
```

上面的name/source_refs/count只是当前基线的摘要约定，不是额外强制Schema字段。原V1设想的call_id、顺序、参数、结果、duration等更细轨迹可以保存于trace_ref背后；目前不能宣称execution.tool_calls已经提供了全部逐次调用内容。不返回模型私有思维链。


### 2.10 澄清问题

路径：`response.clarifications[]`。

这个片段示例针对假设输入中两个相互矛盾的预算条件，不代表标准样例存在该冲突。缺少房源数据通常应返回unknown，不应反复要求用户重新确认已经表达清楚的需求。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_ids` | `array<string>` | 是；不可为null | 受影响条件ID数组。 | 【细化】 |
| `question` | `string` | 是；不可为null | 面向用户的明确问题；由Orchestrator协调A继续收集。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_ids": [
    "budget-floor",
    "budget-ceiling"
  ],
  "question": "目前同时有月租至少4000和不超过3000的条件，请确认应修改哪一项？"
}
```


### 2.10.1 放宽建议

路径：`response.relaxation_suggestions[]`。

上例为建议对象片段；若作为完整响应发送，example-alternative-rent必须有真实对应的Evidence。需要用户明确同意后才由A生成新需求版本。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `criterion_id` | `string` | 是；不可为null | 建议调整的原条件ID；必须属于本次请求。 | 【细化】 |
| `proposed_value` | `any(JSON)` | 是；不可为null | 按原field/operator/unit合法的新值；不是直接生效的需求。 | 【细化】 |
| `reason` | `string` | 是；不可为null | 提出建议的事实理由；不能保证用户接受。 | 【细化】 |
| `evidence_ids` | `array<string>` | 否；不可为null | 可选证据ID数组；若提供，必须在本次evidence中存在。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "criterion_id": "monthly-rent",
  "proposed_value": 3200,
  "reason": "保持其余硬条件时，已评估候选的最低记录月租为3200新币；是否考虑调整？",
  "evidence_ids": [
    "example-alternative-rent"
  ]
}
```


### 2.10.2 限制与提醒

路径：`response.warnings[]`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `code` | `string` | 是；不可为null | 机器可读提示代码；当前不是封闭枚举。基线使用LOCAL_BASELINE、SNAPSHOT_ONLY、LIMITED_DATASET、FEEDBACK_NOT_RERANKED；周侧另可加入GEO_INTERPRETATION。 | 【细化】 |
| `message` | `string` | 是；不可为null | 可向用户展示的限制说明，不能含密钥或内部堆栈。 | 【细化】 |
| `listing_ids` | `array<string>` | 否；不可为null | 可选，提示涉及哪些房源；当前此子字段Schema只约束string[]，发送方仍应给数字ID。 | 【细化】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "code": "SNAPSHOT_ONLY",
  "message": "2026-10-09为历史快照，当前可租状态尚未核实。"
}
```


### 2.10.3 错误对象

路径：`response.error`。

| 字段 | 类型 | 必需 / null | 含义与当前约束 | 相对原V1 |
| --- | --- | --- | --- | --- |
| `code` | `string` | 是；不可为null | 机器可读业务错误代码；当前没有冻结统一枚举。 | 原有 |
| `message` | `string` | 是；不可为null | 失败原因，不暴露密钥和原始数据库异常。 | 原有 |
| `retryable` | `boolean` | 否；不可为null | 可选布尔值；建议重试与否，当前HTTP网关未以此字段自动调度。 | 【新增】 |

例（本节对象片段；完整报文见配套JSON文件）：

```json
{
  "code": "RETRIEVAL_UNAVAILABLE",
  "message": "房源数据服务暂不可用，本次没有完成评估。",
  "retryable": true
}
```


### 2.11 四种status与后续路由

| status | 内容要求 | 周侧后续处理 | 相对原V1 |
| --- | --- | --- | --- |
| success | recommendations至少1条；error=null；可同时有unverified_candidates | 保存结果，交C解释并展示两组 | 原有 |
| needs_clarification | recommendations必须为空，error=null；提供clarifications | Orchestrator展示问题，A收集后发新请求 | 原有 |
| no_match | recommendations为空，error=null；允许unverified_candidates非空 | C说明没有已确认满足全部hard的推荐；可展示未知候选并继续跟进 | 原有 |
| error | recommendations为空且error非null；state/execution等仍需完整提供 | 记录失败，按业务码处理；不当作没有房源 | 原有 |

例：给标准请求新增独立卫浴硬条件后，原候选改为未知分组：
```json
{
  "status": "no_match",
  "recommendations": [],
  "unverified_candidates": [
    {
      "listing": {
        "listing_id": "900000000001",
        "title": "合成示例房源 A",
        "price": 2800,
        "price_unit": "SGD/month",
        "bedrooms": 2,
        "district": "D05",
        "source_url": null,
        "availability_status": "unknown",
        "floor_area_sqft": 800,
        "property_type": "Condominium",
        "thumbnail_url": null
      },
      "rank": 1,
      "score": 0.5517241379310345,
      "criterion_results": [
        {
          "criterion_id": "monthly-rent",
          "status": "pass",
          "observed_value": 2800,
          "unit": "SGD/month",
          "evidence_ids": [
            "fixture-price-a"
          ]
        },
        {
          "criterion_id": "minimum-bedrooms",
          "status": "pass",
          "observed_value": 2,
          "unit": "count",
          "evidence_ids": [
            "fixture-bedrooms-a"
          ]
        },
        {
          "criterion_id": "preferred-district",
          "status": "pass",
          "observed_value": "D05",
          "evidence_ids": [
            "fixture-district-a"
          ]
        },
        {
          "criterion_id": "near-supermarket",
          "status": "pass",
          "observed_value": 650,
          "unit": "m",
          "evidence_ids": [
            "fixture-supermarket-a"
          ]
        },
        {
          "criterion_id": "amenities:private_bathroom",
          "status": "unknown",
          "observed_value": null,
          "evidence_ids": [
            "fixture-private-bath-a"
          ],
          "reason": "快照没有独立卫浴专用使用权记录；卫浴数量不能证明是否共用。"
        }
      ],
      "score_breakdown": [
        {
          "criterion_id": "near-supermarket",
          "origin": "stated_preference",
          "utility": 0.5517241379310345,
          "weight": 3,
          "normalized_weight": 1,
          "contribution": 0.5517241379310345,
          "evidence_ids": [
            "fixture-supermarket-a"
          ]
        }
      ],
      "explanation_evidence": {
        "strengths": [
          {
            "criterion_ids": [
              "monthly-rent",
              "minimum-bedrooms",
              "preferred-district"
            ],
            "evidence_ids": [
              "fixture-price-a",
              "fixture-bedrooms-a",
              "fixture-district-a"
            ]
          }
        ],
        "compromises": [],
        "unknowns": [
          {
            "field": "availability_status",
            "evidence_ids": [
              "fixture-availability-a"
            ]
          },
          {
            "field": "amenities",
            "evidence_ids": [
              "fixture-private-bath-a"
            ]
          }
        ]
      }
    }
  ]
}
```

上段是路由相关字段片段，候选结构完整，但仍需补齐其余顶层字段后才能作为完整响应。可复制、经过当前校验器验证的完整报文为 [response.unverified.json](../contracts/examples/current-v1/response.unverified.json)，配套输入为 [request.private-bathroom.json](../contracts/examples/current-v1/request.private-bathroom.json)。该报文含全部Candidate字段、所有逐项结果以及未知证据，不使用缩略候选替代完整对象。

执行失败而尚未完成评估时，四项counts均应为null，不能伪填0；见 [response.error.json](../contracts/examples/current-v1/response.error.json)。没有足够结果不能凑数；no_match仅描述本次已评估范围，不证明市场无房。

## 3. Explanation Agent 实际收到的内部上下文

江山只返回第2节的RecommendationResponse。周侧Orchestrator验收后再组装下表作为C的内部任务输入，**不要求江山返回这个包装，也不要改变HTTP响应顶层格式**。

| 内部字段 | 提供方 | 内容 | 相对原V1 |
| --- | --- | --- | --- |
| requirements | Orchestrator | 与本次B请求一致的完整Requirements，供C把criterion_id映射回用户条件 | 【内部包装新增】 |
| recommendation | Orchestrator | 经过validateResponse的完整RecommendationResponse，非只取前5个标题 | 【内部包装新增】 |
| query | Orchestrator | 当前操作的用户问题，例如解释第1套或比较前两套；缺省时按通用解释处理 | 【内部包装新增】 |

例（TypeScript代码，表示实际组装方式，不是江山HTTP响应）：

```typescript
await next('c', {
  requirements: currentRequirements,
  recommendation: validatedRecommendationResponse,
  query: currentUserQuery
}, 'explain');
```

内部TaskEnvelope里的schema_version=1.1、agent、operation_id、input_ref、input_hash等属于周侧任务身份和存储协议，不是RecommendationRequest/Response字段。前端选择跟进时的source_request_id也不是新增的B请求字段。

C保留B的分组和排名，根据criterion_results、evidence及score_breakdown解释；模型只可选择要强调的房源和条件，关键数值由代码渲染。C不能因为用户很喜欢某套房而把hard unknown改成pass，也不能把评分说成成交概率。

房源卡片中的照片、完整地址、更多属性和周边列表还有周侧ListingPresentationService的补充读取路径。这些不意味着江山必须把整行数据库、全部图片或所有周边地点塞入B响应；当前B摘要只额外接收floor_area_sqft、property_type、thumbnail_url三个展示字段。经纪人电话也不在B→C的摘要字段内。

另一个当前限制：Schema虽允许inferred_interest分项，但C现有score_meaning文案固定为“明确软偏好匹配分”。江山正式加入行为评分前，需同步调整解释/界面的分数文案，不能把“字段可接收”等同为“行为分已完整解释”。

## 4. HTTP接入、状态与引用

### 4.1 江山需要实现的外部调用入口

| 项目 | 当前网关行为 | 是否来自原V1 |
| --- | --- | --- |
| 请求 | POST {RECOMMENDATION_API_URL}/recommend，JSON body直接为RecommendationRequest | 【细化】原文未定HTTP路径 |
| Header | Content-Type: application/json；Idempotency-Key=request_id | 【细化】原文要求同一逻辑请求重试幂等 |
| 成功传输 | HTTP 200；body直接为RecommendationResponse，无data/result包裹 | 【细化】 |
| 超时/尝试 | 每次30秒超时，最多2次HTTP尝试，保持同一ID与body；当前未按error.retryable细分重试策略 | 【细化】 |
| 体积 | 响应最大8MiB；大候选池和原始工具结果用可解析引用 | 【细化】 |
| 认证 | 当前远程网关未提供跨组织鉴权，假设可信本机/开发网络；不能直接宣称已安全开放公网 | 【细化】 |

例（先在项目根目录调用江山服务；这是调用示例，不表示该8012服务当前已部署）：

```powershell
curl.exe -X POST http://127.0.0.1:8012/recommend `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: 10000000-0000-4000-8000-000000000001" `
  --data-binary "@contracts/examples/current-v1/request.standard.json"
```

周侧切换配置：`RECOMMENDATION_MODE=remote`，`RECOMMENDATION_API_URL=http://127.0.0.1:8012`，然后重启图服务。格式正确的业务error可以放在HTTP200的完整响应内；HTTP4xx/5xx在当前网关中作为HTTP故障，不保证解析其body里的业务error对象。

### 4.2 source_ref / candidate_pool_ref / trace_ref 的真实接入限制

原V1要求引用可解析，但没有冻结跨服务的解析协议。当前代码的事实是：

- 本地 `source_kind=data_api`：source_ref是周侧domain_payloads中已持久化、属于当前会话的引用；apply阶段会查其存在性。B远程自造一个UUID或API URL不能自动通过。
- 本地前端“查看来源”：调用周侧 `/api/evidence`，要求UUID和当前会话所有权。没有配置读取江山自有存储的路由。
- 远程 `source_kind=recommendation_service`：Schema允许；C可使用内联Evidence.value生成解释，但当前点击来源尚不能解析任意远程URI。
- candidate_pool_ref和trace_ref按字符串接收，基线用周侧持久化引用；远程保留期、权限、404/过期及重新计算策略尚待约定。

例（仅说明引用路由差异，不是新增业务字段）：

```json
{
  "source_kind": "recommendation_service",
  "source_ref": "rec-evidence:request-001:list-001:price"
}
```

此片段在结构上允许，但**不代表当前前端可以打开这个引用**。正式交接前可共同选择：江山提供鉴权证据解析端点、周侧新增转发适配；或者周侧接收后把证据原文导入自身payload库，保留原引用并转换成本地引用。两种都是待实现的桥接方案，本文没有声称它们已存在，也没有擅自给请求增加解析URL字段。

## 5. 共同验收要求与当前校验器边界

### 5.1 当前代码已强制检查的关键规则

1. 请求字段、类型、operator、unit、hard/soft权重、合法日期、地区格式和重复criterion_id。
2. 响应的request_id、conversation_id、requirements_version必须与原请求一致。
3. 每组rank连续；跨两组房源ID不重复；recommendations≤result_limit，未知候选≤20。
4. success与非空recommendations一致；error与非空error对象一致。
5. 每个候选覆盖全部criterion_id；不能缺项或重复，且每项有非空证据引用。
6. 引用证据存在，并匹配房源、field、观测值与单位；价格/卧室/邮区摘要不得与相关证据矛盾。
7. 已知结果必须与compare的实际比较一致；不允许unsupported字段伪装成已知结果；已知place_proximity必须使用straight_line证据。
8. recommendations所有hard pass；unverified不能有hard fail且必须有hard unknown。
9. 非空分数分解的贡献和必须等于score；单项contribution=utility*normalized_weight，容差1e-6。
10. 全部计数已知时满足总和；eligible不得小于展示的recommendations数量；调整建议必须对应原条件且proposed_value合法。
11. 周侧再校验data_api引用存在及当前会话、需求版本、活动操作；过期结果不覆盖新需求。

### 5.2 不能把“通过Schema”误当已经完成的验证

以下语义需要B真实实现或双方补强；当前validateResponse没有全部自动验证：证据dataset_version与部署版本一致、target完全一致、证据新鲜度与TTL、推荐是否确实按score降序、所有归一化权重之和、行为贡献ID命名、远程引用可解析、coverage两个布尔值是否真实、count.unverified是否覆盖所有展示未知候选、所有声明的工具是否真实执行。needs_clarification是否确有非空问题、no_match与非零eligible计数是否矛盾，也需要发送端保证。

feedback.payload为开放对象；execution.tool_calls[]为any。explanation_evidence的strength/compromise/unknown元素使用Zod默认剔除额外键，而不是每个都strict报错。双方仍应按本文只发送已定义字段，不能把Schema输出视为任意内容可信。

### 5.3 配套样例及可执行核对

| 文件 | 用途 |
| --- | --- |
| [request.standard.json](../contracts/examples/current-v1/request.standard.json) | 完整需求，含硬预算/卧室/邮区与软超市偏好 |
| [response.success.json](../contracts/examples/current-v1/response.success.json) | 与上一个输入配套的完整成功响应 |
| [request.private-bathroom.json](../contracts/examples/current-v1/request.private-bathroom.json) | 增加独立卫浴硬条件，需求版本递增并回传previous_state_id |
| [response.unverified.json](../contracts/examples/current-v1/response.unverified.json) | 同一房源独卫unknown，只能进入待核实组，status=no_match |
| [response.error.json](../contracts/examples/current-v1/response.error.json) | 没完成评估的结构化错误，counts全部null |
| [request.feedback.json](../contracts/examples/current-v1/request.feedback.json) | 四类反馈事件，需求版本不变但换request_id |
| [request.conflict.json](../contracts/examples/current-v1/request.conflict.json) | 结构合法但两个预算条件互相冲突；正常A会先澄清，此例演示B的防御处理 |
| [response.needs-clarification.json](../contracts/examples/current-v1/response.needs-clarification.json) | 与冲突请求配套，返回问题，不伪造已评估数量 |

例：在项目根目录运行以下脚本，使用当前Zod与validateResponse校验完整样例，并核对本文字段索引与现有JSON Schema没有遗漏：

```powershell
node --import tsx scripts/validate-recommendation-examples.ts
```

所有900000000001房源、fixture-design-v1证据、示例追踪及评分都是**合成接口样例**，不代表当前数据库真的存在这些房源，也不是建议江山生产服务返回fixture。机器可读字段目录另见 [field-index.json](../contracts/examples/current-v1/field-index.json)。

## 6. 相对原V1的交接清单

- 原样保留：输入8个与输出14个顶层字段；完整需求快照；硬条件三态；独立待核实组；反馈去重；版本回显；建议需用户确认。
- 补充具体定义：21项field登记、固定单位/operator、payload键、分数分解、证据字段、覆盖统计、HTTP路径/限额/幂等、状态和空值要求。
- 明确新增的嵌套字段：Target.name、反馈payload.quote、Listing.floor_area_sqft/property_type/thumbnail_url、CriterionResult.reason、Evidence.raw_distance_type/verified_at/route_verified/measurement_method、RelaxationSuggestion.evidence_ids、Error.retryable。
- 内部包装新增：发给Explanation的requirements/recommendation/query；它不改变B的HTTP响应格式。
- 尚待双方冻结：远程证据/候选池/追踪解析与保留期、外部鉴权、行为画像和评分版本约定、unsupported字段何时扩展为可核实能力。

本文是对现有实现的说明，未新增运行时字段，也未将原提案擅自宣布为已冻结的团队协议。
