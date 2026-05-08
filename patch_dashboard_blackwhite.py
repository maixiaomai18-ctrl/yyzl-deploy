#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.html 黑白化 + 删特效 patch
策略:
  1. 改 :root 变量值 → 黑白
  2. 删除所有 @keyframes 块 (5 个动画定义)
  3. animation: xxx 行 → animation: none
  4. backdrop-filter: blur(...) → backdrop-filter: none
  5. linear-gradient(...) → 单色 (取第一个非透明色)
  6. radial-gradient(...) → transparent
  7. mask-image: radial-gradient(...) → none

用法:
    python3 patch_dashboard_blackwhite.py            # 干跑
    python3 patch_dashboard_blackwhite.py --apply    # 实际写入
"""
import sys
import re
import hashlib
from pathlib import Path

SRC = Path("templates/dashboard.html")
DST = Path("templates/dashboard.html.blackwhite-new")
APPLY = "--apply" in sys.argv

if not SRC.exists():
    print(f"找不到 {SRC}")
    sys.exit(1)

original = SRC.read_text(encoding="utf-8")
text = original

# ============================================================
# 步骤 1: 改 :root 变量值
# ============================================================
print("=" * 60)
print("步骤 1: 改 :root 变量值")
print("=" * 60)

COLOR_MAP = {
    # 重定义所有 accent 为白色或灰色, 让"蓝色赛博"消失
    "--accent-red":      "#ffffff",       # 主强调色 → 白
    "--accent-ember":    "#cccccc",       # 次强调 → 浅灰
    "--accent-deep":     "#ffffff",       # 深色强调 → 白
    "--accent-lime":     "#88ff88",       # ONLINE 绿 → 柔和绿
    "--accent-warn":     "#ffcc66",       # 警告橙 → 柔和橙
    "--accent-danger":   "#ff8888",       # 危险红 → 柔和红

    # 背景
    "--bg-0":            "#0a0a0a",       # 主背景 → 近黑
    "--bg-1":            "#161616",       # 卡片背景 → 深灰

    # 文字
    "--text":            "#ffffff",       # 主文字 → 纯白
    "--text-dim":        "#888888",       # 次文字 → 中灰
    "--text-bright":     "#ffffff",       # 高亮文字 → 纯白

    # 线
    "--line":            "#2a2a2a",       # 边框 → 深灰
}

# 找 :root 块
root_match = re.search(r':root\s*\{[^}]+\}', text, re.DOTALL)
if not root_match:
    print("找不到 :root 块!")
    sys.exit(1)

root_block = root_match.group(0)
new_root = root_block
var_count = 0

for var_name, new_value in COLOR_MAP.items():
    var_re = re.compile(
        r'(\s*' + re.escape(var_name) + r'\s*:\s*)([^;]+)(\s*;)',
        re.MULTILINE
    )
    matches = var_re.findall(new_root)
    if matches:
        new_root = var_re.sub(r'\g<1>' + new_value + r'\g<3>', new_root)
        var_count += len(matches)

text = text[:root_match.start()] + new_root + text[root_match.end():]
print(f"  替换了 {var_count} 个 CSS 变量值")

# ============================================================
# 步骤 2: 删除所有 @keyframes 块
# ============================================================
print()
print("=" * 60)
print("步骤 2: 删除 @keyframes 块")
print("=" * 60)

# 匹配 "@keyframes xxx { ... }" 块, 一行内的
# 例: @keyframes bgShift { 0%,100% { filter: brightness(1); } 50% { ... } }
# 注意: keyframes 块内部有嵌套的 {}, 简单 [^}] 不行
# 但根据诊断, dashboard.html 里 5 个 @keyframes 都写在一行内, 用嵌套花括号正则即可

# 用迭代匹配: @keyframes name { 内部最多两层嵌套 }
keyframes_pattern = re.compile(
    r'@keyframes\s+\w+\s*\{(?:[^{}]*\{[^}]*\}[^{}]*)*[^{}]*\}',
    re.DOTALL
)
keyframes_matches = keyframes_pattern.findall(text)
print(f"  找到 {len(keyframes_matches)} 个 @keyframes 块")
for i, m in enumerate(keyframes_matches, 1):
    name_match = re.match(r'@keyframes\s+(\w+)', m)
    if name_match:
        print(f"    [{i}] {name_match.group(1)}: {len(m)} 字节")

text = keyframes_pattern.sub('', text)

# ============================================================
# 步骤 3: animation: xxx 行 → animation: none
# ============================================================
print()
print("=" * 60)
print("步骤 3: 禁用 animation 引用")
print("=" * 60)

# 匹配: animation: xxx 30s linear infinite;  →  animation: none;
animation_re = re.compile(r'(animation\s*:\s*)[^;]+(\s*;)', re.IGNORECASE)
anim_count = len(animation_re.findall(text))
text = animation_re.sub(r'\g<1>none\g<2>', text)
print(f"  替换了 {anim_count} 处 animation 引用")

# ============================================================
# 步骤 4: backdrop-filter → none
# ============================================================
print()
print("=" * 60)
print("步骤 4: 禁用 backdrop-filter")
print("=" * 60)

bf_re = re.compile(r'((?:-webkit-)?backdrop-filter\s*:\s*)[^;]+(\s*;)', re.IGNORECASE)
bf_count = len(bf_re.findall(text))
text = bf_re.sub(r'\g<1>none\g<2>', text)
print(f"  替换了 {bf_count} 处 backdrop-filter")

# ============================================================
# 步骤 5: linear-gradient(...) → 单色
# 策略: 取第一个非透明颜色当单色背景
# ============================================================
print()
print("=" * 60)
print("步骤 5: linear-gradient → 单色")
print("=" * 60)

def replace_linear_gradient(m):
    """linear-gradient(...) 替换成单色"""
    inner = m.group(1)
    # 提取所有颜色值 (排除 transparent, deg)
    # 找形如 #xxxxxx, #xxx, rgba(...), rgb(...), var(...)
    colors = re.findall(
        r'(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|var\([^)]+\))',
        inner
    )
    # 过滤掉 transparent 和过淡的 (透明度 < 0.5 的 rgba)
    valid_colors = []
    for c in colors:
        if c.lower() == 'transparent':
            continue
        if c.startswith('rgba'):
            # 解析 rgba 透明度
            alpha_m = re.search(r',\s*([\d.]+)\s*\)', c)
            if alpha_m:
                try:
                    alpha = float(alpha_m.group(1))
                    if alpha < 0.5:
                        continue  # 太淡, 跳过
                except:
                    pass
        valid_colors.append(c)

    # 取第一个有效颜色, 没有就用 transparent
    if valid_colors:
        return valid_colors[0]
    return 'transparent'

# 匹配 linear-gradient(...) - 处理嵌套括号
def replace_gradient_func(text, func_name):
    """替换 linear-gradient/radial-gradient 函数调用, 处理嵌套括号"""
    result = []
    i = 0
    count = 0
    pattern = func_name + '('
    while i < len(text):
        idx = text.find(pattern, i)
        if idx == -1:
            result.append(text[i:])
            break
        result.append(text[i:idx])
        # 找配对的 )
        depth = 0
        j = idx + len(pattern) - 1  # 指向 (
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(text):
            result.append(text[idx:])
            break
        # text[idx:j+1] 是完整的 func_name(...)
        full = text[idx:j+1]
        # 提取括号内内容
        inner = text[idx + len(pattern):j]
        # 替换
        m = type('M', (), {'group': lambda self, n: inner})()
        replacement = replace_linear_gradient(m)
        result.append(replacement)
        count += 1
        i = j + 1
    return ''.join(result), count

text, lg_count = replace_gradient_func(text, 'linear-gradient')
print(f"  替换了 {lg_count} 个 linear-gradient")

# ============================================================
# 步骤 6: radial-gradient(...) → transparent
# 策略: radial 一般是光晕效果, 直接 transparent 即可
# ============================================================
print()
print("=" * 60)
print("步骤 6: radial-gradient → transparent")
print("=" * 60)

def replace_radial_gradient_simple(text, func_name):
    """radial-gradient(...) 直接替换成 transparent"""
    result = []
    i = 0
    count = 0
    pattern = func_name + '('
    while i < len(text):
        idx = text.find(pattern, i)
        if idx == -1:
            result.append(text[i:])
            break
        result.append(text[i:idx])
        depth = 0
        j = idx + len(pattern) - 1
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(text):
            result.append(text[idx:])
            break
        result.append('transparent')
        count += 1
        i = j + 1
    return ''.join(result), count

text, rg_count = replace_radial_gradient_simple(text, 'radial-gradient')
print(f"  替换了 {rg_count} 个 radial-gradient")

# ============================================================
# 步骤 7: mask-image: transparent (上一步已被改) → mask-image: none
# 因为上一步把 radial-gradient(...) 替换成 transparent, 但 mask-image: transparent 不合法
# 这里把 mask-image: transparent 改成 none
# ============================================================
print()
print("=" * 60)
print("步骤 7: mask-image: transparent → none")
print("=" * 60)

mask_re = re.compile(
    r'((?:-webkit-)?mask-image\s*:\s*)transparent(\s*;)',
    re.IGNORECASE
)
mask_count = len(mask_re.findall(text))
text = mask_re.sub(r'\g<1>none\g<2>', text)
print(f"  替换了 {mask_count} 处 mask-image")

# ============================================================
# 报告
# ============================================================
old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
old_lines = len(original.splitlines())
new_lines = len(text.splitlines())
old_size = len(original.encode("utf-8"))
new_size = len(text.encode("utf-8"))

print()
print("=" * 60)
print("dashboard.html 黑白化 + 删特效 patch 报告")
print("=" * 60)
print(f"  CSS 变量替换: {var_count}")
print(f"  @keyframes 删除: {len(keyframes_matches)}")
print(f"  animation 禁用: {anim_count}")
print(f"  backdrop-filter 禁用: {bf_count}")
print(f"  linear-gradient 单色化: {lg_count}")
print(f"  radial-gradient 透明化: {rg_count}")
print(f"  mask-image 修复: {mask_count}")
print()
print(f"  原文件: {old_lines} 行, {old_size} 字节, MD5={old_md5}")
print(f"  新文件: {new_lines} 行, {new_size} 字节, MD5={new_md5}")
print(f"  行数变化: {new_lines - old_lines:+d}")
print(f"  字节变化: {new_size - old_size:+d}")
print()

# 安全检查
errors = []

# 检查 1: 不应该再有 linear-gradient/ radial-gradient
remain_lg = text.count('linear-gradient(')
remain_rg = text.count('radial-gradient(')
if remain_lg > 0 or remain_rg > 0:
    print(f"  警告: 仍有残留 linear-gradient={remain_lg}, radial-gradient={remain_rg}")

# 检查 2: 不应该再有 @keyframes
remain_kf = len(re.findall(r'@keyframes', text))
if remain_kf > 0:
    print(f"  警告: 仍有 {remain_kf} 个 @keyframes 残留")

# 检查 3: 文件不该缩小过头 (可能误删)
if new_size < old_size * 0.85:
    print(f"  警告: 文件缩小超过 15%, 可能误删")

if APPLY:
    DST.write_text(text, encoding="utf-8")
    print(f"已写入 {DST}")
    print()
    print("下一步:")
    print(f"  1. cp templates/dashboard.html templates/dashboard.html.before-blackwhite.bak")
    print(f"  2. mv {DST} templates/dashboard.html")
    print(f"  3. docker restart tg-web-yyzl")
    print(f"  4. 浏览器 Ctrl+Shift+R 刷新 http://187.77.134.56:8000/dashboard 看效果")
    print()
    print("如果效果不好要还原:")
    print("  cp templates/dashboard.html.before-blackwhite.bak templates/dashboard.html && \\")
    print("  docker restart tg-web-yyzl")
else:
    print("当前为干跑模式, 未写入文件")
    print("确认无误后运行: python3 patch_dashboard_blackwhite.py --apply")
