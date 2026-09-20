"""端到端快速验证（不经过 HTTP，直接调传播函数与 API 客户端）。"""
import json
import sys

sys.path.insert(0, "/workspace")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def show(title, resp):
    print("=" * 70)
    print(title)
    print(json.dumps(resp, ensure_ascii=False, indent=2))


# 1) 欧姆定律 P = U*I，单位校验 + 不相关
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.02},
        ],
        "formula": "U*I",
    },
)
show("1) P=U*I (expect 20 W, uc=sqrt(0.2^2+0.2^2)=0.2828 W)", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert abs(b["result"]["value"] - 20.0) < 1e-12
assert b["result"]["unit"] == "W"
assert abs(b["result"]["standard_uncertainty"] - (0.08 ** 0.5)) < 1e-12

# 2) 完全正相关，相关项使不确定度为 0.4
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.02},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1, 1], [1, 1]],
    },
)
show("2) P=U*I, r=1 (expect uc=0.4 W, share=0.5 each)", r.json())
assert r.status_code == 200
b = r.json()
assert abs(b["result"]["standard_uncertainty"] - 0.4) < 1e-12
assert abs(b["variance_contributions"][0]["share"] - 0.5) < 1e-9

# 3) 负相关抵消
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.02},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1, -1], [-1, 1]],
    },
)
show("3) P=U*I, r=-1 (expect uc=0)", r.json())
assert r.status_code == 200
assert abs(r.json()["result"]["standard_uncertainty"]) < 1e-9

# 4) 量纲不一致：V + A
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.02},
        ],
        "formula": "U + I",
    },
)
show("4) V + A -> 422 dimension_mismatch", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "dimension_mismatch"
assert r.json()["error"]["loc"] == ["formula"]

# 5) 非对称矩阵，定位到 [1,0]
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x+y",
        "correlation_matrix": [[1, 0.5], [0.3, 1]],
    },
)
show("5) asymmetric -> 422 loc [correlation_matrix,1,1?]", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "asymmetric_matrix"
assert r.json()["error"]["loc"][0] == "correlation_matrix"

# 6) 非半正定（三个相关系数互相矛盾）
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "z", "value": 3, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x+y+z",
        "correlation_matrix": [
            [1, 0.9, 0.9],
            [0.9, 1, -0.9],
            [0.9, -0.9, 1],
        ],
    },
)
show("6) non-PSD -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "not_positive_semidefinite"

# 7) 白名单外函数 __import__
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "formula": "__import__('os').system('id')",
    },
)
show("7) injection -> 422", r.json())
assert r.status_code == 422

# 8) 三角函数 + 角度单位，ln/log10
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "theta", "value": 30, "unit": "deg", "standard_uncertainty": 1},
            {"name": "L", "value": 2.0, "unit": "m", "standard_uncertainty": 0.01},
        ],
        "formula": "L * cos(theta)",
    },
)
show("8) L*cos(30 deg) (expect ~1.732 m)", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert abs(b["result"]["value"] - 2.0 * (3 ** 0.5) / 2) < 1e-9
assert b["result"]["unit"] == "m"

# 9) 不确定度显式单位（mV 与 V 量纲一致）
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {
                "name": "U",
                "value": 10.0,
                "unit": "V",
                "standard_uncertainty": {"value": 100, "unit": "mV"},
            },
        ],
        "formula": "U*2",
    },
)
show("9) u=100 mV for 10 V (expect uc=0.2 V)", r.json())
assert r.status_code == 200, r.text
assert abs(r.json()["result"]["standard_uncertainty"] - 0.2) < 1e-12

# 10) 不确定度单位量纲不一致 -> 422 loc 到字段
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {
                "name": "U",
                "value": 10.0,
                "unit": "V",
                "standard_uncertainty": {"value": 0.1, "unit": "A"},
            },
        ],
        "formula": "U*2",
    },
)
show("10) u unit A vs V -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["loc"][-1] == "unit"

# 11) log 量纲错误：log(m)
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "formula": "log(x)",
    },
)
show("11) log(m) -> 422", r.json())
assert r.status_code == 422

