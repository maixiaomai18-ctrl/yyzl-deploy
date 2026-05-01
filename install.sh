#!/bin/bash
# YYZL TG 监控系统 - GitHub 一键安装入口 v4
# 流程: 提示输 PAT → 下载 tar.gz → 解压 → 调用 安装.sh

set -e

REPO="maixiaomai18-ctrl/yyzl-deploy"
TAR_NAME="yyzl-deploy-v20260425_0603.tar.gz"
INSTALL_DIR="/root/yyzl-deploy"

echo ""
echo "============================================"
echo "  YYZL TG 监控系统 - 一键安装 v4"
echo "============================================"
echo ""

# === [0] 拿 PAT ===
if [ -z "$YYZL_PAT" ]; then
    echo "请粘贴 GitHub PAT (跟一键命令里同一个, 屏幕不显示):"
    read -r -s YYZL_PAT
    echo ""
fi
if [ -z "$YYZL_PAT" ]; then
    echo "  X PAT 不能为空"
    exit 1
fi
PAT="$YYZL_PAT"

# === [1/4] 检查工具 ===
echo "[1/4] 检查基础工具..."
for cmd in curl tar; do
    if ! command -v $cmd &> /dev/null; then
        apt-get update -qq && apt-get install -y -qq $cmd
    fi
done
echo "  OK"
echo ""

# === [2/4] 检查 Docker ===
echo "[2/4] 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo "  Docker 未安装, 自动安装..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
fi
echo "  OK Docker 已就绪"
echo ""

# === [3/4] 下载 ===
echo "[3/4] 从 GitHub 下载部署包..."
rm -f /tmp/yyzl-deploy.tar.gz

HTTP_CODE=$(curl -s -L -o /tmp/yyzl-deploy.tar.gz -w "%{http_code}" \
    -H "Authorization: token $PAT" \
    -H "Accept: application/vnd.github.v3.raw" \
    "https://api.github.com/repos/$REPO/contents/$TAR_NAME")

if [ "$HTTP_CODE" != "200" ]; then
    echo "  X 下载失败 (HTTP $HTTP_CODE)"
    [ "$HTTP_CODE" == "401" ] && echo "    PAT 无效或无权限"
    [ "$HTTP_CODE" == "404" ] && echo "    文件不存在: $TAR_NAME"
    exit 1
fi

SIZE_K=$(($(stat -c%s /tmp/yyzl-deploy.tar.gz 2>/dev/null || echo 0) / 1024))
echo "  OK 下载完成 (${SIZE_K}K)"
echo ""

# === [4/4] 解压 + 调用安装.sh ===
echo "[4/4] 解压部署包..."
if [ -d "$INSTALL_DIR" ]; then
    BACKUP_NAME="${INSTALL_DIR}-old-$(date +%s)"
    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        cd "$INSTALL_DIR" && docker compose down 2>/dev/null || true
    fi
    mv "$INSTALL_DIR" "$BACKUP_NAME"
    echo "  旧目录已备份: $BACKUP_NAME"
fi
cd /root && tar xzf /tmp/yyzl-deploy.tar.gz
if [ ! -d "$INSTALL_DIR" ]; then
    echo "  X 解压失败"
    exit 1
fi
echo "  OK 解压完成"
echo ""

cd "$INSTALL_DIR"
bash 安装.sh
