# 节点排行榜预览

此目录提供独立的服务器预览，不会上传正式 OSS、提交 GitHub 或改动原有采集数据库。
现有桌面、手机板块沿用原站，公开文件最多包含 100 行地址和算力，不含账户余额。

## 服务器上的运行位置

- 程序：`~/marschain-rank-preview/app/`
- 私有工作库：`~/marschain-rank-preview/state/preview.sqlite3`
- 页面：`~/marschain-rank-preview/site/`
- 私有固定区块归档：`~/marschain-rank-preview/archives/`
- 服务器本机预览：`http://127.0.0.1:18880/`
- 运行状态：`http://127.0.0.1:18880/api/status`

数据库由原 `scan.sqlite3` 使用 SQLite backup API 一致复制。旧库及旧任务继续独立工作。
预览服务只监听 loopback；网站目录不包含数据库和完整地址归档。

## 后台流程

1. `collector` 每 15 秒尝试跟进已确认区块，保留 6 个确认块。
   以持久区块断点恢复，读取交易及声明过 ABI 的 POWER 事件，刷新关联的两级上级。
   算力、销毁、关系由本机 `getUserInfo(address)` 查询；没有余额查询和分钟 CSV 导出。
   停机较久时重读遗漏区块、查询当前确认状态，避免依赖已被裁剪的旧状态。
2. 初次分批校准全部已知地址，完成前明确沿用旧官网种子排名。
   完成后保留低优先级每日轮询校验；不从创世区块重新发现地址。
3. `publisher` 每分钟从一致数据库快照生成最多前 100 名及现有卡片的数据。
   等待未完成的追赶，拒绝陈旧采集和未经核验的节点总值。
   HTML 只在代码变化时重建，普通循环只原子替换 `data/latest.json`。
4. 网页在可见时每 25 秒检查数据。支持 ETag、304 和 gzip，保留分页、趋势周期和计算器输入。
   来源失败保留带原时间的有效值；区块与采集时间决定陈旧状态，不能用发布时间掩盖停机。
5. `observations` 一次补齐当前及最近两个统计日的区块和 POWER 日志，供原有交易、活跃和销毁日统计使用。
   这不是全链地址历史扫描，不把观察到的新候选地址数量当作全网新增地址。
6. `freeze.timer` 在每日 UTC 00:00:35，即北京时间 08:00:35，选择 08:00 前最后一个已确认区块，
   独立查询整个已知地址集合并原子冻结私有归档。延迟执行仍使用同一截止区块。
   同一截止时间成功后不会重新抓取当前值覆盖旧归档。非日结演练使用单独目录。

## 数据口径

- 地址库仍不完整。算力覆盖率是已收录算力与合约全网算力的比例，不能当作地址覆盖率。
- 全网算力、累计销毁读本机 POWER 合约；价格、流通量和全网地址数暂沿用官方统计接口。
  每项记录来源、区块或抓取时间、缓存/失败状态。
- 日结与分钟数据隔离。只有区块、父哈希、日志覆盖及销毁核验通过才推进新的日结和日趋势。
  已裁剪的旧状态使用合约已完成日算力历史及完整销毁事件核对。
- 全网新增地址需要可信边界快照，不能由不完整地址库推算。缺少依据时显示缺项。
  7 天、30 天无法按相同口径核准的部分保留原截止时间，页面直接标注。
- 原预言机倒计时已到期，显示等待官方下一期，不自行编造新截止时间。

## 状态和恢复

```bash
systemctl --user status marschain-rank-preview-collector.service
systemctl --user status marschain-rank-preview-publisher.service
systemctl --user status marschain-rank-preview-web.service
systemctl --user list-timers marschain-rank-preview-freeze.timer
journalctl --user -u marschain-rank-preview-collector.service -n 5 --no-pager
```

后台服务由用户 systemd 管理，配置了 linger，不依赖 Mac 的 SSH 窗口。
Mac 的 `com.marschainrank.node-preview-tunnel` LaunchAgent 只负责把预览转到本机 18880。
Mac 关闭时服务器仍运行；在服务器桌面可直接访问同一 URL。

区块哈希不一致时采集进入 `needs_reconcile` 并停止推进，发布保留最近有效版本。
不要简单删除断点绕过该状态；应检查分叉范围并重新核对受影响状态。

## 仍待验收的正式接入

此预览**没有** GitHub 写入凭据或阿里云上传凭据，也不修改原站定时任务。
08:00 归档目前只冻结本机，不能据此称为云端备份已完成。
正式切换时仍需配置最小发布授权、验证上传、确定私有备份位置，并统一旧工作流的发布角色。

## 验证

```bash
python3 -m unittest discover -s node_preview -t . -p 'test_*.py'
```

覆盖断点恢复、上级联动、固定区块、RPC 失败保值、重组、公开边界、日结隔离、趋势、固定快照和发布陈旧保护。
