# WhatsApp 与 Telegram 申请、配置与验收

核对日期：2026-10-19。代码入口：`src/integrations/messaging.ts`、`src/domain/followup.ts`、`scripts/messaging-setup.ts`。

首次操作请优先使用[平台代发消息逐步操作教程](平台代发消息逐步操作教程.md)，其中包含 BotFather、chat_id、Meta 测试号码、仅 webhook 的本机 HTTPS 隧道、网页发送及收信验收的完整操作步骤。本文保留配置参考。

只配置 WhatsApp 时使用[WhatsApp单平台完整操作教程](WhatsApp单平台完整操作教程.md)，该教程独立包含全部前置步骤，不需要 Telegram。

当前项目没有两个平台的凭证，`.env` 已补上空配置项，`EXTERNAL_ACTION_MODE=simulation` 保持不变。接口实现、HTTP 模拟及本地业务验收不等于真实账号发送验收；真实账号创建、平台审核、实际收发和送达尚未完成。

## 先选一个平台跑通

课堂本地演示可先使用 Telegram：创建机器人，让小组另一位成员先联系它，获取该成员的 chat_id，然后在本项目审核并发送。消息以项目机器人身份发送，不是从你的个人 Telegram 账号发出。使用 polling 接收回复时，本机无需公网 webhook。

WhatsApp 使用项目的 Business Platform 号码发送，不是控制电脑上的个人 WhatsApp 客户端。需要 Meta 开发者应用、Business Portfolio、WhatsApp Business Account（WABA）和业务号码。初始测试可按控制台提供的测试资源进行。账号审核、号码资格、费用、可用权限以当前控制台为准。[Meta 官方 Cloud API 集合与入门](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)

## Telegram：申请和本地配置

