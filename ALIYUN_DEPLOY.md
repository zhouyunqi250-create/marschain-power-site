# 阿里云上线说明

这套站点默认按“阿里云 OSS + CDN + 自定义域名 + GitHub Actions 定时刷新”上线。

如果 CDN 和域名还没接好，也可以先走“只发 OSS”的过渡形态：

- GitHub Actions 继续每 24 小时构建和上传，抓取时间为每日 00:00（北京时间，夜里 24:00）
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
9. 另建一个**私有** OSS Bucket 保存付费下载文件和订单记录。
10. 部署一个 HTTP 函数运行 [paid_download_service.py](/Users/chu/Documents/openclaw/paid_download_service.py)。可以直接使用 `Deploy Paid Download Backend` 工作流自动完成。

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
- `ALIYUN_PAID_OSS_BUCKET`
- `ALIYUN_PAID_OSS_ENDPOINT`
- `ALIYUN_PAID_OSS_PREFIX`
- `ALIYUN_SITE_BASE_URL`
- `MARSCHAIN_PAID_DOWNLOAD_API_BASE`
- `BAIDU_TONGJI_SITE_ID`
- `MICROSOFT_CLARITY_PROJECT_ID`

说明：

- `ALIYUN_OSS_BUCKET`：站点 Bucket 名称
- `ALIYUN_OSS_ENDPOINT`：例如 `oss-cn-hangzhou.aliyuncs.com`
- `ALIYUN_OSS_PREFIX`：如果你要把站点放在 Bucket 根目录，留空即可
- `ALIYUN_PAID_OSS_BUCKET`：私有下载 Bucket 名称，必须不是公开静态网站 Bucket
- `ALIYUN_PAID_OSS_ENDPOINT`：私有下载 Bucket 所在地域 endpoint，可与站点 Bucket 相同
- `ALIYUN_PAID_OSS_PREFIX`：私有下载文件前缀，默认建议 `paid-downloads`
- `ALIYUN_SITE_BASE_URL`：例如 `https://mars.example.com`
  - 如果 CDN 和独立域名还没准备好，可以暂时留空
- `MARSCHAIN_PAID_DOWNLOAD_API_BASE`：付费下载 API 地址，例如 `https://mars.example.com/api`
- `BAIDU_TONGJI_SITE_ID`：百度统计站点 ID，可选
- `MICROSOFT_CLARITY_PROJECT_ID`：Clarity 项目 ID，可选

统计说明：

- 这两个值都应该放在 GitHub Secrets，不要写死在仓库里
- 构建时只注入官方统计脚本，不会把统计报表写进 OSS
- 浏览量、点击量、热力图只在你自己的后台账号里查看，访客无法直接读取

## 4. GitHub Actions 行为

工作流文件在：

- `.github/workflows/update-marschain-site.yml`

它会每 24 小时执行一次，抓取时间为每日 00:00（北京时间，夜里 24:00）：

1. 跑 `refresh_site.py`
2. 按分层扫描规则冲到 `80%` 覆盖率
3. 生成 `site/`
4. 归档 `output/latest`
5. 同步 `site/` 到 OSS
6. 如果配置了 `ALIYUN_PAID_OSS_BUCKET`，把全量 CSV / Excel 上传到私有 OSS
7. 如果配置了 `ALIYUN_SITE_BASE_URL`，再刷新首页、最新 JSON、构建摘要和旧公开下载文件的 CDN 缓存

## 5. 付费下载 API

[paid_download_service.py](/Users/chu/Documents/openclaw/paid_download_service.py) 提供三个接口：

- `POST /api/orders`：创建付款订单
- `POST /api/orders/{orderId}/verify`：提交交易哈希并自动验链
- `GET /api/orders/{orderId}/download`：已付款订单重新获取下载链接

函数环境变量：

- `ALIBABA_CLOUD_ACCESS_KEY_ID`
- `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
- `ALIYUN_PAID_OSS_BUCKET`
- `ALIYUN_PAID_OSS_ENDPOINT`
- `ALIYUN_PAID_OSS_PREFIX`
- `MARSCHAIN_RPC_URL`，默认 `https://rpcs.marschain.net`
- `MARS_PAYMENT_ADDRESS_DISPLAY`，默认 `0M0fD038365577215292B44F89C92695C7AC8C3363`
- `MARS_PAYMENT_ADDRESS_VERIFY`，默认 `0x0fD038365577215292B44F89C92695C7AC8C3363`
- `MARS_PAID_DOWNLOAD_PRICE`，默认 `1000`
- `MARS_PAID_DOWNLOAD_PRICE_WEI`，默认 `1000000000000000000000`
- `MARS_PAID_DOWNLOAD_EXPIRES_SECONDS`，默认 `3600`
- `MARS_PAYMENT_CONFIRMATIONS`，默认 `3`

这个函数只核销 MarsChain 原生 MARS 转账，不需要 ERC20 合约地址。

也可以直接运行 GitHub Actions 里的 `Deploy Paid Download Backend` 工作流。它会：

1. 创建或复用私有下载 Bucket
2. 部署 HTTP 核销函数
3. 用返回的函数 URL 重新生成站点
4. 上传公开站点和私有全量下载文件
5. 验证 `/health`、公开前 100 JSON 和全量数量

## 6. 本地验证

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
```

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

## 7. 风险提醒

- 如果 `ALIYUN_OSS_PREFIX` 为空，脚本默认要求显式加 `--allow-bucket-root`
- 这是为了避免误删共享 Bucket 里的其他文件
- 推荐始终使用**专用 Bucket**
