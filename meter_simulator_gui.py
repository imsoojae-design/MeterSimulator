#!/usr/bin/env python3
"""
수도미터 계량기 USB 시뮬레이터 GUI (Windows)
서울특별시 디지털계량기 프로토콜 V1.2
tkinter 기반 - 추가 설치 없이 실행
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import serial
import serial.tools.list_ports
from datetime import datetime

# ── 프로토콜 상수 ─────────────────────────────────────
SHORT_START = 0x10
LONG_START  = 0x68
STOP_BYTE   = 0x16
C_REQ_UD2_A = 0x5B
C_REQ_UD2_B = 0x7B
C_REP_UD    = 0x08
CI_FIELD    = 0x78
MDH_FIELD   = 0x0F
DIAM_CODE   = {15:1,20:2,25:3,32:4,40:5,50:6,80:7,100:8,150:9,200:10,250:11,300:12}
DIAM_LIST   = [15,20,25,32,40,50,80,100,150,200,250,300]

# ════════════════════════════════════════════════
#  BCD / 프레임
# ════════════════════════════════════════════════
def bcd_encode_meter_no(s):
    s = s.replace('-','').replace(' ','').zfill(8)
    pairs = [(int(s[i])<<4)|int(s[i+1]) for i in range(0,8,2)]
    return bytes(reversed(pairs))

def bcd_encode_reading(value, decimals):
    iv = round(value * (10**decimals))
    s  = f"{iv:08d}"
    pairs = [(int(s[i])<<4)|int(s[i+1]) for i in range(0,8,2)]
    return bytes(reversed(pairs))

def bcd_decode_le(data):
    rev = bytes(reversed(data))
    r = ''
    for b in rev:
        r += str((b>>4)&0xF) + str(b&0xF)
    return r

def build_long_frame(cfg, addr):
    id_bcd  = bcd_encode_meter_no(cfg['meter_no'])
    status  = (0x80 if cfg['q3'] else 0)|(0x40 if cfg['rev'] else 0)|(0x20 if cfg['leak'] else 0)|(0x04 if cfg['batt'] else 0)
    dc      = DIAM_CODE.get(cfg['diameter'],1)
    dif     = (dc<<4)|0x0C
    vif     = 0x10|(cfg['decimal']&0x0F)
    reading = bcd_encode_reading(cfg['reading'], cfg['decimal'])
    ud      = bytes([MDH_FIELD]) + id_bcd + bytes([status,dif,vif]) + reading
    if cfg['use_udf']:
        pv = cfg['proto_ver']
        vm = cfg['verify_mo']
        mc = ord(cfg['mfr_code'][0]) if cfg['mfr_code'] else 0x41
        ud += bytes([((pv//10)<<4)|(pv%10), ((vm//10)<<4)|(vm%10), 0x00, mc])
    l  = 3 + len(ud)
    ck = sum([C_REP_UD, addr&0xFF, CI_FIELD]+list(ud)) & 0xFF
    return bytes([LONG_START,l,l,LONG_START,C_REP_UD,addr&0xFF,CI_FIELD])+ud+bytes([ck,STOP_BYTE])

def parse_long_frame(data):
    r = {'ok':False}
    if len(data)<14: return r
    if data[0]!=LONG_START or data[3]!=LONG_START: return r
    if data[-1]!=STOP_BYTE: return r
    ud  = data[7:len(data)-2]
    chk = sum(data[4:-2])&0xFF
    r['checksum_ok'] = (chk==data[-2])
    r['addr']   = data[5]
    id_str      = bcd_decode_le(ud[1:5])
    r['meter_no'] = id_str[:2]+'-'+id_str[2:]
    st          = ud[5]
    r['status'] = {'q3':bool(st&0x80),'rev':bool(st&0x40),'leak':bool(st&0x20),'batt':bool(st&0x04)}
    dec         = ud[7]&0x0F
    dc          = (ud[6]>>4)&0xF
    r['diameter']= {1:15,2:20,3:25,4:32,5:40,6:50,7:80,8:100,9:150,10:200,11:250,12:300}.get(dc,0)
    r['decimal'] = dec
    rv           = int(bcd_decode_le(ud[8:12]))/(10**dec)
    r['reading'] = round(rv, dec)
    r['ok']      = True
    return r

def is_req_ud2(data):
    if len(data)<5: return False,0
    if data[0]!=SHORT_START or data[4]!=STOP_BYTE: return False,0
    c,a,chk = data[1],data[2],data[3]
    if (c+a)&0xFF!=chk: return False,0
    if c not in (C_REQ_UD2_A,C_REQ_UD2_B): return False,0
    return True,a

# ════════════════════════════════════════════════
#  GUI 앱
# ════════════════════════════════════════════════
class MeterSimApp:
    def __init__(self, root):
        self.root   = root
        self.root.title("수도미터 USB 시뮬레이터  V1.2")
        self.root.geometry("820x700")
        self.root.configure(bg='#0A0F1E')
        self.root.resizable(True, True)

        # 설정값
        self.cfg = {
            'meter_no': '21-000006',
            'reading' : 556.950,
            'diameter': 15,
            'decimal' : 3,
            'q3'      : False,
            'rev'     : False,
            'leak'    : False,
            'batt'    : False,
            'use_udf' : False,
            'proto_ver': 12,
            'verify_mo': 5,
            'mfr_code' : 'A',
        }

        self.port_obj    = None
        self.com_running = False
        self.rx_count    = 0
        self.tx_count    = 0

        self._build_ui()
        self._refresh_ports()
        self._update_frame_preview()

    # ── UI 빌드 ────────────────────────────────────────
    def _build_ui(self):
        BG   = '#0A0F1E'
        BG2  = '#111827'
        CARD = '#151D2E'
        ACC  = '#38BDF8'
        GRN  = '#34D399'
        YLW  = '#FBBF24'
        RED  = '#F87171'
        TXT  = '#E2E8F0'
        MUT  = '#64748B'
        FONT = ('Consolas', 10)
        FONTB= ('Consolas', 10, 'bold')

        # ── 헤더 ──
        hdr = tk.Frame(self.root, bg=BG2, height=48)
        hdr.pack(fill='x')
        tk.Label(hdr, text='💧 수도미터 USB 시뮬레이터', bg=BG2,
                 fg=TXT, font=('Consolas',13,'bold')).pack(side='left',padx=16,pady=10)
        tk.Label(hdr, text='V1.2  |  서울특별시 디지털계량기 프로토콜', bg=BG2,
                 fg=MUT, font=FONT).pack(side='left')
        self.lbl_stat = tk.Label(hdr, text='● 연결 안됨', bg=BG2, fg=MUT, font=FONTB)
        self.lbl_stat.pack(side='right', padx=16)

        # ── 메인 PanedWindow ──
        pane = tk.PanedWindow(self.root, orient='horizontal', bg=BG, sashwidth=4)
        pane.pack(fill='both', expand=True, padx=8, pady=6)

        # ── 왼쪽 패널 ──
        left = tk.Frame(pane, bg=BG)
        pane.add(left, minsize=340)

        # COM 포트 카드
        c1 = self._card(left, '🔌 COM 포트')
        row = tk.Frame(c1, bg=CARD)
        row.pack(fill='x', pady=4)
        tk.Label(row, text='포트', bg=CARD, fg=MUT, font=FONT, width=6).pack(side='left')
        self.cb_port = ttk.Combobox(row, width=12, font=FONT)
        self.cb_port.pack(side='left', padx=4)
        tk.Button(row, text='새로고침', bg='#1E3A5F', fg=TXT, font=FONT,
                  command=self._refresh_ports, relief='flat', padx=6
                  ).pack(side='left', padx=2)

        row2 = tk.Frame(c1, bg=CARD)
        row2.pack(fill='x', pady=4)
        tk.Label(row2, text='속도', bg=CARD, fg=MUT, font=FONT, width=6).pack(side='left')
        self.cb_baud = ttk.Combobox(row2, values=['1200','2400','9600'], width=8, font=FONT)
        self.cb_baud.set('1200'); self.cb_baud.pack(side='left', padx=4)

        self.btn_conn = tk.Button(c1, text='연결', bg='#0EA5E9', fg='white',
                                  font=('Consolas',11,'bold'), relief='flat',
                                  command=self._toggle_connect, padx=10, pady=6)
        self.btn_conn.pack(fill='x', pady=6)

        # 카운터
        cf = tk.Frame(c1, bg=CARD)
        cf.pack(fill='x')
        for lbl,var in [('RX (수신)','rx'),('TX (응답)','tx')]:
            ff = tk.Frame(cf, bg=CARD)
            ff.pack(side='left', expand=True)
            tk.Label(ff, text=lbl, bg=CARD, fg=MUT, font=FONT).pack()
            lv = tk.Label(ff, text='0', bg=CARD, fg=ACC,
                          font=('Consolas',20,'bold'))
            lv.pack()
            setattr(self, f'lbl_{var}', lv)

        # 계량기 정보 카드
        c2 = self._card(left, '📟 계량기 정보')
        self._field(c2, '기물번호', 'meter_no', '21-000006')
        self._field(c2, '검침값(㎥)', 'reading', '556.950')

        row3 = tk.Frame(c2, bg=CARD)
        row3.pack(fill='x', pady=3)
        tk.Label(row3, text='구경(mm)', bg=CARD, fg=MUT, font=FONT, width=10, anchor='w').pack(side='left')
        self.cb_diam = ttk.Combobox(row3, values=[str(d) for d in DIAM_LIST], width=8, font=FONT)
        self.cb_diam.set('15'); self.cb_diam.pack(side='left', padx=4)
        self.cb_diam.bind('<<ComboboxSelected>>', lambda e: self._apply_cfg())

        row4 = tk.Frame(c2, bg=CARD)
        row4.pack(fill='x', pady=3)
        tk.Label(row4, text='소수점', bg=CARD, fg=MUT, font=FONT, width=10, anchor='w').pack(side='left')
        self.cb_dec = ttk.Combobox(row4, values=['1','2','3','4'], width=4, font=FONT)
        self.cb_dec.set('3'); self.cb_dec.pack(side='left', padx=4)
        self.cb_dec.bind('<<ComboboxSelected>>', lambda e: self._apply_cfg())

        row5 = tk.Frame(c2, bg=CARD)
        row5.pack(fill='x', pady=3)
        tk.Label(row5, text='주소', bg=CARD, fg=MUT, font=FONT, width=10, anchor='w').pack(side='left')
        self.ent_addr = tk.Entry(row5, width=6, bg='#1E2A40', fg=TXT, font=FONT,
                                 insertbackground=TXT, relief='flat')
        self.ent_addr.insert(0,'1'); self.ent_addr.pack(side='left', padx=4)

        tk.Button(c2, text='적용', bg='#0EA5E9', fg='white', font=FONTB,
                  relief='flat', command=self._apply_cfg, pady=5
                  ).pack(fill='x', pady=6)

        # 경보 카드
        c3 = self._card(left, '⚠ 경보 설정')
        alarms = [('Q3 초과 (최대유량)','q3','#F87171'),
                  ('역류 (1분 이상)','rev','#FBBF24'),
                  ('옥내 누수 (7일)','leak','#A78BFA'),
                  ('배터리 부족','batt','#FB923C')]
        self.alarm_vars = {}
        for lbl, key, color in alarms:
            var = tk.BooleanVar()
            self.alarm_vars[key] = var
            cb = tk.Checkbutton(c3, text=lbl, variable=var, bg=CARD,
                                fg=color, selectcolor='#1E2A40',
                                activebackground=CARD, font=FONTB,
                                command=self._apply_cfg)
            cb.pack(anchor='w', pady=2)

        btn_row = tk.Frame(c3, bg=CARD)
        btn_row.pack(fill='x', pady=4)
        tk.Button(btn_row, text='전체 OFF', bg='#1E3A5F', fg=TXT, font=FONT,
                  relief='flat', command=self._clear_alarms, padx=8
                  ).pack(side='left', padx=2)
        tk.Button(btn_row, text='전체 ON', bg='#7C3AED', fg='white', font=FONT,
                  relief='flat', command=self._set_all_alarms, padx=8
                  ).pack(side='left', padx=2)

        # ── 오른쪽 패널 ──
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=380)

        # 프레임 미리보기
        c4 = self._card(right, '📋 프레임 미리보기')
        tk.Label(c4, text='REQ (검침 요청)', bg=CARD, fg=MUT, font=FONT).pack(anchor='w')
        self.lbl_req = tk.Label(c4, text='', bg='#060D1A', fg='#FFD700',
                                 font=('Consolas',10), anchor='w', wraplength=380, justify='left')
        self.lbl_req.pack(fill='x', pady=2)
        tk.Label(c4, text='REP (응답)', bg=CARD, fg=MUT, font=FONT).pack(anchor='w', pady=(6,0))
        self.lbl_rep = tk.Label(c4, text='', bg='#060D1A', fg='#00CFFF',
                                 font=('Consolas',10), anchor='w', wraplength=380, justify='left')
        self.lbl_rep.pack(fill='x', pady=2)

        # 파싱 결과
        c5 = self._card(right, '🔍 파싱 결과')
        self.lbl_parse = tk.Label(c5, text='', bg=CARD, fg=TXT,
                                   font=('Consolas',10), anchor='w', justify='left')
        self.lbl_parse.pack(fill='x')

        tk.Button(c4, text='🔄 프레임 갱신', bg='#1E3A5F', fg=TXT,
                  font=FONTB, relief='flat', command=self._update_frame_preview, pady=4
                  ).pack(fill='x', pady=4)

        # 로그
        c6 = self._card(right, '📝 통신 로그', expand=True)
        self.log_box = scrolledtext.ScrolledText(
            c6, height=10, bg='#060D1A', fg='#94A3B8',
            font=('Consolas',9), insertbackground='white',
            relief='flat', state='disabled')
        self.log_box.pack(fill='both', expand=True)
        self.log_box.tag_config('ok',   foreground='#34D399')
        self.log_box.tag_config('warn', foreground='#FBBF24')
        self.log_box.tag_config('err',  foreground='#F87171')
        self.log_box.tag_config('hex',  foreground='#38BDF8')
        self.log_box.tag_config('info', foreground='#94A3B8')

        btn_log = tk.Frame(c6, bg=CARD)
        btn_log.pack(fill='x', pady=2)
        tk.Button(btn_log, text='로그 지우기', bg='#1E3A5F', fg=TXT,
                  font=FONT, relief='flat', command=self._clear_log
                  ).pack(side='right')

    def _card(self, parent, title, expand=False):
        outer = tk.Frame(parent, bg='#151D2E', padx=12, pady=10)
        outer.pack(fill='x' if not expand else 'both', expand=expand,
                   pady=4, padx=4)
        tk.Label(outer, text=title, bg='#151D2E', fg='#38BDF8',
                 font=('Consolas',10,'bold')).pack(anchor='w', pady=(0,6))
        return outer

    def _field(self, parent, label, key, default):
        row = tk.Frame(parent, bg='#151D2E')
        row.pack(fill='x', pady=3)
        tk.Label(row, text=label, bg='#151D2E', fg='#64748B',
                 font=('Consolas',10), width=10, anchor='w').pack(side='left')
        ent = tk.Entry(row, bg='#1E2A40', fg='#E2E8F0',
                       font=('Consolas',10), insertbackground='white',
                       relief='flat', width=18)
        ent.insert(0, default)
        ent.pack(side='left', padx=4)
        setattr(self, f'ent_{key}', ent)

    # ── 포트 목록 새로고침 ──
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_port['values'] = ports
        if ports: self.cb_port.set(ports[0])

    # ── 설정 적용 ──
    def _read_cfg(self):
        """UI 값을 cfg에 반영 (재귀 없음)"""
        try:
            mn = self.ent_meter_no.get().strip().replace('-','').replace(' ','')
            if len(mn)==8 and mn.isdigit():
                self.cfg['meter_no'] = mn[:2]+'-'+mn[2:]
            self.cfg['reading']  = float(self.ent_reading.get())
            self.cfg['diameter'] = int(self.cb_diam.get())
            # 소수점: 콤보박스 선택값 우선 사용
            self.cfg['decimal']  = int(self.cb_dec.get())
        except: pass
        for key, var in self.alarm_vars.items():
            self.cfg[key] = var.get()

    def _apply_cfg(self):
        self._read_cfg()
        self._update_frame_preview()

    def _clear_alarms(self):
        for v in self.alarm_vars.values(): v.set(False)
        self._apply_cfg()

    def _set_all_alarms(self):
        for v in self.alarm_vars.values(): v.set(True)
        self._apply_cfg()

    # ── 프레임 미리보기 갱신 ──
    def _update_frame_preview(self):
        self._read_cfg()
        try:
            addr    = int(self.ent_addr.get())
        except:
            addr    = 1
        req = bytes([SHORT_START, C_REQ_UD2_A, addr&0xFF,
                     (C_REQ_UD2_A+addr)&0xFF, STOP_BYTE])
        rep = build_long_frame(self.cfg, addr)

        req_hex = ' '.join(f'{b:02X}' for b in req)
        rep_hex = ' '.join(f'{b:02X}' for b in rep)
        self.lbl_req.config(text=f'{req_hex}  ({len(req)}B)')
        self.lbl_rep.config(text=f'{rep_hex}  ({len(rep)}B)')

        # 파싱 결과
        p = parse_long_frame(rep)
        if p['ok']:
            st = p['status']
            alarm = []
            if st['q3']:   alarm.append('Q3초과')
            if st['rev']:  alarm.append('역류')
            if st['leak']: alarm.append('누수')
            if st['batt']: alarm.append('배터리')
            al = ', '.join(alarm) if alarm else '정상'
            chk = '✓' if p['checksum_ok'] else '✗'
            self.lbl_parse.config(
                text=f"  기물번호  {p['meter_no']}\n"
                     f"  검침값    {p['reading']:.{p['decimal']}f} ㎥\n"
                     f"  구경      {p['diameter']} mm\n"
                     f"  상태      {al}\n"
                     f"  체크섬    {chk} {'정상' if p['checksum_ok'] else '오류'}"
            )

    # ── COM 연결/해제 ──
    def _toggle_connect(self):
        if self.port_obj and self.port_obj.is_open:
            self.com_running = False
            self.port_obj.close()
            self.port_obj = None
            self.btn_conn.config(text='연결', bg='#0EA5E9')
            self.lbl_stat.config(text='● 연결 안됨', fg='#64748B')
            self._log('연결 해제', 'warn')
        else:
            port = self.cb_port.get()
            baud = int(self.cb_baud.get())
            if not port:
                messagebox.showerror('오류', 'COM 포트를 선택하세요')
                return
            try:
                self.port_obj = serial.Serial(
                    port=port, baudrate=baud,
                    bytesize=8, parity='N', stopbits=1, timeout=0.2)
                self.port_obj.setDTR(True)
                self.port_obj.setRTS(True)
                self.com_running = True
                threading.Thread(target=self._serial_loop, daemon=True).start()
                self.btn_conn.config(text='연결 해제', bg='#F87171')
                self.lbl_stat.config(text=f'● {port} 연결됨', fg='#34D399')
                self._log(f'연결됨: {port} ({baud}bps 8N1)', 'ok')
            except Exception as e:
                messagebox.showerror('연결 실패', str(e))

    # ── 시리얼 수신 루프 ──
    def _serial_loop(self):
        buf = b''
        while self.com_running and self.port_obj and self.port_obj.is_open:
            try:
                chunk = self.port_obj.read(64)
                if not chunk: continue
                buf += chunk
                while len(buf) >= 5:
                    if buf[0] == SHORT_START:
                        ok, addr = is_req_ud2(buf[:5])
                        if ok:
                            self.rx_count += 1
                            hex_rx = ' '.join(f'{b:02X}' for b in buf[:5])
                            self._log(f'← REQ [{self.rx_count}] addr={addr} | {hex_rx}', 'hex')
                            time.sleep(0.05)  # High 대기
                            resp = build_long_frame(self.cfg, addr)
                            self.port_obj.write(resp)
                            self.tx_count += 1
                            hex_tx = ' '.join(f'{b:02X}' for b in resp)
                            self._log(f'→ REP [{self.tx_count}] {len(resp)}B | {hex_tx}', 'ok')
                            self._log(f'   기물:{self.cfg["meter_no"]}  검침:{self.cfg["reading"]:.{self.cfg["decimal"]}f}㎥', 'info')
                            self._update_counters()
                            buf = buf[5:]
                        else:
                            buf = buf[1:]
                    else:
                        buf = buf[1:]
            except Exception as e:
                if self.com_running:
                    self._log(f'오류: {e}', 'err')
                break

    def _update_counters(self):
        self.root.after(0, lambda: (
            self.lbl_rx.config(text=str(self.rx_count)),
            self.lbl_tx.config(text=str(self.tx_count))
        ))

    # ── 로그 ──
    def _log(self, msg, level='info'):
        ts = datetime.now().strftime('%H:%M:%S')
        line = f'[{ts}] {msg}\n'
        def _write():
            self.log_box.config(state='normal')
            self.log_box.insert('end', line, level)
            self.log_box.see('end')
            self.log_box.config(state='disabled')
        self.root.after(0, _write)

    def _clear_log(self):
        self.log_box.config(state='normal')
        self.log_box.delete('1.0','end')
        self.log_box.config(state='disabled')

# ════════════════════════════════════════════════
#  진입점
# ════════════════════════════════════════════════
if __name__ == '__main__':
    root = tk.Tk()
    try:
        root.iconbitmap(default='')
    except: pass
    style = ttk.Style()
    style.theme_use('clam')
    style.configure('TCombobox', fieldbackground='#1E2A40',
                    background='#1E2A40', foreground='#E2E8F0',
                    selectbackground='#38BDF8', selectforeground='white')
    app = MeterSimApp(root)
    root.mainloop()