先在 Telegram 应用或 [Telegram Web](https://web.telegram.org/) 登录账号，再在聊天界面搜索 `@BotFather`。`t.me/BotFather` 的介绍页上“Start Bot”只是唤起 Telegram 的入口；点击无反应时，直接在已登录的 Telegram 内搜索即可。

1. 在 Telegram 打开官方 [@BotFather](https://t.me/BotFather)，发送 `/newbot`，按提示设置名称和用户名。
2. 将获得的 Token 仅填入项目 `.env` 的 `TELEGRAM_BOT_TOKEN`；它认证的是机器人账号。不要提交到 Git。项目不需要个人 Telegram 的 api_id/api_hash。
3. 让实际收件人打开机器人并发送 `/start`。机器人不能仅凭手机号或私人用户名主动联系陌生用户。[Telegram 官方入门](https://core.telegram.org/bots)、[创建教程](https://core.telegram.org/bots/tutorial)
4. 暂不启动 polling，先在项目目录运行以下只读检查，取得机器人地址与近期联系者的数字 chat_id：

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System'
node --env-file=.env --import tsx scripts/messaging-setup.ts telegram-info
```

脚本不会发送消息，不打印 Token。由你核实 chat_id 对应的经纪人身份；群聊 ID 可能是负数，向群发送会让群成员看到内容。课堂测试建议使用小组成员与机器人的私人聊天。

5. 本地接收回复可配置：

```dotenv
TELEGRAM_BOT_TOKEN=填写自己的机器人Token
TELEGRAM_RECEIVE_MODE=polling
EXTERNAL_ACTION_MODE=live
```

6. 如果机器人此前配置过 webhook，先运行下面的命令移除它；命令保留待处理更新。`getUpdates` 与 webhook 不能同时接收更新。本项目 polling 每 5 秒检查一次，并在 PostgreSQL 保存游标及去重记录。[Telegram 更新与 webhook 接口](https://core.telegram.org/bots/api#getupdates)

```powershell
node --env-file=.env --import tsx scripts/messaging-setup.ts telegram-delete-webhook
```

7. 重启项目，在选定房源的“编辑草稿”中选择 Telegram、填写 chat_id，确认联系人和许可，保存、审核、发送。Telegram 单条正文超过 4096 字符会被本项目拦截。Bot API 成功返回只表示平台返回了消息记录；本项目不伪造 Telegram 已读回执。[sendMessage](https://core.telegram.org/bots/api#sendmessage)

如果需要长期部署的 webhook，配置可访问的 HTTPS 地址和随机 secret：

```dotenv
PUBLIC_BASE_URL=https://你的域名
TELEGRAM_RECEIVE_MODE=webhook
TELEGRAM_WEBHOOK_SECRET=自行生成的长随机字母数字和下划线字符串
```

```powershell
node --env-file=.env --import tsx scripts/messaging-setup.ts telegram-webhook
```

该命令将机器人 webhook 设置为 `https://你的域名/api/webhooks/telegram`，服务端校验 `X-Telegram-Bot-Api-Secret-Token`。更换接收方式后重启；不要在另一个进程同时轮询同一个机器人。[setWebhook](https://core.telegram.org/bots/api#setwebhook)

## WhatsApp：申请和配置

1. 登录 [Meta for Developers](https://developers.facebook.com/)，按当前界面创建适用于 WhatsApp Business Platform 的应用和业务资产，进入 WhatsApp 的 API 测试或设置页面。
2. 初始阶段使用控制台给出的测试号码，并按其提示添加、验证允许接收测试消息的号码。正式使用时完成控制台要求的业务号码注册、权限和审核。不要直接拿个人客户端号码代替 Phone Number ID。
3. 从控制台取得访问令牌、发送号码的 Phone Number ID、WABA ID；将 Graph API 版本填为控制台支持的版本，例如 `v数字.数字`。应用访问令牌、Phone Number ID、WABA ID 是不同项目，不能互换。临时 Token 不适合长期运行，持续部署按 Meta 流程配置系统用户令牌及相应资产权限。[Meta 官方 Cloud API 入门与令牌说明](https://www.postman.com/meta/whatsapp-business-platform/collection/wlk6lh4/whatsapp-cloud-api)
4. 在 `.env` 配置：

```dotenv
WHATSAPP_ACCESS_TOKEN=填写访问令牌
WHATSAPP_PHONE_NUMBER_ID=填写发送号码ID
WHATSAPP_BUSINESS_ACCOUNT_ID=填写WABA_ID
WHATSAPP_GRAPH_API_VERSION=填写控制台支持的版本
WHATSAPP_APP_SECRET=填写Meta应用AppSecret
WHATSAPP_VERIFY_TOKEN=自行生成的随机校验串
EXTERNAL_ACTION_MODE=live
```

发送需要 `whatsapp_business_messaging` 对应权限与资产授权；读取和管理消息模板还涉及 `whatsapp_business_management`。以控制台返回的实际权限、资产及审核要求为准，不要给普通应用用户返回 Token。

5. 将公网 HTTPS 反向代理的 **webhook 路径**指向本机 FastAPI 8010。Meta 回调 URL 填 `https://你的域名/api/webhooks/whatsapp`，Verify Token 填与 `.env` 相同的值，订阅该 WABA 的消息及其状态事件。GET 验证返回 challenge；POST 对原始正文用 App Secret 校验 HMAC-SHA256。只开放所需 webhook 路径；不要把 PostgreSQL、Neo4j、图服务3001或当前本地开发工作台整体暴露到公网。[Meta 官方 webhook 签名说明](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/)

6. **先取得对方许可**。首次主动联系和超出 24 小时窗口的联系，使用获批消息模板；只有业务收到对方来信后的服务窗口内才能发送自由文本。本项目依据已验证 webhook 中的原始来信时间判断窗口，不能在表单中手工填写“最近来信时间”绕过。[WhatsApp 官方 Business Messaging Policy](https://business.whatsapp.com/policy)

7. 在 WhatsApp Manager 创建适合房源咨询的模板，提交审核。模板内容及类别应符合实际用途和平台规则，不能保证某种文案一定获批。可以预留正文参数容纳房源链接、咨询内容及候选时间。工作台“读取获批文本模板”从当前 WABA 查询，最多读取前 100 条；当前仅支持 BODY 和可选 FOOTER、按 `{{1}}` 等位置编号的文本参数。不支持媒体头、按钮和命名参数模板。
8. 选择获批模板后，草稿将替换为模板实际渲染的正文，参数可编辑。将待核实条件、协商诉求和看房起止时间填入模板参数，重新核对预览。看房模板必须包含当前起止时间的完整 ISO 字符串；否则审批会拦截。项目不会用 `hello_world` 悄悄代替你的房源消息。
9. 保存、审批、发送后先看到“平台已受理”；只有收到相应回执才展示送达或已读。对方来信会进入动作时间线；愿意沟通不等于已确认看房，也不证明独立卫浴等条件已满足。

## 配置检查与启动

```powershell
node --env-file=.env --import tsx scripts/messaging-setup.ts status
.\scripts\stop-app.ps1
.\scripts\start-app.ps1 -SkipDataImport
```

`status` 只检查配置项是否齐全，不验证账号权限或号码可达。启动脚本保留 `.env` 中的动作模式，不再强制改回 simulation；只配置一个平台也可以使用它，未配置的平台无法真实发送。恢复演练时改回 `EXTERNAL_ACTION_MODE=simulation` 并重启。

## 真实账号验收顺序

- 先用你控制且已允许联系的小组成员账号。确认所用房源联系人字段是测试资料，不要把历史挂牌电话直接作为测试收件人。
- 审核具体房源、渠道地址、正文和时间；点击发送。检查平台消息ID，并由收件人确认实际收到。
- 再次点击同一修订的执行接口，不应产生第二条发送；编辑后旧审批失效。
- 收件人使用平台“回复该条消息”功能回复；时间线应收到来信。“等待回复”任务完成，看房提醒保留。
- 双方明确同意同一时段后手工记录依据，才变为看房确认。导出 `.ics` 检查时区和时段。
- 创建、修改、完成跟进提醒；生成当天总结并下载核对。提醒仅在应用内出现，应用停止时不会弹出系统通知；重新启动后处理到期提醒。
- `UNKNOWN` 表示可能已经提交但未取得确定结果。禁止自动重试；先在平台及收件人侧核对。确认未发送后，由用户重新起草、审核新动作。当前接口不会把失败动作直接重新排队。

当同一联系人同时对应多条动作且没有回复消息ID时，系统不猜测来信属于哪个会话：保留为未分配来信，不在其他用户会话展示。当前没有管理员分配未归属来信的界面，需由项目维护者核查数据库；建议让经纪人明确回复对应的消息。

## 当前功能范围

已实现 WhatsApp Cloud API 与 Telegram Bot API 的文本发送、接收及跟踪；日历是应用内日历和 ICS 导出，文档保存在 PostgreSQL，可下载 Markdown / HTML。没有邮件、SMS、Google Calendar、Google Drive API，也没有把对方的自然语言回复自动当作有约束力的预约确认。
