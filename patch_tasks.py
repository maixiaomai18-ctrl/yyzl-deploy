#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YYZL tasks.py v4.8 闲聊判定升级补丁

用法 (在 VPS 上):
    cd /root/yyzl-deploy
    cp tasks.py tasks.py.before-v4-8.bak
    python3 patch_tasks.py
    diff tasks.py.before-v4-8.bak tasks.py | head -80

它做 3 件事:
1. 替换 _is_chitchat 函数 (升级判定规则)
2. 新增 _all_unreplied_are_chitchat 函数
3. 改调用处 (line ~286)
"""

import re
import sys
import os

TASKS_PATH = "/root/yyzl-deploy/tasks.py"

NEW_IS_CHITCHAT = '''def _is_chitchat(text: str) -> bool:
    """
    v4.8 启发式闲聊判定:
    满足任一即闲聊:
      1. 空文本
      2. 文本短 (<= 5 字)
      3. 纯 emoji/标点 (无中英文)
      4. 文本短 (<= 12 字) 且不含实质信号:
         - 没有问号 (? ？)
         - 没有数字
         - 没有 @ # http
         - 没有问询关键词
      5. 完全匹配兜底白名单
    """
    import re as _re
    if not text:
        return True
    s = text.strip()
    # 规则 2: 太短
    if len(s) <= 5:
        return True
    # 规则 3: 纯 emoji/标点
    if not _re.search(r"[\\u4e00-\\u9fa5a-zA-Z]", s):
        return True
    # 规则 4: 短文本(<=12 字) 且无实质信号
    if len(s) <= 12:
        has_question = bool(_re.search(r"[?？]", s))
        has_digit = bool(_re.search(r"\\d", s))
        has_special = bool(_re.search(r"[@#]|http", s))
        question_keywords = ['多少', '价格', '怎么', '哪', '什么',
                             '能不能', '可不可以', '请问', '怎样',
                             '吗', '呢', '能否']
        has_question_word = any(kw in s for kw in question_keywords)
        if not (has_question or has_digit or has_special or has_question_word):
            return True
    # 规则 5: 兜底白名单
    chitchat = {
        "你好", "您好", "嗨", "hi", "Hi", "HI", "hello", "Hello",
        "嗯嗯", "嗯", "好的", "好滴", "好哒", "收到", "收到了",
        "哦", "噢", "哈哈", "哈哈哈", "呵呵", "哎",
        "ok", "OK", "Ok", "okay", "Okay",
        "在吗", "在不在", "你在吗", "您在吗",
        "?", "？", "??", "？？", "???", "？？？",
    }
    if s in chitchat:
        return True
    return False


def _all_unreplied_are_chitchat(peer_id, account_id, db):
    """
    v4.8: 查 peer 上从最后一条 A 消息之后所有 B 消息.
    全部都是闲聊 -> True (豁免告警)
    至少一条不是闲聊 -> False (要告警)
    """
    try:
        conn = db.get_conn()
        # 找最后一条 A 消息时间
        last_a = conn.execute("""
            SELECT timestamp FROM messages
            WHERE peer_id=? AND account_id=?
                  AND direction='A' AND deleted=0
            ORDER BY timestamp DESC LIMIT 1
        """, (peer_id, account_id)).fetchone()
        last_a_ts = last_a[0] if last_a else "1970-01-01 00:00:00"
        # 拿这之后所有 B 消息
        unreplied = conn.execute("""
            SELECT text FROM messages
            WHERE peer_id=? AND account_id=?
                  AND direction='B' AND deleted=0
                  AND timestamp > ?
            ORDER BY timestamp ASC
        """, (peer_id, account_id, last_a_ts)).fetchall()
        if not unreplied:
            # 没找到未回消息 (理论上不应该发生, 安全起见豁免)
            return True
        return all(_is_chitchat((row[0] or "")) for row in unreplied)
    except Exception:
        # 任何异常都返回 False (= 不豁免, 按现有逻辑告警, 安全侧)
        return False'''


def main():
    if not os.path.exists(TASKS_PATH):
        print(f"ERROR: {TASKS_PATH} not found")
        return 1

    with open(TASKS_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    original_md5_lines = content.count("\n")
    print(f"[1/4] Read tasks.py: {len(content)} bytes, {original_md5_lines} lines")

    # ===== 替换 1: _is_chitchat 函数 =====
    # 匹配从 'def _is_chitchat' 开始, 到下一个顶层 def/class 之前
    pattern_is_chitchat = re.compile(
        r"^def _is_chitchat\(text: str\) -> bool:\n"
        r"(?:    .*\n|\n)+?"  # 函数体 (缩进的行 + 空行)
        r"(?=^def |^class |^[A-Z_]|^# )",  # 直到下一个顶层定义或注释
        re.MULTILINE,
    )
    m = pattern_is_chitchat.search(content)
    if not m:
        print("ERROR: failed to find _is_chitchat function")
        return 1

    print(f"[2/4] Found _is_chitchat at offset {m.start()}, length {m.end() - m.start()}")
    content = content[: m.start()] + NEW_IS_CHITCHAT + "\n\n\n" + content[m.end() :]
    print(f"      Replaced with new _is_chitchat + _all_unreplied_are_chitchat")

    # ===== 替换 2: 调用处 =====
    old_call = 'if _is_chitchat(dict(row).get("last_text", "") or ""):  # v4.7: 闲聊不触发怠工预警'
    new_call = 'if _all_unreplied_are_chitchat(row["id"], account["id"], db):  # v4.8: 全部 B 消息都是闲聊才豁免'
    if old_call in content:
        content = content.replace(old_call, new_call)
        print(f"[3/4] Replaced caller (v4.7 -> v4.8)")
    else:
        print("WARN: caller pattern not found, manual check needed")
        print("Looking for any '_is_chitchat(' in code...")
        for i, line in enumerate(content.split("\n"), 1):
            if "_is_chitchat(" in line and "def " not in line:
                print(f"  line {i}: {line.strip()}")

    # ===== 写回 =====
    new_lines = content.count("\n")
    print(f"[4/4] Will write back: {len(content)} bytes, {new_lines} lines (delta {new_lines - original_md5_lines:+d})")

    with open(TASKS_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK: patched tasks.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
