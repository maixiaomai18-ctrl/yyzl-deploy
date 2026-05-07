#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.4 步骤 2.10 patch_tasks.py
- 把 tasks.py._daily_data_sync_loop 从老版 daily_data_writer 切到 v3.4 daily_report
- 加启动钩子: 首次进入循环时调 sync_all_accounts() 给所有账号建当日行
- 开关接入 (方案 C): 开关关时循环空转, 每分钟检查一次, 改 .env 60秒内生效

策略: 一刀替换整个 _daily_data_sync_loop 函数体, 锚点用 def 行 + 函数末尾的 await asyncio.sleep(60)
用法:
    python3 patch_tasks_v3_4_step210.py            # 干跑
    python3 patch_tasks_v3_4_step210.py --apply    # 实际写入 tasks.py.v3-4-step210-new
"""
import sys
import re
import hashlib
from pathlib import Path

SRC = Path("tasks.py")
DST = Path("tasks.py.v3-4-step210-new")
APPLY = "--apply" in sys.argv

if not SRC.exists():
    print(f"❌ 找不到 {SRC}")
    sys.exit(1)

original = SRC.read_text(encoding="utf-8")
text = original

# ============================================================
# 新函数体: _daily_data_sync_loop (v3.4 切换 daily_report 版)
# 注意: Python 缩进 = 4 空格, 函数定义在 class 内 = 4空格 + 4空格 = 8空格起
# ============================================================
NEW_FUNC = '''    async def _daily_data_sync_loop(self):
        """每日数据写入 Sheet 的循环 - v3.4 (调用 daily_report.flush_today)
        触发: 柬周一至五 20:30 / 周六 17:30 / 周日跳过
        开关: config.DAILY_REPORT_ENABLED (false 时空转, 改 .env 60秒内生效)
        启动钩子: 首次进入循环时调 sync_all_accounts() 给所有账号建当日行
        """
        import os
        from datetime import datetime, timedelta, timezone
        TZ_KH = timezone(timedelta(hours=7))

        sheet_id = os.environ.get("DAILY_SHEET_ID", "").strip()
        if not sheet_id:
            logger.warning("[daily_sync_loop] DAILY_SHEET_ID 未配置, 不启动")
            return

        vps_label = os.environ.get("VPS_LABEL", "未设置")
        tab_name = vps_label.rstrip("0123456789")

        logger.info(f"[daily_sync_loop] v3.4 启动, sheet_id={sheet_id[:8]}..., tab={tab_name}, vps_label={vps_label}")
        last_synced_date = None
        ensure_done = False  # 启动钩子: 首次进入循环时跑一次

        while self._running:
            try:
                # 每次循环重新读 config (支持 reload_if_env_changed)
                config.reload_if_env_changed()

                if not config.DAILY_REPORT_ENABLED:
                    # 开关关时空转, 60秒后再检查
                    await asyncio.sleep(60)
                    continue

                # 启动钩子: 首次进入循环 + 开关已开 → 给所有账号建当日行
                if not ensure_done:
                    try:
                        from daily_report import DailyReportWriter
                        drw = DailyReportWriter(sheet_id, tab_name, vps_label, self.sheets.gc)
                        result = drw.sync_all_accounts(dry_run=False)
                        logger.info(f"[daily_sync_loop] 启动 ensure_account_row 完成: {result}")
                        ensure_done = True
                    except Exception as e:
                        logger.error(f"[daily_sync_loop] 启动 ensure 失败: {e}", exc_info=True)
                        # 失败也标记为 done, 不要无限重试; 等下次定时 flush 触发时再说
                        ensure_done = True

                now_kh = datetime.now(TZ_KH)
                wd = now_kh.weekday()
                today_kh = now_kh.date()

                if wd == 6:  # 周日跳过
                    await asyncio.sleep(60)
                    continue

                target_h, target_m = (17, 30) if wd == 5 else (20, 30)
                hit = (now_kh.hour == target_h and now_kh.minute == target_m)

                if hit and last_synced_date != today_kh:
                    logger.info(f"[daily_sync_loop] 触发 flush_today: {now_kh.strftime('%Y-%m-%d %H:%M')} (柬)")
                    try:
                        from daily_report import DailyReportWriter
                        drw = DailyReportWriter(sheet_id, tab_name, vps_label, self.sheets.gc)
                        result = drw.flush_today(dry_run=False)
                        logger.info(f"[daily_sync_loop] flush_today 结果: {result}")
                        last_synced_date = today_kh
                    except Exception as e:
                        logger.error(f"[daily_sync_loop] flush_today 异常: {e}", exc_info=True)
                        last_synced_date = today_kh  # 防同分钟内反复重试

            except Exception as e:
                logger.error(f"[daily_sync_loop] 外层异常: {e}", exc_info=True)

            await asyncio.sleep(60)

'''

# ============================================================
# 锚点匹配: 从 "    async def _daily_data_sync_loop(self):" 开始,
# 到下一个 "    async def " 或 "    def " 之前结束
# ============================================================
PATTERN = re.compile(
    r'    async def _daily_data_sync_loop\(self\):.*?'
    r'(?=    async def |    def |^class )',
    re.DOTALL | re.MULTILINE
)
m = PATTERN.search(text)
if not m:
    print("❌ 找不到 _daily_data_sync_loop 函数锚点")
    sys.exit(1)

old_func = m.group(0)
old_func_lines = len(old_func.splitlines())
new_func_lines = len(NEW_FUNC.splitlines())

# ============================================================
# 替换
# ============================================================
text = text[:m.start()] + NEW_FUNC + text[m.end():]

# ============================================================
# 安全校验
# ============================================================
checks = [
    ("daily_data_writer", "切换不彻底, 仍有 daily_data_writer 引用"),
    ("run_daily_sync", "切换不彻底, 仍有 run_daily_sync 引用"),
]
for keyword, msg in checks:
    if keyword in text:
        print(f"❌ 校验失败: {msg}")
        for i, line in enumerate(text.splitlines(), 1):
            if keyword in line:
                print(f"   line {i}: {line}")
        sys.exit(1)

# 必须包含的新内容
must_have = [
    "from daily_report import DailyReportWriter",
    "drw.sync_all_accounts",
    "drw.flush_today",
    "config.DAILY_REPORT_ENABLED",
    "config.reload_if_env_changed",
]
for keyword in must_have:
    if keyword not in text:
        print(f"❌ 校验失败: 新代码里缺少 {keyword}")
        sys.exit(1)

# ============================================================
# 报告
# ============================================================
old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
old_total = len(original.splitlines())
new_total = len(text.splitlines())

print("=" * 60)
print("📋 v3.4 步骤 2.10 patch 报告")
print("=" * 60)
print(f"  ✓ 切换 _daily_data_sync_loop: {old_func_lines} 行 → {new_func_lines} 行")
print(f"  ✓ 切到 v3.4 daily_report.DailyReportWriter")
print(f"  ✓ 加启动钩子: sync_all_accounts (首次进循环 + 开关已开)")
print(f"  ✓ 接入开关: config.DAILY_REPORT_ENABLED (方案 C, 60秒生效)")
print(f"  ✓ 接入 reload_if_env_changed (改 .env 不用重启)")
print(f"  ✓ 移除老版 daily_data_writer / run_daily_sync 调用")
print()
print(f"  原文件: {old_total} 行, MD5={old_md5}")
print(f"  新文件: {new_total} 行, MD5={new_md5}")
print(f"  差异: {new_total - old_total:+d} 行")
print()

if APPLY:
    DST.write_text(text, encoding="utf-8")
    print(f"✅ 已写入 {DST}")
    print()
    print("下一步建议:")
    print(f"  1. python3 -m py_compile {DST}     # 语法检查")
    print(f"  2. diff tasks.py {DST} | head -150  # 人工 review")
    print(f"  3. cp tasks.py tasks.py.before-v3-4-step210.bak  # 备份")
    print(f"  4. mv {DST} tasks.py                # 替换")
    print(f"  5. .env 改 DAILY_REPORT_ENABLED=true (现在还是 false 不会动)")
    print(f"  6. docker restart tg-monitor-yyzl    # 让新 tasks.py 生效")
else:
    print("ℹ️  当前为干跑模式, 未写入文件")
    print("   确认无误后运行: python3 patch_tasks_v3_4_step210.py --apply")
