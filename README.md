# MarsChain Power Site

这是一个面向中国大陆访问场景的 MarsChain 算力排行榜静态站项目。

当前方案：

- 使用 GitHub Actions 每 5 小时自动抓取一次数据
- 按分层扫描规则逐档提升抓取深度，直到覆盖率 `>= 80%`
- 生成静态站点后上传到阿里云 OSS
- 通过阿里云 CDN 和独立域名对外提供 HTTPS 访问

## 项目文件

- [marschain_power_rank.py](marschain_power_rank.py)：深度扫描并生成排行榜原始结果
- [build_frontend_dashboard.py](build_frontend_dashboard.py)：将结果渲染成中文前端看板
- [publish_site.py](publish_site.py)：把排行榜产物整理成静态站目录
- [refresh_site.py](refresh_site.py)：总控脚本，负责分层扫描、覆盖率判断和站点刷新
- [deploy_to_oss.py](deploy_to_oss.py)：把 `site/` 同步到阿里云 OSS，并刷新 CDN 缓存
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
2. 命中 `>= 80%` 覆盖率后生成 `site/`
3. `deploy_to_oss.py` 将 `site/` 上传到阿里云 OSS
4. 刷新 CDN 首页、JSON 和下载文件缓存
5. 通过独立域名对外访问

## 说明

- 网站内容基于公开 explorer API 深度扫描生成，属于 `best effort` 榜单，不是官方后端直接导出的全量榜。
- 如果某轮抓取在最高档位后仍未达到 80% 覆盖率，站点仍会发布该轮最佳结果，并在首页显示醒目的未达标提示。
