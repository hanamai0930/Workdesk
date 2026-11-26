// Compiled only into the disposable test executable. All sites are local fixtures.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

internal sealed partial class WorkdeskHost {
    bool probeStarted;
    partial void StartProbe() {if(probeStarted)return;probeStarted=true;RunProbe();}
    async Task WaitProbe(Func<bool> condition) {
        for(int i=0;i<160;i++){if(condition())return;await Task.Delay(100);}
        throw new Exception("Native view timeout");
    }
    async Task ShellProbe(string script) {await shell.CoreWebView2.ExecuteScriptAsync(script);await Task.Delay(250);}
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
            checks.Add("remote pages have neither host objects nor web-message access");
        }catch(Exception exc){error=exc.Message;}
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(reportPath),"probe-result.json"),json.Serialize(new {checks=checks,error=error,local_fixtures=true,model_requests=0}));
        Close();
    }
}
