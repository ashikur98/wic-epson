from . import Device, PREFACE

import asciimatics as am, asciimatics.widgets as aw, asciimatics.scene, asciimatics.event
#, screen, exceptions
import asyncio, re, string, sys, logging, time
_log = logging.getLogger('reinkpy.ui')


def run_sep(func, wait=False):
    "Run in separate thread"
    from concurrent.futures import ThreadPoolExecutor
    e = ThreadPoolExecutor()
    f = e.submit(func)
    e.shutdown(wait=wait)
    return f.result() if wait else f


class App:

    def __init__(self):
        self.device = aw.DropdownList(
            [('Scanning for devices…', None)], on_change=self.device_changed)
        self.brand = aw.DropdownList(
            [('Select brand…', None), ('EPSON', 'EPSON')],
            disabled=True, on_change=self.brand_changed)
        self.model = aw.DropdownList([], disabled=True, on_change=self.model_changed)
        self.ops = aw.ListBox(aw.Widget.FILL_FRAME, [], on_select=self.op_selected)
        self.msg = LoggingWidget('reinkpy')
        self.msg.write('''HELP:
- "Tab" or arrow keys to move around
- "Space" or "Return" to select items
- "Esc" to quit
''')
        def prelude_cb(sel):
            if sel: raise am.exceptions.NextScene("main")
            else: quit_()
        self.gen_scenes = scener(
            prelude=[
                lambda screen: aw.PopUpDialog(screen, PREFACE, [
                    "NO, Escape!",
                    "YES, I shall do this",
                ], prelude_cb)
            ],
            main=[
                aw.Background,
                framer([
                    layouter([
                        (aw.Label("ReInkPy"), 1),
                        (aw.Button("Esc", quit_, add_box=False), 3),
                    ], [14,4,12,1,1]),
                    layouter([
                        aw.Divider(), self.device,
                        aw.Divider(), self.brand,
                        aw.Divider(), self.model,
                        aw.Divider(), self.ops,
                        aw.Divider(False, 2), self.msg,
                        # (aw.VerticalDivider(), 1)
                    ], [1,30,1], fill_frame=True)
                ],
                       height=lambda h: int(((10-h//40)/10) * h),
                       width=lambda w: int(((8-w//80)/8) * w),
                       has_border=not True, can_scroll=True, reduce_cpu=True) ]
        )
        run_sep(self.find_devices)

    def find_devices(self):
        res = Device.find()
        self.device.options = [('Select device…', None), *((str(d), d) for d in res)]
        if res:
            # self.device.value = self.device.options[1][1]
            self.device.focus()
        self._needs_refresh = True

    def device_changed(self):
        self.brand.disabled = self.device.value is None
        self.brand.value = self.device.value and self.device.value.brand

    def brand_changed(self):
        if self.brand.value is None:
            self.model.options = []
            self.model.value = None
            self.model.disabled = True
        else:
            self.model.options = [('Select model…', None), ('Autodetect…', True),
                                   *((str(m), m) for m in sorted(self.driver.list_models()))]
            self.model.value = True
            self.model.disabled = False

    driver = property(lambda s: s.device.value and s.device.value.epson) # tmpfix
    def model_changed(self):
        # no loop here: configure should be idempotent
        self.model.value = self.driver and self.driver.configure(self.model.value).spec.model
        if self.model.value:
            self.ops.options = [
                (getattr(self.driver, f).__doc__, f) for f in dir(self.driver)
                if re.match('do_', f, re.I)] # do_(reset_All|(?!reset))
            self.ops.disabled = False
        else:
            self.ops.options = [('(No operations available)', None)]
            self.ops.disabled = True

    def op_selected(self):
        if self.ops.value:
            f = getattr(self.driver, self.ops.value)
            def cb(sel):
                if sel == 1: self.run_op(f)
            # TODO: better way to discriminate write operations
            if re.search(r'reset|write', f.__doc__, re.I):
                self.ask(f"{f.__doc__}?", ["No", "Yes"], cb)
            else:
                self.run_op(f)

    def run_op(self, f):
        try:
            _log.info('Running %s', f.__name__)
            r = run_sep(f, wait=True)
            _log.info('%s ended: %r', f.__name__, r)
        except:
            _log.exception('Exception in %s', f.__name__)

    def ask(self, text, options, cb):
        p = aw.PopUpDialog(self._screen, text, options, has_shadow=True, on_close=cb)
        self._screen.current_scene.add_effect(p)

    # def show_info(self): pass

    # TODO: backup / rollback
    # def do_backup(self, fname='eeprom.txt'):
    #     "Save the current printer state (EEPROM) in a file"

    async def arun(self):
        scene = None
        while True:
            screen = am.screen.Screen.open()
            self._needs_refresh = False
            leave = True
            try:
                self._screen = screen
                screen.set_scenes(self.gen_scenes(screen), start_scene=scene,
                                  unhandled_input=handle_input)
                while True:
                    if self._needs_refresh or screen.has_resized():
                        scene = screen._scenes[screen._scene_index]
                        scene.exit()
                        leave = False
                        break
                    else:
                        screen.draw_next_frame()
                        await asyncio.sleep(0.1)
            except am.exceptions.StopApplication:
                break
            finally:
                screen.close(leave)


def handle_input(event):
    if isinstance(event, am.event.KeyboardEvent):
        if event.key_code in (am.screen.Screen.KEY_ESCAPE,):
            quit_()

def quit_():
    raise am.exceptions.StopApplication("Quit")

def layouter(widgets, cols=[1], **kw):
    i = cols.index(max(cols))
    def make_layout(f):
        l = aw.Layout(cols, **kw)
        f.add_layout(l)
        for w in widgets:
            if not isinstance(w, tuple): w = (w, i)
            l.add_widget(*w)
        return l
    return make_layout

def framer(layouters, height=int, width=int, theme='green', **fkw):
    def make_frame(screen):
        f = aw.Frame(screen, height(screen.height), width(screen.width), **fkw)
        f.set_theme(theme)
        for l in layouters: l(f)
        f.fix()
        return f
    return make_frame

def scener(**nf):
    def make_scenes(screen):
        return [am.scene.Scene([f(screen) for f in framers], name=name)
                for (name, framers) in nf.items()]
    return make_scenes


class LoggingWidget(aw.TextBox):

    def __init__(self, name):
        super().__init__(22, as_string=True, line_wrap=False, readonly=True, disabled=False)
        logging.getLogger(name).addHandler(logging.StreamHandler(self))

    def write(self, msg: str):
        self.value = self.value + str(msg) + '\n'

    # TODO: modal dialog on level >= WARNING
    # def popup(self):


async def amain():
    import logging.handlers, os, time
    logging.basicConfig()
    logger = logging.getLogger()
    # logger.setLevel(logging.DEBUG)
    if not os.path.exists('logs'): os.mkdir('logs')
    logfile = os.path.join('logs', time.strftime('reinkpy-%Y%m%d.log'))
    rfh = logging.handlers.RotatingFileHandler(logfile, backupCount=5, delay=True)
    rfh.setFormatter(logger.handlers[0].formatter)
    rfh.doRollover()
    logger.addHandler(rfh)
    off = []
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and h.stream.name in ('<stdout>', '<stderr>'):
            off.append(h)
            logger.removeHandler(h)
    try:
        await App().arun()
    except:
        for h in off: logger.addHandler(h)
        logging.exception('')
    finally:
        rfh.close()
        if os.path.exists(logfile):
            print('Log file: ' + logfile)

def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
