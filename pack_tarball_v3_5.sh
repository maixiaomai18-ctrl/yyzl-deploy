#!/bin/bash
# pack_tarball_v3_5.sh
# 用途: 严格按白名单打包 yyzl-deploy tarball, 准备上传 GitHub 私库
# 版本: v3.5 (2026-05-08)
#
# 流程: 检查 → 干跑(列清单) → 确认 → 打包 → 验证 → 提示更新 install.sh
#
# 用法:
#   cd /root/yyzl-deploy/
#   bash pack_tarball_v3_5.sh                   # 干跑
#   bash pack_tarball_v3_5.sh --apply           # 真打包
#
# 安全提醒:
#   - tarball 含 共享配置.env 真实凭证 (API_HASH/OAuth/Sheet ID 等)
#   - 上传 GitHub 仓库必须是 Private
#   - 不要分享 tarball 文件给监察员之外的任何人
#   - 不要把仓库改成 Public

set -e

# ────────────────────────────────────────────────────────────────
# 颜色
# ────────────────────────────────────────────────────────────────
R='\033[31m'
G='\033[32m'
Y='\033[33m'
B='\033[34m'
BOLD='\033[1m'
END='\033[0m'

# ────────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────────
APPLY=0
[ "$1" == "--apply" ] && APPLY=1

DEPLOY_DIR="/root/yyzl-deploy"
TIMESTAMP=$(date +%Y%m%d_%H%M)
RANDOM_SUFFIX=$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')   # 8 位 hex
TAR_NAME="yyzl-deploy-v${TIMESTAMP}_${RANDOM_SUFFIX}.tar.gz"
TAR_PATH="/tmp/${TAR_NAME}"

# ────────────────────────────────────────────────────────────────
# 白名单 (严格, 任何不在这里的文件都不打)
# ────────────────────────────────────────────────────────────────
PYTHON_FILES=(
    "auth_reset.py"
    "bot.py"
    "center_router.py"
    "config.py"
    "daily_data_writer.py"
    "daily_report.py"
    "daily_sheet_sync.py"
    "dashboard_api.py"
    "database.py"
    "listener.py"
    "login.py"
    "main.py"
    "media_uploader.py"
    "oauth_helper.py"
    "sheets.py"
    "tasks.py"
    "templates.py"
    "update_checker.py"
    "upgrader.py"
    "web.py"
    "yyzl_filter.py"
)

SHELL_FILES=(
    "安装.sh"
    "install.sh"
    "rollback.sh"
    "update.sh"
    "enable_https.sh"
)

CONFIG_FILES=(
    "docker-compose.yml"
    "Dockerfile"
    "Caddyfile"
    "requirements.txt"
    "版本.txt"
    "共享配置.env"
)

TEMPLATE_FILES=(
    "templates/centers.html"
    "templates/chitchat.html"
    "templates/dashboard.html"
    "templates/index.html"
    "templates/login.html"
    "templates/sensitive_words.html"
    "templates/setup.html"
)

# 全部合并 (顺序不重要, 但去重时要看清楚)
ALL_FILES=("${PYTHON_FILES[@]}" "${SHELL_FILES[@]}" "${CONFIG_FILES[@]}" "${TEMPLATE_FILES[@]}")

# ────────────────────────────────────────────────────────────────
# Banner + 安全警告
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${B}=== pack_tarball_v3_5.sh 启动 ===${END}"
echo ""
echo -e "${Y}🔒 安全提醒:${END}"
echo "  - tarball 含 共享配置.env 真实凭证 (API_HASH/OAuth/Sheet ID 等)"
echo "  - 仅上传到 GitHub Private 仓库"
echo "  - 不要分享 tarball 给 4 个监察员之外的任何人"
echo ""

# ────────────────────────────────────────────────────────────────
# 1. 检查工作目录
# ────────────────────────────────────────────────────────────────
echo -e "${B}--- 1. 检查工作目录 ---${END}"
if [ "$(pwd)" != "$DEPLOY_DIR" ]; then
    echo -e "${R}❌ 必须在 $DEPLOY_DIR 下执行${END}"
    echo "   当前在: $(pwd)"
    echo "   请: cd $DEPLOY_DIR && bash pack_tarball_v3_5.sh"
    exit 1
fi
echo -e "  ${G}✓ 当前目录: $(pwd)${END}"

