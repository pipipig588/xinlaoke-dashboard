# 直播间销售分析仪表盘 — 项目状态文档

> 读完此文件即可接手继续开发，无需重新了解背景。

---

## 一、线上地址 & 仓库

| 项目 | 地址 |
|------|------|
| Streamlit 本地地址 | http://localhost:8501 |
| Tailscale 远程地址 | http://100.106.228.108:8501 |
| 公司内网地址 | http://172.16.13.113:8501（Mac 接公司网络后生效） |
| 局域网地址（同办公室热点） | http://172.20.10.4:8501 |
| GitHub 仓库 | https://github.com/pipipig588/xinlaoke-dashboard（私有，仅存代码） |
| 本地路径（Mac） | `/Users/anirv/Downloads/xinlaoke/` |
| 备份路径 | `/Users/anirv/Downloads/xinlaoke_backup_20260520_1856/` |

> ⚠️ 数据安全第一：数据文件（data/）只在本地 Mac mini，不上传 GitHub。外部访问只走 Tailscale 加密隧道。

---

## 二、文件结构

```
xinlaoke/
├── app.py                  # Streamlit 主应用（11个Tab）
├── config.py               # 字段名 & 基础参数配置（含多表关键词）
├── preprocess.py           # Excel → Parquet 预处理脚本（8步，多表）
├── requirements.txt        # Python 依赖
├── 启动仪表盘.command       # 双击一键启动（自动显示 Tailscale URL）
├── 启动仪表盘.bat          # Windows 双击启动（找不到Python时引导装同目录安装包）
├── PROJECT_STATUS.md       # 本文件
├── data/
│   ├── raw/                # 放 Excel 源表（gitignore data/raw/）
│   │   ├── 报表订单.xlsx    # 必需，3 sheet
│   │   ├── 派样.xlsx        # 可选 → Sample回购
│   │   ├── 会员.xlsx        # 可选 → 会员（淘宝ID+入会时间）
│   │   └── 人群.xlsx        # 可选 → 人群（人群名称+淘宝ID）
│   └── processed/          # 预处理生成文件
│       ├── orders.parquet
│       ├── purchase_pairs.parquet
│       ├── samples.parquet  # 派样表（可选）
│       ├── members.parquet  # 会员表（可选）
│       ├── crowds.parquet   # 人群表（可选）
│       └── meta.json
└── .streamlit/
    └── config.toml         # Streamlit 主题配置
```

---

## 三、数据情况

- **最新源表**：`data/raw/报表订单.xlsx`（多 Sheet：`23-24`、`25`、`26`，预处理自动合并）
- **数据规模**：约 213万行，140.5万买家，2023-03-30 ~ 2026-06-14（2026-06-15 重新预处理）
- **去重口径（2026-06-15 修复）**：按「子订单编号」去重（原表每行即唯一子订单）。旧逻辑按 user+时间+货号+达人 去重会误并多个子订单，导致订单数/GMV 低估约6%，已修复
- **预处理后字段**：
  - `user_id`（淘宝ID）、`sub_order_id`（子订单编号，去重粒度）、`sku`（货号）、`influencer_name`（达人昵称，空值→"货架"）
  - `order_status`（订单状态）、`gmv`（订单应付金额）、`pay_time`（支付时间）
  - `category`（品类）、`channel_type`（渠道类型：自营/达人）
  - `platform_discount`（平台优惠原文，格式：`券名-金额;券名-金额`，空值/"-"已统一为空串）
  - `customer_type`（全时段新老客标签）、`customer_type_r12`（滚动窗口新老客标签）
  - `purchase_rank`（该用户第几次购买）

> 平台优惠覆盖率：2024-08 起逐步上升，2025-2026 多数月份 50%-85%。
> 早期月份（2023-2024H1）几乎无平台补贴券，属正常历史数据。

---

## 四、新老客判定逻辑（重要）

### ⚡ 统一一套标签（重要架构决定）

**所有 Tab 统一使用 `customer_type_r12`**，在 `main()` 入口处直接覆盖：
```python
orders["customer_type"] = orders["customer_type_r12"]
pairs["customer_type"]  = pairs["customer_type_r12"]
```
- preprocess 预计算时仍保留两列（`customer_type` 全时段 + `customer_type_r12` 滚动窗口）
- 运行时 app 统一以 `customer_type_r12` 为准，避免侧边栏改参数后各 Tab 定义不一致的混乱

### 有效成交条件（三项同时满足）
```
✅ 订单状态 = 已完成
✅ 订单金额 ≥ X 元（默认 550）
✅ 渠道类型 ∈ 选定渠道（默认不限，即自营+达人都算）
```

### 滚动窗口老客判定（全局生效）
```
当前订单往前 N 天内（默认9999天≈全时段）有有效成交 → 老客
否则（从未买过 or 超N天未买）→ 新客
```

> **N 天默认 9999（等同全时段）**，侧边栏可改为 365（R12）、730（R24）等。

### 侧边栏可自定义参数（⚙️ 老客判定规则）
| 参数 | 默认 | 说明 |
|------|------|------|
| 有效成交渠道 | 全渠道 | 可限定只有"自营"才算有效成交 |
| 最低金额 | ¥550 | 低于此不算有效成交 |
| 最少间隔天数 | 1天 | 有效成交需早于当前订单至少N天 |
| R12回溯窗口 | 9999天 | 滚动窗口天数（9999≈全时段） |

> 参数改变时 app 自动重算（首次约30-60秒），相同参数有缓存不重算。修改后**所有 Tab 同步生效**。

