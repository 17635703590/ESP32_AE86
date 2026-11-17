#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C3-Car TB6612 // BLE 桌面控制台
=================================

依赖:
    pip install bleak

运行:
    python tools/car_gui.py

说明:
    - 通过 BLE 连接设备名 "C3-Car-TB6612"，向 GATT 特征 6E400002-... 写入文本指令。
    - 指令协议: F 前进 / B 后退 / L 左转 / R 右转 / S 停止(急停+回中) / '0'..'9' 设置前进后退速度 / H 车灯开关(联动灯罩舵机)。
    - 速度滑杆只控制前进/后退；左右转由转向舵机定位，点击后自动回中。
    - 前进/后退为「按住行驶、松开即停」的死侍式(dead-man)控制；左转/右转为「点击转向」，舵机自动回中。
    - 任意时刻按 空格 或点击红色「急停」都会立即发送 S 停止。
    - 键盘: ↑W 前进, ↓S 后退, ←A 左转, →D 右转, 空格/Esc 急停。
"""

import asyncio
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

try:
    from bleak import BleakClient, BleakScanner
except Exception as _exc:  # 未安装 bleak 时给出友好提示
    BleakClient = None
    BleakScanner = None
    _BLEAK_ERR = _exc
else:
    _BLEAK_ERR = None

# ---------------------------------------------------------------- 设备与协议
DEVICE_NAME = "C3-Car-TB6612"
SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
COMMAND_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

# ---------------------------------------------------------------- 配色 / 字体
BG = "#05070d"
PANEL_BG = "#070d17"
GRID = "#0f1e30"
CYAN = "#00e5ff"
CYAN_DIM = "#0b5566"
GREEN = "#39ff14"
GREEN_DIM = "#124d10"
RED = "#ff2d55"
RED_DIM = "#5a1020"
AMBER = "#ffb300"
AMBER_DIM = "#5a3f00"
TEXT = "#cfe9ff"
MUTED = "#5b7186"
FILL_IDLE = "#0b1626"
FILL_HOVER = "#12335a"
FILL_PRESS = "#1b5a7a"

FONT_MONO = "Consolas"
FONT_UI = "Microsoft YaHei UI"

# 左/右转由舵机定位（固定脉宽 1000/2000µs），与速度滑杆无关。

# keysym -> 固件指令
KEYMAP = {
    "Up": "F", "w": "F", "W": "F",
    "Down": "B", "s": "B", "S": "B",
    "Left": "L", "a": "L", "A": "L",
    "Right": "R", "d": "R", "D": "R",
}


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画一个圆角矩形（用平滑多边形近似）。"""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


