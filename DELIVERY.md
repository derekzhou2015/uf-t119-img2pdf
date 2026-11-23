# 交付说明：PNG 隔行 / 16 位透明通道行为分裂的普查、归因与修复

## 1. 问题陈述

同一批像素完全相同、仅 IHDR 中隔行标志位（byte 28）不同的 16 位带透明
PNG，img2pdf 的行为随编码变体分裂：

- **非隔行（interlace=0）**：工具拒收并退出
  （`AlphaChannelError: Refusing to work with multiple >8bit channels.`）。
- **隔行 Adam7（interlace=1）**：工具静默产出 PDF，透明通道被降级成 8 位
  软蒙版（`/SMask`），整体位深从 16 位掉到 8 位，无任何错误。

## 2. 根因与分岔点（代码级归因）

`read_images()`（`src/img2pdf.py`）里存在两条 PNG 处理路径，分岔由
IHDR byte 28 决定：

| 路径 | 入口条件 | 是否检查 IHDR 位深 |
|------|----------|--------------------|
| 直嵌 IDAT 路径 | `rawdata[28] == 0`（非隔行，`img2pdf.py:2306`） | 是，`get_imgmetadata(..., rawdata, ...)` 读到 byte 24 |
| 通用帧循环路径 | 其它一切（隔行 PNG、GIF、TIFF…，`img2pdf.py:2387` 起） | **否**，调用 `get_imgmetadata()` 时未传 `rawdata` |

`get_imgmetadata()` 的 16 位透明拒收逻辑（`img2pdf.py:1538-1555`）依赖
`rawdata[24]`（IHDR 位深），且仅在 `rawdata is not None` 时执行：

```python
if imgformat == ImageFormat.PNG and rawdata is not None:
    depth = rawdata[24]
    if depth > 8:
        raise AlphaChannelError("Refusing to work with multiple >8bit channels.")
```

**降级发生在哪一步**：Pillow 在 `Image.open(...).load()` 阶段就把 16 位
RGBA/LA 无损地降成 8 位 RGBA（已实测，Pillow 13.0.0 对隔行与非隔行都如此）。
非隔行路径因为还持有原始字节、能在 Pillow 降级之前读到 IHDR 的 16 位而拒收；
隔行路径丢掉了 `rawdata`，等走到帧循环时 Pillow 已经把数据降成 8 位，于是
`to_png_data()` 重新编码出 8 位 PNG，alpha 被拆成 8 位 `/SMask`，静默落盘。

一句话：**分岔在 `read_images()` 的路径选择，降级在 Pillow 解码阶段，
拒收契约因为帧循环漏传 `rawdata` 而在隔行路径上失效。**

## 3. 完整普查结果（46 个手工构造 PNG 形态）

普查脚本 `tools/census.py`，纯 stdlib + Pillow 读取，离线可复跑：

```
.venv\Scripts\python.exe -I tools\census.py
```

### 3.1 修复前的完整分岔表（关键分裂组合）

| 颜色类型 / 位深 / 透明形态 | 非隔行 i=0 | 隔行 i=1（修复前） |
|---|---|---|
| ct6 RGBA 16 位（真彩+alpha） | 拒收 `AlphaChannelError` | **静默接受，8 位 + 8 位 SMask（分裂）** |
| ct4 LA 16 位（灰度+alpha） | 拒收 `AlphaChannelError` | **静默接受，RGBA 8 位 + SMask（分裂）** |
| ct0 灰度 16 位 + tRNS | 拒收 `AlphaChannelError` | **`ValueError: unknown colorspace`（分裂）** |
| ct2 RGB 16 位 + tRNS | 拒收 `AlphaChannelError` | **静默接受，RGBA 8 位 + SMask（分裂）** |

相邻分裂（无透明，但隔行路径同样异常）：

| 形态 | 非隔行 i=0 | 隔行 i=1（修复前） |
|---|---|---|
| ct0 灰度 16 位，无 alpha | 接受，BPC=16 DeviceGray | **`ValueError: unknown colorspace`** |

灰度 16 位的 `ValueError` 成因：Pillow 把 16 位灰度暴露为 `I;16` 模式，该名
不在 `Colorspace` 枚举里；`get_imgmetadata()` 本有一段“byte 25==0 则回退为
`Colorspace.L`”的兜底（`img2pdf.py:1677-1682`），但同样需要 `rawdata`，
帧循环没传，所以兜底失效。

