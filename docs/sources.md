# 来源接入

Memory Hub 的来源连接器只读本地数据，索引写入 %LOCALAPPDATA%\\MemoryHub。同步不会修改来源文件，也不会把未配置的来源当成已成功读取。

## 本地 Agent 来源

按本机软件的默认数据目录配置 project_roots 或对应来源设置。project_roots 为空表示不额外指定根目录，但 Agent 连接器仍会从本机发现的工作目录进行 filesystem 扫描。来源可能只提供会话标题、工作区和模型等元数据；界面标记 metadata_only 时，只能把它当作指针，不能据此声称已读取聊天正文。

## Gemini 与元宝

网页内容先由各自的浏览器导出器保存到本地，再在 web_export_roots 中填写导出器根目录。目录结构可以是：

~~~text
<export-root>\\browser-export-json-md\\json\\...
~~~

当前不支持 browser-export-json 目录。根目录为空时，程序会尝试兼容既有旧目录；首次未配置时来源显示为 not_configured。已配置但根目录缺失或不可读时显示为 error、partial 或 unavailable，并保留已有索引。导出原件可能含私人对话，不应提交到公共仓库、日志或截图中。

## 同步与失败处理

~~~powershell
.\.venv\Scripts\memory.exe sync
~~~

首次同步或目录较大时可通过 /api/status 查看状态。目录不存在、权限不足、外置盘断开和空目录具有不同含义；在确认来源可读前，不要把“未读到文件”解释为用户删除了全部记录。需要修复来源路径时，先修复配置再重新同步。