class BleLink:
    """把 bleak 的 asyncio 逻辑放到后台线程，GUI 通过队列收消息。"""

    def __init__(self, ui_q):
        self.ui_q = ui_q
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="ble-loop", daemon=True)
        self.client = None
        self._started = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _start(self):
        if not self._started:
            self.thread.start()
            self._started = True

    def _post(self, kind, data=""):
        self.ui_q.put((kind, data))

    def _submit(self, coro):
        self._start()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---- 对外接口（均在 GUI 线程调用） ----
    def scan_and_connect(self):
        self._submit(self._scan_and_connect())

    def disconnect(self):
        self._submit(self._async_disconnect())

    def send(self, text):
        if self.client is None:
            self._post("error", "尚未连接设备")
            return
        self._submit(self._write(text))

    # ---- 协程 ----
    async def _scan_and_connect(self):
        if self.client is not None and self.client.is_connected:
            self._post("status", "设备已连接")
            return
        self._post("scanning", "正在扫描 BLE 设备…")
        try:
            found = await BleakScanner.discover(timeout=10.0, return_adv=True)
        except Exception as exc:
            self._post("scan_failed", "扫描失败: {}".format(exc))
            return

        target = None
        names_seen = []
        for _addr, pair in found.items():
            dev, adv = pair
            name = getattr(adv, "local_name", None) or dev.name
            if name:
                names_seen.append(name)
            uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
            name_hit = bool(name) and DEVICE_NAME.lower() in name.lower()
            if name_hit or SERVICE_UUID.lower() in uuids:
                target = dev
                break

        if target is None:
            summary = "、".join(names_seen[:6]) if names_seen else "无"
            self._post(
                "scan_failed",
                "未找到 {}（共发现 {} 个设备：{}）。请确认板子已烧录固件、"
                "上电且未被其他设备连接。".format(DEVICE_NAME, len(found), summary),
            )
            return

        try:
            self.client = BleakClient(target, disconnected_callback=self._on_disconnect)
            await self.client.connect()
            self._post("connected", target.name or target.address)
        except Exception as exc:
            self.client = None
            self._post("scan_failed", "连接失败: {}".format(exc))

    async def _async_disconnect(self):
        client = self.client
        self.client = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        self._post("disconnected", "已断开连接")

    async def _write(self, text):
        client = self.client
        if client is None or not client.is_connected:
            self._post("error", "尚未连接设备")
            return
        payload = text.encode("utf-8")
        try:
            await client.write_gatt_char(COMMAND_UUID, payload, response=False)
        except Exception:
            try:
                await client.write_gatt_char(COMMAND_UUID, payload, response=True)
            except Exception as exc:
                self._post("error", "发送失败: {}".format(exc))
                return
        self._post("sent", text)

    def _on_disconnect(self, _client):
        self._post("disconnected", "连接已断开")


