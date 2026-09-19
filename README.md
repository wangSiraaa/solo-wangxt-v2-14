# 套装拆退金额核算系统

零售品牌套装订单的**部分退货金额核算**：客服选择已购套装中的退件，财务在 React 工作台
查看优惠 / 运费 / 实付款分摊，Django REST Framework 完成核算，PostgreSQL 持久化
订单快照与退款占用。

## 快速开始

```bash
bash /workspace/start.sh        # 启动 PostgreSQL + Django(:8000) + Vite(:5173)
```

打开 http://127.0.0.1:5173/ 即可操作。后端测试：`cd backend && python3 manage.py test`

## 核心规则

**下单快照分摊（`refunds/services.py: allocate_discount`）**
- 每行以标价为权重分摊订单总优惠；赠品权重为 0，分摊 0。
- 分币尾差用「最大余数法 + 稳定排序」：余数大的行先补 1 分，余数相同按行号升序，
  完全确定、可复现。分摊结果落库（`allocated_discount` / `allocated_paid`），之后不再重算。
- 不变量：`Σ分摊优惠 = 订单总优惠`，`Σ实付分摊 = 商品实付`。

**退款核算（`create_refund`）**
- 非末次：退款 = Σ 所选行实付分摊（运费不退）。
- 末次（退完全部商品行）：按 `实付总额 − 已退 − 占用` 结清全部可退尾差，
  并退还运费；赠品未随末次退回的，按 `gift_value` 扣款。
- 硬上限：`已成功 + 占用中 + 本次 ≤ 实付`，超限直接 400 拦截。
- 创建即占用（行状态 `LOCKED`），失败才释放；**网关返回未知结果时进入
  `PENDING_VERIFICATION`，占用不释放，同一行不能再次退款**。

**幂等与留痕**
- 提交幂等：`idempotency_key` 唯一，重试返回原单（`idempotent_replay: true`）。
- 回调幂等：`(refund, callback_id)` 唯一约束，重复回调仅留痕不改状态。
- 每笔退款的完整计算依据存于 `Refund.calc_trace`，事件流（提交/回调/核实）
  存于 `RefundEvent`，前端逐笔可展开追溯。

## 演示数据（`python3 manage.py seed_demo`）

| 订单 | 场景 |
|---|---|
| ORD-A-20260918 | 3 件商品 + 赠品，全新订单，可实际提交退款 |
| ORD-B-20260910 | 已发生两次跨日部分退款，剩最后一件 + 赠品 → 末次结清 |
| ORD-C-20260915 | 一笔退款因网关未知结果「待核实」，含重复回调留痕 |

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/orders/` `/api/orders/{id}/` | 订单列表 / 分摊明细 |
| POST | `/api/orders/{id}/refunds/` | 提交退款 `{line_ids, reason, idempotency_key, simulate}` |
| POST | `/api/refunds/{id}/callback/` | 模拟网关回调 `{callback_id, result}`（幂等） |
| POST | `/api/refunds/{id}/recheck/` | 主动核实 `{force}` |

`simulate` / `force` 取值 `random|success|failure|unknown`，用于演示支付模拟器的
成功、失败、未知三种结果。

## 目录结构

```
backend/
  config/            Django 配置（PostgreSQL: refunddb）
  refunds/
    models.py        订单快照 / 退款 / 退款行 / 事件流
    services.py      分摊算法 + 退款核算 + 支付模拟器（核心）
    views.py         DRF API
    management/commands/seed_demo.py
    tests/test_services.py   13 个单元测试
frontend/src/App.jsx  React 财务工作台
```
