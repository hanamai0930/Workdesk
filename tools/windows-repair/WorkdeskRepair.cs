using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Workdesk 修复程序")]
[assembly: AssemblyDescription("Workdesk 1.0 合作任务能力等待修复")]
[assembly: AssemblyVersion("1.0.0.1")]

internal sealed class PatchFile {
    public string path { get; set; }
    public string before { get; set; }
    public string after { get; set; }
    public int bytes { get; set; }
}
internal sealed class PatchManifest {
    public int schema { get; set; }
    public string product { get; set; }
    public string patch_id { get; set; }
    public string base_version { get; set; }
    public string launcher_sha256 { get; set; }
    public string release_sha256 { get; set; }
    public PatchFile[] files { get; set; }
}
internal sealed class RepairResult {
    public bool success { get; set; }
    public string state { get; set; }
    public string message { get; set; }
    public string patch_id { get; set; }
    public string backup { get; set; }
    public int files { get; set; }
}

internal sealed class RepairEngine {
    internal readonly PatchManifest Manifest;
    private readonly Dictionary<string, byte[]> payload = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
    internal Action<string> Progress;

    internal RepairEngine() {
        using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("WorkdeskRepairPayload")) {
            if (resource == null) throw new IOException("修复程序不完整，请重新下载。");
            using (var archive = new ZipArchive(resource, ZipArchiveMode.Read)) {
                var manifestEntry = archive.GetEntry("manifest.json");
                if (manifestEntry == null || manifestEntry.Length > 4 * 1024 * 1024) throw new IOException("修复清单无效。");
                using (var reader = new StreamReader(manifestEntry.Open(), Encoding.UTF8)) Manifest = json.Deserialize<PatchManifest>(reader.ReadToEnd());
                if (Manifest == null || Manifest.schema != 1 || Manifest.product != "Workdesk" ||
                    Manifest.patch_id != "1.0.0-capability-recovery-20261123" || Manifest.base_version != "1.0.0" ||
                    !IsHash(Manifest.launcher_sha256) || !IsHash(Manifest.release_sha256) ||
                    Manifest.files == null || Manifest.files.Length == 0 || Manifest.files.Length > 300) throw new IOException("修复清单不适用于此版本。");
                foreach (var file in Manifest.files) {
                    if (file == null || !Allowed(file.path) || file.before != null && !IsHash(file.before) ||
                        !IsHash(file.after) || file.bytes < 0 || file.bytes > 64 * 1024 * 1024 || payload.ContainsKey(file.path)) throw new IOException("修复文件清单无效。");
                    var entry = archive.GetEntry("payload/" + file.path);
                    if (entry == null || entry.Length != file.bytes) throw new IOException("修复文件缺失或大小不符。");
                    byte[] data;
                    using (var memory = new MemoryStream()) { using (var input = entry.Open()) input.CopyTo(memory); data = memory.ToArray(); }
                    if (Hash(data) != file.after) throw new IOException("修复文件校验失败。");
                    payload.Add(file.path, data);
                }
                if (archive.Entries.Count != Manifest.files.Length + 1 || archive.Entries.Select(e => e.FullName).Distinct(StringComparer.OrdinalIgnoreCase).Count() != archive.Entries.Count) throw new IOException("修复包存在未登记或重复文件。");
            }
        }
    }
    private static bool IsHash(string value) { return value != null && value.Length == 64 && value.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f'); }
    internal static string Hash(byte[] data) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    internal static string FileHash(string path) { using (var stream = File.OpenRead(path)) using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
    private static bool Allowed(string path) {
        if (String.IsNullOrWhiteSpace(path) || path.IndexOf('\\') >= 0 || path.IndexOf(':') >= 0 || Path.IsPathRooted(path) || path.Split('/').Any(s => s == "" || s == "." || s == "..")) return false;
        if (path.StartsWith("src/researchos/workdesk/", StringComparison.Ordinal) && path.EndsWith(".py", StringComparison.Ordinal)) return true;
        if (path.StartsWith("workdesk-ui/static/", StringComparison.Ordinal) && (path.EndsWith(".js", StringComparison.Ordinal) || path.EndsWith(".css", StringComparison.Ordinal))) return true;
        if (path.StartsWith("workdesk-ui/dist/", StringComparison.Ordinal)) return new[] { ".js", ".css", ".html", ".json", ".svg", ".png", ".woff2" }.Contains(Path.GetExtension(path), StringComparer.Ordinal);
        return false;
    }
    private static void PlainPath(string path) {
        string current = Path.GetFullPath(path);
        while (!String.IsNullOrEmpty(current)) {
            if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new IOException("程序路径包含链接或重定向，请选择实际安装目录。");
            string parent = Path.GetDirectoryName(current);
            if (parent == current) break;
            current = parent;
        }
    }
    private static string Inside(string root, string relative) {
        string path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new IOException("程序文件路径超出安装目录。");
        PlainPath(path); return path;
    }
    internal string Root(string input) {
        if (String.IsNullOrWhiteSpace(input)) throw new IOException("请先选择已安装的 Workdesk 程序文件夹。");
        if (input.Length < 3 || input[1] != ':' || input[2] != Path.DirectorySeparatorChar) throw new IOException("请选择完整的本机程序安装路径。");
        string full = Path.GetFullPath(input);
        if (String.Equals(full, Path.GetPathRoot(full), StringComparison.OrdinalIgnoreCase)) throw new IOException("请选择 Workdesk 的程序文件夹，不能选择磁盘根目录。");
        string root = full.TrimEnd(Path.DirectorySeparatorChar);
        if (root.StartsWith(@"\\", StringComparison.Ordinal) || !Directory.Exists(root)) throw new IOException("请选择本机已安装的 Workdesk 程序文件夹。");
        PlainPath(root);
        string personal = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".workdesk-release"));
        if (root.Equals(personal, StringComparison.OrdinalIgnoreCase) || root.StartsWith(personal + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
            root.Split(Path.DirectorySeparatorChar).Any(s => s.Equals(".workdesk", StringComparison.OrdinalIgnoreCase) || s.Equals(".workdesk-release", StringComparison.OrdinalIgnoreCase))) throw new IOException("这是用户资料目录，请选择含 Workdesk.exe 的程序安装目录。");
        string marker = Inside(root, "release.json"), launcher = Inside(root, "Workdesk.exe");
        if (!File.Exists(marker) || !File.Exists(launcher) || FileHash(marker) != Manifest.release_sha256 || FileHash(launcher) != Manifest.launcher_sha256) throw new IOException("该目录不是本修复程序支持的 Workdesk 1.0.0 安装版。请核对安装位置与版本。");
        return root;
    }
    private List<PatchFile> Pending(string root) {
        var pending = new List<PatchFile>();
        foreach (var file in Manifest.files) {
            string target = Inside(root, file.path);
            string actual = File.Exists(target) ? FileHash(target) : null;
            if (actual == file.after) continue;
            if (actual != file.before || Directory.Exists(target)) throw new IOException("程序文件与已发布版本不同，修复未开始：" + file.path + "。请保留此目录并联系维护者。");
            pending.Add(file);
        }
        return pending;
    }
    private List<FileStream> Guards(string root, IEnumerable<PatchFile> pending) {
        var streams = new List<FileStream>();
        var targets = new List<string> { "Workdesk.exe", "runtime/python/python.exe", "runtime/python/pythonw.exe", "runtime/workdesk-desktop/Workdesk.Desktop.exe" };
        string pointer = Inside(root, "runtime/workdesk-desktop/current.json");
        if (File.Exists(pointer)) {
            var value = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(pointer, Encoding.UTF8));
            object entry;
            if (value == null || !value.TryGetValue("executable", out entry) || !(entry is string)) throw new IOException("桌面程序位置配置无效，请联系维护者。");
            string relative = (string)entry;
            if (relative.IndexOf('\\') >= 0 || relative.IndexOf(':') >= 0 || relative.Split('/').Any(p => p == "" || p == "." || p == "..") || Path.GetFileName(relative) != "Workdesk.Desktop.exe") throw new IOException("桌面程序路径无效。");
            targets.Add("runtime/workdesk-desktop/" + relative);
        }
        try {
            foreach (string name in targets.Distinct(StringComparer.OrdinalIgnoreCase)) {
                string file = Inside(root, name);
                if (File.Exists(file)) streams.Add(new FileStream(file, FileMode.Open, FileAccess.ReadWrite, FileShare.None));
            }
            // Open each changed file before taking a backup, to detect editors and other writers.
            foreach (var file in pending) {
                string target = Inside(root, file.path);
                if (File.Exists(target)) using (var probe = new FileStream(target, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
            }
            return streams;
        } catch (IOException) { foreach (var stream in streams) stream.Dispose(); throw new IOException("Workdesk 或相关程序文件仍在使用。请先保存工作并关闭 Workdesk；若后台仍占用，请重启 Windows，重启后先运行本修复程序，再打开 Workdesk。修复尚未开始。"); }
    }
    internal RepairResult Inspect(string input) {
        string root = Root(input); var pending = Pending(root);
        if (pending.Count == 0) return Result("ALREADY_APPLIED", "这个 Workdesk 已包含本次修复，可直接打开使用。", 0, null);
        var guards = Guards(root, pending); foreach (var stream in guards) stream.Dispose();
        return Result("READY", "已找到 Workdesk 1.0.0，可更新 " + pending.Count + " 个程序文件。", pending.Count, null);
    }
    private RepairResult Result(string state, string message, int count, string backup) { return new RepairResult { success = true, state = state, message = message, patch_id = Manifest.patch_id, files = count, backup = backup }; }
    private void Tell(string text) { if (Progress != null) Progress(text); }
    private static void AtomicWrite(string target, byte[] bytes) {
        string temp = target + ".workdesk-repair-" + Guid.NewGuid().ToString("N") + ".tmp";
        PlainPath(target); Directory.CreateDirectory(Path.GetDirectoryName(target));
        try {
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
            if (File.Exists(target)) File.Replace(temp, target, null); else File.Move(temp, target);
        } finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    private void Record(string backup, string state, List<PatchFile> pending, string error) {
        var record = new { schema = 1, product = "Workdesk", patch_id = Manifest.patch_id, state = state, utc = DateTime.UtcNow.ToString("o"), files = pending, error = error };
        AtomicWrite(Path.Combine(backup, "transaction.json"), new UTF8Encoding(false).GetBytes(json.Serialize(record)));
    }
    internal RepairResult Apply(string input, Action<int> afterWrite) {
        string root = Root(input);
        using (var mutex = new Mutex(false, @"Local\WorkdeskRepair-" + Hash(Encoding.UTF8.GetBytes(root.ToLowerInvariant())).Substring(0, 24))) {
            bool owned;
            try { owned = mutex.WaitOne(0); } catch (AbandonedMutexException) { owned = true; }
            if (!owned) throw new IOException("另一个修复程序正在处理此安装目录，请等待完成。");
            try {
                var pending = Pending(root);
                if (pending.Count == 0) return Result("ALREADY_APPLIED", "这个 Workdesk 已包含本次修复，可直接打开使用。", 0, null);
                var guards = Guards(root, pending);
                try {
                    // Recheck after acquiring execution locks: no launcher may start while files change.
                    pending = Pending(root);
                    string backup = Inside(root, ".workdesk-repair/" + Manifest.patch_id + "/" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
                    Tell("正在备份原程序文件…");
                    Directory.CreateDirectory(backup);
                    foreach (var file in pending) if (file.before != null) {
                        string target = Inside(root, file.path), saved = Inside(backup, file.path);
                        Directory.CreateDirectory(Path.GetDirectoryName(saved)); File.Copy(target, saved, false);
                        if (FileHash(saved) != file.before) throw new IOException("原程序备份校验失败，修复尚未开始。");
                    }
                    Record(backup, "PREPARED", pending, null);
                    var changed = new List<PatchFile>();
                    try {
                        Tell("正在更新程序文件…");
                        foreach (var file in pending) {
                            string target = Inside(root, file.path);
                            string current = File.Exists(target) ? FileHash(target) : null;
                            if (current != file.before) throw new IOException("更新前程序文件发生变化：" + file.path);
                            changed.Add(file);
                            AtomicWrite(target, payload[file.path]);
                            if (FileHash(target) != file.after) throw new IOException("更新后的程序文件校验失败：" + file.path);
                            if (afterWrite != null) afterWrite(changed.Count);
                        }
                        Record(backup, "APPLIED", pending, null);
                        AtomicWrite(Inside(root, ".workdesk-repair/last-repair.json"), new UTF8Encoding(false).GetBytes(json.Serialize(new { schema = 1, patch_id = Manifest.patch_id, state = "APPLIED", utc = DateTime.UtcNow.ToString("o"), backup = backup, files = Manifest.files })));
                    } catch (Exception failure) {
                        Tell("更新未完成，正在恢复原程序文件…");
                        var rollbackErrors = new List<string>();
                        foreach (var file in changed.AsEnumerable().Reverse()) try {
                            string target = Inside(root, file.path);
                            if (file.before == null) { if (File.Exists(target)) File.Delete(target); }
                            else { AtomicWrite(target, File.ReadAllBytes(Inside(backup, file.path))); if (FileHash(target) != file.before) throw new IOException("恢复校验失败"); }
                        } catch (Exception rollback) { rollbackErrors.Add(file.path + "：" + rollback.Message); }
                        string status = rollbackErrors.Count == 0 ? "ROLLED_BACK" : "ROLLBACK_FAILED";
                        Record(backup, status, pending, failure.Message);
                        if (rollbackErrors.Count > 0) throw new IOException("更新未完成，部分原文件未能恢复。请保留程序目录并联系维护者。备份位置：" + backup + "\r\n" + String.Join("\r\n", rollbackErrors));
                        throw new IOException("更新未完成，已恢复原程序文件。账号和任务资料保持原状。备份位置：" + backup + "\r\n原因：" + failure.Message);
                    }
                    return Result("APPLIED", "修复完成。重新打开 Workdesk 后，在原合作任务中查看等待原因并继续处理。", pending.Count, backup);
                } finally { foreach (var stream in guards) stream.Dispose(); }
            } finally { mutex.ReleaseMutex(); }
        }
    }
    internal static string DetectInstall() {
        using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Workdesk-Windows-x64_is1")) {
            if (key != null) { var value = key.GetValue("InstallLocation") as string; if (!String.IsNullOrWhiteSpace(value)) return value.TrimEnd('\\'); }
        }
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Workdesk");
    }
}

internal sealed class RepairForm : Form {
    private readonly TextBox folder = new TextBox();
    private readonly TextBox status = new TextBox();
    private readonly Button browse = new Button(), check = new Button(), repair = new Button(), launch = new Button();
    private readonly RepairEngine engine;
    internal RepairForm() {
        Text = "Workdesk 1.0 · 合作任务修复"; ClientSize = new Size(660, 430); StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; Font = new Font("Microsoft YaHei UI", 9F); AutoScaleMode = AutoScaleMode.Dpi;
        var intro = new Label { Left = 24, Top = 20, Width = 610, Height = 64, Text = "修复合作任务等待补充能力后无法继续的问题。\r\n仅更新程序文件，保留账号、任务记录与项目文件。\r\n请先保存工作并关闭 Workdesk，再开始修复。" };
        var label = new Label { Left = 24, Top = 92, Width = 500, Height = 24, Text = "Workdesk 程序安装位置" };
        folder.SetBounds(24, 119, 500, 29); folder.Text = RepairEngine.DetectInstall();
        browse.Text = "选择文件夹…"; browse.SetBounds(536, 117, 100, 32);
        browse.Click += delegate { using (var dialog = new FolderBrowserDialog { Description = "选择含 Workdesk.exe 的程序安装文件夹", SelectedPath = folder.Text }) if (dialog.ShowDialog(this) == DialogResult.OK) folder.Text = dialog.SelectedPath; };
        status.SetBounds(24, 163, 612, 184); status.Multiline = true; status.ReadOnly = true; status.ScrollBars = ScrollBars.Vertical; status.BackColor = SystemColors.Window;
        status.Text = "修复程序会先核对版本、备份原文件，更新失败时恢复原程序。\r\n如果后台仍占用文件，请重启 Windows 后先运行本修复程序。";
        check.Text = "检查安装"; check.SetBounds(24, 370, 110, 35);
        repair.Text = "开始修复"; repair.SetBounds(386, 370, 120, 35);
        launch.Text = "打开 Workdesk"; launch.SetBounds(516, 370, 120, 35); launch.Enabled = false;
        Controls.AddRange(new Control[] { intro, label, folder, browse, status, check, repair, launch });
        engine = new RepairEngine(); engine.Progress = delegate(string text) { BeginInvoke((Action)(() => status.AppendText("\r\n" + text))); };
        check.Click += async delegate { await Run(false); };
        repair.Click += async delegate { await Run(true); };
        launch.Click += delegate { try { Process.Start(new ProcessStartInfo(Path.Combine(engine.Root(folder.Text), "Workdesk.exe")) { UseShellExecute = true }); Close(); } catch (Exception error) { status.Text = error.Message; } };
    }
    private async Task Run(bool apply) {
        string path = folder.Text; folder.Enabled = browse.Enabled = check.Enabled = repair.Enabled = launch.Enabled = false; ControlBox = false;
        try {
            var result = await Task.Run(() => apply ? engine.Apply(path, null) : engine.Inspect(path));
            status.Text = result.message + (String.IsNullOrEmpty(result.backup) ? "" : "\r\n\r\n原程序备份：" + result.backup);
            launch.Enabled = result.state == "APPLIED" || result.state == "ALREADY_APPLIED";
        } catch (Exception error) { status.Text = error.Message; }
        finally { folder.Enabled = browse.Enabled = check.Enabled = repair.Enabled = true; ControlBox = true; }
    }
}
internal static class RepairProgram {
    [STAThread] private static int Main(string[] args) {
        string resultPath = null;
        try {
            if (args.Length > 0) {
                bool apply = args[0] == "--apply", inspect = args[0] == "--check";
                if ((!apply && !inspect) || args.Length != 5 || args[1] != "--directory" || args[3] != "--result") throw new ArgumentException("修复参数无效。");
                resultPath = Path.GetFullPath(args[4]);
                var engine = new RepairEngine(); var result = apply ? engine.Apply(args[2], null) : engine.Inspect(args[2]);
                File.WriteAllText(resultPath, new JavaScriptSerializer().Serialize(result), new UTF8Encoding(false)); return 0;
            }
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new RepairForm()); return 0;
        } catch (Exception error) {
            if (resultPath != null) File.WriteAllText(resultPath, new JavaScriptSerializer().Serialize(new RepairResult { success = false, state = "REFUSED", message = error.Message }), new UTF8Encoding(false));
            else MessageBox.Show(error.Message, "Workdesk · 修复未开始", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
