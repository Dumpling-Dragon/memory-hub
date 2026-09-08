# Memory Hub 桌面控制窗

桌面控制窗是可选的 Windows 入口，用于连接或启动当前用户配置的 Memory Hub 服务。它不是独立服务器发行包；请先按根目录 README 完成 pip install -e '.[desktop]'。

## 运行

构建后的控制窗会从 %LOCALAPPDATA%\\MemoryHub\\config.json 读取 host 和 port，并启动当前项目环境中的 memory_hub.service_entry。因此它不依赖固定的项目路径、用户名、日期目录或当前工作目录。关闭窗口不会停止已运行的后端。

窗口显示服务状态、向量覆盖统计并提供打开网页版入口。统计来自 /api/status，不会直接读写数据库，也不会自动触发模型外发或向量化。

## 构建与安装快捷方式

在本目录运行 Build.ps1 编译开发版本；-InstallShortcut 可为当前用户创建桌面快捷方式。快捷方式是本机开发入口，不能复制到另一台电脑当作完整安装包。修改 C# 后请先关闭控制窗，再重新构建。

服务启动错误写入 %LOCALAPPDATA%\\MemoryHub\\logs\\control-service.log。常规命令行入口仍可用：

~~~powershell
memory serve
memory sync
~~~

远程访问、认证和完整记忆模式沿用主配置；控制窗不会修改 host、port、api_token_env 或 full_memory_mode。
