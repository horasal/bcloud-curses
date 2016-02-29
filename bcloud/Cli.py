# Copyright (C) 2016 HongJie Zhai
# Use of this source code is governed by GPLv3 license that can be found
# in http://www.gnu.org/licenses/gpl-3.0.html

import curses
import json
import os
import os.path
from os.path import expanduser 
import sys
import time
from urllib import request
from bcloud import auth
from bcloud import const
from bcloud import encoder
from bcloud import hasher
from bcloud import net
from bcloud import util
from bcloud.RequestCookie import RequestCookie
from bcloud import pcs
from bcloud import Config

DELTA = 24 * 60 * 60
RETRIES = 3

begin_x = 15
begin_y = 5
height = 20
width = 60

def get_tmp_filepath(dir_name, save_name):
    filepath = os.path.join(dir_name, save_name)
    return filepath, filepath + '.part', filepath + '.bcloud-stat'

class Cli:
    profile = None
    tokens = None
    screen = None
    cookie = None
    files = None
    path = '/'
    page_num = 1
    username = ''

    item_pos = 0
    has_next = True

    block_size = 128 * 1024

    def __init__(self):
        self.filewatcher = None
        self.screen = curses.initscr()
        curses.cbreak()
        curses.start_color()
        self.screen.border(0)
        #self.screen.start_color()
    
