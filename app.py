"""
直播间销售数据分析仪表盘
运行：streamlit run app.py
"""

import io
import json
import os
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from config import (
    OLD_CUSTOMER_MIN_AMOUNT,
    OLD_CUSTOMER_MIN_DAYS,
    OLD_CUSTOMER_R12_DEFAULT,
    TRANSACTION_SUCCESS_STATUSES,
)

# 平台优惠 "券名-金额" 提取（贪婪 + 末尾数字，支持千分位逗号）
COUPON_RE = re.compile(r"^(.+)-([\d,]+(?:\.\d+)?)$")

st.set_page_config(page_title="直播间销售分析", layout="wide", page_icon="📊")

# 访问密码：优先读环境变量 DASH_PASSWORD（由启动脚本设置），否则用下面默认值。
# 设为空字符串则关闭密码（任何人可直接访问）。
DEFAULT_PASSWORD = "xinlaoke2026"


def _check_password() -> bool:
    """网页访问密码门。返回 True 表示已通过。适用于任何访问入口（本地/内网/公网隧道）。"""
    pw = os.environ.get("DASH_PASSWORD", DEFAULT_PASSWORD)
    if not pw:                       # 空密码 = 不启用门禁
        return True
    if st.session_state.get("_auth_ok"):
        return True

    st.title("🔒 直播间销售数据分析")
    st.caption("请输入访问密码")
    with st.form("login_form"):
        entered = st.text_input("访问密码", type="password", label_visibility="collapsed")
        ok = st.form_submit_button("进入")
    if ok:
        if entered == pw:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("❌ 密码错误，请重试")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 数据加载（带缓存）
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data
def load_orders() -> pd.DataFrame:
    df = pd.read_parquet("data/processed/orders.parquet")
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["pay_time"]   = pd.to_datetime(df["pay_time"])
    return df


@st.cache_data
def load_pairs() -> pd.DataFrame:
    p = pd.read_parquet("data/processed/purchase_pairs.parquet")
    p["from_date"] = pd.to_datetime(p["from_date"], errors="coerce")
    p["to_date"]   = pd.to_datetime(p["to_date"],   errors="coerce")
    p["from_rank"] = p["from_rank"].fillna(0).astype(int)
    p["to_rank"]   = p["to_rank"].fillna(0).astype(int)
    return p


@st.cache_data
def load_meta() -> dict:
    with open("data/processed/meta.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_samples() -> pd.DataFrame:
    df = pd.read_parquet("data/processed/samples.parquet")
    df["pay_time"]   = pd.to_datetime(df["pay_time"])
    df["order_date"] = pd.to_datetime(df["order_date"])
    return df


@st.cache_data
def load_members() -> pd.DataFrame:
    df = pd.read_parquet("data/processed/members.parquet")
    df["join_time"] = pd.to_datetime(df["join_time"])
    return df


@st.cache_data
def load_crowds() -> pd.DataFrame:
    return pd.read_parquet("data/processed/crowds.parquet")


# ═══════════════════════════════════════════════════════════════════════════════
# 侧边栏筛选
# ═══════════════════════════════════════════════════════════════════════════════

def render_sidebar(meta: dict) -> dict:
    st.sidebar.header("🔍 全局筛选")

    d_min = pd.to_datetime(meta["date_min"]).date()
    d_max = pd.to_datetime(meta["date_max"]).date()
    date_range = st.sidebar.date_input(
        "时间范围", value=(d_min, d_max), min_value=d_min, max_value=d_max
    )

    # ── YOY 同比设置（紧接时间范围下方）──
    yoy_on = st.sidebar.checkbox("📅 开启同比(YOY)", value=False)
    yoy_date_range = None
    if yoy_on:
        st.sidebar.caption("对比时间段（默认为主时间段往前推1年）")
        if len(date_range) == 2:
            default_yoy_start = date_range[0].replace(year=date_range[0].year - 1)
            default_yoy_end   = date_range[1].replace(year=date_range[1].year - 1)
        else:
            default_yoy_start = d_min
            default_yoy_end   = d_max
        yoy_date_range = st.sidebar.date_input(
            "同比时间段",
            value=(default_yoy_start, default_yoy_end),
            min_value=d_min, max_value=d_max,
            key="yoy_dr",
        )

    # ── 2. 渠道类型（自营 / 达人）──
    st.sidebar.subheader("渠道类型")
    sel_channel_types = st.sidebar.multiselect(
        "选择渠道类型（不选 = 全选）", meta.get("channel_types", []), default=[]
    )

    # ── 3. 达人（联动渠道类型）──
    st.sidebar.subheader("达人")
    all_influencers = meta["influencers"]
    if sel_channel_types and "influencer_channel_map" in meta:
        linked_influencers = [
            inf for inf, ct in meta["influencer_channel_map"].items()
            if ct in sel_channel_types
        ]
        available_influencers = [i for i in all_influencers if i in linked_influencers]
        if available_influencers:
            st.sidebar.caption(f"已按渠道类型过滤，共 {len(available_influencers)} 位达人")
        else:
            available_influencers = all_influencers
    else:
        available_influencers = all_influencers
    sel_influencers = st.sidebar.multiselect(
        "选择达人（不选 = 全选）", available_influencers, default=[]
    )

    # ── 4. 品类 ──
    st.sidebar.subheader("品类")
    sel_categories = st.sidebar.multiselect(
        "选择品类（不选 = 全选）", meta.get("categories", []), default=[]
    )

    # ── 5. 货号筛选（联动品类）──
    st.sidebar.subheader("货号")
    all_skus = meta["skus"]
    if sel_categories and "category_sku_map" in meta:
        linked_skus = []
        for cat in sel_categories:
            linked_skus.extend(meta["category_sku_map"].get(cat, []))
        available_skus = sorted(set(linked_skus) & set(all_skus))
        if available_skus:
            st.sidebar.caption(f"已按品类过滤，共 {len(available_skus)} 个货号")
        else:
            available_skus = all_skus
    else:
        available_skus = all_skus
    sku_kw = st.sidebar.text_input("货号关键词（如 tl）", "")
    filtered_skus = [s for s in available_skus if sku_kw.lower() in s.lower()] if sku_kw else available_skus
    if sku_kw:
        st.sidebar.caption(f"匹配 {len(filtered_skus)} 个货号")
    sel_skus = st.sidebar.multiselect(
        "选择货号（不选 = 全选）", filtered_skus, default=[]
    )

    # ── 订单状态 ──
    st.sidebar.subheader("订单状态")
    sel_statuses = st.sidebar.multiselect(
        "选择状态（不选 = 全选）", meta["order_statuses"], default=[]
    )

    # ── 售后状态 ──
    st.sidebar.subheader("售后状态")
    sel_after_sale = st.sidebar.multiselect(
        "选择售后状态（不选 = 全选）",
        meta.get("after_sale_statuses", []),
        default=[],
        key="sel_after_sale",
    )

    # ── 金额区间 ──
    st.sidebar.subheader("成交金额（元）")
    amt_max_data = float(meta.get("gmv_max", 99999))
    amt_min = st.sidebar.number_input("最小金额", value=0.0, min_value=0.0, step=50.0)
    amt_max = st.sidebar.number_input("最大金额", value=amt_max_data, min_value=0.0, step=50.0)

    # ── 老客判定规则（可自定义）──
    st.sidebar.subheader("⚙️ 老客判定规则")
    st.sidebar.caption("以下参数定义「老客」，修改后所有 Tab 同步生效")
    all_channel_types = meta.get("channel_types", [])
    old_cust_channels = st.sidebar.multiselect(
        "有效成交渠道（不选 = 全渠道）",
        all_channel_types,
        default=[],
        key="old_cust_channels",
    )
    old_cust_amount = st.sidebar.number_input(
        "有效成交最低金额（元）", value=int(OLD_CUSTOMER_MIN_AMOUNT), min_value=0, step=50,
        key="old_cust_amount"
    )
    old_cust_days = st.sidebar.number_input(
        "历史订单早于当前至少（天）", value=int(OLD_CUSTOMER_MIN_DAYS), min_value=1, step=1,
        key="old_cust_days"
    )
    r12_window = st.sidebar.number_input(
        "R12 回溯窗口（天）", value=int(OLD_CUSTOMER_R12_DEFAULT), min_value=30, step=30,
        key="r12_window"
    )
    st.sidebar.caption("9999天≈27年，相当于不限时间（全时段）")

    return {
        "date_range":        date_range,
        "yoy_on":            yoy_on,
        "yoy_date_range":    yoy_date_range,
        "sel_categories":    sel_categories,
        "sel_channel_types": sel_channel_types,
        "sel_influencers":   sel_influencers,
        "sel_skus":          sel_skus,
        "sel_statuses":      sel_statuses,
        "sel_after_sale":    sel_after_sale,
        "sku_kw":            sku_kw,
        "filtered_skus":     filtered_skus,
        "amt_min":           amt_min,
        "amt_max":           amt_max,
        "old_cust_channels": old_cust_channels,
        "old_cust_amount":   old_cust_amount,
        "old_cust_days":     old_cust_days,
        "r12_window":        r12_window,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 动态重算新老客标签
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner="重新计算新老客标签…")
def recompute_customer_type(
    orders_parquet_hash: str,   # 用于缓存 key（传入文件 mtime）
    min_amount: float,
    min_days: int,
    r12_window: int,
    channels: tuple,            # 空tuple = 全渠道
) -> tuple:
    """
    根据自定义参数重算 customer_type 和 customer_type_r12。
    返回 (orders_df, pairs_df)，两者都带新列。
    """
    df    = pd.read_parquet("data/processed/orders.parquet")
    pairs = pd.read_parquet("data/processed/purchase_pairs.parquet")
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["pay_time"]   = pd.to_datetime(df["pay_time"])

    # ── 全时段老客（成交状态集合见 config.TRANSACTION_SUCCESS_STATUSES，排除已关闭）──
    q_mask = (df["order_status"].isin(TRANSACTION_SUCCESS_STATUSES)) & (df["gmv"] >= min_amount)
    if channels:  # 限定渠道
        q_mask &= df["channel_type"].isin(channels)
    qualifying = df[q_mask][["user_id", "pay_time"]].copy()

    df["customer_type"] = "新客"
    if not qualifying.empty:
        first_q = qualifying.sort_values("pay_time").groupby("user_id")["pay_time"].first()
        df = df.merge(first_q.rename("_fq_time"), on="user_id", how="left")
        threshold = df["_fq_time"] + pd.Timedelta(days=min_days)
        df.loc[df["_fq_time"].notna() & (df["pay_time"] >= threshold), "customer_type"] = "老客"
        df = df.drop(columns=["_fq_time"])

    # ── R12 老客 ──
    df["customer_type_r12"] = "新客"
    if not qualifying.empty:
        users_q = set(qualifying["user_id"])
        df_sub = df[df["user_id"].isin(users_q)][["user_id", "pay_time"]].reset_index()
        cross = df_sub.merge(
            qualifying.rename(columns={"pay_time": "q_time"}), on="user_id", how="left"
        )
        cross["days_diff"] = (cross["pay_time"] - cross["q_time"]).dt.days
        in_win = cross[(cross["days_diff"] >= min_days) & (cross["days_diff"] <= r12_window)]
        df.loc[in_win["index"].unique(), "customer_type_r12"] = "老客"

    # ── 同步到 pairs ──
    df_keys = df[["user_id", "pay_time", "customer_type", "customer_type_r12"]].copy()
    pairs = pairs.drop(columns=["customer_type", "customer_type_r12"], errors="ignore")
    pairs["from_date"] = pd.to_datetime(pairs["from_date"], errors="coerce")
    pairs = pairs.merge(
        df_keys.rename(columns={
            "pay_time":          "from_pay_time_key",
            "customer_type":     "customer_type",
            "customer_type_r12": "customer_type_r12",
        }),
        left_on=["user_id", "from_date"],
        right_on=["user_id", "from_pay_time_key"],
        how="left",
    ).drop(columns=["from_pay_time_key"], errors="ignore")

    return df, pairs


# ═══════════════════════════════════════════════════════════════════════════════
# 过滤逻辑
# ═══════════════════════════════════════════════════════════════════════════════

def _base_filter(df: pd.DataFrame, f: dict, date_range) -> pd.DataFrame:
    """通用字段过滤，date_range 单独传入（方便YOY复用）"""
    mask = pd.Series(True, index=df.index)

    if len(date_range) == 2:
        mask &= (df["order_date"] >= pd.Timestamp(date_range[0])) & \
                (df["order_date"] <= pd.Timestamp(date_range[1]))

    if f["sel_categories"]:
        mask &= df["category"].isin(f["sel_categories"])

    if f["sel_channel_types"]:
        mask &= df["channel_type"].isin(f["sel_channel_types"])

    if f["sel_influencers"]:
        mask &= df["influencer_name"].isin(f["sel_influencers"])

    if f["sel_skus"]:
        mask &= df["sku"].isin(f["sel_skus"])
    elif f["sku_kw"]:
        mask &= df["sku"].isin(f["filtered_skus"])

    if f["sel_statuses"]:
        mask &= df["order_status"].isin(f["sel_statuses"])

    if f.get("sel_after_sale") and "after_sale_status" in df.columns:
        mask &= df["after_sale_status"].isin(f["sel_after_sale"])

    mask &= (df["gmv"] >= f["amt_min"]) & (df["gmv"] <= f["amt_max"])

    return df[mask].copy()


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    return _base_filter(df, f, f["date_range"])


def apply_yoy_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    if not f["yoy_on"] or not f["yoy_date_range"] or len(f["yoy_date_range"]) < 2:
        return pd.DataFrame()
    return _base_filter(df, f, f["yoy_date_range"])


def filter_pairs(pairs: pd.DataFrame, f: dict) -> pd.DataFrame:
    mask = pd.Series(True, index=pairs.index)

    dr = f["date_range"]
    if len(dr) == 2:
        mask &= (pairs["from_date"] >= pd.Timestamp(dr[0])) & \
                (pairs["from_date"] <= pd.Timestamp(dr[1]))

    if f["sel_channel_types"] and "from_channel_type" in pairs.columns:
        mask &= pairs["from_channel_type"].isin(f["sel_channel_types"])

    if f["sel_influencers"]:
        mask &= pairs["from_influencer"].isin(f["sel_influencers"])

    if f["sel_skus"]:
        mask &= pairs["from_sku"].isin(f["sel_skus"])
    elif f["sku_kw"]:
        mask &= pairs["from_sku"].isin(f["filtered_skus"])

    if f["amt_min"] > 0:
        mask &= pairs["from_gmv"] >= f["amt_min"]

    return pairs[mask].copy()


# ═══════════════════════════════════════════════════════════════════════════════
# KPI 卡片
# ═══════════════════════════════════════════════════════════════════════════════

def _kpi_stats(df: pd.DataFrame) -> dict:
    """计算全店/新客/老客的6项指标"""
    df_new = df[df["customer_type"] == "新客"]
    df_old = df[df["customer_type"] == "老客"]

    user_type = (
        df.groupby("user_id")["customer_type"]
        .apply(lambda s: "新客" if "新客" in s.values else "老客")
    )
    n_total = len(user_type)
    n_new   = int((user_type == "新客").sum())
    n_old   = int((user_type == "老客").sum())

    total_orders = len(df)
    new_orders   = len(df_new)
    old_orders   = len(df_old)

    total_gmv = df["gmv"].sum()
    new_gmv   = df_new["gmv"].sum()
    old_gmv   = df_old["gmv"].sum()

    total_freq = round(total_orders / n_total, 2) if n_total else 0
    new_freq   = round(new_orders   / n_new,   2) if n_new   else 0
    old_freq   = round(old_orders   / n_old,   2) if n_old   else 0

    total_arpu = round(total_gmv / n_total, 0) if n_total else 0
    new_arpu   = round(new_gmv   / n_new,   0) if n_new   else 0
    old_arpu   = round(old_gmv   / n_old,   0) if n_old   else 0

    new_rate = round(n_new / n_total * 100, 1) if n_total else 0
    old_rate = round(n_old / n_total * 100, 1) if n_total else 0

    return dict(
        total_orders=total_orders, new_orders=new_orders, old_orders=old_orders,
        total_gmv=total_gmv,       new_gmv=new_gmv,       old_gmv=old_gmv,
        n_total=n_total,           n_new=n_new,           n_old=n_old,
        total_freq=total_freq,     new_freq=new_freq,      old_freq=old_freq,
        total_arpu=total_arpu,     new_arpu=new_arpu,      old_arpu=old_arpu,
        new_rate=new_rate,         old_rate=old_rate,
    )


def _yoy_delta_html(cur, prev, fmt="num") -> str:
    """生成 YOY 小字 HTML，涨红跌绿"""
    if prev == 0:
        return ""
    delta = cur - prev
    pct   = delta / abs(prev) * 100
    color = "#FF4B4B" if delta >= 0 else "#21BA45"
    arrow = "▲" if delta >= 0 else "▼"
    if fmt == "pct":
        return f'<span style="color:{color};font-size:11px">{arrow}{abs(pct):.1f}pp</span>'
    elif fmt == "gmv":
        return f'<span style="color:{color};font-size:11px">{arrow}{abs(pct):.1f}%（¥{abs(delta):,.0f}）</span>'
    else:
        return f'<span style="color:{color};font-size:11px">{arrow}{abs(pct):.1f}%（{abs(delta):,.0f}）</span>'


def _kpi_card_html(title: str,
                   total_val: str, new_val: str, old_val: str,
                   yoy_total: str = "", yoy_new: str = "", yoy_old: str = "") -> str:
    return f"""
<div style="background:#f8f9fa;border-radius:8px;padding:12px 14px;min-width:180px;flex-shrink:0">
  <div style="font-size:12px;color:#666;margin-bottom:4px">{title}</div>
  <div style="font-size:22px;font-weight:700;color:#1a1a2e">{total_val}
    <span style="font-size:11px;font-weight:400;margin-left:4px">{yoy_total}</span>
  </div>
  <div style="margin-top:6px;font-size:12px;color:#555">
    <span style="color:#FF6B6B">新客</span> {new_val}
    <span style="margin-left:6px">{yoy_new}</span>
  </div>
  <div style="margin-top:3px;font-size:12px;color:#555">
    <span style="color:#4ECDC4">老客</span> {old_val}
    <span style="margin-left:6px">{yoy_old}</span>
  </div>
</div>
"""


def render_kpi(df: pd.DataFrame, df_yoy: pd.DataFrame = None):
    s = _kpi_stats(df)
    y = _kpi_stats(df_yoy) if df_yoy is not None and not df_yoy.empty else None

    def yoy(cur, prev, fmt="num"):
        return _yoy_delta_html(cur, prev, fmt) if y else ""

    cards = [
        _kpi_card_html(
            "订单数",
            f"{s['total_orders']:,}", f"{s['new_orders']:,}", f"{s['old_orders']:,}",
            yoy(s['total_orders'], y['total_orders']) if y else "",
            yoy(s['new_orders'],   y['new_orders'])   if y else "",
            yoy(s['old_orders'],   y['old_orders'])   if y else "",
        ),
        _kpi_card_html(
            "GMV（元）",
            f"¥{s['total_gmv']:,.0f}", f"¥{s['new_gmv']:,.0f}", f"¥{s['old_gmv']:,.0f}",
            yoy(s['total_gmv'], y['total_gmv'], "gmv") if y else "",
            yoy(s['new_gmv'],   y['new_gmv'],   "gmv") if y else "",
            yoy(s['old_gmv'],   y['old_gmv'],   "gmv") if y else "",
        ),
        _kpi_card_html(
            "去重买家数",
            f"{s['n_total']:,}", f"{s['n_new']:,}", f"{s['n_old']:,}",
            yoy(s['n_total'], y['n_total']) if y else "",
            yoy(s['n_new'],   y['n_new'])   if y else "",
            yoy(s['n_old'],   y['n_old'])   if y else "",
        ),
        _kpi_card_html(
            "购买频次（订单/人）",
            f"{s['total_freq']}", f"{s['new_freq']}", f"{s['old_freq']}",
            yoy(s['total_freq'], y['total_freq']) if y else "",
            yoy(s['new_freq'],   y['new_freq'])   if y else "",
            yoy(s['old_freq'],   y['old_freq'])   if y else "",
        ),
        _kpi_card_html(
            "客单价（GMV/人）",
            f"¥{s['total_arpu']:,.0f}", f"¥{s['new_arpu']:,.0f}", f"¥{s['old_arpu']:,.0f}",
            yoy(s['total_arpu'], y['total_arpu'], "gmv") if y else "",
            yoy(s['new_arpu'],   y['new_arpu'],   "gmv") if y else "",
            yoy(s['old_arpu'],   y['old_arpu'],   "gmv") if y else "",
        ),
        _kpi_card_html(
            "新/老客占比",
            f"新 {s['new_rate']}% / 老 {s['old_rate']}%",
            f"{s['new_rate']}%", f"{s['old_rate']}%",
            "",
            yoy(s['new_rate'], y['new_rate'], "pct") if y else "",
            yoy(s['old_rate'], y['old_rate'], "pct") if y else "",
        ),
    ]

    all_cards = "".join(cards)
    st.markdown(
        f'<div style="display:flex;gap:12px;overflow-x:auto;padding-bottom:6px">{all_cards}</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab：渠道汇总（自营/达人占比 + 同比 + 分渠道明细表）
# ═══════════════════════════════════════════════════════════════════════════════

def _channel_agg(df: pd.DataFrame, group_col: str = "channel_type") -> pd.DataFrame:
    """按指定列汇总：订单数 / GMV / 人数 / 新老客拆分"""
    if df is None or df.empty or group_col not in df.columns:
        return pd.DataFrame()

    base = df.groupby(group_col).agg(
        订单数=("gmv", "count"),
        GMV=("gmv", "sum"),
        人数=("user_id", "nunique"),
    ).reset_index()

    new_df = df[df["customer_type"] == "新客"]
    old_df = df[df["customer_type"] == "老客"]

    new_agg = new_df.groupby(group_col).agg(
        新客订单=("gmv", "count"),
        新客GMV=("gmv", "sum"),
        新客人数=("user_id", "nunique"),
    ).reset_index()
    old_agg = old_df.groupby(group_col).agg(
        老客订单=("gmv", "count"),
        老客GMV=("gmv", "sum"),
        老客人数=("user_id", "nunique"),
    ).reset_index()

    out = base.merge(new_agg, on=group_col, how="left") \
              .merge(old_agg, on=group_col, how="left").fillna(0)

    # 人数按互斥口径(与表头KPI一致)：新客=有任意新客订单的人，老客=人数−新客(无新客订单的人)。
    # 订单数/GMV 仍为订单级(新客单/老客单)，不变。
    out["老客人数"] = (out["人数"] - out["新客人数"]).clip(lower=0)

    out["客单价"]    = (out["GMV"] / out["人数"].replace(0, 1)).round(0)
    out["购买频次"]  = (out["订单数"] / out["人数"].replace(0, 1)).round(2)
    out["新客占比%"] = (out["新客人数"] / out["人数"].replace(0, 1) * 100).round(1)
    out["老客占比%"] = (out["老客人数"] / out["人数"].replace(0, 1) * 100).round(1)
    out["新客客单"]  = (out["新客GMV"] / out["新客人数"].replace(0, 1)).round(0)
    out["老客客单"]  = (out["老客GMV"] / out["老客人数"].replace(0, 1)).round(0)
    return out


_CHANNEL_COLORS = {"自营": "#FF6B6B", "达人": "#4ECDC4"}


def _donut(agg: pd.DataFrame, group_col: str, metric: str,
           title: str, fmt: str = "num", legend_label: str = "渠道") -> go.Figure:
    """给定汇总表、分组列、指标列名，画环形图。"""
    if agg.empty or metric not in agg.columns or group_col not in agg.columns:
        fig = go.Figure()
        fig.add_annotation(text="（无数据）", showarrow=False, font=dict(size=14, color="#888"))
        fig.update_layout(title=title, height=360, margin=dict(t=50, b=20, l=10, r=10))
        return fig

    labels = agg[group_col].astype(str).tolist()
    values = agg[metric].tolist()

    # 渠道类型用固定红绿；其他维度用 plotly 默认彩色
    if group_col == "channel_type":
        colors = [_CHANNEL_COLORS.get(l, "#999") for l in labels]
        marker_kw = dict(colors=colors, line=dict(color="white", width=2))
    else:
        marker_kw = dict(line=dict(color="white", width=2))

    if fmt == "gmv":
        text_vals = [f"¥{v:,.0f}" for v in values]
        hover_fmt = f"{legend_label}：%{{label}}<br>金额：¥%{{value:,.0f}}<br>占比：%{{percent}}<extra></extra>"
    else:
        text_vals = [f"{int(v):,}" for v in values]
        hover_fmt = f"{legend_label}：%{{label}}<br>数量：%{{value:,}}<br>占比：%{{percent}}<extra></extra>"

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.55,
        marker=marker_kw,
        text=text_vals,
        textinfo="label+percent+text",
        texttemplate="%{label}<br>%{percent}<br>%{text}",
        hovertemplate=hover_fmt,
        sort=False,
    ))
    # 维度多时把名字放到图例，避免图上文字挤
    show_legend = (group_col != "channel_type" and len(labels) > 3)
    if show_legend:
        fig.update_traces(textinfo="percent+text", texttemplate="%{percent}<br>%{text}")
    fig.update_layout(
        title=title,
        height=360,
        margin=dict(t=50, b=20, l=10, r=10),
        showlegend=show_legend,
        legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02),
    )
    return fig


