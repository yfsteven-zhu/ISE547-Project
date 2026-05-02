# PRD: "Chat with Your Data Application"

**Version**: v0.2
**Date**: 2026-03-29
**Deployment Target**: Local

---

## 1. Product Overview

### 1.1 Background & Goals
Build a locally-run web application that allows users to upload CSV files and ask data analysis questions in natural language. The system leverages AI to perform analysis and return results as textual conclusions combined with visual charts.

The product is delivered in two phases:
- **Phase 1 (MVP)**: Core data analysis features for individual users
- **Phase 2**: Multi-user collaboration system (Organizations + Projects)

### 1.2 Product Vision
> "Let anyone get insights from CSV data as if talking to a data expert — and share those analytical assets across a team."

---

## 2. User Personas

| Role | Description | Core Need |
|------|-------------|-----------|
| Individual Analyst | Independently analyzes business/research data | Quickly discover patterns across multiple CSVs without writing code |
| Team Lead | Manages multiple analysis projects | Create projects, invite members, share datasets |
| Team Member | Asks questions and views results within a project | Quickly get insights based on the team's historical data |

---

## 3. Phase 1: Core Features (MVP)

### 3.1 CSV File Management (P0)
- Support uploading **multiple** CSV files (single or batch)
- File size limit: ≤ 50MB per file, ≤ 200MB total
- After upload, display a file list showing per file:
  - Filename, row count, column count, upload time
  - Data preview (first 10 rows)
  - Column info: name, inferred data type, non-null rate, sample values
- Support deleting individual files
- Support adding a text description to each file (helps AI understand data semantics)
- Automatic encoding detection (UTF-8 / GBK and other common encodings)

### 3.2 Historical File Analysis (P0)
- All uploaded CSV files are persisted locally (survive page refresh)
- Users can select **one or more** files from the file list to include in the current analysis
- Support cross-file joint analysis (e.g., "Compare sales trends between 2024.csv and 2025.csv")
- File selection state persists throughout the conversation session

### 3.3 Natural Language Q&A (P0)
- Text input box with multi-turn conversation support (conversation history retained)
- On each query, the system automatically injects the **currently selected files'** schema + sample data into the AI context
- Provide suggested questions dynamically generated based on uploaded file column names
- Support Enter to send, Shift+Enter for newline
- Support clearing the conversation and starting over

### 3.4 AI Data Analysis (P0)
Analysis pipeline:
1. Assemble prompt: conversation history + schema of selected files + data samples + user question
2. Call Gemini API (streaming)
3. AI decision:
   - If directly inferrable → return textual conclusion
   - If precise computation needed → generate pandas code → execute in backend sandbox → pass results back to AI → AI generates final conclusion
4. Stream response back to frontend

Supported analysis types (examples):
- Descriptive statistics (mean, median, distribution, missing values)
- Filtering & sorting ("Top 10 products by revenue")
- Trend analysis ("Growth rate over the past 6 months")
- Cross-file comparison ("Which user IDs appear in both files")
- Data quality suggestions ("What are the quality issues in this dataset")

### 3.5 Data Visualization (P0)
- AI generates chart configuration alongside textual conclusions
- Supported chart types:
  - Bar chart (categorical comparison)
  - Line chart (trend over time)
  - Pie / donut chart (proportion analysis)
  - Scatter plot (correlation analysis)
  - Heatmap (correlation matrix)
- Charts rendered inline with text conclusions in the conversation
- Support chart download (PNG format)
- Charts support hover tooltips for details

### 3.6 Result Display (P0)
- AI responses rendered as Markdown
- Numeric tables displayed as formatted, sortable tables
- Generated analysis code displayed in syntax-highlighted, collapsible code blocks
- Support copying response content

---

## 4. Phase 2: Multi-User Collaboration

### 4.1 User Authentication (P0)
- Email + password registration / login
- JWT token authentication, stored locally
- Support updating password and profile information
- No email verification required for local deployment

### 4.2 Organization System (P0)

