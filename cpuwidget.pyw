"""
Simple module that show the cpu percentual usage as a system tray icon.
Then was added a quick way to change system power profile.
And then was added a feature for checking for updates.
"""

import ctypes
import datetime
import os
import re
import subprocess
import threading as th
from typing import Callable

import darkdetect
from PIL import ImageFont, Image, ImageDraw
import psutil
import pystray


# Configurables

ignore_pip_packages = (
    'zipp',
    'jedi',
    'readme-renderer',
    'pypdfium2',
)
ignore_choco_packages = (
    'nodejs.install',
    'nodejs',
    'msys2',
)

pip_ignore_breaking_changes = True
choco_ignore_breaking_changes = True

hours_to_search_for_upgrades = (  # 24h format
    22, 4, 10, 16,
)

# End of configurables


PRIORITY_LOW = 128
PRIORITY_NORMAL = 32
POWERCFG = 'powercfg'
FONT = 'Fonts\\msyh.ttc'


class Widget(object):
    """
    Holds all the intended implementation.

    """

    def __init__(self) -> None:
        """
        Set up all the needed environment before the mainloop.

        """
        self.choco_update = False
        self.pip_update = False

        self.sec = 0.5
        self.write_in_black = darkdetect.isLight()

        # Hold the fonts. Switch iops for memory
        self.fonts: dict[int, ImageFont.FreeTypeFont] = {}

        self.active_guid = ''

        self.lock = False
        self.flag22h = 0
        self.flag04h = 0

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        psutil.Process().nice(PRIORITY_LOW)

        th.Thread(
            target=darkdetect.listener,
            args=(self._darkdetect_callback,),
            daemon=True,
        ).start()

        self.version_re = re.compile(r'\d*\.\d*\.\d*')

        self.guid_re = re.compile(
            '([0-9a-f]{8}-'
            '[0-9a-f]{4}-'
            '[0-9a-f]{4}-'
            '[0-9a-f]{4}-'
            '[0-9a-f]{12})'
            r'\s*\((.*)\)( \*)?'
        )

        menu = [
            pystray.MenuItem(
                '1 sec',
                self._set_sec(1),
                checked=lambda _: self.sec == 1,
                radio=True
            ),
            pystray.MenuItem(
                '0.5 sec',
                self._set_sec(0.5),
                checked=lambda _: self.sec == 0.5,
                radio=True
            ),

            pystray.Menu.SEPARATOR,
        ]

        for guid_hash, guid_name, asterisk in self._get_profiles():
            active = bool(asterisk)
            if active:
                assert not self.active_guid and "only one should exist"
                self.active_guid = guid_hash

            menu += [
                pystray.MenuItem(
                    guid_name,
                    self._set_state(guid_hash),
                    checked=self._get_state(guid_hash),
                    radio=True,
                ),
            ]

        menu.extend([
            pystray.Menu.SEPARATOR,

            pystray.MenuItem(
                'Search for updates',
                lambda _: (self._check_for_updates()),
            ),

            pystray.Menu.SEPARATOR,

            pystray.MenuItem('Exit', self._exit_prog),
        ])

        self.icon = pystray.Icon(
            'CpuIcon', self._get_image(), 'Percentual CPU usage', menu,
        )
        self.icon.run_detached()
        self._check_for_updates()

    def mainloop(self) -> None:
        """
        Run program as long as the user won't quit on the 'gui'
        or CTRL-C

        """
        while True:
            if self.choco_update:
                self.icon.notify(
                    'Chocolatey signs upgrades available', 'Chocolatey',
                )
                self.choco_update = False

            if self.pip_update:
                self.icon.notify(
                    'Pip signs upgrades available', 'Pip',
                )
                self.pip_update = False

            now = datetime.datetime.now()
            if now.hour in hours_to_search_for_upgrades:
                if not self.lock:
                    self._check_for_updates()
                    self.lock = True
            else:
                self.lock = False

            # Here lies the sleep, interval set to `self.sec`
            self.icon.icon = self._get_image()

    def _get_profiles(self) -> list[tuple[str, ...]]:
        out, _ = subprocess.Popen(
            [POWERCFG, '/l'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).communicate()

        res = []
        for line in out.decode().splitlines():
            match = self.guid_re.search(line)
            if not match:
                continue
            res.append(match.groups(''))

        return res

    def _darkdetect_callback(self, color: str) -> None:
        # `color` is passed by darkdetect, can be 'Light' or 'Dark'.
        self.write_in_black = not self.write_in_black

    def _set_sec(self, sec: float | int) -> Callable[[pystray.MenuItem], None]:
        def inner(menu_item: pystray.MenuItem) -> None:
            self.sec = sec
        return inner

    def _get_state(self, guid_hash: str) -> Callable[[pystray.MenuItem], bool]:
        def inner(menu_item: pystray.MenuItem) -> bool:
            return self.active_guid == guid_hash
        return inner

    def _set_state(self, guid_hash: str) -> Callable[[pystray.MenuItem], None]:
        def inner(menu_item: pystray.MenuItem) -> None:
            subprocess.Popen(
                [POWERCFG, '/s', guid_hash],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.active_guid = guid_hash
        return inner

    def _get_image(self) -> Image.Image:
        cpu_percent = round(psutil.cpu_percent(interval=self.sec))
        if cpu_percent == 100:
            font_size = 21
            position = (14., 14.)
        else:
            font_size = 24
            position = (18., 14.)

        # Ugly but more readable
        if self.write_in_black and cpu_percent < 50:
            colors_tuple = (0, 0, 0, 255)
        elif self.write_in_black and cpu_percent < 75:
            colors_tuple = (230, 122, 5, 255)
        elif cpu_percent < 50:
            colors_tuple = (255, 255, 255, 255)
        elif cpu_percent < 75:
            colors_tuple = (255, 255, 0, 255)
        else:
            colors_tuple = (255, 0, 0, 255)

        fnt = self.fonts.get(font_size)
        if fnt is None:
            envvar = os.getenv('SYSTEMROOT')  # maybe `windir`?
            assert envvar

            # Is this font available in every Windows version?
            font_path = os.path.join(envvar, FONT)

            fnt = ImageFont.truetype(font_path, font_size)
            self.fonts[font_size] = fnt

        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text(
            xy=position,
            text=str(cpu_percent),
            font=fnt,
            anchor='mm',
            fill=colors_tuple,
        )

        return img

    def _check_for_updates(self) -> None:
        th.Thread(target=self._check_choco_updates).start()
        th.Thread(target=self._check_pip_updates).start()

    def _check_pip_updates(self) -> None:
        # There will be `pip` if this program is running, right?
        out, _ = subprocess.Popen(
            ['pip', 'list', '-o'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).communicate()

        output_list = out.decode().splitlines()
        packages = output_list[2:]

        for line in packages:
            name, version, latest, _ = line.split()
            if name in ignore_pip_packages:
                continue

            if pip_ignore_breaking_changes:
                if not (
                    self.version_re.match(version)
                    or self.version_re.match(latest)
                ):
                    continue

                major_version = int(version[:version.find('.')])
                major_latest = int(latest[:latest.find('.')])

                if major_latest > major_version:
                    continue

            self.pip_update = True
            break

    def _check_choco_updates(self) -> None:
        try:
            out, _ = subprocess.Popen(
                ['choco', 'outdated', '-r'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).communicate()
        except FileNotFoundError:
            return

        output_list = out.decode().splitlines()

        for line in output_list:
            name, version, latest, _ = line.split('|')
            if name in ignore_choco_packages:
                continue

            if choco_ignore_breaking_changes:
                if not (
                    self.version_re.match(version)
                    or self.version_re.match(latest)
                ):
                    continue

                major_version = int(version[:version.find('.')])
                major_latest = int(latest[:latest.find('.')])

                if major_latest > major_version:
                    continue

            self.choco_update = True
            break

    def _exit_prog(self, icon: pystray.Icon) -> None:
        icon._hide()
        os._exit(0)


if __name__ == '__main__':
    widget = Widget()
    try:
        widget.mainloop()
    except KeyboardInterrupt:
        widget._exit_prog(widget.icon)
