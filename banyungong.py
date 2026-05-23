# -*- coding: utf-8 -*-
"""YYZL 搬运工 v4:批量插入 + 进度显示 + 失败重试 + 删除线检测(已删除消息)"""
import re
import time
import gspread
import psycopg2
from psycopg2.extras import execute_values
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ========== 配置 ==========
INDEX_SHEET_ID = "1d1Sj_JX5xuzcyCvdbzA3aBygd6xXQsMJcQAY_J_jqyQ"
ONLY_OPERATOR = None   # None=搬全部;设"季霖"则只搬季霖

DB = dict(host="localhost", dbname="yyzl", user="yyzl_user", password="YyzlData2026")

ADV_NAME_ROW = 6
DATA_START_ROW = 7
FIRST_DATA_COL = 3
COLS_PER_ADV = 3
IGNORE_KEYWORDS = ['预警', '监听', '工作表1', 'Sheet1', '监察日志', '日报', '周报']
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

TAB_SLEEP = 1.2
RETRY_TIMES = 3
RETRY_WAIT = 8


def is_ignored_tab(name):
    name = (name or '').strip()
    if not name:
        return True
    return any(kw in name for kw in IGNORE_KEYWORDS)


def extract_sheet_id(url):
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url or '')
    return m.group(1) if m else None


def read_tab_with_retry(ws):
    """读一个tab的文字,失败自动重试"""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            return ws.get_all_values()
        except Exception as e:
            if attempt < RETRY_TIMES:
                print(f"      (文字重试 {attempt}/{RETRY_TIMES}) 等{RETRY_WAIT}秒...", flush=True)
                time.sleep(RETRY_WAIT)
            else:
                print(f"      文字读取最终失败: {str(e)[:50]}", flush=True)
                return None
    return None


def cell_has_strike(cell):
    """判断一个单元格是否有删除线"""
    if not cell:
        return False
    ef = cell.get('effectiveFormat', {}).get('textFormat', {})
    if ef.get('strikethrough'):
        return True
    for r in cell.get('textFormatRuns', []):
        if r.get('format', {}).get('strikethrough'):
            return True
    return False


def read_strike_grid(service, sheet_id, tab_name, n_rows, n_cols):
    """读整个tab的删除线格式,返回 set{(row_idx从0, col_idx从0)} 表示哪些单元格被删除。失败返回None"""
    if n_rows < DATA_START_ROW or n_cols < FIRST_DATA_COL:
        return set()
    def col_letter(n):
        s = ''
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s
    rng = f"'{tab_name}'!A1:{col_letter(n_cols)}{n_rows}"
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            result = service.spreadsheets().get(
                spreadsheetId=sheet_id, ranges=[rng],
                fields="sheets/data/rowData/values(effectiveFormat/textFormat/strikethrough,textFormatRuns/format/strikethrough)"
            ).execute()
            sheets = result.get('sheets', [])
            if not sheets:
                return set()
            rows = sheets[0].get('data', [{}])[0].get('rowData', [])
            struck = set()
            for ri, row in enumerate(rows):
                cells = row.get('values', [])
                for ci, cell in enumerate(cells):
                    if cell_has_strike(cell):
                        struck.add((ri, ci))
            return struck
        except Exception as e:
            if attempt < RETRY_TIMES:
                print(f"      (格式重试 {attempt}/{RETRY_TIMES}) 等{RETRY_WAIT}秒...", flush=True)
                time.sleep(RETRY_WAIT)
            else:
                print(f"      格式读取失败(删除标记跳过): {str(e)[:40]}", flush=True)
                return None
    return None