**Organization**:
- Users can create one or more organizations; creator becomes "Owner" automatically
- Organization has a unique name and description
- Owner can invite other registered users and assign roles:
  - **Owner**: Full permissions (delete org, manage members, manage all projects)
  - **Admin**: Manage members, create/delete projects
  - **Member**: View authorized projects, upload files, run analysis queries

**Member Management**:
- Invitation: Owner/Admin searches for registered users by username and adds them directly to the organization; the invited user sees the new organization upon next page refresh (no notification or email required)
- Support removing members and changing roles
- Members can leave an organization

### 4.3 Project System (P0)

**Project**:
- Created under an organization, with name, description, and creation timestamp
- Each project has its own:
  - CSV file library (multiple files, persisted)
  - Conversation history
  - Member access permissions (configurable at project level)
- File reference: support referencing files from other projects to avoid re-uploading

**In-Project Analysis**:
- When asking questions in a project, users can choose the file scope:
  - Currently uploaded files
  - All historical files in the project
  - A specified subset of files
- AI context includes schema summaries of all selected files; sample data volume is dynamically adjusted within token limits
- Conversation history is organized into named **Sessions**

### 4.4 Permission Matrix

| Action | Owner | Admin | Member |
|--------|-------|-------|--------|
| Delete organization | ✓ | ✗ | ✗ |
| Manage members | ✓ | ✓ | ✗ |
| Create / delete projects | ✓ | ✓ | ✗ |
| Upload / delete files | ✓ | ✓ | ✓ (own files only) |
| View project files | ✓ | ✓ | ✓ |
| Ask analysis questions | ✓ | ✓ | ✓ |
| View other members' conversations | ✓ | ✓ | ✗ |

---

## 5. Non-Functional Requirements

| Metric | Phase 1 Target | Phase 2 Target |
|--------|---------------|---------------|
| File upload response | < 2s | < 2s |
| AI first-byte response (streaming) | < 3s | < 3s |
| Local concurrent users | 1–3 | 10–20 |
| Data persistence | SQLite + local filesystem | SQLite (extended schema) + local filesystem |
| Browser compatibility | Chrome / Edge latest | Chrome / Edge / Firefox latest |
| Mobile | Basic usable | Full responsive support |

---

## 6. Technical Architecture

### 6.1 Phase 1 Tech Stack

**Frontend**
- Framework: React 18 + Vite
- UI: Tailwind CSS + shadcn/ui
- Charts: Recharts (deep React ecosystem integration)
- Markdown rendering: react-markdown + remark-gfm

**Backend**
- Framework: Python FastAPI
- CSV processing: pandas
- AI integration: Google Gemini API (gemini-2.5-pro), Server-Sent Events streaming
- Code sandbox: restricted Python environment on the backend to safely execute AI-generated pandas code
- Persistence: SQLite (file metadata + conversation history) + local filesystem (raw CSV storage)

**Local Run**
- Backend: `uvicorn`, `localhost:8000`
- Frontend: `vite dev`, `localhost:5173`
- One-click startup scripts (`start.sh` / `start.bat`)

### 6.2 Phase 2 Additional Stack
- Database: SQLite (introduced in Phase 1, schema extended in Phase 2)
- ORM: SQLAlchemy
- Auth: JWT (python-jose) + bcrypt password hashing
- File storage: local filesystem organized by org/project directory hierarchy

### 6.3 Directory Structure (Phase 1)
```
project/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── routers/
│   │   ├── files.py         # File upload/management endpoints
│   │   └── chat.py          # Conversation/analysis endpoints
│   ├── services/
│   │   ├── csv_parser.py    # CSV parsing and schema extraction
│   │   ├── ai_service.py    # Gemini API integration
│   │   ├── code_executor.py # pandas code sandbox execution
│   │   └── chart_service.py # Chart data generation
│   ├── storage/             # Local file persistence
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload/  # File upload area
│   │   │   ├── FileList/    # File list with selection
│   │   │   ├── ChatPanel/   # Conversation area
│   │   │   ├── MessageItem/ # Single message (with inline chart)
│   │   │   └── DataPreview/ # Data preview table
│   │   ├── hooks/
│   │   ├── api/             # Backend API wrappers
│   │   └── App.tsx
│   └── package.json
├── start.sh
└── start.bat
```

