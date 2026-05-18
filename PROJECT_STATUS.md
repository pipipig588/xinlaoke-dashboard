# 直播间销售分析仪表盘 — 项目状态文档

> 读完此文件即可接手继续开发，无需重新了解背景。

---

## 一、线上地址 & 仓库

| 项目 | 地址 |
|------|------|
| Streamlit 线上地址 | https://xinlaoke-dashboard-gmfi33an9wuvoezw6vnbaw.streamlit.app |
| GitHub 仓库 | https://github.com/pipipig588/xinlaoke-dashboard（私有） |
| 本地路径（Mac） | `/Users/anirv/Downloads/xinlaoke/` |
| GitHub 账号 | pipipig588 |

---

## 二、文件结构

```
xinlaoke/
├── app.py                  # Streamlit 主应用（5个Tab）
├── config.py               # 字段名 & 参数配置（改这里适配新数据）
├── preprocess.py           # Excel → Parquet 预处理脚本
├── requirements.txt        # Python 依赖
├── PROJECT_STATUS.md       # 本文件
├── data/
│   ├── raw/                # 放 Excel 源表（不上传 GitHub）
│   └── processed/          # 预处理生成（orders.parquet 等）
└── .streamlit/
    └── config.toml         # Streamlit 主题配置
```

---

## 三、数据情况

- **最新源表**：`data/raw/工作簿3.xlsx`（全量历史订单）
- **数据规模**：88万行，66万买家，2025-03 ~ 2026-05
- **预处理后保留字段（6列，已去敏）**：
  - `user_id`（加密后的买家ID）
  - `sku`（货号）
  - `influencer_name`（达人昵称/渠道，空值标记为"货架"）
  - `order_status`（订单状态）
  - `payable_amount` → `gmv`（订单应付金额）
  - `pay_time`（支付时间）

---

## 四、老客判定规则

```
老客 = 该用户在当前订单之前，有过至少1笔：
  - 订单状态 = "已完成"
  - 订单应付金额 >= 550 元
  - 支付时间早于当前订单至少 1 天
的历史订单
```

**特殊规则**：选定时间段内，若某用户既有新客订单又有老客订单 → 按**新客**计（config.py 可调整阈值）。

---

##五、5个功能 Tab

| Tab | 功能 |
|-----|------|
| 📈 新老客趋势 | 每日/月堆积柱状图，可切换订单数/GMV/占比，可按渠道分组 |
| 📦 货品分析 | 各货号新老客占比 + 销量排名，支持货号关键词（如"tl"）过滤 |
| ⏱ 复购周期 | 渠道→渠道 / 货号→货号 平均间隔天数热力图 |
| 🔁 复购率 | 选首次购买渠道/货号，看后续复购去向分布 |
| 🔄 渠道流转 | 购买顺序 Sankey 流转图（第1次→第2次→第3次直播间） |

---

## 六、更新数据流程（每次换源表执行）

```bash
# 1. 把新 Excel 放入 data/raw/（先关掉 Excel 再操作，避免临时文件干扰）
# 2. 预处理（约 60 秒）
python3 preprocess.py

# 3. 推送到 GitHub（Streamlit Cloud 自动刷新，约 30 秒）
git add data/processed/
git commit -m "更新数据 YYYY-MM"
git push
```

---

## 七、⚠️ 待解决问题

### 问题1：GitHub 文件超过 50MB 限制
- **现象**：`git push` 报警告 `File is 57.67 MB; this is larger than GitHub's recommended maximum file size of 50.00 MB`，push 被拒绝
- **原因**：orders.parquet 数据量增大后超出 GitHub 限制
- **解决方案（二选一）**：
  - 方案A：启用 Git LFS（`git lfs install && git lfs track "*.parquet"`）
  - 方案B：在 preprocess.py 中对 orders.parquet 做压缩（`compression='gzip'`）或只存必要聚合数据
- **临时绕过**：数据目前在本地，Streamlit Cloud 尚未拿到最新数据，需先解决此问题再推

### 问题2：渠道筛选空白项显示
- **现象**：侧边栏渠道筛选下拉里可能有空白选项
- **状态**：preprocess.py 已将空值标记为"货架"，meta.json 已更新，待确认线上是否还有残留

---

## 八、📋 待开发需求

### 需求1：对比日期功能（已提出，待开发）
- 用户可以选择两个时间段（如 2025-Q4 vs 2026-Q1），并排对比新老客/GMV/复购率
- 建议实现：侧边栏增加"对比模式"开关，开启后出现第二个日期选择器，图表双色展示

### 需求2：同事补充需求（待收集）
- 下次对话补充

---

## 九、本地运行命令

```bash
# 安装依赖（只需一次）
pip3 install streamlit pandas plotly openpyxl pyarrow

# 启动（本地预览）
cd /Users/anirv/Downloads/xinlaoke
/Users/anirv/Library/Python/3.9/bin/streamlit run app.py

# 启动（局域网可访问）
/Users/anirv/Library/Python/3.9/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

---

## 十、关键配置（config.py）

```python
OLD_CUSTOMER_MIN_AMOUNT    = 550        # 老客门槛金额（元）
OLD_CUSTOMER_MIN_DAYS      = 1          # 历史订单需早于当前订单几天
TRANSACTION_SUCCESS_STATUS = "已完成"   # 有效历史成交的订单状态
AMOUNT_FIELD               = "payable_amount"  # 使用订单应付金额
```

---

## 十一、下次对话开始方式

直接说：**"继续仪表盘开发，读取 PROJECT_STATUS.md"**，然后描述要做的功能即可。
