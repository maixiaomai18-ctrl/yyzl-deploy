#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4.9 patch_bot.py - 去掉怠工/删除预警群里的「通过/拒绝」按钮
策略：精确锚点匹配 + 多重校验，绝不模糊替换
用法：
    python3 patch_bot.py            # 干跑（dry-run），打印改动但不写文件
    python3 patch_bot.py --apply    # 实际写入 bot.py.v4-9-new
"""
import sys
import re
import hashlib
from pathlib import Path

SRC = Path("bot.py")
DST = Path("bot.py.v4-9-new")
APPLY = "--apply" in sys.argv

if not SRC.exists():
    print(f"❌ 找不到 {SRC}")
    sys.exit(1)

original = SRC.read_text(encoding="utf-8")
text = original

# 改动统计
changes = []

# ============================================================
# 改动 A+B：删除 callback_query 处理器 + _make_keyboard 函数
# 锚点：从 "@self.dp.callback_query(F.data.startswith(\"approve:\")"
#       到 "    def _write_alert_to_sheet(self, alert):" 之前的空行
# ============================================================
PATTERN_AB = re.compile(
    r'        @self\.dp\.callback_query\(F\.data\.startswith\("approve:"\) \| F\.data\.startswith\("reject:"\)\).*?'
    r'(?=    def _write_alert_to_sheet\(self, alert\):)',
    re.DOTALL
)
m = PATTERN_AB.search(text)
if not m:
    print("❌ [A+B] 找不到 callback_query 处理器锚点")
    sys.exit(1)
deleted_ab = m.group(0)
text = text[:m.start()] + text[m.end():]
changes.append(f"[A+B] 删除 callback_query 处理器 + _make_keyboard 函数 ({len(deleted_ab.splitlines())} 行)")

# ============================================================
# 改动 C：删除 _write_alert_to_sheet 函数
# 锚点：从 "    def _write_alert_to_sheet(self, alert):"
#       到 "    async def send_keyword_alert"
# ============================================================
PATTERN_C = re.compile(
    r'    def _write_alert_to_sheet\(self, alert\):.*?'
    r'(?=    async def send_keyword_alert)',
    re.DOTALL
)
m = PATTERN_C.search(text)
if not m:
    print("❌ [C] 找不到 _write_alert_to_sheet 函数锚点")
    sys.exit(1)
deleted_c = m.group(0)
text = text[:m.start()] + text[m.end():]
changes.append(f"[C] 删除 _write_alert_to_sheet 函数 ({len(deleted_c.splitlines())} 行)")

# ============================================================
# 改动 D + E：删除两处 reply_markup=self._make_keyboard(alert_id), 行
# 这两处长得一模一样，预期能匹配到 2 处
# ============================================================
RM_LINE = re.compile(
    r'^                reply_markup=self\._make_keyboard\(alert_id\),\n',
    re.MULTILINE
)
matches = list(RM_LINE.finditer(text))
if len(matches) != 2:
    print(f"❌ [D+E] reply_markup 行预期匹配 2 处，实际匹配 {len(matches)} 处")
    sys.exit(1)
text = RM_LINE.sub("", text)
changes.append(f"[D+E] 删除 2 处 reply_markup=self._make_keyboard(alert_id), 行")

# ============================================================
# 安全校验：确认所有目标都被清理
# ============================================================
if "_make_keyboard" in text:
    print(f"❌ 校验失败：bot.py 里仍残留 _make_keyboard 引用")
    for i, line in enumerate(text.splitlines(), 1):
        if "_make_keyboard" in line:
            print(f"   line {i}: {line}")
    sys.exit(1)

if "_write_alert_to_sheet" in text:
    print(f"❌ 校验失败：bot.py 里仍残留 _write_alert_to_sheet 引用")
    for i, line in enumerate(text.splitlines(), 1):
        if "_write_alert_to_sheet" in line:
            print(f"   line {i}: {line}")
    sys.exit(1)

if "callback_query" in text:
    print(f"❌ 校验失败：bot.py 里仍残留 callback_query 引用")
    for i, line in enumerate(text.splitlines(), 1):
        if "callback_query" in line:
            print(f"   line {i}: {line}")
    sys.exit(1)

# ============================================================
# 输出报告
# ============================================================
old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
old_lines = len(original.splitlines())
new_lines = len(text.splitlines())

print("=" * 60)
print(f"📋 v4.9 patch 报告")
print("=" * 60)
for c in changes:
    print(f"  ✓ {c}")
print()
print(f"  原文件: {old_lines} 行, MD5={old_md5}")
print(f"  新文件: {new_lines} 行, MD5={new_md5}")
print(f"  差异:   {old_lines - new_lines} 行被删除")
print()

if APPLY:
    DST.write_text(text, encoding="utf-8")
    print(f"✅ 已写入 {DST}")
    print(f"   下一步：")
    print(f"   1. python3 -m py_compile {DST}   # 语法检查")
    print(f"   2. diff bot.py {DST} | less       # 人工 review")
    print(f"   3. cp bot.py bot.py.before-v4-9.bak  # 备份")
    print(f"   4. mv {DST} bot.py                # 替换")
else:
    print("ℹ️  当前为干跑模式，未写入文件")
    print("   确认无误后，跑：python3 patch_bot.py --apply")
