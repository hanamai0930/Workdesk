// Compiled only into the disposable test executable. All sites are local fixtures.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

internal sealed partial class WorkdeskHost {
    bool probeStarted;
    readonly List<Uri> externalProbeOpens=new List<Uri>();
    partial void StartProbe() {if(probeStarted)return;probeStarted=true;RunProbe();}
    partial void ProbeExternalOpen(Uri uri,ref bool handled) {externalProbeOpens.Add(uri);handled=true;}
    async Task WaitProbe(Func<bool> condition) {
        for(int i=0;i<160;i++){if(condition())return;await Task.Delay(100);}
        throw new Exception("Native view timeout");
    }
    async Task ShellProbe(string script) {await shell.CoreWebView2.ExecuteScriptAsync(script);await Task.Delay(250);}
    async Task ClickExternalFixture(string url,bool loginLink) {
        await ShellProbe("(()=>{document.querySelector('#probe-external-link')?.remove();const a=document.createElement('a');a.id='probe-external-link';a.href="+json.Serialize(url)+
            ";a.target='_blank';a.rel='noopener noreferrer';a.className="+json.Serialize(loginLink?"wd-partners-login-link":"")+
            ";a.textContent='Local login fixture';a.style='position:fixed;left:20px;top:40px;z-index:2147483647;padding:20px;background:white';document.body.append(a);})()");
        await shell.CoreWebView2.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent","{\"type\":\"mousePressed\",\"x\":70,\"y\":65,\"button\":\"left\",\"clickCount\":1}");
        await shell.CoreWebView2.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent","{\"type\":\"mouseReleased\",\"x\":70,\"y\":65,\"button\":\"left\",\"clickCount\":1}");
    }
    async Task ProbeWorkLogin(List<string> checks) {
        int port=shellUrl.Port==32123?32124:32123;
        string login="http://127.0.0.1:"+port+"/?token=local_fixture_token_not_a_secret";
        string invalid="http://127.0.0.1:"+port+"/not-a-login?token=local_fixture_token_not_a_secret";
        await ShellProbe("location.hash='#connect'");
        await ClickExternalFixture(login,false);
        await Task.Delay(300);
        if(externalProbeOpens.Count!=0)throw new Exception("Unmarked local link reached the system browser launcher");
        await ClickExternalFixture(invalid,true);
        await Task.Delay(300);
        if(externalProbeOpens.Count!=0)throw new Exception("Rejected local route reached the system browser launcher");
        await ClickExternalFixture(login,true);
        await WaitProbe(()=>externalProbeOpens.Count==1);
        if(externalProbeOpens[0].AbsoluteUri!=login)throw new Exception("Official login URL changed before browser handoff");
        await ShellProbe("location.hash='#settings/accounts/deepseek'");
        await ClickExternalFixture(login,true);
        await WaitProbe(()=>externalProbeOpens.Count==2);
        if(externalProbeOpens[1].AbsoluteUri!=login)throw new Exception("Settings login URL changed before browser handoff");
        await ShellProbe("location.hash='#settings'");
        await ClickExternalFixture(login,true);
        await Task.Delay(300);
        if(externalProbeOpens.Count!=2)throw new Exception("Local login can open outside the account UI");
        await ShellProbe("document.querySelector('#probe-external-link')?.remove()");
        await ShellProbe("location.hash='#connect'");
        await ShellProbe("window.workLoginProbe=[];chrome.webview.addEventListener('message',e=>{if(e.data?.channel==='workdesk.work.login')window.workLoginProbe.push(e.data);});"+
            "const fixtureLogin=document.createElement('a');fixtureLogin.id='probe-external-link';fixtureLogin.className='wd-partners-login-link';fixtureLogin.dataset.workLoginProvider='deepseek';fixtureLogin.dataset.workLoginManaged='true';"+
            "fixtureLogin.href="+json.Serialize(login)+";fixtureLogin.textContent='Local login fixture';document.body.append(fixtureLogin);");
        await ShellProbe("chrome.webview.postMessage({channel:'workdesk.work.login',action:'open',requestId:'fixture_wrong',provider:'codex',url:fixtureLogin.href});"+
            "chrome.webview.postMessage({channel:'workdesk.work.login',action:'open',requestId:'fixture_right',provider:'deepseek',url:fixtureLogin.href});");
        await WaitProbe(()=>externalProbeOpens.Count==3);
        if(externalProbeOpens[2].AbsoluteUri!=login || await shell.CoreWebView2.ExecuteScriptAsync(
            "workLoginProbe.some(e=>e.requestId==='fixture_wrong'&&e.state==='FAILED') && workLoginProbe.some(e=>e.requestId==='fixture_right'&&e.state==='OPENED')")!="true")
            throw new Exception("Work login did not acknowledge the exact browser handoff or reject a mismatched partner");
        checks.Add("user-clicked exact official Work login opens from connector and settings; unrelated local paths and links stay blocked");
    }
    async Task ProbeChatLoginFailure(List<string> checks) {
        await ShellProbe("chrome.webview.postMessage({channel:'workdesk.chat.account',action:'open',provider:'codex'});");
        try {await WaitProbe(()=>chatLogins.ContainsKey("codex") && chatLoginStates["codex"]=="OPENED");}
        catch(Exception) {throw new Exception("Chat login did not report first load");}
        var login=chatLogins["codex"];
        var control=login.Controls.OfType<TableLayoutPanel>().Single();
        var confirm=control.Controls.OfType<Button>().Single();
        var retry=control.Controls.OfType<FlowLayoutPanel>().Single().Controls.OfType<LinkLabel>().First();
        login.Controls.OfType<WebView2>().Single().CoreWebView2.Navigate(probeBase.TrimEnd('/')+"/unavailable");
        try {await WaitProbe(()=>chatLoginStates["codex"]=="LOAD_FAILED");}
        catch(Exception) {throw new Exception("Chat login did not recognize the HTTP failure");}
        if(confirm.Enabled || !retry.Visible)throw new Exception("HTTP failure was shown as a usable Chat login");
        typeof(LinkLabel).GetMethod("OnLinkClicked",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(retry,new object[]{new LinkLabelLinkClickedEventArgs(retry.Links[0])});
        try {await WaitProbe(()=>chatLoginStates["codex"]=="OPENED" && confirm.Enabled);}
        catch(Exception) {throw new Exception("Chat login did not recover after reload");}
        login.Close();
        if(await shell.CoreWebView2.ExecuteScriptAsync("chatAccountProbe.some(e=>e.provider==='codex'&&e.state==='LOAD_FAILED')")!="true")
            throw new Exception("Chat Login failure not reported to the connector");
        checks.Add("Chat login separates opening from loaded, reports an HTTP failure and recovers using the in-window reload");
    }
    async void RunProbe() {
        var checks=new List<string>();string error=null;
        try {
            bool mounted=false;
            for(int i=0;i<100;i++) {
                mounted=await shell.CoreWebView2.ExecuteScriptAsync("!!document.querySelector('#wb-chat-route')")=="true";
                if(mounted)break;await Task.Delay(100);
            }
            if(!mounted)throw new Exception("Shell did not mount");
            await ShellProbe("(()=>{const s=document.querySelector('#wb-chat-route');s.value='web';s.dispatchEvent(new Event('change',{bubbles:true}));})()");
            await ShellProbe("(()=>{const s=document.querySelector('#wb-chat-provider');s.value='codex';s.dispatchEvent(new Event('change',{bubbles:true}));})()");
            await ShellProbe("document.querySelector('[data-wb=chat-enter]').click()");
            await ShellProbe("window.chatAccountProbe=[];chrome.webview.addEventListener('message',e=>{if(e.data?.channel==='workdesk.chat.account')window.chatAccountProbe.push(e.data);});");
            foreach(string provider in new[]{"codex","deepseek","qwen"}) {
                if(provider!="codex")await ShellProbe("(()=>{const s=document.querySelector('#wb-chat-provider');s.value='"+provider+"';s.dispatchEvent(new Event('change',{bubbles:true}));})()");
                await WaitProbe(()=>selected==provider && chats.ContainsKey(provider) && chats[provider].Ready && chats[provider].View.Visible);
                var c=chats[provider];
                if(c.Status!="ready")throw new Exception(provider+" failed to load");
                if(c.View.CoreWebView2.Profile.IsInPrivateModeEnabled)throw new Exception("Private profile cannot retain logins");
                bool restore=File.Exists(Path.Combine(Path.GetDirectoryName(reportPath),"restore-profile"));
                string persisted=await c.View.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('workdesk-login-fixture')==='"+provider+"' && document.cookie.includes('workdesk_persist="+provider+"')");
                if(restore && persisted!="true")throw new Exception("Profile did not survive restart: "+provider);
                if(!restore)await c.View.CoreWebView2.ExecuteScriptAsync("localStorage.setItem('workdesk-login-fixture','"+provider+"');document.cookie='workdesk_persist="+provider+"; Max-Age=86400; Path=/; SameSite=Lax'");
                checks.Add(provider+(restore?": persistent cookie and local storage restored across process restart":": persistent profile seeded without reading any real login"));
                if(ChatConfirmed(provider)!=restore)throw new Exception("Confirmation state before opening account: "+provider);
                await ShellProbe("chrome.webview.postMessage({channel:'workdesk.chat.account',action:'open',provider:'"+provider+"'});");
                await WaitProbe(()=>chatLogins.ContainsKey(provider) && chatLogins[provider].Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<Button>().Single().Enabled);
                var login=chatLogins[provider];
                var loginView=login.Controls.OfType<WebView2>().Single();
                var confirm=login.Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<Button>().Single();
                if(loginView.CoreWebView2.Settings.AreHostObjectsAllowed || loginView.CoreWebView2.Settings.IsWebMessageEnabled)
                    throw new Exception("Chat account site received desktop messaging");
                if(await loginView.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('workdesk-login-fixture')==='"+provider+"' && document.cookie.includes('workdesk_persist="+provider+"')")!="true")
                    throw new Exception("Account window does not share official Chat profile: "+provider);
                if(!restore && ChatConfirmed(provider))throw new Exception("Loading the site marked Chat connected");
                confirm.PerformClick();
                await WaitProbe(()=>!chatLogins.ContainsKey(provider) && ChatConfirmed(provider));
                string confirmed=await shell.CoreWebView2.ExecuteScriptAsync("chatAccountProbe.some(e=>e.state==='CONFIRMED'&&e.provider==='"+provider+"'&&e.connections['"+provider+"']===true)");
                if(confirmed!="true")throw new Exception("Chat account bridge did not report confirmation: "+provider);
                checks.Add(provider+": official login launcher shares Chat profile and publishes user confirmation");
                if(c.View.CoreWebView2.Settings.AreHostObjectsAllowed || c.View.CoreWebView2.Settings.IsWebMessageEnabled)throw new Exception("Remote bridge enabled");
                string result=await c.View.CoreWebView2.ExecuteScriptAsync("(()=>{const e=document.querySelector('input');e.value='hello local fixture';document.querySelector('button').click();return document.querySelector('output').textContent;})()");
                if(result!="\"local fixture received: hello local fixture\"")throw new Exception("Input failed for "+provider);
                if(c.View.Bounds.Width<500 || c.View.Bounds.Height<300 || c.View.Bounds.X<200 || c.View.Bounds.Y<100)throw new Exception("Child bounds invalid");
                checks.Add(provider+": actual WebView2 loads, accepts input, and stays inside the main chat region");
            }
            foreach(string provider in new[]{"claude","grok","gemini","kimi","glm"}) {
                bool restore=File.Exists(Path.Combine(Path.GetDirectoryName(reportPath),"restore-profile"));
                await ShellProbe("chrome.webview.postMessage({channel:'workdesk.chat.account',action:'open',provider:'"+provider+"'});");
                await WaitProbe(()=>chatLogins.ContainsKey(provider) && chatLogins[provider].Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<Button>().Single().Enabled);
                var login=chatLogins[provider];
                var view=login.Controls.OfType<WebView2>().Single();
                if(view.CoreWebView2.Settings.AreHostObjectsAllowed || view.CoreWebView2.Settings.IsWebMessageEnabled || view.CoreWebView2.Profile.IsInPrivateModeEnabled)
                    throw new Exception("Unsafe or private Chat account profile: "+provider);
                string saved=await view.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('workdesk-login-fixture')==='"+provider+"'");
                if(restore && saved!="true")throw new Exception("New account profile did not persist: "+provider);
                if(!restore)await view.CoreWebView2.ExecuteScriptAsync("localStorage.setItem('workdesk-login-fixture','"+provider+"')");
                if(ChatConfirmed(provider)!=restore)throw new Exception("Website view incorrectly inferred authentication: "+provider);
                login.Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<Button>().Single().PerformClick();
                await WaitProbe(()=>!chatLogins.ContainsKey(provider) && ChatConfirmed(provider));
                checks.Add(provider+": official website account launcher, isolated persistent profile and explicit confirmation pass with local pages");
            }
            await ShellProbe("document.querySelector('[data-wb=new]').click()");
            await ShellProbe("document.querySelector('[data-wb=chat-enter]').click()");
            await WaitProbe(()=>chats["qwen"].Ready && chats["qwen"].View.Visible);
            if(await chats["qwen"].View.CoreWebView2.ExecuteScriptAsync("document.querySelector('input').value")!="\"\"")throw new Exception("New chat did not return to the official start page");
            checks.Add("new Chat entry resets the current provider to its official start page");
            await ShellProbe("(()=>{const d=document.createElement('dialog');d.id='local-probe-dialog';document.body.append(d);d.showModal();})()");
            await WaitProbe(()=>!chats[selected].View.Visible);
            await ShellProbe("document.querySelector('#local-probe-dialog').close()");
            await WaitProbe(()=>chats[selected].View.Visible);
            checks.Add("native child hides for a shell modal and returns after dismissal");
            await ShellProbe("document.querySelector('[data-wb-menu=conversation]').click()");
            await WaitProbe(()=>!chats[selected].View.Visible);
            await ShellProbe("document.querySelector('[data-wb-menu=conversation]').click()");
            await WaitProbe(()=>chats[selected].View.Visible);
            checks.Add("conversation menu stays above native content and dismisses cleanly");
            await ShellProbe("location.hash='#settings'");await WaitProbe(()=>!wantsChat);
            foreach(var c in chats.Values)if(c.View.Visible)throw new Exception("Remote view covers settings");
            checks.Add("settings navigation hides every native chat view");
            await ProbeWorkLogin(checks);
            await ProbeChatLoginFailure(checks);
            checks.Add("remote pages have neither host objects nor web-message access");
        }catch(Exception exc){error=exc.Message;}
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(reportPath),"probe-result.json"),json.Serialize(new {checks=checks,error=error,local_fixtures=true,model_requests=0}));
        Close();
    }
}
