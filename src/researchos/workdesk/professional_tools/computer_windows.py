"""Bind operations to one Win32 top-level window, process lifetime and child handle."""
import ctypes
from ctypes import wintypes
from .computer_surface import OutcomeUnknown

LIMIT = 160

def native():
    import win32gui as ui
    import win32process
    import win32api
    return ui,win32process,win32api


def identity(hwnd):
    ui,proc,api=native()
    pid=proc.GetWindowThreadProcessId(hwnd)[1]
    process=api.OpenProcess(0x1000,False,pid)
    try:
        from ctypes import byref
        times=[wintypes.FILETIME() for _ in range(4)]
        if not ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(int(process)),*[byref(v) for v in times]):
            raise ctypes.WinError()
        created=(times[0].dwHighDateTime<<32) | times[0].dwLowDateTime
    finally:process.Close()
    return dict(hwnd=hwnd,pid=pid,process_start=str(created),title=ui.GetWindowText(hwnd),class_name=ui.GetClassName(hwnd))


def validated(target):
    ui,_,_=native()
    if not isinstance(target,dict) or type(target.get('hwnd'))!=int:
        raise ValueError('先按准确标题检查目标窗口。')
    hwnd=target['hwnd']
    if not ui.IsWindow(hwnd) or not ui.IsWindowVisible(hwnd) or ui.GetAncestor(hwnd,2)!=hwnd or identity(hwnd)!=target:
        raise ValueError('目标窗口的标题、进程或身份已变化。')
    return hwnd


def elements(hwnd):
    ui,_,_=native()
    controls=[]
    sensitive=False
    def collect(child,_):
        nonlocal sensitive
        if not ui.IsWindowVisible(child):return True
        name=ui.GetClassName(child)
        # Native password edit controls must not be read or individually captured.
        style=ui.GetWindowLong(child,-16)
        if name.lower()=='edit' and style&0x20:
            sensitive=True
            return True
        actions=[]
        if name.lower()=='edit' and not style&0x800:actions=['desktop.set']
        elif name.lower()=='button' and style&15 in (0,1,2,3,4,5,6,9):actions=['desktop.invoke']
        if actions and ui.IsWindowEnabled(child):
            controls.append(dict(control_id=ui.GetDlgCtrlID(child),control_hwnd=child,class_name=name,actions=actions))
        if len(controls)>LIMIT:raise ValueError('可操作控件数量超过本次检查上限。')
        return True
    ui.EnumChildWindows(hwnd,collect,None)
    return controls,sensitive


def capture(hwnd,path):
    import win32ui
    from PIL import Image
    ui,_,_=native()
    left,top,right,bottom=ui.GetWindowRect(hwnd)
    width,height=right-left,bottom-top
    if not (0<width<=8192 and 0<height<=8192 and width*height<=16000000):
        raise ValueError('窗口尺寸不适合截图。')
    dc=ui.GetWindowDC(hwnd)
    try:
        source=win32ui.CreateDCFromHandle(dc)
        dest=source.CreateCompatibleDC()
        bmp=win32ui.CreateBitmap()
        try:
            bmp.CreateCompatibleBitmap(source,width,height)
            old=dest.SelectObject(bmp)
            try:
                ok=ctypes.windll.user32.PrintWindow(wintypes.HWND(hwnd),wintypes.HDC(dest.GetSafeHdc()),2)
                if not ok:raise ValueError('应用未提供指定窗口的图像。')
                Image.frombytes('RGB',(width,height),bmp.GetBitmapBits(True),'raw','BGRX').save(path)
            finally:dest.SelectObject(old)
        finally:
            dest.DeleteDC()
            if bmp.GetHandle():ui.DeleteObject(bmp.GetHandle())
            # CreateDCFromHandle wraps a borrowed window DC. Release it below.
    finally:ui.ReleaseDC(hwnd,dc)


def send(hwnd,msg,wp,lp):
    result=ctypes.c_size_t()
    fn=ctypes.windll.user32.SendMessageTimeoutW
    fn.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.POINTER(ctypes.c_size_t)]
    fn.restype=wintypes.LPARAM
    if not fn(hwnd,msg,wp,lp,2,2500,ctypes.byref(result)):
        raise OutcomeUnknown('操作回执未知，先回看窗口再决定后续操作。')
    return result.value


def operate(p,root):
    ui,proc,_=native()
    act=p['action']
    files=[]
    if act=='desktop.inspect':
        title=p.get('window_title')
        if not isinstance(title,str) or not 1<=len(title)<=512:raise ValueError('请输入准确窗口标题。')
        found=[]
        def match(w,_):
            if ui.IsWindowVisible(w) and ui.GetWindowText(w)==title:found.append(w)
            return True
        ui.EnumWindows(match,None)
        if len(found)!=1:raise ValueError('没有找到标题唯一匹配的可见窗口。')
        target=identity(found[0])
    else:target=p.get('target')
    hwnd=validated(target)
    controls,sensitive=elements(hwnd)
    if act=='desktop.inspect':
        return {'summary':'已检查准确窗口和可操作的标准控件。','target':target,'controls':controls},[]
    if act=='desktop.screenshot':
        if sensitive:raise ValueError('目标包含可见密码框，不采集图像。')
        validated(target)
        path=root/'window.png'
        capture(hwnd,path)
        validated(target)
        return {'summary':'已保存指定窗口的图像。','target':target},[(path,'image/png')]
    if act not in ('desktop.set','desktop.invoke'):raise ValueError('桌面操作无效。')
    matched=[x for x in controls if x['control_id']==p.get('control_id') and x['control_hwnd']==p.get('control_hwnd') and act in x['actions']]
    if len(matched)!=1:raise ValueError('指定控件已变化，或不支持本次动作。')
    control=matched[0]
    child=control['control_hwnd']
    if not ui.IsChild(validated(target),child) or proc.GetWindowThreadProcessId(child)[1]!=target['pid'] or ui.GetClassName(child)!=control['class_name']:
        raise ValueError('目标控件不再属于已授权窗口。')
    if act=='desktop.set':
        text=p.get('text')
        if not isinstance(text,str) or len(text)>4096 or '\x00' in text:raise ValueError('请输入最多 4096 字的普通文本。')
        if ui.GetWindowLong(child,-16)&(0x20|0x800):raise ValueError('控件只读或受保护。')
        data=ctypes.create_unicode_buffer(text)
        if not send(child,0x000c,0,ctypes.addressof(data)):raise ValueError('窗口未接受文本。')
        returned=ctypes.create_unicode_buffer(len(data)+2)
        send(child,0x000d,len(returned),ctypes.addressof(returned))
        if returned.value!=text:raise OutcomeUnknown('控件回读与提交内容不符，先核对目标窗口。')
        summary='已向指定编辑框写入文本并回读核对。'
    else:
        send(child,0x00f5,0,0)
        summary='指定按钮已处理本次点击消息；业务结果由原应用提供。'
    return {'summary':summary,'target':target,'control':control},files
