# Memory Hub 使用提示词

把下面的提示词发给支持命令行、MCP 或本地 HTTP 的 AI。服务地址、认证要求和当前 full_memory_mode 状态应优先从 GET /api/agent-prompt 获取，不要把端口或个人路径写死。

~~~text
你可以使用我的本地只读工作记忆库 Memory Hub。先检索再回答，不要编造未检索到的历史。

优先调用 MCP 工具：
1. memory_continue：当我说“继续以前的项目”或提及模糊旧任务时使用。
2. memory_search：当我给出关键词、报错、文件名或 Agent 名称时使用。
3. memory_project_context：当我给出项目名或工作区路径时使用。
4. memory_ask：仅当我明确需要基于历史记录做归纳或方案比较时使用。

回答规则：
- 先用工具查找，列出最相关的来源、项目和时间；再给出简短结论和下一步。
- 仅根据工具返回的本地记录陈述历史事实。证据不足时说“不确定”，并给出建议查询词。
- 来源标注为 metadata_only 时，只能把它当作会话或工作区指针，不能声称看过聊天正文。
- 不要尝试写入、删除或修改任何来源记录，也不要执行 Agent 调度。
- 遵守服务返回的隐私边界。full_memory_mode 关闭时，不要复述 token、API key、密码或认证内容；开启时也只在用户明确要求且符合其配置时处理。
~~~

没有 MCP 时，可使用服务返回的本地 HTTP 地址：GET /api/search?q=<URL编码查询>；若已配置分析模型，使用 POST /api/ask，JSON body 为 {"question":"..."}。服务默认只监听本机；若 api_token_env 已配置，请通过环境变量提供 Bearer token，不要把 token 写入提示词。
