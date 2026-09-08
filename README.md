# Memory Hub 🪄

**只剩一点印象，也有路走回去。**

有灵感时做了一堆成果，放下几天后，却忘了文件在哪、和哪个 AI 聊过、上次做到哪一步。对有 ADHD、点子多又容易中断项目的人来说，光找回进度就可能耗掉重新开始的力气。

Memory Hub 是一个**本地优先的跨 Agent 工作记忆检索工具**：从关键词或旧任务线索出发，找回散落的记录与出处，交给现在使用的 Agent 继续梳理。像乱房间里的一根「召回魔法棒」，让重新开始轻一点。

[开始使用](#开始使用) · [技术架构](#技术架构) · [Agent 接入](#让-agent-接着帮你) · [配置说明](docs/configuration.md)

## 能做什么

- **找回旧任务：** 搜索本地 AI 工作记录、导出的聊天和项目文本，不必先手动整理所有资料。
- **跨 Agent 续接：** 通过 HTTP、MCP 或命令行查询历史，减少重复交代前情。
- **核对出处：** 保留来源和项目位置；仅有元数据的记录标为 `metadata_only`，不冒充完整聊天内容。
- **按需增强：** 基础关键词搜索无需模型或 API key，可选向量检索、重排和带来源的 AI 问答。

例如，向已接入的 Agent 说：

> “继续之前那个网页收藏整理工具。先查历史，告诉我做到哪了，再帮我选一个今天能做的小步骤。”

实际能找回的内容取决于已接入、已同步的记录；后续行动由你和 Agent 决定。

## 技术架构

核心是 **Python + FastAPI + SQLite**，前端由本地服务提供，无需单独部署向量数据库。

```text
本地 Agent 记录 / 网页聊天导出 / 项目文本
                   ↓ 只读同步
             SQLite 本地索引
                   ↓
     FTS5 关键词搜索 + 可选向量检索
                   ↓
       可选重排 → 结果与来源 / AI 问答
                   ↓
          浏览器界面 / HTTP / MCP
```

- **采集与存储：** 来源连接器读取本地资料，将文本、项目及来源信息写入 SQLite，保留原始文件。
- **关键词搜索：** 使用 SQLite FTS5 全文索引和 BM25 排序，基础检索完全在本地运行；CLI 的 `search` / `continue` 使用这条路径。
- **向量检索（可选）：** 调用配置的 embedding 端点生成向量，存回 SQLite；查询时在本地计算余弦相似度，在增强检索流程中合并关键词与语义结果。按内容哈希识别需要更新的向量。
- **重排与问答（可选）：** 对召回结果调用重排服务，或把相关记录交给模型生成带来源的回答。模型能力通过配置接入。

## 让 Agent 接着帮你

启动服务后，将 [接入提示词](PROMPT-给其他AI.md) 交给能访问这台电脑、支持 **HTTP、MCP 或命令行**的 Agent。提示词约定先检索、核对出处，再回答；当前服务接入信息可从 `/api/agent-prompt` 获取。

MCP 客户端使用虚拟环境中的 Python，参数为 `-m memory_hub.mcp`。详细说明见 [接入文档](PROMPT-给其他AI.md) 和 [skills/](skills/)。

## 开始使用

目前提供源码安装，以下步骤面向 **Windows + Python 3.11 或更新版本**。

### 1. 下载并安装

在 PowerShell 中运行：

```powershell
git clone https://github.com/Dumpling-Dragon/memory-hub.git
cd memory-hub
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### 2. 选择要找回的记录

将 [config.example.json](config.example.json) 复制到 `%LOCALAPPDATA%\MemoryHub\config.json`，按照 [配置说明](docs/configuration.md) 填写来源路径。目标目录不存在时，请先创建；已有配置则直接编辑。

可以先保持模型相关开关关闭。`project_roots` 可添加项目目录；本地 Agent 连接器也会尝试发现已有工作目录。

Gemini、元宝网页聊天需要先导出到本地，再配置 `web_export_roots`，不会直接读取网页账号。来源格式和限制见 [来源接入](docs/sources.md)。

### 3. 同步，开始搜索

```powershell
.\.venv\Scripts\memory.exe sync
.\.venv\Scripts\memory.exe serve
```

浏览器打开 <http://127.0.0.1:17888>（默认地址；修改过端口时以配置为准）。

**试着搜一个你还记得的旧项目关键词。先找回一件东西，就够了。**

<details>
<summary>更多命令与可选桌面入口</summary>

```powershell
.\.venv\Scripts\memory.exe search '关键词'
.\.venv\Scripts\memory.exe continue '旧任务线索'
.\.venv\Scripts\memory.exe sync
.\.venv\Scripts\memory.exe embed --limit 100
.\.venv\Scripts\python.exe -m memory_hub.mcp
```

`continue` 是旧任务线索的检索入口，后续行动由你或接入的 Agent 决定。`embed` 需要先配置并启用 embedding 模型。

Windows 桌面控制窗是可选组件。安装 `.[desktop,test]` 后，按 [桌面控制窗说明](desktop-control/README.md) 构建或运行。桌面入口仍依赖本地服务，不是独立服务器发行包。

</details>

## 你的记录会去哪里？

Memory Hub 使用本地 SQLite 索引，运行数据保存在 `%LOCALAPPDATA%\MemoryHub`。来源连接器只读原文件，默认服务监听 `127.0.0.1`。

- **基础关键词搜索：** 在本地完成，不调用模型。
- **可选模型功能：** 开启 embedding、重排或 AI 问答后，相关文本可能发送到你配置的模型端点。隐私、保留和计费规则取决于提供商。
- **脱敏：** 默认尽力遮蔽明显的 key、token 和 password，但不能保证识别所有敏感信息。`full_memory_mode` 默认关闭；明确开启后才允许模型接收未脱敏的召回内容。
- **来源范围：** 默认跳过密钥、认证文件、浏览器缓存、二进制和大文件。未配置或不可用的来源会显示状态，不会假装已经导入。

需要在受控网络中使用时，可通过 `api_token_env` 配置 Bearer 认证。认证与监听地址分别配置，公开绑定前需设置网络访问控制。详见 [配置说明](docs/configuration.md)。

## 一起让「重新开始」轻一点

如果你也遇到过“明明做过，却找不回来”的时刻，欢迎在 [Issues](https://github.com/Dumpling-Dragon/memory-hub/issues) 留下一个具体场景：你记得什么、想找回什么、卡在了哪里。请使用虚构或脱敏示例。

开发和维护请看 [维护说明](docs/maintenance.md)。数据库、日志、导出原件和迁移备份属于本地运行数据，不应提交到公共仓库。项目采用 [MIT 许可证](LICENSE)。

---

**愿下一次灵感来的时候，你能把力气花在继续做上。**
