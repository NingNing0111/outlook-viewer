#!/usr/bin/env bash
#
# 本地打包并发布镜像到 Docker Hub。
#
# 用法:
#   ./build-push.sh                    # 构建 amd64+arm64 并推送 latest + 日期标签
#   ./build-push.sh -t v1.0.0          # 追加自定义标签
#   ./build-push.sh --load             # 只构建本机架构并加载到本地(不推送)
#   ./build-push.sh --no-push          # 构建多架构但不推送(仅验证)
#   ./build-push.sh --login            # 先登录 Docker Hub
#
# 环境变量(可选, 也可写入 .env.deploy):
#   DOCKER_USER    Docker Hub 用户名, 默认 ningning0111
#   IMAGE_NAME     镜像名, 默认 outlook-viewer
#   PLATFORMS      目标架构, 默认 linux/amd64,linux/arm64
#   TAG            额外标签
#
set -euo pipefail

# ---------------------------------------------------------------- 基础设置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# macOS 自带的 bash 3.2 也要能跑, 因此不使用 bash 4+ 特性。
DOCKER_USER="${DOCKER_USER:-ningning0111}"
IMAGE_NAME="${IMAGE_NAME:-outlook-viewer}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
TAG="${TAG:-}"

# 可选配置文件
if [ -f "$SCRIPT_DIR/.env.deploy" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/.env.deploy"
  DOCKER_USER="${DOCKER_USER:-ningning0111}"
  IMAGE_NAME="${IMAGE_NAME:-outlook-viewer}"
  PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
fi

IMAGE_REF="${DOCKER_USER}/${IMAGE_NAME}"
BUILDER_NAME="mailviewer-builder"

DO_LOGIN=0
DO_PUSH=1
DO_LOAD=0

# ---------------------------------------------------------------- 参数解析
while [ $# -gt 0 ]; do
  case "$1" in
    -t|--tag)       TAG="${2:-}"; shift 2 ;;
    --tag=*)        TAG="${1#*=}"; shift ;;
    --platforms)    PLATFORMS="${2:-}"; shift 2 ;;
    --platforms=*)  PLATFORMS="${1#*=}"; shift ;;
    --image)        IMAGE_REF="${2:-}"; shift 2 ;;
    --login)        DO_LOGIN=1; shift ;;
    --load)         DO_LOAD=1; DO_PUSH=0; shift ;;
    --no-push)      DO_PUSH=0; shift ;;
    -h|--help)      sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              echo "未知参数: $1 (用 --help 查看用法)" >&2; exit 2 ;;
  esac
done

# 颜色(非终端时自动关闭)
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
else
  B=''; G=''; Y=''; R=''; N=''
fi
info() { printf '%s==>%s %s\n' "$B" "$N" "$1"; }
ok()   { printf '%s ✓ %s%s\n' "$G" "$1" "$N"; }
warn() { printf '%s ! %s%s\n' "$Y" "$1" "$N"; }
die()  { printf '%s ✗ %s%s\n' "$R" "$1" "$N" >&2; exit 1; }

# ---------------------------------------------------------------- 环境检查
info "检查运行环境"

command -v docker >/dev/null 2>&1 || die "未找到 docker, 请先安装 Docker Desktop 或 Docker Engine"

if ! docker info >/dev/null 2>&1; then
  die "无法连接 Docker 守护进程, 请确认 Docker 已启动"
fi

# buildx 是多架构构建的前提
if ! docker buildx version >/dev/null 2>&1; then
  die "未找到 docker buildx。请安装 buildx 插件 (Docker Desktop 默认自带)"
fi
ok "docker 与 buildx 就绪"

[ -f "$SCRIPT_DIR/Dockerfile" ] || die "当前目录没有 Dockerfile: $SCRIPT_DIR"
ok "构建上下文: $SCRIPT_DIR"

# ---------------------------------------------------------------- 生成标签
# 日期标签, 兼容 macOS/Linux 的 date
if date -u +%Y%m%d >/dev/null 2>&1; then
  DATE_TAG="$(date -u +%Y%m%d)"