def main():
    print("=== 搬运工 v4 启动(含删除线检测) ===", flush=True)
    creds = Credentials.from_service_account_file("key.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    service = build('sheets', 'v4', credentials=creds)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    print("读取 vps 索引表...", flush=True)
    index_ss = client.open_by_key(INDEX_SHEET_ID)
    index_rows = index_ss.get_worksheet(0).get_all_values()

    targets = []
    for row in index_rows[1:]:
        if len(row) < 8:
            continue
        operator = (row[0] or '').strip()
        dept = (row[2] or '').strip()
        link = (row[7] or '').strip()
        if not operator or not link:
            continue
        if ONLY_OPERATOR and operator != ONLY_OPERATOR:
            continue
        sid = extract_sheet_id(link)
        if sid:
            targets.append((operator, dept, sid))

    print(f"找到 {len(targets)} 个要搬运的表", flush=True)
    total_acc = total_adv = total_msg = total_del = 0
    failed_tabs = []

    for idx, (operator, dept, sid) in enumerate(targets, 1):
        try:
            ss = client.open_by_key(sid)
        except Exception as e:
            print(f"[{idx}/{len(targets)}] 跳过 {dept}: {str(e)[:50]}", flush=True)
            continue
        print(f"[{idx}/{len(targets)}] 处理 {operator}/{dept} ...", flush=True)

        try:
            worksheets = ss.worksheets()
        except Exception as e:
            print(f"    读工作表列表失败: {str(e)[:50]}", flush=True)
            continue

        for ws in worksheets:
            tab_name = ws.title
            if is_ignored_tab(tab_name):
                continue
            data = read_tab_with_retry(ws)
            time.sleep(TAB_SLEEP)
            if data is None:
                failed_tabs.append(f"{dept}/{tab_name}")
                print(f"    [tab跳过] {tab_name}", flush=True)
                continue
            if len(data) < ADV_NAME_ROW:
                continue
            row5 = data[4] if len(data) >= 5 else []
            a5 = (row5[0] if len(row5) > 0 else '').strip()
            b5 = (row5[1] if len(row5) > 1 else '').strip()
            if not (a5 == 'A' or '外事号' in b5):
                continue

            n_rows = len(data)
            n_cols = max((len(r) for r in data), default=0)

            struck = read_strike_grid(service, sid, tab_name, n_rows, n_cols)
            time.sleep(TAB_SLEEP)
            if struck is None:
                struck = set()

            cur.execute("""
                INSERT INTO accounts (operator, dept, sheet_id, account_name)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (sheet_id, account_name) DO UPDATE SET operator=EXCLUDED.operator, dept=EXCLUDED.dept, updated_at=now()
                RETURNING id
            """, (operator, dept, sid, tab_name))
            account_id = cur.fetchone()[0]
            total_acc += 1

            adv_row = data[ADV_NAME_ROW - 1] if len(data) >= ADV_NAME_ROW else []
            last_col = n_cols

            tab_adv = tab_msg = tab_del = 0
            col = FIRST_DATA_COL
            while col <= last_col:
                name = (adv_row[col - 1] if len(adv_row) >= col else '').strip()
                if name == '广告主':
                    col += COLS_PER_ADV
                    continue
                msgs = []
                for r in range(DATA_START_ROW - 1, len(data)):
                    rowdata = data[r]
                    content = (rowdata[col - 1] if len(rowdata) >= col else '').strip()
                    if not content:
                        continue
                    t = (rowdata[col - 3] if len(rowdata) >= col - 2 else '').strip()
                    sender = (rowdata[col - 2] if len(rowdata) >= col - 1 else '').strip()
                    is_del = (r, col - 1) in struck or (r, col - 3) in struck
                    if is_del:
                        tab_del += 1
                    msgs.append((r + 1, t or None, sender, content, is_del))
                if not name and not msgs:
                    col += COLS_PER_ADV
                    continue
                if not name:
                    name = '(未命名广告主)'

                cur.execute("""
                    INSERT INTO advertisers (account_id, col, adv_name)
                    VALUES (%s,%s,%s)
                    ON CONFLICT (account_id, col) DO UPDATE SET adv_name=EXCLUDED.adv_name, updated_at=now()
                    RETURNING id
                """, (account_id, col, name))
                adv_id = cur.fetchone()[0]
                total_adv += 1
                tab_adv += 1

                if msgs:
                    rows_to_insert = [(adv_id, rn, t, s, c, d) for (rn, t, s, c, d) in msgs]
                    execute_values(cur, """
                        INSERT INTO messages (advertiser_id, row_num, msg_time, sender, content, deleted)
                        VALUES %s
                        ON CONFLICT (advertiser_id, row_num) DO UPDATE SET content=EXCLUDED.content, msg_time=EXCLUDED.msg_time, sender=EXCLUDED.sender, deleted=EXCLUDED.deleted
                    """, rows_to_insert)
                    total_msg += len(msgs)
                    tab_msg += len(msgs)
                col += COLS_PER_ADV

            conn.commit()
            dmark = f" 🔴{tab_del}删除" if tab_del else ""
            print(f"    ✓ {tab_name}: {tab_adv}广告主 {tab_msg}消息{dmark}", flush=True)
            total_del += tab_del

    conn.commit()
    print(f"\n=== 搬运完成 ===", flush=True)
    print(f"外事号: {total_acc} | 广告主: {total_adv} | 消息: {total_msg} | 已删除: {total_del}", flush=True)
    if failed_tabs:
        print(f"仍失败的tab({len(failed_tabs)}个): {', '.join(failed_tabs)}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
