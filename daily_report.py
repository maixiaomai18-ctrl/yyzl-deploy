#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YYZL TG 监控系统 - 每日数据自动写入 (v3.4)

功能:
1. 每日定时计算每个账号的"新增/活跃"统计
2. 自动写入每日数据 sheet 的对应 tab
3. B 模式覆盖: B/C/D/J 列永远跟 DB 一致, E 列匹配键

判定规则:
- 新增: 今天 A+B 总句数 >= 4 句 + 过去 5 天 0 条消息
- 活跃: 今天 A+B 总句数 >= 4 句 + 过去 5 天 >= 1 条消息

写入规则:
- F/G 列: 数字 >= 1 才写, 0 留空
- A 列: 不动 (只第 2 行有 =TODAY() 公式)
- E 列: 外事号 (account.name), 用作匹配键
- J 列: VPS_LABEL (如 麦小麦1) - TG所在位置
- H/I/J: 不动 (人工填)

作者: 麦小麦
版本: v3.4
"""

import argparse
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import gspread

logger = logging.getLogger(__name__)

# ============ 常量 ============

TZ = ZoneInfo("Asia/Phnom_Penh")
DB_PATH = "/app/data/data.db"

COL_DATE_LETTER = "A"
COL_CENTER_LETTER = "B"
COL_BRAND_LETTER = "C"
COL_PERSON_LETTER = "D"
COL_ACCOUNT_NAME_LETTER = "E"
COL_NEW_LETTER = "F"
COL_ACTIVE_LETTER = "G"
COL_VPS_LABEL_LETTER = "J"

COL_E_INDEX = 5

DATA_START_ROW = 2
RATE_LIMIT_SECONDS = 1.2


# ============ 主类 ============

class DailyReportWriter:
    """每日数据自动写入器, 复用 SheetsWriter 的 OAuth 凭据."""

    def __init__(self, sheet_id, tab_name, vps_label, gc):
        self.sheet_id = sheet_id
        self.tab_name = tab_name
        self.vps_label = vps_label
        self.gc = gc
        self._spreadsheet = None
        self._worksheet = None
        self._last_api_call = 0.0

    def _ensure_ws(self):
        if self._worksheet is None:
            self._spreadsheet = self.gc.open_by_key(self.sheet_id)
            self._worksheet = self._spreadsheet.worksheet(self.tab_name)
            logger.info("[DailyReport] connected sheet tab: " + self.tab_name)
        return self._worksheet

    def _rate_limit(self):
        elapsed = time.time() - self._last_api_call
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_api_call = time.time()

    @staticmethod
    def compute_today_stats(db_path=DB_PATH):
        """计算今天柬时所有 (account, peer) 4 句以上组的新增/活跃统计."""
        now = datetime.now(TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        hist_start = today_start - timedelta(days=5)

        TODAY_START = today_start.strftime("%Y-%m-%d %H:%M:%S")
        TODAY_END = today_end.strftime("%Y-%m-%d %H:%M:%S")
        HIST_START = hist_start.strftime("%Y-%m-%d %H:%M:%S")

        logger.info("[DailyReport] window: today=[" + TODAY_START + ", " + TODAY_END +
                    "), hist=[" + HIST_START + ", " + TODAY_START + ")")

        results = {}
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT account_id, peer_id, COUNT(*) AS cnt FROM messages "
                "WHERE timestamp >= ? AND timestamp < ? AND deleted = 0 "
                "GROUP BY account_id, peer_id HAVING COUNT(*) >= 4",
                (TODAY_START, TODAY_END)
            ).fetchall()

            for acc_id, peer_id, cnt in rows:
                acc_row = conn.execute(
                    "SELECT name, person, center, company_brand FROM accounts WHERE id=?",
                    (acc_id,)
                ).fetchone()
                if not acc_row:
                    logger.warning("[DailyReport] account_id=%d not found, skip", acc_id)
                    continue
                acc_name, person, center, brand = acc_row

                has_hist = conn.execute(
                    "SELECT 1 FROM messages WHERE account_id=? AND peer_id=? "
                    "AND timestamp>=? AND timestamp<? AND deleted=0 LIMIT 1",
                    (acc_id, peer_id, HIST_START, TODAY_START)
                ).fetchone()

                bucket = "active" if has_hist else "new"

                if acc_id not in results:
                    results[acc_id] = {
                        "name": acc_name, "person": person,
                        "center": center, "brand": brand,
                        "new": 0, "active": 0, "detail": [],
                    }
                results[acc_id][bucket] += 1
                results[acc_id]["detail"].append((peer_id, cnt, bucket))
        finally:
            conn.close()

        return results

    @staticmethod
    def get_all_accounts(db_path=DB_PATH):
        """从 DB 拉所有登录账号."""
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, name, person, center, company_brand FROM accounts "
                "WHERE name IS NOT NULL AND name != '' ORDER BY id"
            ).fetchall()
            return [
                {"id": r[0], "name": r[1], "person": r[2],
                 "center": r[3], "company_brand": r[4]}
                for r in rows
            ]
        finally:
            conn.close()

    def ensure_account_row(self, account, dry_run=False):
        """确保某登录账号在 sheet 里有 1 行."""
        ws = self._ensure_ws()
        target_name = account["name"]

        self._rate_limit()
        e_col_values = ws.col_values(COL_E_INDEX)

        target_row = None
        for idx, val in enumerate(e_col_values[1:], start=2):
            if val == target_name:
                target_row = idx
                break

        if target_row:
            updates = [
                {"range": COL_CENTER_LETTER + str(target_row),
                 "values": [[account["center"]]]},
                {"range": COL_BRAND_LETTER + str(target_row),
                 "values": [[account["company_brand"]]]},
                {"range": COL_PERSON_LETTER + str(target_row),
                 "values": [[account["person"]]]},
                {"range": COL_VPS_LABEL_LETTER + str(target_row),
                 "values": [[self.vps_label]]},
            ]
            detail = ("update row " + str(target_row) + ": B=" + str(account["center"]) +
                      ", C=" + str(account["company_brand"]) +
                      ", D=" + str(account["person"]) +
                      ", J=" + str(self.vps_label))
            if dry_run:
                logger.info("[DRY-RUN] " + detail)
                return {"action": "update_dry", "row": target_row, "detail": detail}

            self._rate_limit()
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            logger.info(detail)
            return {"action": "update", "row": target_row, "detail": detail}

        else:
            new_row = [
                "",                          # A 日期 (留空, 只第 2 行有 =TODAY())
                account["center"],           # B 中心
                account["company_brand"],    # C 部门
                account["person"],           # D 花名
                account["name"],             # E 外事号
                "", "", "", "",              # F G H I (新增/活跃/超时/风控)
                self.vps_label,              # J TG所在位置
            ]
            detail = ("append new row: E=" + str(account["name"]) +
                      ", J=" + str(self.vps_label))
            if dry_run:
                logger.info("[DRY-RUN] " + detail + ", row content=" + str(new_row))
                return {"action": "append_dry", "row": -1, "detail": detail}

            self._rate_limit()
            result = ws.append_row(
                new_row,
                value_input_option="USER_ENTERED",
                table_range="A1:J1",
            )
            updated_range = result.get("updates", {}).get("updatedRange", "")
            new_row_num = -1
            if "!" in updated_range:
                cells = updated_range.split("!")[1]
                if ":" in cells:
                    start_cell = cells.split(":")[0]
                else:
                    start_cell = cells
                num_str = "".join(c for c in start_cell if c.isdigit())
                if num_str:
                    new_row_num = int(num_str)
            logger.info(detail + " -> row " + str(new_row_num))
            return {"action": "append", "row": new_row_num, "detail": detail}

    def sync_all_accounts(self, dry_run=False):
        """对所有 DB accounts 调用 ensure_account_row."""
        accounts = self.get_all_accounts()
        logger.info("[DailyReport] %d accounts found in DB", len(accounts))

        results = []
        for acc in accounts:
            try:
                r = self.ensure_account_row(acc, dry_run=dry_run)
                r["account_name"] = acc["name"]
                results.append(r)
            except Exception as e:
                logger.exception("ensure_account_row failed: " + str(acc.get("name")))
                results.append({
                    "action": "error",
                    "account_name": acc["name"],
                    "error": str(e),
                })
        return results

    def flush_today(self, dry_run=False):
        """
        每日定时调度入口.
        Step 1: 跑 compute_today_stats() 拿今天每个账号的 (新增, 活跃)
        Step 2: 读 E 列, 找所有数据行 (有外事号的行)
        Step 3: batch_update 一次写完: 清空 F/G/H/I, 然后写今天的 F/G (>=1 才写)
        Returns: {'data_rows': int, 'today_accounts': int, 'updates': [...], 'detail': str}
        """
        ws = self._ensure_ws()

        # Step 1: 算今天的统计
        today_stats = self.compute_today_stats()
        # 把按 account_id 索引的结果, 转成按 account_name 索引 (方便 E 列匹配)
        today_by_name = {v["name"]: v for v in today_stats.values()}
        logger.info("[flush_today] today stats: %d accounts >=4 sentence", len(today_by_name))

        # Step 2: 读 E 列, 找所有数据行
        self._rate_limit()
        e_col_values = ws.col_values(COL_E_INDEX)

        # data_rows: list of (row_num, account_name)
        data_rows = []
        for idx, val in enumerate(e_col_values[1:], start=2):  # 跳过表头第 1 行
            if val and val.strip():  # 非空
                data_rows.append((idx, val))

        logger.info("[flush_today] %d data rows in sheet", len(data_rows))

        # Step 3: 准备 batch_update payload (F/G/H/I 4 列一次更新)
        updates = []
        update_summary = []  # 用于 logging
        for row_num, name in data_rows:
            if name in today_by_name:
                v = today_by_name[name]
                f_val = v["new"] if v["new"] >= 1 else ""
                g_val = v["active"] if v["active"] >= 1 else ""
            else:
                # 这账号今天没达标 (或 DB 里根本没消息)
                f_val = ""
                g_val = ""

            # F/G 写算法结果 (>=1 才写), H 总是清空, I 风控不动
            updates.append({
                "range": "F" + str(row_num) + ":H" + str(row_num),
                "values": [[f_val, g_val, ""]],
            })
            update_summary.append((row_num, name, f_val, g_val))

        # 打印每行操作计划
        for row_num, name, f_val, g_val in update_summary:
            f_disp = "" if f_val == "" else str(f_val)
            g_disp = "" if g_val == "" else str(g_val)
            logger.info("  row %d (%s): F='%s' G='%s' H='' (I 风控不动)",
                        row_num, name, f_disp, g_disp)

        if dry_run:
            return {
                "action": "flush_dry",
                "data_rows": len(data_rows),
                "today_accounts": len(today_by_name),
                "updates_planned": len(updates),
                "summary": update_summary,
            }

        # 真写
        if updates:
            self._rate_limit()
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            logger.info("[flush_today] batch_update done: %d ranges", len(updates))

        return {
            "action": "flush",
            "data_rows": len(data_rows),
            "today_accounts": len(today_by_name),
            "updates_done": len(updates),
            "summary": update_summary,
        }

    def dry_run_inspect(self):
        """只读检查: 验证 sheet 连得上 + 算法跑得通."""
        ws = self._ensure_ws()
        info = {
            "tab_title": ws.title,
            "tab_id": ws.id,
            "row_count": ws.row_count,
            "col_count": ws.col_count,
            "vps_label": self.vps_label,
        }
        try:
            header = ws.row_values(1)
            info["header"] = header[:10]
        except Exception as e:
            info["header_error"] = str(e)

        stats = self.compute_today_stats()
        info["today_stats_count"] = len(stats)
        info["today_stats"] = {
            acc_id: {
                "name": v["name"], "person": v["person"],
                "center": v["center"], "brand": v["brand"],
                "new": v["new"], "active": v["active"],
            }
            for acc_id, v in stats.items()
        }
        return info


# ============ 命令行入口 ============

def main():
    parser = argparse.ArgumentParser(description="YYZL Daily Report v3.4")
    parser.add_argument("command", choices=["dry-run", "ensure-rows", "ensure-rows-dry",
                                              "flush-today", "flush-today-dry"])
    parser.add_argument("--tab", default=None, help="override tab name")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from dotenv import load_dotenv
    load_dotenv("/app/.env")

    sheet_id = os.environ.get("DAILY_SHEET_ID")
    vps_label = os.environ.get("VPS_LABEL", "未设置")
    if args.tab:
        tab_name = args.tab
    else:
        tab_name = vps_label.rstrip("0123456789")

    if not sheet_id:
        print("ERROR: DAILY_SHEET_ID not set")
        return 1

    print("DAILY_SHEET_ID = " + sheet_id)
    print("VPS_LABEL      = " + vps_label)
    print("Tab name       = " + tab_name)
    print("Command        = " + args.command)
    print()

    from sheets import SheetsWriter
    sw = SheetsWriter()
    print("SheetsWriter loaded, gc=" + str(sw.gc))
    print()

    drw = DailyReportWriter(sheet_id, tab_name, vps_label, sw.gc)

    if args.command == "dry-run":
        result = drw.dry_run_inspect()
        print("=" * 60)
        print("Dry-run result:")
        print("=" * 60)
        print("Tab: " + str(result['tab_title']) + " (id=" + str(result['tab_id']) + ")")
        print("rows=" + str(result['row_count']) + ", cols=" + str(result['col_count']))
        print("Header: " + str(result.get('header', 'N/A')))
        print("VPS label: " + str(result['vps_label']))
        print()
        print("Today >=4 sentence accounts: " + str(result['today_stats_count']))
        for acc_id, v in result["today_stats"].items():
            print("  [" + str(acc_id) + "] " + v['name'] +
                  " (" + v['person'] + "/" + v['center'] + "/" + v['brand'] +
                  "): new=" + str(v['new']) + ", active=" + str(v['active']))

    elif args.command in ("ensure-rows", "ensure-rows-dry"):
        is_dry = args.command == "ensure-rows-dry"
        print("=" * 60)
        if is_dry:
            print("ensure_account_row (DRY-RUN, no write)")
        else:
            print("ensure_account_row (REAL WRITE)")
        print("=" * 60)
        results = drw.sync_all_accounts(dry_run=is_dry)
        print()
        print("=" * 60)
        print("Result:")
        print("=" * 60)
        for r in results:
            print("[" + r['action'] + "] " + str(r.get('account_name', '?')) + ": " +
                  str(r.get('detail') or r.get('error', '')))
        print()
        n_update = sum(1 for r in results if r['action'] in ('update', 'update_dry'))
        n_append = sum(1 for r in results if r['action'] in ('append', 'append_dry'))
        n_error = sum(1 for r in results if r['action'] == 'error')
        print("Total: update=" + str(n_update) + ", append=" + str(n_append) +
              ", error=" + str(n_error))

    elif args.command in ("flush-today", "flush-today-dry"):
        is_dry = args.command == "flush-today-dry"
        print("=" * 60)
        if is_dry:
            print("flush_today (DRY-RUN, no write)")
        else:
            print("flush_today (REAL WRITE)")
        print("=" * 60)
        result = drw.flush_today(dry_run=is_dry)
        print()
        print("=" * 60)
        print("Result:")
        print("=" * 60)
        print("Action:           " + result["action"])
        print("Data rows:        " + str(result["data_rows"]))
        print("Today accounts:   " + str(result["today_accounts"]))
        if is_dry:
            print("Updates planned:  " + str(result["updates_planned"]))
        else:
            print("Updates done:     " + str(result["updates_done"]))
        print()
        print("Per-row plan:")
        for row_num, name, f_val, g_val in result["summary"]:
            f_disp = "(empty)" if f_val == "" else str(f_val)
            g_disp = "(empty)" if g_val == "" else str(g_val)
            print("  row " + str(row_num) + " (" + name + "): " +
                  "F=" + f_disp + ", G=" + g_disp + ", H=(cleared), I=(unchanged 风控)")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