def tab_channel_summary(df: pd.DataFrame, df_yoy: pd.DataFrame, yoy_on: bool, f: dict):
    st.subheader("渠道汇总")
    st.caption("时间范围内的整体占比与分维度关键指标（受全局筛选影响）。")

    # ── 决定分组维度：选了达人 → 按达人；否则按渠道类型 ──
    if f.get("sel_influencers"):
        group_col   = "influencer_name"
        group_label = "达人"
        section_title = f"📊 各达人占比（共 {len(f['sel_influencers'])} 位）"
    else:
        group_col   = "channel_type"
        group_label = "渠道类型"
        section_title = "📊 渠道类型占比（自营 / 达人）"

    cur = _channel_agg(df, group_col)
    if cur.empty:
        st.info("当前筛选条件下没有可统计的数据。")
        return

    has_yoy = yoy_on and df_yoy is not None and not df_yoy.empty
    yoy = _channel_agg(df_yoy, group_col) if has_yoy else pd.DataFrame()

    st.markdown(f"#### {section_title}")
    if has_yoy:
        st.caption(f"上排：当期（{f['date_range'][0]} ~ {f['date_range'][1]}） · 下排：同比（{f['yoy_date_range'][0]} ~ {f['yoy_date_range'][1]}）")

    c1, c2, c3 = st.columns(3)
    c1.plotly_chart(_donut(cur, group_col, "订单数", "订单数占比",       "num", group_label), use_container_width=True)
    c2.plotly_chart(_donut(cur, group_col, "GMV",   "GMV占比",          "gmv", group_label), use_container_width=True)
    c3.plotly_chart(_donut(cur, group_col, "人数",  "去重买家数占比",   "num", group_label), use_container_width=True)

    if has_yoy:
        y1, y2, y3 = st.columns(3)
        y1.plotly_chart(_donut(yoy, group_col, "订单数", "订单数占比（同比）",     "num", group_label), use_container_width=True)
        y2.plotly_chart(_donut(yoy, group_col, "GMV",   "GMV占比（同比）",        "gmv", group_label), use_container_width=True)
        y3.plotly_chart(_donut(yoy, group_col, "人数",  "去重买家数占比（同比）", "num", group_label), use_container_width=True)

    st.divider()

    st.markdown(f"#### 📋 分{group_label}关键指标")

    cols_order = [
        group_col, "订单数", "人数", "GMV", "客单价", "购买频次",
        "新客人数", "老客人数", "新客占比%", "老客占比%",
        "新客客单", "老客客单", "新客GMV", "老客GMV",
    ]
    cur_show = cur[[c for c in cols_order if c in cur.columns]].copy()
    cur_show = cur_show.rename(columns={group_col: group_label})

    fmt_map = {
        "订单数": "{:,.0f}", "人数": "{:,.0f}",
        "新客人数": "{:,.0f}", "老客人数": "{:,.0f}",
        "GMV": "¥{:,.0f}", "客单价": "¥{:,.0f}",
        "新客客单": "¥{:,.0f}", "老客客单": "¥{:,.0f}",
        "新客GMV": "¥{:,.0f}", "老客GMV": "¥{:,.0f}",
        "购买频次": "{:.2f}",
        "新客占比%": "{:.1f}%", "老客占比%": "{:.1f}%",
    }

    if has_yoy:
        yoy_show = yoy[[c for c in cols_order if c in yoy.columns]].copy()
        yoy_show = yoy_show.rename(columns={group_col: group_label})
        yoy_show[group_label] = yoy_show[group_label].astype(str) + "（同比）"

        combined = pd.concat([cur_show, yoy_show], ignore_index=True)

        def shade_yoy(row):
            return ["background-color: #f0f4f8"] * len(row) if "同比" in str(row[group_label]) else [""] * len(row)

        styled = combined.style.format(fmt_map).apply(shade_yoy, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)

        st.markdown("##### 同比变化（当期 vs 同比时段）")
        merged = cur.merge(
            yoy, on=group_col, how="outer", suffixes=("_当期", "_同比")
        ).fillna(0)

        delta_rows = []
        for _, r in merged.iterrows():
            row = {group_label: r[group_col]}
            for k in ["订单数", "人数", "GMV", "客单价"]:
                cur_v  = r.get(f"{k}_当期", 0)
                yoy_v  = r.get(f"{k}_同比", 0)
                if yoy_v == 0:
                    row[f"{k}同比"] = "—"
                else:
                    pct = (cur_v - yoy_v) / abs(yoy_v) * 100
                    arrow = "▲" if cur_v >= yoy_v else "▼"
                    row[f"{k}同比"] = f"{arrow}{abs(pct):.1f}%"
            delta_rows.append(row)
        delta_df = pd.DataFrame(delta_rows)

        def color_delta(v):
            if not isinstance(v, str) or v == "—":
                return ""
            if v.startswith("▲"):
                return "color:#FF4B4B;font-weight:600"
            if v.startswith("▼"):
                return "color:#21BA45;font-weight:600"
            return ""

        styled_delta = delta_df.style
        for c in delta_df.columns:
            if c != group_label:
                styled_delta = styled_delta.applymap(color_delta, subset=[c])
        st.dataframe(styled_delta, use_container_width=True, hide_index=True)
    else:
        st.dataframe(
            cur_show.style.format(fmt_map),
            use_container_width=True, hide_index=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 1：新老客趋势（含同比 + 汇总表）
# ═══════════════════════════════════════════════════════════════════════════════

def _build_trend_daily(df: pd.DataFrame, date_col: str, count_mode,
                       by_channel: bool, channel_col: str = "influencer_name") -> pd.DataFrame:
    grp_cols = [date_col, "customer_type"]
    if by_channel and channel_col:
        grp_cols = [date_col, channel_col, "customer_type"]
    if "GMV" in count_mode:
        return df.groupby(grp_cols)["gmv"].sum().reset_index(name="value")
    elif "人数" in count_mode:
        # 人数按互斥口径(与表头一致)：每个周期内有任意新客订单=新客，否则=老客；
        # 这样 新客+老客 = 该周期去重人数，不会因「同人既有新客单又有老客单」重复计。
        keys = [date_col] + ([channel_col] if by_channel and channel_col else [])
        total_p = df.groupby(keys)["user_id"].nunique().rename("value")
        new_p = (df[df["customer_type"] == "新客"].groupby(keys)["user_id"].nunique()
                 .reindex(total_p.index, fill_value=0))
        new_df = new_p.rename("value").reset_index(); new_df["customer_type"] = "新客"
        old_df = (total_p - new_p).rename("value").reset_index(); old_df["customer_type"] = "老客"
        return pd.concat([new_df, old_df], ignore_index=True)[keys + ["customer_type", "value"]]
    else:
        return df.groupby(grp_cols).size().reset_index(name="value")


def _build_summary_table(df: pd.DataFrame) -> pd.DataFrame:
    """生成 全店/新客/老客 × 订单数/人数/GMV/客单价 的汇总表。
    人数按互斥口径(与表头一致)：有任意新客订单=新客，否则=老客 → 新客+老客=全店去重人数。"""
    user_type = (
        df.groupby("user_id")["customer_type"]
        .apply(lambda s: "新客" if "新客" in s.values else "老客")
    )
    persons_map = {
        "全店": len(user_type),
        "新客": int((user_type == "新客").sum()),
        "老客": int((user_type == "老客").sum()),
    }
    rows = []
    for label, subset in [("全店", df), ("新客", df[df["customer_type"] == "新客"]),
                           ("老客", df[df["customer_type"] == "老客"])]:
        orders  = len(subset)
        persons = persons_map[label]
        gmv     = subset["gmv"].sum()
        arpu    = round(gmv / persons, 0) if persons > 0 else 0.0
        rows.append({
            "类型":     label,
            "订单数":   orders,
            "人数":     persons,
            "GMV（元）": round(gmv, 0),
            "客单价（元）": arpu,
        })
    return pd.DataFrame(rows)


def tab_trend(df: pd.DataFrame, df_yoy: pd.DataFrame, yoy_on: bool, f: dict):
    st.subheader("每日 / 汇总新老客趋势")

    col1, col2, col3 = st.columns(3)
    time_mode  = col1.radio("时间粒度", ["按天", "按月"], horizontal=True, key="t1_time")
    count_mode = col2.radio("计量指标", ["订单数", "GMV（元）", "人数"], horizontal=True, key="t1_count")
    show_pct   = col3.checkbox("显示占比 %", value=False, key="t1_pct")
    by_channel = st.checkbox("分渠道展示", value=False, key="t1_channel")

    date_col = "order_date" if time_mode == "按天" else "order_ym"

    if by_channel:
        if f.get("sel_influencers"):
            channel_col = "influencer_name"
            channel_label = "达人"
        elif f.get("sel_channel_types") and "channel_type" in df.columns:
            channel_col = "channel_type"
            channel_label = "渠道类型"
        else:
            channel_col = "influencer_name"
            channel_label = "达人"
    else:
        channel_col = None
        channel_label = ""

    daily = _build_trend_daily(df, date_col, count_mode, by_channel, channel_col)
    has_yoy = yoy_on and not df_yoy.empty

    if show_pct:
        grp_keys = [date_col] + ([channel_col] if by_channel and channel_col else [])
        tot = daily.groupby(grp_keys)["value"].transform("sum")
        daily["value"] = (daily["value"] / tot.replace(0, 1) * 100).round(2)
        y_label = "占比 (%)"
        y_range = [0, 100]
    else:
        y_label = count_mode
        y_range = None

    if by_channel and channel_col:
        st.caption(f"📊 当前分渠道维度：**{channel_label}**")

    if has_yoy:
        daily_yoy = _build_trend_daily(df_yoy, date_col, count_mode, by_channel, channel_col)

        if show_pct:
            tot_y = daily_yoy.groupby(grp_keys)["value"].transform("sum")
            daily_yoy["value"] = (daily_yoy["value"] / tot_y.replace(0, 1) * 100).round(2)

        if date_col == "order_date":
            daily_yoy[date_col] = (pd.to_datetime(daily_yoy[date_col])
                                   + pd.DateOffset(years=1)).dt.date
        elif date_col == "order_ym":
            daily_yoy[date_col] = daily_yoy[date_col].apply(
                lambda s: f"{int(s[:4])+1}{s[4:]}" if isinstance(s, str) else s
            )

        def pivot_ct(d):
            p = (d.pivot_table(index=date_col, columns="customer_type",
                               values="value", aggfunc="sum", fill_value=0)
                  .reset_index())
            for ct in ["新客", "老客"]:
                if ct not in p.columns:
                    p[ct] = 0.0
            p[date_col] = p[date_col].astype(str)
            return p

        mp = pivot_ct(daily)
        yp = pivot_ct(daily_yoy)

        fig = go.Figure()
        fig.add_trace(go.Bar(name="新客（今年）", x=mp[date_col], y=mp["新客"],
                             offsetgroup=0, marker_color="#FF6B6B"))
        fig.add_trace(go.Bar(name="老客（今年）", x=mp[date_col], y=mp["老客"],
                             offsetgroup=0, base=mp["新客"].values,
                             marker_color="#4ECDC4"))
        fig.add_trace(go.Bar(name="新客（同比）", x=yp[date_col], y=yp["新客"],
                             offsetgroup=1, marker_color="#FFB3B3"))
        fig.add_trace(go.Bar(name="老客（同比）", x=yp[date_col], y=yp["老客"],
                             offsetgroup=1, base=yp["新客"].values,
                             marker_color="#B3EFEC"))
        fig.update_layout(
            barmode="overlay",
            height=450,
            yaxis_title=y_label,
            xaxis_title="日期",
            hovermode="x unified",
            legend_title="分类",
        )
        if y_range:
            fig.update_yaxes(range=y_range)
        fig.update_xaxes(tickangle=-30)

    elif by_channel and channel_col:
        daily[date_col] = daily[date_col].astype(str)
        fig = px.bar(
            daily, x=date_col, y="value", color="customer_type",
            barmode="stack", facet_col=channel_col, facet_col_wrap=3,
            color_discrete_map={"新客": "#FF6B6B", "老客": "#4ECDC4"},
            labels={"value": y_label, date_col: "日期", "customer_type": "客户类型",
                    channel_col: channel_label},
            height=450,
        )
        fig.update_xaxes(tickangle=-30)
        fig.update_layout(hovermode="x unified")
    else:
        daily[date_col] = daily[date_col].astype(str)
        fig = px.bar(
            daily, x=date_col, y="value", color="customer_type",
            barmode="stack",
            color_discrete_map={"新客": "#FF6B6B", "老客": "#4ECDC4"},
            labels={"value": y_label, date_col: "日期", "customer_type": "客户类型"},
            height=450,
        )
        fig.update_xaxes(tickangle=-30)
        fig.update_layout(hovermode="x unified")

    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 📋 新老客汇总指标")
    summary_df = _build_summary_table(df)

    if yoy_on and not df_yoy.empty:
        summary_yoy = _build_summary_table(df_yoy)
        cur_map = summary_df.set_index("类型")

        def delta_str(cur, prev, is_gmv=False):
            if prev == 0:
                return "—"
            d = cur - prev
            pct = d / abs(prev) * 100
            arrow = "▲" if d >= 0 else "▼"
            val_str = f"¥{abs(d):,.0f}" if is_gmv else f"{abs(d):.2f}" if abs(d) < 1 else f"{abs(d):,.0f}"
            return f"{arrow}{val_str}（{abs(pct):.1f}%）"

        yoy_rows = []
        for _, yrow in summary_yoy.iterrows():
            label = yrow["类型"]
            crow  = cur_map.loc[label] if label in cur_map.index else None
            r = {
                "类型":     label + "（同比）",
                "订单数":   int(yrow["订单数"]),
                "人数":     int(yrow["人数"]),
                "GMV（元）": yrow["GMV（元）"],
                "客单价（元）": yrow["客单价（元）"],
            }
            if crow is not None:
                r["订单数增减"] = delta_str(crow["订单数"],   yrow["订单数"])
                r["人数增减"]   = delta_str(crow["人数"],     yrow["人数"])
                r["GMV增减"]    = delta_str(crow["GMV（元）"], yrow["GMV（元）"], is_gmv=True)
                r["客单价增减"] = delta_str(crow["客单价（元）"], yrow["客单价（元）"], is_gmv=True)
            yoy_rows.append(r)

        yoy_df = pd.DataFrame(yoy_rows)
        for col in ["订单数增减", "人数增减", "GMV增减", "客单价增减"]:
            summary_df[col] = ""
        combined = pd.concat([summary_df, yoy_df], ignore_index=True)

        def color_delta(val):
            if not isinstance(val, str) or val in ("", "—"):
                return ""
            if val.startswith("▲"):
                return "color: #FF4B4B; font-weight:600"
            if val.startswith("▼"):
                return "color: #21BA45; font-weight:600"
            return ""

        fmt = {
            "订单数": "{:,}", "人数": "{:,}",
            "GMV（元）": "¥{:,.0f}", "客单价（元）": "¥{:,.0f}",
        }
        styled = combined.style.format(fmt)
        for dc in ["订单数增减", "人数增减", "GMV增减", "客单价增减"]:
            styled = styled.applymap(color_delta, subset=[dc])
        def shade_yoy_rows(row):
            return ["background-color: #f0f4f8"] * len(row) if "同比" in str(row["类型"]) else [""] * len(row)
        styled = styled.apply(shade_yoy_rows, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.dataframe(
            summary_df.style.format({
                "订单数": "{:,}", "人数": "{:,}",
                "GMV（元）": "¥{:,.0f}", "购买频次": "{:.2f}",
            }),
            use_container_width=True, hide_index=True,
        )

    _period_label = "日期" if date_col == "order_date" else "月份"
    with st.expander(f"查看明细数据（按{_period_label}，可下载）"):
        detail_keys = ([date_col] + ([channel_col] if by_channel and channel_col else [])
                       + ["customer_type"])
        detail = df.groupby(detail_keys).agg(
            订单数=("gmv", "count"),
            GMV=("gmv", "sum"),
            人数=("user_id", "nunique"),
        ).reset_index().sort_values(detail_keys)
        detail["GMV"] = detail["GMV"].round(2)
        detail[date_col] = detail[date_col].astype(str)
        rename_map = {date_col: _period_label, "customer_type": "客户类型"}
        if by_channel and channel_col:
            rename_map[channel_col] = channel_label
        detail = detail.rename(columns=rename_map)
        st.dataframe(detail, use_container_width=True, hide_index=True)
        st.download_button(
            "📥 下载明细 CSV",
            data=detail.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"trend_detail_{'day' if date_col == 'order_date' else 'month'}.csv",
            mime="text/csv", key="t1_dl_detail",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 2：货品分析
# ═══════════════════════════════════════════════════════════════════════════════

def tab_product(df: pd.DataFrame):
    st.subheader("货品新老客占比 & 销量排名")

    c1, c2 = st.columns([2, 1])
    sku_kw2 = c1.text_input("货号关键词过滤（仅此图表）", "", key="sku_kw2")
    metric  = c2.radio("指标", ["GMV（元）", "人数"], horizontal=True, key="prod_metric")

    if sku_kw2:
        df = df[df["sku"].str.contains(sku_kw2, case=False, na=False)]

    if df.empty:
        st.info("没有匹配的货号")
        return

    use_gmv = "GMV" in metric
    metric_label = "GMV（元）" if use_gmv else "人数"

    if use_gmv:
        total_store = df["gmv"].sum()
        sku_grp     = df.groupby(["sku", "customer_type"])["gmv"].sum().reset_index(name="value")
        sku_total   = df.groupby("sku")["gmv"].sum().reset_index(name="total")
    else:
        total_store = df["user_id"].nunique()
        sku_grp     = df.groupby(["sku", "customer_type"])["user_id"].nunique().reset_index(name="value")
        sku_total   = df.groupby("sku")["user_id"].nunique().reset_index(name="total")

    sku_total = sku_total.sort_values("total", ascending=False)
    sku_order = sku_total["sku"].tolist()

    sku_pivot = sku_grp.pivot(index="sku", columns="customer_type", values="value").fillna(0)
    for ct in ("新客", "老客"):
        if ct not in sku_pivot.columns:
            sku_pivot[ct] = 0.0
    if use_gmv:
        sku_pivot["total"] = sku_pivot["新客"] + sku_pivot["老客"]
    else:
        # 人数：互斥口径(与表头一致)。新客=有任意新客单的人；老客=该货号去重人数−新客，
        # 避免「同货号既有新单又有老单」的人被新客/老客重复计。
        sku_pivot["total"] = sku_total.set_index("sku")["total"].reindex(sku_pivot.index).fillna(0)
        sku_pivot["老客"]  = (sku_pivot["total"] - sku_pivot["新客"]).clip(lower=0)
    sku_pivot["新客_pct"] = sku_pivot["新客"] / sku_pivot["total"].replace(0, 1) * 100
    sku_pivot["老客_pct"] = sku_pivot["老客"] / sku_pivot["total"].replace(0, 1) * 100
    sku_pivot = sku_pivot.reindex(sku_order)

    # ── 左：排名 & 全店占比；右：新老客占比 ──
    col_left, col_right = st.columns([2, 3])

    with col_left:
        st.markdown(f"**{metric_label}排名 & 全店占比**")
        display_df = sku_total.head(50).copy()
        display_df["全店占比(%)"] = (display_df["total"] / total_store * 100).round(2) if total_store else 0
        fig2 = go.Figure(go.Bar(
            y=display_df["sku"],
            x=display_df["total"],
            orientation="h",
            marker_color="#6C8EBF",
            text=[
                f"{v:,.0f}  ({p:.1f}%)"
                for v, p in zip(display_df["total"], display_df["全店占比(%)"])
            ],
            textposition="outside",
            hovertemplate="货号: %{y}<br>数值: %{x:,.0f}<extra></extra>",
        ))
        fig2.update_layout(
            height=max(350, len(display_df) * 28 + 120),
            xaxis_title=metric_label,
            yaxis=dict(title="货号", autorange="reversed"),
        )
        st.plotly_chart(fig2, use_container_width=True)

    with col_right:
        st.markdown("**各货号新老客占比（%）**")
        fig1 = go.Figure()
        for ct, color in [("新客", "#FF6B6B"), ("老客", "#4ECDC4")]:
            fig1.add_trace(go.Bar(
                name=ct,
                y=sku_pivot.index,
                x=sku_pivot[f"{ct}_pct"],
                orientation="h",
                marker_color=color,
                text=sku_pivot[f"{ct}_pct"].round(1).astype(str) + "%",
                textposition="inside",
                hovertemplate=f"{ct}: %{{x:.1f}}%<extra></extra>",
            ))
        fig1.update_layout(
            barmode="stack",
            height=max(350, len(sku_pivot) * 28 + 120),
            xaxis=dict(title="占比 (%)", range=[0, 100]),
            yaxis=dict(title="货号", autorange="reversed"),
            legend_title="客户类型",
        )
        st.plotly_chart(fig1, use_container_width=True)

    # ── 下载表：一次同时含 GMV 和 人数 两套指标 ──
    st.markdown("#### 📥 货品明细数据（可下载）")

    gmv_total_store   = df["gmv"].sum()
    ppl_total_store   = df["user_id"].nunique()

    gmv_total = df.groupby("sku")["gmv"].sum().reset_index(name="GMV（元）")
    ppl_total = df.groupby("sku")["user_id"].nunique().reset_index(name="人数")

    gmv_new = df[df["customer_type"] == "新客"].groupby("sku")["gmv"].sum().reset_index(name="新客GMV")
    gmv_old = df[df["customer_type"] == "老客"].groupby("sku")["gmv"].sum().reset_index(name="老客GMV")
    ppl_new = df[df["customer_type"] == "新客"].groupby("sku")["user_id"].nunique().reset_index(name="新客人数")
    ppl_old = df[df["customer_type"] == "老客"].groupby("sku")["user_id"].nunique().reset_index(name="老客人数")

    full = gmv_total.merge(ppl_total, on="sku", how="outer") \
                    .merge(gmv_new, on="sku", how="left") \
                    .merge(gmv_old, on="sku", how="left") \
                    .merge(ppl_new, on="sku", how="left") \
                    .merge(ppl_old, on="sku", how="left").fillna(0)

    full["GMV全店占比(%)"]  = (full["GMV（元）"] / gmv_total_store * 100).round(2) if gmv_total_store else 0
    full["人数全店占比(%)"] = (full["人数"]      / ppl_total_store * 100).round(2) if ppl_total_store else 0
    full["GMV新客占比(%)"]  = (full["新客GMV"]   / full["GMV（元）"].replace(0, 1) * 100).round(2)
    full["GMV老客占比(%)"]  = (full["老客GMV"]   / full["GMV（元）"].replace(0, 1) * 100).round(2)
    full["人数新客占比(%)"] = (full["新客人数"]  / full["人数"].replace(0, 1)      * 100).round(2)
    full["人数老客占比(%)"] = (full["老客人数"]  / full["人数"].replace(0, 1)      * 100).round(2)

    full = full.rename(columns={"sku": "货号"})
    sort_col = "GMV（元）" if use_gmv else "人数"
    full = full.sort_values(sort_col, ascending=False).reset_index(drop=True)

    cols_order = [
        "货号",
        "GMV（元）", "GMV全店占比(%)", "新客GMV", "老客GMV", "GMV新客占比(%)", "GMV老客占比(%)",
        "人数",      "人数全店占比(%)", "新客人数", "老客人数", "人数新客占比(%)", "人数老客占比(%)",
    ]
    full = full[[c for c in cols_order if c in full.columns]]

    int_cols = ["GMV（元）", "新客GMV", "老客GMV", "人数", "新客人数", "老客人数"]
    for c in int_cols:
        if c in full.columns:
            full[c] = full[c].round(0).astype(int)

    st.dataframe(full, use_container_width=True, hide_index=True)

    csv_bytes = full.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="⬇️ 下载 CSV（含 GMV + 人数 全套指标）",
        data=csv_bytes,
        file_name="货品分析_GMV+人数.csv",
        mime="text/csv",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 3：老客复购周期
# ═══════════════════════════════════════════════════════════════════════════════

def tab_repurchase_cycle(df: pd.DataFrame, pairs: pd.DataFrame):
    st.subheader("老客平均复购周期")
    st.caption("仅统计老客的相邻两次购买之间的平均间隔天数。")

    user_ids = set(df[df["customer_type"] == "老客"]["user_id"].unique())
    p = pairs[pairs["user_id"].isin(user_ids)].copy()

    if p.empty:
        st.info("当前筛选条件下没有老客复购记录，请扩大时间范围或渠道范围")
        return

    dim       = st.radio("分析维度", ["渠道 → 渠道", "货号 → 货号"], horizontal=True, key="cycle_dim")
    min_count = st.slider("最少转换次数（去除噪音）", 1, 100, 5, key="cycle_min")

    from_col = "from_influencer" if "渠道" in dim else "from_sku"
    to_col   = "to_influencer"   if "渠道" in dim else "to_sku"

    cycle = (
        p.groupby([from_col, to_col])
        .agg(avg_days=("days_between", "mean"), count=("days_between", "count"))
        .reset_index()
    )
    cycle = cycle[cycle["count"] >= min_count]

    if cycle.empty:
        st.info('没有足够的数据，请降低"最少转换次数"阈值')
        return

    pivot = cycle.pivot(index=from_col, columns=to_col, values="avg_days").round(1)
    text_annot = [[f"{v:.0f}天" if pd.notna(v) else "" for v in row] for row in pivot.values]

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        colorscale="Blues",
        text=text_annot,
        texttemplate="%{text}",
        hovertemplate="从 %{y}<br>→ %{x}<br>平均 %{z:.0f} 天<extra></extra>",
        colorbar=dict(title="天数"),
    ))
    dim_label = "渠道" if "渠道" in dim else "货号"
    fig.update_layout(
        title=f"{dim_label}间平均复购间隔（天）",
        height=max(400, len(pivot) * 45 + 150),
        xaxis_title=f"下一次购买{dim_label}",
        yaxis_title=f"本次购买{dim_label}",
        xaxis=dict(tickangle=-30),
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("查看明细数据"):
        st.dataframe(
            cycle.rename(columns={
                from_col: "来源", to_col: "去向",
                "avg_days": "平均间隔（天）", "count": "转换次数"
            }).sort_values("平均间隔（天）"),
            use_container_width=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 4：老客复购率
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_repurchase(df_base: pd.DataFrame, pairs_base: pd.DataFrame,
                     first_col: str, from_col: str, to_col: str,
                     sel_first: list, ct_col: str = "customer_type") -> tuple:
    use_r12 = (ct_col == "customer_type_r12")

    if use_r12:
        entry = df_base[df_base["customer_type_r12"] == "新客"].copy()
        entry_pair_mask = pairs_base["customer_type_r12"] == "新客"
    else:
        entry = df_base[df_base["purchase_rank"] == 1].copy()
        entry_pair_mask = pairs_base["from_rank"] == 1

    if sel_first:
        entry = entry[entry[first_col].isin(sel_first)]

    total_first = entry["user_id"].nunique()
    if total_first == 0:
        return 0, 0, 0.0, pd.DataFrame(), pd.DataFrame()

    p = pairs_base[
        entry_pair_mask &
        pairs_base["user_id"].isin(entry["user_id"])
    ].copy()
    if sel_first:
        p = p[p[from_col].isin(sel_first)]

    repurchased  = p["user_id"].nunique()
    overall_rate = repurchased / total_first * 100

    next_dist = (
        p.groupby(to_col)
        .agg(复购次数=("user_id", "count"), 复购人数=("user_id", "nunique"))
        .reset_index()
        .sort_values("复购次数", ascending=False)
    )
    next_dist["复购率(%)"] = (next_dist["复购人数"] / total_first * 100).round(2)
    return total_first, repurchased, overall_rate, next_dist, p


def tab_repurchase_rate(df: pd.DataFrame, pairs: pd.DataFrame):
    st.subheader("老客复购率分析")
    st.caption("老客定义以左侧侧边栏「⚙️ 老客判定规则」为准，默认：历史任意时间有过 ≥550元 且成交(已完成/已发货/待发货，排除已关闭)的订单即为老客。")

    ct_col = "customer_type_r12"
    df    = df.copy()
    pairs = pairs.copy()
    if ct_col in pairs.columns:
        pairs["customer_type"] = pairs[ct_col]

    dim = st.radio("首次购买维度", ["渠道（达人）", "货号"], horizontal=True, key="rr_dim")

    if dim == "渠道（达人）":
        first_col = "influencer_name"
        from_col, to_col = "from_influencer", "to_influencer"
        options = sorted(df["influencer_name"].unique().tolist())
        label   = "选择首次购买达人"
    else:
        first_col = "sku"
        from_col, to_col = "from_sku", "to_sku"
        options = sorted(df["sku"].unique().tolist())
        label   = "选择首次购买货号"

    sel_first = st.multiselect(label, options, default=[], key="rr_sel")

    st.markdown("#### 按渠道类型查看")
    has_channel = "channel_type" in df.columns and "from_channel_type" in pairs.columns

    if has_channel:
        channel_types = sorted(df["channel_type"].dropna().unique().tolist())
        sel_ct = st.radio(
            "渠道类型",
            ["全部"] + channel_types,
            horizontal=True,
            key="rr_ct",
        )
        if sel_ct != "全部":
            df_view    = df[df["channel_type"] == sel_ct].copy()
            pairs_view = pairs[pairs["from_channel_type"] == sel_ct].copy()
            influencers_in_ct = sorted(df_view["influencer_name"].dropna().unique().tolist())
            st.caption(f"「{sel_ct}」下达人：{', '.join(influencers_in_ct)}")
        else:
            df_view    = df
            pairs_view = pairs
    else:
        df_view, pairs_view = df, pairs

    total_first, repurchased, overall_rate, next_dist, p_repurch = _calc_repurchase(
        df_view, pairs_view, first_col, from_col, to_col, sel_first, "customer_type_r12"
    )

    if total_first == 0:
        st.info("没有符合条件的首次购买记录")
        return

    st.markdown("#### 环比趋势（月度复购率）")
    rank1_all  = df_view[df_view["customer_type_r12"] == "新客"].copy()
    entry_mask = pairs_view["customer_type_r12"] == "新客"

    if sel_first:
        rank1_all = rank1_all[rank1_all[first_col].isin(sel_first)]
    rank1_all["first_ym"] = pd.to_datetime(rank1_all["pay_time"]).dt.strftime("%Y-%m")

    p_all = pairs_view[
        entry_mask &
        pairs_view["user_id"].isin(rank1_all["user_id"])
    ].copy()
    if sel_first:
        p_all = p_all[p_all[from_col].isin(sel_first)]

    if not p_all.empty and not rank1_all.empty:
        monthly_first = rank1_all.groupby("first_ym")["user_id"].nunique().reset_index()
        monthly_first.columns = ["月份", "首购人数"]

        p_all_ym = p_all.merge(
            rank1_all[["user_id", "first_ym"]], on="user_id", how="left"
        )
        monthly_repurch = p_all_ym.groupby("first_ym")["user_id"].nunique().reset_index()
        monthly_repurch.columns = ["月份", "复购人数"]

        mom = monthly_first.merge(monthly_repurch, on="月份", how="left").fillna(0)
        mom["复购率(%)"] = (mom["复购人数"] / mom["首购人数"].replace(0, 1) * 100).round(2)
        mom["环比变化(pct)"] = mom["复购率(%)"].diff().round(2)

        fig_mom = px.line(
            mom, x="月份", y="复购率(%)",
            markers=True,
            text="复购率(%)",
            color_discrete_sequence=["#4ECDC4"],
            height=420,
            title="月度复购率趋势（环比）",
        )
        fig_mom.update_traces(texttemplate="%{text:.1f}%", textposition="top center")
        fig_mom.update_layout(hovermode="x unified")
        st.plotly_chart(fig_mom, use_container_width=True)

        with st.expander("环比数据表"):
            st.dataframe(mom, use_container_width=True, hide_index=True)

    st.markdown("#### 整体复购概况")
    c1, c2, c3 = st.columns(3)
    c1.metric("首次购买用户数", f"{total_first:,}")
    c2.metric("有复购用户数",   f"{repurchased:,}")
    c3.metric("复购率",         f"{overall_rate:.1f}%")

    if next_dist.empty:
        st.info("该条件下没有复购数据")
        return

    st.markdown("#### 复购去向分布")

    if to_col == "to_influencer" and "to_channel_type" in p_repurch.columns:
        channel_agg = (
            p_repurch.groupby("to_channel_type")
            .agg(复购次数=("user_id", "count"), 复购人数=("user_id", "nunique"))
            .reset_index()
            .rename(columns={"to_channel_type": "渠道类型"})
            .sort_values("复购次数", ascending=False)
        )
        channel_agg["复购率(%)"] = (channel_agg["复购人数"] / total_first * 100).round(2)

        fig_ch = px.bar(
            channel_agg,
            x="渠道类型", y="复购率(%)",
            color="复购次数",
            text="复购率(%)",
            color_continuous_scale="Blues",
            labels={"渠道类型": "复购去向（渠道）", "复购率(%)": "复购率 (%)"},
            height=320,
            title="复购去向汇总（渠道）",
        )
        fig_ch.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig_ch.update_yaxes(range=[0, channel_agg["复购率(%)"].max() * 1.3])
        st.plotly_chart(fig_ch, use_container_width=True)

        for _, row in channel_agg.iterrows():
            ct_name  = row["渠道类型"]
            p_sub    = p_repurch[p_repurch["to_channel_type"] == ct_name]
            inf_agg  = (
                p_sub.groupby(to_col)
                .agg(复购次数=("user_id", "count"), 复购人数=("user_id", "nunique"))
                .reset_index()
                .sort_values("复购次数", ascending=False)
            )
            inf_agg["复购率(%)"] = (inf_agg["复购人数"] / total_first * 100).round(2)
            n = len(inf_agg)
            with st.expander(f"📂 展开「{ct_name}」— 共 {n} 个达人"):
                fig_sub = px.bar(
                    inf_agg,
                    x=to_col, y="复购率(%)",
                    color="复购次数",
                    text="复购率(%)",
                    color_continuous_scale="Blues",
                    labels={to_col: "达人", "复购率(%)": "复购率 (%)"},
                    height=350,
                )
                fig_sub.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig_sub.update_yaxes(range=[0, inf_agg["复购率(%)"].max() * 1.3])
                st.plotly_chart(fig_sub, use_container_width=True)
    else:
        fig = px.bar(
            next_dist,
            x=to_col, y="复购率(%)",
            color="复购次数",
            text="复购率(%)",
            color_continuous_scale="Blues",
            labels={to_col: "复购去向", "复购率(%)": "复购率 (%)"},
            height=400,
        )
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    p_view = pairs_view[
        (pairs_view["user_id"].isin(df_view[df_view["purchase_rank"] == 1]["user_id"])) &
        (pairs_view["from_rank"] == 1)
    ]
    if not p_view.empty:
        st.markdown("**复购间隔分布**")
        fig2 = px.histogram(
            p_view, x="days_between", nbins=30,
            labels={"days_between": "间隔天数", "count": "次数"},
            color_discrete_sequence=["#4ECDC4"], height=300,
        )
        fig2.update_layout(bargap=0.05)
        st.plotly_chart(fig2, use_container_width=True)

    with st.expander("查看明细数据（可下载）"):
        detail = next_dist.rename(columns={to_col: "复购去向"})
        st.dataframe(detail, use_container_width=True, hide_index=True)
        csv_bytes = detail.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="⬇️ 下载复购率明细 CSV",
            data=csv_bytes,
            file_name="复购率明细.csv",
            mime="text/csv",
            key="rr_download",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab：RFM 模型（CRM）
# ═══════════════════════════════════════════════════════════════════════════════

# R 桶定义：标签 → (下界天, 上界天)，区间为 (lower, upper]
_R_BUCKETS = [
    ("0-30天",    0, 30),
    ("30-60天",   30, 60),
    ("60-90天",   60, 90),
    ("90-180天",  90, 180),
    ("180-365天", 180, 365),
    ("365+天",    365, None),
]
_F_BUCKETS = [
    ("1次",    1, 1),
    ("2次",    2, 2),
    ("3-5次",  3, 5),
    ("6-10次", 6, 10),
    ("10+次",  11, None),
]
_M_BUCKETS = [
    ("¥0-500",        0,     500),
    ("¥500-2000",     500,   2000),
    ("¥2000-5000",    2000,  5000),
    ("¥5000-10000",   5000,  10000),
    ("¥10000+",       10000, None),
]


def _r_date_range(label: str, cutoff: pd.Timestamp) -> str:
    lo, hi = next((l, h) for lab, l, h in _R_BUCKETS if lab == label)
    if hi is None:
        end = cutoff - pd.Timedelta(days=lo)
        return f"≤ {end.strftime('%Y-%m-%d')}"
    start = cutoff - pd.Timedelta(days=hi) + pd.Timedelta(days=1)
    end   = cutoff - pd.Timedelta(days=lo)
    return f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"


def _bucket_label(v: float, buckets: list) -> str:
    for label, lo, hi in buckets:
        if hi is None:
            if v >= lo:
                return label
        else:
            if lo <= v <= hi:
                return label
            # 兼容 (lo, hi] 形式（R 桶用此）
            if lo < v <= hi:
                return label
    return buckets[-1][0]


def _r_bucket_label(days: float) -> str:
    """R 桶按 (下界, 上界] 区间分配，最短桶优先（去重）"""
    for label, lo, hi in _R_BUCKETS:
        if hi is None:
            if days > lo:
                return label
        else:
            if days <= hi and (lo == 0 or days > lo):
                return label
    return _R_BUCKETS[-1][0]


def tab_rfm(df: pd.DataFrame, orders_all: pd.DataFrame, f: dict):
    st.subheader("📐 RFM 模型（CRM 用）")
    st.caption(
        "**回购窗口 = 全局筛选「时间范围」**；**分析截止日 = 时间范围开始日的前一天**（R 桶从此日往前推算）。"
        "R=最近一次购买距截止日天数；F=截止日前累计订单数；M=截止日前累计 GMV。"
        "每个用户**只计入一次**：R 桶按最近购买日划分（若落入 0-30 桶，则不再计入 30-60 桶）。"
    )

    if not f.get("date_range") or len(f["date_range"]) != 2:
        st.info("请在左侧选择有效的时间范围。")
        return

    repurch_start_ts = pd.Timestamp(f["date_range"][0])
    repurch_end_ts   = pd.Timestamp(f["date_range"][1])
    cutoff_ts        = repurch_start_ts - pd.Timedelta(days=1)
    repurch_days     = (repurch_end_ts - repurch_start_ts).days + 1

    st.markdown(
        f"📅 回购窗口：**{repurch_start_ts.strftime('%Y-%m-%d')} ~ {repurch_end_ts.strftime('%Y-%m-%d')}**（{repurch_days} 天）"
        f"　·　分析截止日：**{cutoff_ts.strftime('%Y-%m-%d')}**"
    )

    # ── 老客基数渠道选择（自营 / 达播 / 都算；独立于左侧全局渠道筛选）──
    rfm_chan = []
    if "channel_type" in orders_all.columns:
        chan_opts = sorted([c for c in orders_all["channel_type"].dropna().unique().tolist() if c])
        rfm_chan = st.multiselect(
            "老客基数渠道（不选 = 全部）", chan_opts, default=[], key="rfm_chan",
            help="圈定老客基数人群所在渠道（如只看自营老客 / 达播老客 / 都算）；"
                 "此处独立于左侧「渠道类型」筛选——左侧选自营、这里全选，则按所有渠道的老客算。",
        )
    st.caption(
        "**老客基数 = 截止日前「购买过」（≥550 且成交：已完成/已发货/待发货，排除已关闭）的去重用户**，"
        "每人按最近一次购买落入一个 R 桶、只算一次，各桶相加 = 基数总数。"
        "**回购率 = 该桶里在回购窗口内、且符合左侧全局筛选(如自营)又购买的比例**。"
        "分工：**「老客基数渠道」只决定基数池是哪些渠道的客户**；**回购走左侧全局筛选**。"
        "👉 想让回购人数对齐表头老客，**老客基数渠道选「全部」**(因表头按全渠道历史判定老客)。"
    )

    # ── 渠道由本 RFM 选择器控制；状态用「成交」集合(不受全局状态筛选)；金额≥老客门槛；时间另算 ──
    succ_status = ["已完成", "已发货", "待发货"]
    min_amt = float(f.get("old_cust_amount", 550))
    f_rfm = dict(f)
    f_rfm["date_range"] = ()
    f_rfm["sel_channel_types"] = []
    f_rfm["sel_influencers"] = []
    f_rfm["sel_statuses"] = []
    scoped = _base_filter(orders_all, f_rfm, ())
    scoped = scoped[scoped["order_status"].isin(succ_status) & (scoped["gmv"] >= min_amt)]
    if rfm_chan:
        scoped = scoped[scoped["channel_type"].isin(rfm_chan)]

    df_before = scoped[scoped["pay_time"] <= cutoff_ts]
    if df_before.empty:
        st.info("分析截止日之前没有成交记录，请调整截止日或筛选条件。")
        return

    # 老客基数 = 截止日前购买过(≥550 成交)的去重用户，每人一次；R/F/M 按其截止日前历史
    user_summary = df_before.groupby("user_id").agg(
        last_pay=("pay_time", "max"),
        order_count=("pay_time", "count"),
        total_gmv=("gmv", "sum"),
    ).reset_index()
    user_summary["R_days"] = (cutoff_ts - user_summary["last_pay"]).dt.days

    # 回购：在「左侧全局筛选」范围内(本期)又购买的人 —— df 已是全局筛选(渠道/状态/时间范围)后的订单。
    # 注意：回购走全局筛选(如自营)，老客基数渠道只决定"基数池"是哪些渠道的客户，二者分工不同；
    # 这样「老客基数渠道=全部」时，回购人数 ≈ 表头老客(全局自营口径)。
    df_repurch = df[(df["pay_time"] > cutoff_ts) & (df["pay_time"] <= repurch_end_ts)]
    repurch_users = set(df_repurch["user_id"].unique())
    user_summary["is_repurch"] = user_summary["user_id"].isin(repurch_users).astype(int)

    # 分桶
    user_summary["R_bucket"] = user_summary["R_days"].apply(_r_bucket_label)
    user_summary["F_bucket"] = user_summary["order_count"].apply(lambda x: _bucket_label(x, _F_BUCKETS))
    user_summary["M_bucket"] = user_summary["total_gmv"].apply(lambda x: _bucket_label(x, _M_BUCKETS))

    def render_dim(col: str, label: str, bucket_order: list, with_date: bool = False):
        agg = (
            user_summary.groupby(col)
            .agg(老客基数=("user_id", "count"), 回购人数=("is_repurch", "sum"))
            .reindex(bucket_order, fill_value=0)
            .reset_index()
            .rename(columns={col: label})
        )
        agg["回购率(%)"] = (agg["回购人数"] / agg["老客基数"].replace(0, 1) * 100).round(2)

        if with_date:
            agg.insert(1, "日期范围", agg[label].apply(lambda lab: _r_date_range(lab, cutoff_ts)))

        total = {
            label: "TTL",
            "老客基数": int(agg["老客基数"].sum()),
            "回购人数": int(agg["回购人数"].sum()),
            "回购率(%)": round(agg["回购人数"].sum() / agg["老客基数"].sum() * 100, 2) if agg["老客基数"].sum() else 0,
        }
        if with_date:
            total["日期范围"] = "—"
        agg = pd.concat([agg, pd.DataFrame([total])], ignore_index=True)
        return agg

    # ── R 表 ──
    st.markdown(f"#### 🕐 R — 最近购买距 **{cutoff_ts.strftime('%Y-%m-%d')}** 天数")
    r_order = [b[0] for b in _R_BUCKETS]
    r_table = render_dim("R_bucket", "R 桶", r_order, with_date=True)
    fmt = {"老客基数": "{:,.0f}", "回购人数": "{:,.0f}", "回购率(%)": "{:.2f}%"}
    def highlight_ttl(row, label):
        return ["background-color:#fff4e6;font-weight:600" if str(row[label]) == "TTL" else ""] * len(row)
    st.dataframe(
        r_table.style.format(fmt).apply(lambda r: highlight_ttl(r, "R 桶"), axis=1),
        use_container_width=True, hide_index=True,
    )

    # ── R 桶回购率柱状图 ──
    r_chart = r_table[r_table["R 桶"] != "TTL"]
    if not r_chart.empty:
        fig_r = px.bar(
            r_chart, x="R 桶", y="回购率(%)",
            text="回购率(%)", color="老客基数",
            color_continuous_scale="Blues",
            labels={"R 桶": "R（最近购买距今）", "回购率(%)": "回购率 (%)"},
            height=320, title="各 R 桶的回购率对比",
        )
        fig_r.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
        st.plotly_chart(fig_r, use_container_width=True)

    st.divider()

    # ── F 表 ──
    st.markdown("#### 🔁 F — 截止日前累计订单数")
    f_order = [b[0] for b in _F_BUCKETS]
    f_table = render_dim("F_bucket", "F 桶", f_order)
    st.dataframe(
        f_table.style.format(fmt).apply(lambda r: highlight_ttl(r, "F 桶"), axis=1),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── M 表 ──
    st.markdown("#### 💰 M — 截止日前累计 GMV")
    m_order = [b[0] for b in _M_BUCKETS]
    m_table = render_dim("M_bucket", "M 桶", m_order)
    st.dataframe(
        m_table.style.format(fmt).apply(lambda r: highlight_ttl(r, "M 桶"), axis=1),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── R×F 交叉透视（回购率）──
    st.markdown("#### 🧊 R × F 回购率交叉透视")
    st.caption("行=R 桶，列=F 桶；数值=回购率 (%)；hover 显示基数/回购人数。")
    cross = (
        user_summary.groupby(["R_bucket", "F_bucket"])
        .agg(基数=("user_id", "count"), 回购=("is_repurch", "sum"))
        .reset_index()
    )
    cross["回购率"] = (cross["回购"] / cross["基数"].replace(0, 1) * 100).round(2)
    cross["R_bucket"] = pd.Categorical(cross["R_bucket"], categories=r_order, ordered=True)
    cross["F_bucket"] = pd.Categorical(cross["F_bucket"], categories=f_order, ordered=True)

    rate_pivot = cross.pivot(index="R_bucket", columns="F_bucket", values="回购率").reindex(index=r_order, columns=f_order)
    base_pivot = cross.pivot(index="R_bucket", columns="F_bucket", values="基数").reindex(index=r_order, columns=f_order)
    repu_pivot = cross.pivot(index="R_bucket", columns="F_bucket", values="回购").reindex(index=r_order, columns=f_order)

    custom = []
    for i in range(len(rate_pivot.index)):
        row = []
        for j in range(len(rate_pivot.columns)):
            row.append([base_pivot.values[i][j], repu_pivot.values[i][j]])
        custom.append(row)

    fig_x = go.Figure(go.Heatmap(
        z=rate_pivot.values,
        x=rate_pivot.columns.tolist(),
        y=rate_pivot.index.tolist(),
        colorscale="YlOrRd",
        text=[[f"{v:.1f}%" if pd.notna(v) else "" for v in row] for row in rate_pivot.values],
        texttemplate="%{text}",
        customdata=custom,
        hovertemplate="R=%{y}<br>F=%{x}<br>回购率 %{z:.2f}%<br>基数 %{customdata[0]:,}<br>回购 %{customdata[1]:,}<extra></extra>",
        colorbar=dict(title="回购率(%)"),
    ))
    fig_x.update_layout(
        height=380,
        xaxis_title="F 桶（订单数）",
        yaxis_title="R 桶（最近购买距今）",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_x, use_container_width=True)

    # ── 用户级明细下载 ──
    with st.expander("📥 用户级 RFM 明细（可下载）"):
        out = user_summary.rename(columns={
            "user_id": "用户ID",
            "last_pay": "最近购买时间",
            "order_count": "累计订单数",
            "total_gmv": "累计GMV",
            "R_days": "R(天)",
            "is_repurch": "是否回购",
        }).copy()
        out["累计GMV"] = out["累计GMV"].round(2)
        st.caption(f"共 {len(out):,} 个用户，下方表格显示前 500 行。")
        st.dataframe(out.head(500), use_container_width=True, hide_index=True)
        csv_bytes = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ 下载用户级 RFM 明细 CSV",
            data=csv_bytes,
            file_name=f"RFM_用户明细_cutoff_{cutoff_ts.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="rfm_user_dl",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 5：渠道流转 Sankey
# ═══════════════════════════════════════════════════════════════════════════════

def tab_channel_flow(df: pd.DataFrame, pairs: pd.DataFrame):
    st.subheader("渠道流转分析")
    st.caption("展示用户在不同直播间/渠道之间的购买顺序流转情况。")

    user_ids = set(df["user_id"].unique())
    p = pairs[pairs["user_id"].isin(user_ids)].copy()

    c1, c2 = st.columns(2)
    max_rank  = c1.slider("展示到第几次购买", 2, 6, 4, key="flow_rank")
    min_count = c2.slider("最少流转次数（去除噪音）", 1, 100, 3, key="flow_min")

    p = p[p["from_rank"] <= max_rank - 1]

    p["source"] = "第" + p["from_rank"].astype(int).astype(str) + "次\n" + p["from_influencer"]
    p["target"] = "第" + p["to_rank"].fillna(0).astype(int).astype(str) + "次\n" + p["to_influencer"]

    flow = p.groupby(["source", "target"]).size().reset_index(name="count")
    flow = flow[flow["count"] >= min_count]

    if flow.empty:
        st.info('没有足够的流转数据，请降低"最少流转次数"')
        return

    source_total = flow.groupby("source")["count"].sum().to_dict()

    all_nodes = list(dict.fromkeys(flow["source"].tolist() + flow["target"].tolist()))
    node_idx  = {n: i for i, n in enumerate(all_nodes)}

    node_out = flow.groupby("source")["count"].sum().to_dict()

    palette = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD"]
    node_colors = []
    for n in all_nodes:
        try:
            rank = int(n.split("次")[0].replace("第", "")) - 1
        except Exception:
            rank = 0
        node_colors.append(palette[min(rank, len(palette) - 1)])

    link_customdata = []
    for _, row in flow.iterrows():
        src_total = source_total.get(row["source"], row["count"])
        conv_rate = row["count"] / src_total * 100 if src_total > 0 else 0
        link_customdata.append([src_total, round(conv_rate, 1)])

    node_labels = []
    for n in all_nodes:
        out = node_out.get(n, 0)
        node_labels.append(f"{n}（出流量{out}次）" if out > 0 else n)

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20, thickness=22,
            line=dict(color="black", width=0.5),
            label=node_labels,
            color=node_colors,
        ),
        link=dict(
            source=[node_idx[s] for s in flow["source"]],
            target=[node_idx[t] for t in flow["target"]],
            value=flow["count"].tolist(),
            customdata=link_customdata,
            hovertemplate=(
                "%{source.label}<br>"
                "→ %{target.label}<br>"
                "转化量 %{value} 次，转化率 %{customdata[1]:.1f}%"
                "<extra></extra>"
            ),
        ),
    ))
    fig.update_layout(height=580, title_text="购买顺序 × 渠道流转图")
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("查看流转明细表"):
        detail = flow.copy()
        detail["来源总成交"] = detail["source"].map(source_total)
        detail["转化率(%)"] = (detail["count"] / detail["来源总成交"] * 100).round(1)
        st.dataframe(
            detail.rename(columns={"source": "来源", "target": "去向", "count": "转化次数"})
                  .sort_values("转化次数", ascending=False),
            use_container_width=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 6：平台优惠（真实收入核算）
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_platform_discount(df: pd.DataFrame) -> pd.DataFrame:
    """
    解析 platform_discount 列 (格式: "券名-金额;券名-金额")
    返回长表 DataFrame[row_idx, coupon, amount]，row_idx 对齐 df.index。
    """
    if "platform_discount" not in df.columns:
        return pd.DataFrame(columns=["row_idx", "coupon", "amount"])
    s = df["platform_discount"].astype(str)
    s = s[s.str.len() > 0]
    if s.empty:
        return pd.DataFrame(columns=["row_idx", "coupon", "amount"])

    exploded = s.str.split(";").explode().str.strip()
    exploded = exploded[exploded.str.len() > 0]
    extracted = exploded.str.extract(COUPON_RE)
    extracted.columns = ["coupon", "amount"]
    extracted = extracted.dropna()
    extracted["coupon"] = extracted["coupon"].str.strip()
    extracted["amount"] = extracted["amount"].str.replace(",", "", regex=False).astype(float)
    out = extracted.reset_index().rename(columns={"index": "row_idx"})
    return out[["row_idx", "coupon", "amount"]]


def tab_platform_discount(df: pd.DataFrame):
    st.subheader("平台优惠 — 真实收入核算")
    st.caption(
        "客户「订单应付金额」已扣除所有优惠券，但其中**平台补贴类**优惠券会以补贴形式回到商家账户，"
        "属于真实收入。请在下方勾选属于「平台补贴」的优惠券，未勾选视为商家承担折扣，不计入真实收入。"
    )

    coupons = _parse_platform_discount(df)
    if coupons.empty:
        st.info("当前筛选范围内没有带平台优惠的订单。")
        return

    all_coupons = sorted(coupons["coupon"].unique().tolist())

    coupon_stats_all = (
        coupons.groupby("coupon")
        .agg(使用订单数=("row_idx", "nunique"), 补贴金额=("amount", "sum"))
        .reset_index()
        .sort_values("补贴金额", ascending=False)
    )

    with st.expander(f"📋 当前筛选共发现 {len(all_coupons)} 种优惠券（按金额排序）", expanded=False):
        st.dataframe(
            coupon_stats_all.rename(columns={"coupon": "优惠券名称"}),
            use_container_width=True, hide_index=True,
        )

    sel_coupons = st.multiselect(
        f"✅ 勾选「平台补贴类」优惠券（共 {len(all_coupons)} 种）",
        options=all_coupons,
        default=[],
        help="多选；只有勾选的券金额才计入「平台补贴总额」并加回真实收入。",
        key="pd_selected_coupons",
    )

    sel_set = set(sel_coupons)
    sub_coupons = coupons[coupons["coupon"].isin(sel_set)].copy()
    subsidy_per_order = (
        sub_coupons.groupby("row_idx")["amount"].sum()
        if not sub_coupons.empty
        else pd.Series(dtype=float)
    )

    customer_pay_total = float(df["gmv"].sum())
    subsidy_total      = float(subsidy_per_order.sum())
    real_income_total  = customer_pay_total + subsidy_total
    n_orders           = len(df)
    n_subsidized       = int((subsidy_per_order > 0).sum())
    uplift_pct         = (subsidy_total / customer_pay_total * 100) if customer_pay_total > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("订单数", f"{n_orders:,}", delta=f"{n_subsidized:,} 单受补贴")
    c2.metric("客户实付总额", f"¥{customer_pay_total:,.2f}")
    c3.metric("平台补贴总额", f"¥{subsidy_total:,.2f}")
    c4.metric("真实收入总额", f"¥{real_income_total:,.2f}", delta=f"+{uplift_pct:.2f}%")

    st.divider()

    monthly_df = df[["order_date", "gmv"]].copy()
    monthly_df["subsidy"] = subsidy_per_order.reindex(df.index).fillna(0).values
    monthly_df["real_income"] = monthly_df["gmv"] + monthly_df["subsidy"]
    monthly_df["ym"] = pd.to_datetime(monthly_df["order_date"]).dt.to_period("M").astype(str)
    monthly = (
        monthly_df.groupby("ym")
        .agg(客户实付=("gmv", "sum"),
             平台补贴=("subsidy", "sum"),
             真实收入=("real_income", "sum"))
        .reset_index()
    )
    fig_trend = px.line(
        monthly.melt(id_vars="ym",
                     value_vars=["客户实付", "平台补贴", "真实收入"],
                     var_name="指标", value_name="金额"),
        x="ym", y="金额", color="指标", markers=True,
        title="月度趋势：客户实付 / 平台补贴 / 真实收入",
        color_discrete_map={"客户实付": "#45B7D1", "平台补贴": "#FFA94D", "真实收入": "#FF6B6B"},
    )
    fig_trend.update_layout(xaxis_title="月份", yaxis_title="金额（元）", height=380)
    st.plotly_chart(fig_trend, use_container_width=True)

    st.subheader("各优惠券统计")
    coupon_stats_show = coupon_stats_all.copy()
    coupon_stats_show["是否计入平台补贴"] = coupon_stats_show["coupon"].isin(sel_set).map(
        {True: "✅ 已勾选", False: "—"}
    )
    coupon_stats_show = coupon_stats_show.rename(columns={"coupon": "优惠券名称"})
    coupon_stats_show["补贴金额"] = coupon_stats_show["补贴金额"].round(2)

    cc1, cc2 = st.columns([3, 2])
    with cc1:
        st.dataframe(coupon_stats_show, use_container_width=True, hide_index=True, height=380)
    with cc2:
        if not sub_coupons.empty:
            sub_sum = (
                sub_coupons.groupby("coupon")["amount"].sum().reset_index()
                .sort_values("amount", ascending=False)
            )
            fig_pie = px.pie(
                sub_sum, names="coupon", values="amount",
                title="已勾选券的补贴金额占比",
                hole=0.4,
            )
            fig_pie.update_layout(height=380)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("勾选至少一个优惠券后，这里将展示金额占比饼图。")

    st.divider()

    st.subheader("订单明细")
    only_subsidized = st.checkbox(
        "只显示有平台优惠的订单", value=True, key="pd_only_subsidized"
    )

    detail = df.copy()
    detail["平台补贴金额"] = subsidy_per_order.reindex(detail.index).fillna(0)
    detail["真实收入"] = detail["gmv"] + detail["平台补贴金额"]

    if not sub_coupons.empty:
        per_order_detail = (
            sub_coupons.groupby("row_idx")
            .apply(lambda x: "; ".join(f"{r.coupon}({r.amount:.2f})" for r in x.itertuples()))
        )
        detail["已计入券明细"] = per_order_detail.reindex(detail.index).fillna("")
    else:
        detail["已计入券明细"] = ""

    if only_subsidized:
        detail = detail[detail["平台补贴金额"] > 0]

    show_cols_map = {
        "pay_time":          "支付时间",
        "user_id":           "用户ID",
        "sku":               "货号",
        "influencer_name":   "达人",
        "channel_type":      "渠道",
        "order_status":      "订单状态",
        "gmv":               "客户实付",
        "平台补贴金额":      "平台补贴金额",
        "真实收入":          "真实收入",
        "已计入券明细":      "已计入券明细",
        "platform_discount": "平台优惠原文",
    }
    show_cols = [c for c in show_cols_map if c in detail.columns]
    detail_show = (
        detail[show_cols].rename(columns=show_cols_map)
        .sort_values("支付时间", ascending=False)
        .reset_index(drop=True)
    )
    for c in ("客户实付", "平台补贴金额", "真实收入"):
        if c in detail_show.columns:
            detail_show[c] = detail_show[c].round(2)

    st.caption(f"共 {len(detail_show):,} 行；表格默认只展示前 500 行，完整数据请使用下方下载。")
    st.dataframe(detail_show.head(500), use_container_width=True, hide_index=True)

    c_dl1, c_dl2 = st.columns(2)
    c_dl1.download_button(
        "📥 下载订单明细 CSV",
        data=detail_show.to_csv(index=False).encode("utf-8-sig"),
        file_name="platform_discount_orders.csv",
        mime="text/csv",
        key="pd_dl_detail",
    )
    summary_csv = pd.DataFrame([{
        "订单数": n_orders,
        "受补贴订单数": n_subsidized,
        "客户实付总额": round(customer_pay_total, 2),
        "平台补贴总额": round(subsidy_total, 2),
        "真实收入总额": round(real_income_total, 2),
        "补贴占实付比例(%)": round(uplift_pct, 2),
        "已勾选优惠券": "; ".join(sel_coupons) if sel_coupons else "（未勾选）",
    }]).to_csv(index=False).encode("utf-8-sig")
    c_dl2.download_button(
        "📥 下载汇总 CSV", data=summary_csv,
        file_name="platform_discount_summary.csv",
        mime="text/csv", key="pd_dl_summary",
    )


def tab_sample_repurchase(df: pd.DataFrame, df_yoy: pd.DataFrame = None, yoy_on: bool = False):
    """派样（低价试用装）回购分析。

    回购定义：在所选派样窗口内买过试用装的顾客，只要在全局筛选范围内有过正装购买即算回购。
    回购观察口径 = 左侧全局时间筛选（df 已是全局筛选后的正装订单）。
    yoy_on 时用 df_yoy（同比时间段的正装订单）对同一批买样用户算回购，给出同比差值。
    """
    st.subheader("派样分析 — 试用装转正装")
    st.caption(
        "在所选派样窗口内买过试用装（派样）的顾客，只要在全局筛选范围内"
        "**有过正装购买**即视为回购。"
        "**回购观察口径 = 左侧全局筛选**；下方四个筛选只作用于派样（活动期间）。"
    )

    try:
        samples = load_samples()
    except FileNotFoundError:
        st.info(
            "尚未生成派样数据。请把 `派样.xlsx` 放入 `data/raw/` 后重新运行 "
            "`python preprocess.py`。"
        )
        return

    # 兼容旧数据：派样类型列可能尚未生成
    if "sample_type" not in samples.columns:
        samples["sample_type"] = "未知"

    # ── 派样专属筛选（独立于左侧全局筛选）──
    s_min, s_max = samples["pay_time"].min().date(), samples["pay_time"].max().date()
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        s_date = st.date_input(
            "① 派样购买时间（活动期间）",
            value=(s_min, s_max), min_value=s_min, max_value=s_max,
            key="smp_date",
        )
    # 解析日期范围（date_input 选一个值时返回单值）
    if isinstance(s_date, (tuple, list)) and len(s_date) == 2:
        sd0, sd1 = s_date
    else:
        sd0 = sd1 = s_date if not isinstance(s_date, (tuple, list)) else s_date[0]

    # 时间窗内的派样记录 —— 货号/派样类型选项只从该窗口内取（点3：只保留当时派样时段内有的货号）
    in_window = samples[(samples["pay_time"].dt.date >= sd0) & (samples["pay_time"].dt.date <= sd1)]

    with c2:
        status_opts = sorted(samples["order_status"].unique().tolist())
        s_status = st.multiselect(
            "② 派样订单状态", status_opts, default=status_opts, key="smp_status",
        )
    with c3:
        type_opts = sorted(in_window["sample_type"].unique().tolist())
        s_type = st.multiselect(
            "③ 派样类型", type_opts, default=type_opts, key="smp_type",
            help="付邮试用 / 尝鲜礼盒 等。选项仅含所选时间窗内出现过的派样类型。",
        )
    with c4:
        # 货号选项随时间窗 + 派样类型联动，只展示窗口内实际出现过的货号
        sku_pool = in_window
        if s_type:
            sku_pool = sku_pool[sku_pool["sample_type"].isin(s_type)]
        sku_opts = sorted(sku_pool["sku"].unique().tolist())
        s_sku = st.multiselect(
            "④ 派样货号", sku_opts, default=[], key="smp_sku",
            help="留空 = 该时间窗内全部货号。选项仅含所选时间窗（及派样类型）内出现过的货号。",
        )

    smask = (samples["pay_time"].dt.date >= sd0) & (samples["pay_time"].dt.date <= sd1)
    if s_status:
        smask &= samples["order_status"].isin(s_status)
    if s_type:
        smask &= samples["sample_type"].isin(s_type)
    if s_sku:
        smask &= samples["sku"].isin(s_sku)
    smp = samples[smask]

    if smp.empty:
        st.warning("⚠️ 当前派样筛选条件下没有买样记录，请放宽条件。")
        return

    # 每个用户在所选窗口内「最早一次买样时间」作为回购门槛
    first_smp = smp.groupby("user_id")["pay_time"].min().rename("smp_time").reset_index()
    n_buyers = len(first_smp)

    # 回购 = 买样用户在全局筛选范围内的全部正装订单
    repur = df[["user_id", "pay_time", "sku", "gmv", "customer_type"]].merge(
        first_smp, on="user_id", how="inner")

    n_smp_orders   = len(smp)
    n_repur_orders = len(repur)
    n_repur_users  = repur["user_id"].nunique()
    repur_rate     = (n_repur_users / n_buyers) if n_buyers else 0.0
    repur_gmv      = float(repur["gmv"].sum())

    # 新客 = 买样用户中在全局筛选范围内有正装订单、且被判定为新客的人数。
    #   口径与全店 KPI 完全一致：只要其正装订单里出现过「新客」即记为新客；
    #   「老客判定」默认 = 交易成功(已完成) 的正装客，可由左侧「老客判定规则」修改。
    #   拉新率 = 新客人数 ÷ 买样人数。
    if not repur.empty:
        utype = repur.groupby("user_id")["customer_type"].apply(
            lambda s: "新客" if (s == "新客").any() else "老客")
        n_new_users = int((utype == "新客").sum())
    else:
        n_new_users = 0
    acq_rate = (n_new_users / n_buyers) if n_buyers else 0.0

    # YoY：同一批买样用户在左侧「同比时间段」内的正装回购表现
    yoy_active = yoy_on and df_yoy is not None and not df_yoy.empty
    y_orders = y_users = y_new = 0
    y_rate = y_gmv = y_acq = 0.0
    if yoy_active:
        repur_y = df_yoy[["user_id", "pay_time", "gmv", "customer_type"]].merge(
            first_smp, on="user_id", how="inner")
        y_orders = len(repur_y)
        y_users  = repur_y["user_id"].nunique()
        y_rate   = (y_users / n_buyers) if n_buyers else 0.0
        y_gmv    = float(repur_y["gmv"].sum())
        if not repur_y.empty:
            utype_y = repur_y.groupby("user_id")["customer_type"].apply(
                lambda s: "新客" if (s == "新客").any() else "老客")
            y_new = int((utype_y == "新客").sum())
        y_acq = (y_new / n_buyers) if n_buyers else 0.0

    # ── 维度1：活动期间整体 KPI ──
    st.markdown("##### 📊 活动期间整体（按所选派样窗口）")
    if yoy_active:
        st.caption("同比差值 = 同一批买样用户在左侧「同比时间段」内的正装回购（派样订单数 / 买样人数不随同比变化）。")
    kc = st.columns(8)
    kc[0].metric("派样订单数", f"{n_smp_orders:,}", help="所选派样窗口内的派样订单总数（含同人多单）")
    kc[1].metric("买样人数", f"{n_buyers:,}")
    kc[2].metric("新客人数", f"{n_new_users:,}",
                 delta=(f"{n_new_users - y_new:+,}" if yoy_active else None),
                 help="买样用户中、在全局筛选内有正装订单且被判定为新客的人数。"
                      "口径同全店 KPI；老客判定默认=交易成功正装客，可在左侧「老客判定规则」修改")
    kc[3].metric("拉新率", f"{acq_rate * 100:.2f}%",
                 delta=(f"{(acq_rate - y_acq) * 100:+.2f}pp" if yoy_active else None),
                 help="新客人数 ÷ 买样人数")
    kc[4].metric("回购人数", f"{n_repur_users:,}",
                 delta=(f"{n_repur_users - y_users:+,}" if yoy_active else None))
    kc[5].metric("回购订单数", f"{n_repur_orders:,}",
                 delta=(f"{n_repur_orders - y_orders:+,}" if yoy_active else None))
    kc[6].metric("回购率", f"{repur_rate * 100:.2f}%",
                 delta=(f"{(repur_rate - y_rate) * 100:+.2f}pp" if yoy_active else None),
                 help="回购人数 ÷ 买样人数")
    kc[7].metric("回购GMV", f"¥{repur_gmv:,.0f}",
                 delta=(f"{repur_gmv - y_gmv:+,.0f}" if yoy_active else None))

    st.divider()

    # ── 维度2：按派样购买周期（cohort，可切按月/按天，图表与下表联动）──
    st.markdown("##### 📅 按派样购买周期（活动期 cohort）")
    cohort_gran = st.radio("时间粒度", ["按月", "按天"], horizontal=True, key="smp_cohort_gran")
    _cfmt = "%Y-%m" if cohort_gran == "按月" else "%Y-%m-%d"
    _clabel = "派样购买月份" if cohort_gran == "按月" else "派样购买日期"

    cohort = first_smp.copy()
    cohort["period"] = cohort["smp_time"].dt.strftime(_cfmt)
    buyers_by_p = cohort.groupby("period")["user_id"].nunique().rename("买样人数")

    repur_cohort = repur[["user_id"]].drop_duplicates().merge(
        cohort[["user_id", "period"]], on="user_id", how="left")
    repur_users_by_p = repur_cohort.groupby("period")["user_id"].nunique().rename("回购人数")
    orders_cohort = repur.merge(cohort[["user_id", "period"]], on="user_id", how="left")
    repur_orders_by_p = orders_cohort.groupby("period").size().rename("回购订单数")

    cohort_df = (
        pd.concat([buyers_by_p, repur_users_by_p, repur_orders_by_p], axis=1)
        .fillna(0).astype(int).reset_index().sort_values("period")
    )
    cohort_df["回购率(%)"] = (cohort_df["回购人数"] / cohort_df["买样人数"].replace(0, 1) * 100).round(2)

    fig = px.bar(
        cohort_df, x="period", y=["买样人数", "回购人数"], barmode="group",
        title=f"各{_clabel}：买样人数 vs 回购人数",
        color_discrete_map={"买样人数": "#45B7D1", "回购人数": "#FF6B6B"},
    )
    fig.add_scatter(
        x=cohort_df["period"], y=cohort_df["回购率(%)"], name="回购率(%)",
        mode="lines+markers", yaxis="y2", line=dict(color="#FFA94D"),
    )
    # 悬停统一显示：买样人数 / 回购人数 / 回购率
    fig.update_traces(selector=dict(name="买样人数"),
                      hovertemplate="买样人数 %{y:,.0f}<extra></extra>")
    fig.update_traces(selector=dict(name="回购人数"),
                      hovertemplate="回购人数 %{y:,.0f}<extra></extra>")
    fig.update_traces(selector=dict(name="回购率(%)"),
                      hovertemplate="回购率 %{y:.2f}%<extra></extra>")
    fig.update_layout(
        xaxis_title=_clabel, yaxis_title="人数", height=400,
        xaxis=dict(type="category"),
        hovermode="x unified",
        yaxis2=dict(title="回购率(%)", overlaying="y", side="right", showgrid=False),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        cohort_df.rename(columns={"period": _clabel}),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── 回购货品：回购的正装订单按货号排名 ──
    st.markdown("##### 🛍 回购货品（回购的正装订单按货号）")
    prod = (
        repur.groupby("sku")
        .agg(回购订单数=("gmv", "count"), 回购人数=("user_id", "nunique"), 回购GMV=("gmv", "sum"))
        .reset_index().rename(columns={"sku": "货号"})
        .sort_values("回购订单数", ascending=False)
    )
    prod["回购GMV"] = prod["回购GMV"].round(0)
    st.dataframe(prod.head(500), use_container_width=True, hide_index=True, height=380)

    st.divider()

    # ── 派样货品明细：按 派样类型 × 商品（货号）看派样→回购（参考线下表）──
    st.markdown("##### 🧾 派样货品明细（按 派样类型 × 货号）")
    st.caption(
        "**派样订单数**=该派样货品的派样订单数(不去重)，**派样人数**=去重买样人数；**回购人数/订单数/金额/AUS** = 这些人在全局筛选范围内的"
        "正装订单。**回购订单数**不去重，**回购AUS** = 回购金额 ÷ 回购人数，**回购率** = 回购人数 ÷ 派样人数。"
        "同一人若试用多款会分别计入各款；每个派样类型「小计」按人去重。"
    )

    # 每个买样用户的正装回购汇总（金额 / 订单数）
    user_repur = (
        repur.groupby("user_id")
        .agg(rep_gmv=("gmv", "sum"), rep_orders=("gmv", "count")).reset_index()
    )
    repur_user_set = set(user_repur["user_id"])

    # 买样用户 ×（派样类型, 货号）去重成员关系
    memb = smp[["user_id", "sample_type", "sku"]].drop_duplicates().merge(
        user_repur, on="user_id", how="left")
    memb["is_rep"] = memb["user_id"].isin(repur_user_set)
    # 类型级（按人去重，用于小计）
    memb_type = smp[["user_id", "sample_type"]].drop_duplicates().merge(
        user_repur, on="user_id", how="left")
    memb_type["is_rep"] = memb_type["user_id"].isin(repur_user_set)

    def _detail_for(mb, keys):
        n_smp = mb.groupby(keys)["user_id"].nunique().rename("派样人数")
        rep = mb[mb["is_rep"]]
        n_rep = rep.groupby(keys)["user_id"].nunique().rename("回购人数")
        n_ord = rep.groupby(keys)["rep_orders"].sum().rename("回购订单数")
        gmv = rep.groupby(keys)["rep_gmv"].sum().rename("回购金额")
        out = pd.concat([n_smp, n_rep, n_ord, gmv], axis=1).fillna(0)
        out["回购金额"] = out["回购金额"].round(0)
        out["回购AUS"] = (out["回购金额"] / out["回购人数"].replace(0, 1)).round(0)
        out["回购率(%)"] = (out["回购人数"] / out["派样人数"].replace(0, 1) * 100).round(2)
        return out.reset_index()

    per = _detail_for(memb, ["sample_type", "sku"])
    tot = _detail_for(memb_type, ["sample_type"])

    # 派样订单数（不去重，直接按派样订单行计数）
    smp_ord_sku  = smp.groupby(["sample_type", "sku"]).size()
    smp_ord_type = smp.groupby("sample_type").size()

    cols = ["派样类型", "商品", "派样订单数", "派样人数", "回购人数", "回购订单数",
            "回购金额", "回购AUS", "回购率(%)"]
    rows = []
    for stype in sorted(per["sample_type"].unique()):
        sub = per[per["sample_type"] == stype].sort_values("派样人数", ascending=False)
        for _, r in sub.iterrows():
            rows.append([stype, r["sku"], int(smp_ord_sku.get((stype, r["sku"]), 0)),
                         int(r["派样人数"]), int(r["回购人数"]),
                         int(r["回购订单数"]), r["回购金额"], r["回购AUS"], r["回购率(%)"]])
        tr = tot[tot["sample_type"] == stype]
        if not tr.empty:
            r = tr.iloc[0]
            rows.append([stype, "小计", int(smp_ord_type.get(stype, 0)),
                         int(r["派样人数"]), int(r["回购人数"]),
                         int(r["回购订单数"]), r["回购金额"], r["回购AUS"], r["回购率(%)"]])
    detail = pd.DataFrame(rows, columns=cols)

    fmt = {"派样订单数": "{:,.0f}", "派样人数": "{:,.0f}", "回购人数": "{:,.0f}",
           "回购订单数": "{:,.0f}", "回购金额": "{:,.0f}",
           "回购AUS": "{:,.0f}", "回购率(%)": "{:.2f}%"}
    st.dataframe(detail.style.format(fmt), use_container_width=True, hide_index=True, height=460)

    cda, cdb, cdc = st.columns(3)
    cda.download_button(
        "📥 下载回购货品 CSV",
        data=prod.to_csv(index=False).encode("utf-8-sig"),
        file_name="sample_repurchase_products.csv", mime="text/csv",
        key="smp_dl_prod",
    )
    cdb.download_button(
        "📥 下载货品明细 CSV",
        data=detail.to_csv(index=False).encode("utf-8-sig"),
        file_name="sample_repurchase_detail.csv", mime="text/csv",
        key="smp_dl_detail",
    )
    cdc.download_button(
        "📥 下载 cohort CSV",
        data=cohort_df.rename(columns={"period": _clabel}).to_csv(index=False).encode("utf-8-sig"),
        file_name="sample_repurchase_cohort.csv", mime="text/csv",
        key="smp_dl_cohort",
    )


def tab_membership(df: pd.DataFrame):
    """会员分析：① 按入会时间分新/老会员看购买；② 会员 vs 非会员对比。
    购买口径 = 左侧全局筛选（df 已是全局筛选后的订单）。"""
    st.subheader("会员分析 — 新老会员 & 会员/非会员")
    st.caption(
        "会员表仅含「淘宝ID + 入会时间」。下方按入会时间区分新/老会员，"
        "并与订单表匹配看购买表现；**购买口径 = 左侧全局筛选**。"
    )

    try:
        members = load_members()
    except FileNotFoundError:
        st.info(
            "尚未生成会员数据。请把 `会员.xlsx`（含「淘宝ID」「入会时间」两列）放入 "
            "`data/raw/` 后重新运行 `python preprocess.py`。"
        )
        return

    has_time = members["join_time"].notna().any()
    if has_time:
        jmin = members["join_time"].min().date()
        jmax = members["join_time"].max().date()
        default_start = max(jmin, (members["join_time"].max() - pd.Timedelta(days=365)).date())
        nd = st.date_input(
            "① 新会员入会时间（此区间内入会 = 新会员，更早入会 = 老会员）",
            value=(default_start, jmax), min_value=jmin, max_value=jmax, key="mb_date",
        )
        if isinstance(nd, (tuple, list)) and len(nd) == 2:
            nd0, nd1 = nd
        else:
            nd0 = nd1 = nd if not isinstance(nd, (tuple, list)) else nd[0]
        is_new = (members["join_time"].dt.date >= nd0) & (members["join_time"].dt.date <= nd1)
    else:
        st.warning("会员表「入会时间」为空，暂无法区分新老会员，仅做会员/非会员对比。")
        is_new = pd.Series(False, index=members.index)

    members = members.copy()
    members["会员类型"] = "老会员"
    members.loc[is_new, "会员类型"] = "新会员"
    member_type = members.set_index("user_id")["会员类型"]
    member_ids = set(members["user_id"])

    n_total = len(members)
    n_new   = int((members["会员类型"] == "新会员").sum())
    n_old   = n_total - n_new

    mo = df[df["user_id"].isin(member_ids)].copy()
    mo["会员类型"] = mo["user_id"].map(member_type)
    n_buyers = mo["user_id"].nunique()
    conv = (n_buyers / n_total) if n_total else 0.0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("会员总数", f"{n_total:,}")
    k2.metric("新会员", f"{n_new:,}")
    k3.metric("老会员", f"{n_old:,}")
    k4.metric("有购买会员数", f"{n_buyers:,}")
    k5.metric("会员购买转化率", f"{conv * 100:.2f}%",
              help="有购买会员数 ÷ 会员总数（购买在全局筛选范围内）")

    if has_time:
        st.divider()
        st.markdown("##### 🆕 新会员 vs 老会员（购买表现）")
        rows = []
        for t in ["新会员", "老会员"]:
            n_m = int((members["会员类型"] == t).sum())
            sub = mo[mo["会员类型"] == t]
            n_b = sub["user_id"].nunique()
            rows.append({
                "会员类型": t, "会员数": n_m,
                "下单人数": n_b, "订单数": len(sub),
                "GMV": round(float(sub["gmv"].sum()), 0),
                "购买转化率(%)": round(n_b / n_m * 100, 2) if n_m else 0,
                "客单价": round(float(sub["gmv"].sum()) / n_b, 0) if n_b else 0,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("##### 📅 入会趋势（按入会月份）")
        join_trend = (
            members.dropna(subset=["join_time"])
            .groupby("join_ym")["user_id"].nunique()
            .rename("入会人数").reset_index().sort_values("join_ym")
        )
        fig = px.bar(join_trend, x="join_ym", y="入会人数", title="每月新增会员数",
                     color_discrete_sequence=["#45B7D1"])
        fig.update_layout(xaxis_title="入会月份", yaxis_title="入会人数", height=360)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("##### 👥 会员 vs 非会员（全局筛选范围内的下单用户）")
    d = df.copy()
    d["是否会员"] = "非会员"
    d.loc[d["user_id"].isin(member_ids), "是否会员"] = "会员"
    vs = (
        d.groupby("是否会员")
        .agg(下单人数=("user_id", "nunique"), 订单数=("gmv", "count"), GMV=("gmv", "sum"))
        .reset_index()
    )
    vs["客单价"]   = (vs["GMV"] / vs["下单人数"].replace(0, 1)).round(0)
    vs["购买频次"] = (vs["订单数"] / vs["下单人数"].replace(0, 1)).round(2)
    vs["GMV"]      = vs["GMV"].round(0)
    st.dataframe(vs, use_container_width=True, hide_index=True)
    st.download_button(
        "📥 下载会员/非会员对比 CSV",
        data=vs.to_csv(index=False).encode("utf-8-sig"),
        file_name="member_vs_nonmember.csv", mime="text/csv", key="mb_dl",
    )


def tab_crowd(df: pd.DataFrame):
    """上传人群分析：人群id = 淘宝ID，与订单表匹配看各人群购买情况。
    购买口径 = 左侧全局筛选。"""
    st.subheader("上传人群 — 购买匹配")
    st.caption(
        "上传的人群（人群id = 淘宝ID）与订单表匹配，看各人群的后续购买情况；"
        "**购买口径 = 左侧全局筛选**。"
    )

    try:
        crowds = load_crowds()
    except FileNotFoundError:
        st.info(
            "尚未生成人群数据。请把 `人群.xlsx`（含「人群名称」「淘宝ID」两列）放入 "
            "`data/raw/` 后重新运行 `python preprocess.py`。"
        )
        return

    all_crowds = sorted(crowds["crowd_name"].unique().tolist())
    sel = st.multiselect(
        "① 选择人群（可多选，不选 = 全部）", all_crowds, default=all_crowds, key="cr_sel",
    )
    use = crowds[crowds["crowd_name"].isin(sel)] if sel else crowds

    all_ids  = set(use["user_id"])
    sub_all  = df[df["user_id"].isin(all_ids)]
    n_buyers = sub_all["user_id"].nunique()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("人群人数（去重）", f"{len(all_ids):,}")
    k2.metric("购买人数", f"{n_buyers:,}")
    k3.metric("购买转化率", f"{(n_buyers / len(all_ids) * 100) if all_ids else 0:.2f}%",
              help="购买人数 ÷ 人群去重人数（购买在全局筛选范围内）")
    k4.metric("人群GMV", f"¥{float(sub_all['gmv'].sum()):,.0f}")

    st.divider()
    st.markdown("##### 📊 各人群购买对比")
    rows = []
    for name, grp in use.groupby("crowd_name"):
        ids = set(grp["user_id"])
        sub = df[df["user_id"].isin(ids)]
        n_b = sub["user_id"].nunique()
        rows.append({
            "人群": name, "人群人数": len(ids),
            "购买人数": n_b,
            "购买转化率(%)": round(n_b / len(ids) * 100, 2) if ids else 0,
            "订单数": len(sub),
            "GMV": round(float(sub["gmv"].sum()), 0),
            "客单价": round(float(sub["gmv"].sum()) / n_b, 0) if n_b else 0,
        })
    crowd_df = pd.DataFrame(rows).sort_values("人群人数", ascending=False)
    st.dataframe(crowd_df, use_container_width=True, hide_index=True)

    st.divider()

    # ── 人群购买趋势（全局筛选范围内，by day/month）──
    st.markdown("##### 📈 人群购买趋势（全局筛选范围内）")
    st.caption("所选人群在左侧全局筛选时间范围内的购买变化；可切换按天/按月。")
    gran = st.radio("时间粒度", ["按天", "按月"], horizontal=True, key="cr_gran")
    if sub_all.empty:
        st.info("当前所选人群在全局筛选范围内没有购买记录。")
    else:
        tcol = "order_date" if gran == "按天" else "order_ym"
        ts = (
            sub_all.groupby(tcol)
            .agg(订单数=("gmv", "count"), 购买人数=("user_id", "nunique"), GMV=("gmv", "sum"))
            .reset_index().sort_values(tcol)
        )
        ts["x"] = ts[tcol].astype(str)
        figt = px.bar(
            ts, x="x", y=["订单数", "购买人数"], barmode="group",
            title=f"人群购买趋势（{gran}）",
            color_discrete_map={"订单数": "#45B7D1", "购买人数": "#FF6B6B"},
        )
        figt.add_scatter(
            x=ts["x"], y=ts["GMV"], name="GMV", mode="lines+markers",
            yaxis="y2", line=dict(color="#FFA94D"),
        )
        # 悬停统一显示：订单数 / 购买人数 / GMV
        figt.update_traces(selector=dict(name="订单数"),
                           hovertemplate="订单数 %{y:,.0f}<extra></extra>")
        figt.update_traces(selector=dict(name="购买人数"),
                           hovertemplate="购买人数 %{y:,.0f}<extra></extra>")
        figt.update_traces(selector=dict(name="GMV"),
                           hovertemplate="GMV ¥%{y:,.0f}<extra></extra>")
        figt.update_layout(
            xaxis_title=("日期" if gran == "按天" else "月份"), yaxis_title="数量", height=400,
            xaxis=dict(type="category"),
            hovermode="x unified",
            yaxis2=dict(title="GMV", overlaying="y", side="right", showgrid=False),
        )
        st.plotly_chart(figt, use_container_width=True)

    st.divider()
    st.markdown("##### 🛍 人群购买货品（按货号）")
    prod = (
        sub_all.groupby("sku")
        .agg(订单数=("gmv", "count"), 购买人数=("user_id", "nunique"), GMV=("gmv", "sum"))
        .reset_index().rename(columns={"sku": "货号"})
        .sort_values("订单数", ascending=False)
    )
    prod["GMV"] = prod["GMV"].round(0)
    st.dataframe(prod.head(500), use_container_width=True, hide_index=True, height=360)

    cda, cdb = st.columns(2)
    cda.download_button(
        "📥 下载人群对比 CSV", data=crowd_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="crowd_comparison.csv", mime="text/csv", key="cr_dl1",
    )
    cdb.download_button(
        "📥 下载人群购买货品 CSV", data=prod.to_csv(index=False).encode("utf-8-sig"),
        file_name="crowd_products.csv", mime="text/csv", key="cr_dl2",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if not _check_password():
        st.stop()

    st.title("📊 直播间销售数据分析")

    # 拦截 Cmd/Ctrl+C：避免复制文本时触发 Streamlit 的「Clear caches」快捷键弹窗。
    # 同时挂在 window 与 document 的「捕获阶段」——window 级 capture 是事件最先经过的环节，
    # 抢在 Streamlit 的监听器之前 stopImmediatePropagation（Windows 上 Streamlit 多挂在 window，
    # 仅 document 级拦不住，故补一层 window）。不 preventDefault，浏览器原生复制仍正常。
    components.html(
        """
<script>
const w = window.parent;
function blockClearCache(e){
  if ((e.key === 'c' || e.key === 'C') && (e.metaKey || e.ctrlKey)) {
    e.stopImmediatePropagation();
  }
}
w.addEventListener('keydown', blockClearCache, true);
w.document.addEventListener('keydown', blockClearCache, true);
</script>
""",
        height=0,
    )

    st.markdown("""
<style>
[data-testid="stTabsContent"] [data-testid="stHorizontalBlock"] {
    overflow-x: auto !important;
    flex-wrap: nowrap !important;
}
[data-testid="stTabsContent"] [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"] {
    min-width: 320px;
}
</style>
""", unsafe_allow_html=True)

    try:
        orders = load_orders()
        pairs  = load_pairs()
        meta   = load_meta()
    except FileNotFoundError:
        st.error(
            "❌ 数据文件不存在！\n\n"
            "请先将 Excel 源表放入 `data/raw/` 目录，然后运行：\n"
            "```\npython preprocess.py\n```"
        )
        st.stop()

    f  = render_sidebar(meta)

    default_amount, default_days, default_r12, default_channels = (
        OLD_CUSTOMER_MIN_AMOUNT, OLD_CUSTOMER_MIN_DAYS, OLD_CUSTOMER_R12_DEFAULT, ()
    )
    sel_channels = tuple(sorted(f["old_cust_channels"]))
    need_recompute = (
        f["old_cust_amount"] != default_amount or
        f["old_cust_days"]   != default_days   or
        f["r12_window"]      != default_r12     or
        sel_channels         != default_channels
    )
    if need_recompute:
        import os
        mtime = str(os.path.getmtime("data/processed/orders.parquet"))
        orders, pairs = recompute_customer_type(
            mtime,
            float(f["old_cust_amount"]),
            int(f["old_cust_days"]),
            int(f["r12_window"]),
            sel_channels,
        )

    orders["customer_type"] = orders["customer_type_r12"]
    pairs["customer_type"]  = pairs["customer_type_r12"]

    df     = apply_filters(orders, f)
    df_yoy = apply_yoy_filters(orders, f)

    if df.empty:
        st.warning("⚠️ 没有符合筛选条件的数据，请调整筛选条件")
        st.stop()

    render_kpi(df, df_yoy if f["yoy_on"] else None)
    st.divider()

    pairs_filtered = filter_pairs(pairs, f)

    t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11 = st.tabs([
        "🏷 渠道汇总",
        "📈 新老客趋势",
        "📦 货品分析",
        "📐 RFM",
        "🔄 渠道流转",
        "🔁 复购率",
        "⏱ 复购周期",
        "🧪 派样",
        "🪪 会员",
        "👥 人群",
        "💰 平台优惠",
    ])

    with t1:
        tab_channel_summary(df, df_yoy, f["yoy_on"], f)
    with t2:
        tab_trend(df, df_yoy, f["yoy_on"], f)
    with t3:
        tab_product(df)
    with t4:
        tab_rfm(df, orders, f)
    with t5:
        tab_channel_flow(df, pairs_filtered)
    with t6:
        tab_repurchase_rate(df, pairs_filtered)
    with t7:
        tab_repurchase_cycle(df, pairs_filtered)
    with t8:
        tab_sample_repurchase(df, df_yoy, f["yoy_on"])
    with t9:
        tab_membership(df)
    with t10:
        tab_crowd(df)
    with t11:
        tab_platform_discount(df)


if __name__ == "__main__":
    main()
