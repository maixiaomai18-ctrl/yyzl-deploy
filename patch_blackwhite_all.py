#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用黑白化 patch (覆盖 5 个 HTML 文件)
用一个脚本统一处理:
  - dashboard.html (二次清理: 删残留绿/蓝)
  - index.html
  - centers.html
  - sensitive_words.html
  - setup.html (含删特效)

策略:
  1. 每个文件按 :root 变量名映射 → 黑白
  2. 全局写死颜色替换 (#58A6FF / #2aabee 等所有蓝绿 → 白/灰)
  3. setup.html 还要删 keyframes / animation / gradients

用法:
    python3 patch_blackwhite_all.py            # 干跑
    python3 patch_blackwhite_all.py --apply    # 实际写入
"""
import sys
import re
import hashlib
from pathlib import Path

APPLY = "--apply" in sys.argv

# ============================================================
# 全局写死颜色替换 (作用于所有文件)
# 原本写死的赛博朋克蓝绿 → 黑白系
# ============================================================
GLOBAL_HARDCODED_COLORS = {
    # GitHub 暗色调
    "#58A6FF": "#ffffff",   # 主蓝
    "#1F6FEB": "#cccccc",   # 深蓝
    "#56D364": "#ffffff",   # 亮绿 (success-text)
    "#3FB950": "#ffffff",   # 绿 (注: ONLINE 灯也跟着白)
    "#F0883E": "#ffaa66",   # 橙 (warning) → 柔和
    "#F85149": "#ff8888",   # 红 (danger) → 柔和
    # setup/dashboard 用的蓝色调
    "#2aabee": "#ffffff",   # accent-red 蓝
    "#3390ec": "#cccccc",   # accent-deep 深蓝
    "#64abde": "#aaaaaa",   # accent-ember 浅蓝
    "#4fae4f": "#ffffff",   # accent-lime 绿
    "#f5a623": "#ffaa66",   # accent-warn 橙
    # rgba 蓝色调 (透明度先留 ≥0.1 的)
    "rgba(88, 166, 255": "rgba(255, 255, 255",   # #58A6FF rgba
    "rgba(31, 111, 235": "rgba(204, 204, 204",   # #1F6FEB rgba
    "rgba(42, 171, 238": "rgba(255, 255, 255",   # #2aabee rgba
    "rgba(42,171,238": "rgba(255,255,255",       # 无空格版
    "rgba(51, 144, 236": "rgba(204, 204, 204",   # #3390ec rgba
    "rgba(51,144,236": "rgba(204,204,204",
}

# ============================================================
# 每个文件的 :root 变量映射
# ============================================================

# index.html 和 dashboard.html 的 :root 变量名差不多, 但也有差异
ROOT_MAPS = {
    "dashboard.html": {
        "--accent-red":      "#ffffff",
        "--accent-ember":    "#cccccc",
        "--accent-deep":     "#ffffff",
        "--accent-lime":     "#ffffff",     # 绿 → 白 (干掉 ONLINE 绿灯)
        "--accent-warn":     "#ffaa66",
        "--accent-danger":   "#ff8888",
        "--bg-0":            "#0a0a0a",
        "--bg-1":            "#161616",
        "--text":            "#ffffff",
        "--text-dim":        "#888888",
        "--text-bright":     "#ffffff",
        "--line":            "#2a2a2a",
    },
    "index.html": {
        "--bg-main":         "#0a0a0a",
        "--bg-card":         "#161616",
        "--bg-input":        "#0a0a0a",
        "--bg-hover":        "#1f1f1f",
        "--bg-header":       "rgba(10, 10, 10, 0.92)",
        "--text-main":       "#ffffff",
        "--text-soft":       "#888888",
        "--text-light":      "#555555",
        "--primary":         "#ffffff",
        "--primary-hover":   "#cccccc",
        "--primary-soft":    "#ffffff44",
        "--primary-bg":      "#ffffff22",
        "--border":          "#2a2a2a",
        "--border-focus":    "#ffffff",
        "--success":         "#88ff88",       # 绿色保留, 因为有"成功"语义
        "--success-text":    "#88ff88",
        "--warning":         "#ffaa66",
        "--warning-bg":      "#2a1f0f",
        "--danger":          "#ff8888",
        "--danger-bg":       "#2a1414",
        "--info":            "#ffffff",
        "--info-bg":         "#1a1a1a",
        "--shadow-soft":     "rgba(0, 0, 0, 0.3)",
        "--shadow-medium":   "rgba(0, 0, 0, 0.5)",
    },
    "centers.html": {
        "--bg":              "#0a0a0a",
        "--bg-card":         "#161616",
        "--bg-soft":         "#1f1f1f",
        "--bg-input":        "#0a0a0a",
        "--primary":         "#ffffff",
        "--primary-h":       "#cccccc",
        "--accent":          "#ffaa66",
        "--text":            "#ffffff",
        "--text-soft":       "#888888",
        "--text-dim":        "#555555",
        "--line":            "#2a2a2a",
        "--line-soft":       "#1f1f1f",
        "--ok":              "#88ff88",
        "--err":             "#ff8888",
        "--shadow":          "0 1px 3px rgba(0,0,0,0.4)",
    },
    "sensitive_words.html": {
        "--bg":              "#0a0a0a",
        "--bg-card":         "#161616",
        "--bg-soft":         "#1f1f1f",
        "--bg-input":        "#0a0a0a",
        "--primary":         "#ffffff",
        "--primary-h":       "#cccccc",
        "--accent":          "#ffaa66",
        "--text":            "#ffffff",
        "--text-soft":       "#888888",
        "--text-dim":        "#555555",
        "--line":            "#2a2a2a",
        "--line-soft":       "#1f1f1f",
        "--ok":              "#88ff88",
        "--err":             "#ff8888",
    },
    "setup.html": {
        "--accent-red":      "#ffffff",
        "--accent-ember":    "#aaaaaa",
        "--accent-deep":     "#cccccc",
        "--accent-lime":     "#ffffff",
        "--accent-danger":   "#ff8888",
        "--accent-warn":     "#ffaa66",
        "--bg-0":            "#050505",       # 已经够黑, 保持
        "--bg-1":            "#0d0d0d",       # 也保持
        "--bg-2":            "#1a1a1a",       # 也保持
        "--text":            "#ffffff",
        "--text-dim":        "#888888",
        "--text-bright":     "#ffffff",
        "--border":          "#2a2a2a",       # 原 rgba(42,171,238,0.2) 蓝色边框 → 灰
    },
}

# 哪些文件需要删特效 (动画/渐变/模糊)
DELETE_EFFECTS_FILES = {"setup.html"}  # dashboard.html 上次已删过, 这次不重复

# ============================================================
# 工具函数
# ============================================================

def replace_root_vars(text, var_map, label):
    """在 :root 块内替换 CSS 变量值"""
    m = re.search(r':root\s*\{[^}]+\}', text, re.DOTALL)
    if not m:
        print(f"    [{label}] 找不到 :root 块, 跳过")
        return text, 0

    block = m.group(0)
    new_block = block
    count = 0

    for var_name, new_value in var_map.items():
        var_re = re.compile(
            r'(\s*' + re.escape(var_name) + r'\s*:\s*)([^;]+)(\s*;)',
            re.MULTILINE
        )
        matches = var_re.findall(new_block)
        if matches:
            new_block = var_re.sub(r'\g<1>' + new_value + r'\g<3>', new_block)
            count += len(matches)

    text = text[:m.start()] + new_block + text[m.end():]
    return text, count


def replace_global_colors(text, color_map):
    """全局替换写死的颜色"""
    count = 0
    for old, new in color_map.items():
        # 简单字符串替换 (大小写不敏感, 但保留原文件大小写)
        # 用正则做大小写不敏感匹配
        regex = re.compile(re.escape(old), re.IGNORECASE)
        n = len(regex.findall(text))
        if n > 0:
            text = regex.sub(new, text)
            count += n
    return text, count


def remove_keyframes(text):
    """删除所有 @keyframes 块"""
    # 一行内的 @keyframes (dashboard 那种)
    keyframes_pattern = re.compile(
        r'@keyframes\s+\w+\s*\{(?:[^{}]*\{[^}]*\}[^{}]*)*[^{}]*\}',
        re.DOTALL
    )
    matches = keyframes_pattern.findall(text)
    text = keyframes_pattern.sub('', text)
    return text, len(matches)


def disable_animations(text):
    """animation: xxx 行 → animation: none"""
    animation_re = re.compile(r'(animation\s*:\s*)[^;]+(\s*;)', re.IGNORECASE)
    count = len(animation_re.findall(text))
    text = animation_re.sub(r'\g<1>none\g<2>', text)
    return text, count


def disable_backdrop_filter(text):
    """backdrop-filter: blur(...) → backdrop-filter: none"""
    bf_re = re.compile(r'((?:-webkit-)?backdrop-filter\s*:\s*)[^;]+(\s*;)', re.IGNORECASE)
    count = len(bf_re.findall(text))
    text = bf_re.sub(r'\g<1>none\g<2>', text)
    return text, count


def replace_gradient_func(text, func_name, replacement_strategy):
    """替换 linear-gradient / radial-gradient 函数调用 (处理嵌套括号)"""
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
        inner = text[idx + len(pattern):j]
        replacement = replacement_strategy(inner)
        result.append(replacement)
        count += 1
        i = j + 1
    return ''.join(result), count


def linear_gradient_to_color(inner):
    """linear-gradient(...) → 单色 (取第一个非透明色)"""
    colors = re.findall(
        r'(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)|var\([^)]+\))',
        inner
    )
    valid_colors = []
    for c in colors:
        if c.lower() == 'transparent':
            continue
        if c.startswith('rgba'):
            alpha_m = re.search(r',\s*([\d.]+)\s*\)', c)
            if alpha_m:
                try:
                    alpha = float(alpha_m.group(1))
                    if alpha < 0.5:
                        continue
                except:
                    pass
        valid_colors.append(c)
    return valid_colors[0] if valid_colors else 'transparent'


def fix_mask_image(text):
    """mask-image: transparent → none (上一步把 radial-gradient 换成 transparent 了)"""
    mask_re = re.compile(
        r'((?:-webkit-)?mask-image\s*:\s*)transparent(\s*;)',
        re.IGNORECASE
    )
    count = len(mask_re.findall(text))
    text = mask_re.sub(r'\g<1>none\g<2>', text)
    return text, count


# ============================================================
# 主流程
# ============================================================

def patch_file(filename):
    src = Path("templates") / filename
    dst = Path("templates") / (filename + ".blackwhite-new")

    if not src.exists():
        print(f"  跳过 {filename} (文件不存在)")
        return None

    original = src.read_text(encoding="utf-8")
    text = original

    print()
    print("=" * 60)
    print(f"处理 {filename}")
    print("=" * 60)

    stats = {}

    # 步骤 1: 改 :root 变量
    var_map = ROOT_MAPS.get(filename, {})
    if var_map:
        text, c = replace_root_vars(text, var_map, filename)
        stats[":root 变量"] = c
        print(f"  :root 变量替换: {c}")

    # 步骤 2: 删特效 (仅 setup.html)
    if filename in DELETE_EFFECTS_FILES:
        # 2a. 删 keyframes
        text, c = remove_keyframes(text)
        stats["@keyframes 删"] = c
        print(f"  @keyframes 删: {c}")

        # 2b. 禁 animation
        text, c = disable_animations(text)
        stats["animation 禁用"] = c
        print(f"  animation 禁用: {c}")

        # 2c. 禁 backdrop-filter
        text, c = disable_backdrop_filter(text)
        stats["backdrop-filter 禁用"] = c
        print(f"  backdrop-filter 禁用: {c}")

        # 2d. linear-gradient 单色化
        text, c = replace_gradient_func(text, 'linear-gradient', linear_gradient_to_color)
        stats["linear-gradient 单色"] = c
        print(f"  linear-gradient 单色化: {c}")

        # 2e. radial-gradient 透明化
        text, c = replace_gradient_func(text, 'radial-gradient', lambda x: 'transparent')
        stats["radial-gradient 透明"] = c
        print(f"  radial-gradient 透明化: {c}")

        # 2f. mask-image: transparent → none
        text, c = fix_mask_image(text)
        stats["mask-image 修复"] = c
        print(f"  mask-image 修复: {c}")

    # 步骤 3: 全局写死颜色替换
    text, c = replace_global_colors(text, GLOBAL_HARDCODED_COLORS)
    stats["写死颜色全局替换"] = c
    print(f"  写死颜色全局替换: {c}")

    # 报告
    old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
    new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
    old_size = len(original.encode("utf-8"))
    new_size = len(text.encode("utf-8"))
    old_lines = len(original.splitlines())
    new_lines = len(text.splitlines())

    print(f"  ----")
    print(f"  原文件: {old_lines} 行, {old_size} 字节")
    print(f"  新文件: {new_lines} 行, {new_size} 字节")
    print(f"  变化:   {new_lines - old_lines:+d} 行, {new_size - old_size:+d} 字节")
    print(f"  MD5:    {old_md5[:12]} → {new_md5[:12]}")

    if old_md5 == new_md5:
        print(f"  ⚠️  文件无变化")
        return None

    if APPLY:
        dst.write_text(text, encoding="utf-8")
        print(f"  已写入 {dst}")

    return stats


def main():
    print("通用黑白化 patch — 5 个文件")
    print("=" * 60)
    print(f"模式: {'实际写入' if APPLY else '干跑'}")

    files = ["dashboard.html", "index.html", "centers.html",
             "sensitive_words.html", "setup.html"]

    all_stats = {}
    for f in files:
        s = patch_file(f)
        if s:
            all_stats[f] = s

    print()
    print("=" * 60)
    print("汇总")
    print("=" * 60)
    for f, stats in all_stats.items():
        print(f"  {f}:")
        for k, v in stats.items():
            print(f"    {k}: {v}")

    print()
    if APPLY:
        print("下一步:")
        print("  1. 备份原文件:")
        for f in files:
            print(f"     cp templates/{f} templates/{f}.before-blackwhite-v2.bak")
        print("  2. 替换:")
        for f in files:
            print(f"     mv templates/{f}.blackwhite-new templates/{f}")
        print("  3. docker restart tg-web-yyzl")
        print("  4. 浏览器 Ctrl+Shift+R 验证")
    else:
        print("当前为干跑模式, 未写入文件")
        print("确认无误后运行: python3 patch_blackwhite_all.py --apply")


if __name__ == "__main__":
    main()
