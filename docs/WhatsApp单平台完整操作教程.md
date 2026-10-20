# WhatsApp 单平台完整操作教程

核对日期：2026-10-20。项目目录：`D:\Real Estate Agent\Multi-Agent-Assistance-System`。

本教程独立完成“平台业务号码代用户发送消息 → 经纪人回复 → 本地系统接收和跟进”。不需要 Telegram 账号、Bot Token 或 chat_id。

先用 Meta 测试业务号码和你自己的 WhatsApp 收件账号跑通，再换正式业务号码。当前项目已实现接口；尚无真实 WhatsApp 凭证，因此本教程不代表已经用真实账号验收通过。

## 0. 准备与操作顺序

准备：

- 一个能登录 Meta 开发者平台的账号，按平台提示完成所需验证。
- 一个可正常收发消息的个人 WhatsApp 账号，用于接收测试。没有账号时，先从[WhatsApp 官方下载页](https://www.whatsapp.com/download)安装并完成注册；平台发送方随后使用 Meta 的测试业务号码。
- 当前 Windows 项目及已经安装的 Node.js、Python `.venv` 和 Docker Desktop。

先在 PowerShell 进入项目，后文项目命令都在此目录执行：

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
```

配置文件是项目根目录 `.env`。先打开查看：

```powershell
notepad .env
```

保留已有模型与数据库配置，只编辑对应的 WhatsApp 配置行，每个键只保留一个值。不要把现有 `.env` 整个替换为教程片段，不把密钥发到聊天或截图里。

Telegram 的 Token 保持空白即可；本流程不会用到它。仅配置 WhatsApp 也能运行整个找房、发送、回复跟踪、日历和文档流程。

顺序：Meta 控制台测试消息成功 → 收集凭证并填 `.env` → 启动本地应用和专用回调入口 → 建立临时 HTTPS 地址 → Meta 验证回调并订阅 → 发一条真实来信开启服务窗口 → 网页发送房源草稿 → 明确回复并检查本地记录。

本次本地测试需保持三个部分运行：

| 部分 | 地址/窗口 | 作用 |
|---|---|---|
| 主项目 | 浏览器8010；图服务3001；数据服务8089 | 搜索房源、草稿、审批、发送、保存回复 |
| 专用 webhook 入口 | 新PowerShell窗口，127.0.0.1:8011 | 仅转发消息回调到图服务 |
| HTTPS临时隧道 | 另一个PowerShell窗口 | 让Meta访问8011入口 |

## 1. WhatsApp：先建立测试发送账号

### 1.1 准备发送方和收件人

初次测试包含两个不同号码：

| 角色 | 说明 |
|---|---|
| 平台发送方 | Meta 提供的 WhatsApp 测试业务号码；稍后正式上线可换为自己的业务号码 |
| 测试收件人 | 你自己或小组成员正常使用的 WhatsApp 号码，并在 Meta 测试页面完成允许收件人的验证 |

使用自己的 WhatsApp 号码接收测试，不会让该号码变成平台发送方。平台消息始终来自所配置的业务号码。

### 1.2 创建 Meta 应用并添加 WhatsApp

1. 打开 [Meta for Developers](https://developers.facebook.com/)，用你自己的账号登录并完成页面要求的开发者注册/校验。
2. 进入 **My Apps / 我的应用**，选择 **Create App / 创建应用**。
3. 填应用名称，例如 `Qiju SG Real Estate Assistant`，以及可接收通知的联系邮箱。
4. 选择用于 WhatsApp Business Platform 的用途或产品，并按提示创建或选择你有管理权限的 Business Portfolio。
5. 打开应用的 WhatsApp 设置区域。不同账号界面可能叫 **Get Started、API Setup、API Testing、Getting Started**；寻找包含发送号码、收件人、Token 和测试发送按钮的页面。

具体菜单因控制台版本、账号资格而异；如果没有测试资源或被要求完成额外校验，应按该页面处理，不能保证所有新账号即时获得相同资源。当前先验证测试资源可用；正式业务号码的资质、审核和计费按控制台要求单独处理。[Meta 官方开发者入口](https://whatsappbusiness.com/developers/developer-hub/)、[官方 Cloud API 集合](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)

### 1.3 获取并分清这些值

| `.env` 配置项 | 去哪里找 / 它是什么 |
|---|---|
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp API 设置/测试页生成的访问令牌；初期通常使用页面提供的临时令牌 |
| `WHATSAPP_PHONE_NUMBER_ID` | 当前测试发送号码的 **Phone Number ID**，不是电话号码本身 |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | 对应的 **WhatsApp Business Account ID / WABA ID**，不是 Business Portfolio ID |
| `WHATSAPP_GRAPH_API_VERSION` | 使用该控制台请求示例中支持的版本，如 `v数字.数字`；不要把占位说明直接填进去 |
| `WHATSAPP_APP_SECRET` | 当前 Meta 应用 **App settings / Settings → Basic** 中的 App Secret，查看时可能需要再验证账号 |
| `WHATSAPP_VERIFY_TOKEN` | 你自己生成的随机校验串，稍后在 Meta 回调配置中填同一串，不是 Access Token |

要生成 Verify Token，可在本机执行以下命令，把结果自行保存进 `.env`：

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
```

这个命令只新生成一串随机值，不会读取或输出项目已有密钥。

### 1.4 在 Meta 添加允许的测试收件人

在同一个 WhatsApp API 测试页：

1. From / 发送方选择测试业务号码。
2. To / 收件人处添加你自己的 WhatsApp 号码，选择正确国家代码并按 Meta 提示完成验证码验证。
3. 使用控制台提供的测试消息按钮，先给这个收件人发一条默认测试模板消息，常见示例名为 `hello_world`。
4. 在手机上确认真的收到来自该测试业务号码的消息。

这里的 `hello_world` 仅用于验证 Meta 账号链路；我们的房源草稿不会被后台偷偷替换成它。[Meta 官方测试消息示例](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)

如果控制台自身都发不通，应先解决 Meta 账号、允许收件人、令牌或号码配置，再测试项目。测试号码通常只向控制台已允许的收件人发送，不等于已经能任意联系所有经纪人。

### 1.5 填项目 `.env`

填入你刚取得的真实值，其他原有配置保留：

```dotenv
WHATSAPP_ACCESS_TOKEN=实际访问令牌
WHATSAPP_PHONE_NUMBER_ID=实际发送号码ID
WHATSAPP_BUSINESS_ACCOUNT_ID=实际WABA_ID
WHATSAPP_GRAPH_API_VERSION=控制台示例中的实际版本
WHATSAPP_APP_SECRET=当前应用的实际AppSecret
WHATSAPP_VERIFY_TOKEN=自己生成并保存的随机串
EXTERNAL_ACTION_MODE=live
```

访问令牌须授权对应应用与 WABA/号码。发送涉及 `whatsapp_business_messaging`，管理与读取模板涉及 `whatsapp_business_management`；以账号实际授权和控制台要求为准。临时令牌有有效期，查看控制台显示的到期信息。[Meta 官方样例的权限说明](https://github.com/fbsamples/business-messaging-sample-tech-provider-app)

## 2. WhatsApp：把回复安全送到本机

### 2.1 为什么需要额外入口

Meta 不能访问你电脑上的 `127.0.0.1`。需要一条可从公网访问的 HTTPS 地址，将请求转到本机 webhook。项目提供了只开放两个消息 webhook 的辅助应用 `api/webhook_gateway.py`：

```text
Meta → HTTPS临时隧道 → 本机8011（仅webhook）→ 图服务3001（签名校验和入库）
网页工作台仍只在127.0.0.1:8010使用
```

辅助入口不提供 `/api/state`、`/api/sessions`、网页或文档下载。不要直接把工作台8010、图服务3001、PostgreSQL或Neo4j整体转到公网。

### 2.2 启动项目

在项目 PowerShell 中执行：

```powershell
.\scripts\stop-app.ps1
.\scripts\start-app.ps1 -SkipDataImport
```

确认 [http://127.0.0.1:8010/health](http://127.0.0.1:8010/health) 正常。

### 2.3 启动本机 webhook 辅助入口

新开一个 PowerShell 窗口，运行并保持窗口开启：

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
.venv\Scripts\python.exe -m uvicorn api.webhook_gateway:app --host 127.0.0.1 --port 8011 --no-access-log
```

预期看到 `Uvicorn running on http://127.0.0.1:8011`。`--no-access-log` 避免把回调验证查询参数写入此辅助应用的访问日志。签名验证仍由图服务执行，辅助入口不替代 App Secret 校验。

如果你改过图服务端口，先在这个窗口设置正确的 `GRAPH_SERVICE_URL`；默认部署不用修改。

### 2.4 安装并启动临时 HTTPS 隧道

Cloudflare Quick Tunnel 是一个开发测试方案，不需要你先购买域名。Windows 安装方式以[官方 cloudflared 下载页](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)为准；使用 winget 可执行：

```powershell
winget install --id Cloudflare.cloudflared -e
```

安装后重新开 PowerShell，确认：

```powershell
cloudflared --version
```

再运行，并保持窗口开启：

```powershell
cloudflared tunnel --url http://127.0.0.1:8011
```

终端会给出一个临时 HTTPS 地址，例如 `https://随机名称.trycloudflare.com`。这是示意，必须使用你自己终端输出的地址。此时只开放了辅助入口，不会开放整个工作台。[Cloudflare Quick Tunnel 官方教程](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

Quick Tunnel 用于开发测试，重启后地址可能改变；更改后需要更新 Meta 回调。正式部署使用固定域名与稳定服务。此步骤不会由当前聊天自动安装或创建公网隧道，需要你按教程运行。

### 2.5 配置 Meta 回调

返回 Meta 应用的 WhatsApp **Configuration / Webhooks** 配置，可能也在应用的 Webhooks 产品中、对象选择 `whatsapp_business_account`。

填写：

```text
Callback URL:
https://你实际得到的隧道域名/api/webhooks/whatsapp

Verify token:
与.env中WHATSAPP_VERIFY_TOKEN完全相同的值
```

点击 Verify and Save / 验证并保存。成功时 Meta 会请求上述 URL 的 GET 验证，项目校验 Verify Token 并返回 challenge。

然后订阅 **messages** 字段。它包含来信和发送消息的状态事件；同时确认应用与该 WABA 的订阅关系。若控制台没有完成 WABA 订阅，可运行项目辅助命令：

```powershell
node --env-file=.env --import tsx scripts/messaging-setup.ts whatsapp-subscribe
```

该命令会实际把当前应用订阅到所配置的 WABA，不发送聊天消息。它不是代替控制台的回调配置；回调地址、messages 字段和 WABA 订阅关系都应正确。[Meta 官方 Cloud API 集合](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)

检查只读资产信息：

```powershell
node --env-file=.env --import tsx scripts/messaging-setup.ts whatsapp-info
```

`configured_phone_id_found` 应为 true，输出号码应是你选的测试发送号码。`subscribed_app_count` 只说明存在订阅，不能单凭数量证明其中就是当前应用；`whatsapp-subscribe` 成功以及实际回调才是进一步依据。

Verify Token 用于 GET 回调验证；App Secret 用于 POST 签名验证。二者不能互相替代。项目会校验原始请求体的 `X-Hub-Signature-256`，不要在代理中重新序列化 JSON。[Meta 官方签名验证说明](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/)

## 3. WhatsApp：先在24小时窗口内发送真实房源草稿

### 3.1 让测试收件人发一条当前来信

完成回调配置并启动图服务后，用测试收件人的 WhatsApp，向之前发送 `hello_world` 的业务号码回复：

```text
我同意接收本次找房项目的测试消息，请开始测试。
```

必须是配置回调之后发出的新来信。Meta 应把这条真实来信送到本机并入库；未关联具体房源的这条初始来信不一定显示在某张动作卡片上，但服务端会记录它的时间。

收到对方来信后的服务窗口内可以发送自由文本；首次主动联系或窗口外联系应使用获批模板，并取得收件人许可。本项目根据验签后的真实来信时间判断，不允许表单自己宣称窗口已经开启。[WhatsApp 官方消息政策](https://business.whatsapp.com/policy)

### 3.2 在网页发送

1. 刷新工作台，选一套房源，生成联系核实或看房草稿。
2. 编辑草稿，渠道选择 **WhatsApp**。
3. 填测试收件人的国际号码，例如新加坡使用 `+65` 加本地号码。输入框中可写 `+65XXXXXXXX`，应换成真实数字；不要填写 Phone Number ID 或 WABA ID。
4. 人工核对联系人和许可；测试时明确这是小组模拟经纪人。
5. 在 WhatsApp 格式中保留 **自由文本（仅限24小时服务窗口内）**。本轮已经有真实来信，不需要等待自定义模板获批。
6. 加入协商诉求；若选看房，填写未来候选时间。
7. 保存 → 审核实际正文 → 批准当前修订 → **发送到所选平台**。

预期：平台受理并返回消息ID，测试收件人手机实际收到房源咨询。若 Meta 回传相应状态，可进一步显示已送达或已读；不能将 API 返回成功当作实际送达。

### 3.3 验证回信与跟进

让收件人使用 WhatsApp 的“回复该条消息”功能，针对刚收到的房源咨询回复。工作台时间线应出现原文，等待回复任务相应完成；经纪人的文字不自动改写房源未知字段。

然后可测试设置提醒、调整提醒、记录双方同意、导出日历和当天会话总结。看房成功仍需客户和经纪人都明确同意具体时段，不能仅根据“可以聊聊”或消息已读就确认。

本机电脑、图服务、8011辅助入口和隧道都要保持运行。只打开浏览器而后台或隧道已停，不能继续接收公网回调。

## 4. WhatsApp：首次主动联系和窗口外的获批模板

正式主动联系经纪人，仍须先取得相应联系许可，并处理窗口限制。不能因为房源页面公开了电话，就认为该电话已开通 WhatsApp或已同意本平台发送。

进入 WhatsApp Manager 的 **Message templates / 消息模板 → Create template**，按真实用途、语言与 Meta 提示选择类别。当前项目支持：

- BODY 文本与可选 FOOTER；
- 按 `{{1}}`、`{{2}}`、`{{3}}` 编号的位置参数；
- 状态为 APPROVED；
- 不带媒体头、按钮或命名参数。

可以参考以下结构写你自己的固定文案，按页面要求提供参数样例并提交审核；这不是保证获批的模板：

```text
Hello, this is Qiju SG rental assistant. With the prospective tenant's approval,
we would like to enquire about property {{1}}.
The tenant's questions and negotiation requests are: {{2}}.
Their proposed viewing times are: {{3}}.
Please confirm current availability and whether the proposed arrangements are possible.
```

获批后，在网页草稿选择 WhatsApp → **读取获批文本模板** → 选择匹配名称和语言 → 填参数。

参数1填房源ID与链接；参数2填待核实事项和协商诉求；参数3填候选起止时间。若是本项目的看房动作，模板正文必须包含这次时段的完整 ISO 字符串，例如 `YYYY-MM-DDTHH:mm:ss.sssZ`，否则项目审批会拦截。以保存时的实际预览为准，不复制过期示例日期。

选择模板会替换自由草稿正文，必须重新检查实际内容。协商诉求应完整出现在最终渲染的模板正文中，不要只留在旁边的备注框。当前读取最多100条模板；不符合支持格式的模板不会显示。

## 5. 从测试号码转为正式业务号码

在 WhatsApp Manager 中按提示添加正式业务号码，完成其归属验证码、显示名称、注册与控制台要求的业务资料、权限/审核、计费等步骤。号码能否与已有 WhatsApp Business App 共用、是否需迁移，以控制台提供的接入方式为准；不要未经确认就删除自己的现有账号。

准备实际业务名称、联系方式和控制台要求的证明资料。获取测试凭证与完成正式业务审核是不同阶段，不能保证新账号或正式业务审核当天通过。

长期运行时，用有权限的系统用户分配应用与 WhatsApp 资产，并按 Meta 流程生成合适权限及有效期的访问令牌。临时测试令牌可能过期，不能作为长期运行保障。[Meta 官方令牌与权限说明](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)

切换后核对更新：Access Token、Phone Number ID、WABA ID；如果换了 Meta 应用，还要更新 App Secret。重新配置订阅和回调，按本教程再验收实际收发。正式部署将临时隧道换成固定域名和稳定的运行服务。

## 6. 常见问题和明确的检查顺序

| 问题 | 下一步 |
|---|---|
| 不知道应进入哪个Meta页面 | 寻找WhatsApp API Setup/API Testing/Get Started，目标是能看到测试发送号码、To收件人和发送测试消息按钮 |
| 没有测试号码，或Meta要求额外验证 | 先完成账号页面的要求；这属于Meta账号资源可用性，修改本地代码不能代替 |
| Meta控制台自身无法发送测试消息 | 先检查令牌、测试允许收件人、号码和账号状态，手机实际收到后再继续本地配置 |
| status里whatsapp.configured为false | 检查Access Token、Phone Number ID和API版本是否完整保存；该命令只看配置，不验证实际权限 |
| whatsapp-info报错 | 检查Token及有效期、WABA ID、对应资产和管理权限；WABA ID不是业务组合ID |
| configured_phone_id_found为false | 配置中的Phone Number ID不在当前WABA返回的号码列表中，核对是否混用了不同应用/账号的资产 |
| 找不到cloudflared命令 | 安装后新开PowerShell；或按官方下载页使用cloudflared.exe的完整路径 |
| 回调根地址打开显示404 | 正常：专用入口没有首页。回调URL必须包含/api/webhooks/whatsapp，不能把首页404当作隧道失效 |
| Verify and Save失败 | 确认主项目、8011和隧道都运行；核对临时域名、路径及Verify Token；改.env后重启主项目 |
| 验证通过但实际来信未入库 | 核对messages字段、WABA订阅、发送号码ID、App Secret签名验证；再发一条真实新来信。Meta测试事件成功不等于真实来信已成功 |
| WHATSAPP_TEMPLATE_REQUIRED | 没有有效的24小时内来信记录。配置好回调后发一条新来信，或使用获批模板 |
| 模板列表为空 | 是否APPROVED；是否同一WABA；是否只有BODY和可选FOOTER、位置参数；当前最多读前100条 |
| TEMPLATE_MISMATCH | 重新读取模板并核对名称、语言、参数及渲染正文是否一致 |
| TEMPLATE_CONTENT_REQUIRED | 看房模板必须包含完整候选起止ISO时间以及本次协商诉求，补全参数后重新保存审批 |
| 页面仍为模拟执行 | .env中EXTERNAL_ACTION_MODE设为live，重启主项目并刷新网页；查看是否出现重复配置项 |
| 平台返回400/401/403等 | 从Token有效期、权限、资产ID、测试允许收件人、模板状态排查，在Meta控制台验证账号链路 |
| UNKNOWN/NETWORK_RESULT_UNKNOWN | 结果不明，先查收件人和平台记录；不要连续重试造成重复发送 |
| 经纪人回复没有出现在具体卡片 | 让对方明确“回复该条消息”；同一联系人对应多条动作且无回复上下文时，系统不猜归属 |
| CALENDAR_CONFLICT | 当前客户或联系人已有重叠的本地候选时段，换一个时间；之前的测试预约也会占用时段 |

## 7. 完整业务验收

1. Meta控制台测试消息实际到达测试收件人。
2. 本地接收到配置回调后发来的真实文字消息。
3. 在网页为一套房源起草并审核自定义消息，协商内容正确。
4. 点击发送后收到平台消息ID，手机实际收到对应内容。
5. 收件人回复那条消息，工作台显示正确房源的回复原文。
6. 设置“等待回复”提醒并验证后续回复能完成它；看房提醒不会因此自动取消。
7. 修改、完成提醒，核对保存后的状态。
8. 选未来看房时段。只有双方都明确同意该具体时段，才记录双方同意及依据。
9. 导出ICS和当天会话总结，核对时间、协商、未知事项和模式。

测试通过的标准是实际收到消息、实际收到回复并正确关联。配置显示true、API返回成功、Meta回调验证成功都只是各自阶段的证据。

## 8. 结束测试与下次启动

想恢复模拟，在.env中改：

```dotenv
EXTERNAL_ACTION_MODE=simulation
```

然后重启：

```powershell
.\scripts\stop-app.ps1
.\scripts\start-app.ps1 -SkipDataImport
```

临时隧道与8011辅助入口在各自终端按Ctrl+C关闭；主项目停止脚本不管理这两个手工启动的进程。

下次测试时重新启动主项目、8011、隧道；如果临时域名改变，更新Meta回调地址。如果临时令牌已过期，先重新获取并保存令牌、重启主项目。自由文本发送前重新确认有服务端记录的24小时内真实来信。

## 9. 本项目对应文件

- 配置：项目根目录 `.env`。
- 检查及订阅命令：`scripts/messaging-setup.ts` 的 `status`、`whatsapp-info`、`whatsapp-subscribe`。
- 发送适配器：`src/integrations/messaging.ts`。
- 回调签名、消息归属和跟进：`src/domain/followup.ts`、`src/server.ts`。
- 仅webhook的隧道入口：`api/webhook_gateway.py`。

本文与上述代码核对后编写。当前没有真实WhatsApp凭证，没有在本轮创建Meta账号、安装/打开公网隧道或发送真实消息。
