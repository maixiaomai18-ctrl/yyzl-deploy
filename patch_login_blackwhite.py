#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
login.html 黑白化 patch
策略: 只改 CSS 变量值, 其他 HTML/CSS/JS 完全不动
覆盖范围: :root 块 + [data-theme="dark"] 块 (两个主题统一改成纯黑)
用法:
    python3 patch_login_blackwhite.py            # 干跑
    python3 patch_login_blackwhite.py --apply    # 实际写入
"""
import sys
import re
import hashlib
from pathlib import Path

SRC = Path("templates/login.html")
DST = Path("templates/login.html.blackwhite-new")
APPLY = "--apply" in sys.argv

if not SRC.exists():
    print(f"找不到 {SRC}")
    sys.exit(1)

original = SRC.read_text(encoding="utf-8")
text = original

# ============================================================
# 变量映射表 (黑白色调最终值)
# 同样的颜色规则同时用在 :root 和 [data-theme="dark"] 块里
# ============================================================
COLOR_MAP = {
    # 背景色
    "--bg-main":         "#0a0a0a",       # 主背景: 近黑
    "--bg-card":         "#161616",       # 卡片背景: 深灰
    "--bg-input":        "#0a0a0a",       # 输入框背景: 跟主背景一致
    "--bg-hover":        "#1f1f1f",       # 悬停背景: 比卡片亮一点

    # 文字色
    "--text-main":       "#ffffff",       # 主文字: 纯白
    "--text-soft":       "#888888",       # 次文字: 中灰
    "--text-light":      "#555555",       # 弱文字 (placeholder): 暗灰

    # 主色 (按钮/聚焦边框)
    "--primary":         "#ffffff",       # 按钮主色: 白色
    "--primary-hover":   "#cccccc",       # 按钮悬停: 浅灰
    "--primary-soft":    "#ffffff22",     # 主色淡版: 白 13% 透明度

    # 边框
    "--border":          "#2a2a2a",       # 边框: 深灰
    "--border-focus":    "#ffffff",       # 聚焦边框: 白色

    # 状态色 (柔和版, 不刺眼)
    "--success":         "#88ff88",       # 成功: 柔和绿
    "--success-soft":    "#102a10",       # 成功底色: 暗绿
    "--danger":          "#ff8888",       # 错误: 柔和红
    "--danger-soft":     "#2a1010",       # 错误底色: 暗红

    # 阴影 (近乎不可见)
    "--shadow-soft":     "rgba(0, 0, 0, 0.3)",
    "--shadow-medium":   "rgba(0, 0, 0, 0.5)",

    # 圆角 (保持原有)
    "--radius-sm":       "4px",
    "--radius-md":       "6px",
    "--radius-lg":       "8px",
}

# ============================================================
# 替换函数: 在指定块内逐行替换变量值
# ============================================================
def replace_vars_in_block(text, block_pattern, label):
    """在 block_pattern 匹配的 CSS 块内, 替换所有变量值"""
    m = re.search(block_pattern, text, re.DOTALL)
    if not m:
        print(f"  [{label}] 找不到 CSS 块, 跳过")
        return text, 0

    block = m.group(0)
    new_block = block
    count = 0

    for var_name, new_value in COLOR_MAP.items():
        # 正则匹配 "  --xxx: yyy;" 这一行 (yyy 可以是任意非分号内容)
        # 注意 var_name 已经带了 -- 前缀 (如 --bg-main)
        var_re = re.compile(
            r'(\s*' + re.escape(var_name) + r'\s*:\s*)([^;]+)(\s*;)',
            re.MULTILINE
        )
        before_count = len(var_re.findall(new_block))
        new_block = var_re.sub(r'\g<1>' + new_value + r'\g<3>', new_block)
        if before_count > 0:
            count += before_count

    text = text[:m.start()] + new_block + text[m.end():]
    print(f"  [{label}] 替换了 {count} 个变量值")
    return text, count

# ============================================================
# 步骤 1: 改默认 :root 块
# ============================================================
text, c1 = replace_vars_in_block(
    text,
    r':root\s*\{[^}]+\}',
    ':root (默认主题)'
)

# ============================================================
# 步骤 2: 改 [data-theme="dark"] 块
# ============================================================
text, c2 = replace_vars_in_block(
    text,
    r'\[data-theme="dark"\]\s*\{[^}]+\}',
    '[data-theme="dark"]'
)

if c1 + c2 == 0:
    print("没有任何变量被替换, patch 失败")
    sys.exit(1)

# ============================================================
# 安全校验: 关键蓝色不应该再出现
# ============================================================
old_blues = [
    "#58A6FF",   # primary 蓝
    "#1F6FEB",   # primary-hover 深蓝
    "#0D1117",   # bg-main 深蓝黑
    "#161B22",   # bg-card 蓝灰
    "#1C2421",   # dark bg-main 绿黑
    "#7FB69F",   # dark primary 青绿
]
remaining = []
for color in old_blues:
    if color in text:
        remaining.append(color)
if remaining:
    print(f"警告: 仍有旧颜色残留 (可能在非 :root 块的位置): {remaining}")
    print("(这是正常的, 因为有些颜色可能写在具体规则里)")
    # 不视为错误, 只警告

# ============================================================
# 报告
# ============================================================
old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
old_lines = len(original.splitlines())
new_lines = len(text.splitlines())

print()
print("=" * 60)
print("login.html 黑白化 patch 报告")
print("=" * 60)
print(f"  :root 块: 替换 {c1} 个变量")
print(f"  [data-theme=dark] 块: 替换 {c2} 个变量")
print(f"  总共替换: {c1 + c2} 个变量值")
print()
print(f"  原文件: {old_lines} 行, MD5={old_md5}")
print(f"  新文件: {new_lines} 行, MD5={new_md5}")
print(f"  行数差异: {new_lines - old_lines:+d} (应该是 0, 因为只改值不删行)")
print()

if APPLY:
    DST.write_text(text, encoding="utf-8")
    print(f"已写入 {DST}")
    print()
    print("下一步:")
    print(f"  1. cp templates/login.html templates/login.html.before-blackwhite.bak")
    print(f"  2. mv {DST} templates/login.html")
    print(f"  3. docker restart tg-web-yyzl")
    print(f"  4. 浏览器打开 http://187.77.134.56:8000 看效果")
else:
    print("当前为干跑模式, 未写入文件")
    print("确认无误后运行: python3 patch_login_blackwhite.py --apply")
