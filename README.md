# Memory Hub

Memory Hub 是本地优先的跨 Agent 工作记忆检索器。它只读取你配置的本地来源，索引保存在 `%LOCALAPPDATA%\MemoryHub`，默认服务只监听 `127.0.0.1`。默认搜索不需要模型或 API key。

## Windows 源码安装

在 PowerShell 中进入项目目录（下面的路径只是示例，请替换成你的实际 clone 目录）：

```powershell
cd 'C:\path\to\memory-hub'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[desktop,test]'
```

复制 `config.example.json` 到 `%LOCALAPPDATA%\MemoryHub\config.json`，按 [配置说明](docs/configuration.md) 填写来源目录和可选模型。最小配置可以只保留默认值；首次启动后执行：

```powershell
.\.venv\Scripts\python.exe -m memory_hub.cli sync
.\.venv\Scripts\python.exe -m memory_hub.cli serve
```

也可以使用安装后的入口：

```powershell
.\.venv\Scripts\memory.exe sync
.\.venv\Scripts\memory.exe serve
```

打开 `http://127.0.0.1:17888`。端口和绑定地址以配置文件为准；不要把示例地址当成远程服务承诺。

## 来源与隐私边界

来源连接器是只读的。默认会跳过密钥、认证文件、浏览器缓存、二进制文件和大文件。缺失或未配置的来源会显示为未配置，不代表它已被导入。Gemini 和元宝网页导出需要先由对应导出器生成本地文件，再通过 `web_export_roots` 配置导出根目录；参见 [来源接入](docs/sources.md)。

本地 FTS 搜索不会外发内容。启用 embedding、重排或问答后，召回文本可能发送到你配置的端点；默认会尽力遮蔽明显的 key、token 和 password，但这不是任意敏感信息的保证。`full_memory_mode` 默认为 `false`，只有你明确改为 `true` 时才允许模型接收未脱敏的召回内容。模型服务的隐私、保留和计费规则由你选择的提供商决定。

HTTP API 默认只接受本机连接。需要在受控网络中使用时，可以配置 `api_token_env` 指向一个环境变量名来启用 Bearer 认证；留空表示关闭认证。认证不会改变 host/port 绑定，公开绑定前请先设置网络层访问控制。

## 常用命令

```powershell
.\.venv\Scripts\memory.exe search '关键词'
.\.venv\Scripts\memory.exe continue '旧任务线索'
.\.venv\Scripts\memory.exe sync
.\.venv\Scripts\memory.exe embed --limit 100
.\.venv\Scripts\python.exe -m memory_hub.mcp
```

`memory_hub.mcp` 是标准输入/输出的只读 MCP 服务，可作为任意 MCP 客户端的 command。三个薄接入说明位于 `skills/`；复制给 Agent 的提示词见 [PROMPT-给其他AI.md](PROMPT-给其他AI.md)，其中的服务地址会从 `/api/agent-prompt` 动态生成，避免复制过时端口或完整记忆模式状态。

## 桌面入口

Windows 桌面控制窗是可选组件。安装 `.[desktop,test]` 后可按 [桌面控制窗说明](desktop-control/README.md) 构建或运行；它读取当前用户的 Memory Hub 配置，不依赖固定的项目路径。源码脚本仍可直接使用，桌面入口不是独立服务器发行包。

## 维护

请先读 [维护说明](docs/maintenance.md)。数据库、日志、导出原件和迁移备份属于本地运行数据，不应提交到公共仓库；`.gitignore` 只提供防误提交保护，发布前仍需检查最终文件清单和 Git 历史。项目按 MIT 许可证发布，详见 [LICENSE](LICENSE)。