---

## 7. Key User Flows

### Phase 1 Flow
1. Launch app → land on main interface
2. Upload one or more CSV files → file list appears on the left panel
3. Select files to include in analysis (all selected by default)
4. Type a question (or click a suggested question)
5. Wait for AI streaming response: textual conclusion + inline charts
6. Continue follow-up questions / switch file selection / upload new files

### Phase 2 Flow (additions)
1. Register / log in → enter personal workspace
2. Create an organization → add members by username
3. Create a project under the organization → upload CSV files
4. Start an analysis session in the project → multi-turn conversation, AI references all project historical files
5. Conversation history archived by session, reviewable at any time

---

## 8. Out of Scope

| Feature | Note |
|---------|------|
| Cloud deployment | Local only for both phases |
| Excel / JSON format support | CSV only |
| Custom AI model selection | Fixed to Gemini 2.5 Pro |
| PDF report export | Possible future extension |
| Real-time collaboration | Phase 2 shares data, no live co-editing |

---

## 9. Delivery Roadmap

| Phase | Key Deliverables | Milestone |
|-------|-----------------|-----------|
| **Phase 1 MVP** | File upload, multi-file selection, AI Q&A, chart visualization, local persistence | Full core analysis pipeline working end-to-end |
| **Phase 2 V2** | User auth, organization creation & membership, project management, historical file analysis within projects | Multi-user collaboration system live |

---
---

# PRD："Chat with Your Data Application"

**版本**：v0.2
**日期**：2026-03-29
**部署目标**：本地运行

---

## 一、产品概述

### 1.1 背景与目标
构建一款本地运行的 Web 应用，允许用户上传 CSV 文件并以自然语言提出数据分析问题，系统通过 AI 能力自动完成分析并以文字结论 + 可视化图表的形式返回结果。

产品分两个阶段交付：
- **第一阶段（MVP）**：面向个人用户的核心数据分析功能
- **第二阶段**：引入多用户协作体系（组织 + 项目机制）

### 1.2 产品愿景
> "让任何人都能像与数据专家对话一样，从 CSV 数据中获取洞察，并在团队中共享分析资产。"

---

## 二、用户画像

| 角色 | 描述 | 核心诉求 |
|------|------|---------|
| 个人分析师 | 独立分析业务/研究数据 | 快速从多个 CSV 中发现规律，无需写代码 |
| 团队负责人 | 管理多个分析项目 | 创建项目、邀请成员、共享数据集 |
| 普通团队成员 | 在项目中提问和查看分析结果 | 基于团队历史数据快速获取洞察 |

---

## 三、第一阶段：核心功能（MVP）

### 3.1 CSV 文件管理（P0）
- 支持上传**多个** CSV 文件（单次或批量上传）
- 文件大小限制：单文件 ≤ 50MB，总计 ≤ 200MB
- 上传后展示文件列表，每个文件显示：
  - 文件名、行数、列数、上传时间
  - 数据预览（前 10 行）
  - 列信息：列名、数据类型（自动推断）、非空率、示例值
- 支持删除单个文件
- 支持为文件添加备注描述（方便 AI 理解数据含义）
- 编码自动检测（UTF-8 / GBK 等常见编码）

### 3.2 历史文件分析（P0）
- 所有上传过的 CSV 文件持久化存储在本地（不随页面刷新丢失）
- 用户可在文件列表中选择**一个或多个**文件参与本次分析
- 支持跨文件联合分析（如："比较 2024.csv 和 2025.csv 中的销售趋势差异"）
- 文件选择状态在对话期间保持

### 3.3 自然语言问答（P0）
- 文本输入框，支持多轮对话（保留对话历史）
- 每次提问时，系统自动将**当前选中文件**的 schema + 样本数据注入到 AI 上下文
- 提供示例问题引导（根据已上传文件的列名动态生成建议问题）
- 支持 Enter 发送，Shift+Enter 换行
- 支持清空对话、重新开始