# 12) 未声明符号
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "formula": "x + y",
    },
)
show("12) unknown symbol y -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_symbol"

# 13) 幂次为变量
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "n", "value": 2, "unit": "dimensionless", "standard_uncertainty": 0.0},
        ],
        "formula": "x**n",
    },
)
show("13) x**n -> 422 non_constant_exponent", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "non_constant_exponent"

# 14) 未知单位
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "frobnicate", "standard_uncertainty": 0.1}],
        "formula": "x",
    },
)
show("14) unknown unit -> 422 loc inputs/0/unit", r.json())
assert r.status_code == 422 and r.json()["error"]["loc"] == ["inputs", 0, "unit"]

# 15) 相关系数越界
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x+y",
        "correlation_matrix": [[1, 1.2], [1.2, 1]],
    },
)
show("15) r=1.2 -> 422 loc element", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "correlation_out_of_range"
assert r.json()["error"]["loc"] == ["correlation_matrix", 0, 1]

# 16) 对角线 != 1
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x+y",
        "correlation_matrix": [[0.9, 0], [0, 1]],
    },
)
show("16) diag=0.9 -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "diagonal_not_one"

# 17) 矩阵形状不符
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x+y",
        "correlation_matrix": [[1]],
    },
)
show("17) bad shape -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "bad_matrix_shape"

# 18) sqrt(m^2) 量纲
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "a", "value": 3, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "b", "value": 4, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "sqrt(a^2 + b^2)",
    },
)
show("18) sqrt(a^2+b^2) (expect 5 m)", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert abs(b["result"]["value"] - 5.0) < 1e-9
assert b["result"]["unit"] == "m"

# 19) 重复名称
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "x", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "formula": "x",
    },
)
show("19) duplicate name -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "duplicate_name"

# 20) 常数公式
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "formula": "pi*E",
    },
)
show("20) pi*E, no x dependence (uc=0)", r.json())
assert r.status_code == 200, r.text
assert abs(r.json()["result"]["standard_uncertainty"]) < 1e-12

# --- POST /api/v1/allocate-uncertainty ------------------------------------

# 21) P=U*I，r=0.5，把 u(I) 当未知量，目标 uc=0.5 W
#   Var = 100 uI^2 + 2 uI + 0.04 = 0.25
#   uI = (-2 + sqrt(88)) / 200
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 0.5, "unit": "W"},
    },
)
show("21) allocate u(I) for target uc=0.5 W", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert abs(b["allocated_standard_uncertainty"] - ((88 ** 0.5) - 2.0) / 200.0) < 1e-12
assert b["allocated_standard_uncertainty_unit"] == "A"
assert b["result"]["unit"] == "W"
assert abs(b["result"]["standard_uncertainty"] - 0.5) < 1e-9
q = b["variance_quadratic"]
assert (q["a"], q["b"], q["c"]) == (100.0, 2.0, 0.04000000000000001)
assert q["unknown_uncertainty_unit"] == "A"

# 21b) 把求得的 u(I) 代回 /propagate，合成值应恰为目标
r = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {
                "name": "I",
                "value": 2.0,
                "unit": "A",
                "standard_uncertainty": b["allocated_standard_uncertainty"],
            },
        ],
        "formula": "U*I",
        "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
    },
)
assert r.status_code == 200
assert abs(r.json()["result"]["standard_uncertainty"] - 0.5) < 1e-9

# 22) 目标不可达：r=-0.5 时最小总方差 0.03（x=0.01），目标 uc=0.1 不可达
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1.0, -0.5], [-0.5, 1.0]],
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 0.1, "unit": "W"},
    },
)
show("22) target unreachable -> 422 target_unreachable", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "target_unreachable"
assert r.json()["error"]["loc"] == ["target_combined_uncertainty"]

# 23) 完全负相关、目标 uc=0：可行集仅一个点 uI=0.02
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1, -1], [-1, 1]],
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 0.0, "unit": "W"},
    },
)
show("23) r=-1, target uc=0 -> uI=0.02 A", r.json())
assert r.status_code == 200, r.text
assert abs(r.json()["allocated_standard_uncertainty"] - 0.02) < 1e-9

