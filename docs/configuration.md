# 配置

运行时配置文件位于 %LOCALAPPDATA%\\MemoryHub\\config.json。从 config.example.json 开始，并把路径替换为本机实际路径；不要把个人路径、用户名、日期目录或 API key 提交到仓库。

## 基础设置

host、port 控制服务监听地址和端口。默认值是 127.0.0.1 和 17888。project_roots 是额外指定的本地项目根目录列表；设为空表示不额外指定根目录，但 Agent 连接器仍会从本机发现的工作目录进行 filesystem 扫描。full_memory_mode 默认是 false，保持关闭时，外发模型请求会先做尽力脱敏。

可选的 api_token_env 是环境变量名，例如 MEMORY_HUB_API_TOKEN。设置后，HTTP API 要求 Authorization: Bearer <该环境变量的值>；留空则不启用 Bearer 认证。环境变量名可以提交，实际 token 只能存在于本机环境变量或密钥管理器中。

## Gemini 和元宝导出

web_export_roots 是一个字典，键为 gemini 和 yuanbao，值为对应网页导出器的根目录。例如：

~~~json
{
  "web_export_roots": {
    "gemini": "D:\\Data\\AI-exports\\gemini",
    "yuanbao": "D:\\Data\\AI-exports\\yuanbao"
  }
}
~~~

当前支持的结构是 <root>/browser-export-json-md/json。browser-export-json 目录不属于当前支持的结构。空值会自动兼容已有的本地旧目录；首次未配置时来源显示为 not_configured。已配置但根目录缺失或不可读时，来源显示为 error、partial 或 unavailable，并保留已有索引数据。

## 可选模型

embedding、reranker 和 ai 各自通过 enabled 开关启用，使用 OpenAI 兼容端点。对应的 api_key_env 只填写环境变量名。PowerShell 示例：

~~~powershell
[Environment]::SetEnvironmentVariable('MEMORY_HUB_CHAT_KEY', 'your-key', 'User')
[Environment]::SetEnvironmentVariable('MEMORY_HUB_EMBEDDING_KEY', 'your-key', 'User')
~~~

重启服务后，embedding 或问答请求才会读取新的环境变量。模型外发是可选行为；未启用模型时，搜索仍使用本地 FTS。
