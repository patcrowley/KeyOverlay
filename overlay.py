#!/usr/bin/env python3
"""Key Press Overlay — hotkey-triggered always-on-top screen overlays."""

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
import ctypes, ctypes.wintypes
import json, os, sys, threading, time, uuid
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# ── Crash log (windowed exe has no console) ───────────────────────────────────
_LOG = Path(os.environ.get("APPDATA", Path.home())) / "KeyOverlay" / "error.log"
_LOG.parent.mkdir(exist_ok=True)
if FROZEN or sys.stderr is None:
    try:
        _logf = open(_LOG, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = _logf
    except Exception:
        pass

def _excepthook(t, v, tb):
    import traceback
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.ctime()} ---\n")
            traceback.print_exception(t, v, tb, file=f)
    except Exception:
        pass
sys.excepthook = _excepthook
threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)

# ── Auto-install (script mode only; the exe already bundles everything) ──────
def _pip(*pkgs):
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs, "-q"])

if not FROZEN:
    for _pkg, _mod in [("Pillow","PIL"), ("pystray","pystray"), ("keyboard","keyboard")]:
        try:    __import__(_mod)
        except ImportError: print(f"Installing {_pkg}..."); _pip(_pkg)

from PIL import Image, ImageTk, ImageDraw, ImageFont
import pystray
import keyboard as KB

# ── Config ────────────────────────────────────────────────────────────────────
CFG = Path(os.environ.get("APPDATA", Path.home())) / "KeyOverlay" / "settings.json"
CFG.parent.mkdir(exist_ok=True)

def cfg_load():
    try:
        if CFG.exists():
            return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Load error: {e}")
    return {"bindings": []}

def cfg_save(s):
    try:    CFG.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e: print(f"Save error: {e}")

# ── Monitor enumeration (Windows ctypes, no extra packages) ───────────────────
def enum_displays():
    """Return list of monitor dicts: {x, y, w, h, primary, label, index}."""
    monitors = []
    _Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong,
                                ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_double)
    def _cb(hMon, hDC, pRect, _):
        r = pRect.contents
        monitors.append({"x": r.left, "y": r.top,
                         "w": r.right - r.left, "h": r.bottom - r.top})
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, _Proc(_cb), 0)
    for i, m in enumerate(monitors):
        m["primary"] = (m["x"] == 0 and m["y"] == 0)
        m["index"]   = i
        m["label"]   = f"Display {i+1}" + (" (Primary)" if m["primary"] else "")
    monitors.sort(key=lambda m: not m["primary"])
    return monitors if monitors else [{"x":0,"y":0,"w":1920,"h":1080,
                                       "primary":True,"index":0,"label":"Display 1 (Primary)"}]

# ── Session-unlock monitor (re-registers KB hooks after lock/unlock) ──────────
def _is_locked():
    """True while the Windows lock screen is up (input desktop not accessible)."""
    u32 = ctypes.windll.user32
    u32.OpenInputDesktop.restype  = ctypes.c_void_p
    u32.CloseDesktop.argtypes     = [ctypes.c_void_p]
    h = u32.OpenInputDesktop(0, False, 0x0100)   # DESKTOP_SWITCHDESKTOP
    if not h:
        return True
    u32.CloseDesktop(h)
    return False

def _setup_session_monitor(root, on_unlock, interval=2000):
    """Poll lock state; call on_unlock() shortly after the screen is unlocked
    so the keyboard library's low-level hook gets re-registered."""
    state = {"locked": False}
    def _tick():
        try:
            locked = _is_locked()
            if state["locked"] and not locked:
                root.after(500, on_unlock)
            state["locked"] = locked
        except Exception as e:
            print(f"Lock check failed: {e}")
        root.after(interval, _tick)
    root.after(interval, _tick)

# ── Colour utils ──────────────────────────────────────────────────────────────
CHROMA = "#010101"

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def resolve(v, total):
    if v == "center":                          return total // 2
    if isinstance(v, str) and v.endswith("%"): return int(float(v[:-1]) / 100 * total)
    try:                                       return int(v)
    except Exception:                          return 0

