// Workdesk owns the shell; official Chat sites own their conversation UIs.
// Remote pages never receive native objects, shell tokens or API credentials.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class ChatView {
    public WebView2 View;
    public bool Ready;
    public string Status = "loading";
    public string Host = "";
}

internal sealed class ChatAppearance {
    public string Background;
    public double BackgroundOpacity;
    public string Border;
    public double BorderOpacity;
}

internal sealed partial class WorkdeskHost : Form {
    readonly Uri shellUrl;
    readonly string userRoot;
    readonly string reportPath;
    readonly string probeBase;
    readonly WebView2 shell = new WebView2();
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly Dictionary<string, ChatView> chats = new Dictionary<string, ChatView>();
    readonly Dictionary<string, ChatAppearance> appearances = new Dictionary<string, ChatAppearance>();
    readonly Dictionary<string, string> sites = new Dictionary<string, string> {
        {"codex", "https://chatgpt.com/"}, {"deepseek", "https://chat.deepseek.com/"}, {"qwen", "https://chat.qwen.ai/"},
        {"claude", "https://claude.ai/"}, {"grok", "https://grok.com/"}, {"gemini", "https://gemini.google.com/"},
        {"kimi", "https://www.kimi.com/"}, {"glm", "https://chat.z.ai/"}
    };
    string selected = "";
    bool wantsChat;
    string closePreference = "keyboard";
    bool closeShortcut, closingPrompt;
    Rectangle chatBounds;

    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [STAThread] static void Main(string[] args) {
        string url = null, root = null, report = null, probe = null, title = "Workdesk · 工作台";
        for (int i=0; i+1<args.Length; i+=2) {
            if (args[i]=="--url") url=args[i+1];
            else if (args[i]=="--user-root") root=args[i+1];
            else if (args[i]=="--report") report=args[i+1];
            else if (args[i]=="--probe-base") probe=args[i+1];
            else if (args[i]=="--title") title=args[i+1];
            else return;
        }
        Uri uri;
        if (!Uri.TryCreate(url, UriKind.Absolute, out uri) || uri.Scheme!="http" || uri.Host!="127.0.0.1" || !String.IsNullOrEmpty(uri.UserInfo) || !uri.AbsolutePath.StartsWith("/s/")) return;
        if (String.IsNullOrEmpty(root) || !Path.IsPathRooted(root)) return;
        if (title!="Workdesk · 工作台" && title!="Workdesk · 开发") return;
        if (probe!=null) {
            Uri p;
            if (!Uri.TryCreate(probe,UriKind.Absolute,out p) || p.Scheme!="http" || p.Host!="127.0.0.1" || p.Port!=uri.Port) return;
        }
        try {SetProcessDpiAwarenessContext(new IntPtr(-4));} catch (EntryPointNotFoundException) {}
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new WorkdeskHost(uri,root,report,probe,title));
    }

    WorkdeskHost(Uri url, string root, string report, string probe, string title) {
        shellUrl=url;userRoot=root;reportPath=report;probeBase=probe;
        Text=title;AutoScaleMode=AutoScaleMode.None;
        Size=new Size(1440,900);MinimumSize=new Size(780,560);
        StartPosition=FormStartPosition.CenterScreen;
        shell.Dock=DockStyle.Fill;Controls.Add(shell);
        Shown+=async (sender,e)=>await Initialize();
        FormClosing+=ConfirmWindowClose;
        FormClosed+=(sender,e)=>{foreach(var c in chats.Values)c.View.Dispose();shell.Dispose();};
    }
    void ConfirmWindowClose(object sender,FormClosingEventArgs e) {
        bool confirm=e.CloseReason==CloseReason.UserClosing &&
            (closePreference=="always" || closePreference=="keyboard" && closeShortcut);
        closeShortcut=false;
        if(!confirm || closingPrompt) return;
        closingPrompt=true;
        try {e.Cancel=MessageBox.Show(this,"关闭 Workdesk 窗口？后台工作会继续运行。","Workdesk",MessageBoxButtons.YesNo,MessageBoxIcon.Question,MessageBoxDefaultButton.Button2)!=DialogResult.Yes;}
        finally {closingPrompt=false;}
    }
    void CloseFromShortcut() {
        if(closingPrompt || IsDisposed) return;
        closeShortcut=true;
        Close();
    }
    protected override bool ProcessCmdKey(ref Message msg,Keys keyData) {
        if(keyData==(Keys.Control|Keys.W)) {CloseFromShortcut();return true;}
        return base.ProcessCmdKey(ref msg,keyData);
    }
    async Task Initialize() {
        try {
            var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(userRoot,"runtime","desktop","shell"));
            await shell.EnsureCoreWebView2Async(env);
            shell.CoreWebView2.Settings.AreHostObjectsAllowed=false;
            shell.CoreWebView2.NavigationStarting+=(sender,e)=> {
                Uri target;
                if (!Uri.TryCreate(e.Uri,UriKind.Absolute,out target) || target.GetLeftPart(UriPartial.Authority)!=shellUrl.GetLeftPart(UriPartial.Authority) || !target.AbsolutePath.StartsWith(shellUrl.AbsolutePath)) e.Cancel=true;
            };
            shell.CoreWebView2.NewWindowRequested+=(sender,e)=> {e.Handled=true;OpenExternal(e.Uri);};
            shell.CoreWebView2.NavigationCompleted+=(sender,e)=>{if(probeBase!=null && e.IsSuccess)StartProbe();};
            shell.CoreWebView2.WebMessageReceived+=async (sender,e)=> {
                try {
                    Uri source=new Uri(e.Source);
                    if (source.GetLeftPart(UriPartial.Authority)!=shellUrl.GetLeftPart(UriPartial.Authority) || !source.AbsolutePath.StartsWith(shellUrl.AbsolutePath)) return;
                    var m=json.Deserialize<Dictionary<string,object>>(e.WebMessageAsJson);
                    if(m.ContainsKey("channel") && Convert.ToString(m["channel"])=="workdesk.window") {
                        string windowAction=m.ContainsKey("action")?Convert.ToString(m["action"]):"";
                        if(windowAction=="configure" && m.Count==3 && m.ContainsKey("close")) {
                            string value=Convert.ToString(m["close"]);
                            if(value=="always"||value=="keyboard"||value=="never")closePreference=value;
                        } else if(windowAction=="shortcut-close" && m.Count==2) CloseFromShortcut();
                        return;
                    }
                    if (!m.ContainsKey("channel")) return;
                    if ((string)m["channel"]=="workdesk.chat.account") {await HandleChatAccount(m);return;}
                    if ((string)m["channel"]!="workdesk.chat") return;
                    string action=Convert.ToString(m["action"]);
                    if (action=="hide") {wantsChat=false;HideChats();WriteReport();return;}
                    string provider=Convert.ToString(m["provider"]);
                    if (!sites.ContainsKey(provider)) return;
                    if (action=="appearance") {
                        string background,border;double backgroundOpacity,borderOpacity;
                        if(m.Count!=7 || !m.ContainsKey("background") || !m.ContainsKey("border") || !m.ContainsKey("backgroundOpacity") || !m.ContainsKey("borderOpacity") ||
                           !TryChannels(m["background"],out background) || !TryChannels(m["border"],out border) ||
                           !TryOpacity(m["backgroundOpacity"],out backgroundOpacity) || !TryOpacity(m["borderOpacity"],out borderOpacity))return;
                        appearances[provider]=new ChatAppearance {Background=background,BackgroundOpacity=backgroundOpacity,Border=border,BorderOpacity=borderOpacity};
                        if(chats.ContainsKey(provider) && chats[provider].Ready)await ApplyAppearance(chats[provider],provider);
                        return;
                    }
                    if (action=="reset") {if(chats.ContainsKey(provider)){var current=chats[provider];current.Ready=false;current.Status="loading";current.View.Visible=false;string start=Target(provider,null);if(current.View.Source!=null && current.View.Source.AbsoluteUri==start)current.View.Reload();else current.View.Source=new Uri(start);}return;}
                    if (action=="bounds") {UpdateBounds(m);LayoutSelected();return;}
                    if (action=="reload") {if(chats.ContainsKey(provider))chats[provider].View.Reload();else await ShowChat(provider,null);return;}
                    if (action!="show") return;
                    selected=provider;wantsChat=true;UpdateBounds(m);HideChats();
                    string requested=m.ContainsKey("url")?Convert.ToString(m["url"]):null;
                    await ShowChat(provider,requested);
                } catch(Exception exc) {Publish("error",exc.GetType().Name);}
            };
            await shell.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("if(location.origin==="+json.Serialize(shellUrl.GetLeftPart(UriPartial.Authority))+"){window.WorkdeskDesktop={engine:'WebView2',chatViews:true,chatAccounts:true,windowPreferences:true};}");
            shell.Source=shellUrl;WriteReport();
        } catch(Exception exc) {
            WriteFailure(exc);
            MessageBox.Show("Workdesk桌面视图未能启动。请查看本地桌面诊断记录。","Workdesk",MessageBoxButtons.OK,MessageBoxIcon.Error);
            Close();
        }
    }
    void UpdateBounds(Dictionary<string,object> m) {
        var b=(Dictionary<string,object>)m["bounds"];
        double ratio=m.ContainsKey("scale")?Convert.ToDouble(m["scale"],CultureInfo.InvariantCulture):1;
        ratio=Math.Max(.5,Math.Min(4,ratio));
        int x=Math.Max(0,(int)Math.Round(Convert.ToDouble(b["x"])*ratio));
        int y=Math.Max(0,(int)Math.Round(Convert.ToDouble(b["y"])*ratio));
        int w=Math.Max(0,(int)Math.Round(Convert.ToDouble(b["width"])*ratio));
        int h=Math.Max(0,(int)Math.Round(Convert.ToDouble(b["height"])*ratio));
        chatBounds=Rectangle.Intersect(new Rectangle(x,y,w,h),ClientRectangle);
    }
    void HideChats() {foreach(var c in chats.Values)c.View.Visible=false;}
    void LayoutSelected() {
        if (!chats.ContainsKey(selected)) return;
        var c=chats[selected];c.View.Bounds=chatBounds;
        c.View.Visible=wantsChat && c.Ready && chatBounds.Width>20 && chatBounds.Height>20;
        if(c.View.Visible)c.View.BringToFront();
    }
    string Target(string provider,string requested) {
        if(probeBase!=null)return probeBase.TrimEnd('/')+"/"+provider;
        if(String.IsNullOrEmpty(requested))return sites[provider];
        Uri uri;
        if (!Uri.TryCreate(requested,UriKind.Absolute,out uri) || uri.Scheme!="https" || uri.Host!=new Uri(sites[provider]).Host || !String.IsNullOrEmpty(uri.UserInfo) || !uri.IsDefaultPort) throw new ArgumentException("Official Chat URL required");
        return uri.AbsoluteUri;
    }
    async Task ShowChat(string provider,string requested) {
        try {await ShowChatCore(provider,requested);}
        catch {
            ChatView failed;
            if(chats.TryGetValue(provider,out failed) && failed.View.CoreWebView2==null) {chats.Remove(provider);failed.View.Dispose();}
            throw;
        }
    }
    async Task ShowChatCore(string provider,string requested) {
        string target=Target(provider,requested);
        ChatView c;
        if(chats.TryGetValue(provider,out c)) {
            if(c.View.CoreWebView2==null) {Publish("loading","");return;}
            if(!String.IsNullOrEmpty(requested) && c.View.Source.AbsoluteUri!=target) {c.Ready=false;c.View.Source=new Uri(target);}
            if(selected==provider){if(c.Ready)await ApplyAppearance(c,provider);LayoutSelected();Publish(c.Status,"");}
            return;
        }
        c=new ChatView {View=new WebView2()};chats.Add(provider,c);
        c.View.Visible=false;Controls.Add(c.View);c.View.Bounds=chatBounds;
        Publish("loading","");
        // Stable provider profiles survive UI URL/revision changes and normal
        // restarts. Never clear browsing data or copy cookies to the shell.
        var env=await OpenChatProfile(c.View,provider);
        var core=c.View.CoreWebView2;
        RestrictChatView(core,env,this);
        core.NavigationStarting+=(sender,e)=> {
            if(e.Cancel)return;
            c.Status="loading";if(selected==provider)Publish("loading","");
        };
        core.SourceChanged+=(sender,e)=> {Uri uri;if(Uri.TryCreate(core.Source,UriKind.Absolute,out uri))c.Host=uri.Host;WriteReport();};
        core.NavigationCompleted+=async (sender,e)=> {
            c.Ready=true;c.Status=e.IsSuccess?"ready":"error";
            if(e.IsSuccess)await ApplyAppearance(c,provider);
            if(selected==provider){LayoutSelected();Publish(c.Status,e.IsSuccess?"":e.WebErrorStatus.ToString());}
            WriteReport();
        };
        core.ProcessFailed+=(sender,e)=> {c.Status="error";c.Ready=false;if(selected==provider){LayoutSelected();Publish("error","WebViewProcessFailed");}WriteReport();};
        c.View.Source=new Uri(target);
        if(selected==provider)LayoutSelected();
    }
    async Task ApplyAppearance(ChatView c,string provider) {
        if(probeBase==null && c.Host!=new Uri(sites[provider]).Host)return;
        string rgb=provider=="codex"?"16,163,127":provider=="deepseek"?"77,107,254":"107,80,232";
        ChatAppearance color;
        if(!appearances.TryGetValue(provider,out color))color=new ChatAppearance {Background=rgb,BackgroundOpacity=.05,Border=rgb,BorderOpacity=.05};
        string opacity=color.BackgroundOpacity.ToString("R",CultureInfo.InvariantCulture),borderOpacity=color.BorderOpacity.ToString("R",CultureInfo.InvariantCulture);
        string script="(()=>{const id='workdesk-brand-tint',reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;let e=document.getElementById(id);const created=!e;if(!e){e=document.createElement('div');e.id=id;e.setAttribute('aria-hidden','true');document.documentElement.append(e);}e.style.cssText='position:fixed;inset:0;box-sizing:border-box;pointer-events:none;z-index:2147483646;background:rgba("+color.Background+","+opacity+");border:1px solid rgba("+color.Border+","+borderOpacity+");opacity:1;transition:background-color '+(reduce?'0':'220')+'ms ease';if(created&&!reduce)e.animate([{opacity:0},{opacity:1}],{duration:220,easing:'ease-out'});})()";
        try {await c.View.CoreWebView2.ExecuteScriptAsync(script);}catch(Exception) {}
    }
    static bool TryOpacity(object raw,out double value) {
        value=0;
        if(!(raw is int || raw is long || raw is double || raw is decimal))return false;
        value=Convert.ToDouble(raw,CultureInfo.InvariantCulture);
        return !Double.IsNaN(value) && !Double.IsInfinity(value) && value>=0 && value<=1;
    }
    static bool TryChannels(object raw,out string value) {
        value=null;var channels=raw as System.Collections.IList;
        if(channels==null || channels.Count!=3)return false;
        var result=new string[3];
        for(int i=0;i<3;i++) {
            var item=channels[i];if(!(item is int || item is long || item is double || item is decimal))return false;
            double n=Convert.ToDouble(item,CultureInfo.InvariantCulture);
            if(Double.IsNaN(n) || Double.IsInfinity(n) || n<0 || n>255 || n!=Math.Floor(n))return false;
            result[i]=((int)n).ToString(CultureInfo.InvariantCulture);
        }
        value=String.Join(",",result);return true;
    }
    // Test-only partial implementation is compiled into a separate fixture executable.
    partial void StartProbe();
    void Publish(string status,string error) {
        if(shell.CoreWebView2!=null)shell.CoreWebView2.PostWebMessageAsJson(json.Serialize(new {channel="workdesk.chat",provider=selected,status=status,error=error}));
        WriteReport();
    }
    static void OpenExternal(string value) {
        Uri uri;if(!Uri.TryCreate(value,UriKind.Absolute,out uri) || uri.Scheme!="https")return;
        Process.Start(new ProcessStartInfo(uri.AbsoluteUri) {UseShellExecute=true});
    }
    void WriteFailure(Exception exc) {if(reportPath!=null){Directory.CreateDirectory(Path.GetDirectoryName(reportPath));File.WriteAllText(reportPath,json.Serialize(new {engine="WebView2",status="error",error=exc.GetType().Name,message=exc.Message}));}}
    void WriteReport() {
        if(reportPath==null)return;
        try {
            var views=new Dictionary<string,object>();foreach(var pair in chats)views[pair.Key]=new {status=pair.Value.Status,host=pair.Value.Host,visible=pair.Value.View.Visible};
            Directory.CreateDirectory(Path.GetDirectoryName(reportPath));
            File.WriteAllText(reportPath,json.Serialize(new {engine="WebView2",title=Text,active=selected,chat_visible=wantsChat,views=views,width=ClientSize.Width,height=ClientSize.Height,window_width=Width,window_height=Height,pid=Process.GetCurrentProcess().Id,updated=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0,remote_host_objects=false,remote_web_messages=false}));
        }catch(IOException) {}
    }
}