# 24) 灵敏度为零且目标已满足 -> 无有限上界
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*2",
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 1.0, "unit": "V"},
    },
)
show("24) zero sensitivity, target met -> 422 no_finite_bound", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "no_finite_bound"
assert r.json()["error"]["loc"] == ["allocate_input"]

# 25) 灵敏度为零且目标不满足 -> zero_sensitivity
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*2",
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 0.01, "unit": "V"},
    },
)
show("25) zero sensitivity, target missed -> 422 zero_sensitivity", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "zero_sensitivity"
assert r.json()["error"]["loc"] == ["allocate_input"]

# 26) 待分配输入量未声明
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
        ],
        "formula": "U*2",
        "allocate_input": "Q",
        "target_combined_uncertainty": {"value": 1.0, "unit": "V"},
    },
)
show("26) unknown allocate_input -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "unknown_allocate_input"
assert r.json()["error"]["loc"] == ["allocate_input"]

# 27) 非待分配输入量也缺不确定度
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "allocate_input": "U",
        "target_combined_uncertainty": {"value": 1.0, "unit": "W"},
    },
)
show("27) other input missing uncertainty -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "missing_uncertainty"
assert r.json()["error"]["loc"] == ["inputs", 1, "standard_uncertainty"]

# 28) 目标单位量纲不一致
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
        ],
        "formula": "U*2",
        "allocate_input": "U",
        "target_combined_uncertainty": {"value": 1.0, "unit": "A"},
    },
)
show("28) target unit A vs output V -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "incompatible_unit"
assert r.json()["error"]["loc"] == ["target_combined_uncertainty", "unit"]

# 29) 目标单位换算：500 mW = 0.5 W；uU=0 时 uI = 0.05 A
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.0},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 500, "unit": "mW"},
    },
)
show("29) target 500 mW -> uI=0.05 A", r.json())
assert r.status_code == 200, r.text
assert abs(r.json()["allocated_standard_uncertainty"] - 0.05) < 1e-12
assert abs(r.json()["result"]["standard_uncertainty"] - 0.5) < 1e-9

# 30) 相关矩阵非法仍走同一套校验
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1, 2], [2, 1]],
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 1.0, "unit": "W"},
    },
)
show("30) bad correlation -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "correlation_out_of_range"

# 31) 不相关、取 0 已超目标 -> target_unreachable
r = client.post(
    "/api/v1/allocate-uncertainty",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A"},
        ],
        "formula": "U*I",
        "allocate_input": "I",
        "target_combined_uncertainty": {"value": 0.1, "unit": "W"},
    },
)
show("31) x=0 already above target -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["code"] == "target_unreachable"

# --- POST /api/v1/propagate-joint -------------------------------------------

# 32) 联合传播 P=U*I 与 R=U/I，r=0.5
#   P: cu=[0.2, 0.5]  -> Var=0.04+0.25+2*0.5*0.2*0.5=0.39
#   R: cu=[0.05, -0.125] -> Var=0.0025+0.015625-2*0.5*0.05*0.125=0.011875
#   Cov(P,R)=0.01-0.0125+0.0125-0.0625=-0.0525
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.05},
        ],
        "outputs": [
            {"name": "P", "formula": "U*I"},
            {"name": "R", "formula": "U/I"},
        ],
        "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
    },
)
show("32) joint P=U*I, R=U/I (Var_P=0.39, Var_R=0.011875, Cov=-0.0525)", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert b["number_of_inputs"] == 2 and b["number_of_outputs"] == 2
assert b["outputs"][0] == {
    "name": "P",
    "value": 20.0,
    "unit": "W",
    "standard_uncertainty": 0.39 ** 0.5,
}
assert b["outputs"][1]["name"] == "R"
assert abs(b["outputs"][1]["value"] - 5.0) < 1e-12
assert abs(b["outputs"][1]["standard_uncertainty"] - 0.011875 ** 0.5) < 1e-12
cov = b["output_covariance_matrix"]
assert abs(cov[0][0]["value"] - 0.39) < 1e-12 and cov[0][0]["unit"] == "W ** 2"
assert abs(cov[0][1]["value"] - (-0.0525)) < 1e-12
assert abs(cov[1][0]["value"] - (-0.0525)) < 1e-12
assert abs(cov[1][1]["value"] - 0.011875) < 1e-12
oc = b["output_correlation_matrix"]
assert oc[0][0] == 1.0 and oc[1][1] == 1.0
expected_r = -0.0525 / (0.39 ** 0.5 * 0.011875 ** 0.5)
assert abs(oc[0][1] - expected_r) < 1e-12 and abs(oc[1][0] - expected_r) < 1e-12
# 每个输出的灵敏度系数（雅可比行）
sens = {s["output"]: s["coefficients"] for s in b["sensitivity_coefficients"]}
assert [c["value"] for c in sens["P"]] == [2.0, 10.0]
assert [c["value"] for c in sens["R"]] == [0.5, -2.5]

# 32b) 与单输出接口一致：联合对角元 = /propagate 的合成不确定度
r1 = client.post(
    "/api/v1/propagate",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.05},
        ],
        "formula": "U*I",
        "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
    },
)
assert r1.status_code == 200
assert abs(r1.json()["result"]["standard_uncertainty"] - b["outputs"][0]["standard_uncertainty"]) < 1e-15

