# 维护与发布

开发安装：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[desktop,test]'
.\.venv\Scripts\memory.exe serve
~~~

源码脚本仍可用，例如 .\.venv\Scripts\python.exe -m memory_hub.cli sync。桌面控制窗是可选 Windows 入口，具体构建和验证见 desktop-control/README.md。项目依赖来自 pyproject.toml 的运行时和可选桌面组件；依赖清单未经过独立安全审计，不应据此推断第三方依赖没有漏洞。

发布前检查：

- 只提交源码、必要脚本、示例配置、公开文档和测试；数据库、WAL/SHM、日志、虚拟环境、编译产物、便携包、迁移/审计/发布目录、导出原件和个人交接文件不属于源码发布物。
- 检查 git status、最终 staged 文件和历史中的绝对路径、用户名、token、数据库及备份；.gitignore 不能清除已经提交的内容。
- 用全新目录执行 editable 安装，运行 .\.venv\Scripts\memory.exe serve、.\.venv\Scripts\memory.exe sync 和本地 health 检查；不要把作者机器上的桌面快捷方式或本地服务状态当成安装步骤。
- 运行 .\.venv\Scripts\python.exe scripts/run_tests.py 验证测试；需要桌面和测试依赖时使用 .[desktop,test]。
- 项目按 MIT 许可证发布，许可证文本见 LICENSE；第三方依赖仍受各自许可证约束。

## 已验证的依赖与检查入口

Windows / Python 3.11 可使用 `-c constraints-windows-py311.txt` 安装锁定的运行和测试依赖。该文件来自干净环境，不包含个人环境的额外工具。2026-09-08 对这 27 个版本执行 pip-audit，未发现已知漏洞；这不保证未来没有漏洞。

隔离回归测试使用 `python scripts/run_tests.py`，临时库仅保存在项目 `.test-work` 下。`python -m build` 后执行 `python scripts/wheel_smoke.py` 可验证安装包里的页面和 CLI；GitHub Actions 已配置这两项。

`node tests/frontend.cjs` 是使用 Playwright 和 Edge 的合成响应测试，覆盖请求乱序和网络失败恢复；需要测试机预先安装 Node 与 Playwright。

`classify_web.py` 是继承的个人归属启发式，包含“我/同学”等预设，不能当通用身份识别器。普通导入不需要运行它；`refresh-web-memory.ps1` 的旧编排会调用它，使用该编排前应检查规则和已有归属确认文件。

将运行数据保留在 %LOCALAPPDATA%\\MemoryHub，把 API key 放在环境变量或密钥管理器中。开启模型后，按照配置的模型提供商规则评估外发、保存和计费；full_memory_mode 默认关闭。远程访问需要用户自行配置网络边界和可选 Bearer 认证，不应把默认 loopback 配置理解为远程安全层。
