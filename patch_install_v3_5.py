#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_install_v3_5.py — 给 安装.sh 加 v3.5 会话吊销预警支持

3 处改动:
  1. 在生成 .env 之前，自动 detect 外网 IP (EXTERNAL_IP 变量)
  2. 在 { ... } > .env 块里加两行 echo:
       echo "VPS_LABEL=${SUPERVISOR_LABEL}"
       echo "EXTERNAL_IP=${EXTERNAL_IP}"
  3. 在部署完成总结里显示后台链接 (http://EXTERNAL_IP:WEB_PORT)

特性:
  - 幂等: 已升级过会跳过 (检查 EXTERNAL_IP detect 那段是否已存在)
  - 备份: 自动生成 安装.sh.before-v3-5-install.bak
  - 不依赖 sed/heredoc, 全用 Python str/re 处理, 强制 LF 行尾
  - 用法: 在 /root/yyzl-deploy/ 下跑 python3 patch_install_v3_5.py
"""

from __future__ import annotations
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 颜色
class C:
    R = "\033[31m"
    G = "\033[32m"
    Y = "\033[33m"
    B = "\033[34m"
    BOLD = "\033[1m"
    END = "\033[0m"

INSTALL_SH = Path("安装.sh")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def backup(p: Path) -> bool:
    bak = p.with_suffix(p.suffix + ".before-v3-5-install.bak")
    if bak.exists():
        print(f"{C.Y}  备份已存在, 跳过: {bak.name}{C.END}")
        return True
    try:
        shutil.copy2(p, bak)
        print(f"  ✓ 备份: {p.name} → {bak.name}")
        return True
    except Exception as e:
        print(f"{C.R}  ✗ 备份失败: {e}{C.END}")
        return False


def restore(p: Path):
    bak = p.with_suffix(p.suffix + ".before-v3-5-install.bak")
    if bak.exists():
        shutil.copy2(bak, p)
        print(f"  ↻ 已回滚: {p.name}")


def shell_syntax_check(p: Path) -> tuple[bool, str]:
    """bash -n 语法检查"""
    try:
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, ""
        return False, r.stderr.strip()
    except Exception as e:
        return False, f"无法执行 bash -n: {e}"


# ============================================================
# 改动 1: 加 EXTERNAL_IP detect 逻辑 (在 [6/7] echo 之后, { 之前)
# ============================================================
DETECT_BLOCK = '''
# v3.5: 自动 detect 外网 IP (用于会话吊销预警里的后台链接)
echo "  detect 外网 IP..."
EXTERNAL_IP=$(curl -s4 --max-time 5 ifconfig.me 2>/dev/null \\
            || curl -s4 --max-time 5 ip.sb 2>/dev/null \\
            || curl -s4 --max-time 5 api.ipify.org 2>/dev/null \\
            || echo "")
# 校验是否是合法 IPv4 (防止 detect 服务返回垃圾文本)
if ! echo "$EXTERNAL_IP" | grep -qE '^[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}$'; then
    EXTERNAL_IP=""
fi
if [ -n "$EXTERNAL_IP" ]; then
    echo "  OK 外网 IP: $EXTERNAL_IP"
else
    echo "  ! 未能自动 detect, 留空 (后续可在 .env 手动填 EXTERNAL_IP=...)"
fi

'''


def patch_step1_detect(content: str) -> str:
    """在 echo "[6/7] 生成 .env 配置文件..." 之后插入 detect IP 块"""
    # 锚点: echo "[6/7] 生成 .env 配置文件..." 这一行的结尾
    # 后面紧跟 { 那一行
    anchor_pat = re.compile(
        r'(echo\s+"\[6/7\]\s*生成\s*\.env\s*配置文件\.\.\."\s*\n)(\s*\{)',
        re.UNICODE,
    )
    m = anchor_pat.search(content)
    if not m:
        raise RuntimeError("找不到 [6/7] 生成 .env 锚点 (echo \"[6/7] ... { )")

    new_content = anchor_pat.sub(r'\1' + DETECT_BLOCK + r'\2', content, count=1)
    if new_content == content:
        raise RuntimeError("改动 1 没生效 (sub 替换失败)")
    return new_content


# ============================================================
# 改动 2: 在 { ... } > .env 块里加两行 echo
# ============================================================
def patch_step2_env_lines(content: str) -> str:
    """在 echo "INSTALL_DIR=$(pwd)" 之后加:
       echo "VPS_LABEL=${SUPERVISOR_LABEL}"
       echo "EXTERNAL_IP=${EXTERNAL_IP}"
    """
    anchor_pat = re.compile(
        r'(\s*)(echo\s+"INSTALL_DIR=\$\(pwd\)"\s*\n)',
        re.UNICODE,
    )
    m = anchor_pat.search(content)
    if not m:
        raise RuntimeError('找不到 echo "INSTALL_DIR=$(pwd)" 锚点')

    indent = m.group(1).split("\n")[-1] if "\n" in m.group(1) else "    "
    addition = (
        f'{indent}echo "VPS_LABEL=${{SUPERVISOR_LABEL}}"     # v3.5: 用于会话吊销预警显示\n'
        f'{indent}echo "EXTERNAL_IP=${{EXTERNAL_IP}}"        # v3.5: 用于预警里的后台链接\n'
    )
    new_content = anchor_pat.sub(r'\1\2' + addition, content, count=1)
    if new_content == content:
        raise RuntimeError("改动 2 没生效 (sub 替换失败)")
    return new_content


# ============================================================
# 改动 3: 在部署总结里加显示后台链接 (在 echo "  监察员:  $OPERATOR_NAME" 后)
# ============================================================
def patch_step3_summary(content: str) -> str:
    """在 echo "  监察员: $OPERATOR_NAME" 那一行之后加显示后台链接"""
    # 这条比较特殊: 中文 "监察员" + 多个空格 + $OPERATOR_NAME
    # 我们用宽松的正则匹配
    anchor_pat = re.compile(
        r'(echo\s+"\s*监察员[^"]*\$OPERATOR_NAME"\s*\n)',
        re.UNICODE,
    )
    m = anchor_pat.search(content)
    if not m:
        # 不致命, 跳过 (有些版本可能没这一行)
        print(f"{C.Y}  ! 改动 3 跳过: 找不到 监察员: $OPERATOR_NAME 锚点 (非致命){C.END}")
        return content

    addition = (
        '\n'
        '# v3.5: 显示后台访问链接 (会话吊销预警里也是这个链接)\n'
        'if [ -n "$EXTERNAL_IP" ]; then\n'
        '    echo "  后台链接:  http://${EXTERNAL_IP}:${WEB_PORT:-8000}"\n'
        '    echo "  Web 密码:  ${WEB_PASSWORD}"\n'
        'else\n'
        '    echo "  ! 外网 IP 未 detect, 后台链接需手动填 EXTERNAL_IP 后再查看"\n'
        'fi\n'
    )
    new_content = anchor_pat.sub(r'\1' + addition, content, count=1)
    if new_content == content:
        raise RuntimeError("改动 3 没生效 (sub 替换失败)")
    return new_content


# ============================================================
# 主流程
# ============================================================
def main():
    print(f"\n{C.BOLD}{C.B}=== patch_install_v3_5.py 启动 ==={C.END}\n")

    # 1. 检查文件存在
    if not INSTALL_SH.exists():
        print(f"{C.R}❌ 找不到 安装.sh: {INSTALL_SH.absolute()}{C.END}")
        print(f"{C.R}   请在 /root/yyzl-deploy/ 下执行此脚本{C.END}")
        sys.exit(1)

    print(f"{C.B}--- 1. 文件 MD5 (改前) ---{C.END}")
    md5_before = md5(INSTALL_SH)
    size_before = INSTALL_SH.stat().st_size
    print(f"  MD5:  {md5_before}")
    print(f"  Size: {size_before} bytes")

    # 2. 读取
    print(f"\n{C.B}--- 2. 读取文件 ---{C.END}")
    content = INSTALL_SH.read_text(encoding="utf-8")
    print(f"  字符数: {len(content)}")
    print(f"  行数:   {content.count(chr(10))}")

    # 3. 幂等性检查
    print(f"\n{C.B}--- 3. 幂等性检查 ---{C.END}")
    if "v3.5: 自动 detect 外网 IP" in content:
        print(f"{C.Y}  ! 已升级过 v3.5, 跳过 (检测到 detect 外网 IP 标记){C.END}")
        sys.exit(0)
    print(f"  {C.G}✓ 未升级过, 继续{C.END}")

    # 4. 干跑生成新内容
    print(f"\n{C.B}--- 4. 干跑生成新内容 ---{C.END}")
    try:
        new_content = patch_step1_detect(content)
        print(f"{C.G}  ✓ 改动 1: 加 EXTERNAL_IP detect 逻辑{C.END}")

        new_content = patch_step2_env_lines(new_content)
        print(f"{C.G}  ✓ 改动 2: .env 块加 VPS_LABEL + EXTERNAL_IP{C.END}")

        new_content = patch_step3_summary(new_content)
        if "后台链接:" in new_content:
            print(f"{C.G}  ✓ 改动 3: 部署总结加后台链接{C.END}")
    except Exception as e:
        print(f"\n{C.R}❌ 干跑失败: {e}{C.END}")
        sys.exit(1)

    # 5. diff 摘要
    diff = len(new_content) - len(content)
    print(f"\n{C.B}--- 5. 改动摘要 ---{C.END}")
    print(f"  字符数: {len(content)} → {len(new_content)} ({C.G}+{diff}{C.END})")

    # 6. 用户确认
    print(f"\n{C.BOLD}{C.Y}--- 6. 确认 apply? ---{C.END}")
    print("将执行:")
    print("  1) 备份 安装.sh.before-v3-5-install.bak")
    print("  2) 写入新内容 (强制 LF 行尾)")
    print("  3) bash -n 语法检查")
    print("  4) 显示新 MD5")
    print("\n输入 yes 继续, 其他键退出: ", end="", flush=True)
    ans = input().strip().lower()
    if ans != "yes":
        print(f"{C.Y}已取消, 文件未改{C.END}")
        sys.exit(0)

    # 7. 备份
    print(f"\n{C.B}--- 7. 备份 ---{C.END}")
    if not backup(INSTALL_SH):
        sys.exit(1)

    # 8. 写入 (强制 LF 行尾, UTF-8)
    print(f"\n{C.B}--- 8. 写入 ---{C.END}")
    INSTALL_SH.write_text(new_content, encoding="utf-8", newline="\n")
    print(f"  ✓ {INSTALL_SH.name} 写入完成")

    # 9. bash 语法检查
    print(f"\n{C.B}--- 9. bash -n 语法检查 ---{C.END}")
    ok, err = shell_syntax_check(INSTALL_SH)
    if ok:
        print(f"{C.G}  ✓ 语法检查通过{C.END}")
    else:
        print(f"{C.R}  ✗ 语法错误:\n{err}{C.END}")
        print(f"\n{C.R}❌ 自动回滚{C.END}")
        restore(INSTALL_SH)
        sys.exit(1)

    # 10. 新 MD5
    print(f"\n{C.B}--- 10. 文件 MD5 (改后) ---{C.END}")
    md5_after = md5(INSTALL_SH)
    size_after = INSTALL_SH.stat().st_size
    print(f"  MD5:  {md5_after}")
    print(f"  Size: {size_after} bytes")

    print(f"\n{C.BOLD}{C.G}=== ✅ patch 全部应用成功! ==={C.END}\n")
    print("下一步:")
    print("  1. 验证关键改动: grep -n 'EXTERNAL_IP\\|VPS_LABEL\\|后台链接' 安装.sh")
    print("  2. 重新打包 tarball: tar czf yyzl-deploy-v$(date +%Y%m%d_%H%M).tar.gz <文件清单>")
    print("  3. 更新 install.sh 里的 TAR_NAME 变量")
    print("  4. 上传 tarball 和 install.sh 到 GitHub")
    print()


if __name__ == "__main__":
    main()
