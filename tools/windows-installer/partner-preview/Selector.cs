using System;
using System.Collections.Generic;
using System.Globalization;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Win32;

namespace Workdesk.Installation
{
    public sealed class Dependency
    {
        public string id { get; set; }
        public string sha256 { get; set; }
        public long bytes { get; set; }
    }
    public sealed class Partner
    {
        public string id { get; set; }
        public string name { get; set; }
        public string version { get; set; }
        public string file { get; set; }
        public string release_id { get; set; }
        public string sha256 { get; set; }
        public long bytes { get; set; }
        public long installed_bytes { get; set; }
        public long component_bytes { get; set; }
        public Dependency[] dependencies { get; set; }
    }
    public sealed class Catalog
    {
        public string format { get; set; }
        public int format_version { get; set; }
        public Partner[] partners { get; set; }
    }

    public sealed class InstallMessage
    {
        public string type { get; set; }
        public string phase { get; set; }
        public long done { get; set; }
        public long total { get; set; }
        public bool ok { get; set; }
        public bool retryable { get; set; }
        public string error { get; set; }
        public string release_id { get; set; }
    }

    internal sealed class InstallFailure : IOException
    {
        internal bool Retryable { get; private set; }

        internal InstallFailure(string message, bool retryable) : base(message)
        {
            Retryable = retryable;
        }
    }

    internal class Selector
    {
        const string PackageExtension = ".wdpa";
        const string LegacyExtension = ".wdpartner";
        readonly string packageDirectory;
        readonly Dictionary<string,string> localFiles = new Dictionary<string,string>();
        bool installing;
        bool finished;
        bool closing;
        string failedPartner;
        readonly HashSet<string> completed = new HashSet<string>();
        readonly HashSet<string> needsReview = new HashSet<string>();
        static readonly byte[] PackageHeader = Encoding.ASCII.GetBytes("WDPARTNER\u0001");
        readonly Window window;
        readonly Partner[] partners;
        readonly HashSet<string> selected = new HashSet<string>();
        readonly HashSet<string> local = new HashSet<string>();
        readonly Dictionary<string, CheckBox> boxes = new Dictionary<string, CheckBox>();
        readonly Brush line = new SolidColorBrush(Color.FromRgb(228, 233, 226));
        readonly Brush accent = new SolidColorBrush(Color.FromRgb(36, 85, 61));
        readonly Brush muted = new SolidColorBrush(Color.FromRgb(82, 102, 92));
        bool confirming;
        bool importing;

        Stream Resource(string name)
        {
            var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
            if (stream == null) throw new InvalidDataException("预览资源缺失：" + name);
            return stream;
        }