# ────────────────────────────────────────────────────────────────
# 2. 检查所有白名单文件存在
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}--- 2. 检查所有白名单文件存在 ---${END}"
MISSING=()
for f in "${ALL_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        MISSING+=("$f")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo -e "${R}❌ 缺少 ${#MISSING[@]} 个文件:${END}"
    for f in "${MISSING[@]}"; do
        echo -e "${R}    - $f${END}"
    done
    exit 1
fi
echo -e "  ${G}✓ 全部 ${#ALL_FILES[@]} 个白名单文件都在${END}"

# ────────────────────────────────────────────────────────────────
# 3. 黑名单检查 (不能进 tarball 的东西)
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}--- 3. 黑名单检查 (不该出现的文件) ---${END}"
for f in "${ALL_FILES[@]}"; do
    case "$f" in
        *.bak|*.session|*.log|.env|.env.before-*|*sessions*|*data/*|*__pycache__*|patch_*.py|*-deploy-v*.tar.gz)
            echo -e "${R}❌ 黑名单文件混进白名单了: $f${END}"
            exit 1
            ;;
    esac
done
echo -e "  ${G}✓ 白名单里没有 .bak / .env / sessions / patch_ / 旧 tarball${END}"

# ────────────────────────────────────────────────────────────────
# 4. 列出待打包文件清单
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}--- 4. 待打包清单 (${#ALL_FILES[@]} 个文件) ---${END}"
echo ""
echo -e "  ${BOLD}主程序 + 辅助 .py (${#PYTHON_FILES[@]} 个):${END}"
for f in "${PYTHON_FILES[@]}"; do
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf "    %-30s %8s bytes\n" "$f" "$SIZE"
done

echo ""
echo -e "  ${BOLD}Shell 脚本 (${#SHELL_FILES[@]} 个):${END}"
for f in "${SHELL_FILES[@]}"; do
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf "    %-30s %8s bytes\n" "$f" "$SIZE"
done

echo ""
echo -e "  ${BOLD}配置 (${#CONFIG_FILES[@]} 个):${END}"
for f in "${CONFIG_FILES[@]}"; do
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo "?")
    if [ "$f" == "共享配置.env" ]; then
        printf "    %-30s %8s bytes  ${Y}🔒 含敏感凭证${END}\n" "$f" "$SIZE"
    else
        printf "    %-30s %8s bytes\n" "$f" "$SIZE"
    fi
done

echo ""
echo -e "  ${BOLD}HTML 模板 (${#TEMPLATE_FILES[@]} 个):${END}"
for f in "${TEMPLATE_FILES[@]}"; do
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf "    %-30s %8s bytes\n" "$f" "$SIZE"
done

# 总大小
TOTAL_BYTES=0
for f in "${ALL_FILES[@]}"; do
    SIZE=$(stat -c%s "$f" 2>/dev/null || echo "0")
    TOTAL_BYTES=$((TOTAL_BYTES + SIZE))
done
TOTAL_KB=$((TOTAL_BYTES / 1024))
echo ""
echo -e "  ${BOLD}总大小 (未压缩): ${TOTAL_KB} KB${END}"

# ────────────────────────────────────────────────────────────────
# 5. 干跑模式: 不真打包, 显示要执行的 tar 命令然后退出
# ────────────────────────────────────────────────────────────────
if [ $APPLY -eq 0 ]; then
    echo ""
    echo -e "${Y}--- 5. 干跑模式 (没有真打包) ---${END}"
    echo ""
    echo -e "${BOLD}如果上面的清单都对, 跑下面这条命令真打包:${END}"
    echo ""
    echo -e "${G}  bash pack_tarball_v3_5.sh --apply${END}"
    echo ""
    echo "(这条命令会:"
    echo "  - 生成 tarball: ${TAR_NAME}"
    echo "  - 临时放在 /tmp/, 不留在 $DEPLOY_DIR"
    echo "  - 跑完显示新 TAR_NAME, 让你更新 install.sh)"
    echo ""
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# 6. 真打包
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}--- 5. 打包 ---${END}"
echo "  目标: ${TAR_PATH}"

# tar 命令: 用 -C 在当前目录, 直接列文件
# 不用 --exclude, 因为我们是白名单而非黑名单
tar czf "$TAR_PATH" "${ALL_FILES[@]}"

if [ ! -f "$TAR_PATH" ]; then
    echo -e "${R}❌ 打包失败${END}"
    exit 1
fi

TAR_SIZE=$(stat -c%s "$TAR_PATH")
TAR_KB=$((TAR_SIZE / 1024))
echo -e "  ${G}✓ 打包完成: ${TAR_KB} KB${END}"

# ────────────────────────────────────────────────────────────────
# 7. 验证 tarball
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}--- 6. 验证 tarball ---${END}"

# 7.1 文件数对得上
TAR_FILE_COUNT=$(tar tzf "$TAR_PATH" | grep -v '/$' | wc -l)
EXPECTED_COUNT=${#ALL_FILES[@]}
if [ "$TAR_FILE_COUNT" -eq "$EXPECTED_COUNT" ]; then
    echo -e "  ${G}✓ 文件数: $TAR_FILE_COUNT (= 期望 $EXPECTED_COUNT)${END}"
else
    echo -e "${R}  ✗ 文件数不对: 实际 $TAR_FILE_COUNT, 期望 $EXPECTED_COUNT${END}"
    exit 1
fi

# 7.2 黑名单文件不在 tarball 里
echo -n "  检查 tarball 里没有黑名单文件 ... "
BAD=$(tar tzf "$TAR_PATH" | grep -E '\.bak$|\.session$|^sessions/|^data/|__pycache__|^patch_.*\.py$|^\.env$' || true)
if [ -z "$BAD" ]; then
    echo -e "${G}✓${END}"
else
    echo -e "${R}✗${END}"
    echo -e "${R}    黑名单文件:${END}"
    echo "$BAD" | head -10
    rm -f "$TAR_PATH"
    exit 1
fi

# 7.3 关键文件 MD5 对比 (随机抽 3 个)
echo "  关键文件 MD5 对比:"
for f in "bot.py" "templates.py" "安装.sh"; do
    MD5_SRC=$(md5sum "$f" | cut -d' ' -f1)
    MD5_TAR=$(tar xzOf "$TAR_PATH" "$f" 2>/dev/null | md5sum | cut -d' ' -f1)
    if [ "$MD5_SRC" == "$MD5_TAR" ]; then
        echo -e "    ${G}✓ $f: $MD5_SRC${END}"
    else
        echo -e "${R}    ✗ $f: 源=$MD5_SRC tar=$MD5_TAR (不一致, 退出)${END}"
        rm -f "$TAR_PATH"
        exit 1
    fi
done

# 7.4 tar 完整性测试
echo -n "  tar 完整性测试 ... "
if tar tzf "$TAR_PATH" > /dev/null 2>&1; then
    echo -e "${G}✓${END}"
else
    echo -e "${R}✗${END}"
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# 8. 输出
# ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${G}=== ✅ tarball 打包成功 ===${END}"
echo ""
echo -e "${BOLD}文件信息:${END}"
echo "  路径:     ${TAR_PATH}"
echo "  大小:     ${TAR_KB} KB"
echo "  MD5:      $(md5sum $TAR_PATH | cut -d' ' -f1)"
echo "  TAR_NAME: ${TAR_NAME}"
echo ""
echo -e "${BOLD}下一步:${END}"
echo ""
echo -e "  ${BOLD}1. 更新 install.sh 第 8 行的 TAR_NAME:${END}"
echo "     原值: TAR_NAME=\"yyzl-deploy-v20260425_0603.tar.gz\""
echo -e "     新值: TAR_NAME=\"${G}${TAR_NAME}${END}\""
echo ""
echo "     可以跑这条 sed 命令自动改:"
echo -e "     ${G}sed -i 's|^TAR_NAME=.*|TAR_NAME=\"${TAR_NAME}\"|' install.sh${END}"
echo ""
echo "     验证:"
echo -e "     ${G}grep '^TAR_NAME=' install.sh${END}"
echo ""
echo -e "  ${BOLD}2. 上传到 GitHub Private 仓库 (yyzl-deploy):${END}"
echo "     - ${TAR_PATH}"
echo "     - install.sh (改了 TAR_NAME)"
echo ""
echo -e "  ${BOLD}3. 删旧 tarball (yyzl-deploy-v20260425_0603.tar.gz)${END}"
echo "     直接在 GitHub Web UI 上点垃圾桶图标"
echo ""
echo -e "${Y}🔒 安全提醒: 仓库必须是 Private, tarball 不要发给监察员之外的人${END}"
echo ""
