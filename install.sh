#!/bin/bash
# YYZL TG 监控系统 - 一键安装脚本
set -e

GITHUB_TOKEN="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"
GITHUB_USER="maixiaomai18-ctrl"
GITHUB_REPO="yyzl-deploy"
TARBALL_NAME="yyzl-deploy-v20260425_0603.tar.gz"

echo ""
echo "============================================"
echo "  YYZL TG 监控系统 - 一键安装"
echo "============================================"
echo ""

if [ "$GITHUB_TOKEN" = "REPLACE_WITH_YOUR_PAT" ]; then
    echo "X 错误: install.sh 里的 GITHUB_TOKEN 还没替换"
    exit 1
fi

echo "[1/8] 检查基础工具..."
if ! command -v curl &> /dev/null; then
    apt-get update -qq && apt-get install -y -qq curl
fi
if ! command -v tar &> /dev/null; then
    apt-get install -y -qq tar
fi
echo "  OK"

echo ""
echo "[2/8] 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo "  Docker 未安装,正在自动安装(约 2 分钟)..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
fi
echo "  OK Docker 已就绪"

echo ""
echo "[3/8] 从 GitHub 下载部署包..."
cd /root
curl -fsSL \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3.raw" \
    "https://api.github.com/repos/${GITHUB_USER}/${GITHUB_REPO}/contents/${TARBALL_NAME}" \
    -o "${TARBALL_NAME}"

if [ ! -s "/root/${TARBALL_NAME}" ]; then
    echo "  X 下载失败,请检查 Token 或仓库名"
    exit 1
fi
echo "  OK 下载完成($(du -h /root/${TARBALL_NAME} | cut -f1))"

echo ""
echo "[4/8] 解压部署包..."
cd /root
rm -rf yyzl-deploy
tar -xzf "${TARBALL_NAME}"
cd yyzl-deploy
echo "  OK 解压完成"

echo ""
echo "[5/8] 开始配置..."
echo ""
echo "  请告诉我你是哪一位监察员?"
echo ""
echo "  1) 麦小麦"
echo "  2) 季霖"
echo "  3) 吴苍河"
echo "  4) 陈家碧"
echo ""
while true; do
    read -p "  输入数字 [1-4]: " choice
    case "$choice" in
        1) OPERATOR_NAME="麦小麦"; SHEET_GID="897421260"; break;;
        2) OPERATOR_NAME="季霖"; SHEET_GID="1607202551"; break;;
        3) OPERATOR_NAME="吴苍河"; SHEET_GID="1109052736"; break;;
        4) OPERATOR_NAME="陈家碧"; SHEET_GID="1232047050"; break;;
        *) echo "  请输入 1/2/3/4";;
    esac
done
echo "  OK 你是: $OPERATOR_NAME"

echo ""
read -p "  这是你的第几台 VPS? [1-9]: " VPS_NUM
if ! [[ "$VPS_NUM" =~ ^[1-9]$ ]]; then
    VPS_NUM=1
fi
VPS_CODE="${OPERATOR_NAME}-${VPS_NUM}号机"
echo "  OK VPS 编号: $VPS_CODE"

echo ""
echo "  输入你的 Bot Token (粘贴时屏幕不会显示,这是正常的):"
while true; do
    read -s -p "  Bot Token: " BOT_TOKEN
    echo ""
    if [[ "$BOT_TOKEN" =~ ^[0-9]+:.+ ]]; then
        echo "  OK Bot Token 格式正确"
        break
    else
        echo "  X 格式错误"
    fi
done

echo ""
echo "  设置 Web 后台密码 (至少 6 位):"
while true; do
    read -s -p "  Web 密码: " WEB_PASSWORD
    echo ""
    if [ ${#WEB_PASSWORD} -lt 6 ]; then
        echo "  X 至少 6 位"
        continue
    fi
    read -s -p "  再输一次: " WEB_PASSWORD2
    echo ""
    if [ "$WEB_PASSWORD" != "$WEB_PASSWORD2" ]; then
        echo "  X 两次不一致"
        continue
    fi
    echo "  OK 密码已设置"
    break
done
METRICS_TOKEN=$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 32)

echo ""
echo "[6/8] 生成 .env..."
{
    cat 共享配置.env
    echo ""
    echo "# === 监察员独有 ==="
    echo "OPERATOR_NAME=${OPERATOR_NAME}"
    echo "VPS_CODE=${VPS_CODE}"
    echo "BOT_TOKEN=${BOT_TOKEN}"
    echo "DAILY_SHEET_GID=${SHEET_GID}"
    echo "WEB_PASSWORD=${WEB_PASSWORD}"
    echo "METRICS_TOKEN=${METRICS_TOKEN}"
} > .env
chmod 600 .env
echo "  OK"

echo ""
echo "[7/8] 启动容器(约 2-3 分钟)..."
docker compose up -d --build 2>&1 | tail -10

echo ""
echo "[8/8] 等待 15 秒..."
sleep 15

if docker ps | grep -q "tg-monitor"; then
    echo "  OK 容器启动成功"
else
    echo "  X 容器启动失败"
    docker compose logs --tail 20
    exit 1
fi

VPS_IP=$(curl -s -m 5 ifconfig.me 2>/dev/null || echo "你的VPS外网IP")

echo ""
echo "============================================"
echo "  OK 安装全部完成!"
echo "============================================"
echo ""
echo "  监察员: $OPERATOR_NAME"
echo "  VPS:    $VPS_CODE"
echo "  IP:     $VPS_IP"
echo ""
echo "  ===== 浏览器打开下面链接登 Telegram ====="
echo ""
echo "    http://${VPS_IP}:8000"
echo ""
echo "  ====================================="
echo ""