### 3.4 AI 数据分析（P0）
分析流程：
1. 组装 prompt：对话历史 + 选中文件的 schema + 数据样本 + 用户问题
2. 调用 Gemini API（流式输出）
3. AI 决策：
   - 若可直接推断 → 返回文字结论
   - 若需精确计算 → 生成 pandas 代码 → 后端沙箱执行 → 将执行结果回传给 AI → AI 生成最终结论
4. 流式返回给前端

支持的分析类型（示例）：
- 描述性统计（均值、中位数、分布、缺失值）
- 筛选与排序（"销售额前10的产品"）
- 趋势分析（"过去6个月的增长率"）
- 跨文件对比（"两份文件中共同出现的用户ID有哪些"）
- 数据清洗建议（"这份数据有哪些质量问题"）

### 3.5 可视化图表（P0）
- AI 在返回分析结论时，可同步生成图表配置
- 支持图表类型：
  - 柱状图（分类对比）
  - 折线图（趋势变化）
  - 饼图/环形图（占比分析）
  - 散点图（相关性分析）
  - 热力图（相关矩阵）
- 图表与文字结论内联展示在对话消息中
- 支持图表下载（PNG 格式）
- 图表可响应点击查看详情（tooltip）

### 3.6 结果展示（P0）
- AI 回复以 Markdown 渲染
- 数字表格格式化展示（支持排序）
- 生成的分析代码以代码块高亮展示（可折叠）
- 支持复制回答内容

---

## 四、第二阶段：多用户协作体系

### 4.1 用户认证（P0）
- 邮箱 + 密码注册/登录
- JWT Token 认证，本地存储
- 支持修改密码、个人信息
- 本地部署下无需邮件验证（可选）

### 4.2 组织机制（P0）

**组织（Organization）**：
- 用户可创建一个或多个组织，自动成为"Owner"
- 组织有唯一名称和描述
- Owner 可邀请其他已注册用户加入组织，分配角色：
  - **Owner**：完整权限（删除组织、管理成员、管理所有项目）
  - **Admin**：管理成员、创建/删除项目
  - **Member**：查看组织内被授权的项目、上传文件、提问分析

**成员管理**：
- 邀请方式：Owner/Admin 通过用户名搜索已注册用户并直接添加至组织，被邀请方刷新页面后即可看到新组织（无需通知或邮件）
- 支持移除成员、变更角色
- 成员退出组织

### 4.3 项目机制（P0）

**项目（Project）**：
- 在组织下创建，有名称、描述、创建时间
- 每个项目拥有独立的：
  - CSV 文件库（可上传多份文件，持久化存储）
  - 对话历史记录
  - 成员访问权限（可细化到项目级别）
- 项目文件继承：支持从其他项目"引用"已有文件（避免重复上传）

**项目内分析**：
- 用户在项目中提问时，可选择参与分析的文件范围：
  - 当前上传的文件
  - 项目历史文件（所有历史上传文件）
  - 指定文件组合
- AI 上下文包含项目内所有选中文件的 schema 摘要，在 token 限制内动态调整数据样本量
- 对话历史按"会话（Session）"组织，每个会话可命名

### 4.4 权限矩阵

| 操作 | Owner | Admin | Member |
|------|-------|-------|--------|
| 删除组织 | ✓ | ✗ | ✗ |
| 管理成员 | ✓ | ✓ | ✗ |
| 创建/删除项目 | ✓ | ✓ | ✗ |
| 上传/删除文件 | ✓ | ✓ | ✓（仅自己上传的）|
| 查看项目文件 | ✓ | ✓ | ✓ |
| 提问分析 | ✓ | ✓ | ✓ |
| 查看他人对话 | ✓ | ✓ | ✗ |

---

## 五、非功能需求

