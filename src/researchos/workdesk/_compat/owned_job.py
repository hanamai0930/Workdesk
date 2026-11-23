"""Windows Job Object for the helper and its SDK-owned descendants only."""
import ctypes as c
from ctypes import wintypes as w
import time


class BASIC_LIMIT(c.Structure):
    _fields_=[('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),('LimitFlags',w.DWORD),
              ('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),('ActiveProcessLimit',w.DWORD),
              ('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]


class IO_COUNTERS(c.Structure):
    _fields_=[(x,c.c_ulonglong) for x in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class EXTENDED_LIMIT(c.Structure):
    _fields_=[('BasicLimitInformation',BASIC_LIMIT),('IoInfo',IO_COUNTERS),('ProcessMemoryLimit',c.c_size_t),
              ('JobMemoryLimit',c.c_size_t),('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]


class ACCOUNTING(c.Structure):
    _fields_=[(x,c.c_longlong) for x in ('TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime')]+[(x,w.DWORD) for x in ('TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses')]


class OwnedJob:
    def __init__(self,proc):
        self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]
        self.kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle: raise c.WinError(c.get_last_error())
        limits=EXTENDED_LIMIT();limits.BasicLimitInformation.LimitFlags=0x2000
        if not self.kernel.SetInformationJobObject(self.handle,9,c.byref(limits),c.sizeof(limits)):
            self.kernel.CloseHandle(self.handle);raise c.WinError(c.get_last_error())
        if not self.kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(proc._handle))):
            self.kernel.CloseHandle(self.handle);raise c.WinError(c.get_last_error())

    def active(self):
        value=ACCOUNTING()
        if not self.kernel.QueryInformationJobObject(self.handle,1,c.byref(value),c.sizeof(value),None): raise c.WinError(c.get_last_error())
        return value.ActiveProcesses

    def close(self):
        forced=self.active()>0
        if forced and not self.kernel.TerminateJobObject(self.handle,137): raise c.WinError(c.get_last_error())
        deadline=time.monotonic()+5
        while self.active()>0 and time.monotonic()<deadline: time.sleep(.05)
        result={'active_processes':self.active(),'forced_owned_job_cleanup':forced}
        self.kernel.CloseHandle(self.handle);self.handle=None
        return result
