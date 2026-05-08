#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_session_alert.py — 会话吊销预警升级 (v3.5 安全)

改动 4 个文件:
  1. config.py     — 加 VPS_LABEL/OPERATOR_NAME/WEB_PORT/EXTERNAL_IP 4 个全局变量 + reload 同步
  2. templates.py  — session_revoked_alert + session_restored_alert 加 3 参数
  3. bot.py        — 重写 send_session_alert: 中心路由 + 兜底全推 + 去重发送
  4. .env          — 加 EXTERNAL_IP 字段(默认空)

修复痛点:
  - 原代码依赖 ALERT_GROUP_ID, 但 .env 没配该字段 → 当前会话吊销预警根本不推送
  - 模板没有 VPS 标识/监察员/web 后台链接, 监察员收到也不知道是哪台 VPS
  - 4 监察员部署后, 没有按账号归属中心路由

执行流程:
  - 自动备份 4 个文件 (.before-session-alert.bak)
  - 干跑显示所有改动 + 让用户确认
  - apply 后 MD5 + 语法检查 + diff 验证
  - 失败自动回滚

放在 /root/yyzl-deploy/ 跑: python3 patch_session_alert.py
"""

import os
import sys
import re
import shutil
import hashlib
import subprocess
from pathlib import Path

# === 配置 ===
ROOT = Path(__file__).parent.resolve()
BAK_SUFFIX = ".before-session-alert.bak"

FILES = {
    "config":    ROOT / "config.py",
    "templates": ROOT / "templates.py",
    "bot":       ROOT / "bot.py",
    "env":       ROOT / ".env",
}

# === 颜色 ===
class C:
    R = "\033[31m"; G = "\033[32m"; Y = "\033[33m"; B = "\033[34m"
    BOLD = "\033[1m"; END = "\033[0m"


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def backup(path):
    if not path.exists():
        print(f"{C.R}❌ 文件不存在: {path}{C.END}")
        return False
    bak = path.with_suffix(path.suffix + BAK_SUFFIX)
    if bak.exists():
        print(f"{C.Y}⚠️  备份已存在跳过: {bak.name}{C.END}")
    else:
        shutil.copy2(path, bak)
        print(f"{C.G}✓ 备份: {path.name} → {bak.name}{C.END}")
    return True


def restore(path):
    bak = path.with_suffix(path.suffix + BAK_SUFFIX)
    if bak.exists():
        shutil.copy2(bak, path)
        print(f"{C.Y}↩  回滚: {path.name}{C.END}")


def py_syntax_check(path):
    """语法检查 .py 文件"""
    if not path.suffix == ".py":
        return True, ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr
    except Exception as e:
        return False, str(e)


# ============================================================
# 改动 1: config.py
# ============================================================
def patch_config(content):
    """
    在 ALERT_GROUP_ID = ... 那一行后面加 4 个全局变量
    然后修改 reload_if_env_changed 函数: global 列表 + 末尾刷新逻辑
    """

    # --- step 1: 加 4 个全局变量 (在 ALERT_GROUP_ID 那块下面) ---
    anchor1 = 'ALERT_GROUP_ID = int(_group_id) if _group_id else 0'
    if anchor1 not in content:
        raise RuntimeError(f"找不到锚点 1 (ALERT_GROUP_ID 定义)")

    insertion1 = (
        '\n\n# v3.5: 会话吊销预警/部署标识 — install.sh 写入,运行时可热刷\n'
        'VPS_LABEL = os.environ.get("VPS_LABEL", "").strip()\n'
        'OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "").strip()\n'
        'WEB_PORT = os.environ.get("WEB_PORT", "8000").strip()\n'
        'EXTERNAL_IP = os.environ.get("EXTERNAL_IP", "").strip()'
    )

    # 防重复
    if 'VPS_LABEL = os.environ.get("VPS_LABEL"' in content:
        print(f"{C.Y}  config.py: 4 全局变量已存在,跳过加变量步骤{C.END}")
    else:
        content = content.replace(anchor1, anchor1 + insertion1, 1)
        print(f"{C.G}  ✓ config.py: 加 4 全局变量 (VPS_LABEL/OPERATOR_NAME/WEB_PORT/EXTERNAL_IP){C.END}")

    # --- step 2: reload_if_env_changed 加 global 声明 ---
    # 原: global KEYWORDS, NO_REPLY_MINUTES, PEER_ROLE_LABEL, OPERATOR_LABEL, COMPANY_DISPLAY
    #     global CHITCHAT_WHITELIST
    # 新: 上面 + global VPS_LABEL, OPERATOR_NAME, WEB_PORT, EXTERNAL_IP
    anchor2 = 'global CHITCHAT_WHITELIST'
    if anchor2 not in content:
        raise RuntimeError(f"找不到锚点 2 (reload 函数 global CHITCHAT_WHITELIST)")

    insertion2 = (
        '\n    global VPS_LABEL, OPERATOR_NAME, WEB_PORT, EXTERNAL_IP'
    )

    if 'global VPS_LABEL, OPERATOR_NAME, WEB_PORT, EXTERNAL_IP' in content:
        print(f"{C.Y}  config.py: reload global 列表已包含,跳过{C.END}")
    else:
        content = content.replace(anchor2, anchor2 + insertion2, 1)
        print(f"{C.G}  ✓ config.py: reload global 列表加 4 变量{C.END}")

    # --- step 3: reload 函数末尾加刷新逻辑 ---
    # 找一个稳定的锚点: 'load_dotenv(_ENV_PATH, override=True)'
    # 之后是: ALERTS_ENABLED = os.environ.get(...)
    # 我在最稳定的位置插入: 在 ALERT_DELETE_ENABLED 这行之后
    anchor3 = 'ALERT_DELETE_ENABLED = _resolve_subswitch("ALERT_DELETE_ENABLED", ALERTS_ENABLED)'
    if anchor3 not in content:
        raise RuntimeError(f"找不到锚点 3 (reload 函数 ALERT_DELETE_ENABLED 行)")

    insertion3 = (
        '\n    # v3.5: 部署标识热刷\n'
        '    VPS_LABEL = os.environ.get("VPS_LABEL", "").strip()\n'
        '    OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "").strip()\n'
        '    WEB_PORT = os.environ.get("WEB_PORT", "8000").strip()\n'
        '    EXTERNAL_IP = os.environ.get("EXTERNAL_IP", "").strip()'
    )

    if 'VPS_LABEL = os.environ.get("VPS_LABEL", "").strip()\n    OPERATOR_NAME = os.environ.get' in content:
        print(f"{C.Y}  config.py: reload 末尾刷新逻辑已存在,跳过{C.END}")
    else:
        content = content.replace(anchor3, anchor3 + insertion3, 1)
        print(f"{C.G}  ✓ config.py: reload 末尾加 4 变量刷新{C.END}")

    return content


# ============================================================
# 改动 2: templates.py
# ============================================================
def patch_templates(content):
    """整段替换 session_revoked_alert + session_restored_alert
    用正则匹配函数签名 + 函数体, 容错中英文标点差异
    """

    # 已升级标记
    if re.search(r'def session_revoked_alert\(phone, account_name, vps_label=', content):
        print(f"{C.Y}  templates.py: 已升级过,跳过{C.END}")
        return content

    new_revoked = '''def session_revoked_alert(phone, account_name, vps_label="", operator_name="", web_url=""):
    """v3.5: 会话吊销预警 - 加 VPS 标识/监察员/后台链接"""
    return (
        f"🚨 【TG 会话被吊销 / 可能被盗】\\n\\n"
        f"📍 VPS: {vps_label or '未命名'}\\n"
        f"👤 监察员: {operator_name or '未指定'}\\n\\n"
        f"外事号: {account_name}\\n"
        f"手机: {phone}\\n\\n"
        f"⚠️ 该账号 session 已失效, 可能原因:\\n"
        f"  • 账号主人/攻击者撤销了本设备 session\\n"
        f"  • TG 官方风控\\n"
        f"  • 账号被注销\\n\\n"
        f"🔧 处理步骤:\\n"
        f"  1. 打开后台: {web_url or '请咨询管理员获取后台地址'}\\n"
        f"  2. 账号管理 → 找到该账号 → 重新登录\\n"
        f"  3. 登录成功会自动推「✅ 会话已恢复」"
    )'''

    new_restored = '''def session_restored_alert(phone, account_name, vps_label="", operator_name="", web_url=""):
    """v3.5: 会话恢复预警 - 加 VPS 标识/监察员"""
    return (
        f"✅ 【TG 会话已恢复】\\n\\n"
        f"📍 VPS: {vps_label or '未命名'}\\n"
        f"👤 监察员: {operator_name or '未指定'}\\n\\n"
        f"外事号: {account_name}\\n"
        f"手机: {phone}\\n\\n"
        f"账号已重新登录成功, 监控继续运行。"
    )'''

    # 正则: 匹配 def session_revoked_alert(phone, account_name): 函数 (从 def 到 return ( 配对的 ) 之后)
    # 用 lookahead 找下一个 def 或文件末尾,不依赖标点细节
    pat_revoked = re.compile(
        r'def session_revoked_alert\(phone,\s*account_name\):.*?\n\s*\)\n',
        re.DOTALL
    )
    pat_restored = re.compile(
        r'def session_restored_alert\(phone,\s*account_name\):.*?\n\s*\)\n',
        re.DOTALL
    )

    m1 = pat_revoked.search(content)
    if not m1:
        raise RuntimeError("templates.py 找不到 session_revoked_alert(phone, account_name) 的函数定义")

    m2 = pat_restored.search(content)
    if not m2:
        raise RuntimeError("templates.py 找不到 session_restored_alert(phone, account_name) 的函数定义")

    # 替换两个函数 (从后往前换避免位置偏移)
    content = content[:m2.start()] + new_restored + '\n' + content[m2.end():]
    content = content[:m1.start()] + new_revoked + '\n' + content[m1.end():]

    print(f"{C.G}  ✓ templates.py: 升级 session_revoked_alert + session_restored_alert{C.END}")
    return content


# ============================================================
# 改动 3: bot.py
# ============================================================
def patch_bot(content):
    """整段替换 send_session_alert 函数"""

    # 老函数完整代码 (从 def 到下一个 def 之前)
    # 用更稳的方式: 从 def send_session_alert 抓到 def send_update_notice 之前
    # 已升级标记: 含 _get_web_host (老版没有这个辅助方法)
    if 'def _get_web_host' in content and 'get_center_groups' in content:
        print(f"{C.Y}  bot.py: 已升级过,跳过{C.END}")
        return content

    pattern = re.compile(
        r'    async def send_session_alert\(self.*?\n'
        r'(?:.*?\n)*?'
        r'(?=    async def send_update_notice)',
        re.DOTALL
    )
    m = pattern.search(content)
    if not m:
        raise RuntimeError("bot.py 找不到 send_session_alert ~ send_update_notice 之间的代码段")

    new_func = '''    async def send_session_alert(self, kind: str, phone: str, account_id: int = 0, account_name: str = ""):
        """v3.5 升级: 推送 session 吊销/恢复预警 — 中心路由 + 全推兜底
        - 旧版依赖 ALERT_GROUP_ID,但 .env 没配该字段 → 永远不推送
        - 新版按账号归属中心走 ALERT_GROUP_<XXXX> 怠工群; 账号未归属时 fallback 推所有 4 个怠工群
        - 模板加 VPS_LABEL/OPERATOR_NAME/web_url, 让监察员一眼看出哪台 VPS / 怎么处理
        """
        if not self.bot:
            logger.warning("[session_%s] bot 未配置,跳过 phone=%s", kind, phone)
            return
        try:
            config.reload_if_env_changed()
            # 1. 取 VPS 标识 + Web 后台链接
            vps_label = (config.VPS_LABEL or "未命名 VPS").strip()
            operator = (config.OPERATOR_NAME or "未指定").strip()
            web_host = self._get_web_host()
            web_url = f"http://{web_host}:{config.WEB_PORT}"
            # 2. 渲染模板
            if kind == "revoked":
                msg = templates.session_revoked_alert(phone, account_name, vps_label, operator, web_url)
                alert_type = "session_revoked"
            elif kind == "restored":
                msg = templates.session_restored_alert(phone, account_name, vps_label, operator, web_url)
                alert_type = "session_restored"
            else:
                logger.warning("[session_alert] 未知 kind=%s", kind)
                return
            # 3. DB 写表 (审计留痕,不受任何开关影响)
            try:
                if account_id:
                    db.insert_alert(alert_type, account_id, peer_id=None,
                                    message_text=f"[{phone}] {account_name}")
            except Exception as e:
                logger.warning("[session_%s] insert_alert 失败: %s", kind, e)
            # 4. 决定推送目标 — 优先按账号归属中心路由
            target_groups = []
            if account_id:
                try:
                    row = db.get_conn().execute(
                        "SELECT center FROM accounts WHERE id=?", (account_id,)
                    ).fetchone()
                    center_zh = ""
                    if row:
                        center_zh = (row["center"] or "").strip()
                    if center_zh:
                        from center_router import get_center_groups
                        groups_map = get_center_groups(kind="no_reply")
                        gid = groups_map.get(center_zh, 0)
                        if gid:
                            target_groups.append(gid)
                            logger.info("[session_%s] 账号「%s」归属「%s」 → 怠工群 %s",
                                        kind, account_name, center_zh, gid)
                        else:
                            logger.warning("[session_%s] 账号「%s」归属「%s」, 但该中心怠工群未配",
                                           kind, account_name, center_zh)
                    else:
                        logger.info("[session_%s] 账号「%s」未归属任何中心", kind, account_name)
                except Exception as e:
                    logger.warning("[session_%s] 查归属中心失败: %s", kind, e)
            # 5. 兜底:推所有怠工群 (账号未归属/中心未配 时)
            if not target_groups:
                try:
                    from center_router import get_center_groups
                    target_groups = [gid for gid in get_center_groups(kind="no_reply").values() if gid]
                    if target_groups:
                        logger.info("[session_%s] 兜底:推所有 %d 个怠工群", kind, len(target_groups))
                except Exception as e:
                    logger.error("[session_%s] 取怠工群列表失败: %s", kind, e)
            if not target_groups:
                logger.warning("[session_%s] 无任何怠工群可推, 跳过 phone=%s", kind, phone)
                return
            # 6. 真正推送 (去重)
            sent = set()
            ok = 0
            for gid in target_groups:
                if gid in sent:
                    continue
                sent.add(gid)
                try:
                    await self.bot.send_message(gid, msg)
                    ok += 1
                except Exception as e:
                    logger.error("[session_%s] 推 group=%s 失败: %s", kind, gid, e)
            logger.info("[session_%s] 完成 phone=%s, 推 %d/%d 个群", kind, phone, ok, len(sent))
        except Exception as e:
            logger.error("[session_%s] 总失败 phone=%s: %s", kind, phone, e)

    def _get_web_host(self):
        """取 Web 后台主机名/IP — 优先 .env 配的 EXTERNAL_IP, 没配则尝试 detect"""
        if config.EXTERNAL_IP:
            return config.EXTERNAL_IP
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            # 容器内多半是 172.x.x.x 私网,实际不可达,但聊胜于无 (用户可改 .env 修)
            return ip
        except Exception:
            return "your-vps-ip"

'''

    content = pattern.sub(new_func, content, count=1)
    print(f"{C.G}  ✓ bot.py: 重写 send_session_alert + 加 _get_web_host 辅助方法{C.END}")
    return content


# ============================================================
# 改动 4: .env
# ============================================================
def patch_env(content):
    """加 EXTERNAL_IP 字段 (默认空,VPS_LABEL 那块附近)"""
    if re.search(r'^EXTERNAL_IP=', content, re.M):
        print(f"{C.Y}  .env: EXTERNAL_IP 已存在,跳过{C.END}")
        return content

    # 加在 VPS_LABEL=麦小麦1 后面
    anchor = 'VPS_LABEL=麦小麦1'
    if anchor in content:
        addition = '\n# v3.5: 外网 IP — 用于会话吊销预警里的后台链接 (留空则自动 detect)\nEXTERNAL_IP='
        content = content.replace(anchor, anchor + addition, 1)
        print(f"{C.G}  ✓ .env: 加 EXTERNAL_IP 字段{C.END}")
    else:
        # 兜底: 加在文件末尾
        content = content.rstrip() + '\n\n# v3.5: 外网 IP — 用于会话吊销预警里的后台链接 (留空则自动 detect)\nEXTERNAL_IP=\n'
        print(f"{C.G}  ✓ .env: 末尾加 EXTERNAL_IP 字段 (找不到 VPS_LABEL 锚点){C.END}")
    return content


# ============================================================
# 主流程
# ============================================================
def main():
    print(f"\n{C.BOLD}{C.B}=== patch_session_alert.py 启动 ==={C.END}\n")

    # --- 1. 检查所有目标文件存在 ---
    for k, p in FILES.items():
        if not p.exists():
            print(f"{C.R}❌ {k} 文件不存在: {p}{C.END}")
            sys.exit(1)

    print(f"{C.B}--- 1. 文件 MD5 (改前) ---{C.END}")
    md5_before = {k: md5(p) for k, p in FILES.items()}
    for k, h in md5_before.items():
        print(f"  {k:10s}: {h}  ({FILES[k].name})")

    # --- 2. 读取所有文件 ---
    print(f"\n{C.B}--- 2. 读取文件 ---{C.END}")
    originals = {}
    for k, p in FILES.items():
        originals[k] = p.read_text(encoding="utf-8")
        print(f"  {k:10s}: {len(originals[k])} 字符")

    # --- 3. 干跑生成新内容 ---
    print(f"\n{C.B}--- 3. 干跑生成新内容 ---{C.END}")
    try:
        new_contents = {
            "config":    patch_config(originals["config"]),
            "templates": patch_templates(originals["templates"]),
            "bot":       patch_bot(originals["bot"]),
            "env":       patch_env(originals["env"]),
        }
    except Exception as e:
        print(f"\n{C.R}❌ 干跑失败: {e}{C.END}")
        sys.exit(1)

    # --- 4. 显示 diff 摘要 ---
    print(f"\n{C.B}--- 4. 改动摘要 (字符数差异) ---{C.END}")
    for k in FILES:
        d = len(new_contents[k]) - len(originals[k])
        sign = "+" if d >= 0 else ""
        color = C.G if d > 0 else (C.Y if d == 0 else C.R)
        print(f"  {k:10s}: {color}{sign}{d}{C.END} 字符 ({len(originals[k])} → {len(new_contents[k])})")

    # --- 5. 用户确认 ---
    print(f"\n{C.BOLD}{C.Y}--- 5. 确认 apply? ---{C.END}")
    print("将执行:")
    print("  1) 备份 4 个文件 (.before-session-alert.bak)")
    print("  2) 写入新内容")
    print("  3) py 语法检查 (config/templates/bot)")
    print("  4) 显示新 MD5")
    print("\n输入 yes 继续,其他键退出: ", end="", flush=True)
    ans = input().strip().lower()
    if ans != "yes":
        print(f"{C.Y}已取消, 文件未改{C.END}")
        sys.exit(0)

    # --- 6. 备份 ---
    print(f"\n{C.B}--- 6. 备份 ---{C.END}")
    for k, p in FILES.items():
        if not backup(p):
            sys.exit(1)

    # --- 7. 写入新内容 ---
    print(f"\n{C.B}--- 7. 写入 ---{C.END}")
    for k, p in FILES.items():
        p.write_text(new_contents[k], encoding="utf-8")
        print(f"  ✓ {p.name}")

    # --- 8. 语法检查 ---
    print(f"\n{C.B}--- 8. py 语法检查 ---{C.END}")
    fail = False
    for k in ("config", "templates", "bot"):
        ok, err = py_syntax_check(FILES[k])
        if ok:
            print(f"{C.G}  ✓ {FILES[k].name}{C.END}")
        else:
            print(f"{C.R}  ✗ {FILES[k].name}:\n{err}{C.END}")
            fail = True

    if fail:
        print(f"\n{C.R}❌ 语法检查失败, 自动回滚{C.END}")
        for p in FILES.values():
            restore(p)
        sys.exit(1)

    # --- 9. 改后 MD5 ---
    print(f"\n{C.B}--- 9. 文件 MD5 (改后) ---{C.END}")
    for k, p in FILES.items():
        h = md5(p)
        marker = C.G + "✓" if h != md5_before[k] else C.Y + "·"
        print(f"  {marker} {k:10s}: {h}  ({p.name}){C.END}")

    print(f"\n{C.BOLD}{C.G}=== ✅ patch 全部应用成功! ==={C.END}\n")
    print(f"{C.B}下一步:{C.END}")
    print(f"  1. {C.BOLD}docker compose restart tg-monitor{C.END}  # 重启容器加载新代码")
    print(f"  2. 看日志: {C.BOLD}docker logs -f tg-monitor-yyzl{C.END}")
    print(f"  3. 触发测试: TG 网页端 → 设置 → 已活动会话 → 撤销 VPS 这台,看预警是否推到怠工群\n")


if __name__ == "__main__":
    main()
