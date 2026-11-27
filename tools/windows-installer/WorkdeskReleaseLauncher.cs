using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Workdesk")]
[assembly: System.Reflection.AssemblyProduct("Workdesk")]
[assembly: System.Reflection.AssemblyVersion("1.2.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.2.0.0")]

class WorkdeskReleaseLauncher {
    [STAThread]
    static int Main(string[] args) {
        string option = args.Length == 0 ? "" : args[0];
        string command;
        switch (option) {
            case "": command = "start"; break;
            case "--background": command = "start --no-open"; break;
            case "--stop": command = "stop"; break;
            case "--status": command = "status"; break;
            case "--check-stopped": command = "check-stopped"; break;
            default: return 2;
        }
        if (args.Length > 1) return 2;
        var quiet = option == "--check-stopped" || option == "--background";
        var root = AppDomain.CurrentDomain.BaseDirectory;
        try {
            var start = new ProcessStartInfo(Path.Combine(root, @"runtime\python\python.exe"),
                "-I -B -X utf8 \"" + Path.Combine(root, @"tools\release_instance.py") + "\" " + command);
            start.WorkingDirectory = root;
            start.CreateNoWindow = true;
            start.UseShellExecute = false;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.StandardOutputEncoding = Encoding.UTF8;
            start.StandardErrorEncoding = Encoding.UTF8;
            foreach (string key in new System.Collections.ArrayList(start.EnvironmentVariables.Keys)) {
                if (key.StartsWith("WORKDESK_", StringComparison.OrdinalIgnoreCase) ||
                    key.StartsWith("PYTHON", StringComparison.OrdinalIgnoreCase) ||
                    key.Equals("NODE_OPTIONS", StringComparison.OrdinalIgnoreCase)) start.EnvironmentVariables.Remove(key);
            }
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            using (var process = Process.Start(start)) {
                var errTask = process.StandardError.ReadToEndAsync();
                var output = process.StandardOutput.ReadToEnd();
                process.WaitForExit();
                var err = errTask.Result.Trim();
                if (process.ExitCode != 0) {
                    if (!quiet) MessageBox.Show(err.Length > 0 ? err : "Workdesk 操作未完成。", "Workdesk", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                } else if (option == "--status") {
                    MessageBox.Show(output.Contains("\"backend_state\": \"RUNNING\"") ? "工作服务正在运行。" : "工作服务已停止。", "Workdesk 状态");
                }
                return process.ExitCode;
            }
        } catch (Exception error) {
            if (!quiet) MessageBox.Show(error.Message, "Workdesk", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
