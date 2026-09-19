# 套装拆退金额核算系统

零售品牌售出组合套装后，顾客可只退其中部分商品。本系统负责**拆退金额核算**：
客服在工作台选择已购套装的退件，财务查看优惠/运费/实付的分摊明细，
后端按下单时权重分摊并处理分币尾差，支付网关结果未知时保持退款待核实。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + Vite（构建产物由 Django 托管，单端口交付） |
| 后端 | Django 5 + Django REST Framework |
| 数据库 | PostgreSQL 17（订单快照 JSON + 退款占用记录 + 支付事件流水） |

## 快速开始

```bash
./start.sh            # 启动 PostgreSQL + 迁移 + Django（http://127.0.0.1:8000）
./start.sh --seed     # 同时重置演示数据
./start.sh --build    # 同时重新构建前端
```

开发模式前端热更新：`cd frontend && npm run dev`（Vite 代理 /api 到 8000）。

运行测试：`cd backend && python3 manage.py test`

## 核心核算规则

### 1. 下单分摊（落库快照）
- 优惠、运费按**行原价权重**分摊到每个订单行（赠品权重为 0，分摊 0 元）；
- 分币尾差**稳定排序**分配：各行先向下取整，剩余分币按「小数余数大者优先、
  余数相同行号小者优先」逐行 +1 分，保证 `Σ行分摊 = 订单总额` 且结果可复现；
- 分摊结果与下单快照（`Order.snapshot`）一起写入 PostgreSQL，永久留档。

### 2. 拆退计算
- 单件可退 = `行实付分摊 ÷ 行数量`（向下取整）；
- **退到该行最后一件时结清**：`行退款 = 行实付分摊 − 该行已退/占用金额`，尾差随尾件结清；
- **整单结清**：本次退完全单所有剩余件数时，`本次总额 = 实付 − 已占用总额`；
- 不变量：**累计退款（成功 + 待核实）≤ 实付**，创建退款时校验，
  事务内 `SELECT ... FOR UPDATE` 锁订单行，并发安全。

### 3. 退款占用与支付状态机
```
创建退款 -> PENDING(待核实，占用额度)
  ├─ 网关 SUCCESS -> SUCCESS（占用转为已退）
  ├─ 网关 FAILED  -> FAILED（释放占用，可再次退款）
  └─ 网关 UNKNOWN -> 保持 PENDING（额度不释放，不能再次退款）
```
- 创建退款需携带 `Idempotency-Key`，重复提交返回原单（HTTP 200），不产生重复退款；
- 支付回调以 `event_id` 幂等：同一事件重复投递 / 不同事件号的重复通知均被忽略；
  已完结后收到相反结果为冲突（HTTP 409），保持原终态；
- 所有回调写入 `PaymentEvent` 流水，逐笔可追溯。

## 演示数据（`python3 manage.py seed_demo`）

| 订单 | 场景 |
|---|---|
| SO20260901001 焕采护肤三件套 | **含赠品**；已退 1 件洁面（成功 ¥95.77）；精华退款被模拟器返回「未知」→ **待核实占用中**，可对其演练回调 |
| SO20260905002 T恤 3 件装 | **跨次数退件**：实付 ¥271.00 不能整除 3，已分两次各退 ¥90.33，最后 1 件结清 ¥90.34（含 1 分尾差） |
| SO20260908003 降噪耳机套装 | **重复回调**：保护套退款成功，事件流水含「重复通知-幂等忽略」与「状态冲突-已拒绝」 |
| SO20260912004 手冲咖啡礼盒 | 全新未退（含赠品），供完整走一遍「选件 → 试算 → 提交 → 支付模拟」 |

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/orders/` | 订单列表（含已退/待核实/可退汇总） |
| GET | `/api/orders/{id}/` | 订单明细（快照 + 逐行分摊 + 可退情况） |
| POST | `/api/orders/{id}/refunds/quote/` | 退款试算（不落库，返回逐行计算依据） |
| POST | `/api/orders/{id}/refunds/` | 创建退款（Header: `Idempotency-Key`） |
| GET | `/api/orders/{id}/ledger/` | 订单台账（全部退款 + 支付事件） |
| GET | `/api/refunds/{id}/` | 退款详情（计算依据 + 事件流水） |
| POST | `/api/refunds/{id}/pay/` | 支付模拟器 `{outcome: success\|failed\|unknown}` |
| POST | `/api/payments/callback/` | 网关回调（幂等；`replay_last=true` 重放同一回调） |

## 目录结构

```
backend/
  refunds/
    allocation.py   # 分币分摊引擎（稳定排序尾差分配）
    services.py     # 退款试算/创建、占用、支付回调（全部事务化）
    models.py       # Order(快照) / OrderItem(分摊) / Refund(占用) / PaymentEvent(流水)
    management/commands/seed_demo.py
    tests.py        # 10 个用例：分摊、尾差结清、占用、幂等、重复回调
frontend/
  src/components/   # 分摊明细表 / 试算面板 / 退款记录（计算依据 + 事件流水）
```
