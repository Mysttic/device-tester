import ctypes, threading, time
from ctypes import wintypes
import device_tester as dt

# in-process injector (same message queue -> reliable)
u = ctypes.WinDLL("user32", use_last_error=True)
PUL = ctypes.POINTER(ctypes.c_ulong)
class KI(ctypes.Structure):
    _fields_=[("wVk",wintypes.WORD),("wScan",wintypes.WORD),("dwFlags",wintypes.DWORD),("time",wintypes.DWORD),("dwExtraInfo",PUL)]
class MI(ctypes.Structure):
    _fields_=[("dx",wintypes.LONG),("dy",wintypes.LONG),("mouseData",wintypes.DWORD),("dwFlags",wintypes.DWORD),("time",wintypes.DWORD),("dwExtraInfo",PUL)]
class II(ctypes.Union): _fields_=[("ki",KI),("mi",MI)]
class INPUT(ctypes.Structure): _fields_=[("type",wintypes.DWORD),("u",II)]
u.SendInput.argtypes=[wintypes.UINT,ctypes.POINTER(INPUT),ctypes.c_int]

def inject():
    time.sleep(0.6)
    for vk in (0x41, 0x1B, 0xAF):  # A, Esc, Volume Up
        for f in (0, 0x0002):
            i=INPUT(); i.type=1; i.u.ki=KI(vk,0,f,0,None)
            u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.03)
    # mouse wheel + left click (injected)
    i=INPUT(); i.type=0; i.u.mi=MI(0,0,120,0x0800,0,None)  # WHEEL, delta +120
    u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.03)
    for f in (0x0002,0x0004):  # left down/up
        i=INPUT(); i.type=0; i.u.mi=MI(0,0,0,f,0,None)
        u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.03)
    time.sleep(0.4)
    dt._running = False

hwnd = dt.create_message_window()
ok = dt.register_devices(hwnd)
print("registered usages:", ok, "/", len(dt.TARGET_USAGES))
threading.Thread(target=inject, daemon=True).start()
dt.message_loop()
print("loop exited cleanly")
