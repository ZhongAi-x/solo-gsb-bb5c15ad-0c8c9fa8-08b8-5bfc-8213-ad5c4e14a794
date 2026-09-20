# 校准计算不确定度传播 API

基于 FastAPI + SymPy + Pint，对测量模型 $y=f(x_1,\dots,x_n)$ 做 GUM 一阶不确定度传播：
服务端解析仅含白名单运算的公式并进行量纲校验，利用符号偏导与相关系数矩阵计算结果值、
单位、合成标准不确定度，并按输入量列出含相关项的方差贡献。

另提供**多输出联合传播**接口：对同一组输入量与同一输入协方差，按一阶雅可比计算
$C=J\Sigma J^{T}$，一次返回 2～8 个输出量的值、单位、合成标准不确定度、完整输出
协方差矩阵与输出相关系数矩阵。

另提供**不确定度逆分配**接口：把某个输入量的标准不确定度视为未知量，在完整保留相关项
的前提下反求满足目标合成标准不确定度的最大允许取值。

另提供**蒙特卡洛传播校核**接口：按输入相关矩阵的特征分解生成相关正态样本（半正定但
奇异的相关矩阵也可运行），逐样本计算白名单公式，返回样本均值、标准不确定度、
2.5%/50%/97.5% 分位数，以及相对 GUM 一阶传播结果的偏差，用于非线性模型的一阶近似校核。

## 启动

单条命令一键启动（Docker Compose）：

```bash
docker compose up --build
```

依赖在**容器启动时**安装；服务随后监听 API 端口 **8000**（交互文档
`http://localhost:8000/docs`）。

## 配置

- 宿主机端口：通过环境变量 `PORT` 覆盖（默认 `8000`），容器内固定监听 `8000`。
  例：`PORT=9000 docker compose up --build`
- 依赖清单：`requirements.txt`（容器启动时由 `docker/entrypoint.sh` 安装）。

## API（端口 8000）

- `GET /health` — 健康检查
- `GET /api/v1/whitelist` — 公式白名单
- `POST /api/v1/propagate` — 不确定度传播；相关矩阵非对称 / 非半正定 / 单位量纲不兼容 / 公式不合法时返回 **422**，错误体 `error.loc` 精确定位（如 `correlation_matrix.0.1`、`inputs.0.unit`、`formula`）
- `POST /api/v1/propagate-joint` — 多输出联合传播（见下）
- `POST /api/v1/allocate-uncertainty` — 不确定度逆分配（见下）
- `POST /api/v1/propagate-monte-carlo` — 相关输入量的蒙特卡洛传播校核（见下）
- 交互文档：`http://localhost:8000/docs`

### 多输出联合传播 `POST /api/v1/propagate-joint`

请求体在 `/api/v1/propagate` 的基础上把单个 `formula` 换成 2～8 个**具名输出公式**
`outputs: [{name, formula}, ...]`；输入量、公式白名单、单位与相关矩阵校验完全复用
单输出接口。设 $J_{ki}=\partial f_k/\partial x_i$ 在输入估计值处取值，输入协方差
$\Sigma=DRD$（$D=\mathrm{diag}(u(x_i))$，$R$ 为输入相关系数矩阵），服务端按一阶
雅可比计算输出协方差矩阵：

$$
C=J\Sigma J^{T},\qquad
C_{kl}=\sum_{i}\sum_{j}c_i^{(k)}u(x_i)\,r_{ij}\,u(x_j)c_j^{(l)}
$$

对角元即各输出量的合成方差 $u_c(y_k)^2$。响应返回每个输出的值、单位、合成标准
不确定度，以及：

- `output_covariance_matrix` — 完整输出协方差矩阵，每个单元给出数值与单位
  （**输出 i 单位 × 输出 j 单位**，如 `Ω * W`）；
- `output_correlation_matrix` — 输出相关系数矩阵 $C_{kl}/\big(u_c(y_k)u_c(y_l)\big)$
  （某输出标准不确定度为 0 时其非对角相关系数无定义，置 0）；
- `sensitivity_coefficients` — 每个输出对各输入量的灵敏度系数（即雅可比各行）。

请求示例（$P=U\cdot I$、$R=U/I$ 联合传播，$r(U,I)=0.5$）：

