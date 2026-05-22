# -*- coding: utf-8 -*-
"""YYZL 速查 API:从数据库查数据,毫秒返回"""
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ========== 配置 ==========
DB = dict(host="localhost", dbname="yyzl", user="yyzl_user", password="YyzlData2026")
PASSWORD = "000000"                 # 统一登录密码
LEADER_USERS = ["季霖"]              # 组长(能切换看同事)

app = FastAPI(title="YYZL速查API")

# 允许跨域(网页从别处访问本API)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def db():
    conn = psycopg2.connect(**DB)
    return conn


def get_all_operators():
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT operator FROM accounts ORDER BY operator")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


class LoginReq(BaseModel):
    username: str
    password: str


# ========== 提供前端网页(放最前面,跟其它路由一起注册)==========
@app.get("/web")
def web_page():
    return FileResponse("yyzl_web.html")


@app.get("/")
def root():
    return {"ok": True, "msg": "YYZL速查API运行中"}


@app.post("/api/login")
def login(req: LoginReq):
    name = (req.username or "").strip()
    if req.password != PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")
    operators = get_all_operators()
    if name not in operators:
        raise HTTPException(status_code=401, detail="监察员不存在")
    return {
        "ok": True,
        "username": name,
        "is_leader": name in LEADER_USERS,
    }


@app.get("/api/teammates")
def teammates(username: str):
    """组长能看的同事列表"""
    name = (username or "").strip()
    if name not in LEADER_USERS:
        return {"ok": True, "teammates": []}
    others = [o for o in get_all_operators() if o != name]
    return {"ok": True, "teammates": others}


@app.get("/api/skeleton")
def skeleton(username: str, view_as: str = ""):
    """某监察员的外事号列表(秒开骨架)"""
    name = (username or "").strip()
    target = (view_as or "").strip() or name
    # 权限:非组长只能看自己
    if name not in LEADER_USERS and target != name:
        raise HTTPException(status_code=403, detail="无权查看他人")

    conn = db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.id, a.dept, a.account_name,
               (SELECT count(*) FROM advertisers ad WHERE ad.account_id=a.id) AS adv_count
        FROM accounts a
        WHERE a.operator=%s
        ORDER BY a.dept, a.account_name
    """, (target,))
    rows = cur.fetchall()
    conn.close()
    return {"ok": True, "operator": target, "accounts": rows}


@app.get("/api/advertisers")
def advertisers(account_id: int):
    """某外事号的广告主 + 每个广告主的消息"""
    conn = db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, dept, account_name FROM accounts WHERE id=%s", (account_id,))
    acc = cur.fetchone()
    if not acc:
        conn.close()
        raise HTTPException(status_code=404, detail="外事号不存在")

    cur.execute("""
        SELECT id, col, adv_name FROM advertisers
        WHERE account_id=%s ORDER BY col
    """, (account_id,))
    advs = cur.fetchall()

    result = []
    for ad in advs:
        cur.execute("""
            SELECT row_num, msg_time, sender, content
            FROM messages WHERE advertiser_id=%s
            ORDER BY row_num
        """, (ad["id"],))
        msgs = cur.fetchall()
        result.append({
            "adv_id": ad["id"],
            "adv_name": ad["adv_name"],
            "messages": msgs,
        })
    conn.close()
    return {"ok": True, "account": acc, "advertisers": result}


@app.get("/api/search")
def search(username: str, q: str, view_as: str = ""):
    """在某监察员数据里搜索关键词"""
    name = (username or "").strip()
    target = (view_as or "").strip() or name
    keyword = (q or "").strip()
    if name not in LEADER_USERS and target != name:
        raise HTTPException(status_code=403, detail="无权查看他人")
    if not keyword:
        return {"ok": True, "results": []}

    conn = db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.id AS account_id, a.dept, a.account_name,
               ad.adv_name, m.msg_time, m.sender, m.content
        FROM messages m
        JOIN advertisers ad ON m.advertiser_id=ad.id
        JOIN accounts a ON ad.account_id=a.id
        WHERE a.operator=%s AND m.content ILIKE %s
        ORDER BY m.msg_time DESC NULLS LAST
        LIMIT 200
    """, (target, f"%{keyword}%"))
    rows = cur.fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "results": rows}


# ========== 启动(必须放在所有路由之后)==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
