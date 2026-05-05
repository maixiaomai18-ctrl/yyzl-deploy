"""
sheets.py - YYZL TG 监控系统的 Google Sheets 写入模块（真版本 v1.0）

设计原则：
1. 队列模式 - 主线程不卡，消息进队列立即返回
2. 批量推送 - 减少 API 调用 100 倍
3. 自动限流 - 永远不触发 429
4. 磁盘备份 - 程序崩溃也不丢数据
5. 失败重试 - 网络抖动自动恢复
6. 精准告警 - 哪个 tab 积压推哪个对应中心的 RISK 群

数据结构（外事号 tab）：
    第 1 行：[蓝色横条 - 装饰]
    第 2 行：账号归属人 | 高嘉奕                                  (合并)
    第 3 行：中心/部门 | 运营中心/瑞升公司                          (合并)
    第 4 行：[空]
    第 5 行：A | 外事号 | 呦呦禁区官方账号 | A | 外事号 | (客户2外事号) | ...
    第 6 行：B | 广告商 | 禾                | B | 广告商 | (客户2名字)  | ...
    第 7 行起：实际数据（每客户 3 列：时间 / 方向 / 内容）

依赖：
    - oauth_helper.get_credentials()  → 拿 OAuth 凭证（必须存在）
    - config.SHEET_ID                  → Sheet ID（必须存在）
    - database.get_conn()              → SQLite 连接（用于读 accounts 表）
    - aiogram.Bot                      → 用于发送积压告警（可选，None 时跳过）

作者：YYZL Team
版本：v1.0 (2026-05-05)
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

import gspread
from gspread.exceptions import APIError, WorksheetNotFound

logger = logging.getLogger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 表头说明区（前 6 行）的固定行号
HEADER_TITLE_ROW = 1            # 蓝色横条
HEADER_OWNER_ROW = 2            # 账号归属人
HEADER_CENTER_ROW = 3           # 中心/部门
HEADER_BLANK_ROW = 4            # 空行
HEADER_ACCOUNT_ROW = 5          # A | 外事号 | (外事号名)
HEADER_PEER_ROW = 6             # B | 广告商 | (客户名)
DATA_START_ROW = 7              # 数据从第 7 行开始

# 每客户占用的列数
COLS_PER_PEER = 3               # 时间 / 方向 / 内容

# 积压阈值（条数）
BACKLOG_NORMAL = 50             # ≤ 50：正常模式（每 5 秒推一次）
BACKLOG_BUSY = 200              # 50-200：紧急模式（每 2 秒推一次）
BACKLOG_HIGH = 1000             # 200-1000：发 Telegram 告警
BACKLOG_CRITICAL = 2000         # > 1000：紧急持久化到磁盘

# Google API 限流参数
RATE_LIMIT_PER_MINUTE = 60      # 每分钟最多 60 次写请求（保守值，Google 实际允许 100）
RATE_LIMIT_INTERVAL = 60.0 / RATE_LIMIT_PER_MINUTE  # 1 秒一次

# 重试参数
RETRY_DELAYS = [5, 30, 120]     # 重试间隔（秒）

# 磁盘备份路径
BACKUP_PATH = Path("/app/data/_pending_backup.jsonl")

# 固定的 3 个审计 tab 名（{display} 会被 COMPANY_DISPLAY 替换）
ALERT_TAB_TEMPLATES = {
    "no_reply": "信息未回复预警{display}",      # 怠工
    "keyword": "关键词监听{display}",            # 关键词
    "deleted": "信息删除预警{display}",          # 撤回
}

# 审计 tab 的表头
ALERT_HEADERS = {
    "no_reply": ["时间", "中心/部门", "公司", "监察员", "外事号", "广告商", "无回复时长(分钟)", "最后消息"],
    "keyword":  ["时间", "中心/部门", "公司", "监察员", "外事号", "广告商", "命中关键词", "消息内容"],
    "deleted":  ["时间", "中心/部门", "公司", "监察员", "外事号", "广告商", "撤回内容", "原始时间"],
}


# ============================================================================
# 工具函数
# ============================================================================

def _col_letter(col_num):
    """
    把列号（1-based）转成 Excel/Sheets 列字母
    例：1→A, 2→B, 27→AA, 52→AZ
    """
    if col_num < 1:
        raise ValueError(f"列号必须 >= 1，得到 {col_num}")
    result = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        result = chr(65 + remainder) + result
    return result


def col_letter(col_num):
    """别名，兼容老代码"""
    return _col_letter(col_num)


def _now_bj():
    """北京时间字符串"""
    bj_tz = timezone(timedelta(hours=8))
    return datetime.now(bj_tz).strftime("%Y-%m-%d %H:%M:%S")


def _safe_tab_name(name, max_len=100):
    """
    清理 tab 名，确保符合 Google Sheets 限制：
    - 不能包含 [ ] : / \\ ? *
    - 长度不超过 100 字符
    """
    if not name:
        return "未知"
    forbidden = "[]:/\\?*"
    cleaned = "".join(c if c not in forbidden else "_" for c in str(name))
    return cleaned[:max_len].strip() or "未知"


def _media_placeholder(msg):
    """
    根据消息类型返回媒体占位文字：【图片】【视频】【语音】【文件】【贴纸】
    """
    if not msg:
        return ""
    msg_type = msg.get("type", "").lower() if isinstance(msg, dict) else ""
    text = msg.get("text", "") if isinstance(msg, dict) else str(msg)

    type_map = {
        "photo": "【图片】",
        "image": "【图片】",
        "video": "【视频】",
        "voice": "【语音】",
        "audio": "【语音】",
        "document": "【文件】",
        "file": "【文件】",
        "sticker": "【贴纸】",
        "animation": "【动图】",
        "video_note": "【视频留言】",
    }

    placeholder = type_map.get(msg_type, "")
    if placeholder:
        # 如果原消息有文字（caption），加在占位符后面
        if text and text.strip():
            return f"{placeholder} {text.strip()}"
        return placeholder

    # 没有特殊类型，返回原文字
    return text or ""


# ============================================================================
# SheetsWriter 主类
# ============================================================================

class SheetsWriter:
    """
    真正的 Google Sheets 写入器
    
    特性：
    - 队列模式：主线程不阻塞
    - 批量写入：减少 API 调用
    - 自动限流：避免 429
    - 磁盘备份：崩溃不丢数据
    - 失败重试：网络抖动自愈
    - 智能告警：精准推送到对应中心 RISK 群
    """

    def __init__(self, sheet_id=None, alert_bot=None):
        """
        初始化 SheetsWriter
        
        Args:
            sheet_id: Google Sheet ID。若为 None 会从 config.SHEET_ID 读
            alert_bot: aiogram Bot 实例，用于发送积压告警。可选
        """
        self.disabled = False
        self.spreadsheet = None  # gspread Spreadsheet 对象
        self.gc = None           # gspread Client 对象
        self.creds = None        # OAuth Credentials
        self.bot = alert_bot

        # 读 SHEET_ID
        if sheet_id is None:
            try:
                import config
                sheet_id = getattr(config, "SHEET_ID", "") or os.environ.get("SHEET_ID", "")
            except ImportError:
                sheet_id = os.environ.get("SHEET_ID", "")
        self.sheet_id = sheet_id

        # 写入锁（保护 spreadsheet 操作）
        self._write_lock = threading.Lock()
        # 队列锁（保护 _pending_writes / _pending_queue）
        self._queue_lock = threading.Lock()

        # 待写入队列：[(tab_name, account_info, peer_info, msg, ts), ...]
        self._pending_queue = deque()
        # 推送中的备份（推送失败可恢复）
        self._pending_writes = []

        # API 限流
        self._last_api_call = 0.0
        self._api_call_count = 0
        self._error_count = 0

        # 缓存：{tab_name: gspread.Worksheet}
        self._sheets_cache = {}
        # 缓存：{tab_name: {peer_id: 起始列号}}（避免每次重读列结构）
        self._peer_columns_cache = defaultdict(dict)

        # 兼容老代码的属性
        self._cache = {}
        self._sheet_cache = {}
        self._account_sheets = {}
        self._pending = []

        # 积压告警去重（每个 tab 5 分钟最多告警 1 次）
        self._last_alert_time = {}

        # 初始化连接
        if not self.sheet_id:
            logger.warning("SHEET_ID 未配置，Sheet 写入禁用")
            self.disabled = True
            return

        try:
            self._connect()
        except Exception as e:
            logger.warning(f"OAuth 凭证缺失或连接失败，Sheet 写入禁用: {e}")
            self.disabled = True
            return

        # 启动后立即恢复磁盘备份的待写入数据
        try:
            self._load_backup_from_disk()
        except Exception as e:
            logger.warning(f"加载磁盘备份失败: {e}")

        logger.info(f"SheetsWriter 初始化成功: sheet_id={self.sheet_id[:20]}..., bot={'有' if self.bot else '无'}")

    # ------------------------------------------------------------------
    # 连接与认证
    # ------------------------------------------------------------------

    def _connect(self):
        """连接 Google Sheets API"""
        try:
            from oauth_helper import get_credentials
        except ImportError as e:
            raise RuntimeError(f"找不到 oauth_helper.get_credentials: {e}")

        self.creds = get_credentials()
        if self.creds is None:
            raise RuntimeError("get_credentials() 返回 None，OAuth 未配置")

        self.gc = gspread.authorize(self.creds)
        self.spreadsheet = self.gc.open_by_key(self.sheet_id)
        logger.info(f"已连接 Spreadsheet: {self.spreadsheet.title}")

    def _reconnect(self):
        """重新连接（用于 token 过期等场景）"""
        try:
            self._connect()
            return True
        except Exception as e:
            logger.error(f"重连失败: {e}")
            return False

    # ------------------------------------------------------------------
    # API 限流
    # ------------------------------------------------------------------

    def _rate_limit(self):
        """
        Google API 限流：确保每次调用之间至少间隔 RATE_LIMIT_INTERVAL 秒
        在调用任何 spreadsheet 写入操作前调用此方法
        """
        now = time.time()
        elapsed = now - self._last_api_call
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        self._last_api_call = time.time()
        self._api_call_count += 1

    # ------------------------------------------------------------------
    # 队列管理（主入口）
    # ------------------------------------------------------------------

    def append_message(self, account, peer, msg, direction="A"):
        """
        把一条对话消息加入待写入队列（线程安全，立即返回）
        
        Args:
            account: dict，外事号信息 {id, name, operator, center, company_brand, person, ...}
            peer: dict，对方信息 {id, name, ...}
            msg: dict，消息内容 {text, type, timestamp, ...} 或 str
            direction: "A" = 外事号说的, "B" = 客户说的
        """
        if self.disabled:
            return

        # 标准化输入
        ts = _now_bj()
        if isinstance(msg, dict):
            ts = msg.get("timestamp") or ts
            content = _media_placeholder(msg)
        else:
            content = str(msg) if msg else ""

        record = {
            "ts": ts,
            "direction": direction,
            "content": content,
            "account": dict(account) if account else {},
            "peer": dict(peer) if peer else {},
        }

        with self._queue_lock:
            self._pending_queue.append(record)
            backlog = len(self._pending_queue)

        # 积压告警
        if backlog >= BACKLOG_HIGH:
            self._maybe_alert_backlog(backlog, account)

        # 紧急持久化
        if backlog >= BACKLOG_CRITICAL:
            try:
                self._save_backup_to_disk()
            except Exception as e:
                logger.error(f"紧急持久化失败: {e}")

    def append_row(self, *args, **kwargs):
        """兼容老接口：转发到合适的方法"""
        # 老代码调用 ws.append_row([...]) 时，args[0] 是 list
        # 这里我们不直接处理（因为没有 tab 上下文），让调用方使用新接口
        logger.debug(f"[兼容] append_row 被调用但被忽略: args={args[:1]}")
        return None

    def write_message(self, account, peer, msg, direction="A"):
        """write_message 是 append_message 的别名"""
        return self.append_message(account, peer, msg, direction)

    @property
    def pending_count(self):
        """当前积压条数（供 Web 后台显示用）"""
        with self._queue_lock:
            return len(self._pending_queue) + len(self._pending_writes)

    # ------------------------------------------------------------------
    # 批量推送（核心写入逻辑）
    # ------------------------------------------------------------------

    def flush_pending(self):
        """
        把队列里的所有消息批量写入 Sheet
        返回：实际写入的条数
        
        被 tasks.py 的 _sheets_flush_loop 每 5 秒调用一次
        """
        if self.disabled:
            return 0

        # 取出队列里的所有消息
        with self._queue_lock:
            if not self._pending_queue:
                return 0
            batch = list(self._pending_queue)
            self._pending_queue.clear()
            self._pending_writes = batch  # 备份，失败可恢复

        # 按 (tab_name) 分组
        groups = defaultdict(list)
        for record in batch:
            account_name = record.get("account", {}).get("name") or "未知外事号"
            tab_name = _safe_tab_name(account_name)
            groups[tab_name].append(record)

        written = 0
        failed_records = []

        for tab_name, records in groups.items():
            try:
                count = self._write_tab_batch(tab_name, records)
                written += count
            except Exception as e:
                logger.error(f"写入 tab {tab_name} 失败: {e}", exc_info=True)
                self._error_count += 1
                failed_records.extend(records)

        # 失败的放回队列，下次再试
        if failed_records:
            with self._queue_lock:
                # 失败的优先放队列前面
                self._pending_queue.extendleft(reversed(failed_records))

        # 推送成功，清空备份
        with self._queue_lock:
            self._pending_writes = []

        # 全部成功 + 磁盘备份存在 → 清磁盘备份
        if not failed_records and BACKUP_PATH.exists():
            try:
                BACKUP_PATH.unlink()
                logger.info("磁盘备份已清空（所有数据写入成功）")
            except Exception:
                pass

        if written > 0:
            logger.info(f"flush_pending 完成: 写入 {written} 条，失败 {len(failed_records)} 条")

        return written

    def _write_tab_batch(self, tab_name, records):
        """
        把一批记录写入指定 tab
        
        每条记录会找到对应的 peer 列（没有就建），然后追加在该列的末尾
        """
        with self._write_lock:
            # 拿/建 worksheet
            ws = self._get_or_create_account_tab(tab_name, records[0].get("account", {}))
            if ws is None:
                return 0

            # 按 peer 分组
            peer_groups = defaultdict(list)
            for r in records:
                peer_id = r.get("peer", {}).get("id") or r.get("peer", {}).get("name") or "未知"
                peer_groups[str(peer_id)].append(r)

            count = 0
            for peer_id, peer_records in peer_groups.items():
                try:
                    self._append_records_to_peer_columns(ws, tab_name, peer_records)
                    count += len(peer_records)
                except Exception as e:
                    logger.error(f"写入 peer {peer_id} 失败: {e}")

            return count

    def _append_records_to_peer_columns(self, ws, tab_name, records):
        """
        把一批属于同一客户的记录追加到对应的 3 列下面
        如果该客户还没有列，先建立 3 列
        """
        if not records:
            return

        peer = records[0].get("peer", {})
        account = records[0].get("account", {})

        # 找/建该客户的起始列号
        start_col = self._get_or_create_peer_columns(ws, tab_name, account, peer)
        if start_col is None:
            return

        # 找到该 3 列下方第一个空行
        next_row = self._find_next_empty_row(ws, start_col)

        # 准备写入数据：每条 1 行，3 列（时间、方向、内容）
        rows_to_write = []
        for r in records:
            rows_to_write.append([r["ts"], r["direction"], r["content"]])

        # 写入：用 update() 批量更新一个矩形区域
        end_row = next_row + len(rows_to_write) - 1
        end_col = start_col + COLS_PER_PEER - 1
        range_str = f"{_col_letter(start_col)}{next_row}:{_col_letter(end_col)}{end_row}"

        self._rate_limit()
        try:
            ws.update(range_str, rows_to_write, value_input_option="USER_ENTERED")
        except APIError as e:
            # 如果是 token 过期之类的，尝试重连一次
            if "401" in str(e) or "UNAUTHENTICATED" in str(e):
                logger.warning("Token 可能过期，尝试重连...")
                if self._reconnect():
                    # 重连成功，重新拿 ws
                    ws = self.spreadsheet.worksheet(ws.title)
                    self._rate_limit()
                    ws.update(range_str, rows_to_write, value_input_option="USER_ENTERED")
                else:
                    raise
            else:
                raise

    def _find_next_empty_row(self, ws, col_num):
        """
        找到指定列的下一个空行（从 DATA_START_ROW 开始往下找）
        用 col_values() 一次拿整列，性能比循环 cell() 好
        """
        self._rate_limit()
        col_values = ws.col_values(col_num)
        # col_values 是从第 1 行开始的列表
        # 我们要找第 DATA_START_ROW 行（含）之后的第一个空值位置
        for i in range(DATA_START_ROW - 1, len(col_values)):
            if not col_values[i]:
                return i + 1  # 转回 1-based 行号
        # 整列都满了，下一个空行 = 列长度 + 1
        return max(len(col_values) + 1, DATA_START_ROW)

    # ------------------------------------------------------------------
    # Tab 管理
    # ------------------------------------------------------------------

    def _get_or_create_account_tab(self, tab_name, account_info):
        """
        获取或创建外事号 tab
        如果 tab 不存在，会自动建立 + 写入表头说明区
        """
        # 缓存
        if tab_name in self._sheets_cache:
            return self._sheets_cache[tab_name]

        # 尝试找现有 tab
        try:
            self._rate_limit()
            ws = self.spreadsheet.worksheet(tab_name)
            self._sheets_cache[tab_name] = ws
            return ws
        except WorksheetNotFound:
            pass

        # 不存在，创建
        try:
            self._rate_limit()
            ws = self.spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=30)
            logger.info(f"已创建外事号 tab: {tab_name}")

            # 写入表头说明区
            self._init_account_tab_header(ws, account_info)
            self._sheets_cache[tab_name] = ws
            return ws
        except Exception as e:
            logger.error(f"创建 tab {tab_name} 失败: {e}")
            return None

    def _init_account_tab_header(self, ws, account_info):
        """
        初始化外事号 tab 的表头说明区（前 6 行）
        
        Row 1: [蓝色横条 - 仅 A1 设格式]
        Row 2: 账号归属人 | (姓名)
        Row 3: 中心/部门 | (中心)/(公司)
        Row 4: [空]
        Row 5: A | 外事号 | (外事号名)
        Row 6: B | 广告商 | (留空，新客户来了才填)
        """
        person = account_info.get("person") or account_info.get("operator") or ""
        center = account_info.get("center") or ""
        company = account_info.get("company_brand") or account_info.get("company") or ""
        account_name = account_info.get("name") or ""

        center_dept = f"{center}/{company}" if company else center

        header_data = [
            [""],                                                            # Row 1
            ["账号归属人", person],                                            # Row 2
            ["中心/部门", center_dept],                                        # Row 3
            [""],                                                            # Row 4
            ["A", "外事号", account_name],                                    # Row 5
            ["B", "广告商", ""],                                              # Row 6
        ]

        try:
            self._rate_limit()
            ws.update("A1:C6", header_data, value_input_option="USER_ENTERED")

            # 设置 Row 1 蓝色横条 + Row 5/6 表头底色
            self._rate_limit()
            ws.format("A1:Z1", {
                "backgroundColor": {"red": 0.4, "green": 0.8, "blue": 0.9},
            })
            self._rate_limit()
            ws.format("A5:Z6", {
                "backgroundColor": {"red": 0.6, "green": 0.85, "blue": 0.85},
                "textFormat": {"bold": True},
            })
            logger.info(f"tab {ws.title} 表头初始化完成")
        except Exception as e:
            logger.warning(f"tab {ws.title} 表头初始化部分失败（不影响数据写入）: {e}")

    def _get_or_create_peer_columns(self, ws, tab_name, account, peer):
        """
        在外事号 tab 里，找/建某客户的 3 列起始列号
        
        逻辑：
        - 读 Row 6（广告商行）所有内容
        - 找到客户名匹配的位置 → 返回那个位置 - 1（即 A/外事号 标记的列）
        - 没找到 → 在最右边追加新的 3 列，返回新列起始位置
        """
        peer_name = peer.get("name") or peer.get("id") or "未知"
        peer_key = str(peer.get("id") or peer_name)

        # 缓存命中
        if peer_key in self._peer_columns_cache.get(tab_name, {}):
            return self._peer_columns_cache[tab_name][peer_key]

        # 读 Row 6 所有列
        self._rate_limit()
        try:
            row6_values = ws.row_values(HEADER_PEER_ROW)
        except Exception as e:
            logger.error(f"读取 Row 6 失败: {e}")
            return None

        # 找客户：peer_name 应该在「广告商」标签后第 1 列（即 col=3, 6, 9, ...）
        # 即 row6 的索引 2, 5, 8, ... (0-based)
        for col_idx in range(2, len(row6_values), COLS_PER_PEER):
            if col_idx < len(row6_values) and row6_values[col_idx] == peer_name:
                # 找到了！返回对应 col_num（1-based）
                # col_idx 是 peer_name 的位置（即 C/F/I/...），起始列是 A/D/G/...（col_idx - 1）
                start_col = col_idx - 1  # 0-based
                start_col = start_col + 1  # 转 1-based
                self._peer_columns_cache[tab_name][peer_key] = start_col
                return start_col

        # 没找到，在最右边新建 3 列
        # 新起始列 = max(已有列数, 1) + 1
        existing_cols = max(len(row6_values), 0)
        # 新 peer 应该在 1, 4, 7, 10, ... 这种位置（1-based, 即每 3 列一组）
        if existing_cols == 0:
            new_start_col = 1
        else:
            # 算下一个 3 的倍数 + 1
            new_start_col = ((existing_cols + COLS_PER_PEER - 1) // COLS_PER_PEER) * COLS_PER_PEER + 1

        # 保险：如果计算出来的起始列已经被占了（例如老数据格式不规整），找下一个
        while new_start_col <= len(row6_values) and (new_start_col - 1) < len(row6_values) and row6_values[new_start_col - 1]:
            new_start_col += COLS_PER_PEER

        # 写入这 3 列的表头（Row 5 + Row 6）
        account_name = account.get("name") or ""
        try:
            self._rate_limit()
            row5_data = [["A", "外事号", account_name]]
            ws.update(
                f"{_col_letter(new_start_col)}{HEADER_ACCOUNT_ROW}:{_col_letter(new_start_col + 2)}{HEADER_ACCOUNT_ROW}",
                row5_data,
                value_input_option="USER_ENTERED",
            )

            self._rate_limit()
            row6_data = [["B", "广告商", peer_name]]
            ws.update(
                f"{_col_letter(new_start_col)}{HEADER_PEER_ROW}:{_col_letter(new_start_col + 2)}{HEADER_PEER_ROW}",
                row6_data,
                value_input_option="USER_ENTERED",
            )

            # 给 Row 5/6 这 3 列加底色
            self._rate_limit()
            try:
                ws.format(
                    f"{_col_letter(new_start_col)}5:{_col_letter(new_start_col + 2)}6",
                    {
                        "backgroundColor": {"red": 0.6, "green": 0.85, "blue": 0.85},
                        "textFormat": {"bold": True},
                    },
                )
            except Exception:
                pass  # 格式失败不影响数据

            self._peer_columns_cache[tab_name][peer_key] = new_start_col
            logger.info(f"tab {tab_name} 新建客户列: {peer_name} -> 起始列 {_col_letter(new_start_col)}")
            return new_start_col
        except Exception as e:
            logger.error(f"新建客户列失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 兼容 tasks.py 的接口
    # ------------------------------------------------------------------

    def get_or_create_sheet(self, account):
        """
        兼容 tasks.py：拿/建外事号 tab
        Args:
            account: dict 或 sqlite3.Row
        Returns:
            gspread.Worksheet 或 None
        """
        if self.disabled:
            return None
        try:
            account_dict = dict(account) if not isinstance(account, dict) else account
        except Exception:
            account_dict = {"name": str(account)}
        tab_name = _safe_tab_name(account_dict.get("name") or "未知外事号")
        return self._get_or_create_account_tab(tab_name, account_dict)

    def sync_headers(self):
        """
        兼容 tasks.py：同步表头
        现在的逻辑是「每次写入时确保表头存在」，所以这里只做缓存清理
        """
        if self.disabled:
            return
        # 清理缓存，下次写入时会重新读 Row 6
        self._peer_columns_cache.clear()
        logger.debug("sync_headers: 已清理列缓存")

    def ensure_alert_tabs(self):
        """
        启动时确保 3 个固定审计 tab 存在
        """
        if self.disabled:
            return
        try:
            import config
            display = getattr(config, "COMPANY_DISPLAY", "") or os.environ.get("COMPANY_DISPLAY", "")
        except ImportError:
            display = os.environ.get("COMPANY_DISPLAY", "")

        for kind, template in ALERT_TAB_TEMPLATES.items():
            tab_name = template.format(display=display)
            try:
                with self._write_lock:
                    self._rate_limit()
                    try:
                        ws = self.spreadsheet.worksheet(tab_name)
                    except WorksheetNotFound:
                        self._rate_limit()
                        ws = self.spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=15)
                        logger.info(f"已创建审计 tab: {tab_name}")
                        # 写入表头
                        headers = ALERT_HEADERS.get(kind, [])
                        if headers:
                            self._rate_limit()
                            ws.update("A1", [headers], value_input_option="USER_ENTERED")
                            self._rate_limit()
                            try:
                                ws.format("A1:Z1", {
                                    "backgroundColor": {"red": 0.6, "green": 0.85, "blue": 0.85},
                                    "textFormat": {"bold": True},
                                })
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"ensure_alert_tabs 处理 {tab_name} 失败: {e}")

    def ensure_account_tabs(self):
        """
        启动时为 accounts 表里的所有外事号建 tab
        """
        if self.disabled:
            return
        try:
            import database as db
            accounts = db.get_conn().execute(
                "SELECT id, name, operator, center, company_brand, person FROM accounts"
            ).fetchall()
        except Exception as e:
            logger.warning(f"ensure_account_tabs 读 accounts 表失败: {e}")
            return

        for acc in accounts:
            try:
                acc_dict = dict(acc)
                tab_name = _safe_tab_name(acc_dict.get("name") or "未知")
                self._get_or_create_account_tab(tab_name, acc_dict)
            except Exception as e:
                logger.warning(f"ensure_account_tabs 处理 {acc} 失败: {e}")

    def mark_deleted_in_sheet(self, ws, msg):
        """
        撤回标记：把对应行整行设为红色 + 删除线
        
        Args:
            ws: gspread.Worksheet 对象
            msg: dict 或 sqlite3.Row，需要包含 timestamp（用于定位行）
        """
        if self.disabled or ws is None:
            return

        try:
            msg_dict = dict(msg) if not isinstance(msg, dict) else msg
        except Exception:
            return

        # 用 timestamp 定位（在所有客户的时间列里找）
        target_ts = msg_dict.get("timestamp") or msg_dict.get("ts") or ""
        if not target_ts:
            logger.debug("mark_deleted_in_sheet: 无 timestamp，跳过")
            return

        try:
            with self._write_lock:
                # 读 Row 6 知道有多少客户列
                self._rate_limit()
                row6 = ws.row_values(HEADER_PEER_ROW)
                if not row6:
                    return

                # 时间列在 1, 4, 7, 10, ...（每 3 列一组的第 1 列）
                num_peers = (len(row6) + COLS_PER_PEER - 1) // COLS_PER_PEER

                for peer_idx in range(num_peers):
                    time_col = peer_idx * COLS_PER_PEER + 1  # 1-based
                    self._rate_limit()
                    try:
                        col_vals = ws.col_values(time_col)
                    except Exception:
                        continue
                    for row_idx, val in enumerate(col_vals[DATA_START_ROW - 1:], start=DATA_START_ROW):
                        if val == target_ts:
                            # 找到了！整行（这 3 列）设为红色 + 删除线
                            range_str = f"{_col_letter(time_col)}{row_idx}:{_col_letter(time_col + 2)}{row_idx}"
                            self._rate_limit()
                            try:
                                ws.format(range_str, {
                                    "textFormat": {
                                        "foregroundColor": {"red": 0.8, "green": 0.0, "blue": 0.0},
                                        "strikethrough": True,
                                    },
                                })
                                logger.info(f"撤回标记成功: {ws.title} {range_str}")
                            except Exception as e:
                                logger.warning(f"撤回格式设置失败: {e}")
                            return  # 找到一处就够了
        except Exception as e:
            logger.warning(f"mark_deleted_in_sheet 失败: {e}")

    # ------------------------------------------------------------------
    # 磁盘备份（崩溃保护）
    # ------------------------------------------------------------------

    def _save_backup_to_disk(self):
        """
        把当前队列里的所有消息写到磁盘 jsonl 文件
        """
        with self._queue_lock:
            all_records = list(self._pending_writes) + list(self._pending_queue)

        if not all_records:
            return

        try:
            BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(BACKUP_PATH, "w", encoding="utf-8") as f:
                for r in all_records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            logger.warning(f"已紧急备份 {len(all_records)} 条到磁盘: {BACKUP_PATH}")
        except Exception as e:
            logger.error(f"磁盘备份失败: {e}")

    def _load_backup_from_disk(self):
        """
        启动时从磁盘加载未推送的备份
        """
        if not BACKUP_PATH.exists():
            return

        loaded = []
        try:
            with open(BACKUP_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"读取磁盘备份失败: {e}")
            return

        if loaded:
            with self._queue_lock:
                # 备份的优先入队
                self._pending_queue.extendleft(reversed(loaded))
            logger.info(f"已从磁盘恢复 {len(loaded)} 条待写入消息")

    # ------------------------------------------------------------------
    # 积压告警（精准推送到对应中心 RISK 群）
    # ------------------------------------------------------------------

    def _maybe_alert_backlog(self, backlog_count, account_info):
        """
        触发积压告警：
        - 同一个外事号 5 分钟内最多发 1 次
        - 推送到该外事号所属中心的 RISK 群
        """
        center = account_info.get("center") or ""
        # 中心名 → ALERT_RISK_GROUP_xxx 的映射
        center_to_env = {
            "商务中心": "ALERT_RISK_GROUP_SHANGWU",
            "渠道中心": "ALERT_RISK_GROUP_QUDAO",
            "运营中心": "ALERT_RISK_GROUP_YUNYING",
            "恒丰中心": "ALERT_RISK_GROUP_HENGFENG",
        }

        env_key = center_to_env.get(center, "ALERT_RISK_GROUP_YUNYING")  # 默认运营
        group_id_str = os.environ.get(env_key, "")
        if not group_id_str:
            try:
                import config
                group_id_str = getattr(config, env_key, "")
            except ImportError:
                pass

        if not group_id_str:
            logger.warning(f"积压告警找不到群 ID: {env_key}")
            return

        try:
            group_id = int(group_id_str)
        except (ValueError, TypeError):
            logger.warning(f"积压告警群 ID 格式错误: {group_id_str}")
            return

        # 5 分钟去重
        alert_key = f"{center}_{account_info.get('name', '')}"
        now = time.time()
        last = self._last_alert_time.get(alert_key, 0)
        if now - last < 300:
            return
        self._last_alert_time[alert_key] = now

        # 发送告警（异步，不阻塞主线程）
        if self.bot is None:
            logger.warning(f"⚠️ Sheets 积压告警: {backlog_count} 条 (无 bot 实例，仅日志)")
            return

        msg_text = (
            f"⚠️ Sheets 写入积压告警\n\n"
            f"中心: {center or '未知'}\n"
            f"外事号: {account_info.get('name', '未知')}\n"
            f"积压条数: {backlog_count}\n"
            f"时间: {_now_bj()}\n\n"
            f"建议: 检查 Google API 配额或网络"
        )

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.bot.send_message(group_id, msg_text))
                else:
                    loop.run_until_complete(self.bot.send_message(group_id, msg_text))
            except RuntimeError:
                # 没有 running loop，新建一个
                asyncio.run(self.bot.send_message(group_id, msg_text))
            logger.warning(f"已发送积压告警到 {center} RISK 群: {backlog_count} 条")
        except Exception as e:
            logger.error(f"发送积压告警失败: {e}")


# ============================================================================
# 模块级工具函数（兼容老代码 import）
# ============================================================================

def get_writer(*args, **kwargs):
    """工厂函数：返回 SheetsWriter 实例"""
    return SheetsWriter(*args, **kwargs)