---

## 五、11个功能 Tab（顺序即 app.py st.tabs 顺序）

| Tab | 功能 |
|-----|------|
| 🏷 渠道汇总 | 各渠道/达人 订单数·GMV·人数·客单价·购买频次·新老客拆分 汇总表 + 订单数/GMV 占比饼图（支持 YOY） |
| 📈 新老客趋势 | 每日/月堆积柱状图，可切换订单数/GMV/占比，可按渠道分组；支持 YOY 同比，6行汇总表含涨跌 |
| 📦 货品分析 | 各货号新老客占比 + 销量/GMV排名 + 全店占比%，支持货号关键词过滤，可下载 CSV |
| 📐 RFM | R（最近）/F（频次）/M（金额）分层与分布 |
| 🔄 渠道流转 | 购买顺序 Sankey 流转图，节点显示出流量，hover 显示转化量和转化率 |
| 🔁 复购率 | 月度复购率趋势（环比）+ 复购去向分布，老客定义以侧边栏为准 |
| ⏱ 复购周期 | 渠道→渠道 / 货号→货号 平均间隔天数热力图 |
| 🧪 Sample回购 | 派样（试用装）转正装回购。回购=买样用户正装时间≥窗口内最早买样时间；回购率=回购人数÷买样人数。3个sample专属筛选（购买时间/状态/货号）+ 活动期整体KPI + 按sample月份cohort + 回购货品。源表 派样.xlsx |
| 🪪 会员 | 新/老会员（按入会时间区间，区间内=新）购买对比 + 入会月份趋势 + 会员vs非会员对比。源表 会员.xlsx（淘宝ID+入会时间） |
| 👥 人群 | 上传人群（人群id=淘宝ID）匹配订单：多选人群→各人群购买人数·转化率·订单·GMV·客单价 + 购买货品。源表 人群.xlsx（人群名称+淘宝ID） |
| 💰 平台优惠 | 真实收入核算：从「平台优惠」字段提取所有券名，手动勾选平台补贴类券，「真实收入 = 客户实付 + 平台补贴」；含月度趋势图、各券补贴占比饼图、订单明细表、CSV 导出 |

> Sample回购 / 会员 / 人群 三个 Tab 的"购买"口径均 = 左侧全局筛选；源表缺失时 Tab 显示"尚未生成数据"提示，不报错。

---

## 六、侧边栏筛选顺序

1. 时间范围 + YOY 同比开关
2. 渠道类型（自营/达人）
3. 达人（联动渠道类型）
4. 品类（联动货号）
5. 货号（联动品类）
6. 订单状态
7. 成交金额区间
8. ⚙️ 老客判定规则

---

## 七、启动方式

```bash
# 方式1：双击 Finder 里的「启动仪表盘.command」

# 方式2：手动启动
cd /Users/anirv/Downloads/xinlaoke
STREAMLIT_SERVER_HEADLESS=true /Users/anirv/Library/Python/3.9/bin/streamlit run app.py \
  --server.address 0.0.0.0 --server.port 8501 \
  --server.enableCORS false --server.enableXsrfProtection false
```

> Mac mini 需联网才能让远程同事通过 Tailscale 访问。同办公室可用局域网 IP（不需要联网）。

---

## 八、更新数据流程（每次换源表执行）

```bash
# 1. 把新 Excel 放入 data/raw/（先关掉 Excel 避免临时文件干扰）
# 2. 预处理（约 60-90 秒）
cd /Users/anirv/Downloads/xinlaoke
python3 preprocess.py

# 3. 推送代码到 GitHub（只推代码，不推数据）
git add app.py config.py preprocess.py
git commit -m "更新代码 YYYY-MM-DD"
git push
```

> data/ 目录已在 .gitignore 中完整屏蔽，不会误传数据。

---

## 九、同事使用 Tailscale 接入方式

1. 同事安装 Tailscale：https://tailscale.com/download
2. 用 GitHub 账号登录
3. 你在管理台邀请：https://login.tailscale.com/admin/users → Invite users
4. 同事连上后访问：http://100.106.228.108:8501

> 浏览器显示「不安全」是正常的，Tailscale 本身已端对端加密，比 https 更安全。

---

## 十、同事访问方式

| 场景 | 方式 |
|------|------|
| 公司内网（Mac 接公司网络后） | 直接访问 http://172.16.13.113:8501，无需任何账号 |
| 远程（Tailscale） | 管理台邀请 https://login.tailscale.com/admin/users → 同事登录后访问 http://100.106.228.108:8501 |
| 局域网（同办公室热点） | http://172.20.10.4:8501 |

> 公司内网访问检查清单：
> 1. Mac 接入公司网络后，运行 `ipconfig getifaddr en0`（有线）或 `ipconfig getifaddr en1`（WiFi）确认 IP 为 172.16.13.113
> 2. 双击「启动仪表盘.command」，启动脚本会自动显示当前公司内网链接
> 3. 同事浏览器访问 http://172.16.13.113:8501 即可
> 4. macOS 防火墙首次启动可能弹窗，点「允许」放行

---

## 十一、待开发需求

### 需求1：中国同事内网部署
- 同事公司有可用电脑，拿到内网 IP 后告知 Dan，一键部署脚本待写

### 需求2：同事补充功能需求
- 待收集

---

## 十一、下次对话开始方式

直接说：**"继续仪表盘开发，读取 PROJECT_STATUS.md"**，然后描述要做的功能即可。
