# ============================================================
# 字段配置 — 如果 Excel 列名有变化，只需修改这里
# ============================================================

COLUMN_MAP = {
    "order_id":          "主订单编号",
    "user_id":           "淘宝ID",
    "sub_order_id":      "子订单编号",
    "product_name":      "选购商品",
    "product_id":        "商品ID",
    "spec":              "商品规格",
    "sample_type":       "派样类型",       # 派样表：付邮试用 / 尝鲜礼盒 / 积分兑换…（货号前一列）
    "sku":               "货号",           # 品类筛选维度
    "merchant_code":     "商家编码",
    "quantity":          "商品数量",
    "item_amount":       "商品金额",
    "order_submit_time": "订单提交时间",
    "pay_month":         "支付月份",
    "pay_time":          "支付时间",       # 主要时间字段
    "pay_complete":      "支付完成时间",
    "order_status":      "订单状态",
    "after_sale_status": "售后状态",
    "payable_amount":    "订单应付金额",
    "influencer_id":     "达人ID",
    "influencer_name":   "达人昵称",       # 直播间/渠道维度
    "category":          "品类",
    "channel_type":      "渠道",
    "platform_discount": "平台优惠",       # 平台补贴券原文 "券名-金额;券名-金额"
    "join_time":         "入会时间",       # 会员表：入会时间
    "crowd_name":        "人群名称",       # 人群表：人群/包名
}

# ── 老客判定规则（★多业务组只改这一块，改完重跑 python preprocess.py 即可）────────
# 「老客」定义：历史有过 >= OLD_CUSTOMER_MIN_AMOUNT 元、订单状态属于 TRANSACTION_SUCCESS_STATUSES、
# 且时间早于当前订单至少 OLD_CUSTOMER_MIN_DAYS 天、并落在最近 OLD_CUSTOMER_R12_DEFAULT 天回溯窗口内的成交。
# 例：海蓝之谜 = 550 / 已完成+已发货+待发货 / 不限时间(9999)
#     雅诗兰黛 = 200 / 仅已完成 / 365 天   → 改成：
#       OLD_CUSTOMER_MIN_AMOUNT = 200
#       TRANSACTION_SUCCESS_STATUSES = ["已完成"]
#       OLD_CUSTOMER_R12_DEFAULT = 365
OLD_CUSTOMER_MIN_AMOUNT    = 550        # 元（有效成交最低金额）
OLD_CUSTOMER_MIN_DAYS      = 1          # 天（历史订单需早于当前订单至少 N 天）
OLD_CUSTOMER_R12_DEFAULT   = 9999       # 天（R12 回溯窗口；9999≈27年≈全时段）
# 判定为"有效历史成交"的订单状态集合（排除"已关闭"）。只认已完成则填 ["已完成"]。
TRANSACTION_SUCCESS_STATUSES = ["已完成", "已发货", "待发货"]
TRANSACTION_SUCCESS_STATUS = "已完成"   # 兼容旧引用（单值，保留）

# ── 金额字段选择 ──────────────────────────────────────────────────────────────
# "payable_amount"（订单应付金额，实付）或 "item_amount"（商品金额，原价）
AMOUNT_FIELD = "payable_amount"

# ── 数据目录 ──────────────────────────────────────────────────────────────────
RAW_DATA_DIR       = "data/raw"
PROCESSED_DATA_DIR = "data/processed"

# ── 多表识别（data/raw 下可同时存在多张表，按文件名关键词区分）──────────────────
ORDERS_FILE_KEYWORD = "报表订单"   # 文件名含此关键词 → 正装订单表
SAMPLE_FILE_KEYWORD = "派样"       # 文件名含此关键词 → 派样（低价试用装）表
MEMBER_FILE_KEYWORD = "会员"       # 文件名含此关键词 → 会员表（淘宝ID + 入会时间）
CROWD_FILE_KEYWORD  = "人群"       # 文件名含此关键词 → 上传人群表（人群名称 + 淘宝ID）

# ── Sankey / 流转图 ────────────────────────────────────────────────────────────
SANKEY_MIN_COUNT = 3   # 低于此次数的路径不显示
MAX_PURCHASE_RANK = 6  # 最多展示第几次购买
