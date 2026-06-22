# MarsChain Power Dashboard

这是排行榜网站的静态发布目录，设计目标是：

- 由 GitHub Actions 每 24 小时自动刷新，抓取时间为每日 00:00（北京时间，夜里 24:00）
- 按分层扫描规则抓到覆盖率 `>= 80%` 再发布
- 发布到阿里云 OSS 静态网站源站
- 通过阿里云 CDN 和独立域名对外访问

## 本地预览

```bash
cd site
python3 -m http.server 8000
```

然后打开：

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

## 目录说明

- `index.html`：首页
- `data/latest.json`：公开前 100 榜单 JSON
- `build-meta.json`：最近一次构建摘要

## 发布方式

当前默认发布链不是 GitHub Pages，而是：

1. GitHub Actions 定时执行 [refresh_site.py](/Users/chu/Documents/openclaw/refresh_site.py)
2. 生成最新 `site/` 目录
3. 通过 [deploy_to_oss.py](/Users/chu/Documents/openclaw/deploy_to_oss.py) 同步到阿里云 OSS
4. 刷新阿里云 CDN 的首页、JSON、构建摘要和旧公开下载文件缓存
5. 由独立域名通过 CNAME 指向 CDN 域名对外提供 HTTPS 访问

详细云端配置步骤见：

- [ALIYUN_DEPLOY.md](/Users/chu/Documents/openclaw/ALIYUN_DEPLOY.md)

## 说明

- 这是基于公开 explorer API 深度扫描得到的 `best effort` 榜单，不是官方后端直接导出的全量榜。
- 如果某轮扫描在最高档位后仍未达到 80% 覆盖率，首页会显示黄条提醒，但仍会发布该轮最佳结果。