| 指标 | 第一阶段目标 | 第二阶段目标 |
|------|------------|------------|
| 文件上传响应 | < 2s | < 2s |
| AI 首字节响应（流式） | < 3s | < 3s |
| 本地并发用户 | 1-3 人 | 10-20 人 |
| 数据存储 | SQLite + 本地文件系统 | SQLite（扩展表结构）+ 本地文件系统 |
| 浏览器兼容 | Chrome / Edge 最新版 | Chrome / Edge / Firefox 最新版 |
| 移动端 | 基本可用 | 响应式完整支持 |

---

## 六、技术架构建议

### 6.1 第一阶段技术栈

**前端**
- 框架：React 18 + Vite
- UI：Tailwind CSS + shadcn/ui
- 图表：Recharts（与 React 生态深度集成）
- Markdown 渲染：react-markdown + remark-gfm

**后端**
- 框架：Python FastAPI
- CSV 处理：pandas
- AI 集成：Google Gemini API（gemini-2.5-pro），Server-Sent Events 流式输出
- 代码沙箱：后端受限 Python 环境执行 AI 生成的 pandas 代码
- 数据持久化：SQLite（文件元数据 + 对话历史）+ 本地文件系统（CSV 原文件存储）

**本地运行**
- 后端：`uvicorn` 启动，`localhost:8000`
- 前端：`vite dev`，`localhost:5173`
- 一键启动脚本（`start.sh` / `start.bat`）

### 6.2 第二阶段新增技术栈
- 数据库：SQLite（第一阶段已引入，第二阶段扩展表结构）
- ORM：SQLAlchemy
- 认证：JWT（python-jose）+ bcrypt 密码哈希
- 文件存储：本地文件系统按组织/项目目录组织

### 6.3 目录结构（第一阶段）
```
project/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── routers/
│   │   ├── files.py         # 文件上传/管理接口
│   │   └── chat.py          # 对话/分析接口
│   ├── services/
│   │   ├── csv_parser.py    # CSV 解析与 schema 提取
│   │   ├── ai_service.py    # Gemini API 调用
│   │   ├── code_executor.py # pandas 代码沙箱执行
│   │   └── chart_service.py # 图表数据生成
│   ├── storage/             # 本地文件持久化
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload/  # 文件上传区域
│   │   │   ├── FileList/    # 文件列表与选择
│   │   │   ├── ChatPanel/   # 对话区域
│   │   │   ├── MessageItem/ # 单条消息（含图表）
│   │   │   └── DataPreview/ # 数据预览表格
│   │   ├── hooks/
│   │   ├── api/             # 后端接口封装
│   │   └── App.tsx
│   └── package.json
├── start.sh
└── start.bat
```

---

## 七、核心用户流程

### 第一阶段流程
1. 启动应用 → 进入主界面
2. 上传一个或多个 CSV 文件 → 左侧文件列表展示
3. 选中参与分析的文件（默认全选）
4. 在输入框提问（或点击智能建议问题）
5. 等待 AI 流式输出：文字结论 + 可视化图表
6. 继续追问 / 切换文件组合 / 上传新文件

### 第二阶段流程（新增）
1. 注册/登录 → 进入个人工作台
2. 创建组织 → 通过用户名添加成员
3. 在组织下创建项目 → 上传 CSV 文件
4. 在项目中发起分析会话 → 多轮对话，AI 参考项目所有历史文件
5. 对话历史按会话归档，可随时回顾

---

## 八、超出范围

| 功能 | 说明 |
|------|------|
| 云端部署 | 仅本地运行 |
| 支持 Excel / JSON 格式 | 仅 CSV |
| 自定义 AI 模型选择 | 固定使用 Gemini 2.5 Pro |
| 报告 PDF 导出 | 可后续扩展 |
| 实时协作（多人同时编辑） | 第二阶段仅共享数据，不实时协作 |

---

## 九、开发阶段规划

| 阶段 | 核心交付 | 关键里程碑 |
|------|---------|----------|
| **第一阶段 MVP** | 文件上传、多文件选择、AI 问答、图表可视化、本地持久化 | 完整核心分析链路可用 |
| **第二阶段 V2** | 用户注册登录、组织创建与邀请、项目管理、项目内历史文件分析 | 多用户协作体系上线 |