```bash
curl -s http://localhost:8000/api/v1/propagate-joint \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": [
      {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
      {"name": "I", "value": 2.0, "unit": "A", "standard_uncertainty": 0.05}
    ],
    "outputs": [
      {"name": "P", "formula": "U*I"},
      {"name": "R", "formula": "U/I"}
    ],
    "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]]
  }'
```

响应（节选）：

```json
{
  "outputs": [
    {"name": "P", "value": 20.0, "unit": "W", "standard_uncertainty": 0.6244997998398398},
    {"name": "R", "value": 5.0, "unit": "Ω", "standard_uncertainty": 0.10897247358851683}
  ],
  "output_covariance_matrix": [
    [{"value": 0.39, "unit": "W ** 2"},  {"value": -0.0525, "unit": "Ω * W"}],
    [{"value": -0.0525, "unit": "Ω * W"}, {"value": 0.011875, "unit": "Ω ** 2"}]
  ],
  "output_correlation_matrix": [[1.0, -0.7714542762891773], [-0.7714542762891773, 1.0]]
}
```

输出数量不在 2～8 范围、输出名重复或公式非法时返回 **422**，`error.loc` 精确定位
（如 `outputs`、`outputs.1.name`、`outputs.1.formula`）；相关矩阵非法、单位量纲
不兼容等沿用传播接口的同一套校验与 code/loc。

### 不确定度逆分配 `POST /api/v1/allocate-uncertainty`

请求体与 `/api/v1/propagate` 相同，额外指定待分配输入量与**带单位的目标合成标准不
确定度**。待分配输入量可以省略 `standard_uncertainty`（其值即待求未知量 $u_k$，
其余输入量仍必须给出）。

设 $c_i=\partial f/\partial x_i$，$r_{ij}$ 为相关系数，总方差完整保留相关项后是关于
$u_k$（以该输入量自身单位计）的二次函数：

$$
u_c^2(u_k)=c_k^2u_k^2+2c_ku_k\sum_{j\ne k}c_ju_jr_{kj}
+\sum_{i\ne k}\sum_{j\ne k}c_iu_i c_ju_jr_{ij}=a\,u_k^2+b\,u_k+c
$$

服务端解二次不等式 $a u_k^2+b u_k+c\le U_t^2$（$u_k\ge0$），返回**最大非负根**
（满足目标的最大标准不确定度）及其单位，并把该值代回，按与传播接口完全相同的结构
给出合成值、灵敏度系数与含相关项的方差贡献；`variance_quadratic` 同时回显带单位的
$a/b/c$ 三个系数。

请求示例（$P=U\cdot I$，$u(U)=0.1\,\mathrm{V}$，$r=0.5$，目标
$u_c(P)=0.5\,\mathrm{W}$，反求 $u(I)$）：

```bash
curl -s http://localhost:8000/api/v1/allocate-uncertainty \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": [
      {"name": "U", "value": 10.0, "unit": "V", "standard_uncertainty": 0.1},
      {"name": "I", "value": 2.0, "unit": "A"}
    ],
    "formula": "U*I",
    "correlation_matrix": [[1.0, 0.5], [0.5, 1.0]],
    "allocate_input": "I",
    "target_combined_uncertainty": {"value": 0.5, "unit": "W"}
  }'
```

响应（节选）：

```json
{
  "result": {"value": 20.0, "unit": "W", "standard_uncertainty": 0.5},
  "allocate_input": "I",
  "allocated_standard_uncertainty": 0.036904157598234295,
  "allocated_standard_uncertainty_unit": "A",
  "target_combined_uncertainty": {"value": 0.5, "unit": "W", "standard_uncertainty": 0.5},
  "variance_quadratic": {
    "a": 100.0, "a_unit": "W ** 2 / A ** 2",
    "b": 2.0,   "b_unit": "W ** 2 / A",
    "c": 0.04,  "c_unit": "W ** 2",
    "unknown_uncertainty_unit": "A"
  }
}
```

目标单位须与公式输出同量纲（支持单位换算，如目标填 `{"value": 500, "unit": "mW"}`）。
以下情况返回 **422**，错误体带明确 `code` 与 `loc`：