        internal Selector(string packageDirectory)
        {
            this.packageDirectory = packageDirectory;
            using (var stream = Resource("selector.xaml")) window = (Window)XamlReader.Load(stream);
            using (var stream = Resource("catalog.json"))
            using (var reader = new StreamReader(stream))
            {
                var data = new JavaScriptSerializer().Deserialize<Catalog>(reader.ReadToEnd());
                if (data == null || data.format != "wdpartner" || data.format_version != 1)
                    throw new InvalidDataException("伙伴包目录的格式或版本不受支持。");
                partners = data.partners;
            }
            string[] order = { "codex", "deepseek", "qwen", "claude", "grok", "gemini", "kimi", "glm" };
            if (partners == null || partners.Length != order.Length) throw new InvalidDataException("伙伴清单不完整。");
            var catalog = partners.ToDictionary(p => p.id);
            partners = order.Select(id => catalog[id]).ToArray();
            foreach (var p in partners)
            {
                if (!IsPartnerFile(p.file) || p.file != Path.GetFileName(p.file) ||
                    p.bytes <= PackageHeader.Length || p.component_bytes <= 0 || p.dependencies == null ||
                    p.installed_bytes != p.component_bytes + p.dependencies.Sum(d => d.bytes) ||
                    String.IsNullOrEmpty(p.sha256) || p.sha256.Length != 64)
                    throw new InvalidDataException("伙伴包目录不完整：" + p.name);
            }
            Control<CheckBox>("SelectAll").Click += delegate {
                bool on = Pending().Count() != partners.Length - completed.Count;
                selected.Clear();
                selected.UnionWith(completed);
                if (on) selected.UnionWith(partners.Select(p => p.id));
                Render();
            };
            Control<Button>("Next").Click += async delegate {
                if (finished || (confirming && selected.Count == 0)) window.Close();
                else if (confirming) await InstallSelected();
                else { confirming = true; Feedback(""); Render(); }
            };
            window.Closing += delegate(object sender, System.ComponentModel.CancelEventArgs e) {
                if (installing) e.Cancel = MessageBox.Show(window, "当前伙伴将继续在后台安装。关闭窗口并跳过后续伙伴？",
                    "Workdesk", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes;
                closing = !e.Cancel;
            };
            Control<Button>("Back").Click += delegate { confirming = false; Feedback(""); Render(); };
            Control<Button>("Close").Click += delegate { window.Close(); };
            Control<Button>("Import").Click += async delegate { await Browse(); };
            Render();
        }

        T Control<T>(string name) where T : FrameworkElement { return (T)window.FindName(name); }

        static string Size(long value)
        {
            if (value == 0) return "0 MB";
            bool gb = value >= 1000000000;
            return (value / (gb ? 1000000000.0 : 1000000.0)).ToString(gb ? "0.##" : "0.#", CultureInfo.InvariantCulture) + (gb ? " GB" : " MB");
        }

        IEnumerable<Partner> Picks() { return partners.Where(p => selected.Contains(p.id)); }
        IEnumerable<Partner> Pending() { return Picks().Where(p => !completed.Contains(p.id)); }
        long DownloadBytes() { return Pending().Where(p => !local.Contains(p.id)).Sum(p => p.bytes); }
        long InstalledBytes()
        {
            var chosen = Picks().ToArray();
            var common = chosen.SelectMany(p => p.dependencies).GroupBy(d => new { d.id, d.sha256 });
            return chosen.Sum(p => p.component_bytes) + common.Sum(g => g.First().bytes);
        }

        void Totals()
        {
            Control<TextBlock>("Picked").Text = completed.Count == 0 ? "已选 " + selected.Count + " 位伙伴" :
                "已安装 " + completed.Count + " 位 · 待安装 " + Pending().Count() + " 位";
            Control<TextBlock>("Download").Text = Size(DownloadBytes());
            Control<TextBlock>("Installed").Text = Size(InstalledBytes());
            Control<CheckBox>("SelectAll").IsChecked = selected.Count == 0 ? (bool?)false :
                (selected.Count == partners.Length ? (bool?)true : null);
        }

        static Grid Columns()
        {
            var grid = new Grid { Margin = new Thickness(14, 0, 14, 0) };
            grid.ColumnDefinitions.Add(new ColumnDefinition());
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(98) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(98) });
            return grid;
        }

        static TextBlock Cell(Grid grid, int col, string text)
        {
            var block = new TextBlock {
                Text = text, VerticalAlignment = VerticalAlignment.Center,
                HorizontalAlignment = col == 0 ? HorizontalAlignment.Left : HorizontalAlignment.Right
            };
            Grid.SetColumn(block, col);
            grid.Children.Add(block);
            return block;
        }

