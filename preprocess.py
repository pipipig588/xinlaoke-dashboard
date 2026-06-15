"""
预处理脚本
----------
把 data/raw/ 下所有 .xlsx 文件处理成 data/processed/ 下的 Parquet 文件。

每次替换源表后重新运行：
    python preprocess.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

from config import (
    AMOUNT_FIELD,
    COLUMN_MAP,
    MAX_PURCHASE_RANK,
    OLD_CUSTOMER_MIN_AMOUNT,
    OLD_CUSTOMER_MIN_DAYS,
    PROCESSED_DATA_DIR,
    RAW_DATA_DIR,
    SANKEY_MIN_COUNT,
    TRANSACTION_SUCCESS_STATUS,
)


# ── 1. 加载 ───────────────────────────────────────────────────────────────────

def load_all_excel(data_dir: str) -> pd.DataFrame:
    # 过滤掉 Excel/WPS 打开时产生的临时文件（以 .~ 或 ~$ 开头）
    files = [f for f in Path(data_dir).glob("*.xlsx")
             if not f.name.startswith(".~") and not f.name.startswith("~$")]
    if not files:
        print(f"[ERROR] 在 {data_dir} 目录下没有找到 .xlsx 文件")
        sys.exit(1)

    dfs = []
    for f in sorted(files):
        xl = pd.ExcelFile(f)
        for sheet in xl.sheet_names:
            print(f"  读取 {f.name} [sheet={sheet}] ...")
            df = pd.read_excel(xl, sheet_name=sheet, dtype=str)
            dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    print(f"  合并后共 {len(combined):,} 行")
    return combined


# ── 2. 清洗 ───────────────────────────────────────────────────────────────────

def clean(df: pd.DataFrame) -> pd.DataFrame:
    # 重命名为标准英文字段
    reverse_map = {v: k for k, v in COLUMN_MAP.items()}
    df = df.rename(columns=reverse_map)

    # ── 只保留必要列，其余全部丢弃（减少敏感信息）──
    needed = [
        "user_id",          # 新老客判断（已加密）
        "sub_order_id",     # 子订单编号（去重粒度，每行唯一）
        "sku",              # 货号
        "influencer_name",  # 达人昵称/渠道
        "order_status",     # 订单状态
        "after_sale_status",# 售后状态
        "payable_amount",   # 订单应付金额
        "pay_time",         # 支付时间（优先）
        "order_submit_time",# 备用时间（若 pay_time 为空）
        "category",         # 品类
        "channel_type",     # 渠道类型
        "platform_discount",# 平台优惠原文
    ]
    df = df[[c for c in needed if c in df.columns]].copy()

    # ── 时间字段：优先 pay_time，其次 order_submit_time ──
    for col in ("pay_time", "order_submit_time"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    if "pay_time" not in df.columns or df["pay_time"].isna().all():
        df["pay_time"] = df.get("order_submit_time", pd.NaT)

    # 去掉关键字段为空的行
    df = df.dropna(subset=["pay_time", "user_id"])
    df["user_id"] = df["user_id"].astype(str).str.strip()

    # ── 数值字段 ──
    if "payable_amount" in df.columns:
        df["payable_amount"] = pd.to_numeric(df["payable_amount"], errors="coerce").fillna(0)

    # 统一 gmv 字段
    df["gmv"] = df["payable_amount"] if "payable_amount" in df.columns else 0.0

    # ── 文本字段归一化 ──
    for col in ("sku", "order_status"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": "未知", "": "未知"})
        else:
            df[col] = "未知"
    # 售后状态："-" 与空值都视为「无售后」
    if "after_sale_status" in df.columns:
        df["after_sale_status"] = (
            df["after_sale_status"].astype(str).str.strip()
            .replace({"nan": "无售后", "": "无售后", "-": "无售后", "None": "无售后"})
        )
    else:
        df["after_sale_status"] = "无售后"
    # 达人昵称单独处理：空值标记为"货架"
    if "influencer_name" in df.columns:
        df["influencer_name"] = (
            df["influencer_name"].astype(str).str.strip()
            .replace({"nan": "货架", "": "货架"})
        )
    else:
        df["influencer_name"] = "货架"

    # 品类和渠道类型
    for col, default in [("category", "未分类"), ("channel_type", "未知")]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": default, "": default})
        else:
            df[col] = default

    # 平台优惠原文：保留为字符串，空值/"-"统一为空串
    if "platform_discount" in df.columns:
        df["platform_discount"] = (
            df["platform_discount"].astype(str).str.strip()
            .replace({"nan": "", "-": "", "None": ""})
        )
    else:
        df["platform_discount"] = ""

    # ── 时间衍生字段 ──
    df["order_date"] = df["pay_time"].dt.date
    df["order_ym"]   = df["pay_time"].dt.strftime("%Y-%m")

    # 去重 — 按「子订单编号」去重，原表每行即一个唯一子订单，只删真正重复的行。
    # （旧逻辑按 user_id+pay_time+sku+influencer 去重，会把"同人同时同货号"拆成的
    #   多个子订单误并成一行，导致订单数与 GMV 被低估约 6%，故改为按子订单编号。）
    before = len(df)
    if "sub_order_id" in df.columns:
        df["sub_order_id"] = (
            df["sub_order_id"].astype(str).str.strip()
            .replace({"nan": "", "None": ""})
        )
        has_id = df["sub_order_id"] != ""
        df = pd.concat([
            df[has_id].drop_duplicates(subset=["sub_order_id"]),
            # 子订单编号缺失的行用旧粒度兜底，避免空值被全部并成一行
            df[~has_id].drop_duplicates(subset=["user_id", "pay_time", "sku", "influencer_name"]),
        ], ignore_index=True)
    else:
        df = df.drop_duplicates(subset=["user_id", "pay_time", "sku", "influencer_name"])
    after = len(df)
    if before != after:
        print(f"  去重移除 {before - after:,} 行重复记录")

    print(f"  清洗后剩余 {len(df):,} 行")
    return df


# ── 3. 计算新老客 ──────────────────────────────────────────────────────────────
#
# 老客定义：该用户在当前订单之前（至少 OLD_CUSTOMER_MIN_DAYS 天），
#           有过 ≥ OLD_CUSTOMER_MIN_AMOUNT 元 且 订单状态=TRANSACTION_SUCCESS_STATUS 的历史订单。
#
# 实现思路：
#   1. 找出所有"有效历史成交"订单（状态=交易成功 且 金额≥550）
#   2. 对每个用户，取有效成交中最早那笔的时间 → first_qualifying_time
#   3. 当前订单时间 ≥ first_qualifying_time + 1天 → 老客，否则新客
#
# 同时记录老客的"首次有效成交"信息，用于客户数据库视图。

def label_customer_type(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["user_id", "pay_time"]).reset_index(drop=True)

    # 找有效历史成交记录
    qualifying = df[
        (df["order_status"] == TRANSACTION_SUCCESS_STATUS) &
        (df["gmv"] >= OLD_CUSTOMER_MIN_AMOUNT)
    ].copy()

    if qualifying.empty:
        print("  [警告] 没有找到任何\"交易成功且金额>=550\"的订单，所有用户均标记为新客")
        df["customer_type"] = "新客"
        df["first_qualify_time"] = pd.NaT
        df["first_qualify_influencer"] = ""
        df["first_qualify_sku"] = ""
        df["first_qualify_gmv"] = 0.0
        return df

    # 每个用户最早有效成交
    first_q = (
        qualifying.sort_values("pay_time")
        .groupby("user_id")
        .first()[["pay_time", "influencer_name", "sku", "gmv"]]
        .rename(columns={
            "pay_time":        "first_qualify_time",
            "influencer_name": "first_qualify_influencer",
            "sku":             "first_qualify_sku",
            "gmv":             "first_qualify_gmv",
        })
        .reset_index()
    )

    df = df.merge(first_q, on="user_id", how="left")

    # 成为老客的门槛时间 = first_qualify_time + 1天
    threshold = df["first_qualify_time"] + pd.Timedelta(days=OLD_CUSTOMER_MIN_DAYS)
    is_old = df["first_qualify_time"].notna() & (df["pay_time"] >= threshold)

    df["customer_type"] = "新客"
    df.loc[is_old, "customer_type"] = "老客"

    n_new = (df["customer_type"] == "新客").sum()
    n_old = (df["customer_type"] == "老客").sum()
    print(f"  新客订单: {n_new:,}  |  老客订单: {n_old:,}")

    # ── R12 老客：默认9999天（≈全时段），侧边栏可调 ──
    print("  计算 R12 老客标签（滚动9999天，默认等同全时段）...")
    df["customer_type_r12"] = "新客"

    users_with_qualifying = set(qualifying["user_id"].unique())
    q_times = qualifying[["user_id", "pay_time"]].copy()

    df_sub = df[df["user_id"].isin(users_with_qualifying)][["user_id", "pay_time"]].reset_index()
    cross = df_sub.merge(
        q_times.rename(columns={"pay_time": "q_time"}),
        on="user_id", how="left"
    )
    cross["days_diff"] = (cross["pay_time"] - cross["q_time"]).dt.days
    in_window = cross[
        (cross["days_diff"] >= OLD_CUSTOMER_MIN_DAYS) &
        (cross["days_diff"] <= 9999)
    ]
    r12_old_orig_idx = in_window["index"].unique()
    df.loc[r12_old_orig_idx, "customer_type_r12"] = "老客"

    n_r12_new = (df["customer_type_r12"] == "新客").sum()
    n_r12_old = (df["customer_type_r12"] == "老客").sum()
    print(f"  新客订单: {n_r12_new:,}  |  老客订单（R12）: {n_r12_old:,}")
    return df


# ── 4. 购买序号 ───────────────────────────────────────────────────────────────

def add_purchase_rank(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["user_id", "pay_time"])
    df["purchase_rank"] = df.groupby("user_id").cumcount() + 1
    return df


# ── 5. 购买对（用于复购周期/复购率/流转图） ────────────────────────────────────

def build_purchase_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """
    为每个用户生成相邻两次购买的"对"，包含时间间隔。
    用于：复购周期热力图、复购率分析、渠道流转 Sankey。
    """
    df_s = df.sort_values(["user_id", "pay_time"]).copy()

    shift_cols = ["pay_time", "influencer_name", "sku", "order_ym",
                  "purchase_rank", "order_date", "gmv", "order_status",
                  "category", "channel_type", "customer_type", "customer_type_r12"]

    for col in shift_cols:
        if col in df_s.columns:
            df_s[f"next_{col}"] = df_s.groupby("user_id")[col].shift(-1)

    pairs = df_s.dropna(subset=["next_pay_time"]).copy()
    pairs["days_between"] = (
        pd.to_datetime(pairs["next_pay_time"]) - pd.to_datetime(pairs["pay_time"])
    ).dt.days

    # 只保留 days_between >= 0 的（极少数时间倒序情况）
    pairs = pairs[pairs["days_between"] >= 0]

    pairs = pairs.rename(columns={
        "order_ym":             "from_ym",
        "influencer_name":      "from_influencer",
        "sku":                  "from_sku",
        "purchase_rank":        "from_rank",
        "order_date":           "from_date",
        "gmv":                  "from_gmv",
        "order_status":         "from_status",
        "next_order_ym":        "to_ym",
        "next_influencer_name": "to_influencer",
        "next_sku":             "to_sku",
        "next_purchase_rank":   "to_rank",
        "next_order_date":      "to_date",
        "next_gmv":             "to_gmv",
        "next_order_status":    "to_status",
        "category":             "from_category",
        "channel_type":         "from_channel_type",
        "next_category":        "to_category",
        "next_channel_type":    "to_channel_type",
    })

    keep = [
        "user_id", "customer_type", "customer_type_r12",
        "from_ym", "from_date", "from_influencer", "from_sku",
        "from_rank", "from_gmv", "from_status", "from_category", "from_channel_type",
        "to_ym", "to_date", "to_influencer", "to_sku",
        "to_rank", "to_gmv", "to_status", "to_category", "to_channel_type",
        "days_between",
    ]
    pairs = pairs[[c for c in keep if c in pairs.columns]]

    print(f"  购买对数量: {len(pairs):,}")
    return pairs


# ── 6. 保存 ───────────────────────────────────────────────────────────────────

def save_all(df: pd.DataFrame, pairs: pd.DataFrame, out_dir: str):
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    df.to_parquet(f"{out_dir}/orders.parquet", index=False)
    pairs.to_parquet(f"{out_dir}/purchase_pairs.parquet", index=False)

    # 渠道→达人映射（用于侧边栏联动）
    influencer_channel_map = {}
    if "channel_type" in df.columns:
        influencer_channel_map = (
            df[["influencer_name", "channel_type"]]
            .drop_duplicates()
            .set_index("influencer_name")["channel_type"]
            .to_dict()
        )

    # 品类→货号映射（用于侧边栏联动）
    category_sku_map = {}
    if "category" in df.columns:
        for cat, grp in df.groupby("category"):
            category_sku_map[cat] = sorted(grp["sku"].dropna().unique().tolist())

    meta = {
        "skus":                sorted(df["sku"].dropna().unique().tolist()),
        "influencers":         sorted(df["influencer_name"].dropna().unique().tolist()),
        "order_statuses":      sorted(df["order_status"].dropna().unique().tolist()),
        "after_sale_statuses": sorted(df["after_sale_status"].dropna().unique().tolist()) if "after_sale_status" in df.columns else [],
        "categories":          sorted(df["category"].dropna().unique().tolist()) if "category" in df.columns else [],
        "channel_types":       sorted(df["channel_type"].dropna().unique().tolist()) if "channel_type" in df.columns else [],
        "influencer_channel_map": influencer_channel_map,
        "category_sku_map":    category_sku_map,
        "date_min":            str(df["order_date"].min()),
        "date_max":            str(df["order_date"].max()),
        "gmv_max":             float(df["gmv"].max()),
        "total_rows":          len(df),
        "total_users":         df["user_id"].nunique(),
    }
    with open(f"{out_dir}/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 处理完成！")
    print(f"   订单行数  : {meta['total_rows']:,}")
    print(f"   独立买家  : {meta['total_users']:,}")
    print(f"   时间范围  : {meta['date_min']} ~ {meta['date_max']}")
    print(f"   货号数量  : {len(meta['skus'])}")
    print(f"   达人数量  : {len(meta['influencers'])}")
    print(f"   输出目录  : {out_dir}/")


# ── 主流程 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("Step 1/5  加载 Excel ...")
    raw = load_all_excel(RAW_DATA_DIR)

    print("Step 2/5  清洗数据 ...")
    df = clean(raw)

    print("Step 3/5  标记新老客 ...")
    df = label_customer_type(df)

    print("Step 4/5  计算购买序号与购买对 ...")
    df = add_purchase_rank(df)
    pairs = build_purchase_pairs(df)

    print("Step 5/5  保存文件 ...")
    save_all(df, pairs, PROCESSED_DATA_DIR)
