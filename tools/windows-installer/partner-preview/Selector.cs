using System;
using System.Collections.Generic;
using System.Globalization;
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

namespace Workdesk.InstallationPreview
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

    internal class Selector
    {
        const string PackageExtension = ".wdpartner";
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

        internal Selector()
        {
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
                bool on = selected.Count != partners.Length;
                selected.Clear();
                if (on) selected.UnionWith(partners.Select(p => p.id));
                Render();
            };
            Control<Button>("Next").Click += delegate {
                if (confirming) window.Close();
                else { confirming = true; Feedback(""); Render(); }
            };
            Control<Button>("Back").Click += delegate { confirming = false; Render(); };
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
        long DownloadBytes() { return Picks().Where(p => !local.Contains(p.id)).Sum(p => p.bytes); }
        long InstalledBytes()
        {
            var chosen = Picks().ToArray();
            var common = chosen.SelectMany(p => p.dependencies).GroupBy(d => new { d.id, d.sha256 });
            return chosen.Sum(p => p.component_bytes) + common.Sum(g => g.First().bytes);
        }

        void Totals()
        {
            Control<TextBlock>("Picked").Text = "已选 " + selected.Count + " 位伙伴";
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
                if (local.Contains(p.id)) label.Children.Add(new TextBlock {
                    Text = "本地", FontSize = 11, Foreground = accent, Margin = new Thickness(9, 1, 0, 0), VerticalAlignment = VerticalAlignment.Center
                });
                var check = new CheckBox {
                    Content = label, IsChecked = selected.Contains(p.id), IsEnabled = !importing, Cursor = Cursors.Hand,
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
                Cell(grid, 1, local.Contains(p.id) ? "已校验本地包" : "联网下载").Foreground = muted;
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
            Control<CheckBox>("SelectAll").Visibility = edit;
            Control<TextBlock>("ListHeader").Visibility = review;
            Control<Button>("Back").Visibility = review;
            Control<Button>("Import").Visibility = edit;
            Control<TextBlock>("ImportHint").Visibility = edit;
            Control<TextBlock>("PageTitle").Text = confirming ? "确认安装清单" : "选择工作伙伴";
            Control<TextBlock>("PageHelp").Text = confirming ? "核对本次选择的组件和来源。" : "按需勾选官方工作端，也可以稍后在设置中添加。";
            Control<TextBlock>("SizeHeader").Text = confirming ? "来源" : "包体大小";
            Control<TextBlock>("DiskHeader").Text = confirming ? "包体大小" : "安装后占用";
            Control<Button>("Next").Content = confirming ? "关闭预览" : "查看清单　›";
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
                Filter = "Workdesk 伙伴补充包 (*" + PackageExtension + ")|*" + PackageExtension
            };
            string nearby = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "packages");
            if (Directory.Exists(nearby)) picker.InitialDirectory = nearby;
            if (picker.ShowDialog(window) == true) await ImportFiles(picker.FileNames);
        }

        static bool IsPartnerFile(string file)
        {
            return String.Equals(Path.GetExtension(file), PackageExtension, StringComparison.OrdinalIgnoreCase);
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
            if (importing) return;
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
                        else { local.Add(match.id); selected.Add(match.id); matched++; }
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

        void SetEnabled(bool enabled)
        {
            Control<Button>("Next").IsEnabled = enabled;
            Control<Button>("Import").IsEnabled = enabled;
            Control<CheckBox>("SelectAll").IsEnabled = enabled;
            foreach (var box in boxes.Values) box.IsEnabled = enabled;
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
            string file = Path.Combine(dir, "packages", pkg.file);
            // A renamed ZIP or a different format revision is not a partner package.
            Assert(!HasPackageHeader(new MemoryStream(new byte[] { 0x50, 0x4b, 0x03, 0x04 })));
            Assert(!HasPackageHeader(new MemoryStream(Encoding.ASCII.GetBytes("WDPARTNER\u0002"))));
            Assert(!IsPartnerFile("legacy.zip") && IsPartnerFile("renamed.WDPARTNER"));
            await ImportFiles(new[] { file });
            Assert(local.Contains(pkg.id) && DownloadBytes() == partners.Single(p => p.id == "codex").bytes);
            await ImportFiles(new[] { Path.Combine(dir, "packages", "catalog.json") });
            Assert(selected.Count == 2 && Control<TextBlock>("Feedback").Text.Contains("未匹配"));
            Control<Button>("Next").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(confirming && Control<StackPanel>("PartnerRows").Children.Count == 3);
            Screenshot(Path.Combine(dir, "review.png"));
            Control<Button>("Back").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Control<CheckBox>("SelectAll").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(selected.Count == 8 && InstalledBytes() <= partners.Sum(p => p.installed_bytes));
            Control<CheckBox>("SelectAll").RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Assert(selected.Count == 0 && DownloadBytes() == 0);
            await ImportFiles(partners.Select(p => Path.Combine(dir, "packages", p.file)).ToArray());
            Assert(local.Count == 8 && selected.Count == 8 && DownloadBytes() == 0);
        }

        static void Assert(bool ok) { if (!ok) throw new InvalidDataException("选装预览检查失败。"); }

        [STAThread]
        public static int Main(string[] args)
        {
            bool test = args.Length == 1 && args[0] == "--check";
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            try
            {
                var app = new Application();
                var picker = new Selector();
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
                else MessageBox.Show(ex.Message, "Workdesk 安装预览");
                return 1;
            }
        }
    }
}