### 3.2 修复后行为（统一后）

- 上表 4 个 16 位透明组合，**隔行与非隔行全部统一为
  `AlphaChannelError` 拒收**（共 8 个用例，2 引擎一致）。
- 隔行灰度 16 位无 alpha：从 `ValueError` 修复为**接受且 BPC=16
  DeviceGray**，与非隔行产物完全对齐。
- 其余 38 个形态全部维持接受，行为不变。

### 3.3 普查中确认“不分裂、未改动”的相邻形态

- 8 位 RGBA / LA（ct6/ct4，depth=8）：两变体都接受，8 位 + 8 位 SMask。
- 8 位灰度/RGB + tRNS（ct0/ct2，depth=8）：两变体都接受，8 位 SMask。
- 索引色 ct3，depth 1/2/4/8，带/不带 tRNS：两变体都接受，保留原生位深，
  带 tRNS 时加 SMask。
- 灰度 ct0，depth 1/2/4/8：两变体都接受，位深保留。
- RGB ct2，depth 8：两变体都接受。
- 多帧形态：GIF（含 `animation.gif` 两帧）走帧循环，2 帧、8 位、PNG 编码，
  不受影响；APNG 在 Pillow 13 不可读，走既有的 `ImageOpenError`，与本分裂
  无关；TIFF 16 位透明有独立的 `AlphaChannelError` 分支（`img2pdf.py:1556`
  -1569），本就正确，未改动。

### 3.4 一个保留的、与本问题无关的既有局限

隔行 **RGB 16 位无 alpha（ct2）** 会被 Pillow 在解码时降成 8 位，最终 PDF
BPC=8（非隔行同图则直嵌 IDAT 保留 16 位）。这是 Pillow 解码能力局限，且
**不带透明、不在本次拒收契约范围内**；修复未触及它，避免越界改变其他语义。
如需补齐，须自行实现 Adam7 反交错 + 16 位直嵌，工作量与风险都显著更大。

## 4. 修复方案与定案理由

### 选定走向：对 16 位透明采取「明确拒绝」，与非隔行路径同契约

三条候选走向（位深保留 / 明确拒绝 / 明示降级）中选择**明确拒绝**，理由：

1. **这是既有契约**。非隔行路径、TIFF 16 位透明路径早已用同一个
   `AlphaChannelError` 拒收，错误信息还指引用户用 ImageMagick
   `convert -depth 8` 自行降级。让隔行路径对齐它，是消除分裂、而非发明
   新行为，改动面最小、最可预测。
2. **位深保留在当前架构下不可行**。PDF 软蒙版由 Pillow 重编码产生，而
   Pillow 在打开 16 位 RGBA/LA 的瞬间就已丢成 8 位；要保留 16 位必须自行
   实现 Adam7 反交错、通道拆分与 16 位 PNG filter 编码，等于重写一条像素
   管线，远超本次缺陷范围。
3. **明示降级会把静默数据损失合法化**。当前没有任何降级开关或警告通道，
   新造一条“悄悄降 8 位”的显式路径反而固化了数据损失，违背无损转换的
   项目定位。

### 实际代码改动（一处，一行有效逻辑）

`src/img2pdf.py` 帧循环内的 `get_imgmetadata()` 调用补传 `rawdata`：

```python
color, ndpi, imgwidthpx, imgheightpx, rotation, iccp = get_imgmetadata(
    imgdata, imgformat, default_dpi, colorspace, rawdata, rot
)
```

这一处同时：

- 让 16 位透明 PNG 在隔行路径命中既有的 IHDR 位深拒收（4 类组合统一）；
- 让隔行 16 位灰度无 alpha 命中 `I;16 → Colorspace.L` 兜底，正确产出
  16 位 DeviceGray。

**牺牲面**：

- 16 位带透明的隔行 PNG 从“静默出 8 位 PDF”变为“拒收”。这正是要求的契约
  贯通；任何此前依赖该静默降级产物的下游，现在会收到明确错误，需按提示先
  自行降到 8 位。
- 隔行 RGB 16 位无 alpha 的 Pillow 8 位截断保留未修（见 3.4）。

### 影响面与“其他格式语义不变”的论证