# auto_signin mode is not supported now
    def signin(self, auto_signin = True):
        self.profile = None
        self.screen.nodelay(0)

        self.screen.addstr(2, 2, "Baidu Yun Login")
        self.screen.addstr(4, 2, "Please input your ID and Password")
        self.screen.addstr(6, 2, "Press Any Key to continue ... ")
        self.screen.refresh()

        self.screen.getch()

        curses.echo()
        win = curses.newwin(height, width, begin_y, begin_x);
        win.border(0)
        win.addstr(2, 2, "UserName:")
        win.refresh()
        username = win.getstr(3, 2, 20).decode(encoding='utf-8')
        self.username = username

        self.cookie, self.tokens = self.load_auth(username)
        if self.cookie and self.tokens:
            win.addstr(7, 2, "Cookie is valid. Press any key to continue.");
            win.refresh()
            win.getch()
            return True

        win.addstr(4, 2, "Password: (Won't be shown)")
        win.refresh()
        curses.noecho()
        password = win.getstr(5, 2, 20).decode(encoding='utf-8')
        curses.echo()

        self.cookie = RequestCookie()
        self.tokens = {}
        verifycode = ''
        codeString = ''
        password_enc = ''
        rsakey = ''

        win.addstr(7, 2, "Get BaiduID...");
        win.refresh()
        uid_cookie = auth.get_BAIDUID()
        if not uid_cookie:
            win.addstr(8, 2, " Get BaiduID failed, press any key to exit")
            win.refresh()
            win.getch()
            return False
        self.cookie.load_list(uid_cookie)
        win.addstr(8, 2, "Get token...");
        win.refresh()
        info = auth.get_token(self.cookie)
        if not info:
            win.addstr(9, 2, " Get Token failed, press any key to exit")
            win.refresh()
            win.getch()
            return False
        hosupport, token = info
        self.cookie.load_list(hosupport)
        self.cookie.load('cflag=65535%3A1; PANWEB=1;')
        self.tokens['token'] = token
        win.addstr(9, 2, "Get UBI...");
        win.refresh()
        ubi_cookie = auth.get_UBI(self.cookie, self.tokens)
        self.cookie.load_list(ubi_cookie)
        win.addstr(10, 2, "Check login...");
        win.refresh()
        ubi_cookie, status = auth.check_login(self.cookie, self.tokens, username)
        self.cookie.load_list(ubi_cookie)
        codeString = status['data']['codeString']
        vcodetype = status['data']['vcodetype']
        if codeString:
            win.addstr(11, 2, "Vcode is needed but not supported")
            win.addstr(12, 2, "Press any key to quit")
            win.refresh()
            win.getch()
            return False
        win.addstr(11, 2, "Get public key...");
        win.refresh()
        info = auth.get_public_key(self.cookie,self.tokens)
        pubkey = info['pubkey']
        rsakey = info['key']
        password_enc = util.RSA_encrypt(pubkey, password)
        win.addstr(12, 2, "login...");
        win.refresh()
        info = auth.post_login(self.cookie, self.tokens, username, password_enc, rsakey)
        errno, query = info
        if errno == 0:
            self.cookie.load_list(query)
            win.addstr(13, 2, "Get bds_token ...")
            win.refresh()
            bdstoken = auth.get_bdstoken(self.cookie)
            self.tokens['bdstoken'] = bdstoken
            win.addstr(14, 2, "Login finished, press any key to continue")
            win.refresh()
            win.getch()
        elif errno == 257:
            win.addstr(13, 2, "need vcode, but not supported")
            win.addstr(14, 2, "Press any key to quit")
            win.refresh()
            win.getch()
            return False
        elif errno == 4:
            win.addstr(13, 2, "invalid password!")
            win.addstr(14, 2, "Press any key to quit")
            win.refresh()
            win.getch()
            return False
        else:
            win.addstr(13, 2, "Something is wrong")
            win.addstr(14, 2, "Press any key to quit")
            win.refresh()
            win.getch()
            return False

        self.dump_auth(self.username, self.cookie, self.tokens)
        return True

    #-------------------------------------------------
    #        user info area
    #-------------------------------------------------
    #                                     | file info
    #                                     | area
    #         file list                   |
    #                                     |
    #                                     |
    #                                     |
    #------------------------------------------------
    #            operation area
    #------------------------------------------------
    def cloud_driver(self):
        self.item_pos = 0
        self.path = '/'
        self.page_num = 1
        self.has_next = True

        curses.curs_set(0)
        self.screen.keypad(True)
        curses.noecho()
        info, filel, filei, o = self.initialize_window()
        self.draw_info(info)
        self.draw_nav(o)
        self.draw_file(filel, filei )
        self.screen.nodelay(1)
        while True:
            c = self.screen.getch()
            if c == curses.KEY_UP:
                self.up()
                self.draw_current_list(filel, filei)
            elif c == curses.KEY_DOWN:
                self.down()
                self.draw_current_list(filel, filei)
            elif c == curses.KEY_RIGHT:
                self.enter_dir()
                self.draw_file(filel, filei)
                self.draw_info(info)
            elif c == curses.KEY_LEFT:
                self.back_dir()
                self.draw_file(filel, filei)
                self.draw_info(info)
            elif c == 27 or c == ord('Q') or c == ord('q'):
                break
            elif c == 10:
                self.download(o)
            elif c == ord('>'):
                self.next_page()
                self.draw_file(filel, filei)
                self.draw_info(info)
            elif c == ord('<'):
                self.prev_page()
                self.draw_file(filel, filei)
                self.draw_info(info)
            elif c == ord('R') or c == ord('r'):
                info.clear()
                o.clear()
                filel.clear()
                filei.clear()
                info.border(0)
                o.border(0)
                filel.border(0)
                filei.border(0)
                self.draw_info(info)
                self.draw_nav(o)
                self.draw_file(filel, filei)
            else:
                pass

    def next_page(self):
        self.files = None
        self.item_pos = 0
        if not self.has_next:
            self.page_num += 1

    def prev_page(self):
        self.files = None
        self.item_pos = 0
        if self.page_num > 1:
            self.page_num -= 1

    def up(self):
        if self.files and self.item_pos > 0:
            self.item_pos -= 1
    
    def down(self):
        if self.files and self.item_pos < len(self.files) - 1:
            self.item_pos += 1

    def download(self, o):
        if self.files and self.files[self.item_pos] and not self.files[self.item_pos]['isdir']:
            self.screen.nodelay(1)
            end_size = self.files[self.item_pos]['size']
            path = self.files[self.item_pos]['path']
            o.clear()
            o.border(0)
            curses.echo()
            o.addstr(1, 2, "DownloadPath: (Default: ~/)")
            o.refresh()
            download_path = o.getstr(2, 2, 40).decode(encoding='utf-8')
            if download_path == "":
                download_path = expanduser("~")
            else:
                download_path = expanduser(download_path)
            filepath, tmp_filepath , conf_filepath = get_tmp_filepath(download_path, path[1:])
            if not os.path.exists(os.path.dirname(tmp_filepath)):
                os.makedirs(os.path.dirname(tmp_filepath), exist_ok=True)
            o.clear()
            o.border(0)
            o.refresh()
            if os.path.exists(filepath):
                o.clear()
                o.border(0)
                o.addstr(1, 2, "File Already Exists.")
                o.addstr(2, 2, "Press Any Key to Continue")
                o.refresh()
                self.screen.getch()
                return
            o.addstr(1, 2, "Getting Download links...")
            o.refresh()
            url = pcs.get_download_link(self.cookie, self.tokens, path)
            if not url:
                o.clear()
                o.border(0)
                o.addstr(1, 2, "Failed to get url")
                o.addstr(2, 2, "Press ESC to abort")
                o.refresh()
                self.screen.getch()
                return

            o.addstr(2, 2, "Prepare file...")
            o.refresh()
            if os.path.exists(conf_filepath) and os.path.exists(tmp_filepath):
                with open(conf_filepath) as conf_fh:
                    status = json.load(conf_fh)
                file_exists = True
                fh = open(tmp_filepath, 'rb+')
            else:
                req = net.urlopen_simple(url)
                if not req:
                    o.clear()
                    o.border(0)
                    o.addstr(1, 2, "Failed to request")
                    o.addstr(2, 2, "Press ESC to abort")
                    o.refresh()
                    self.screen.getch()
                    return
                content_length = req.getheader('Content-Length')
                if not content_length:
                    match = re.search('\sContent-Length:\s*(\d+)', str(req.headers))
                    if not match:
                        o.clear()
                        o.border(0)
                        o.addstr(1, 2, "Failed to match content-length")
                        o.addstr(2, 2, "Press ESC to abort")
                        o.refresh()
                        self.screen.getch()
                        return
                    content_length = match.group(1)
                size = int(content_length)
                if size == 0:
                    open(filepath, 'a').close()
                    o.clear()
                    o.border(0)
                    o.addstr(1, 2, "File already downloaded")
                    o.addstr(2, 2, "Press ESC to abort")
                    o.refresh()
                    self.screen.getch()
                    return
                file_exists = False
                fh = open(tmp_filepath, 'wb')
                try:
                    fh.truncate(size)
                except (OSError, IOError):
                    o.clear()
                    o.border(0)
                    o.addstr(1, 2, "Disk error (disk is full?)")
                    o.addstr(2, 2, "Press ESC to abort")
                    o.refresh()
                    self.screen.getch()
                    return

            start_size = 0
            if file_exists:
                start_size, end_size, received = status
            offset = start_size
            count = 0
            while offset < end_size:
                status = [offset, end_size, 0]
                count += 1
                o.clear()
                o.border(0)
                c = self.screen.getch()
                if c == 27:
                    with open(conf_filepath, 'w') as fh:
                        json.dump(status, fh)
                    break

                o.addstr(1, 2, "Downloading: {0} ... ".format(path))
                o.refresh()
                req = self.get_req(url, offset, end_size)
                if not req:
                    o.addstr(2, 2, "Network error{0}, retry after 3s.".format(count))
                    o.addstr(3, 2, "Press ESC to abort.")
                    o.refresh()
                    time.sleep(3)
                    continue
                else:
                    try:
                        block = req.read(self.block_size)
                    except: 
                        o.addstr(2, 2, "Can not Read block, retry.".format(offset, end_size))
                        time.sleep(1)
                        continue
                    o.addstr(2, 2, "Process: {0} / {1}".format(offset, end_size))
                    fh.seek(offset)
                    fh.write(block)
                    offset += len(block)
                o.addstr(3, 2, "Press ESC to abort")
                o.refresh()
            
    def enter_dir(self):
        if self.files[self.item_pos] and self.files[self.item_pos]['isdir']:
            self.path = self.files[self.item_pos]['path']
        self.files = None
        self.item_pos = 0

    def back_dir(self):
        self.files = None
        self.item_pos = 0
        if not self.path == '/' or not self.path == '':
            self.path = os.path.dirname(self.path)

    def draw_current_list(self, filel, filei):
        filel.clear()
        filei.clear()
        filel.border(0)
        filei.border(0)
        if self.files:
            index = 0
            start_pos = self.item_pos
            if len(self.files) - self.item_pos < 26:
                start_pos = max(len(self.files) - 26, 0)
            for pindex in range(start_pos, len(self.files)):
                pfile = self.files[pindex]
                path = os.path.basename(pfile['path'])[:50]
                size = pfile.get('size', 0)
                if pfile['isdir']:
                    human_size = '--'
                    path = path + "/"
                else:
                    human_size = util.get_human_size(pfile['size'])[0]
                if pindex == self.item_pos:
                    filel.addstr(index + 1, 2, "> " + path, curses.COLOR_RED)
                else:
                    filel.addstr(index + 1, 2, path)
                filei.addstr(index + 1, 2, human_size)
                index += 1
                if index > 26:
                    filel.addstr(index + 1, 2, "More {0} files...".format(len(self.files) - index + 1))
                    break
            filel.refresh()
            filei.refresh()

    def draw_file(self, filel, filei):
        filel.clear()
        filei.clear()
        filel.border(0)
        filei.border(0)

        if not self.cookie or not self.tokens:
            filel.refresh()
            filei.refresh()
            return

        content = pcs.list_dir(self.cookie, self.tokens, self.path, self.page_num)
        if not content:
            filel.addstr(2,2, "Network Error - content empty")
        elif content.get('errno', -1) != 0:
            filel.addstr(2,2, "Network Error - error")
        elif content['list']:
            # process here
            self.files = content['list']
            self.draw_current_list(filel, filei)
        else:
            filel.addstr(2,2, "Already last page")
            self.has_next = False

        filel.refresh()
        filei.refresh()

    def initialize_window(self):
        self.screen.refresh()
        info_x, info_y, info_w, info_h = 0, 0, 90, 4
        info = curses.newwin(info_h, info_w, info_y, info_x)
        info.border(0)
        file_y, file_x, file_w, file_h = 4, 0, 60, 30
        filel = curses.newwin(file_h, file_w, file_y, file_x)
        filel.border(0)
        filei_y, filei_x, filei_w, filei_h = 4, 61, 30, 30
        filei = curses.newwin(filei_h, filei_w, filei_y, filei_x)
        filei.border(0)
        o_y, o_x, o_w, o_h = 34, 0, 90, 5
        o = curses.newwin(o_h, o_w, o_y, o_x)
        o.border(0)
        return info, filel, filei, o

    def draw_info(self, info):
        if not self.cookie or not self.tokens:
            info.addstr(1,2, 'User: {0}, Cookie and Tokens are not available'.format(self.username)) 
            info.addstr(2,2, 'Try restarting application to fix'.format(self.path, self.page_num))
            info.refresh()
            return
        quota_info = pcs.get_quota(self.cookie, self.tokens)
        used = quota_info['used']
        total = quota_info['total']
        used_size = util.get_human_size(used)[0]
        total_size = util.get_human_size(total)[0]
        info.addstr(1,2, 'User: {2},  Quota: {0} / {1}'.format(used_size, total_size, self.username))
        info.addstr(2,2, 'Path: {0}, Page: {1}'.format(self.path, self.page_num))
        info.refresh()

    def draw_nav(self, o):
        o.addstr(1, 2, "Up/Down - Select File, Left/Right - Change Directory, Enter - Download")
        o.addstr(2, 2, "Esc/Q - Quit, R - Refresh, F - Find, Delete - Not supported.")
        o.addstr(3, 2, "< - previous page, > - next page")
        o.refresh()

    def run(self, argv):
        if not self.profile:
            if not self.signin(True):
                return
            self.cloud_driver()
            curses.endwin()


    def load_auth(self, username):
        auth_file = os.path.join(Config.get_tmp_path(username), 'auth.json')
        if os.path.exists(auth_file):
            if time.time() - os.stat(auth_file).st_mtime < DELTA:
                with open(auth_file) as fh:
                    c, tokens = json.load(fh)
                cookie = RequestCookie(c)
                return cookie, tokens
        return None, None

    def dump_auth(self, username, cookie, tokens):
        auth_file = os.path.join(Config.get_tmp_path(username), 'auth.json')
        with open(auth_file, 'w') as fh:
            json.dump([str(cookie), tokens], fh)

    def get_req(self, url, start_size, end_size):
        opener = request.build_opener()
        content_range = 'bytes={0}-{1}'.format(start_size, end_size)
        opener.addheaders = [
            ('Range', content_range),
            ('User-Agent', const.USER_AGENT),
            ('Referer', const.PAN_REFERER),
        ]
        for i in range(RETRIES):
            try:
                return opener.open(url, timeout = 8)
            except:
                return None
        else:
            return None
