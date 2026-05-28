# 解题结果

## 解题思路

### 解题思路

**第一步：建立空间直角坐标系**  
以 \(D\) 为原点，\(DA\) 为 \(x\) 轴，\(DC\) 为 \(y\) 轴，\(DD_1\) 为 \(z\) 轴。  
由棱长4，得各点坐标：  
\(A(4,0,0), B(4,4,0), C(0,4,0), D(0,0,0)\)，  
\(A_1(4,0,4), B_1(4,4,4), C_1(0,4,4), D_1(0,0,4)\)。  
中点：\(E(2,0,4)\)（\(A_1D_1\)中点），\(F(2,4,4)\)（\(C_1B_1\)中点）。  
由 \(CG = 3C_1G\)，得 \(G\) 在 \(CC_1\) 上，设 \(G(0,4,z)\)，由比例得 \(z=3\)，故 \(G(0,4,3)\)。

**第二步：证明 \(GF \perp\) 平面 \(EBF\)**  
计算向量：\(\overrightarrow{GF} = (2,0,1)\)，\(\overrightarrow{EB} = (2,4,-4)\)，\(\overrightarrow{EF} = (0,4,0)\)。  
验证 \(\overrightarrow{GF} \cdot \overrightarrow{EB} = 4+0-4=0\)，\(\overrightarrow{GF} \cdot \overrightarrow{EF} = 0+0+0=0\)，  
故 \(GF \perp EB\) 且 \(GF \perp EF\)，所以 \(GF \perp\) 平面 \(EBF\)。

**第三步：求平面 \(FBE\) 与平面 \(EBG\) 夹角的余弦值**  
平面 \(FBE\) 的法向量即 \(\overrightarrow{GF} = (2,0,1)\)。  
平面 \(EBG\) 中，\(\overrightarrow{EB} = (2,4,-4)\)，\(\overrightarrow{EG} = (-2,4,-1)\)，  
求法向量 \(\vec{n} = \overrightarrow{EB} \times \overrightarrow{EG} = (12,6,16)\)，化简为 \((6,3,8)\)。  
两平面夹角余弦值：\(\cos\theta = \frac{|\vec{n} \cdot \overrightarrow{GF}|}{|\vec{n}| |\overrightarrow{GF}|} = \frac{|12+0+8|}{\sqrt{36+9+64} \cdot \sqrt{4+1}} = \frac{20}{\sqrt{109} \cdot \sqrt{5}} = \frac{20}{\sqrt{545}}\)。

**第四步：求三棱锥 \(D-FBE\) 的体积**  
利用等体积法：\(V_{D-FBE} = V_{F-DBE}\)。  
底面 \(\triangle DBE\) 中，\(D(0,0,0), B(4,4,0), E(2,0,4)\)，  
面积 \(S_{\triangle DBE} = \frac12 |\overrightarrow{DB} \times \overrightarrow{DE}| = \frac12 |(4,4,0) \times (2,0,4)| = \frac12 |(16,-16,-8)| = \frac12 \sqrt{256+256+64} = \frac12 \cdot 24 = 12\)。  
高为点 \(F\) 到平面 \(DBE\) 的距离。平面 \(DBE\) 法向量 \(\vec{m} = (2,-2,-1)\)，点 \(F(2,4,4)\)，  
距离 \(h = \frac{|2\cdot2 + (-2)\cdot4 + (-1)\cdot4|}{\sqrt{4+4+1}} = \frac{|4-8-4|}{3} = \frac{8}{3}\)。  
体积 \(V = \frac13 \cdot 12 \cdot \frac{8}{3} = \frac{32}{3}\)。

**关键突破口**：  
- 利用比例关系准确求出点 \(G\) 坐标。  
- 用向量法证明线面垂直时，只需验证直线方向向量与平面内两条不共线向量垂直。  
- 求二面角时，注意法向量的方向选择，余弦值取绝对值。  
- 三棱锥体积常用等体积法转换顶点，简化计算。

## 完整解析

## 完整解题过程

### 第一步：建立空间直角坐标系并求各点坐标

以 \(D\) 为原点，\(DA\) 为 \(x\) 轴，\(DC\) 为 \(y\) 轴，\(DD_1\) 为 \(z\) 轴，建立空间直角坐标系。

正方体棱长为 4，各顶点坐标：
\[
\begin{aligned}
&A(4,0,0),\quad B(4,4,0),\quad C(0,4,0),\quad D(0,0,0),\\
&A_1(4,0,4),\quad B_1(4,4,4),\quad C_1(0,4,4),\quad D_1(0,0,4)
\end{aligned}
\]