        void AddRow(Partner p)
        {
            var grid = Columns();
            var name = new TextBlock { Text = p.name, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
            if (!confirming)
            {
                var label = new StackPanel { Orientation = Orientation.Horizontal };
                label.Children.Add(name);
                if (completed.Contains(p.id) || local.Contains(p.id)) label.Children.Add(new TextBlock {
                    Text = completed.Contains(p.id) ? "已安装" : "本地", FontSize = 11, Foreground = accent, Margin = new Thickness(9, 1, 0, 0), VerticalAlignment = VerticalAlignment.Center
                });
                var check = new CheckBox {
                    Content = label, IsChecked = selected.Contains(p.id), IsEnabled = !importing && !completed.Contains(p.id), Cursor = Cursors.Hand,
                    VerticalAlignment = VerticalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Center,
                    ToolTip = p.name + " " + p.version
                };
                boxes.Add(p.id, check);
                AutomationProperties.SetName(check, p.name);
                check.Click += delegate {
                    if (check.IsChecked == true) selected.Add(p.id); else selected.Remove(p.id);
                    Totals();
                };
                grid.Children.Add(check);
                Cell(grid, 1, Size(p.bytes)).ToolTip = p.bytes.ToString("N0") + " 字节";
                Cell(grid, 2, Size(p.installed_bytes)).Foreground = muted;
            }
            else
            {
                grid.Children.Add(name);
                string source = local.Contains(p.id) ? "已校验本地包" : "官方源下载";
                if (completed.Contains(p.id)) source = "已安装";
                if (needsReview.Contains(p.id)) source = "待核对";
                Cell(grid, 1, source).Foreground = completed.Contains(p.id) ? accent : muted;
                Cell(grid, 2, Size(p.bytes));
            }
            AddGrid(grid);
        }

        void AddGrid(Grid grid)
        {
            Control<StackPanel>("PartnerRows").Children.Add(new Border {
                BorderBrush = line, BorderThickness = new Thickness(0, 1, 0, 0), Height = 41, Child = grid
            });
        }

        void Render()
        {
            var rows = Control<StackPanel>("PartnerRows");
            rows.Children.Clear(); boxes.Clear();
            var edit = confirming ? Visibility.Collapsed : Visibility.Visible;
            var review = confirming ? Visibility.Visible : Visibility.Collapsed;
            bool retry = confirming && failedPartner != null && selected.Contains(failedPartner);
            Control<CheckBox>("SelectAll").Visibility = edit;
            Control<TextBlock>("ListHeader").Visibility = review;
            Control<Button>("Back").Visibility = finished ? Visibility.Collapsed : review;
            Control<Button>("Close").Visibility = retry && !finished ? Visibility.Visible : Visibility.Collapsed;
            Control<Button>("Import").Visibility = retry && !finished ? Visibility.Visible : edit;
            Control<TextBlock>("ImportHint").Visibility = Control<Button>("Import").Visibility;
            Control<TextBlock>("PageTitle").Text = finished ? "伙伴已就绪" : retry ? "安装未完成" :
                confirming ? "确认安装清单" : "选择工作伙伴";
            Control<TextBlock>("PageHelp").Text = finished ? "可以打开 Workdesk，在账号与模型中选择连接。" :
                retry ? (Pending().Any(p => needsReview.Contains(p.id)) ?
                    "请在 Workdesk 核对待确认的伙伴。可返回调整其余选择。" : "可重试待安装的伙伴，也可导入本地补充包。") :
                confirming ? "核对本次选择的组件和来源。" : "按需勾选官方工作端，也可以稍后在设置中添加。";
            Control<TextBlock>("SizeHeader").Text = confirming ? "来源" : "包体大小";
            Control<TextBlock>("DiskHeader").Text = confirming ? "包体大小" : "安装后占用";
            Control<Button>("Next").Content = finished ? "关闭" : confirming ?
                (!Pending().Any() ? "完成" : retry ? "重试安装" : "安装所选伙伴") : "查看清单　›";
            SetEnabled(!installing && !importing);
            if (confirming)
            {
                var main = Columns();
                Cell(main, 0, "Workdesk 主程序").FontWeight = FontWeights.SemiBold;
                Cell(main, 1, "主安装包").Foreground = muted;
                Cell(main, 2, "已包含").Foreground = muted;
                AddGrid(main);
            }
            foreach (var p in confirming ? Picks() : partners.AsEnumerable()) AddRow(p);
            Totals();
        }

        void Feedback(string text)
        {
            var message = Control<TextBlock>("Feedback");
            message.Text = text;
            message.Visibility = text.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
        }

        async Task Browse()
        {
            var picker = new OpenFileDialog {
                Title = "选择本地伙伴补充包", Multiselect = true, DefaultExt = PackageExtension,
                Filter = "Workdesk 伙伴补充包 (*.wdpa;*.wdpartner)|*.wdpa;*.wdpartner"
            };
            string nearby = packageDirectory;
            if (Directory.Exists(nearby)) picker.InitialDirectory = nearby;
            if (picker.ShowDialog(window) == true) await ImportFiles(picker.FileNames);
        }

        static bool IsPartnerFile(string file)
        {
            var ext = Path.GetExtension(file);
            return String.Equals(ext, PackageExtension, StringComparison.OrdinalIgnoreCase) ||
                   String.Equals(ext, LegacyExtension, StringComparison.OrdinalIgnoreCase);
        }

        static bool HasPackageHeader(Stream stream)
        {
            foreach (byte expected in PackageHeader)
                if (stream.ReadByte() != expected) return false;
            return true;
        }

        Partner Match(string file)
        {
            if (!IsPartnerFile(file)) return null;
            using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read, 131072, FileOptions.SequentialScan))
            {
                var candidates = partners.Where(p => p.bytes == stream.Length).ToArray();
                if (candidates.Length == 0 || !HasPackageHeader(stream)) return null;
                stream.Position = 0;
                using (var hasher = SHA256.Create())
                {
                    var hash = BitConverter.ToString(hasher.ComputeHash(stream)).Replace("-", "");
                    return candidates.SingleOrDefault(p => String.Equals(p.sha256, hash, StringComparison.OrdinalIgnoreCase));
                }
            }
        }

        async Task ImportFiles(string[] files)
        {
            if (installing || importing || finished) return;
            importing = true;
            SetEnabled(false);
            int matched = 0;
            var failures = new List<string>();
            try
            {
                foreach (string file in files)
                {
                    Feedback("正在校验 " + Path.GetFileName(file) + "…");
                    try
                    {
                        var match = await Task.Run(() => Match(file));
                        if (match == null) failures.Add(Path.GetFileName(file));
                        else if (!completed.Contains(match.id)) { local.Add(match.id); localFiles[match.id] = file; selected.Add(match.id); matched++; }
                    }
                    catch (IOException) { failures.Add(Path.GetFileName(file)); }
                    catch (UnauthorizedAccessException) { failures.Add(Path.GetFileName(file)); }
                }
                if (failures.Count == 0) Feedback("已校验 " + matched + " 份本地包。");
                else Feedback("已校验 " + matched + " 份。下列文件未匹配当前版本：" + String.Join("、", failures));
            }
            finally
            {
                importing = false; SetEnabled(true); Render();
            }
        }

        async Task InstallSelected(Func<Partner,string,IProgress<InstallMessage>,Task> install = null)
        {
            if (installing || finished) return;
            if (!Pending().Any()) { window.Close(); return; }
            installing = true;
            failedPartner = null;
            SetEnabled(false);
            Partner current = null;
            try {
                foreach (var partner in Pending().ToArray()) {
                    current = partner;
                    Render();
                    Feedback("");
                    ShowProgress(partner, new InstallMessage { phase = "prepare" });
                    var progress = new Progress<InstallMessage>(value => {
                        if (installing && !closing && current == partner) ShowProgress(partner, value);
                    });
                    string file;
                    localFiles.TryGetValue(partner.id, out file);
                    if (install == null) await Task.Run(() => InstallOne(partner, file, progress));
                    else await install(partner, file, progress);
                    if (closing) return;
                    completed.Add(partner.id);
                }
                finished = true;
                Feedback("已完成 " + completed.Count + " 位伙伴的安装。");
            } catch (Exception ex) {
                if (closing) return;
                if (current == null) throw;
                failedPartner = current.id;
                var failure = ex as InstallFailure;
                if (failure == null || !failure.Retryable) needsReview.Add(current.id);
                Feedback(current.name + " · " + ex.Message);
            } finally {
                installing = false;
                if (!closing) {
                    Control<StackPanel>("ProgressArea").Visibility = Visibility.Collapsed;
                    Render();
                }
            }
        }

        void ShowProgress(Partner partner, InstallMessage message)
        {
            string stage;
            switch (message.phase) {
                case "download": stage = "下载"; break;
                case "verify_bundle": stage = "校验补充包"; break;
                case "extract": stage = "解包"; break;
                case "verify_files": stage = "校验安装文件"; break;
                case "publish": stage = "登记安装结果"; break;
                default: stage = "准备安装"; break;
            }
            var bar = Control<ProgressBar>("InstallProgress");
            bar.IsIndeterminate = message.total <= 0;
            string suffix = "";
            if (!bar.IsIndeterminate) {
                bar.Value = Math.Min(100, Math.Max(0, message.done * 100.0 / message.total));
                suffix = " " + Math.Floor(bar.Value).ToString(CultureInfo.InvariantCulture) + "%";
            }
            string status = (completed.Count + 1) + " / " + selected.Count + " · " + partner.name + " · " + stage + suffix;
            Control<TextBlock>("ProgressText").Text = status;
            AutomationProperties.SetName(bar, status);
            Control<StackPanel>("ProgressArea").Visibility = Visibility.Visible;
        }

        static void InstallOne(Partner partner, string bundle, IProgress<InstallMessage> progress)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            var start = new ProcessStartInfo {
                FileName = Path.Combine(root, "runtime", "python", "python.exe"),
                Arguments = "-I -B -X utf8 tools/partner_command.py --progress",
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            var codec = new JavaScriptSerializer();
            var request = new {id=partner.id, version=partner.version, release_id=partner.release_id, bundle=bundle};
            using (var process = Process.Start(start)) {
                var errors = process.StandardError.ReadToEndAsync();
                var bytes = Encoding.UTF8.GetBytes(codec.Serialize(request) + "\n");
                process.StandardInput.BaseStream.Write(bytes, 0, bytes.Length);
                process.StandardInput.Close();
                InstallMessage result = null;
                string line;
                while ((line = process.StandardOutput.ReadLine()) != null) {
                    var message = codec.Deserialize<InstallMessage>(line);
                    if (message == null) continue;
                    if (message.type == "progress") progress.Report(message);
                    else result = message;
                }
                process.WaitForExit();
                errors.GetAwaiter().GetResult();
                if (result == null) throw new InstallFailure("本机安装服务未返回结果，请先核对原操作。", false);
                if (process.ExitCode != 0 || !result.ok)
                    throw new InstallFailure(result.error ?? "安装服务回执异常。", result.retryable);
                if (!partner.release_id.Equals(result.release_id)) throw new InstallFailure("安装回执的工作端版本不符。", false);
            }
        }

        void SetEnabled(bool enabled)
        {
            Control<Button>("Next").IsEnabled = enabled && (!confirming || !Pending().Any(p => needsReview.Contains(p.id)));
            Control<Button>("Back").IsEnabled = enabled;
            Control<Button>("Close").IsEnabled = enabled;
            Control<Button>("Import").IsEnabled = enabled && !finished;
            Control<CheckBox>("SelectAll").IsEnabled = enabled;
            foreach (var box in boxes) box.Value.IsEnabled = enabled && !completed.Contains(box.Key);
        }

        void Screenshot(string filepath)
        {
            var view = (FrameworkElement)window.Content;
            view.UpdateLayout();
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(view.ActualWidth), (int)Math.Ceiling(view.ActualHeight), 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(view);
            var png = new PngBitmapEncoder(); png.Frames.Add(BitmapFrame.Create(bitmap));
            using (var file = File.Create(filepath)) png.Save(file);
        }

        async Task Check(string dir)
        {
            // Run the same controls and package verification as the visible preview.
            Assert(selected.Count == 0 && DownloadBytes() == 0 && boxes.Count == 8);
            Screenshot(Path.Combine(dir, "selection.png"));
            Control<Button>("Next").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(confirming && Control<StackPanel>("PartnerRows").Children.Count == 1);
            Control<Button>("Back").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            foreach (string id in new[] { "codex", "deepseek" })
            {
                boxes[id].IsChecked = true;
                boxes[id].RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            }
            Assert(selected.Count == 2 && DownloadBytes() == Picks().Sum(p => p.bytes));
            var pkg = partners.Single(p => p.id == "deepseek");
            string file = Path.Combine(packageDirectory, pkg.file);
            // A renamed ZIP or a different format revision is not a partner package.
            Assert(!HasPackageHeader(new MemoryStream(new byte[] { 0x50, 0x4b, 0x03, 0x04 })));
            Assert(!HasPackageHeader(new MemoryStream(Encoding.ASCII.GetBytes("WDPARTNER\u0002"))));
            Assert(!IsPartnerFile("legacy.zip") && IsPartnerFile("renamed.WDPARTNER"));
            await ImportFiles(new[] { file });
            Assert(local.Contains(pkg.id) && DownloadBytes() == partners.Single(p => p.id == "codex").bytes);
            await ImportFiles(new[] { Path.Combine(packageDirectory, "catalog.json") });
            Assert(selected.Count == 2 && Control<TextBlock>("Feedback").Text.Contains("未匹配"));
            Control<Button>("Next").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(confirming && Control<StackPanel>("PartnerRows").Children.Count == 3);
            Screenshot(Path.Combine(dir, "review.png"));
            Control<Button>("Back").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Control<CheckBox>("SelectAll").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(selected.Count == 8 && InstalledBytes() <= partners.Sum(p => p.installed_bytes));
            Control<CheckBox>("SelectAll").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(selected.Count == 0 && DownloadBytes() == 0);
            await ImportFiles(partners.Select(p => Path.Combine(packageDirectory, p.file)).ToArray());
            Assert(local.Count == 8 && selected.Count == 8 && DownloadBytes() == 0);

            selected.IntersectWith(new[] { "codex", "deepseek", "kimi" });
            local.Remove(pkg.id);
            localFiles.Remove(pkg.id);
            Control<Button>("Next").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            var attempted = new List<string>();
            await InstallSelected(async (partner, bundle, progress) => {
                attempted.Add(partner.id);
                if (partner.id == "deepseek") {
                    Assert(bundle == null);
                    progress.Report(new InstallMessage { phase = "download", done = 55, total = 100 });
                    await Task.Yield();
                    Assert(Control<ProgressBar>("InstallProgress").Value == 55 &&
                        Control<TextBlock>("ProgressText").Text.Contains("2 / 3 · DeepSeek · 下载 55%"));
                    Screenshot(Path.Combine(dir, "progress.png"));
                    throw new InstallFailure("下载连接中断。", true);
                }
            });
            Assert(attempted.SequenceEqual(new[] { "codex", "deepseek" }) && completed.SetEquals(new[] { "codex" }));
            Assert(!finished && Control<Button>("Next").Content.Equals("重试安装") && Control<Button>("Next").IsEnabled &&
                Control<Button>("Back").IsEnabled && Control<Button>("Close").Visibility == Visibility.Visible &&
                Control<Button>("Import").Visibility == Visibility.Visible);
            Screenshot(Path.Combine(dir, "retry.png"));
            Control<Button>("Back").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(!confirming && !boxes["codex"].IsEnabled && selected.Contains("codex"));
            await ImportFiles(new[] { file });
            Control<Button>("Next").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            await InstallSelected((partner, bundle, progress) => {
                attempted.Add(partner.id);
                if (partner.id == "deepseek") Assert(bundle == file);
                return Task.FromResult(0);
            });
            Assert(finished && completed.Count == 3 && attempted.SequenceEqual(new[] { "codex", "deepseek", "deepseek", "kimi" }));
            Assert(Control<TextBlock>("Feedback").Text == "已完成 3 位伙伴的安装。");
        }

        static void Assert(bool ok) { if (!ok) throw new InvalidDataException("选装预览检查失败。"); }

        [STAThread]
        public static int Main(string[] args)
        {
            bool test = args.Contains("--check");
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            try
            {
                var app = new Application();
                int index = Array.IndexOf(args, "--package-dir");
                string packs = index >= 0 && index + 1 < args.Length ? Path.GetFullPath(args[index + 1]) : Path.Combine(dir, "partners");
                var picker = new Selector(packs);
                if (test)
                {
                    picker.window.ShowInTaskbar = false;
                    picker.window.ShowActivated = false;
                    picker.window.WindowStartupLocation = WindowStartupLocation.Manual;
                    picker.window.Left = -3000;
                    picker.window.ContentRendered += async delegate {
                        try { await picker.Check(dir); app.Shutdown(0); }
                        catch (Exception ex) { File.WriteAllText(Path.Combine(dir, "preview-check-error.txt"), ex.ToString()); app.Shutdown(1); }
                    };
                }
                return app.Run(picker.window);
            }
            catch (Exception ex)
            {
                if (test) File.WriteAllText(Path.Combine(dir, "preview-check-error.txt"), ex.ToString());
                else MessageBox.Show(ex.Message, "Workdesk 伙伴安装");
                return 1;
            }
        }
    }
}
