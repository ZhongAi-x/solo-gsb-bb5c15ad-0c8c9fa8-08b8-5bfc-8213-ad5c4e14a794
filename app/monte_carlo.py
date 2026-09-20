"""非线性测量模型的蒙特卡洛传播校核（GUM Supplement 1 思路）。

对测量模型 y = f(x_1, ..., x_n)，把每个输入量视为以估计值为中心、以其标准
不确定度（按声明单位解释）为尺度的正态随机变量，用输入相关系数矩阵 R 生成
**相关**正态样本，逐样本代入白名单公式求值，最后统计输出样本的均值、标准
偏差与分位数，并与 ``propagate`` 给出的 GUM 一阶结果对比偏差。

相关正态样本由 R 的特征分解生成：

    X = μ + Z · (V · diag(sqrt(λ)))ᵀ，  Z ~ N(0, I)

特征值被钳制到非负，因此**半正定但奇异**的相关矩阵（如完全相关 r=1）也可
运行：零特征值方向不产生随机成分。输入量单位与不确定度单位先统一换算到
SI 基本单位再采样，输出样本再统一换算回公式的显示单位。

输入量解析、公式白名单/量纲/单位校验、相关矩阵校验与一阶传播全部复用
``propagation`` 模块。
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import sympy as sp

from .correlation import validate_correlation_matrix
from .errors import UnprocessableError
from .formula import Log10, Log2
from .propagation import WHITELIST, _prepare_inputs, evaluate_model, variance_from_cu
from .units import Quantity, unit_str

# 白名单函数名 -> NumPy 向量化实现（角度的 rad/deg 换算已在输入单位处完成）。
_NUMPY_FUNCS: dict[str, object] = {
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "log2": np.log2,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "asin": np.arcsin,
    "acos": np.arccos,
    "atan": np.arctan,
    "abs": np.abs,
}


def _base_unit(unit) -> object:
    """返回该单位对应的 SI 基本单位（如 V -> kg·m²/(A·s³)，rad -> dimensionless）。"""
    return Quantity(1.0, unit).to_base_units().units


def _linear_factor(unit) -> tuple[float, object]:
    """返回单位 -> 基本单位的线性换算系数与基本单位（差值意义，正确处理偏移单位）。

    对偏移单位（如 degC），1 个单位增量对应的基本单位增量与绝对换算系数不同，
    因此用 Q(1,u)-Q(0,u) 的差值取系数；不确定度是增量，必须使用线性系数。
    """
    factor = float(Quantity(1.0, unit).to_base_units().magnitude) - float(
        Quantity(0.0, unit).to_base_units().magnitude
    )
    return factor, _base_unit(unit)


def _correlated_normal(
    means: np.ndarray,
    scales: np.ndarray,
    corr: np.ndarray,
    samples: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """生成相关正态样本矩阵 (samples, n)。

    令协方差 Σ = D R D，取 R = V·diag(λ)·Vᵀ，则 Σ 的平方根因子为
    D·V·diag(sqrt(λ))；零（钳制后的微小负）特征值方向不产生随机成分，
    故半正定奇异矩阵同样可采样。
    """
    eigenvalues, eigvecs = np.linalg.eigh(corr)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    # Σ = D R D 的平方根因子 A = D·V·diag(sqrt(λ))：按列缩放特征向量后，
    # 再按行乘以各输入量的不确定度。零（钳制后的微小负）特征值方向不产生
    # 随机成分，故半正定奇异矩阵同样可采样。样本行 x = μ + z·Aᵀ。
    factor = scales[:, None] * (eigvecs * (eigenvalues ** 0.5))
    z = rng.standard_normal((samples, len(means)))
    return means[None, :] + z @ factor.T


def _vectorized_model(expr: sp.Expr, input_symbols: list[sp.Symbol]):
    """把白名单 SymPy 表达式编译为 NumPy 向量化函数（按列接收输入样本）。

    自定义的 Log10/Log2 不在 lambdify 的已知函数表中，先改写为自然对数
    （log10(x)=log(x)/log(10)）后再编译。
    """
    expr = expr.replace(Log10, lambda arg: sp.log(arg) / sp.log(10))
    expr = expr.replace(Log2, lambda arg: sp.log(arg) / sp.log(2))

    namespace: dict[str, object] = {s.name: None for s in input_symbols}
    namespace.update(_NUMPY_FUNCS)
    namespace["pi"] = np.pi
    namespace["E"] = np.e
    return sp.lambdify(input_symbols, expr, modules=[namespace, {}])


def _to_display_unit(
    values: np.ndarray, display_unit, base_unit
) -> np.ndarray:
    """把 SI 基本单位下的输出样本数组换算回显示单位。

    常规单位用线性系数；偏移单位（degC/degF）需要减去绝对零点偏移。
    """
    factor = float(Quantity(1.0, display_unit).to(base_unit).magnitude) - float(
        Quantity(0.0, display_unit).to(base_unit).magnitude
    )
    if factor == 0.0:  # 防御：白名单公式产生不出零系数单位
        raise UnprocessableError(
            f"输出单位 {unit_str(display_unit)} 的换算系数为零，无法换算样本",
            ["formula"],
            code="evaluation_error",
        )
    offset = float(Quantity(0.0, display_unit).to(base_unit).magnitude)
    return (values - offset) / factor


def _relative_deviation(mc: float, first_order: float) -> float | None:
    """相对偏差 (MC - 一阶) / |一阶|；一阶值为 0 时无定义，返回 None。"""
    if first_order == 0.0:
        return None
    return (mc - first_order) / abs(first_order)


def propagate_monte_carlo(req) -> dict:
    """执行蒙特卡洛传播，返回可直接序列化的结果字典。"""
    inputs = _prepare_inputs(req)
    n = len(inputs)

    corr = validate_correlation_matrix(req.correlation_matrix, n)
    model = evaluate_model(inputs, req.formula)
    result_q = model["result_q"]
    display_unit = model["display_unit"]

    # 把每个输入量的均值与标准不确定度换算到 SI 基本单位，供向量化采样。
    means = np.empty(n, dtype=np.float64)
    scales = np.empty(n, dtype=np.float64)
    for i, p in enumerate(inputs):
        unit = p["unit"]
        factor, _ = _linear_factor(unit)
        means[i] = float(Quantity(p["magnitude"], unit).to_base_units().magnitude)
        scales[i] = p["u_magnitude"] * factor

    # GUM 一阶参考结果（复用传播主流程，保证与 /propagate 完全一致）。
    cu = [model["c_mags"][i] * inputs[i]["u_magnitude"] for i in range(n)]
    total_variance, _ = variance_from_cu(cu, corr)
    first_order_value = float(result_q.magnitude)
    first_order_uc = math.sqrt(total_variance)

    # 复用 evaluate_model 已解析并通过全部白名单/量纲校验的表达式，
    # 编译为 NumPy 向量化函数（按列接收各输入量的样本）。
    fn = _vectorized_model(model["expr"], [p["symbol"] for p in inputs])
    base_unit = _base_unit(display_unit)

    # 校核：标称输入经同一条向量化路径求值，应与带单位的模型值一致。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nominal_base = fn(*means)
    nominal_display = _to_display_unit(
        np.asarray(nominal_base, dtype=np.float64), display_unit, base_unit
    )
    if not math.isfinite(float(nominal_display)) or abs(
        float(nominal_display) - first_order_value
    ) > 1e-9 * max(1.0, abs(first_order_value)):
        raise UnprocessableError(
            "标称输入的向量化求值与带单位模型值不一致，请检查单位与公式",
            ["formula"],
            code="evaluation_error",
        )

    rng = np.random.default_rng(req.seed)
    sample_matrix = _correlated_normal(means, scales, corr, req.samples, rng)

    columns = [sample_matrix[:, i] for i in range(n)]
    with warnings.catch_warnings():
        # 无效域（log 负数、除零、asin 越界等）在 NumPy 下产生 NaN/Inf 与
        # RuntimeWarning；抑制告警，统一按非有限结果判为无效样本。
        warnings.simplefilter("ignore")
        raw = fn(*columns)
    outputs_base = np.asarray(raw, dtype=np.float64)
    if outputs_base.shape != (req.samples,):
        # 与输入无关的常数公式会被广播成标量/常数数组，规整为逐样本数组。
        outputs_base = np.broadcast_to(outputs_base, (req.samples,)).astype(np.float64)

    invalid = ~np.isfinite(outputs_base)
    invalid_count = int(np.count_nonzero(invalid))
    if invalid_count:
        first_index = int(np.flatnonzero(invalid)[0])
        raise UnprocessableError(
            f"{invalid_count} 个样本落入公式无效域（如对数取非正数、除零、asin/acos "
            "参数越界等），无法完成蒙特卡洛传播",
            ["formula"],
            code="invalid_sample_domain",
            invalid_sample_count=invalid_count,
            first_invalid_sample_index=first_index,
        )

    outputs = _to_display_unit(outputs_base, display_unit, base_unit)

    # 与输入无关（或有效不确定度为零）时，全部样本应落在标称值上；浮点求和
    # 本身会引入 ~1e-15 的假抖动，此时直接按零散布处理统计量。
    zero_spread = bool(
        np.all(np.abs(outputs - first_order_value) <= 1e-10 * max(1.0, abs(first_order_value)))
    )
    if zero_spread:
        mean = first_order_value
        standard_uncertainty = 0.0
        q025 = q500 = q975 = first_order_value
    else:
        mean = float(np.mean(outputs))
        # 标准不确定度 = 输出样本的实验标准偏差（贝塞尔，ddof=1）。
        standard_uncertainty = (
            float(np.std(outputs, ddof=1)) if req.samples > 1 else 0.0
        )
        q025, q500, q975 = (
            float(v) for v in np.quantile(outputs, [0.025, 0.5, 0.975], method="linear")
        )

    return {
        "result": {
            "value": mean,
            "unit": unit_str(result_q.units),
            "standard_uncertainty": standard_uncertainty,
        },
        "quantiles": {
            "0.025": q025,
            "0.5": q500,
            "0.975": q975,
        },
        "first_order": {
            "value": first_order_value,
            "unit": unit_str(result_q.units),
            "standard_uncertainty": first_order_uc,
        },
        "relative_deviation": {
            "mean": _relative_deviation(mean, first_order_value),
            "standard_uncertainty": _relative_deviation(
                standard_uncertainty, first_order_uc
            ),
        },
        "samples": req.samples,
        "seed": req.seed,
        "number_of_inputs": n,
        "correlation_matrix": [[float(x) for x in row] for row in corr],
        "formula": req.formula,
        "whitelist": list(WHITELIST),
    }