\(E\) 为 \(A_1D_1\) 中点，\(F\) 为 \(C_1B_1\) 中点：
\[
E\left(\frac{4+0}{2},\frac{0+0}{2},\frac{4+4}{2}\right) = (2,0,4),\quad 
F\left(\frac{0+4}{2},\frac{4+4}{2},\frac{4+4}{2}\right) = (2,4,4)
\]

由 \(CG = 3C_1G\)，设 \(G(0,4,z)\)，则 \(CG = |z-0| = z\)，\(C_1G = |4-z|\)，
\[
z = 3(4-z) \Rightarrow z = 12 - 3z \Rightarrow 4z = 12 \Rightarrow z = 3
\]
故 \(G(0,4,3)\)。

### 第二步：证明 \(GF \perp\) 平面 \(EBF\)

计算向量：
\[
\overrightarrow{GF} = (2-0, 4-4, 4-3) = (2,0,1)
\]
\[
\overrightarrow{EB} = (4-2, 4-0, 0-4) = (2,4,-4)
\]
\[
\overrightarrow{EF} = (2-2, 4-0, 4-4) = (0,4,0)
\]

验证垂直：
\[
\overrightarrow{GF} \cdot \overrightarrow{EB} = 2\times2 + 0\times4 + 1\times(-4) = 4 + 0 - 4 = 0
\]
\[
\overrightarrow{GF} \cdot \overrightarrow{EF} = 2\times0 + 0\times4 + 1\times0 = 0
\]

所以 \(GF \perp EB\) 且 \(GF \perp EF\)，又 \(EB\) 与 \(EF\) 相交于 \(E\)，故 \(GF \perp\) 平面 \(EBF\)。

### 第三步：求平面 \(FBE\) 与平面 \(EBG\) 夹角的余弦值

由第二步知，平面 \(FBE\) 的一个法向量为 \(\vec{n}_1 = \overrightarrow{GF} = (2,0,1)\)。

求平面 \(EBG\) 的法向量：
\[
\overrightarrow{EB} = (2,4,-4),\quad \overrightarrow{EG} = (0-2, 4-0, 3-4) = (-2,4,-1)
\]
\[
\vec{n}_2 = \overrightarrow{EB} \times \overrightarrow{EG} = 
\begin{vmatrix}
\mathbf{i} & \mathbf{j} & \mathbf{k} \\
2 & 4 & -4 \\
-2 & 4 & -1
\end{vmatrix}
= \mathbf{i}(4\times(-1) - (-4)\times4) - \mathbf{j}(2\times(-1) - (-4)\times(-2)) + \mathbf{k}(2\times4 - 4\times(-2))
\]
\[
= \mathbf{i}(-4 + 16) - \mathbf{j}(-2 - 8) + \mathbf{k}(8 + 8) = (12, 10, 16)
\]

化简为 \((6,5,8)\)。

两平面夹角余弦值：
\[
\cos\theta = \frac{|\vec{n}_1 \cdot \vec{n}_2|}{|\vec{n}_1| \cdot |\vec{n}_2|} = \frac{|2\times6 + 0\times5 + 1\times8|}{\sqrt{2^2+0^2+1^2} \cdot \sqrt{6^2+5^2+8^2}} = \frac{|12+0+8|}{\sqrt{5} \cdot \sqrt{36+25+64}} = \frac{20}{\sqrt{5} \cdot \sqrt{125}} = \frac{20}{\sqrt{625}} = \frac{20}{25} = \frac{4}{5}
\]

### 第四步：求三棱锥 \(D-FBE\) 的体积

利用等体积法：\(V_{D-FBE} = V_{F-DBE}\)。

计算底面 \(\triangle DBE\) 的面积：
\[
\overrightarrow{DB} = (4,4,0),\quad \overrightarrow{DE} = (2,0,4)
\]
\[
\overrightarrow{DB} \times \overrightarrow{DE} = 
\begin{vmatrix}
\mathbf{i} & \mathbf{j} & \mathbf{k} \\
4 & 4 & 0 \\
2 & 0 & 4
\end{vmatrix}
= \mathbf{i}(4\times4 - 0\times0) - \mathbf{j}(4\times4 - 0\times2) + \mathbf{k}(4\times0 - 4\times2)
\]
\[
= (16, -16, -8)
\]
\[
S_{\triangle DBE} = \frac12 |(16, -16, -8)| = \frac12 \sqrt{16^2 + (-16)^2 + (-8)^2} = \frac12 \sqrt{256 + 256 + 64} = \frac12 \times 24 = 12
\]

