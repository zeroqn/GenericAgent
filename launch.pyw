import threading, subprocess, sys, time, os, ctypes, atexit, socket, random

WINDOW_WIDTH, WINDOW_HEIGHT, RIGHT_PADDING, TOP_PADDING = 1200, 900, 0, 50

script_dir = os.path.dirname(os.path.abspath(__file__))
frontends_dir = os.path.join(script_dir, "frontends")

def find_free_port(lo=18501, hi=18599):
    ports = list(range(lo, hi+1)); random.shuffle(ports)
    for p in ports:
        try: s = socket.socket(); s.bind(('127.0.0.1', p)); s.close(); return p
        except OSError: continue
    raise RuntimeError(f'No free port in {lo}-{hi}')

def get_screen_width():
    try: return ctypes.windll.user32.GetSystemMetrics(0)
    except: return 1920

def _terminate_process(proc):
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass

def start_streamlit(port):
    cmd = [sys.executable, "-m", "streamlit", "run", os.path.join(frontends_dir, "stapp.py"), "--server.port", str(port), "--server.address", "localhost", "--server.headless", "true", "--client.toolbarMode", "viewer"]
    proc = subprocess.Popen(cmd)
    atexit.register(_terminate_process, proc)
    return proc

PASTE_HOOK_JS = """if (!window._pasteHooked) { window._pasteHooked = true;
    document.addEventListener('paste', e => {
        const items = e.clipboardData?.items; if (!items) return;
        let t = null, hasText = false;
        for (const item of items) {
            if (item.kind === 'string' && (item.type === 'text/plain' || item.type === 'text/html')) hasText = true;
            if (item.kind === 'file') { t = item.type.startsWith('image/') ? 'image in clipboard, ' : 'file in clipboard, '; }
        }
        if (!t || hasText) return;
        e.preventDefault(); e.stopImmediatePropagation();
        const el = document.querySelector('textarea[data-testid="stChatInputTextArea"]') || document.activeElement;
        if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
            const s = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            s.call(el, el.value + t); el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, true);
}"""

def paste_hook_injector():
    """注入剪贴板粘贴图片钩子，等窗口加载后执行一次，之后每60秒补注入防页面刷新丢失"""
    time.sleep(5)  # 等 webview 加载
    while True:
        try: window.evaluate_js(PASTE_HOOK_JS)
        except: pass
        time.sleep(60)

def env_truthy(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def wait_headless(processes):
    print('[Launch] Headless mode enabled; GUI window disabled.')
    print('[Launch] Press Ctrl+C to stop.')
    try:
        while True:
            live = [p for p in processes if p.poll() is None]
            if not live:
                print('[Launch] All child processes exited.')
                return
            time.sleep(1)
    except KeyboardInterrupt:
        print('[Launch] Stopping...')
        for p in processes:
            _terminate_process(p)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('port', nargs='?', default='0'); 
    parser.add_argument('--headless', action='store_true', help='只启动后端服务，不打开 pywebview GUI 窗口（也可设置 GA_HEADLESS=1）')
    parser.add_argument('--tg', action='store_true', help='启动 Telegram Bot'); 
    parser.add_argument('--qq', action='store_true', help='启动 QQ Bot');
    parser.add_argument('--feishu', '--fs', dest='feishu', action='store_true', help='启动 Feishu Bot');
    parser.add_argument('--wechat', '--wx', dest='wechat', action='store_true', help='启动 WeChat Bot');
    parser.add_argument('--wecom', action='store_true', help='启动 WeCom Bot');
    parser.add_argument('--dingtalk', '--dt', dest='dingtalk', action='store_true', help='启动 DingTalk Bot');
    parser.add_argument('--sched', action='store_true', help='启动计划任务调度器')
    parser.add_argument('--llm_no', type=int, default=0, help='LLM编号')
    args = parser.parse_args()
    port = str(find_free_port()) if args.port == '0' else args.port
    headless = args.headless or env_truthy('GA_HEADLESS')
    processes = []
    print(f'[Launch] Using port {port}')
    streamlit_proc = start_streamlit(port)
    processes.append(streamlit_proc)

    if args.tg:
        tgproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "tgapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, tgproc)
        processes.append(tgproc)
        print('[Launch] Telegram Bot started')
    else: print('[Launch] Telegram Bot not enabled (use --tg to start)')

    if args.qq:
        qqproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "qqapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, qqproc)
        processes.append(qqproc)
        print('[Launch] QQ Bot started')
    else: print('[Launch] QQ Bot not enabled (use --qq to start)')

    if args.feishu:
        fsproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "fsapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, fsproc)
        processes.append(fsproc)
        print('[Launch] Feishu Bot started')
    else: print('[Launch] Feishu Bot not enabled (use --feishu to start)')

    if args.wechat:
        wxproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, 'wechatapp.py')], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, wxproc)
        processes.append(wxproc)
        print('[Launch] WeChat Bot started')
    else: print('[Launch] WeChat Bot not enabled (use --wechat to start)')

    if args.wecom:
        wcproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "wecomapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, wcproc)
        processes.append(wcproc)
        print('[Launch] WeCom Bot started')
    else: print('[Launch] WeCom Bot not enabled (use --wecom to start)')

    if args.dingtalk:
        dtproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "dingtalkapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, dtproc)
        processes.append(dtproc)
        print('[Launch] DingTalk Bot started')
    else: print('[Launch] DingTalk Bot not enabled (use --dingtalk to start)')
    
    if args.sched:
        scheduler_proc = subprocess.Popen([sys.executable, os.path.join(script_dir, "agentmain.py"), "--reflect", os.path.join(script_dir, "reflect", "scheduler.py"), "--llm_no", str(args.llm_no)], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(_terminate_process, scheduler_proc)
        processes.append(scheduler_proc)
        print('[Launch] Task Scheduler started (duplicate prevented by scheduler port lock)')
    else: print('[Launch] Task Scheduler not enabled (--sched)')

    if headless:
        print(f'[Launch] Streamlit server: http://localhost:{port}')
        wait_headless(processes)
        sys.exit(0)

    threading.Thread(target=paste_hook_injector, daemon=True).start()
    if os.name == 'nt':
        screen_width = get_screen_width()
        x_pos = screen_width - WINDOW_WIDTH - RIGHT_PADDING
    else: x_pos = 100
    time.sleep(2) 
    import webview
    window = webview.create_window(
        title='GenericAgent', url=f'http://localhost:{port}',
        width=WINDOW_WIDTH, height=WINDOW_HEIGHT, x=x_pos, y=TOP_PADDING,
        resizable=True, text_select=True)
    webview.start()
