# 阿里云上线说明

这套站点默认按“阿里云 OSS + CDN + 自定义域名 + GitHub Actions 定时刷新”上线。

如果 CDN 和域名还没接好，也可以先走“只发 OSS”的过渡形态：

- GitHub Actions 继续每 5 小时构建和上传
- `ALIYUN_SITE_BASE_URL` 先留空
- 工作流会跳过 CDN 刷新
- 等 CDN 和域名准备好后，再补上 `ALIYUN_SITE_BASE_URL`

## 1. 阿里云侧准备

1. 购买并实名新域名。
2. 给域名完成 `ICP备案`。
3. 创建一个**专用** OSS Bucket 用于这个排行榜站点。
4. 打开 OSS 静态网站托管，首页设为 `index.html`。
5. 创建 CDN 加速域名，并把 OSS Bucket 设为源站。
6. 给 CDN 绑定你备案完成的自定义域名。
7. 配置 HTTPS 证书，并强制 HTTPS。
8. DNS 把自定义域名 `CNAME` 到 CDN 分配的加速域名。

## 2. RAM 子账号权限

不要用主账号。

给专用 RAM 子账号开最小权限：

- OSS Bucket 的读写、列目录、删对象
- CDN 刷新缓存权限

建议这个子账号只服务这一个站点。

## 3. GitHub Secrets

在仓库里配置这些 Secrets：

- `ALIBABA_CLOUD_ACCESS_KEY_ID`
- `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
- `ALIYUN_OSS_BUCKET`
- `ALIYUN_OSS_ENDPOINT`
- `ALIYUN_OSS_PREFIX`
- `ALIYUN_SITE_BASE_URL`

说明：

- `ALIYUN_OSS_BUCKET`：站点 Bucket 名称
- `ALIYUN_OSS_ENDPOINT`：例如 `oss-cn-hangzhou.aliyuncs.com`
- `ALIYUN_OSS_PREFIX`：如果你要把站点放在 Bucket 根目录，留空即可
- `ALIYUN_SITE_BASE_URL`：例如 `https://mars.example.com`
  - 如果 CDN 和独立域名还没准备好，可以暂时留空

## 4. GitHub Actions 行为

工作流文件在：

- `.github/workflows/update-marschain-site.yml`

它会每 5 小时执行一次：

1. 跑 `refresh_site.py`
2. 按分层扫描规则冲到 `80%` 覆盖率
3. 生成 `site/`
4. 归档 `output/latest`
5. 同步 `site/` 到 OSS
6. 如果配置了 `ALIYUN_SITE_BASE_URL`，再刷新首页、最新 JSON、下载文件和构建摘要的 CDN 缓存

## 5. 本地验证

先生成站点：

```bash
python3 refresh_site.py --coverage-target 0.80 --output-dir output --site-dir site
```

只演练 OSS 发布动作，不真正上传：

```bash
python3 deploy_to_oss.py \
  --site-dir site \
  --bucket your-bucket \
  --endpoint oss-cn-hangzhou.aliyuncs.com \
  --allow-bucket-root \
  --dry-run

如果要同时演练 CDN 刷新：

```bash
python3 deploy_to_oss.py \
  --site-dir site \
  --bucket your-bucket \
  --endpoint oss-cn-hangzhou.aliyuncs.com \
  --base-url https://mars.example.com \
  --allow-bucket-root \
  --dry-run
```
```

## 6. 风险提醒

- 如果 `ALIYUN_OSS_PREFIX` 为空，脚本默认要求显式加 `--allow-bucket-root`
- 这是为了避免误删共享 Bucket 里的其他文件
- 推荐始终使用**专用 Bucket**
