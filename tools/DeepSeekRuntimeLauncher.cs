// argv-only adapter to the published official dsh CLI. No agent/tool logic.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
internal static class DeepSeekRuntimeLauncher {
    static string Quote(string value) {
        var output=new StringBuilder("\"");int slashes=0;
        foreach(char c in value) {
            if(c=='\\'){slashes++;continue;}
            if(c=='\"'){output.Append('\\',slashes*2+1);output.Append(c);slashes=0;continue;}
            output.Append('\\',slashes);slashes=0;output.Append(c);
        }
        output.Append('\\',slashes*2);output.Append('"');return output.ToString();
    }
    static int Main(string[] args) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string node=Path.GetFullPath(Path.Combine(root,"..","node-v22.23.3-win-x64","node.exe"));
        string cli=Path.Combine(root,"node_modules","@deepseek-ai","dsh","lib","bin.js");
        var argv=new StringBuilder(Quote(cli));foreach(string arg in args)argv.Append(" ").Append(Quote(arg));
        try {
            var start=new ProcessStartInfo(node,argv.ToString());start.UseShellExecute=false;start.CreateNoWindow=true;
            start.RedirectStandardInput=true;start.RedirectStandardOutput=true;start.RedirectStandardError=true;
            using(var child=Process.Start(start)){
                Task.Run(()=>{try{var input=Console.OpenStandardInput();var buffer=new byte[8192];int count;while((count=input.Read(buffer,0,buffer.Length))>0){child.StandardInput.BaseStream.Write(buffer,0,count);child.StandardInput.BaseStream.Flush();}child.StandardInput.Close();}catch(IOException){}catch(ObjectDisposedException){}});
                var stdout=child.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
                var stderr=child.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
                child.WaitForExit();Task.WaitAll(stdout,stderr);return child.ExitCode;
            }
        } catch(Exception error){Console.Error.WriteLine("Official DeepSeek CLI launch failed: "+error.Message);return 1;}
    }
}
