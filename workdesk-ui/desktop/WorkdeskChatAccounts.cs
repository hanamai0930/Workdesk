using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed partial class WorkdeskHost {
    readonly Dictionary<string,Form> chatLogins = new Dictionary<string,Form>();
    readonly Dictionary<string,string> chatLoginStates = new Dictionary<string,string>();
    readonly Dictionary<string,string> chatLoginNotes = new Dictionary<string,string>();
    readonly Dictionary<string,string> chatNames = new Dictionary<string,string> {
        {"codex","ChatGPT"}, {"deepseek","DeepSeek"}, {"qwen","Qwen"},
        {"claude","Claude"}, {"grok","Grok"}, {"gemini","Gemini"}, {"kimi","Kimi"}, {"glm","GLM"}
    };

    string ChatProfile(string provider) {
        return Path.Combine(userRoot,"browser-profiles","official-chat",provider);
    }
    static Task UseChatProfile(WebView2 view,CoreWebView2Environment env) {
        var profile=env.CreateCoreWebView2ControllerOptions();
        profile.ProfileName="Default";
        profile.IsInPrivateModeEnabled=false;
        return view.EnsureCoreWebView2Async(env,profile);
    }
    async Task<CoreWebView2Environment> OpenChatProfile(WebView2 view,string provider) {
        var env=await CoreWebView2Environment.CreateAsync(null,ChatProfile(provider));
        await UseChatProfile(view,env);
        return env;
    }
    bool TryChatNavigation(string value,out Uri uri) {
        return Uri.TryCreate(value,UriKind.Absolute,out uri) && String.IsNullOrEmpty(uri.UserInfo) &&
            (uri.Scheme=="https" || probeBase!=null && uri.Scheme=="http" && uri.Host=="127.0.0.1" && uri.Port==shellUrl.Port);
    }
    void RestrictChatView(CoreWebView2 core,CoreWebView2Environment env,Form owner) {
        core.Settings.AreHostObjectsAllowed=false;
        core.Settings.IsWebMessageEnabled=false;
        core.NavigationStarting+=(sender,e)=>{
            Uri uri;
            if(!TryChatNavigation(e.Uri,out uri))e.Cancel=true;
        };
        core.NewWindowRequested+=async (sender,e)=>{
            var deferral=e.GetDeferral();
            Form popup=null;
            try {
                e.Handled=true;
                Uri uri;
                if(!TryChatNavigation(e.Uri,out uri))return;
                popup=new Form {Text="官方聊天 · "+uri.Host,Width=1000,Height=800,StartPosition=FormStartPosition.CenterParent};
                var view=new WebView2 {Dock=DockStyle.Fill};
                popup.Controls.Add(view);
                popup.Show(owner);
                // Auth popups share the opener's provider profile. Closing a
                // window only closes its view, leaving browser sessions intact.
                await UseChatProfile(view,env);
                RestrictChatView(view.CoreWebView2,env,popup);
                view.CoreWebView2.WindowCloseRequested+=(s,args)=>popup.Close();
                e.NewWindow=view.CoreWebView2;
            } catch (Exception) {
                if(popup!=null)popup.Close();
            } finally {deferral.Complete();}
        };
    }
    string ChatConfirmation(string provider) {
        return Path.Combine(ChatProfile(provider),"workdesk-confirmed.json");
    }
    bool ChatConfirmed(string provider) {
        try {
            var file=new FileInfo(ChatConfirmation(provider));
            if(!file.Exists || file.Length>256)return false;
            var record=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file.FullName));
            return record!=null && record.Count==3 && record.ContainsKey("schema") && Equals(record["schema"],1)
                && record.ContainsKey("provider") && Equals(record["provider"],provider)
                && record.ContainsKey("confirmed") && Equals(record["confirmed"],true);
        } catch (IOException) {return false;}
        catch (UnauthorizedAccessException) {return false;}
        catch (ArgumentException) {return false;}
    }
    void RememberChatConfirmation(string provider) {
        string target=ChatConfirmation(provider);
        string temp=target+"."+Guid.NewGuid().ToString("N")+".tmp";
        try {
            File.WriteAllText(temp,json.Serialize(new {schema=1,provider=provider,confirmed=true}));
            if(File.Exists(target))File.Replace(temp,target,null);
            else File.Move(temp,target);
        } catch (IOException) {
            if(!ChatConfirmed(provider))throw;
        } finally {
            if(File.Exists(temp))File.Delete(temp);
        }
    }
    void PublishChatAccount(string provider,string state,string message="") {
        if(chatLogins.ContainsKey(provider))chatLoginNotes[provider]=message;
        if(IsDisposed || shell.CoreWebView2==null)return;
        var connections=new Dictionary<string,bool>();
        foreach(string id in sites.Keys)connections[id]=ChatConfirmed(id);
        shell.CoreWebView2.PostWebMessageAsJson(json.Serialize(new {
            channel="workdesk.chat.account",provider=provider,state=state,message=message,connections=connections
        }));
    }
    async Task HandleChatAccount(Dictionary<string,object> message) {
        object action,id;
        if(!message.TryGetValue("action",out action))return;
        if(Equals(action,"status") && message.Count==2) {
            PublishChatAccount("","STATUS");
            return;
        }
        if(!Equals(action,"open") || message.Count!=3 || !message.TryGetValue("provider",out id) || !(id is string))return;
        string provider=(string)id;
        if(!sites.ContainsKey(provider))return;
        try {await OpenChatAccount(provider);}
        catch (Exception) {PublishChatAccount(provider,"FAILED","无法打开官方 Chat 登录，请重试。");}
    }
    async Task OpenChatAccount(string provider) {
        Form existing;
        if(chatLogins.TryGetValue(provider,out existing)) {
            existing.Activate();
            string note;
            chatLoginNotes.TryGetValue(provider,out note);
            PublishChatAccount(provider,chatLoginStates[provider],note??"");
            return;
        }
        var popup=new Form {Text=chatNames[provider]+" · Chat 账号",Width=1000,Height=825,MinimumSize=new Size(575,400),
            StartPosition=FormStartPosition.CenterParent};
        var view=new WebView2 {Dock=DockStyle.Fill};
        var controls=new TableLayoutPanel {Dock=DockStyle.Bottom,Height=57,ColumnCount=3,RowCount=1,
            Padding=new Padding(16,6,16,6),BackColor=SystemColors.Control};
        controls.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        controls.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        controls.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var label=new Label {AutoSize=true,Anchor=AnchorStyles.Left,Text="正在打开官方网页…"};
        var confirm=new Button {AutoSize=true,Enabled=false,Anchor=AnchorStyles.Right,Text="我已完成登录"};
        var retry=new LinkLabel {AutoSize=true,Visible=false,Anchor=AnchorStyles.Right,Text="重新加载"};
        var openBrowser=new LinkLabel {AutoSize=true,Visible=false,Anchor=AnchorStyles.Right,Text="用浏览器打开"};
        var recovery=new FlowLayoutPanel {AutoSize=true,WrapContents=false,Anchor=AnchorStyles.Right};
        recovery.Controls.Add(retry);
        recovery.Controls.Add(openBrowser);
        var chinaSite=new LinkLabel {AutoSize=true,Visible=provider=="glm" && probeBase==null,Anchor=AnchorStyles.Right,Text="打开中国站"};
        recovery.Controls.Add(chinaSite);
        confirm.Padding=new Padding(10,3,10,3);
        controls.Controls.Add(label,0,0);
        controls.Controls.Add(recovery,1,0);
        controls.Controls.Add(confirm,2,0);
        popup.Controls.Add(view);
        popup.Controls.Add(controls);
        chatLogins.Add(provider,popup);
        chatLoginStates.Add(provider,"OPENING");
        bool confirmed=false,failed=false;
        var timer=new Timer {Interval=20000};
        Action<string> loadFailed=message=>{
            timer.Stop();
            confirm.Enabled=false;
            retry.Visible=view.CoreWebView2!=null;
            openBrowser.Visible=probeBase==null;
            label.Text=message;
            chatLoginStates[provider]="LOAD_FAILED";
            PublishChatAccount(provider,"LOAD_FAILED",message);
        };
        timer.Tick+=(sender,e)=>loadFailed(view.CoreWebView2==null?"登录窗口启动较慢，可关闭后重试。":"官方网页加载较慢，可重新加载。");
        popup.FormClosed+=(sender,e)=>{
            timer.Dispose();
            chatLogins.Remove(provider);
            chatLoginStates.Remove(provider);
            chatLoginNotes.Remove(provider);
            if(!confirmed && !failed)PublishChatAccount(provider,"CLOSED");
        };
        popup.Show(this);
        PublishChatAccount(provider,"OPENING");
        timer.Start();
        try {
            string target=Target(provider,null);
            Uri official=new Uri(target);
            openBrowser.LinkClicked+=(sender,e)=>{
                try {
                    OpenExternal(official);
                    label.Text="已交给默认浏览器，可在浏览器中查看官网。";
                } catch(Exception) {loadFailed("暂时无法打开默认浏览器，请检查后重试。");}
            };
            var env=await OpenChatProfile(view,provider);
            if(popup.IsDisposed)return;
            var core=view.CoreWebView2;
            RestrictChatView(core,env,popup);
            if(provider=="glm" && probeBase==null)chinaSite.LinkClicked+=(sender,e)=>{
                official=new Uri("https://zchat.z.ai/");
                target=official.AbsoluteUri;
                try {core.Navigate(target);}
                catch(Exception) {loadFailed("暂时无法打开中国站，可重新加载。");}
            };
            ulong navigation=0;
            core.NavigationStarting+=(sender,e)=>{
                if(popup.IsDisposed || e.Cancel)return;
                if(navigation==e.NavigationId)return;
                navigation=e.NavigationId;
                confirm.Enabled=false;retry.Visible=false;openBrowser.Visible=false;
                label.Text="正在打开官方网页…";
                if(chatLoginStates[provider]!="OPENING") {
                    chatLoginStates[provider]="OPENING";
                    PublishChatAccount(provider,"OPENING");
                }
                timer.Stop();timer.Start();
            };
            core.NavigationCompleted+=(sender,e)=>{
                if(popup.IsDisposed || e.NavigationId!=navigation)return;
                timer.Stop();
                Uri current;
                if(!e.IsSuccess || e.HttpStatusCode>=400 || !TryChatNavigation(core.Source,out current)) {
                    loadFailed(e.HttpStatusCode==403?"官网暂未允许此窗口加载，可重新加载。":
                        e.HttpStatusCode==429?"官网请求较多，请稍后重新加载。":"官方网页暂时无法连接，可重新加载。");
                    return;
                }
                if(provider=="claude" && current.Host=="claude.com" && current.AbsolutePath.TrimEnd('/')=="/app-unavailable-in-region") {
                    loadFailed("官网提示当前地区暂不可用。");
                    return;
                }
                retry.Visible=false;openBrowser.Visible=false;
                confirm.Enabled=current.GetLeftPart(UriPartial.Authority)==official.GetLeftPart(UriPartial.Authority) ||
                    provider=="glm" && current.Scheme=="https" && current.IsDefaultPort && (current.Host=="chat.z.ai" || current.Host=="zchat.z.ai");
                label.Text=confirm.Enabled?"当前网页 "+current.Host+" · 请在完成登录后确认":"当前网页 "+current.Host+" · 登录后请返回官方聊天";
                chatLoginStates[provider]="OPENED";
                PublishChatAccount(provider,"OPENED","官网页面已打开，登录后请在窗口内确认。");
            };
            core.ProcessFailed+=(sender,e)=>{
                if(!popup.IsDisposed)loadFailed("官方网页暂时无法加载，可重新加载。");
            };
            retry.LinkClicked+=(sender,e)=>{
                try {core.Navigate(target);}
                catch(Exception) {loadFailed("暂时无法重新加载，请关闭窗口后重试。");}
            };
            confirm.Click+=(sender,e)=>{
                try {
                    RememberChatConfirmation(provider);
                    confirmed=true;
                    PublishChatAccount(provider,"CONFIRMED");
                    popup.Close();
                } catch (Exception) {
                    label.Text="暂时无法保存登录记录，请重试。";
                    PublishChatAccount(provider,"FAILED",label.Text);
                }
            };
            view.Source=official;
        } catch (Exception) {
            if(popup.IsDisposed)return;
            failed=true;
            popup.Close();
            throw;
        }
    }
}