# ── Overlay window ─────────────────────────────────────────────────────────────
class Overlay:
    def __init__(self, root):
        self.root = root
        self.win = self.canvas = None
        self._alpha = 0.0
        self._job   = None
        self._refs  = []

    def _ensure(self):
        if self.win and self.win.winfo_exists(): return
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost",          True)
        self.win.wm_attributes("-transparentcolor", CHROMA)
        self.win.wm_attributes("-alpha",            0.0)
        self.win.configure(bg=CHROMA)
        self.canvas = tk.Canvas(self.win, bg=CHROMA, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

    def show(self, cfg, mon=None):
        self._ensure()
        self._cancel()
        m  = mon or {"x":0, "y":0, "w": self.root.winfo_screenwidth(),
                                    "h": self.root.winfo_screenheight()}
        pos = cfg.get("position", {})
        px = resolve(pos.get("x", 0),       m["w"])
        py = resolve(pos.get("y", 0),       m["h"])
        pw = resolve(pos.get("width",  "100%"), m["w"])
        ph = resolve(pos.get("height", "100%"), m["h"])
        wx, wy = m["x"] + px, m["y"] + py
        self.win.geometry(f"{pw}x{ph}+{wx}+{wy}")
        self._draw(cfg, pw, ph)
        self.win.wm_attributes("-alpha", 0.0)
        self._alpha = 0.0
        self.win.deiconify()
        self.win.lift()
        self.win.wm_attributes("-topmost", True)
        self._fadeto(1.0, cfg.get("fadeIn", 300), cfg.get("easing", "ease"))

    def hide(self, ms=300, easing="ease"):
        if not self.win or not self.win.winfo_exists(): return
        self._cancel()
        def done():
            if self.win and self.win.winfo_exists(): self.win.withdraw()
        self._fadeto(0.0, ms, easing, done)

    # ── Drawing ────────────────────────────────────────────────────────────────
    def _draw(self, cfg, w, h):
        self.canvas.delete("all")
        self._refs.clear()
        t = cfg.get("type", "border")

        if t == "border":
            b       = cfg.get("border", {})
            rgb     = hex_rgb(b.get("color", "#ff0000"))
            bw      = max(1, int(b.get("width", 8)))
            ins     = int(b.get("inset", 0))
            alpha   = int(float(b.get("opacity", 1.0)) * 255)
            img     = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d       = ImageDraw.Draw(img)
            for r in [(ins, ins, w-ins-1, ins+bw-1),
                      (ins, h-ins-bw, w-ins-1, h-ins-1),
                      (ins, ins, ins+bw-1, h-ins-1),
                      (w-ins-bw, ins, w-ins-1, h-ins-1)]:
                d.rectangle(r, fill=(*rgb, alpha))
            bg = Image.new("RGB", (w, h), hex_rgb(CHROMA))
            bg.paste(img, mask=img.split()[3])
            ref = ImageTk.PhotoImage(bg); self._refs.append(ref)
            self.canvas.create_image(0, 0, anchor="nw", image=ref)

        elif t == "tint":
            tc      = cfg.get("tint", {})
            rgb     = hex_rgb(tc.get("color", "#ff0000"))
            opacity = float(tc.get("opacity", 0.3))
            img     = Image.new("RGBA", (w, h), (*rgb, int(opacity * 255)))
            bg      = Image.new("RGB",  (w, h), hex_rgb(CHROMA))
            bg.paste(img, mask=img.split()[3])
            ref = ImageTk.PhotoImage(bg); self._refs.append(ref)
            self.canvas.create_image(0, 0, anchor="nw", image=ref)

        elif t == "image":
            ic  = cfg.get("image", {})
            src = ic.get("src", "")
            if src and os.path.exists(src):
                try:
                    img     = Image.open(src).convert("RGBA")
                    img     = self._fit(img, w, h, ic.get("objectFit", "contain"))
                    opacity = float(ic.get("opacity", 1.0))
                    if opacity < 1.0:
                        r2, g2, b2, a2 = img.split()
                        a2  = a2.point(lambda x: int(x * opacity))
                        img = Image.merge("RGBA", (r2, g2, b2, a2))
                    bg  = Image.new("RGB", (w, h), hex_rgb(CHROMA))
                    bg.paste(img, mask=img.split()[3])
                    ref = ImageTk.PhotoImage(bg); self._refs.append(ref)
                    self.canvas.create_image(0, 0, anchor="nw", image=ref)
                except Exception as e:
                    print(f"Image error: {e}")

        elif t == "text":
            tc   = cfg.get("text", {})
            txt  = tc.get("content", "TEXT")
            fsz  = int(tc.get("fontSize", 64))
            col  = hex_rgb(tc.get("color", "#ffffff"))
            shad = tc.get("shadow", True)
            cx   = tc.get("cx", w // 2)
            cy   = tc.get("cy", h // 2)

            try:    font = ImageFont.truetype("arialbd.ttf", fsz)
            except:
                try:    font = ImageFont.truetype("arial.ttf", fsz)
                except: font = ImageFont.load_default()

            _tmp = ImageDraw.Draw(Image.new("L", (1,1)))
            bbox = _tmp.textbbox((0,0), txt, font=font)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            tx = cx - tw // 2
            ty = cy - th // 2

            bg = Image.new("RGB", (w, h), hex_rgb(CHROMA))

            if shad:
                sm = Image.new("L", (w, h), 0)
                ImageDraw.Draw(sm).text((tx+3, ty+3), txt, font=font, fill=180)
                bg.paste((0, 0, 0), mask=sm)

            # ── Sharp text ─────────────────────────────────────────────────────
            tm = Image.new("L", (w, h), 0)
            ImageDraw.Draw(tm).text((tx, ty), txt, font=font, fill=255)
            bg.paste(col, mask=tm)

            ref = ImageTk.PhotoImage(bg); self._refs.append(ref)
            self.canvas.create_image(0, 0, anchor="nw", image=ref)

    def _fit(self, img, w, h, fit):
        if fit == "contain":
            img.thumbnail((w, h), Image.LANCZOS)
            bg = Image.new("RGBA", (w, h), (0,0,0,0))
            bg.paste(img, ((w-img.width)//2, (h-img.height)//2)); return bg
        if fit == "cover":
            r = max(w/img.width, h/img.height)
            img = img.resize((int(img.width*r), int(img.height*r)), Image.LANCZOS)
            return img.crop(((img.width-w)//2, (img.height-h)//2,
                             (img.width+w)//2, (img.height+h)//2))
        if fit == "fill": return img.resize((w, h), Image.LANCZOS)
        bg = Image.new("RGBA", (w, h), (0,0,0,0)); bg.paste(img,(0,0)); return bg

    # ── Fade ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _ease(t, mode):
        if mode == "linear":   return t
        if mode == "ease-in":  return t * t
        if mode == "ease-out": return 1 - (1-t)**2
        return t * t * (3 - 2*t)

    def _cancel(self):
        if self._job:
            try: self.root.after_cancel(self._job)
            except Exception: pass
            self._job = None

    def _fadeto(self, target, ms, easing="ease", cb=None):
        if ms <= 0:
            self._alpha = target
            if self.win and self.win.winfo_exists():
                self.win.wm_attributes("-alpha", target)
            if cb: cb(); return
        start = self._alpha; t0 = time.perf_counter()
        def step():
            p = min((time.perf_counter()-t0)*1000/ms, 1.0)
            a = start + (target-start)*self._ease(p, easing)
            self._alpha = a
            if self.win and self.win.winfo_exists():
                self.win.wm_attributes("-alpha", a)
            if p < 1.0: self._job = self.root.after(14, step)
            else:        self._job = None; (cb() if cb else None)
        self._job = self.root.after(0, step)


# ── Manager ───────────────────────────────────────────────────────────────────
def _acc_to_kb(acc):
    MAP = {
        "Control":"ctrl","Alt":"alt","Shift":"shift","Meta":"windows",
        "Return":"enter","Space":"space","Escape":"esc",
        "Backspace":"backspace","Delete":"delete","Tab":"tab",
        "Left":"left","Right":"right","Up":"up","Down":"down",
        "Home":"home","End":"end","PageUp":"page up","PageDown":"page down",
        "Insert":"insert","PrintScreen":"print screen",
        **{f"F{i}": f"f{i}" for i in range(1, 25)},
    }
    return "+".join(MAP.get(p, p.lower()) for p in acc.split("+"))


class Manager:
    def __init__(self, root):
        self.root    = root
        self.cfg     = cfg_load()
        self.displays = enum_displays()
        self._ovs    = {}
        self._active = {}
        self._register()

    def _get_mon(self, display_val):
        """Return list of monitor dicts for a display setting."""
        if display_val == "all":
            return self.displays
        for m in self.displays:
            if str(m["index"]) == str(display_val): return [m]
        # default: primary
        return [m for m in self.displays if m["primary"]] or [self.displays[0]]

    def _register(self):
        try: KB.unhook_all_hotkeys()
        except Exception: pass
        for b in self.cfg.get("bindings", []):
            if not b.get("enabled", True) or not b.get("key"): continue
            try:
                kstr = _acc_to_kb(b["key"])
                KB.add_hotkey(kstr,
                    lambda bd=b: self.root.after(0, lambda x=bd: self._press(x)),
                    suppress=False)
                print(f"  ✓  {b['key']}  →  {kstr}")
            except Exception as e:
                print(f"  ✗  {b.get('key')}: {e}")

    def _press(self, b):
        if b["id"] in self._active: self._hide(b)
        else:                       self._show(b)

    def _show(self, b):
        bid  = b["id"]
        mons = self._get_mon(b["overlay"].get("display", "0"))
        for mon in mons:
            key = f"{bid}_{mon['index']}"
            if key not in self._ovs: self._ovs[key] = Overlay(self.root)
            self._ovs[key].show(b["overlay"], mon)
        hold = b["overlay"].get("holdDuration", 0)
        if hold > 0:
            delay = hold + b["overlay"].get("fadeIn", 300)
            self._active[bid] = self.root.after(delay, lambda: self._hide(b))
        else:
            self._active[bid] = None

    def _hide(self, b):
        bid = b["id"]
        job = self._active.pop(bid, "x")
        if job and job != "x":
            try: self.root.after_cancel(job)
            except Exception: pass
        for key in [k for k in self._ovs if k.startswith(f"{bid}_")]:
            self._ovs[key].hide(b["overlay"].get("fadeOut", 300),
                                b["overlay"].get("easing",  "ease"))

    # ── Preview (one-shot, 2.5 s) ──────────────────────────────────────────────
    def preview(self, overlay):
        mon = (self._get_mon(overlay.get("display","0")) or [self.displays[0]])[0]
        pid = "__preview__"
        if pid not in self._ovs: self._ovs[pid] = Overlay(self.root)
        self._ovs[pid].show(overlay, mon)
        hold  = max(overlay.get("holdDuration", 0), 1500)
        delay = hold + overlay.get("fadeIn", 300)
        self.root.after(delay, lambda: self._ovs[pid].hide(overlay.get("fadeOut",300)))

    # ── Live preview (stays until stop_live called) ───────────────────────────
    def start_live(self, overlay):
        mon = (self._get_mon(overlay.get("display","0")) or [self.displays[0]])[0]
        lid = "__live__"
        if lid not in self._ovs: self._ovs[lid] = Overlay(self.root)
        ov_copy = dict(overlay); ov_copy["fadeIn"] = 80
        self._ovs[lid].show(ov_copy, mon)

    def update_live(self, overlay):
        lid = "__live__"
        if lid not in self._ovs or not self._ovs[lid].win or \
           not self._ovs[lid].win.winfo_exists():
            self.start_live(overlay); return
        mon = (self._get_mon(overlay.get("display","0")) or [self.displays[0]])[0]
        ov  = self._ovs[lid]
        # Reposition and redraw without fade
        m   = mon
        pos = overlay.get("position", {})
        pw  = resolve(pos.get("width",  "100%"), m["w"])
        ph  = resolve(pos.get("height", "100%"), m["h"])
        px  = m["x"] + resolve(pos.get("x", 0), m["w"])
        py  = m["y"] + resolve(pos.get("y", 0), m["h"])
        ov.win.geometry(f"{pw}x{ph}+{px}+{py}")
        ov._draw(overlay, pw, ph)
        ov.win.wm_attributes("-alpha", 1.0)
        ov.win.lift()
        ov.win.wm_attributes("-topmost", True)

    def stop_live(self):
        lid = "__live__"
        if lid in self._ovs: self._ovs[lid].hide(200)

    def reload(self):
        self.cfg      = cfg_load()
        self.displays = enum_displays()
        self._register()


# ── Theme constants ───────────────────────────────────────────────────────────
BG  = "#12121f";  PNL = "#1a1a2e";  SRF = "#23233a";  SR2 = "#2c2c47"
BRD = "#35355a";  ACC = "#ff8c00";  TXT = "#e8e8f0";  T2  = "#9090b0"
DNG = "#e05050"

def _e(parent, textvariable=None, **kw):
    return tk.Entry(parent, textvariable=textvariable, bg=SR2, fg=TXT,
                    insertbackground=TXT, relief="flat", highlightthickness=1,
                    highlightbackground=BRD, highlightcolor=ACC,
                    font=("Arial", 10), **kw)

def _btn(parent, text, cmd, fg=TXT, bg=SR2, bold=False, **kw):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                     activebackground=SRF, activeforeground=fg, relief="flat",
                     padx=10, pady=5,
                     font=("Arial", 10, "bold") if bold else ("Arial", 10), **kw)


# ── Settings window ────────────────────────────────────────────────────────────
SEC_ORDER  = ["info","type","image","border","tint","text","pos","anim","actions"]
TYPE_SECS  = {"image":["image","pos"],"border":["border","pos"],
              "tint":["tint","pos"],"text":["text"]}
ALWAYS     = ["info","type","anim","actions"]


class Settings:
    def __init__(self, root, mgr):
        self.root = root; self.mgr = mgr
        self.win  = None
        self._sel = None
        self._recording = False
        self._rec_key   = None
        self._live      = False
        self._live_job  = None   # debounce timer

    def open(self):
        if self.win and self.win.winfo_exists():
            self.win.lift(); self.win.focus_force(); return
        self.win = tk.Toplevel(self.root)
        self.win.title("Key Press Overlay – Settings")
        self.win.configure(bg=BG)
        self.win.geometry("900x640")
        self.win.minsize(720, 500)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self._refresh_list()

    def _on_close(self):
        if self._live: self.mgr.stop_live(); self._live = False
        self.win.destroy(); self.win = None

    # ── Toggle button helper (replaces broken Checkbutton on dark themes) ──────
    def _toggle_btn(self, parent, var, label_on, label_off):
        """A Button that acts as a checkbox. Reliable on dark Windows themes."""
        def _refresh():
            if var.get(): btn.config(bg=ACC, fg="#000000", text=f"✓  {label_on}")
            else:          btn.config(bg=SR2, fg=T2,        text=f"✗  {label_off}")
        def _click():
            var.set(not var.get()); _refresh(); self._live_update()
        btn = tk.Button(parent, command=_click, relief="flat", padx=10, pady=4,
                        font=("Arial", 10))
        _refresh()
        return btn

    # ── Layout ─────────────────────────────────────────────────────────────────
    def _build(self):
        hdr = tk.Frame(self.win, bg=PNL)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Key Press Overlay", bg=PNL, fg=TXT,
                 font=("Arial",13,"bold")).pack(side="left", padx=14, pady=10)
        _btn(hdr, "＋ New Binding", self._add, bg=ACC, fg="#000",
             bold=True).pack(side="right", padx=12, pady=8)

        body = tk.Frame(self.win, bg=BG); body.pack(fill="both", expand=True)

        # Sidebar
        self._sidebar = tk.Frame(body, bg=PNL, width=210)
        self._sidebar.pack(side="left", fill="y"); self._sidebar.pack_propagate(False)
        tk.Label(self._sidebar, text="BINDINGS", bg=PNL, fg=T2,
                 font=("Arial",9,"bold")).pack(anchor="w", padx=10, pady=(8,2))
        self._list_box = tk.Frame(self._sidebar, bg=PNL)
        self._list_box.pack(fill="both", expand=True, padx=4)

        # Editor
        right = tk.Frame(body, bg=BG); right.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        vsb    = tk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self._ed = tk.Frame(canvas, bg=BG)
        _cw = canvas.create_window((0,0), window=self._ed, anchor="nw")
        self._ed.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_cw, width=e.width))

        self._empty = tk.Label(self._ed, text="← Select or create a binding",
                               bg=BG, fg=T2, font=("Arial",12))
        self._empty.pack(padx=30, pady=40)
        self._build_form()
        self._toggle_form(False)

    def _sec(self, key, title):
        f = tk.LabelFrame(self._ed, text=f"  {title}  ", bg=SRF, fg=ACC,
                          font=("Arial",9,"bold"), bd=1, relief="flat",
                          highlightthickness=1, highlightbackground=BRD, labelanchor="nw")
        self._secs[key] = f; return f

    def _build_form(self):
        self._secs = {}

        # ── Info ──────────────────────────────────────────────────────────────
        s = self._sec("info", "Binding Info")
        self.v_name    = tk.StringVar()
        self.v_enabled = tk.BooleanVar(value=True)

        nr = tk.Frame(s, bg=SRF); nr.pack(fill="x", padx=10, pady=(8,2))
        tk.Label(nr, text="Name", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        _e(nr, textvariable=self.v_name).pack(side="left", fill="x", expand=True)

        kr = tk.Frame(s, bg=SRF); kr.pack(fill="x", padx=10, pady=2)
        tk.Label(kr, text="Key", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        self.key_lbl = tk.Label(kr, text="—", bg=SR2, fg=TXT,
                                 font=("Courier New",10), relief="flat", padx=8, pady=3,
                                 highlightthickness=1, highlightbackground=BRD)
        self.key_lbl.pack(side="left", fill="x", expand=True, padx=(0,6))
        self.rec_btn = _btn(kr, "Record Key", self._toggle_rec)
        self.rec_btn.pack(side="left", padx=(0,4))
        _btn(kr, "✕", self._clear_key, fg=DNG).pack(side="left")

        er = tk.Frame(s, bg=SRF); er.pack(fill="x", padx=10, pady=(2,10))
        tk.Label(er, text="Enabled", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        self._toggle_btn(er, self.v_enabled, "Enabled", "Disabled").pack(side="left")

        # ── Overlay type tabs ─────────────────────────────────────────────────
        s = self._sec("type", "Overlay Type")
        tabs = tk.Frame(s, bg=SRF); tabs.pack(fill="x", padx=10, pady=8)
        self._type_btns = {}
        self.v_type = tk.StringVar(value="border")
        for key, label in [("image","🖼 Image"),("border","▭ Border"),
                            ("tint","🎨 Tint"),("text","T  Text")]:
            b = tk.Button(tabs, text=label, bg=SR2, fg=TXT, relief="flat",
                          padx=10, pady=5, font=("Arial",10),
                          command=lambda k=key: self._set_type(k))
            b.pack(side="left", padx=(0,4)); self._type_btns[key] = b

        # ── Image ─────────────────────────────────────────────────────────────
        s = self._sec("image", "Image")
        self.v_img = tk.StringVar(value="")
        ir = tk.Frame(s, bg=SRF); ir.pack(fill="x", padx=10, pady=(8,2))
        tk.Label(ir, text="File", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        tk.Label(ir, textvariable=self.v_img, bg=SR2, fg=T2, font=("Arial",9),
                 anchor="w", relief="flat", padx=6,
                 highlightthickness=1, highlightbackground=BRD
                 ).pack(side="left", fill="x", expand=True, padx=(0,6))
        _btn(ir, "Browse…", self._pick_img).pack(side="left")

        self.v_img_fit = tk.StringVar(value="contain")
        fr2 = tk.Frame(s, bg=SRF); fr2.pack(fill="x", padx=10, pady=2)
        tk.Label(fr2, text="Fit", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        ttk.Combobox(fr2, values=["contain","cover","fill","none"],
                     textvariable=self.v_img_fit, state="readonly",
                     font=("Arial",10), width=12).pack(side="left")
        self.v_img_op, _ = self._slider(s, "Opacity", 0, 1, .05,
                                         fmt=lambda v: f"{int(v*100)}%", init=1.0)

        # ── Border ────────────────────────────────────────────────────────────
        s = self._sec("border", "Hard Border")
        self.v_bdr_col, _ = self._color_row(s, "Color", "#ff0000")
        self.v_bdr_w,   _ = self._slider(s, "Width (px)",  1,  80, 1,
                                          fmt=lambda v: f"{int(v)}px", init=8)
        self.v_bdr_ins, _ = self._slider(s, "Inset (px)",  0,  80, 1,
                                          fmt=lambda v: f"{int(v)}px")
        self.v_bdr_op,  _ = self._slider(s, "Opacity",     0,   1, .05,
                                          fmt=lambda v: f"{int(v*100)}%", init=1.0)

        # ── Tint ─────────────────────────────────────────────────────────────
        s = self._sec("tint", "Tint")
        self.v_tnt_col, _ = self._color_row(s, "Color",   "#ff0000")
        self.v_tnt_op,  _ = self._slider(s, "Opacity",     0,   1, .05,
                                          fmt=lambda v: f"{int(v*100)}%", init=0.3)

        # ── Text ─────────────────────────────────────────────────────────────
        s = self._sec("text", "Text")
        self.v_txt = tk.StringVar(value="TEXT")
        tr = tk.Frame(s, bg=SRF); tr.pack(fill="x", padx=10, pady=(8,2))
        tk.Label(tr, text="Content", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        _e(tr, textvariable=self.v_txt).pack(side="left", fill="x", expand=True)

        self.v_txt_sz,  _ = self._slider(s, "Font Size (px)",  8, 1024, 2,
                                          fmt=lambda v: f"{int(v)}px", init=64)
        self.v_txt_col, _ = self._color_row(s, "Color", "#ffffff")
        self.v_txt_sh = tk.BooleanVar(value=True)
        shr = tk.Frame(s, bg=SRF); shr.pack(fill="x", padx=10, pady=(2,4))
        tk.Label(shr, text="Drop Shadow", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        self._toggle_btn(shr, self.v_txt_sh, "On", "Off").pack(side="left")

        # Text position (overlay is always full-screen; these move the text center)
        sep2 = tk.Frame(s, bg=BRD, height=1); sep2.pack(fill="x", padx=10, pady=(8,4))
        tk.Label(s, text="Position", bg=SRF, fg=ACC,
                 font=("Arial",9,"bold")).pack(anchor="w", padx=10, pady=(0,4))
        _sw = self.root.winfo_screenwidth()
        _sh = self.root.winfo_screenheight()
        self.v_txt_cx, _ = self._slider(s, "Center X", 0, _sw, 1,
                                         fmt=lambda v: f"{int(v)}px  ({int(v/_sw*100)}%)",
                                         live=True, init=_sw//2)
        self.v_txt_cy, _ = self._slider(s, "Center Y", 0, _sh, 1,
                                         fmt=lambda v: f"{int(v)}px  ({int(v/_sh*100)}%)",
                                         live=True, init=_sh//2)

        # Display selector for text type (shared v_display var with pos section)
        mons = self.mgr.displays
        disp_opts = ["All displays"] + [m["label"] for m in mons]
        self.v_display = tk.StringVar(value=mons[0]["label"] if mons else "Display 1")
        tdr = tk.Frame(s, bg=SRF); tdr.pack(fill="x", padx=10, pady=(2,10))
        tk.Label(tdr, text="Display", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        tcb = ttk.Combobox(tdr, values=disp_opts, textvariable=self.v_display,
                           state="readonly", font=("Arial",10), width=26)
        tcb.pack(side="left", padx=4)
        tcb.bind("<<ComboboxSelected>>", lambda e: self._live_update())

        # ── Position & Size ───────────────────────────────────────────────────
        s = self._sec("pos", "Position & Size")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        self.v_px, _ = self._slider(s, "X",      0, sw, 1,
                                     fmt=lambda v: f"{int(v)}px  ({int(v/sw*100)}%)",
                                     live=True)
        self.v_py, _ = self._slider(s, "Y",      0, sh, 1,
                                     fmt=lambda v: f"{int(v)}px  ({int(v/sh*100)}%)",
                                     live=True)
        self.v_pw, _ = self._slider(s, "Width",  1, sw, 1,
                                     fmt=lambda v: f"{int(v)}px  ({int(v/sw*100)}%)",
                                     live=True, init=sw)
        self.v_ph, _ = self._slider(s, "Height", 1, sh, 1,
                                     fmt=lambda v: f"{int(v)}px  ({int(v/sh*100)}%)",
                                     live=True, init=sh)

        # Monitor selector (shares v_display already created in text section above)
        dr = tk.Frame(s, bg=SRF); dr.pack(fill="x", padx=10, pady=(2,10))
        tk.Label(dr, text="Display", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        cb = ttk.Combobox(dr, values=disp_opts, textvariable=self.v_display,
                          state="readonly", font=("Arial",10), width=26)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._live_update())

        # ── Animation ─────────────────────────────────────────────────────────
        s = self._sec("anim", "Animation")
        self.v_fi, _ = self._slider(s, "Fade In (ms)",  0, 2000, 25,
                                     fmt=lambda v: f"{int(v)}ms", init=300)
        self.v_fo, _ = self._slider(s, "Fade Out (ms)", 0, 2000, 25,
                                     fmt=lambda v: f"{int(v)}ms", init=400)
        self.v_hd, _ = self._slider(s, "Hold (ms)",     0, 10000,100,
                                     fmt=lambda v: "Toggle" if v==0 else f"{int(v)}ms")
        er = tk.Frame(s, bg=SRF); er.pack(fill="x", padx=10, pady=(2,10))
        tk.Label(er, text="Easing", bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        self.v_ease = tk.StringVar(value="ease")
        ttk.Combobox(er, values=["ease","ease-in","ease-out","ease-in-out","linear"],
                     textvariable=self.v_ease, state="readonly",
                     font=("Arial",10), width=14).pack(side="left")

        # ── Actions ───────────────────────────────────────────────────────────
        s = tk.Frame(self._ed, bg=BG); self._secs["actions"] = s
        _btn(s, "Delete",  self._delete, fg=DNG).pack(side="left",  padx=(14,0), pady=12)
        self.live_btn = tk.Button(s, text="◉ Live Preview", command=self._toggle_live,
                                   bg=SR2, fg=TXT, relief="flat", padx=10, pady=5,
                                   font=("Arial",10))
        self.live_btn.pack(side="left", padx=(8,0), pady=12)
        _btn(s, "▶ Preview", self._preview).pack(side="right", padx=(4,14), pady=12)
        _btn(s, "Save", self._save, bg=ACC, fg="#000",
             bold=True).pack(side="right", pady=12)

    # ── Widget helpers ─────────────────────────────────────────────────────────
    def _slider(self, parent, label, lo, hi, res, fmt=None, init=None, live=True):
        var = tk.DoubleVar(value=init if init is not None else lo)
        row = tk.Frame(parent, bg=SRF); row.pack(fill="x", padx=10, pady=2)
        tk.Label(row, text=label, bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        lbl = tk.Label(row, text="", bg=SRF, fg=T2, font=("Arial",10), width=18, anchor="e")
        lbl.pack(side="right")
        def upd(*_):
            lbl.config(text=fmt(var.get()) if fmt else str(var.get()))
            if live: self._live_update()
        sl = tk.Scale(row, from_=lo, to=hi, resolution=res, orient="horizontal",
                      variable=var, bg=SRF, fg=TXT, troughcolor=SR2,
                      activebackground=ACC, highlightthickness=0,
                      sliderrelief="flat", showvalue=False, command=upd)
        sl.pack(side="left", fill="x", expand=True)
        upd(); return var, sl

    def _color_row(self, parent, label, default="#ff0000"):
        var = tk.StringVar(value=default)
        row = tk.Frame(parent, bg=SRF); row.pack(fill="x", padx=10, pady=2)
        tk.Label(row, text=label, bg=SRF, fg=T2, font=("Arial",10),
                 width=14, anchor="w").pack(side="left")
        swatch = tk.Label(row, bg=default, width=3, relief="flat",
                          highlightthickness=1, highlightbackground=BRD)
        swatch.pack(side="left", padx=(0,4))
        e = _e(row, textvariable=var, width=10); e.pack(side="left")
        def pick():
            c = colorchooser.askcolor(color=var.get(), parent=self.win)
            if c[1]: var.set(c[1]); swatch.config(bg=c[1]); self._live_update()
        def on_type(*_):
            try: swatch.config(bg=var.get())
            except Exception: pass
            self._live_update()
        _btn(row, "…", pick).pack(side="left", padx=4)
        var.trace_add("write", on_type)
        return var, swatch

    # ── Section visibility ─────────────────────────────────────────────────────
    def _set_type(self, t):
        self.v_type.set(t)
        show = set(ALWAYS + TYPE_SECS.get(t, []))
        for k in SEC_ORDER:
            f = self._secs.get(k)
            if f:
                try: f.pack_forget()
                except Exception: pass
        for k in SEC_ORDER:
            if k in show:
                f = self._secs.get(k)
                if f:
                    kw = dict(fill="x", padx=14, pady=(0,10)) if k != "actions" \
                         else dict(fill="x")
                    f.pack(**kw)
        for k, b in self._type_btns.items():
            b.config(bg=ACC if k==t else SR2, fg="#000" if k==t else TXT)

    def _toggle_form(self, show):
        if show:
            self._empty.pack_forget()
            self._set_type(self.v_type.get())
        else:
            for k in SEC_ORDER:
                f = self._secs.get(k)
                if f:
                    try: f.pack_forget()
                    except Exception: pass
            self._empty.pack(padx=30, pady=40)

    # ── Sidebar ────────────────────────────────────────────────────────────────
    def _refresh_list(self):
        for w in self._list_box.winfo_children(): w.destroy()
        for b in self.mgr.cfg.get("bindings", []):
            active = b["id"] == self._sel
            item   = tk.Frame(self._list_box, bg=SR2 if active else PNL,
                              highlightthickness=1,
                              highlightbackground=ACC if active else PNL,
                              cursor="hand2")
            item.pack(fill="x", pady=2)
            tk.Label(item, text=b.get("name","Unnamed"),
                     bg=SR2 if active else PNL, fg=TXT,
                     font=("Arial",10,"bold"), anchor="w"
                     ).pack(fill="x", padx=8, pady=(6,0))
            tk.Label(item, text=b.get("key","No key set"),
                     bg=SR2 if active else PNL, fg=T2,
                     font=("Courier New",9), anchor="w"
                     ).pack(fill="x", padx=8, pady=(0,6))
            for w in [item] + item.winfo_children():
                w.bind("<Button-1>", lambda e, bid=b["id"]: self._select(bid))

    def _select(self, bid):
        self._sel = bid
        b = next((x for x in self.mgr.cfg.get("bindings",[]) if x["id"]==bid), None)
        if b: self._load(b); self._toggle_form(True)
        self._refresh_list()

    # ── Load / build overlay from form ─────────────────────────────────────────
    def _load(self, b):
        self.v_name.set(b.get("name",""))
        self.v_enabled.set(b.get("enabled", True))
        self._rec_key = b.get("key")
        self.key_lbl.config(text=b.get("key") or "—", fg=TXT)

        ov  = b.get("overlay", {})
        pos = ov.get("position", {})
        sw  = self.root.winfo_screenwidth()
        sh  = self.root.winfo_screenheight()

        # Position: stored values may be ints, "100%", or "center" — convert to px
        def to_px(v, total):
            if v == "center": return total // 2
            if isinstance(v, str) and v.endswith("%"): return int(float(v[:-1])/100*total)
            try: return int(v)
            except: return 0

        self.v_px.set(to_px(pos.get("x",  0),     sw))
        self.v_py.set(to_px(pos.get("y",  0),     sh))
        self.v_pw.set(to_px(pos.get("width","100%"),  sw))
        self.v_ph.set(to_px(pos.get("height","100%"), sh))

        # Display
        disp = ov.get("display","0")
        if disp == "all":
            self.v_display.set("All displays")
        else:
            mons = self.mgr.displays
            try: self.v_display.set(mons[int(disp)]["label"])
            except: self.v_display.set(mons[0]["label"] if mons else "Display 1")

        self.v_fi.set(ov.get("fadeIn",       300))
        self.v_fo.set(ov.get("fadeOut",      400))
        self.v_hd.set(ov.get("holdDuration", 0))
        self.v_ease.set(ov.get("easing",     "ease"))

        if ic := ov.get("image"):
            self.v_img.set(ic.get("src",""))
            self.v_img_fit.set(ic.get("objectFit","contain"))
            self.v_img_op.set(ic.get("opacity", 1))
        if bc := ov.get("border"):
            self.v_bdr_col.set(bc.get("color","#ff0000"))
            self.v_bdr_w.set(bc.get("width", 8))
            self.v_bdr_ins.set(bc.get("inset", 0))
            self.v_bdr_op.set(bc.get("opacity", 1))
        if tc := ov.get("tint"):
            self.v_tnt_col.set(tc.get("color","#ff0000"))
            self.v_tnt_op.set(tc.get("opacity", 0.3))
        if tc := ov.get("text"):
            _sw = self.root.winfo_screenwidth()
            _sh = self.root.winfo_screenheight()
            self.v_txt.set(tc.get("content","TEXT"))
            self.v_txt_sz.set(tc.get("fontSize", 64))
            self.v_txt_col.set(tc.get("color","#ffffff"))
            self.v_txt_sh.set(tc.get("shadow", True))
            self.v_txt_cx.set(tc.get("cx", _sw // 2))
            self.v_txt_cy.set(tc.get("cy", _sh // 2))

        self._set_type(ov.get("type","border"))

    def _get_display_val(self):
        txt = self.v_display.get()
        if txt == "All displays": return "all"
        for m in self.mgr.displays:
            if m["label"] == txt: return str(m["index"])
        return "0"

    def _to_overlay(self):
        t   = self.v_type.get()
        pos = {"x": int(self.v_px.get()), "y": int(self.v_py.get()),
               "width": int(self.v_pw.get()), "height": int(self.v_ph.get())}
        ov  = {"type": t, "position": pos,
               "display":       self._get_display_val(),
               "fadeIn":        int(self.v_fi.get()),
               "fadeOut":       int(self.v_fo.get()),
               "holdDuration":  int(self.v_hd.get()),
               "easing":        self.v_ease.get()}
        if t == "image":
            ov["image"]  = {"src": self.v_img.get(),
                            "objectFit": self.v_img_fit.get(),
                            "opacity": self.v_img_op.get()}
        elif t == "border":
            ov["border"] = {"color": self.v_bdr_col.get(),
                            "width": int(self.v_bdr_w.get()),
                            "inset": int(self.v_bdr_ins.get()),
                            "opacity": self.v_bdr_op.get()}
        elif t == "tint":
            ov["tint"]   = {"color": self.v_tnt_col.get(),
                            "opacity": self.v_tnt_op.get()}
        elif t == "text":
            # Text overlay always covers the full monitor; cx/cy position the text inside it
            _sw = self.root.winfo_screenwidth()
            _sh = self.root.winfo_screenheight()
            ov["position"] = {"x": 0, "y": 0, "width": _sw, "height": _sh}
            ov["text"]   = {"content":  self.v_txt.get(),
                            "fontSize": int(self.v_txt_sz.get()),
                            "color":    self.v_txt_col.get(),
                            "shadow":   self.v_txt_sh.get(),
                            "cx":       int(self.v_txt_cx.get()),
                            "cy":       int(self.v_txt_cy.get())}
        return ov

    def _to_binding(self):
        return {"id": self._sel or str(uuid.uuid4()),
                "name": self.v_name.get() or "Unnamed",
                "key":  self._rec_key,
                "enabled": self.v_enabled.get(),
                "overlay": self._to_overlay()}

    # ── Live preview ───────────────────────────────────────────────────────────
    def _live_update(self):
        if not self._live: return
        # Debounce: wait 40 ms after last change before updating
        if self._live_job:
            try: self.root.after_cancel(self._live_job)
            except Exception: pass
        self._live_job = self.root.after(40, self._do_live)

    def _do_live(self):
        self._live_job = None
        if self._live: self.mgr.update_live(self._to_overlay())

    def _toggle_live(self):
        self._live = not self._live
        if self._live:
            self.live_btn.config(bg=ACC, fg="#000", text="■ Stop Preview")
            self.mgr.start_live(self._to_overlay())
        else:
            self.live_btn.config(bg=SR2, fg=TXT, text="◉ Live Preview")
            self.mgr.stop_live()

    # ── Actions ────────────────────────────────────────────────────────────────
    def _add(self):
        bid = str(uuid.uuid4())
        b   = {"id":bid,"name":"New Binding","key":None,"enabled":True,
               "overlay":{"type":"border","fadeIn":200,"fadeOut":300,
                          "holdDuration":0,"easing":"ease","display":"0",
                          "position":{"x":0,"y":0,"width":1920,"height":1080},
                          "border":{"color":"#ff0000","width":8,"inset":0,"opacity":1}}}
        self.mgr.cfg.setdefault("bindings",[]).append(b)
        cfg_save(self.mgr.cfg)
        self._sel = bid
        self._refresh_list(); self._load(b); self._toggle_form(True)

    def _save(self):
        b   = self._to_binding()
        lst = self.mgr.cfg.setdefault("bindings",[])
        idx = next((i for i,x in enumerate(lst) if x["id"]==b["id"]), None)
        if idx is not None: lst[idx] = b
        else:               lst.append(b)
        cfg_save(self.mgr.cfg); self.mgr.reload(); self._refresh_list()
        self.win.title("Key Press Overlay – Settings  ✓ Saved")
        self.win.after(1500, lambda: self.win and self.win.winfo_exists() and
                       self.win.title("Key Press Overlay – Settings"))

    def _delete(self):
        if not self._sel: return
        if not messagebox.askyesno("Delete","Delete this binding?",parent=self.win): return
        self.mgr.cfg["bindings"] = [x for x in self.mgr.cfg.get("bindings",[])
                                     if x["id"] != self._sel]
        cfg_save(self.mgr.cfg); self.mgr.reload()
        self._sel = None; self._toggle_form(False); self._refresh_list()

    def _preview(self): self.mgr.preview(self._to_overlay())
    def _pick_img(self):
        p = filedialog.askopenfilename(parent=self.win, title="Select Image",
            filetypes=[("Images","*.png *.jpg *.jpeg *.gif *.webp *.bmp"),("All","*.*")])
        if p: self.v_img.set(p)

    # ── Key recorder ──────────────────────────────────────────────────────────
    def _toggle_rec(self):
        if self._recording: self._stop_rec()
        else:               self._start_rec()

    def _start_rec(self):
        self._recording = True
        self.rec_btn.config(text="Cancel", bg=DNG, fg="#fff")
        self.key_lbl.config(text="Press any key…", fg=ACC)
        self.win.bind("<KeyPress>", self._on_key)
        self.win.focus_force()

    def _stop_rec(self):
        self._recording = False
        self.rec_btn.config(text="Record Key", bg=SR2, fg=TXT)
        self.key_lbl.config(fg=TXT)
        self.win.unbind("<KeyPress>")

    def _clear_key(self):
        self._rec_key = None
        self.key_lbl.config(text="—", fg=T2)
        self._stop_rec()

    def _on_key(self, event):
        k = event.keysym
        if k in ("Control_L","Control_R","Alt_L","Alt_R",
                 "Shift_L","Shift_R","Super_L","Super_R"): return
        mods = []
        try:
            if KB.is_pressed("ctrl"):    mods.append("Control")
            if KB.is_pressed("alt"):     mods.append("Alt")
            if KB.is_pressed("shift"):   mods.append("Shift")
            if KB.is_pressed("windows"): mods.append("Meta")
        except Exception:
            if event.state & 0x0004:  mods.append("Control")
            if event.state & 0x20000: mods.append("Alt")
            if event.state & 0x0001:  mods.append("Shift")
        SPEC = {"Return":"Return","space":"Space","Escape":"Escape",
                "BackSpace":"Backspace","Delete":"Delete","Tab":"Tab",
                "Left":"Left","Right":"Right","Up":"Up","Down":"Down",
                "Home":"Home","End":"End","Prior":"PageUp","Next":"PageDown",
                "Insert":"Insert", **{f"F{i}":f"F{i}" for i in range(1,25)}}
        if k in SPEC:      key = SPEC[k]
        elif len(k) == 1:  key = k.upper()
        else:              return
        acc = "+".join(mods + [key])
        self._rec_key = acc
        self.key_lbl.config(text=acc, fg=TXT)
        self._stop_rec()


# ── Tray icon ─────────────────────────────────────────────────────────────────
def _make_icon():
    img = Image.new("RGBA", (64,64),(0,0,0,0))
    d   = ImageDraw.Draw(img)
    d.ellipse([2,2,62,62], fill=(255,140,0,255))
    d.rounded_rectangle([10,16,54,40], radius=5, fill=(15,15,35,255))
    d.rounded_rectangle([22,34,42,54], radius=4, fill=(15,15,35,255))
    return img


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk(); root.withdraw()
    mgr  = Manager(root)
    sw   = Settings(root, mgr)

    if not mgr.cfg.get("bindings"):
        root.after(150, sw.open)

    # Re-register keyboard hooks after Windows lock/unlock
    _setup_session_monitor(root, mgr._register)

    def _quit():
        try: KB.unhook_all_hotkeys()
        except Exception: pass
        icon.stop(); root.quit()

    icon = pystray.Icon("KeyOverlay", _make_icon(), "Key Press Overlay",
        pystray.Menu(
            pystray.MenuItem("Open Settings", lambda: root.after(0, sw.open), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: root.after(0, _quit)),
        ))

    threading.Thread(target=icon.run, daemon=True).start()
    print("Key Press Overlay running. Right-click tray icon to quit.")
    root.mainloop()

if __name__ == "__main__":
    main()