| code | loc | 含义 |
| --- | --- | --- |
| `target_unreachable` | `target_combined_uncertainty` | 无论 $u_k$ 取何非负值，最小合成标准不确定度仍高于目标 |
| `zero_sensitivity` | `allocate_input` | $c_k=0$（该输入量不影响结果）且当前值已不满足目标 |
| `no_finite_bound` | `allocate_input` | $c_k=0$ 而目标已满足，任意大的 $u_k$ 都可行，不存在有限上界 |
| `unknown_allocate_input` | `allocate_input` | 待分配输入量未在 `inputs` 中声明 |
| `missing_uncertainty` | `inputs.<i>.standard_uncertainty` | 非待分配输入量缺少标准不确定度 |
| `incompatible_unit` | `target_combined_uncertainty.unit` | 目标单位与公式输出量纲不一致 |

相关矩阵非法、公式不合法、其余单位错误沿用传播接口的同一套校验与 code/loc。

### 蒙特卡洛传播校核 `POST /api/v1/propagate-monte-carlo`

针对**非线性测量模型**，在 `/api/v1/propagate` 的 `inputs` / `formula` /
`correlation_matrix` 之外增加：

- `samples` —— 蒙特卡洛样本数，整数 **1000～100000**；
- `seed` —— 可选随机种子（非负整数）；**固定 seed 时结果可复现**，缺省时非确定性。

每个输入量视为以估计值为中心、以其标准不确定度（**按输入声明单位解释**，支持
`{value, unit}` 显式不确定度单位与角度 deg/rad）为标准差的正态变量。采样器对输入
相关系数矩阵 $R$ 做特征分解 $R=V\operatorname{diag}(\lambda)V^T$，生成

$$
X = \mu + Z\\,(D\\,V\\,\operatorname{diag}(\sqrt{\lambda}))^T,
\\qquad Z\\sim N(0,I),\\ \\Sigma=DRD
$$

零（及钳制后的微小负）特征值方向不产生随机成分，因此**半正定但奇异**的相关矩阵
（如完全相关 $r=\pm1$、秩亏损矩阵）同样可以运行。逐样本代入白名单公式（NumPy
向量化求值，量纲与单位校验与 `/propagate` 完全一致）后，响应给出：

- `result` —— 样本**均值**、输出单位与样本标准不确定度（实验标准偏差，ddof=1）；
- `quantiles` —— 输出样本的 **2.5% / 50% / 97.5% 分位数**（线性插值）；
- `first_order` —— 同一输入下 GUM 一阶传播的值与合成标准不确定度；
- `relative_deviation` —— 样本均值、样本标准不确定度相对一阶结果的相对偏差
  $(\text{MC}-\text{一阶})/|\text{一阶}|$；一阶值恰为 0 时对应字段为 `null`。

请求示例（非线性模型 $y=x_1x_2+e^{-x_1/2}$，$r=0.3$，10000 个样本，固定种子）：

```bash
curl -s http://localhost:8000/api/v1/propagate-monte-carlo \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": [
      {"name": "x", "value": 1.0, "unit": "dimensionless", "standard_uncertainty": 0.05},
      {"name": "y", "value": 2.0, "unit": "dimensionless", "standard_uncertainty": 0.05}
    ],
    "formula": "x*y + exp(-x/2)",
    "correlation_matrix": [[1.0, 0.3], [0.3, 1.0]],
    "samples": 10000,
    "seed": 20240517
  }'
```

响应（节选）：

```json
{
  "result": {"value": 2.6047, "unit": "dimensionless", "standard_uncertainty": 0.1113},
  "quantiles": {"0.025": 2.3938, "0.5": 2.6025, "0.975": 2.8294},
  "first_order": {"value": 2.6065, "unit": "dimensionless", "standard_uncertainty": 0.1106},
  "relative_deviation": {"mean": -0.00070, "standard_uncertainty": 0.00597},
  "samples": 10000,
  "seed": 20240517
}
```

若任一样本落入公式无效域（如 `sqrt`/`log` 取到非正数、`asin`/`acos` 参数越界、
结果非有限），返回 **422**：

```json
{
  "error": {
    "code": "invalid_sample_domain",
    "message": "940 个样本落入公式无效域（如对数取非正数、除零、asin/acos 参数越界等），无法完成蒙特卡洛传播",
    "loc": ["formula"],
    "invalid_sample_count": 940,
    "first_invalid_sample_index": 0
  }
}
```

`invalid_sample_count` 为失败样本数量，`first_invalid_sample_index` 为首个失败样本
的索引（从 0 开始，固定 seed 时可复现）。`samples` 越界、`seed` 非法、相关矩阵非法、
单位/量纲不兼容、公式不合法等同样返回 **422**，沿用既有 code/loc 定位。
