# 拾信 Mail Viewer

单页 Outlook 邮件提取工具，通过 Microsoft Graph 直连读取收件箱并高亮验证码。

## 一键部署

```bash
cp .env.example .env      # 可选：填入 SECRET_KEY
docker compose up -d --build
```

打开 http://127.0.0.1:8000 即可使用。

## 常用命令

```bash
docker compose logs -f          # 查看日志
docker compose down             # 停止并删除
docker compose up -d --build    # 重新构建
```

## 打包与发布到 Docker Hub

`build-push.sh` 用于本地构建并发布镜像，支持 **Linux 与 macOS**，并自动构建多架构镜像。

```bash
./build-push.sh --login     # 首次使用，先登录 Docker Hub
./build-push.sh             # 构建 amd64+arm64 并推送
./build-push.sh -t v1.0.0   # 追加自定义标签
```

| 参数 | 说明 |
| --- | --- |
| （无） | 构建 `linux/amd64,linux/arm64` 并推送 `latest` + 日期标签 |
| `--login` | 先执行 `docker login` |
| `--load` | 仅构建本机架构并加载到本地，不推送 |
| `--no-push` | 构建多架构但不推送，仅验证能否构建成功 |
| `-t, --tag <标签>` | 追加一个标签，如 `v1.0.0` |
| `--platforms <列表>` | 覆盖目标架构 |
| `--image <仓库>` | 覆盖完整镜像名 |

可用环境变量（或写入 `.env.deploy`）：

| 变量 | 默认值 |
| --- | --- |
| `DOCKER_USER` | `ningning0111` |
| `IMAGE_NAME` | `outlook-viewer` |
| `PLATFORMS` | `linux/amd64,linux/arm64` |
| `TAG` | 空 |

发布结果：

```text
docker pull ningning0111/outlook-viewer:latest
docker pull ningning0111/outlook-viewer:20260923
```

### 使用已发布镜像

```bash
docker run -d \
  --name outlook-viewer \
  -p 127.0.0.1:8000:8000 \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  --read-only --tmpfs /tmp:size=16m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  ningning0111/outlook-viewer:latest
```

> 依赖 `docker buildx`（Docker Desktop 自带）。多架构镜像无法 `--load` 到本地，需推送到仓库后拉取。

## 配置项（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 空 | 会话签名密钥。留空则每次启动随机生成；固定它可在重启后保持会话。用 `openssl rand -hex 32` 生成 |
| `COOKIE_SECURE` | `0` | 走 HTTPS 时设为 `1`，Cookie 仅通过 HTTPS 传输 |
| `HOST_PORT` | `8000` | 宿主机端口，默认只绑定 `127.0.0.1` |
| `DEFAULT_CLIENT_ID` | 空 | 可选，预填页面上的 clientid 输入框 |

## 输入格式

在页面粘贴以下任意一种（也支持 `—-` 分隔符）：

```text
邮箱----密码----clientid----refreshtoken
邮箱----密码----API地址----邮箱----密码----clientid----refreshtoken
邮箱----密码----邮箱----clientid----refreshtoken
邮箱----clientid----refreshtoken
```

也可只填邮箱，在下方单独填写 clientid 与 refresh token。

> 密码与 API 地址字段仅用于兼容输入，系统统一通过 Microsoft Graph 直连读取。

## 安全说明

- 凭据仅用于单次请求，不落库、不写日志；容器的日志已限制为 5MB × 3。
- 出站请求仅允许 `login.microsoftonline.com` 与 `graph.microsoft.com`。
- 容器以非 root 用户运行，根文件系统只读，仅 `/tmp` 可写，并丢弃全部 capabilities。
- 默认仅监听 `127.0.0.1`，公网发布请在前方加反向代理或隧道。
- 请仅填写本人或已获授权的邮箱。

## 镜像说明

多阶段构建，构建工具不进入最终镜像：

- 镜像大小约 **92 MB**（压缩后约 22 MB）
- 基于 `python:3.12-alpine`
- 运行时仅安装 Flask / requests / gunicorn

## 测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```
