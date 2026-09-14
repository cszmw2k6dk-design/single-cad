# 单线图块库（Block Library）使用说明

这是从「半自动」落地到「内置 CAD」的第一步。目标：**把常用的单线图符号做成可维护的块库，用一个 LISP 命令快速插入 + 自动编号，不再复制粘贴、不再手敲标签。**

---

## 1. 目录结构（这就是“数据库”）

```
blocklib/
├── _manifest.csv            ← ★ 注册表（数据库核心）集中定义所有块
├── blocks/                  ← 块文件（.dwg 优先，缺省回退 .dxf）
│   ├── INVERTER.dxf          （起步模板，另存为 .dwg 后由你维护）
│   ├── TRANSFORMER.dxf
│   ├── MV_SWITCHGEAR.dxf
│   ├── MAIN_TRANSFORMER.dxf
│   ├── POI.dxf
│   ├── BESS_UNIT.dxf
│   ├── BESS_PCS.dxf
│   ├── RECLOSER.dxf
│   ├── FUSE.dxf
│   ├── DISCONNECT.dxf
│   ├── BUS.dxf
│   ├── FEEDER.dxf
│   ├── GROUND.dxf
│   ├── CT.dxf
│   ├── PT.dxf
│   ├── ARRESTER.dxf
│   └── METER.dxf
└── lisp/
    ├── bklib.lsp             ← ★ AutoLISP 工具（插入/清单/刷新）
    └── bklib.dcl             ←  选块对话框
```

`_manifest.csv` 是**唯一的数据源**（=数据库）。它会告诉 LISP：有哪些块、每块的图形、默认缩放/旋转、前缀、有哪些属性。

```
格式: ID,BLOCK,FILE,PREFIX,XSCALE,YSCALE,ROT,ATTRS
例  : INVERTER,INVERTER,INVERTER.dxf,PCS-,1,1,0,TAG;ACKW;STR;FUSE
```

**要新增/改块**：修改 `_manifest.csv`（加一行），再在 `blocks/` 放对应 `.dxf` 或 `.dwg` 即可，无需改 LISP。

---

## 2. 把起步模板变成可维护的 .dwg（一次性）

模板是 `.dxf`（因为本机没装 AutoCAD/ODA，无法直接产出 .dwg 二进制）。放在 CAD 里更合你“打开就能更新”的需求：

### 方式 A（推荐，一键批量）
1. 在 AutoCAD 里加载 `bklib.lsp`。
2. 用 `BKPATH` 设好库路径（**建议无空格路径**，如 `C:/Blocks/SLD_Lib`）。
3. 运行命令 `BKDXF2DWG`：
   - 自动遍历注册表，把每个 `blocks/<块>.dxf` 转为同名 `.dwg`；
   - **已有 .dwg 会自动跳过**（不会覆盖你已改好的块）。
4. 完成后 `blocks/` 里全是 `.dwg`，注册表 `FILE` 列也已声明为 `.dwg`。

> 若某个 CAD 版本转换报错，退回“方式 B”，只点几次即可。

### 方式 B（手动，零风险）
1. 在 AutoCAD 打开 `blocks/INVERTER.dxf`。
2. 调整图形、文字、属性位置（改到公司标准）。
3. **另存为** 同目录下的 `INVERTER.dwg`（文件名 = 块名）。
4. 其余 16 个同样处理，或用 `mover` 批量转换。

> 完成后 `blocks/` 里就是清一色的 `.dwg`，以后你直接双击它打开、改、保存，就是更新块库。LISP 会优先生成 `.dwg`。

---

## 3. 内置 CAD：加载 AutoLISP

### 方法 A（推荐/一劳永逸）
把 `bklib.lsp` 放入 AutoCAD 的**启动组**（Startup Suite）：
`APPLOAD` → 点“内容(Contents)” → “启动组” → 添加 `bklib.lsp`。
以后每次打开图纸自动加载。

### 方法 B（临时）
命令行：
```
APPLOAD
```
选择 `blocklib/lisp/bklib.lsp` → Load。

---

## 4. 使用方法

| 命令 | 作用 |
|---|---|
| `BKINS` | **快速插入块**：弹对话框选块 → 点插入点 → 自动缩放/旋转（可回车用默认）→ **自动编号 + 填充属性** |
| `BKDXF2DWG` | **一键把库中 .dxf 开始转成 .dwg**（已有 .dwg 自动跳过），让库里清一色 .dwg |
| `BKLIST` | 列出库中所有块（ID/块名/前缀/属性） |
| `BKREF` | **刷新库**：从 `blocks/` 重新定义所有块，图中已有块自动更新（你改了 .dwg 后跑这个） |
| `BKPATH` | 设置块库路径（换位置时用） |

### 自动编号（重点，省时间的关键）
- PCS 块会自动编号 `PCS-101`、`PCS-102`…（扫描图中已有实例的最大号 +1）。
- TR 块 → `TR-101`、BESS → `BESS-101`，以此类推。
- 插入后如需改名，双击块改 TAG 即可（其他属性各填充项会自动跟随提示）。

> **注意**：路径里“single line-cad”带空格，个别 CAD 版本 `-INSERT` 读路径可能出错。建议用 `BKPATH` 把库放到无空格路径（如 `C:/Blocks/SLD_Lib`）再使用。

---

## 5. 维护规则

- **库放在哪**：用 `BKPATH` 指定，路径别带空格。
- **改动流程**：打开对应 `.dwg` → 改 → 保存 → 在图里跑 `BKREF` 一键更新，所有图纸引用同步。
- **加新块类型**：① `_manifest.csv` 加一行；② `blocks/` 放同名 `.dwg`；③ 重载 LISP。
- **属性顺序**：`TAG` 是自动编号主属性；其余按需用 `;` 分隔写在 `ATTRS` 列。

---

## 6. 已生成的起步模板（可在 CAD 里直接打开另存 .dwg）

从 [blocklib_contact.png](<C:/Users/ZhaokeShi/.codex/visualizations/2026/09/07/01a07a4e-a3ed-7ec3-accc-8f1f3d9254cc/blocklib_contact.png>) 可看到全部 17 个符号与属性。图形只是**示意起点**，请按你公司图例改到标准后再另存 .dwg。

---

## 7. 下一步（可选）

当前 `BKINS` 解决了“快速插入+编号”。要继续往全自动走，下一步是：
把 `_manifest.csv` 与 `sld_generate.py` 打通——让生成引擎直接**按注册表布局**输出整张单线图（而不是手工逐块插），即方案里的 **L2 自动生成**。