else
  DATE_TAG="$(date -u +%Y%m%d 2>/dev/null || echo unknown)"
fi

TAGS="--tag ${IMAGE_REF}:latest --tag ${IMAGE_REF}:${DATE_TAG}"
if [ -n "$TAG" ]; then
  TAGS="$TAGS --tag ${IMAGE_REF}:${TAG}"
fi

info "镜像: ${IMAGE_REF}"
info "标签: latest, ${DATE_TAG}${TAG:+, $TAG}"

# ---------------------------------------------------------------- 登录
if [ "$DO_LOGIN" = "1" ]; then
  info "登录 Docker Hub (用户: ${DOCKER_USER})"
  docker login -u "$DOCKER_USER"
  ok "登录完成"
fi

check_docker_login() {
  # 本次执行如果已经显式执行了 login 并成功
  if [ "$DO_LOGIN" = "1" ]; then
    return 0
  fi
  # 检查旧版 docker info 输出
  if docker info 2>/dev/null | grep -q 'Username:'; then
    return 0
  fi
  # 检查 Docker 配置文件中的凭据或 registry 配置
  local config_file="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  if [ -f "$config_file" ]; then
    if grep -qE '("https://index\.docker\.io/v1/"|"registry-1\.docker\.io"|"docker\.io")' "$config_file" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

if [ "$DO_PUSH" = "1" ]; then
  if check_docker_login; then
    ok "Docker Hub 登录状态正常"
  else
    warn "未检测到 Docker Hub 登录凭据"
    warn "若稍后推送失败，请先执行: $0 --login"
  fi
fi

# ---------------------------------------------------------------- 构建
# 多架构镜像无法直接 --load 到本地, 因此分开处理。
if [ "$DO_LOAD" = "1" ]; then
  HOST_ARCH="$(docker version --format '{{.Server.Arch}}' 2>/dev/null || echo amd64)"
  case "$HOST_ARCH" in
    x86_64) HOST_ARCH=amd64 ;;
    aarch64) HOST_ARCH=arm64 ;;
  esac
  info "仅构建本机架构 (linux/${HOST_ARCH}) 并加载到本地"

  docker buildx build \
    --platform "linux/${HOST_ARCH}" \
    $TAGS \
    --load \
    .

  ok "已构建并加载: ${IMAGE_REF}:latest"
  printf '\n运行:\n  docker run --rm -p 8000:8000 %s:latest\n' "$IMAGE_REF"
  exit 0
fi

# 使用独立的 builder 实例, 避免污染默认 builder
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  info "创建 buildx 构建器: ${BUILDER_NAME}"
  docker buildx create --name "$BUILDER_NAME" --use >/dev/null
else
  docker buildx use "$BUILDER_NAME" >/dev/null
fi
docker buildx inspect --bootstrap >/dev/null 2>&1 || true

info "构建平台: ${PLATFORMS}"

BUILD_ARGS=""
if [ "$DO_PUSH" = "1" ]; then
  BUILD_ARGS="--push"
  info "构建完成后将推送到 Docker Hub"
else
  info "构建后不推送 (--no-push)"
fi

# 多架构 + --push 不能同时 --load, 所以推送时只校验构建结果。
# shellcheck disable=SC2086
docker buildx build \
  --platform "$PLATFORMS" \
  $TAGS \
  $BUILD_ARGS \
  --provenance=false \
  .

if [ "$DO_PUSH" = "1" ]; then
  ok "推送完成"
  printf '\n镜像地址:\n'
  printf '  docker pull %s:latest\n' "$IMAGE_REF"
  printf '  docker pull %s:%s\n' "$IMAGE_REF" "$DATE_TAG"
  [ -n "$TAG" ] && printf '  docker pull %s:%s\n' "$IMAGE_REF" "$TAG"
  printf '\n已发布架构:\n'
  # 尽力展示远端 manifest, 失败不影响结果
  docker buildx imagetools inspect "$IMAGE_REF:latest" 2>/dev/null \
    | grep -E 'Name:|Platform:|MediaType:' || warn "无法读取远端 manifest (不影响推送结果)"
else
  ok "构建完成(未推送)"
fi
