#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
闲聊白名单可编辑 (patch_chitchat_editable.py)

改动:
  1. .env 加 CHITCHAT_WHITELIST 行
  2. config.py 加 CHITCHAT_WHITELIST 全局变量 + reload 同步刷新
  3. tasks.py:53-62 写死 set 改成 set(config.CHITCHAT_WHITELIST)
  4. web.py 加 4 个路由 (page/list/save) + DEFAULT_CHITCHAT 常量
  5. 新建 templates/chitchat.html (复制 sensitive_words.html, 改 4 处)
  6. index.html 导航栏加 "闲聊白名单" 按钮

用法:
    python3 patch_chitchat_editable.py            # 干跑
    python3 patch_chitchat_editable.py --apply    # 实际写入
"""
import sys
import re
import hashlib
from pathlib import Path

APPLY = "--apply" in sys.argv

# ============================================================
# 默认闲聊白名单 (用 tasks.py 当前写死的那批, 保证老用户行为不变)
# ============================================================
DEFAULT_CHITCHAT_WORDS = [
    "你好", "您好", "嗨", "hi", "Hi", "HI", "hello", "Hello",
    "嗯嗯", "嗯", "好的", "好滴", "好哒", "收到", "收到了",
    "哦", "噢", "哈哈", "哈哈哈", "呵呵", "哎",
    "ok", "OK", "Ok", "okay", "Okay",
    "在吗", "在不在", "你在吗", "您在吗",
    "?", "？", "??", "？？", "???", "？？？",
]
DEFAULT_CHITCHAT_STR = ",".join(DEFAULT_CHITCHAT_WORDS)


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def patch_file(path, transformer, label):
    """读 path → transform → 写 path.chitchat-new (干跑) 或 path 内容打到 .chitchat-new"""
    p = Path(path)
    if not p.exists():
        print(f"  [{label}] 文件不存在: {path}")
        return False
    original = p.read_text(encoding="utf-8")
    new_text, info = transformer(original)
    if new_text == original:
        print(f"  [{label}] 无变化 (可能已经改过)")
        return False

    print(f"  [{label}] {info}")
    print(f"    {len(original)} 字节 → {len(new_text)} 字节")

    if APPLY:
        new_path = Path(str(p) + ".chitchat-new")
        new_path.write_text(new_text, encoding="utf-8")
        print(f"    已写入 {new_path}")
    return True


# ============================================================
# 改动 1: .env 加 CHITCHAT_WHITELIST 行
# ============================================================
def transform_env(text):
    if "CHITCHAT_WHITELIST" in text:
        return text, "已存在 CHITCHAT_WHITELIST 行"

    # 在 KEYWORDS 行下面加
    lines = text.splitlines(keepends=True)
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and line.startswith("KEYWORDS="):
            new_lines.append(f"CHITCHAT_WHITELIST={DEFAULT_CHITCHAT_STR}\n")
            inserted = True
    if not inserted:
        # KEYWORDS 行不存在? 就加在末尾
        if not text.endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"CHITCHAT_WHITELIST={DEFAULT_CHITCHAT_STR}\n")
    new_text = "".join(new_lines)
    return new_text, f"在 KEYWORDS 行下方插入 CHITCHAT_WHITELIST"


# ============================================================
# 改动 2: config.py
#   2a. global 声明加 CHITCHAT_WHITELIST
#   2b. reload 函数末尾加解析
#   2c. 模块底部启动时解析
# ============================================================
def transform_config(text):
    if "CHITCHAT_WHITELIST" in text:
        return text, "已存在 CHITCHAT_WHITELIST"

    # 2a. 在 reload 函数的 global KEYWORDS 那行加 CHITCHAT_WHITELIST
    old_global = "    global KEYWORDS, NO_REPLY_MINUTES, PEER_ROLE_LABEL, OPERATOR_LABEL, COMPANY_DISPLAY"
    new_global = "    global KEYWORDS, NO_REPLY_MINUTES, PEER_ROLE_LABEL, OPERATOR_LABEL, COMPANY_DISPLAY\n    global CHITCHAT_WHITELIST"
    if old_global not in text:
        return text, "❌ 找不到 global 声明行 (config.py 结构变了?)"
    text = text.replace(old_global, new_global)

    # 2b. reload 函数里 (4 空格缩进) — 紧挨在 KEYWORDS 行后加
    reload_anchor = '    KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]'
    reload_new = reload_anchor + '\n    CHITCHAT_WHITELIST = [w.strip() for w in os.environ.get("CHITCHAT_WHITELIST", "").split(",") if w.strip()]'
    cnt = text.count(reload_anchor)
    if cnt == 0:
        return text, "❌ 找不到 reload 函数里的 KEYWORDS 行 (4 空格缩进)"
    # 只替换第一处 (reload 函数里的)
    text = text.replace(reload_anchor, reload_new, 1)

    # 2c. 模块底部启动时 (无缩进) — 紧挨在启动 KEYWORDS 行后加
    startup_anchor = '\nKEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]'
    startup_new = startup_anchor + '\nCHITCHAT_WHITELIST = [w.strip() for w in os.environ.get("CHITCHAT_WHITELIST", "").split(",") if w.strip()]'
    if startup_anchor not in text:
        return text, "❌ 找不到模块底部启动时的 KEYWORDS 行 (无缩进)"
    text = text.replace(startup_anchor, startup_new, 1)

    return text, "加 CHITCHAT_WHITELIST: global + reload 解析 + 启动解析"


# ============================================================
# 改动 3: tasks.py 把写死的 set 改成读 config
# ============================================================
def transform_tasks(text):
    # 当前 set 字面量
    old_block = '''    # 规则 5: 兜底白名单
    chitchat = {
        "你好", "您好", "嗨", "hi", "Hi", "HI", "hello", "Hello",
        "嗯嗯", "嗯", "好的", "好滴", "好哒", "收到", "收到了",
        "哦", "噢", "哈哈", "哈哈哈", "呵呵", "哎",
        "ok", "OK", "Ok", "okay", "Okay",
        "在吗", "在不在", "你在吗", "您在吗",
        "?", "？", "??", "？？", "???", "？？？",
    }'''
    new_block = '''    # 规则 5: 兜底白名单 (v4.10: 改为读取 config.CHITCHAT_WHITELIST, 用户可在 web 后台编辑)
    chitchat = set(config.CHITCHAT_WHITELIST)'''

    if old_block not in text:
        # 可能空白字符不一致, 用更松的方式找
        return text, "❌ 找不到 chitchat = {...} 写死块 (可能已改过, 或缩进/字符变化)"

    text = text.replace(old_block, new_block)
    return text, "把写死的 chitchat set 改成 set(config.CHITCHAT_WHITELIST)"


# ============================================================
# 改动 4: web.py
#   4a. 加 DEFAULT_CHITCHAT 常量 (在 DEFAULT_KEYWORDS 下面)
#   4b. 加 3 个路由 (page/list/save) — 在敏感词路由块后面
# ============================================================
WEB_NEW_ROUTES = '''

# === 闲聊白名单管理 (v4.10) ===
@app.route("/chitchat", methods=["GET"])
@login_required
def chitchat_page():
    return render_template("chitchat.html",
                           company=config.COMPANY_DISPLAY)


@app.route("/api/chitchat/list", methods=["GET"])
@login_required
def api_chitchat_list():
    env = read_env()
    raw = env.get("CHITCHAT_WHITELIST", DEFAULT_CHITCHAT)
    words = [w.strip() for w in raw.split(",") if w.strip()]
    return jsonify({"ok": True, "words": words})


@app.route("/api/chitchat/save", methods=["POST"])
@admin_required
def api_chitchat_save():
    """全量保存闲聊白名单"""
    data = request.json or {}
    words = data.get("words", [])
    if not isinstance(words, list):
        return jsonify({"ok": False, "error": "格式错误"})
    cleaned = []
    seen = set()
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        if "," in w:
            return jsonify({"ok": False, "error": f"白名单词不能含逗号: {w}"})
        if w in seen:
            continue
        seen.add(w)
        cleaned.append(w)
    write_env({"CHITCHAT_WHITELIST": ",".join(cleaned)})
    return jsonify({"ok": True, "count": len(cleaned)})

'''

def transform_web(text):
    if "chitchat_page" in text:
        return text, "已存在 chitchat 路由"

    # 4a. 加 DEFAULT_CHITCHAT
    old_default = 'DEFAULT_KEYWORDS = "到期,续费,暂停,下架,上架,地址,打款,欠费,返点,返利,回扣"'
    new_default = old_default + f'\nDEFAULT_CHITCHAT = "{DEFAULT_CHITCHAT_STR}"'
    if old_default not in text:
        return text, "❌ 找不到 DEFAULT_KEYWORDS 定义"
    text = text.replace(old_default, new_default)

    # 4b. 加 3 个路由 — 在 api_sensitive_words_save 函数结束后插入
    # 找 api_sensitive_words_save 函数体结束的标志
    # 它的特征: write_env({"KEYWORDS": ",".join(cleaned)}) 之后下一个 return
    # 然后下一个空行 + 下一个 # === 注释或 @app.route
    # 用更稳的锚点: 紧挨着 api_sensitive_words_save 后面的 # === 标记
    anchor = '    write_env({"KEYWORDS": ",".join(cleaned)})\n    return jsonify({"ok": True, "count": len(cleaned)})\n\n\n# === 账号归属信息'
    if anchor not in text:
        return text, "❌ 找不到 api_sensitive_words_save 函数后的锚点"

    # 在锚点 \n\n\n 处插入新路由 (锚点保留 # === 账号归属信息 行)
    new_anchor = '    write_env({"KEYWORDS": ",".join(cleaned)})\n    return jsonify({"ok": True, "count": len(cleaned)})\n' + WEB_NEW_ROUTES + '\n# === 账号归属信息'
    text = text.replace(anchor, new_anchor)

    return text, "加 DEFAULT_CHITCHAT + 3 个路由 (chitchat_page/list/save)"


# ============================================================
# 改动 5: 新建 templates/chitchat.html (复制 sensitive_words.html 改 4 处)
# ============================================================
def make_chitchat_html(sensitive_text):
    """从 sensitive_words.html 文本生成 chitchat.html"""
    text = sensitive_text

    # 替换标题 (页面标签)
    text = text.replace("敏感词管理", "闲聊白名单管理")

    # 替换 API 端点
    text = text.replace("/api/sensitive_words/list", "/api/chitchat/list")
    text = text.replace("/api/sensitive_words/save", "/api/chitchat/save")

    # 替换提示文字
    text = text.replace(
        "当外事号或对方在对话里说出这些词, 会触发风控-敏感词预警, 推到对应中心的风控群",
        "命中此清单的短消息会被认定为闲聊, 不触发怠工预警 (启发式规则之外的兜底)"
    )
    text = text.replace(
        "默认词: 地址、到期、续费、催款、结算",
        "默认词: 你好、嗯嗯、好的、收到、ok 等 38 个常用问候/应答"
    )
    text = text.replace("敏感词清单", "闲聊白名单")
    text = text.replace("输入新敏感词", "输入新白名单词")
    text = text.replace("共 ", "共 ")  # 占位
    text = text.replace("个敏感词", "个白名单词")

    return text


# ============================================================
# 改动 6: index.html 加导航按钮
# ============================================================
def transform_index(text):
    if 'href="/chitchat"' in text:
        return text, "已存在 /chitchat 链接"

    old_link = '<a href="/sensitive_words">敏感词</a>'
    new_link = old_link + '\n    <a href="/chitchat">闲聊白名单</a>'
    if old_link not in text:
        return text, "❌ 找不到 /sensitive_words 链接锚点"
    text = text.replace(old_link, new_link)
    return text, "在 /sensitive_words 链接后加 /chitchat 链接"


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("闲聊白名单可编辑功能 patch")
    print("=" * 60)
    print(f"模式: {'实际写入' if APPLY else '干跑'}")
    print()

    results = []

    # 改动 1: .env
    print("--- [1/6] .env ---")
    results.append(("env", patch_file(".env", transform_env, ".env")))

    # 改动 2: config.py
    print("\n--- [2/6] config.py ---")
    results.append(("config.py", patch_file("config.py", transform_config, "config.py")))

    # 改动 3: tasks.py
    print("\n--- [3/6] tasks.py ---")
    results.append(("tasks.py", patch_file("tasks.py", transform_tasks, "tasks.py")))

    # 改动 4: web.py
    print("\n--- [4/6] web.py ---")
    results.append(("web.py", patch_file("web.py", transform_web, "web.py")))

    # 改动 5: 新建 chitchat.html
    print("\n--- [5/6] templates/chitchat.html (新建) ---")
    sensitive_path = Path("templates/sensitive_words.html")
    if not sensitive_path.exists():
        print(f"  ❌ 找不到 {sensitive_path}, 无法生成 chitchat.html")
        results.append(("chitchat.html", False))
    else:
        sensitive_text = sensitive_path.read_text(encoding="utf-8")
        chitchat_text = make_chitchat_html(sensitive_text)
        chitchat_path = Path("templates/chitchat.html")
        if chitchat_path.exists():
            print(f"  已存在 {chitchat_path} (会被覆盖)")
        print(f"  生成 chitchat.html: {len(chitchat_text)} 字节")
        if APPLY:
            new_path = Path(str(chitchat_path) + ".chitchat-new")
            new_path.write_text(chitchat_text, encoding="utf-8")
            print(f"  已写入 {new_path}")
        results.append(("chitchat.html", True))

    # 改动 6: index.html
    print("\n--- [6/6] templates/index.html ---")
    results.append(("index.html", patch_file("templates/index.html", transform_index, "index.html")))

    # 汇总
    print()
    print("=" * 60)
    print("汇总")
    print("=" * 60)
    for name, ok in results:
        status = "✓" if ok else "✗ (无变化或失败)"
        print(f"  {status}  {name}")

    print()
    if APPLY:
        print("下一步:")
        print("  1. 备份 + 替换 (运行下面这块):")
        print("    cd /root/yyzl-deploy && \\")
        print("    cp .env .env.before-chitchat.bak && \\")
        print("    cp config.py config.py.before-chitchat.bak && \\")
        print("    cp tasks.py tasks.py.before-chitchat.bak && \\")
        print("    cp web.py web.py.before-chitchat.bak && \\")
        print("    cp templates/index.html templates/index.html.before-chitchat.bak && \\")
        print("    mv .env.chitchat-new .env && \\")
        print("    mv config.py.chitchat-new config.py && \\")
        print("    mv tasks.py.chitchat-new tasks.py && \\")
        print("    mv web.py.chitchat-new web.py && \\")
        print("    mv templates/chitchat.html.chitchat-new templates/chitchat.html && \\")
        print("    mv templates/index.html.chitchat-new templates/index.html && \\")
        print("    docker restart tg-monitor-yyzl tg-web-yyzl && \\")
        print("    echo '✅ 完成'")
        print("")
        print("  2. 浏览器访问 http://187.77.134.56:8000/chitchat 验证")
    else:
        print("当前为干跑模式, 未写入文件")
        print("确认无误后运行: python3 patch_chitchat_editable.py --apply")


if __name__ == "__main__":
    main()