求点 \(F\) 到平面 \(DBE\) 的距离。平面 \(DBE\) 的法向量 \(\vec{m} = (16, -16, -8)\)，化简为 \((2, -2, -1)\)。

平面 \(DBE\) 过点 \(D(0,0,0)\)，方程为 \(2x - 2y - z = 0\)。

点 \(F(2,4,4)\) 到平面的距离：
\[
h = \frac{|2\times2 + (-2)\times4 + (-1)\times4|}{\sqrt{2^2 + (-2)^2 + (-1)^2}} = \frac{|4 - 8 - 4|}{\sqrt{4+4+1}} = \frac{8}{3}
\]

三棱锥体积：
\[
V = \frac13 \times S_{\triangle DBE} \times h = \frac13 \times 12 \times \frac{8}{3} = \frac{32}{3}
\]

### 辅助图形

```latex
\begin{tikzpicture}[scale=1.2]
  % 正方体底面
  \draw[thick] (0,0) -- (4,0) -- (4,4) -- (0,4) -- cycle;
  % 正方体顶面
  \draw[thick] (0,0) ++(0,4) -- ++(4,0) -- ++(0,4) -- ++(-4,0) -- cycle;
  % 侧棱
  \draw[thick] (0,0) -- (0,4);
  \draw[thick] (4,0) -- (4,4);
  \draw[thick] (4,4) -- (4,8);
  \draw[thick] (0,4) -- (0,8);
  % 标注顶点
  \node[below left] at (0,0) {$D$};
  \node[below right] at (4,0) {$C$};
  \node[above right] at (4,4) {$B$};
  \node[above left] at (0,4) {$A$};
  \node[above left] at (0,8) {$A_1$};
  \node[above right] at (4,8) {$B_1$};
  \node[below right] at (4,4) {$C_1$};
  \node[below left] at (0,4) {$D_1$};
  % 标注中点 E, F
  \filldraw[red] (0,6) circle (2pt) node[left] {$E$};
  \filldraw[red] (4,6) circle (2pt) node[right] {$F$};
  % 标注点 G
  \filldraw[blue] (4,3) circle (2pt) node[right] {$G$};
  % 连接线
  \draw[dashed, blue] (4,6) -- (4,3);
  \draw[dashed, red] (0,6) -- (4,4);
  \draw[dashed, red] (0,6) -- (4,6);
  \draw[dashed, blue] (4,3) -- (0,6);
\end{tikzpicture}
```

---

**学科：**数学  
**知识点：**立体几何、空间向量、线面垂直、二面角、三棱锥体积  
**题目难度：**较难

## 思维导图

核心概念：立体几何综合问题（线面垂直、二面角、体积）
├── 第一步：建立空间直角坐标系
│   ├── 确定原点：以D为原点，DA为x轴，DC为y轴，DD₁为z轴
│   ├── 求各顶点坐标：正方体棱长4，写出A、B、C、D、A₁、B₁、C₁、D₁坐标
│   ├── 求中点E、F坐标：E为A₁D₁中点→(2,0,4)，F为C₁B₁中点→(2,4,4)
│   └── 求点G坐标：由CG=3C₁G，设G(0,4,z)，解方程得z=3→G(0,4,3)
├── 第二步：证明GF⊥平面EBF
│   ├── 计算向量：GF=(2,0,1)，EB=(2,4,-4)，EF=(0,4,0)
│   ├── 验证垂直：GF·EB=0，GF·EF=0
│   └── 得出结论：GF垂直于平面内两条相交直线EB和EF，故GF⊥平面EBF
├── 第三步：求平面FBE与平面EBG夹角的余弦值
│   ├── 求平面FBE法向量：即GF=(2,0,1)
│   ├── 求平面EBG法向量：计算EB×EG=(12,10,16)，化简为(6,5,8)
│   └── 计算余弦值：cosθ=|n₁·n₂|/(|n₁|·|n₂|)=20/(√5·√125)=4/5
└── 第四步：求三棱锥D-FBE的体积
    ├── 等体积法：V(D-FBE)=V(F-DBE)
    ├── 求底面△DBE面积：计算DB×DE=(16,-16,-8)，面积=12
    ├── 求点F到平面DBE的距离：平面法向量(2,-2,-1)，距离h=8/3
    └── 计算体积：V=1/3×12×8/3=32/3

