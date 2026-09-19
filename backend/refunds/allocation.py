"""分币级分摊引擎。

所有金额一律使用「分」(整数) 计算，杜绝浮点误差。
"""
from fractions import Fraction


def allocate_cents(total_cents, weights):
    """把 total_cents 按 weights 比例分摊成整数分，返回与 weights 等长的列表。

    尾差分配（稳定排序）：
      1. 每行先取精确值的向下取整；
      2. 剩余的分币按「小数余数大者优先，余数相同行号小者优先」逐行 +1 分。
    保证：sum(结果) == total_cents，且同一输入永远得到同一输出。
    """
    n = len(weights)
    if n == 0:
        return []
    if total_cents == 0:
        return [0] * n

    total_weight = sum(weights)
    if total_weight == 0:
        # 全部行权重为 0（如整单赠品）：按行均摊，尾差从第一行起依次 +1
        base, rem = divmod(total_cents, n)
        return [base + (1 if i < rem else 0) for i in range(n)]

    exact = [Fraction(total_cents * w, total_weight) for w in weights]
    floors = [e.numerator // e.denominator for e in exact]
    remainder = total_cents - sum(floors)

    # 稳定排序：余数大的优先；余数相同索引（行号）小的优先
    order = sorted(range(n), key=lambda i: (-(exact[i] - floors[i]), i))
    result = list(floors)
    for i in order[:remainder]:
        result[i] += 1
    return result


def unit_refund_cents(line_paid_cents, line_qty):
    """单行单件可退金额（向下取整）。最后一件通过结清补齐尾差。"""
    if line_qty <= 0:
        return 0
    return line_paid_cents // line_qty
