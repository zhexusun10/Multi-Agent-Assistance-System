# 业务数据库迁移

`DomainStore.setup()` 直接读取 `001-business.sql`，在图服务启动时执行。SQL 采用 `IF NOT EXISTS`，可重复启动；新增列通过显式 `ALTER TABLE`，不删除既有会话或数据。

任务、会话事件和 LangGraph checkpoint 表仍由原仓库各 store 的 setup 创建。业务状态、下一步任务及事件使用同一个 PoolClient 和事务提交。`handled_results`、任务 step_key、请求 ID 和执行键分别负责结果应用、阶段、用户消息和外部动作的去重。

正式多人部署应引入有序迁移版本登记和备份流程；本次为课程本机部署，不执行破坏式迁移。