补传的 `rawdata` 仅在 `get_imgmetadata()` 的 PNG 分支被读取
（`imgformat == ImageFormat.PNG`）：

- GIF / JPEG2000：进入同一透明判断但不读 `rawdata[24]`，行为不变；
- TIFF：走独立的 `elif` 分支，基于 `tag_v2` 位深，不看 `rawdata`，不变；
- 实测 `animation.gif`、`normal.png` 等既有夹具产物与帧数、位深、SMask
  全部一致。

## 5. 测试与回归

### 新增离线回归（Windows 原生、无外部工具、无网络）

- `src/pngbuild.py`：纯 stdlib 的手工 PNG 构造器，支持全部颜色类型/位深、
  Adam7 隔行、tRNS、PLTE。隔行夹具即按要求手工构造，不依赖 ImageMagick。
- `src/test_png_interlace_16bit.py`：59 个测试，覆盖
  - 4 类 16 位透明组合 × 2 隔行变体 × 2 引擎 → 统一 `AlphaChannelError`；
  - 隔行/非隔行灰度 16 位无 alpha → BPC=16 DeviceGray、无 SMask；
  - 8 位 RGBA/LA、8 位 tRNS、索引色 1/2/4/8 带 tRNS → 接受且位深/SMask
    与非隔行一致（保证“隔行无透明与低深度图的既有产物不受波及”）；
  - 全合法位深矩阵的隔行奇偶校验；
  - GIF 多帧、普通 PNG 夹具等其他格式语义不变；
  - 构造器自检：隔行夹具能被 Pillow 解回与非隔行完全相同的像素。

所有普查出的分裂组合都有对应用例，可复跑：

```
.venv\Scripts\python.exe -I -m pytest src/test_png_interlace_16bit.py -q
```

### 测试收集的环境适配（不改变被测语义）

`src/img2pdf_test.py` 顶部探测 ImageMagick 时只捕获 `CalledProcessError`，
在本机（无 `convert`/`magick`）抛 `FileNotFoundError` 导致**整个模块收集
失败**。将捕获放宽为
`(CalledProcessError, FileNotFoundError, OSError)`，缺失时回退到
`magick` 命令名并由既有的 `HAVE_IMAGEMAGICK_MODERN` 等标志把相关测试
skip。这是让全量套件在离线 Windows 可收集、可运行的最小改动，不触碰任何
img2pdf 逻辑。

### 全量回归结果

```
.venv\Scripts\python.exe -I -m pytest src/ -q
```

- **259 passed**（既有套件 200 + 新增 59），**122 skipped**（依赖缺失的
  ghostscript/poppler/mupdf/pdfimages/ImageMagick/faketime 的用例），
  新增测试 0 失败。
- 32 个 `test_date` **error 属明确豁免项**：它们在 setup 阶段调用 Unix 的
  `env`/`date` 外部命令，本机不存在而抛 `FileNotFoundError`。已用
  `git stash` 对照验证：未改动的原始代码在同一环境下于收集阶段即崩溃
  （即题述的“既有收集问题”），根本到不了这些用例；修复收集后该环境红灯
  才显形，与本次代码改动无因果关系。验收按题意不计入。

### 手工端到端（CLI）

对隔行与非隔行 16 位 RGBA 各跑一次 CLI，两个引擎（internal / pikepdf）
均以退出码 1、相同的 `Refusing to work with multiple >8bit channels.`
拒收，且在任何 PDF 内容写入前中止（输出文件为 0 字节，源于 argparse
`FileType("wb")` 解析期建句柄的既有统一行为，非隔行拒收同样如此）。

## 6. 改动文件清单

| 文件 | 性质 | 说明 |
|------|------|------|
| `src/img2pdf.py` | 修改（1 处） | 帧循环 `get_imgmetadata` 补传 `rawdata` |
| `src/img2pdf_test.py` | 修改（1 处） | 外部工具探测放宽异常捕获，修复离线收集 |
| `src/pngbuild.py` | 新增 | 手工 PNG/Adam7/tRNS 构造器（测试夹具） |
| `src/test_png_interlace_16bit.py` | 新增 | 59 个离线回归测试 |
| `tools/census.py` | 新增 | 46 形态行为普查脚本，可复跑 |
| `tools/work/` | 新增 | 普查原始输出与手工样本（非测试依赖） |
