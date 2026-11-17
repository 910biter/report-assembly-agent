# 报告整编智能体

Report Assembly Agent 是一个面向多来源材料的报告工作台。它将文档解析、事实提取、分析规划、报告撰写和审阅串联起来，并保留正文到材料的溯源关系。用户可以在需求和目录阶段与助手协作，也可以对生成的报告进行编辑、版本对比和增量更新。

## 核心能力

- **材料理解与证据溯源**：解析上传材料，提取事实及其来源，供分析、写作和人工核验使用。
- **协作规划与报告生成**：支持自动生成，也支持用户参与需求和目录确认；报告按计划组织事实与推断。
- **模板学习**：从参考材料学习文体和结构；有适用 Word 母版时可用于版式输出。
- **报告工作区**：支持正文编辑、证据查看、版本审阅、增量更新和 Word 导出。
- **材料库与资料解析**：管理和复用材料，通过助手讨论材料内容；知识图谱按用户操作构建。
- **多用户与队列**：管理员创建账号，用户使用各自的材料和任务；任务通过持久队列调度。

## 工作流程

```text
材料上传与解析 → 材料理解 → 事实与证据提取 → 分析推断
    → 最终规划与目录 → 分单元撰写 → 质量检查 → 报告审阅与导出
```

协作规划可在需求和目录检查点暂停，由用户与助手讨论后继续。模板主要提供表达、结构或版式参考；报告中的业务事实以任务材料及其证据为依据。具体阶段、失败处理和版本约定以[产品契约](docs/CURRENT_PRODUCT_CONTRACT.md)和代码实现为准。

## 运行架构

| 组件 | 职责 |
| --- | --- |
| Vue 3 + TypeScript | 工作台、任务与报告界面 |
| FastAPI + Python | API、任务调度、各阶段智能体与报告处理 |
| PostgreSQL | 任务、材料、证据、报告、账号和配置等业务数据 |
| Docling / OCR / Whisper | 文档解析、图文识别和音频转写 |
| 本地 Embedding + Qdrant | 向量索引与检索；向量不可用时部分检索路径可回退词法检索 |
| 独立模型服务 | 报告生成和分析；通过管理员设置接入 vLLM 或兼容 API |

模型推理服务与应用独立部署。应用容器和本地解析、Embedding 默认使用 CPU；自行部署 vLLM 所需的 GPU 和驱动由模型服务环境提供。

## 部署与启动

### 离线安装包

项目提供 Linux Docker 离线交付和 Windows 原生安装的构建脚本。Linux 安装包需要目标机已有可用的 Docker Engine 和 Compose；镜像包含应用运行环境及相关模型资源，不包含生成式推理模型。首次启动后，管理员需完成登录、离线授权和模型接入配置。

- Linux 镜像、安装包构建与数据持久化：[Docker 离线部署](deploy/docker/README.md)
- Windows 原生安装与构建：[Windows 部署说明](deploy/windows/README.md)
- 源码部署与依赖：[部署指南](deploy/DEPLOY.md)

安装包、模型权重、客户数据和签发私钥不属于源码仓库。升级或迁移前应备份数据库、上传材料、运行数据和授权状态；卸载应用不应替代备份。

### 源码开发

以下是源码开发的基本入口；PostgreSQL、Qdrant、解析与 Embedding 所需模型需事先准备。环境变量及完整依赖说明见[部署指南](deploy/DEPLOY.md)。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp deploy/env.example .env
```

按实际环境调整 `.env` 中的数据库、Qdrant 和本地模型路径，准备原生授权组件后启动：

```bash
.venv/bin/pip install -r tools/license-build-requirements.txt
.venv/bin/python tools/build_license_native.py --output .native-build
cp .native-build/app/licensing/_trust*.so app/licensing/
.venv/bin/python run.py
```

应用默认监听端口 `8000`，可通过 `IRA_HOST` 和 `IRA_PORT` 调整。启动后可检查应用进程：

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

前端独立开发与构建：

```bash
cd frontend
npm ci
npm run dev
# 或运行 npm run build 生成前端构建产物
```

应用启动不代表模型或所有依赖均已就绪；使用前还需检查数据库、向量服务、授权与模型接入。面向公网部署时应配置 HTTPS 和适当的访问控制；内网 HTTP 测试设置不适用于公网。

## 模型接入与上下文

管理员在系统设置中配置模型服务地址、模型名称、密钥和必要的容量信息。vLLM 可通过 `/tokenize` 提供请求计数和上下文窗口信息；兼容服务未提供该接口时使用保守估算，不要求用户提供 Tokenizer。服务返回的实际使用量可用于校准估算。

系统按阶段组织输入并预留输出空间。Writer 默认沿用常规额度，必要时可扩展输入，最终根据请求实际计量及安全余量调整输出；超出模型容量或无法装入必要证据时，需明确处理而不是把未处理内容当作事实缺失。各阶段具体预算与实现见 `app/runtime_profiles.py`、`app/vllm_capacity.py` 和[模型运行约定](docs/MODEL_RUNTIME_ARCHITECTURE.md)。

## 离线授权

Windows 和 Linux 使用离线许可证协议。管理员登录后在授权页面导入许可证；未授权、到期或检测到时间异常时，主要业务功能受限，普通用户看到联系管理员的提示。授权绑定安装身份和设备信息；升级时应保留 `runtime/license` 及相关持久数据。

内部签发工具是 `tools/license_issuer.py`，不应随客户安装包分发。私钥、密码、客户申请和签发台账须存放在受限位置，不能提交 Git：

```bash
# 内部环境：创建加密签发密钥及公钥映射
python tools/license_issuer.py keygen \
  --key /secure/issuer/issuer.private.pem --output /secure/issuer/public.json

# 根据管理员导出的安装申请签发；密码通过交互输入
python tools/license_issuer.py issue \
  --key /secure/issuer/issuer.private.pem --request request.txt \
  --generation 1 --months 12 --customer customer-name --output customer.license
```

签发期限支持 3、6、12、36、60、120 个月，默认 3 个月；续期需使用新许可证及更高代次。公钥编译在原生信任组件中，发布前需按签发密钥更新并构建；私钥不进入应用。时间回退恢复、密钥轮换及平台差异应依据工具帮助和部署说明操作。

离线软件保护可提高伪造和跨安装复制的难度，但不能防止拥有机器管理员权限的攻击者修改程序、克隆系统或回滚完整快照，也不提供可信时间或远程吊销。

## 代码导航

| 路径 | 内容 |
| --- | --- |
| `app/api/` | API 与页面服务 |
| `app/workflow/` | 任务阶段、检查点、队列和工作区 |
| `app/parsing/`、`app/evidence/`、`app/analysis/` | 材料解析、证据提取与分析 |
| `app/planning/`、`app/writing/` | 报告规划、叙事计划与正文生成 |
| `app/retrieval/`、`app/graph/` | 检索与关系网络 |
| `app/memory/`、`app/template_engine/` | 材料、风格与模板相关能力 |
| `app/licensing/`、`app/auth/` | 离线授权与账号访问控制 |
| `frontend/`、`web/spa/` | Vue 前端源码与构建产物 |
| `deploy/`、`tools/`、`tests/` | 部署脚本、内部工具和测试 |

更多内容：[代码地图](docs/CODEBASE_MAP.md) · [产品契约](docs/CURRENT_PRODUCT_CONTRACT.md) · [模型接入](docs/model-connection.md) · [模板学习](docs/template-style-learning.md) · [vLLM 部署](docs/VLLM_DEPLOYMENT.md)
