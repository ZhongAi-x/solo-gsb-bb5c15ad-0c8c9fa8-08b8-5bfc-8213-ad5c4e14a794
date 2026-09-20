"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from .allocation import allocate
from .errors import UnprocessableError, request_validation_handler, unprocessable_handler
from .formula import WHITELIST_DISPLAY
from .joint import propagate_joint
from .propagation import propagate
from .schemas import (
    AllocationRequest,
    AllocationResponse,
    JointPropagationRequest,
    JointPropagationResponse,
    PropagationRequest,
    PropagationResponse,
)

app = FastAPI(
    title="校准计算不确定度传播 API",
    description=(
        "提交带数值、单位、标准不确定度的输入量、相关系数矩阵和白名单测量公式，"
        "服务端进行量纲校验，并用符号偏导与协方差矩阵计算合成标准不确定度（GUM 一阶）。"
    ),
    version="1.0.0",
)

app.add_exception_handler(UnprocessableError, unprocessable_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/whitelist", tags=["uncertainty"])
def whitelist() -> dict:
    """返回公式允许使用的运算与函数。"""
    return {"whitelist": WHITELIST_DISPLAY}


_EXAMPLE = {
    "inputs": [
        {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
        {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.02},
    ],
    "formula": "U*I",
    "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
}

_ALLOC_EXAMPLE = {
    "inputs": [
        {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
        # I 的不确定度是待求未知量，可省略其 standard_uncertainty。
        {"name": "I", "value": 2.0, "unit": "A"},
    ],
    "formula": "U*I",
    "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
    "allocate_input": "I",
    "target_combined_uncertainty": {"value": 0.5, "unit": "W"},
}

_JOINT_EXAMPLE = {
    "inputs": [
        {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
        {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.05},
    ],
    "outputs": [
        {"name": "P", "formula": "U*I"},
        {"name": "R", "formula": "U/I"},
    ],
    "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
}


@app.post(
    "/api/v1/propagate",
    response_model=PropagationResponse,
    tags=["uncertainty"],
    responses={422: {"description": "相关矩阵非法或单位/量纲不兼容等"}},
    openapi_extra={
        "examples": {"欧姆定律 P=U*I": {"value": _EXAMPLE}},
    },
)
def calculate(req: PropagationRequest) -> dict:
    """执行一次不确定度传播计算。"""
    return propagate(req)


@app.post(
    "/api/v1/propagate-joint",
    response_model=JointPropagationResponse,
    tags=["uncertainty"],
    responses={
        422: {
            "description": (
                "输出量名称重复、输出公式非法、相关矩阵非法或单位/量纲不兼容等"
            )
        }
    },
    openapi_extra={
        "examples": {"欧姆定律 P=U*I 与 R=U/I 联合传播": {"value": _JOINT_EXAMPLE}},
    },
)
def calculate_joint(req: JointPropagationRequest) -> dict:
    """多输出联合传播：对同一输入协方差按一阶雅可比计算 JΣJᵀ，给出输出协方差与相关系数矩阵。"""
    return propagate_joint(req)


@app.post(
    "/api/v1/allocate-uncertainty",
    response_model=AllocationResponse,
    tags=["uncertainty"],
    responses={
        422: {
            "description": (
                "目标不可达 target_unreachable / 灵敏度为零 zero_sensitivity / "
                "无有限上界 no_finite_bound，或相关矩阵非法、单位量纲不兼容等"
            )
        }
    },
    openapi_extra={
        "examples": {"P=U*I：反求 I 的最大允许不确定度": {"value": _ALLOC_EXAMPLE}},
    },
)
def allocate_uncertainty(req: AllocationRequest) -> dict:
    """把指定输入量的标准不确定度视为未知量，反求满足目标合成标准不确定度的最大非负值。"""
    return allocate(req)
