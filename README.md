# MarsChain Power Site

这是一个面向中国大陆访问场景的 MarsChain 算力排行榜静态站项目。

当前方案：

- 使用 GitHub Actions 每 5 小时自动抓取一次数据
- 基于公开 RPC 扫描 POWER 合约日志，扫描窗口设为 1000 万块以覆盖当前全链，尽量提高覆盖率
- 算力缓存默认 3 小时过期，短于 5 小时定时周期，避免定时刷新继续使用旧算力
- 首页明确展示全链地址、算力候选钱包、正算力钱包、链上今日新增钱包和链上今日新增总算力
- 页面保留公开数据口径和风险提示，避免把 `best effort` 榜单误读成官方最终口径
- 页面免费公开前 100 名，全量排行榜下载走 MARS 付款核销
- 生成静态站点后上传到阿里云 OSS
- 全量 CSV / Excel 可上传到私有 OSS Bucket，由后端生成 1 小时有效签名链接
- 通过阿里云 CDN 和独立域名对外提供 HTTPS 访问

## 项目文件

- [marschain_power_rank.py](marschain_power_rank.py)：深度扫描并生成排行榜原始结果
- [build_frontend_dashboard.py](build_frontend_dashboard.py)：将结果渲染成中文前端看板
- [publish_site.py](publish_site.py)：把排行榜产物整理成静态站目录
- [refresh_site.py](refresh_site.py)：总控脚本，负责分层扫描、覆盖率判断和站点刷新
- [deploy_to_oss.py](deploy_to_oss.py)：把 `site/` 同步到阿里云 OSS，并刷新 CDN 缓存
- [paid_download_service.py](paid_download_service.py)：核销 1000 MARS 转账并返回 1 小时有效的私有下载链接
- [ALIYUN_DEPLOY.md](ALIYUN_DEPLOY.md)：阿里云上线步骤和 GitHub Secrets 配置说明
- [update-marschain-site.yml](.github/workflows/update-marschain-site.yml)：每 5 小时自动更新的 GitHub Actions 工作流

## 本地运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

刷新站点：

```bash
python3 refresh_site.py --coverage-target 0.80 --output-dir output --site-dir site
```

本地预览：

```bash
cd site
python3 -m http.server 8000
```

## 自动发布

默认发布链：

1. GitHub Actions 定时执行 `refresh_site.py`
2. 扫描完成后生成 `site/`，即使未达 80% 也发布当轮最佳结果
3. `deploy_to_oss.py` 将 `site/` 上传到阿里云 OSS
4. 如果配置了私有下载 Bucket，同步全量 CSV / Excel 到私有 OSS
5. 刷新 CDN 首页、JSON 和构建摘要缓存
6. 通过独立域名对外访问

## 付费下载

默认收费规则：

- 前 100 名免费公开
- 全量排行榜下载收费 `1000 MARS`
- 收款地址：`0M8678F454D69d2185DfAa6643cF06faCB8DE17c7c`
- 后端验账地址：`0x8678F454D69d2185DfAa6643cF06faCB8DE17c7c`
- 下载链接有效期：1 小时

前端只在 `MARSCHAIN_PAID_DOWNLOAD_API_BASE` 配置后启用订单按钮。后端需要部署 [paid_download_service.py](paid_download_service.py)，并使用私有 OSS Bucket 保存全量文件和订单记录。

## 访问统计

站点已经预留了两套统计接入位，默认不公开任何统计结果：

- `BAIDU_TONGJI_SITE_ID`：百度统计站点 ID
- `MICROSOFT_CLARITY_PROJECT_ID`：Microsoft Clarity 项目 ID

只要把这两个值配置进 GitHub Secrets，构建时就会自动注入官方统计脚本。

当前已埋点的前端行为包括搜索、筛选、排序和付费下载入口点击。

统计结果只会进入你自己的百度统计 / Clarity 后台，不会写进公开站点文件，也不会暴露给访客。

## 说明

- 网站内容基于公开 explorer API 深度扫描生成，属于 `best effort` 榜单，不是官方后端直接导出的全量榜。
- 如果某轮抓取在最高档位后仍未达到 80% 覆盖率，站点仍会发布该轮最佳结果，并在首页显示醒目的未达标提示。
