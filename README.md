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
