#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.4 步骤 2.10 patch_tasks.py (v2 - 行级锚点版)
策略: 不用正则, 改用行级扫描定位函数边界, 最稳健
用法:
    python3 patch_tasks_v3_4_step210.py            # 干跑
    python3 patch_tasks_v3_4_step210.py --apply    # 实际写入
"""
import sys
import hashlib
from pathlib import Path

SRC = Path("tasks.py")
DST = Path("tasks.py.v3-4-step210-new")
APPLY = "--apply" in sys.argv

if not SRC.exists():
    print(f"找不到 {SRC}")
    sys.exit(1)

original = SRC.read_text(encoding="utf-8")
lines = original.splitlines(keepends=True)

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
        ensure_done = False

        while self._running:
            try:
                config.reload_if_env_changed()

                if not config.DAILY_REPORT_ENABLED:
                    await asyncio.sleep(60)
                    continue

                if not ensure_done:
                    try:
                        from daily_report import DailyReportWriter
                        drw = DailyReportWriter(sheet_id, tab_name, vps_label, self.sheets.gc)
                        result = drw.sync_all_accounts(dry_run=False)
                        logger.info(f"[daily_sync_loop] 启动 ensure_account_row 完成: {result}")
                        ensure_done = True
                    except Exception as e:
                        logger.error(f"[daily_sync_loop] 启动 ensure 失败: {e}", exc_info=True)
                        ensure_done = True

                now_kh = datetime.now(TZ_KH)
                wd = now_kh.weekday()
                today_kh = now_kh.date()

                if wd == 6:
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
                        last_synced_date = today_kh

            except Exception as e:
                logger.error(f"[daily_sync_loop] 外层异常: {e}", exc_info=True)

            await asyncio.sleep(60)

'''

# 第 1 步: 找起始行
START_PREFIX = "    async def _daily_data_sync_loop"
start_idx = None
for i, line in enumerate(lines):
    if line.startswith(START_PREFIX):
        start_idx = i
        break

if start_idx is None:
    print(f"找不到起始行 '{START_PREFIX}'")
    print("尝试列出所有 _daily 相关定义:")
    for i, line in enumerate(lines):
        if "_daily" in line and "def " in line:
            print(f"   line {i+1}: {line.rstrip()}")
    sys.exit(1)

print(f"找到起始行: line {start_idx+1}")

# 第 2 步: 找下一个同级定义
end_idx = None
for j in range(start_idx + 1, len(lines)):
    line = lines[j]
    if line.startswith("    async def ") or line.startswith("    def "):
        end_idx = j
        break
    if line.startswith("class ") or line.startswith("def ") or line.startswith("async def "):
        end_idx = j
        break

if end_idx is None:
    end_idx = len(lines)
    print(f"  函数是文件最后一个, end_idx 设为文件末尾 ({end_idx})")
else:
    print(f"找到下一个同级定义: line {end_idx+1}: {lines[end_idx].rstrip()[:80]}")

old_func_lines = lines[start_idx:end_idx]
old_func_count = len(old_func_lines)
new_func_count = len(NEW_FUNC.splitlines())

print(f"  旧函数: {old_func_count} 行 (line {start_idx+1} 到 line {end_idx})")
print(f"  新函数: {new_func_count} 行")

# 第 3 步: 替换
new_lines = lines[:start_idx] + [NEW_FUNC] + lines[end_idx:]
text = "".join(new_lines)

# 安全校验
checks_must_remove = [
    ("daily_data_writer", "仍有 daily_data_writer 引用"),
    ("run_daily_sync", "仍有 run_daily_sync 引用"),
]
for keyword, msg in checks_must_remove:
    if keyword in text:
        print(f"校验失败: {msg}")
        for i, line in enumerate(text.splitlines(), 1):
            if keyword in line:
                print(f"   line {i}: {line}")
        sys.exit(1)

checks_must_have = [
    "from daily_report import DailyReportWriter",
    "drw.sync_all_accounts",
    "drw.flush_today",
    "config.DAILY_REPORT_ENABLED",
    "config.reload_if_env_changed",
]
for keyword in checks_must_have:
    if keyword not in text:
        print(f"校验失败: 新代码里缺少 {keyword}")
        sys.exit(1)

# 报告
old_md5 = hashlib.md5(original.encode("utf-8")).hexdigest()
new_md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
old_total = len(original.splitlines())
new_total = len(text.splitlines())

print()
print("=" * 60)
print("v3.4 步骤 2.10 patch 报告 (v2 行级锚点)")
print("=" * 60)
print(f"  切换 _daily_data_sync_loop: {old_func_count} 行 -> {new_func_count} 行")
print(f"  切到 v3.4 daily_report.DailyReportWriter")
print(f"  加启动钩子 sync_all_accounts")
print(f"  接入开关 config.DAILY_REPORT_ENABLED (方案 C)")
print(f"  接入 reload_if_env_changed")
print(f"  移除老版 daily_data_writer / run_daily_sync")
print()
print(f"  原文件: {old_total} 行, MD5={old_md5}")
print(f"  新文件: {new_total} 行, MD5={new_md5}")
print(f"  差异: {new_total - old_total:+d} 行")
print()

if APPLY:
    DST.write_text(text, encoding="utf-8")
    print(f"已写入 {DST}")
    print()
    print("下一步:")
    print(f"  1. python3 -m py_compile {DST}")
    print(f"  2. diff tasks.py {DST} | head -150")
    print(f"  3. cp tasks.py tasks.py.before-v3-4-step210.bak")
    print(f"  4. mv {DST} tasks.py")
    print(f"  5. .env 改 DAILY_REPORT_ENABLED=true (可选)")
    print(f"  6. docker restart tg-monitor-yyzl")
else:
    print("当前为干跑模式, 未写入文件")
    print("确认无误后运行: python3 patch_tasks_v3_4_step210.py --apply")
