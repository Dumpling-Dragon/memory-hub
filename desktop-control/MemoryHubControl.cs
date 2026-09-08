using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class Program
{
    internal const string WindowTitle = "Memory Hub · 本机档案室";
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string className, string windowName);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr handle, int command);

    [STAThread]
    private static void Main()
    {
        bool first;
        using (var mutex = new Mutex(true, @"Local\MemoryHub.DesktopControl", out first))
        {
            if (!first)
            {
                var handle = FindWindow(null, WindowTitle);
                if (handle != IntPtr.Zero) { ShowWindow(handle, 9); SetForegroundWindow(handle); }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try { Application.Run(new ControlWindow()); }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "Memory Hub 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}

internal sealed class InkProgress : Control
{
    internal double Fraction;
    internal InkProgress() { DoubleBuffered = true; AccessibleName = "向量覆盖进度"; }
    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.Clear(ColorTranslator.FromHtml("#E9E4D8"));
        using (var brush = new SolidBrush(ColorTranslator.FromHtml("#8B3A32")))
            e.Graphics.FillRectangle(brush, 0, 0, (int)(Width * Math.Max(0, Math.Min(1, Fraction))), Height);
    }
}

internal sealed class ControlWindow : Form
{
    private readonly Color ink = ColorTranslator.FromHtml("#20201C");
    private readonly Color mute = ColorTranslator.FromHtml("#656157");
    private readonly Color accent = ColorTranslator.FromHtml("#8B3A32");
    private readonly Color green = ColorTranslator.FromHtml("#47513A");
    private readonly string projectRoot;
    private readonly string appData = Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MemoryHub");
    private readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(6) };
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 10000 };
    private string baseUrl = "http://127.0.0.1:17888";
    private int port = 17888;
    private bool busy;
    private Process startedService;
    private long previousEmbedded = -1;
    private Label state, detail, percent, embedded, pending, total, modelLine, sampling, storage, activity;
    private Button start, refresh, web, progressPage;
    private InkProgress bar;

    internal ControlWindow()
    {
        // The compiled exe lives in <project>/desktop-control/bin.
        projectRoot = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
        ReadEndpoint();
        Text = Program.WindowTitle;
        Font = new Font("Microsoft YaHei UI", 10);
        BackColor = ColorTranslator.FromHtml("#FBF8F1");
        ForeColor = ink;
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new Size(744, 612);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        BuildPage();
        Shown += async delegate { await Connect(true); timer.Start(); };
        timer.Tick += async delegate { await Connect(false); };
        FormClosed += delegate { timer.Stop(); timer.Dispose(); http.Dispose(); };
    }

    private void ReadEndpoint()
    {
        var path = Path.Combine(appData, "config.json");
        if (!File.Exists(path)) return;
        var config = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
        if (config.ContainsKey("api_token_env"))
        {
            var variable = Convert.ToString(config["api_token_env"]);
            if (!String.IsNullOrWhiteSpace(variable))
            {
                var token = Environment.GetEnvironmentVariable(variable) ?? Environment.GetEnvironmentVariable(variable, EnvironmentVariableTarget.User);
                if (!String.IsNullOrWhiteSpace(token)) http.DefaultRequestHeaders.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            }
        }
        if (config.ContainsKey("port")) port = Convert.ToInt32(config["port"]);
        var host = config.ContainsKey("host") ? Convert.ToString(config["host"]) : "127.0.0.1";
        if (host == "0.0.0.0") host = "127.0.0.1";
        if (host == "::") host = "::1";
        baseUrl = new UriBuilder("http", host, port).Uri.ToString().TrimEnd('/');
    }

    private Label TextAt(string name, string text, int x, int y, int width, int height, float size, string face, Color color)
    {
        var label = new Label { Name = name, AccessibleName = name, Text = text, Location = new Point(x, y),
            Size = new Size(width, height), Font = new Font(face, size), ForeColor = color, AutoEllipsis = true };
        Controls.Add(label);
        return label;
    }

    private void Rule(int y, int thickness)
    {
        Controls.Add(new Panel { Location = new Point(32, y), Size = new Size(680, thickness),
            BackColor = thickness > 1 ? ink : ColorTranslator.FromHtml("#B9B1A3") });
    }

    private Button Action(string name, string caption, int x, int width, bool primary)
    {
        var button = new Button { Name = name, AccessibleName = caption, Text = caption,
            Location = new Point(x, 502), Size = new Size(width, 44), FlatStyle = FlatStyle.Flat,
            BackColor = primary ? ink : BackColor, ForeColor = primary ? BackColor : ink,
            Cursor = Cursors.Hand, UseVisualStyleBackColor = false };
        button.FlatAppearance.BorderColor = ink;
        Controls.Add(button);
        return button;
    }

    private void BuildPage()
    {
        TextAt("Edition", "LOCAL WORK MEMORY  /  桌面值班台", 32, 20, 480, 24, 9, "Microsoft YaHei UI", mute);
        TextAt("Date", DateTime.Now.ToString("yyyy.MM.dd"), 596, 20, 118, 24, 11, "Consolas", mute);
        Rule(50, 1);
        TextAt("Title", "Memory Hub", 29, 63, 425, 55, 33, "Georgia", ink);
        TextAt("Subtitle", "本机档案室", 551, 87, 162, 28, 17, "SimSun", ink);
        Rule(132, 3);
        state = TextAt("ServiceState", "正在连接…", 32, 150, 260, 30, 15, "Microsoft YaHei UI", mute);
        TextAt("Endpoint", baseUrl.Replace("http://", ""), 464, 155, 250, 25, 11, "Consolas", mute).TextAlign = ContentAlignment.TopRight;
        detail = TextAt("ServiceDetail", "检查本地服务", 32, 190, 680, 46, 10, "Microsoft YaHei UI", mute);
        Rule(245, 1);
        TextAt("Section", "向量编目", 32, 262, 210, 28, 17, "SimSun", ink);
        TextAt("SectionNote", "已有向量覆盖率", 542, 269, 172, 22, 10, "Microsoft YaHei UI", mute).TextAlign = ContentAlignment.TopRight;
        percent = TextAt("Coverage", "—", 29, 298, 220, 64, 36, "Georgia", accent);
        total = TextAt("Documents", "总记录  —", 282, 312, 430, 24, 11, "Microsoft YaHei UI", ink);
        embedded = TextAt("Embedded", "已覆盖  —", 282, 343, 215, 24, 11, "Microsoft YaHei UI", green);
        pending = TextAt("Pending", "待补齐  —", 501, 343, 211, 24, 11, "Microsoft YaHei UI", accent);
        bar = new InkProgress { Location = new Point(32, 381), Size = new Size(680, 12) };
        Controls.Add(bar);
        modelLine = TextAt("Models", "等待向量库统计…", 32, 405, 680, 27, 9, "Microsoft YaHei UI", mute);
        activity = TextAt("Activity", "", 32, 438, 680, 26, 10, "Microsoft YaHei UI", mute);
        Rule(482, 1);
        start = Action("StartService", "启动服务", 32, 150, true);
        refresh = Action("Refresh", "刷新状态", 194, 142, false);
        web = Action("OpenWeb", "打开网页版", 348, 176, false);
        progressPage = Action("OpenProgress", "详细进度 ↗", 536, 176, false);
        start.Click += async delegate { await Connect(true); };
        refresh.Click += async delegate { await Connect(false); };
        web.Click += delegate { OpenPage(""); };
        progressPage.Click += delegate { OpenPage("/embedding-progress"); };
        sampling = TextAt("UpdatedAt", "等待首次采样", 32, 562, 400, 21, 9, "Microsoft YaHei UI", mute);
        storage = TextAt("Storage", "", 431, 562, 281, 21, 9, "Microsoft YaHei UI", mute);
        storage.TextAlign = ContentAlignment.TopRight;
        TextAt("CloseHint", "关闭此窗口后，服务和后台任务继续运行。", 32, 586, 680, 21, 9, "Microsoft YaHei UI", mute);
        web.Enabled = progressPage.Enabled = false;
    }

    private void OpenPage(string path)
    {
        try { Process.Start(new ProcessStartInfo(baseUrl + path) { UseShellExecute = true }); }
        catch (Exception e) { detail.Text = "打开浏览器失败：" + e.Message; }
    }

    private async Task<bool> Healthy()
    {
        try
        {
            var response = json.Deserialize<Dictionary<string, object>>(await http.GetStringAsync(baseUrl + "/api/health/live"));
            return response.ContainsKey("status") && Convert.ToString(response["status"]) == "ok";
        }
        catch { return false; }
    }

    private bool PortOccupied()
    {
        return IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners().Any(endpoint => endpoint.Port == port);
    }

    private async Task LaunchService()
    {
        if (startedService == null || startedService.HasExited)
        {
            var python = Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python)) throw new IOException("找不到项目 Python 环境；请从桌面快捷方式打开，并保留项目目录。\n" + python);
            startedService = Process.Start(new ProcessStartInfo(python, "-m memory_hub.service_entry")
            {
                WorkingDirectory = projectRoot, UseShellExecute = false,
                CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden
            });
        }
        var deadline = DateTime.UtcNow.AddSeconds(35);
        while (DateTime.UtcNow < deadline)
        {
            if (await Healthy()) return;
            if (startedService.HasExited) throw new IOException("服务进程已退出。诊断日志：" + Path.Combine(appData, "logs", "control-service.log"));
            await Task.Delay(750);
        }
        throw new IOException("服务尚未就绪，请稍后刷新。诊断日志：" + Path.Combine(appData, "logs", "control-service.log"));
    }

    private async Task Connect(bool allowStart)
    {
        if (busy) return;
        busy = true;
        start.Enabled = refresh.Enabled = false;
        bool healthy = false;
        try
        {
            healthy = await Healthy();
            if (!healthy && allowStart)
            {
                if (PortOccupied()) throw new IOException("端口已被占用，但服务未通过健康检查；请查看原服务状态后再重试。");
                state.Text = "正在启动服务…"; state.ForeColor = mute;
                detail.Text = "正在等待服务就绪…";
                await LaunchService();
                healthy = true;
            }
            if (!healthy)
            {
                state.Text = "服务未就绪"; state.ForeColor = accent;
                detail.Text = "可点击“启动服务”连接或启动；下方历史统计如有显示，不代表当前状态。";
                sampling.Text = "连接失败 · " + DateTime.Now.ToString("HH:mm:ss");
                previousEmbedded = -1;
                return;
            }
            state.Text = "服务运行中"; state.ForeColor = green;
            var snapshot = json.Deserialize<Snapshot>(await http.GetStringAsync(baseUrl + "/api/status"));
            if (snapshot == null || snapshot.embedding == null) throw new IOException("服务未返回有效的向量统计。");
            Render(snapshot);
        }
        catch (Exception e)
        {
            state.Text = healthy ? "服务在线 · 统计暂不可用" : "服务尚未就绪";
            state.ForeColor = accent;
            detail.Text = e is TaskCanceledException ? "读取超时，请稍后刷新。" : e.Message;
            sampling.Text = "读取失败 · " + DateTime.Now.ToString("HH:mm:ss") + " · 旧统计未更新";
        }
        finally
        {
            if (!IsDisposed)
            {
                start.Enabled = !healthy;
                start.Text = healthy ? "服务已启动" : "启动 / 重试";
                start.BackColor = healthy ? ColorTranslator.FromHtml("#E9E4D8") : ink;
                start.ForeColor = healthy ? mute : BackColor;
                web.Enabled = progressPage.Enabled = healthy;
                refresh.Enabled = true;
                busy = false;
            }
        }
    }

    private void Render(Snapshot snapshot)
    {
        var data = snapshot.embedding;
        var fraction = data.documents > 0 ? (double)data.embedded / data.documents : 0;
        percent.Text = (fraction * 100).ToString("F1") + "%";
        total.Text = "总记录  " + data.documents.ToString("N0");
        embedded.Text = "已覆盖  " + data.embedded.ToString("N0");
        pending.Text = "待补齐  " + data.pending.ToString("N0");
        bar.Fraction = fraction; bar.AccessibleDescription = percent.Text; bar.Invalidate();
        modelLine.Text = data.models == null ? "暂无模型统计" : string.Join("    /    ",
            data.models.Select(m => m.model + "  ·  " + m.embedded.ToString("N0")).ToArray());
        var syncing = snapshot.sync != null && snapshot.sync.running;
        detail.Text = syncing ? "后台正在收集最新记录；网页检索仍可使用。" : "本地服务已就绪，可打开网页版检索和管理记忆。";
        var added = previousEmbedded >= 0 ? data.embedded - previousEmbedded : 0;
        activity.Text = added > 0 ? "本次采样：向量覆盖增加 " + added.ToString("N0") + " 条。" : "每 10 秒读取向量覆盖量；同步和向量化操作请打开网页版。";
        previousEmbedded = data.embedded;
        sampling.Text = "更新于 " + DateTime.Now.ToString("HH:mm:ss") + " · 每 10 秒刷新";
        long bytes = 0;
        foreach (var suffix in new[] { "", "-wal", "-shm" })
        {
            var file = new FileInfo(Path.Combine(appData, "memory_hub.sqlite" + suffix));
            if (file.Exists) bytes += file.Length;
        }
        storage.Text = "本地库占用 " + (bytes / 1024d / 1024d).ToString("N1") + " MiB（含 WAL）";
    }

    public sealed class Snapshot { public Embedding embedding { get; set; } public Sync sync { get; set; } }
    public sealed class Sync { public bool running { get; set; } }
    public sealed class Embedding
    {
        public long documents { get; set; } public long embedded { get; set; } public long pending { get; set; }
        public List<Model> models { get; set; }
    }
    public sealed class Model { public string model { get; set; } public long embedded { get; set; } }
}
