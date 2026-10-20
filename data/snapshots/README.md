# 完整房源快照

`propertyguru-2026-10-09.zip` 是团队已上传的完整 SQLite 快照，保留自上游 main `907cfa8` 的 `data/propertyguru(1).zip`；只改文件路径，压缩包内容未改动。其数据库与当前本地部署使用的数据完全一致。

| 项目 | 值 |
| --- | --- |
| 快照日期 | 2026-10-09 |
| ZIP大小 | 67,325,490字节 |
| 解压后文件 | propertyguru.db |
| 解压后大小 | 1,053,462,528字节 |
| SQLite SHA-256 | `3fb6bdd8bc1b677d44abf1b3bb036692a00ccfa002613970bf5860529b82a2e5` |
| properties / agents / property_images / price_history | 21,431 / 4,657 / 418,700 / 0 |

首次克隆后，在项目根目录解压（已有 `data/propertyguru.db` 时先校验，勿覆盖正在使用的文件）：

```powershell
Expand-Archive -LiteralPath '.\data\snapshots\propertyguru-2026-10-09.zip' -DestinationPath '.\data'
Get-FileHash -LiteralPath '.\data\propertyguru.db' -Algorithm SHA256
```

然后按 [本地部署说明](../../LOCAL_DEPLOYMENT.md) 导入 PostgreSQL / Neo4j。解压的1GB数据库和运行时数据仍被 Git 忽略；仓库只保留这一份完整压缩快照。它是历史挂牌资料，不证明房源目前仍可租。