class CarConsole:
    def __init__(self, root):
        self.root = root
        root.title("C3-Car // TB6612 BLE Console")
        root.configure(bg=BG)
        root.geometry("1000x640")
        root.resizable(False, False)

        self.cv = tk.Canvas(root, width=1000, height=640, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)

        self.ui_q = queue.Queue()
        self.ble = BleLink(self.ui_q)

        self.t0 = time.time()
        self.anim_t = 0
        self.speed_digit = 0
        self.active_dir = None
        self.held = set()              # 已发送到固件的驱动指令集合（F/B 最多一个）
        self._press_seq = []           # 按下的输入源顺序 [(source_id, cmd), ...]
        self.pressed_keys = set()      # 物理键盘按键
        self.conn_state = "disconnected"
        self.status_msg = "等待连接设备"
        self.status_color = MUTED
        self.last_cmd = "S"
        self.light_on = False

        self.buttons = []
        self.pressed_btn = None
        self.hover_btn = None
        self.slider_pressed = False
        self.slider_x0 = 600
        self.slider_x1 = 944
        self.slider_y = 330
        self._closing = False
        self._hold_job = None

        self._build()
        self._bind()
        self._refresh_all()
        self._poll()
        self._tick()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ 绘制
    def _build(self):
        self._draw_grid()
        self._draw_brackets()
        self._draw_header()
        self._draw_panels()
        self.scan = self.cv.create_line(24, 104, 468, 104, fill=CYAN_DIM)
        self._draw_dpad()
        self._draw_right_controls()

    def _draw_grid(self):
        cv = self.cv
        for x in range(0, 1001, 40):
            cv.create_line(x, 0, x, 640, fill=GRID)
        for y in range(0, 641, 40):
            cv.create_line(0, y, 1000, y, fill=GRID)

    def _draw_brackets(self):
        cv = self.cv
        L = 26
        for (x, y, dx, dy) in ((14, 14, 1, 1), (986, 14, -1, 1),
                               (14, 626, 1, -1), (986, 626, -1, -1)):
            cv.create_line(x, y, x + dx * L, y, fill=CYAN_DIM, width=2)
            cv.create_line(x, y, x, y + dy * L, fill=CYAN_DIM, width=2)

    def _draw_header(self):
        cv = self.cv
        cv.create_text(36, 30, text="C3-CAR // TB6612", anchor="w",
                       fill=CYAN_DIM, font=(FONT_MONO, 20, "bold"))
        cv.create_text(34, 30, text="C3-CAR // TB6612", anchor="w",
                       fill=CYAN, font=(FONT_MONO, 20, "bold"))
        cv.create_text(36, 56, text="BLE MOTOR CONTROLLER  ·  ESP32-C3  ·  GATT UART",
                       anchor="w", fill=MUTED, font=(FONT_MONO, 9))
        cv.create_text(962, 30, text="DEVICE  " + DEVICE_NAME, anchor="e",
                       fill=TEXT, font=(FONT_MONO, 10))
        cv.create_text(962, 54, text="SERVICE 6E400001  ·  CHAR 6E400002", anchor="e",
                       fill=MUTED, font=(FONT_MONO, 8))
        cv.create_line(20, 80, 980, 80, fill=GRID)

    def _section(self, x, y, text):
        self.cv.create_text(x, y, text=text, anchor="w",
                            fill=CYAN, font=(FONT_UI, 10, "bold"))

    def _draw_panels(self):
        cv = self.cv
        # 左：运动控制
        round_rect(cv, 20, 96, 472, 616, 14, fill=PANEL_BG, outline=GRID, width=1)
        self._section(36, 114, "运动控制  //  MOTION")

        # 右上：连接
        round_rect(cv, 490, 96, 980, 262, 12, fill=PANEL_BG, outline=GRID, width=1)
        self._section(508, 114, "连接  //  LINK")

        # 右中：速度
        round_rect(cv, 490, 272, 980, 438, 12, fill=PANEL_BG, outline=GRID, width=1)
        self._section(508, 290, "前进 / 后退速度  //  SPEED")
        self.txt_speed_num = cv.create_text(508, 330, text="0", anchor="w",
                                            fill=RED, font=(FONT_MONO, 34, "bold"))
        self.txt_pwm = cv.create_text(508, 366, text="PWM 0", anchor="w",
                                      fill=MUTED, font=(FONT_MONO, 9))

        # 轨道
        cv.create_line(self.slider_x0, self.slider_y, self.slider_x1, self.slider_y,
                       fill=GRID, width=6, capstyle="round")
        self.slider_fill = cv.create_line(self.slider_x0, self.slider_y,
                                          self.slider_x0, self.slider_y,
                                          fill=CYAN, width=6, capstyle="round")
        for i in range(10):
            x = self.slider_x0 + i / 9.0 * (self.slider_x1 - self.slider_x0)
            cv.create_line(x, self.slider_y + 14, x, self.slider_y + 20, fill=MUTED)
            cv.create_text(x, self.slider_y + 30, text=str(i), fill=MUTED,
                           font=(FONT_MONO, 8))
        self.slider_knob = cv.create_oval(self.slider_x0 - 7, self.slider_y - 7,
                                          self.slider_x0 + 7, self.slider_y + 7,
                                          outline=CYAN, fill=BG, width=2)
        cv.create_text(600, 386, text="拖动滑条或按 0-9 设置 · 0 = 停止",
                       anchor="w", fill=MUTED, font=(FONT_UI, 9))
        cv.create_text(600, 404, text="左右转 = 舵机固定角度，点击后自动回中",
                       anchor="w", fill=CYAN_DIM, font=(FONT_UI, 9))

        # 右下：遥测
        round_rect(cv, 490, 448, 980, 548, 12, fill=PANEL_BG, outline=GRID, width=1)
        self._section(508, 466, "遥测  //  TELEMETRY")
        self.txt_lastcmd = cv.create_text(508, 496, text="", anchor="w",
                                          fill=TEXT, font=(FONT_MONO, 10))
        self.txt_uptime = cv.create_text(508, 524, text="", anchor="w",
                                         fill=TEXT, font=(FONT_MONO, 10))

    def _make_button(self, name, rect, label, color, on_press,
                     on_release=None, font_size=15, sub=None, radius=12):
        x1, y1, x2, y2 = rect
        cv = self.cv
        poly = round_rect(cv, x1, y1, x2, y2, radius, fill=FILL_IDLE,
                          outline=color, width=2)
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        ty = cy - 9 if sub else cy
        txt = cv.create_text(cx, ty, text=label, fill=color,
                             font=(FONT_UI, font_size, "bold"))
        sub_item = None
        if sub:
            sub_item = cv.create_text(cx, cy + 13, text=sub, fill=MUTED,
                                      font=(FONT_MONO, 8))
        btn = {"name": name, "rect": rect, "color": color, "poly": poly,
               "txt": txt, "sub": sub_item, "on_press": on_press,
               "on_release": on_release, "state": "idle"}
        self.buttons.append(btn)
        return btn

    def _draw_dpad(self):
        cv = self.cv
        # 中央状态盘
        cv.create_oval(192, 298, 300, 406, outline=CYAN_DIM, width=1, fill="#08131f")
        cv.create_oval(206, 312, 286, 392, outline=CYAN_DIM, width=1)
        self.hub_text = cv.create_text(246, 340, text="S", fill=CYAN,
                                       font=(FONT_MONO, 28, "bold"))
        self.hub_sub = cv.create_text(246, 372, text="STOP", fill=MUTED,
                                      font=(FONT_MONO, 8))

        self._make_button("F", (176, 152, 316, 236), "前进", CYAN,
                          lambda: self._dir_press("F"), self._dir_release, sub="F")
        self._make_button("B", (176, 468, 316, 552), "后退", CYAN,
                          lambda: self._dir_press("B"), self._dir_release, sub="B")
        self._make_button("L", (54, 312, 188, 396), "左转", CYAN,
                          lambda: self._send("L"), sub="L")
        self._make_button("R", (304, 312, 438, 396), "右转", CYAN,
                          lambda: self._send("R"), sub="R")

        # 车灯开关（点击切换，前大灯 + 尾灯同亮同灭，联动翻灯舵机）
        self._section(36, 560, "灯光 / 翻灯  //  LIGHTS")
        self._make_button("light", (54, 570, 438, 610), "车灯控制", CYAN_DIM,
                          lambda: self._toggle_light(), sub="OFF",
                          font_size=12, radius=8)

    def _draw_right_controls(self):
        cv = self.cv
        self.led = cv.create_oval(510, 136, 522, 148, fill=RED_DIM, outline=RED)
        self.txt_conn = cv.create_text(532, 142, text="OFFLINE", anchor="w",
                                       fill=RED, font=(FONT_MONO, 10, "bold"))
        self.txt_device = cv.create_text(508, 164, text="设备  " + DEVICE_NAME,
                                         anchor="w", fill=TEXT, font=(FONT_UI, 9))
        self.txt_status_line = cv.create_text(
            508, 194, text=self.status_msg, anchor="nw", width=452,
            justify="left", fill=self.status_color, font=(FONT_UI, 9))

        self._make_button("connect", (704, 106, 958, 144), "扫描并连接", CYAN,
                          self.ble.scan_and_connect, font_size=12, radius=8)
        self._make_button("disconnect", (704, 152, 958, 190), "断开连接", AMBER,
                          self._on_disconnect_click, font_size=12, radius=8)
        self._make_button("estop", (490, 558, 980, 616),
                          "急停   E-STOP        [ 空格 ]", RED,
                          self.emergency_stop, font_size=16, radius=12)

    # ------------------------------------------------------------ 事件绑定
    def _bind(self):
        self.cv.bind("<ButtonPress-1>", self._on_click)
        self.cv.bind("<ButtonRelease-1>", self._on_mouse_release)
        self.cv.bind("<Motion>", self._on_motion)
        self.root.bind("<KeyPress>", self._on_key_down)
        self.root.bind("<KeyRelease>", self._on_key_up)
        self.root.focus_set()

    def _btn_at(self, x, y):
        for btn in self.buttons:
            x1, y1, x2, y2 = btn["rect"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return btn
        return None

    def _set_btn_state(self, btn, state):
        if btn["state"] == state:
            return
        btn["state"] = state
        fills = {"idle": FILL_IDLE, "hover": FILL_HOVER, "press": FILL_PRESS}
        widths = {"idle": 2, "hover": 3, "press": 3}
        self.cv.itemconfig(btn["poly"], fill=fills[state], outline=btn["color"],
                           width=widths[state])

    def _on_click(self, event):
        if self._slider_hit(event.x, event.y):
            self.slider_pressed = True
            self._slider_set_from_x(event.x)
            return
        btn = self._btn_at(event.x, event.y)
        if btn is None:
            return
        self.pressed_btn = btn
        self.hover_btn = btn
        self._set_btn_state(btn, "press")
        if btn["on_press"]:
            btn["on_press"]()

    def _on_mouse_release(self, event):
        self.slider_pressed = False
        btn = self.pressed_btn
        self.pressed_btn = None
        if btn is not None:
            self._set_btn_state(btn, "idle")
            if btn["on_release"]:
                btn["on_release"]()
        hovered = self._btn_at(event.x, event.y)
        self.hover_btn = hovered
        if hovered is not None and hovered is not btn:
            self._set_btn_state(hovered, "hover")

    def _on_motion(self, event):
        if self.slider_pressed:
            self._slider_set_from_x(event.x)
            return
        hovered = self._btn_at(event.x, event.y)
        if hovered is self.hover_btn:
            return
        if self.hover_btn is not None and self.hover_btn is not self.pressed_btn:
            self._set_btn_state(self.hover_btn, "idle")
        self.hover_btn = hovered
        if hovered is not None and hovered is not self.pressed_btn:
            self._set_btn_state(hovered, "hover")

    # ------------------------------------------------------------ 键盘
    def _on_key_down(self, event):
        key = event.keysym
        if key in ("space", "Escape"):
            self.emergency_stop()
            return "break"
        cmd = KEYMAP.get(key)
        if cmd in ("L", "R"):
            self._send(cmd)   # 键盘左/右：点击转向，舵机自动回中
            return "break"
        if key in self.pressed_keys:
            return "break"
        if cmd is not None:
            self.pressed_keys.add(key)
            self._on_press(("key", key), cmd)
        return "break"

    def _on_key_up(self, event):
        key = event.keysym
        if key not in self.pressed_keys:
            return "break"
        self.pressed_keys.discard(key)
        self._on_release(("key", key))
        return "break"

    # ------------------------------------------------------------ 动作
    def _dir_press(self, cmd):
        self._on_press(("mouse", None), cmd)

    def _dir_release(self):
        self._on_release(("mouse", None))

    def _on_press(self, sid, cmd):
        # 同一输入源去重后按顺序记录，靠“后按覆盖同轴方向”实现轴内互斥
        self._press_seq = [(i, c) for (i, c) in self._press_seq if i != sid]
        self._press_seq.append((sid, cmd))
        self._sync_held()

    def _on_release(self, sid):
        self._press_seq = [(i, c) for (i, c) in self._press_seq if i != sid]
        self._sync_held()

    def _active_cmds(self):
        """由当前按住的输入源，算出驱动轴(F/B)指令。转向(L/R)走锁存，不在此处。"""
        drive = None
        for _sid, cmd in self._press_seq:
            if cmd in ("F", "B"):
                drive = cmd
        return {drive} if drive is not None else set()

    def _sync_held(self):
        desired = self._active_cmds()
        added = desired - self.held
        removed = self.held - desired

        if removed:
            self.ble.send("S")  # 释放了驱动轴：先停，再恢复仍按住的轴
            for cmd in sorted(desired):
                self.ble.send(cmd)
        else:
            for cmd in sorted(added):
                self.ble.send(cmd)

        self.held = desired
        if desired:
            self.active_dir = sorted(desired)[-1]
            self.last_cmd = self.active_dir
            self._set_hub(desired)
            self._schedule_hold()
        else:
            self._cancel_hold()
            self.active_dir = None
            self.last_cmd = "S"
            self._set_hub("S")
        self._refresh_status()

    def _dir_stop(self):
        self._cancel_hold()
        self.pressed_keys.clear()
        self._press_seq = []
        self.held = set()
        self.active_dir = None
        self._send("S")
        self._set_hub("S")

    def emergency_stop(self):
        self._cancel_hold()
        self.active_dir = None
        self.pressed_keys.clear()
        self._press_seq = []
        self.held = set()
        self._send("S")
        self._set_hub("S")
        self._flash_estop()

    # 按住前进/后退期间每 150ms 重发一次 F/B（固件 1.5s 失联保护）；
    # 松开时发送 S，立即停车并回中。
    def _schedule_hold(self):
        self._cancel_hold()
        self._hold_job = self.root.after(150, self._hold_tick)

    def _hold_tick(self):
        self._hold_job = None
        if self._closing or not self.held:
            return
        for cmd in sorted(self.held):
            self.ble.send(cmd)
        self._schedule_hold()

    def _cancel_hold(self):
        if self._hold_job is not None:
            try:
                self.root.after_cancel(self._hold_job)
            except Exception:
                pass
            self._hold_job = None

    def _on_disconnect_click(self):
        self._dir_stop()
        self.ble.disconnect()

    def _send(self, cmd):
        self.last_cmd = cmd
        self.ble.send(cmd)
        self._refresh_status()

    def _set_hub(self, held):
        if isinstance(held, str):
            held = set() if held == "S" else {held}
        if not held:
            text, sub, col = "S", "STOP", CYAN
        else:
            drive = next((c for c in ("F", "B") if c in held), None)
            text = drive or "S"
            sub = {"F": "FWD", "B": "REV"}.get(drive, "STOP")
            col = GREEN if drive else CYAN
        self.cv.itemconfig(self.hub_text, text=text, fill=col)
        self.cv.itemconfig(self.hub_sub, text=sub)

    def _toggle_light(self):
        self.light_on = not self.light_on
        self._send("H")
        self._refresh_lights()

    def _refresh_lights(self):
        btn = next((b for b in self.buttons if b["name"] == "light"), None)
        if btn is None:
            return
        on_color = AMBER
        btn["color"] = on_color if self.light_on else CYAN_DIM
        self.cv.itemconfig(btn["txt"], fill=btn["color"])
        self.cv.itemconfig(btn["poly"], outline=btn["color"])
        self.cv.itemconfig(btn["sub"],
                           text="ON" if self.light_on else "OFF",
                           fill=on_color if self.light_on else MUTED)

    def _flash_estop(self):
        btn = next((b for b in self.buttons if b["name"] == "estop"), None)
        if btn is None:
            return
        self.cv.itemconfig(btn["poly"], fill="#ff2d55", outline="#ffffff")
        self.root.after(160, lambda: self.cv.itemconfig(
            btn["poly"], fill=FILL_IDLE, outline=btn["color"], width=2))

    # ------------------------------------------------------------ 速度
    def _slider_hit(self, x, y):
        return (self.slider_x0 - 12 <= x <= self.slider_x1 + 12 and
                self.slider_y - 16 <= y <= self.slider_y + 16)

    def _slider_set_from_x(self, x):
        t = (x - self.slider_x0) / float(self.slider_x1 - self.slider_x0)
        t = max(0.0, min(1.0, t))
        self._set_speed(int(round(t * 9)))

    def _set_speed(self, digit):
        digit = max(0, min(9, digit))
        if digit != self.speed_digit:
            self.speed_digit = digit
            self._send(str(digit))
        self._refresh_speed()

    # ------------------------------------------------------------ 刷新
    def _refresh_all(self):
        self._refresh_status()
        self._refresh_speed()
        self._refresh_lights()

    def _refresh_status(self):
        if self.conn_state == "connected":
            col, txt = GREEN, "ONLINE"
            self.cv.itemconfig(self.led, fill=GREEN, outline=GREEN)
        elif self.conn_state == "scanning":
            col, txt = AMBER, "SCANNING"
            self.cv.itemconfig(self.led, fill=AMBER, outline=AMBER)
        else:
            col, txt = RED, "OFFLINE"
            self.cv.itemconfig(self.led, fill=RED_DIM, outline=RED)
        self.cv.itemconfig(self.txt_conn, text=txt, fill=col)
        self.cv.itemconfig(self.txt_lastcmd,
                           text="最近指令  {}   (前进速度 {})".format(self.last_cmd, self.speed_digit))
        self.cv.itemconfig(self.txt_status_line, text=self.status_msg,
                           fill=self.status_color)

    def _refresh_speed(self):
        d = self.speed_digit
        pwm = int(round(d * 255 / 9.0))
        col = RED if d == 0 else (CYAN if d < 7 else GREEN)
        self.cv.itemconfig(self.txt_speed_num, text=str(d), fill=col)
        self.cv.itemconfig(self.txt_pwm, text="PWM {}".format(pwm))
        knx = self.slider_x0 + d / 9.0 * (self.slider_x1 - self.slider_x0)
        self.cv.coords(self.slider_fill, self.slider_x0, self.slider_y, knx, self.slider_y)
        self.cv.itemconfig(self.slider_fill, fill=col)
        self.cv.coords(self.slider_knob, knx - 7, self.slider_y - 7,
                       knx + 7, self.slider_y + 7)
        self.cv.itemconfig(self.slider_knob, outline=col)

    # ------------------------------------------------------------ BLE 消息
    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                kind, data = self.ui_q.get_nowait()
                self._handle_msg(kind, data)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle_msg(self, kind, data):
        if kind == "scanning":
            self.conn_state = "scanning"
            self.status_msg = data
            self.status_color = AMBER
        elif kind == "connected":
            self.conn_state = "connected"
            self.status_msg = "已连接  " + str(data)
            self.status_color = GREEN
            self.last_cmd = "S"
            self._cancel_hold()
            self.active_dir = None
            self.pressed_keys.clear()
            self._press_seq = []
            self.held = set()
            self._set_hub("S")
            self.ble.send("S")  # 连接后先确保停止（回中）
            self.ble.send(str(self.speed_digit))  # 同步当前速度到固件
            self.light_on = False
            self._refresh_lights()
        elif kind == "disconnected":
            self.conn_state = "disconnected"
            self.status_msg = data or "已断开"
            self.status_color = MUTED
            self._cancel_hold()
            self.active_dir = None
            self.pressed_keys.clear()
            self._press_seq = []
            self.held = set()
            self._set_hub("S")
            self.light_on = False
            self._refresh_lights()
        elif kind == "scan_failed":
            self.conn_state = "disconnected"
            self.status_msg = str(data)
            self.status_color = RED
        elif kind == "sent":
            self.last_cmd = str(data)
            self.status_msg = "已发送  {}".format(data)
            self.status_color = MUTED
        elif kind == "status":
            self.status_msg = str(data)
            self.status_color = MUTED
        elif kind == "error":
            self.status_msg = str(data)
            self.status_color = RED
        self._refresh_status()

    # ------------------------------------------------------------ 动画
    def _tick(self):
        if self._closing:
            return
        self.anim_t += 1
        y = 104 + (self.anim_t * 2) % 500
        self.cv.coords(self.scan, 24, y, 468, y)
        if self.conn_state == "connected":
            on = (self.anim_t // 8) % 2 == 0
            self.cv.itemconfig(self.led, fill=GREEN if on else GREEN_DIM,
                               outline=GREEN)
        elapsed = int(time.time() - self.t0)
        self.cv.itemconfig(self.txt_uptime,
                           text="运行时间  %02d:%02d:%02d" %
                                (elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60))
        self.root.after(40, self._tick)

    # ------------------------------------------------------------ 关闭
    def _on_close(self):
        try:
            self.emergency_stop()
        except Exception:
            pass
        self._closing = True
        self.root.after(220, self.root.destroy)


def main():
    if BleakClient is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "缺少依赖 bleak",
            "未安装 bleak：{}\n\n请先安装：\n    pip install bleak\n\n"
            "或用：python -m pip install -r tools/requirements.txt".format(_BLEAK_ERR),
        )
        root.destroy()
        return
    root = tk.Tk()
    CarConsole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