# 33) 输出名重复 -> 422 loc outputs/1/name
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": "a", "formula": "x"}, {"name": "a", "formula": "x*2"}],
    },
)
show("33) duplicate output name -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "duplicate_name"
assert r.json()["error"]["loc"] == ["outputs", 1, "name"]

# 34) 第二个输出公式非法（未声明符号）-> 422 loc outputs/1/formula
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": "a", "formula": "x"}, {"name": "b", "formula": "x+y"}],
    },
)
show("34) unknown symbol in output 1 -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "unknown_symbol"
assert r.json()["error"]["loc"] == ["outputs", 1, "formula"]

# 34b) 输出公式量纲不一致 -> 422 loc 同样精确定位
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [
            {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
            {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.05},
        ],
        "outputs": [{"name": "P", "formula": "U*I"}, {"name": "bad", "formula": "U+I"}],
    },
)
assert r.status_code == 422
assert r.json()["error"]["code"] == "dimension_mismatch"
assert r.json()["error"]["loc"] == ["outputs", 1, "formula"]

# 35) 输出数量越界：1 个或 9 个 -> 422 loc outputs
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": "a", "formula": "x"}],
    },
)
show("35) single output -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["loc"] == ["outputs"]
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": f"y{k}", "formula": f"x*{k+1}"} for k in range(9)],
    },
)
show("35b) nine outputs -> 422", r.json())
assert r.status_code == 422 and r.json()["error"]["loc"] == ["outputs"]

# 36) 8 个输出（上限）可正常计算
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 2, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": f"y{k}", "formula": f"x*{k+1}"} for k in range(8)],
    },
)
show("36) eight outputs -> 200", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert b["number_of_outputs"] == 8
# y_k = (k+1) x，完全相关：输出相关系数矩阵应全为 1
assert all(abs(v - 1.0) < 1e-12 for row in b["output_correlation_matrix"] for v in row)

# 37) 相关矩阵非法仍走同一套校验
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.1},
        ],
        "outputs": [{"name": "s", "formula": "x+y"}, {"name": "d", "formula": "x-y"}],
        "correlation_matrix": [[1, 1.2], [1.2, 1]],
    },
)
show("37) bad correlation -> 422", r.json())
assert r.status_code == 422
assert r.json()["error"]["code"] == "correlation_out_of_range"
assert r.json()["error"]["loc"] == ["correlation_matrix", 0, 1]

# 38) 零不确定度输出：非对角相关系数无定义置 0，对角为 1
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [{"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1}],
        "outputs": [{"name": "c", "formula": "pi*E"}, {"name": "y", "formula": "x*2"}],
    },
)
show("38) zero-variance output -> off-diagonal corr 0", r.json())
assert r.status_code == 200, r.text
b = r.json()
assert b["outputs"][0]["standard_uncertainty"] == 0.0
assert b["output_correlation_matrix"] == [[1.0, 0.0], [0.0, 1.0]]

# 39) 缺省相关矩阵 = 单位阵；完全负相关输入使两输出协方差为负
r = client.post(
    "/api/v1/propagate-joint",
    json={
        "inputs": [
            {"name": "x", "value": 1, "unit": "m", "standard_uncertainty": 0.1},
            {"name": "y", "value": 2, "unit": "m", "standard_uncertainty": 0.2},
        ],
        "outputs": [{"name": "s", "formula": "x+y"}, {"name": "d", "formula": "x-y"}],
    },
)
show("39) default identity corr; s=x+y, d=x-y", r.json())
assert r.status_code == 200, r.text
b = r.json()
# Var(s)=0.01+0.04=0.05, Var(d)=0.05, Cov(s,d)=0.01-0.04=-0.03
cov = b["output_covariance_matrix"]
assert abs(cov[0][0]["value"] - 0.05) < 1e-12
assert abs(cov[0][1]["value"] - (-0.03)) < 1e-12
assert abs(b["output_correlation_matrix"][0][1] - (-0.6)) < 1e-12
assert cov[0][1]["unit"] == "m ** 2"

print("\nALL TESTS PASSED")
