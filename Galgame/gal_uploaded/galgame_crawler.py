# -*- coding: utf-8 -*-
"""
Galgame 一键采集器
- 按游戏名搜索：VNDB / Bangumi / Shionlib / SearchGal / 鲲Gal / TouchGal
- 批量网址采集、图片/视频链接、导出
说明：仅整理公开可访问的搜索结果与页面信息，请遵守版权与各站条款。
"""

import csv
import json
import os
import queue
import random
import re
import shutil
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "千惠"
# 界面自定义配置（与脚本同目录）
APP_DIR = Path(__file__).resolve().parent
UI_CONFIG_PATH = APP_DIR / "ui_config.json"
UI_ASSETS_DIR = APP_DIR / "ui_assets"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
VIDEO_EXTS = (".mp4", ".webm", ".m3u8", ".mov", ".mkv")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")

SESSION_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7,ja;q=0.6",
}


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def is_http_url(url):
    return urlparse(url).scheme in ("http", "https")


def _item(source, title, url, released="", score="-", alt="", cover="", id_=""):
    return {
        "source": source,
        "title": title or url,
        "alt": alt or "",
        "released": released or "",
        "score": score if score is not None else "-",
        "url": url,
        "cover": cover or "",
        "id": str(id_ or ""),
    }


# ========================= 各站搜索 =========================

def search_vndb(keyword, limit=20):
    api = "https://api.vndb.org/kana/vn"
    payload = {
        "filters": ["search", "=", keyword],
        "fields": "title,alttitle,image.url,released,rating,votecount",
        "results": min(limit, 50),
        "sort": "searchrank",
    }
    r = requests.post(
        api,
        json=payload,
        headers={**SESSION_HEADERS, "Content-Type": "application/json",
                 "User-Agent": "GalgameCollector/1.1 (personal research)"},
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for vn in (r.json().get("results") or []):
        vid = vn.get("id") or ""
        title = vn.get("title") or vn.get("alttitle") or vid
        rating = vn.get("rating")
        score = f"{rating/10:.2f}" if rating else "-"
        out.append(_item(
            "VNDB", title, f"https://vndb.org/{vid}",
            released=vn.get("released") or "",
            score=score,
            alt=vn.get("alttitle") or "",
            cover=(vn.get("image") or {}).get("url") or "",
            id_=vid,
        ))
    return out


def search_bangumi(keyword, limit=20):
    api = "https://api.bgm.tv/v0/search/subjects"
    payload = {"keyword": keyword, "filter": {"type": [4]}}
    r = requests.post(
        api, json=payload,
        headers={**SESSION_HEADERS, "Content-Type": "application/json",
                 "User-Agent": "GalgameCollector/1.1 (personal research)"},
        timeout=20, params={"limit": min(limit, 25)},
    )
    if r.status_code >= 400:
        r = requests.get(
            f"https://api.bgm.tv/search/subject/{quote(keyword)}",
            params={"type": 4, "responseGroup": "small", "max_results": limit},
            headers={**SESSION_HEADERS, "User-Agent": "GalgameCollector/1.1 (personal research)"},
            timeout=20,
        )
        r.raise_for_status()
        items = (r.json().get("list") or [])
    else:
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or data.get("list") or []

    out = []
    for s in items[:limit]:
        sid = s.get("id")
        name, name_cn = s.get("name") or "", s.get("name_cn") or ""
        title = name_cn or name or str(sid)
        if name_cn and name and name_cn != name:
            title = f"{name_cn} / {name}"
        rating = s.get("rating") or {}
        score = rating.get("score") if isinstance(rating, dict) else s.get("score")
        imgs = s.get("images") or {}
        out.append(_item(
            "Bangumi", title, f"https://bgm.tv/subject/{sid}",
            released=s.get("date") or "",
            score=str(score) if score else "-",
            alt=name if name_cn else "",
            cover=imgs.get("large") or imgs.get("common") or "",
            id_=sid,
        ))
    return out


def search_shionlib(keyword, limit=20):
    """Shionlib 公开搜索 API"""
    r = requests.get(
        "https://shionlib.com/api/search",
        params={"q": keyword},
        headers=SESSION_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    items = ((data.get("data") or {}).get("games") or {}).get("items") or []
    out = []
    for g in items[:limit]:
        gid = g.get("id")
        title = g.get("title_zh") or g.get("title_jp") or g.get("title_en") or str(gid)
        alt_parts = [g.get("title_jp") or "", g.get("title_en") or ""]
        alt = " / ".join(p for p in alt_parts if p and p != title)
        released = (g.get("release_date") or "")[:10]
        covers = g.get("covers") or []
        cover = ""
        if covers:
            cover = (covers[0] or {}).get("url") or ""
        out.append(_item(
            "Shionlib", title, f"https://shionlib.com/zh/game/{gid}",
            released=released, alt=alt, cover=cover, id_=gid,
        ))
    return out


def search_searchgal(keyword, limit=40):
    """
    SearchGal 聚合搜索（公开接口，流式 JSON）
    返回各资源站上的条目名与链接。
    """
    endpoints = [
        "https://vercel.api.searchgal.top/gal",
        "https://netlify.api.searchgal.top/gal",
        "https://cf.api.searchgal.top/gal",
    ]
    headers = {
        **SESSION_HEADERS,
        "Origin": "https://www.searchgal.top",
        "Referer": "https://www.searchgal.top/",
    }
    last_err = None
    text_body = None
    for ep in endpoints:
        try:
            r = requests.post(
                ep, data={"game": keyword}, headers=headers, timeout=55, stream=True,
            )
            if r.status_code != 200:
                last_err = f"{ep} -> {r.status_code}"
                continue
            # 读流
            lines = []
            for line in r.iter_lines(decode_unicode=True):
                if line:
                    lines.append(line)
            text_body = "\n".join(lines)
            break
        except Exception as e:
            last_err = str(e)
            continue
    if text_body is None:
        # 回退：只返回搜索页
        q = quote(keyword)
        return [_item(
            "SearchGal", f"[打开搜索页] {keyword}",
            f"https://www.searchgal.top/?s={q}",
            alt=f"接口失败: {last_err or 'unknown'}",
        )]

    out, seen = [], set()
    for line in text_body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        res = obj.get("result")
        if not res:
            continue
        platform = res.get("name") or "SearchGal"
        for it in res.get("items") or []:
            name = clean_text(it.get("name") or "")
            url = (it.get("url") or "").strip()
            if not url or not is_http_url(url):
                continue
            key = url.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            title = name or url
            # 来源标注为 SearchGal/平台名
            out.append(_item(
                f"SG/{platform}"[:20], title, url,
                alt=platform,
            ))
            if len(out) >= limit:
                return out
    if not out:
        q = quote(keyword)
        out.append(_item(
            "SearchGal", f"[打开搜索页] {keyword}",
            f"https://www.searchgal.top/?s={q}",
        ))
    return out


def search_kungal(keyword, limit=20):
    """
    鲲 Galgame：优先走 SearchGal 里的鲲结果；并附带站内搜索页。
    API 需登录，故用公开搜索页 + 从 SearchGal 过滤 kungal 链接。
    """
    out = []
    q = quote(keyword)
    search_page = f"https://www.kungal.com/search?q={q}"
    out.append(_item("鲲Gal搜索页", f"[打开鲲搜索] {keyword}", search_page))

    # 复用 SearchGal 结果里域名是 kungal.com 的
    try:
        for item in search_searchgal(keyword, limit=80):
            if "kungal.com" in (item.get("url") or ""):
                item = dict(item)
                item["source"] = "鲲Galgame"
                out.append(item)
                if len(out) >= limit + 1:
                    break
    except Exception:
        pass
    return out[: limit + 1]


def search_touchgal(keyword, limit=5):
    """TouchGal 有 Cloudflare，只能生成搜索页链接供浏览器打开"""
    q = quote(keyword)
    # 常见搜索参数
    urls = [
        f"https://www.touchgal.ink/search?q={q}",
        f"https://www.touchgal.ink/search?keyword={q}",
    ]
    return [
        _item("TouchGal", f"[打开 TouchGal 搜索] {keyword}", urls[0]),
    ]


# ========================= 网页采集 =========================

class Crawler:
    def __init__(self, log, progress, should_stop):
        self.log = log
        self.progress = progress
        self.should_stop = should_stop
        self.results = []
        self.seen = set()
        self.session = requests.Session()

    def fetch(self, url, ua, timeout):
        headers = {
            "User-Agent": ua or DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7,ja;q=0.6",
        }
        response = self.session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response

    def parse_page(self, url, html, content_type=""):
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
            tag.decompose()

        title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        if not title:
            h1 = soup.find("h1")
            title = clean_text(h1.get_text(" ", strip=True) if h1 else "")

        description = ""
        for selector in [
            ("meta", {"property": "og:description"}),
            ("meta", {"name": "description"}),
            ("meta", {"name": "twitter:description"}),
        ]:
            node = soup.find(*selector)
            if node and node.get("content"):
                description = clean_text(node["content"])
                if description:
                    break

        main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
        if main is None:
            main = soup.body or soup
        paragraphs = [
            clean_text(p.get_text(" ", strip=True))
            for p in main.find_all(["p", "h1", "h2", "h3", "li"])
        ]
        paragraphs = [p for p in paragraphs if len(p) > 1]
        body = "\n".join(paragraphs)
        if not body:
            body = clean_text(main.get_text(" ", strip=True))
        if len(body) > 60000:
            body = body[:60000] + "\n[正文过长，已截断]"

        links, images, videos = [], [], []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"].strip())
            if is_http_url(href):
                links.append({"text": clean_text(a.get_text(" ", strip=True)), "url": href})

        for img in soup.find_all(["img", "source"]):
            src = (
                img.get("src") or img.get("data-src")
                or img.get("data-original") or img.get("data-lazy-src")
            )
            if not src and img.get("srcset"):
                src = img.get("srcset").split(",")[0].strip().split(" ")[0]
            if src:
                full = urljoin(url, src.strip())
                if is_http_url(full):
                    images.append({"alt": clean_text(img.get("alt", "")), "url": full})

        for node in soup.find_all(["video", "source", "iframe"]):
            src = node.get("src") or node.get("data-src")
            if src:
                full = urljoin(url, src.strip())
                if is_http_url(full):
                    kind = "media" if urlparse(full).path.lower().endswith(VIDEO_EXTS) else "player/page"
                    videos.append({"url": full, "kind": kind})
        for link in links:
            if urlparse(link["url"]).path.lower().endswith(VIDEO_EXTS):
                videos.append({"url": link["url"], "kind": "media"})

        def dedupe(items, key):
            out, seen = [], set()
            for item in items:
                k = item.get(key, "")
                if k and k not in seen:
                    seen.add(k)
                    out.append(item)
            return out

        return {
            "url": url,
            "final_url": url,
            "title": title,
            "description": description,
            "body": body,
            "links": dedupe(links, "url"),
            "images": dedupe(images, "url"),
            "videos": dedupe(videos, "url"),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }

    def run(self, urls, ua, min_delay, max_delay, timeout, max_pages, follow_links, same_domain_only):
        todo = list(urls)
        root_domains = {urlparse(u).netloc.lower() for u in urls}
        done = 0
        while todo and not self.should_stop() and done < max_pages:
            url = todo.pop(0).strip()
            if not url or url in self.seen or not is_http_url(url):
                continue
            self.seen.add(url)
            try:
                self.log(f"正在采集：{url}")
                resp = self.fetch(url, ua, timeout)
                ctype = resp.headers.get("Content-Type", "")
                if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
                    self.log(f"跳过非网页：{url}")
                    continue
                item = self.parse_page(resp.url, resp.text, ctype)
                item["url"] = url
                item["final_url"] = resp.url
                self.results.append(item)
                done += 1
                self.progress(done, max_pages)
                self.log(
                    f"完成：{item['title'] or resp.url} | 图 {len(item['images'])} | 视频 {len(item['videos'])}"
                )
                if follow_links:
                    for link in item["links"]:
                        target = link["url"]
                        if target not in self.seen and target not in todo:
                            if (not same_domain_only) or (
                                urlparse(target).netloc.lower() in root_domains
                            ):
                                todo.append(target)
            except requests.HTTPError as e:
                self.log(f"HTTP {getattr(e.response, 'status_code', '?')}：{url}")
            except requests.RequestException as e:
                self.log(f"网络错误：{url}（{e}）")
            except Exception as e:
                self.log(f"解析失败：{url}（{e}）")
            if todo and not self.should_stop():
                time.sleep(random.uniform(min_delay, max_delay))
        return self.results


# ========================= 图形界面 =========================

SEARCH_SOURCES = {
    "vndb": ("VNDB", search_vndb),
    "bangumi": ("Bangumi", search_bangumi),
    "shionlib": ("Shionlib", search_shionlib),
    "searchgal": ("SearchGal", search_searchgal),
    "kungal": ("鲲Gal", search_kungal),
    "touchgal": ("TouchGal", search_touchgal),
}


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1120x860")
        self.root.minsize(920, 700)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.results = []
        self.search_results = []
        self.worker = None
        # 界面图片引用必须挂在实例上，否则会被垃圾回收导致不显示
        self._banner_photo = None
        self._icon_photo = None
        self.ui_config = self._load_ui_config()
        self._build()
        self._apply_ui_config()
        self.root.after(100, self._poll_events)

    def _load_ui_config(self):
        default = {"banner": "", "icon": "", "bg_color": ""}
        try:
            if UI_CONFIG_PATH.exists():
                data = json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8"))
                default.update({k: data.get(k, "") for k in default})
        except Exception:
            pass
        return default

    def _save_ui_config(self):
        try:
            UI_CONFIG_PATH.write_text(
                json.dumps(self.ui_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _load_tk_image(self, path, max_width=None, max_height=None):
        """加载 png/gif；若安装了 Pillow 也支持 jpg/webp。"""
        path = str(path)
        if not os.path.isfile(path):
            return None
        ext = os.path.splitext(path)[1].lower()
        img = None
        try:
            if ext in (".png", ".gif", ".pgm", ".ppm"):
                img = tk.PhotoImage(file=path)
            else:
                from PIL import Image, ImageTk
                im = Image.open(path)
                if max_width or max_height:
                    im.thumbnail(
                        (max_width or 2000, max_height or 2000),
                        Image.Resampling.LANCZOS,
                    )
                img = ImageTk.PhotoImage(im)
                return img
        except Exception:
            try:
                from PIL import Image, ImageTk
                im = Image.open(path)
                if max_width or max_height:
                    im.thumbnail(
                        (max_width or 2000, max_height or 2000),
                        Image.Resampling.LANCZOS,
                    )
                return ImageTk.PhotoImage(im)
            except Exception:
                return None
        # PhotoImage 简单缩放（仅缩小）
        if img and (max_width or max_height):
            w, h = img.width(), img.height()
            mw, mh = max_width or w, max_height or h
            factor = max(1, max(w // mw if mw else 1, h // mh if mh else 1))
            if factor > 1:
                img = img.subsample(factor, factor)
        return img

    def _apply_ui_config(self):
        # 窗口图标
        icon = self.ui_config.get("icon") or ""
        if icon and os.path.isfile(icon):
            try:
                if icon.lower().endswith(".ico"):
                    self.root.iconbitmap(icon)
                else:
                    self._icon_photo = self._load_tk_image(icon, 64, 64)
                    if self._icon_photo:
                        self.root.iconphoto(True, self._icon_photo)
            except Exception:
                pass
        # 横幅
        banner = self.ui_config.get("banner") or ""
        if banner and os.path.isfile(banner):
            self._set_banner_image(banner, save=False)
        # 背景色
        bg = (self.ui_config.get("bg_color") or "").strip()
        if bg:
            try:
                self.root.configure(bg=bg)
            except Exception:
                pass

    def _set_banner_image(self, path, save=True):
        photo = self._load_tk_image(path, max_width=1080, max_height=140)
        if not photo:
            messagebox.showerror(
                "无法加载图片",
                "请使用 PNG / GIF。\n若要用 JPG/WEBP，请先安装：pip install Pillow",
            )
            return False
        self._banner_photo = photo
        self.banner_label.configure(image=photo, text="")
        self.banner_label.image = photo
        self.banner_frame.pack(fill="x", pady=(0, 6))
        if save:
            # 复制到 ui_assets，避免原图被删后失效
            try:
                UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                dest = UI_ASSETS_DIR / f"banner{Path(path).suffix.lower()}"
                shutil.copy2(path, dest)
                self.ui_config["banner"] = str(dest)
                self._save_ui_config()
            except Exception:
                self.ui_config["banner"] = path
                self._save_ui_config()
        return True

    def import_banner(self):
        path = filedialog.askopenfilename(
            title="选择顶部横幅图片",
            filetypes=[
                ("图片", "*.png *.gif *.jpg *.jpeg *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            if self._set_banner_image(path, save=True):
                self.log(f"已设置横幅：{path}")
                self.status.set("已更新顶部横幅图")

    def import_icon(self):
        path = filedialog.askopenfilename(
            title="选择窗口图标",
            filetypes=[
                ("图标/图片", "*.ico *.png *.gif *.jpg *.jpeg"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            dest = UI_ASSETS_DIR / f"icon{Path(path).suffix.lower()}"
            shutil.copy2(path, dest)
            self.ui_config["icon"] = str(dest)
            self._save_ui_config()
            if dest.suffix.lower() == ".ico":
                self.root.iconbitmap(str(dest))
            else:
                self._icon_photo = self._load_tk_image(str(dest), 64, 64)
                if self._icon_photo:
                    self.root.iconphoto(True, self._icon_photo)
            self.log(f"已设置窗口图标：{path}")
            self.status.set("已更新窗口图标")
        except Exception as e:
            messagebox.showerror("设置图标失败", str(e))

    def pick_bg_color(self):
        try:
            from tkinter import colorchooser
            color = colorchooser.askcolor(title="选择窗口背景色")
            if color and color[1]:
                self.ui_config["bg_color"] = color[1]
                self.root.configure(bg=color[1])
                self._save_ui_config()
                self.status.set(f"背景色：{color[1]}")
        except Exception as e:
            messagebox.showerror("选色失败", str(e))

    def reset_ui(self):
        self.ui_config = {"banner": "", "icon": "", "bg_color": ""}
        self._save_ui_config()
        self._banner_photo = None
        self.banner_label.configure(image="", text="")
        self.banner_frame.pack_forget()
        try:
            self.root.configure(bg="SystemButtonFace")
        except Exception:
            pass
        self.status.set("已恢复默认界面")
        self.log("界面已恢复默认")

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # 可自定义顶部横幅
        self.banner_frame = ttk.Frame(outer)
        self.banner_label = ttk.Label(self.banner_frame, anchor="center")
        self.banner_label.pack(fill="x")
        # 默认先隐藏，有图时再显示
        # self.banner_frame.pack(...)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")
        ttk.Label(
            header, text="多站搜索 · 批量采集 · 请遵守版权与站点条款", foreground="#666"
        ).pack(side="left", padx=12)
        # 界面自定义按钮
        ttk.Button(header, text="导入横幅图", command=self.import_banner).pack(side="right", padx=2)
        ttk.Button(header, text="导入图标", command=self.import_icon).pack(side="right", padx=2)
        ttk.Button(header, text="背景色", command=self.pick_bg_color).pack(side="right", padx=2)
        ttk.Button(header, text="恢复默认界面", command=self.reset_ui).pack(side="right", padx=2)

        # ---- 搜索区 ----
        search_box = ttk.LabelFrame(outer, text="按游戏名搜索", padding=10)
        search_box.pack(fill="x", pady=(10, 6))

        srow = ttk.Frame(search_box)
        srow.pack(fill="x")
        ttk.Label(srow, text="游戏名：").pack(side="left")
        self.search_keyword = tk.StringVar()
        se = ttk.Entry(srow, textvariable=self.search_keyword, width=28)
        se.pack(side="left", padx=4)
        se.bind("<Return>", lambda e: self.do_search("all"))

        ttk.Button(srow, text="搜全部", command=lambda: self.do_search("all")).pack(side="left", padx=2)
        ttk.Button(srow, text="VNDB", command=lambda: self.do_search("vndb")).pack(side="left", padx=1)
        ttk.Button(srow, text="Bangumi", command=lambda: self.do_search("bangumi")).pack(side="left", padx=1)
        ttk.Button(srow, text="Shionlib", command=lambda: self.do_search("shionlib")).pack(side="left", padx=1)
        ttk.Button(srow, text="SearchGal", command=lambda: self.do_search("searchgal")).pack(side="left", padx=1)
        ttk.Button(srow, text="鲲Gal", command=lambda: self.do_search("kungal")).pack(side="left", padx=1)
        ttk.Button(srow, text="TouchGal", command=lambda: self.do_search("touchgal")).pack(side="left", padx=1)

        srow2 = ttk.Frame(search_box)
        srow2.pack(fill="x", pady=(6, 0))
        ttk.Button(srow2, text="把勾选结果加入下方网址", command=self.add_selected_to_urls).pack(side="left", padx=2)
        ttk.Button(srow2, text="全选", command=self.select_all_search).pack(side="left", padx=2)
        ttk.Button(srow2, text="清空搜索结果", command=self.clear_search).pack(side="left", padx=2)
        ttk.Button(srow2, text="浏览器打开勾选链接", command=self.open_selected_search).pack(side="left", padx=8)
        ttk.Label(
            srow2,
            text="SearchGal/部分站点含第三方资源链接，请自行判断合法性",
            foreground="#a60",
        ).pack(side="right")

        scols = ("sel", "source", "title", "released", "score", "url")
        self.search_tree = ttk.Treeview(
            search_box, columns=scols, show="headings", height=7, selectmode="extended"
        )
        for col, text, w, anc in [
            ("sel", "选", 36, "center"),
            ("source", "来源", 100, "center"),
            ("title", "标题", 320, "w"),
            ("released", "发售", 90, "center"),
            ("score", "评分", 55, "center"),
            ("url", "链接", 300, "w"),
        ]:
            self.search_tree.heading(col, text=text)
            self.search_tree.column(col, width=w, anchor=anc)
        self.search_tree.pack(fill="x", pady=(8, 0))
        self.search_tree.bind("<Button-1>", self._on_search_click)
        self.search_tree.bind("<Double-1>", self._toggle_search_check)

        # ---- 网址 ----
        ttk.Label(outer, text="目标网址（一行一个；可从上方搜索加入）").pack(anchor="w", pady=(8, 4))
        self.urls = tk.Text(outer, height=4, wrap="word")
        self.urls.pack(fill="x")
        self.urls.insert("1.0", "https://vndb.org/v17\n")

        # ---- 设置 ----
        settings = ttk.LabelFrame(outer, text="采集设置", padding=8)
        settings.pack(fill="x", pady=6)
        row1 = ttk.Frame(settings)
        row1.pack(fill="x", pady=2)
        self.min_delay = tk.StringVar(value="2")
        self.max_delay = tk.StringVar(value="5")
        self.timeout = tk.StringVar(value="20")
        self.max_pages = tk.StringVar(value="30")
        for label, var in [("最短间隔", self.min_delay), ("最长间隔", self.max_delay),
                           ("超时", self.timeout), ("最多网页", self.max_pages)]:
            ttk.Label(row1, text=label).pack(side="left")
            ttk.Entry(row1, textvariable=var, width=6).pack(side="left", padx=(3, 12))
        self.follow = tk.BooleanVar(value=False)
        self.same_domain = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="跟进站内链接", variable=self.follow).pack(side="left")
        ttk.Checkbutton(row1, text="仅同域", variable=self.same_domain).pack(side="left", padx=6)

        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="UA").pack(side="left")
        self.ua = tk.StringVar(value="browser")
        ttk.Combobox(row2, textvariable=self.ua, state="readonly", width=10,
                     values=("browser", "custom")).pack(side="left", padx=4)
        self.custom_ua = tk.StringVar(value=DEFAULT_UA)
        ttk.Entry(row2, textvariable=self.custom_ua).pack(side="left", fill="x", expand=True, padx=4)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(2, 4))
        self.start_btn = ttk.Button(actions, text="▶ 开始采集", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="■ 停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(actions, text="导出 JSON", command=lambda: self.export("json")).pack(side="left", padx=3)
        ttk.Button(actions, text="导出 CSV", command=lambda: self.export("csv")).pack(side="left", padx=3)
        ttk.Button(actions, text="导出 HTML", command=lambda: self.export("html")).pack(side="left", padx=3)
        ttk.Button(actions, text="下载已采集图片", command=self.download_images).pack(side="left", padx=3)

        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 4))
        self.status = tk.StringVar(value="就绪 — 输入游戏名搜索，或直接粘贴网址采集")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w")

        panes = ttk.Panedwindow(outer, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(6, 0))
        log_frame = ttk.LabelFrame(panes, text="运行日志", padding=6)
        result_frame = ttk.LabelFrame(panes, text="采集结果（双击打开）", padding=6)
        panes.add(log_frame, weight=2)
        panes.add(result_frame, weight=3)
        self.logbox = tk.Text(log_frame, height=6, state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            result_frame, columns=("title", "url", "images", "videos"), show="headings", height=6
        )
        for c, t, w in [("title", "标题", 260), ("url", "网址", 480),
                        ("images", "图片", 55), ("videos", "视频", 55)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("images", "videos") else "w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.open_selected)

    # ---------- 搜索 ----------
    def do_search(self, source="all"):
        kw = self.search_keyword.get().strip()
        if not kw:
            messagebox.showinfo("提示", "请输入游戏名")
            return
        self.status.set(f"正在搜索：{kw} …")
        self.log(f"搜索「{kw}」范围={source}")
        self.search_tree.delete(*self.search_tree.get_children())
        self.search_results = []

        def task():
            results = []
            keys = list(SEARCH_SOURCES.keys()) if source == "all" else [source]
            for key in keys:
                if key not in SEARCH_SOURCES:
                    continue
                name, fn = SEARCH_SOURCES[key]
                try:
                    part = fn(kw)
                    results.extend(part)
                    self.events.put(("log", f"  {name}: {len(part)} 条"))
                except Exception as e:
                    self.events.put(("log", f"  {name} 失败: {e}"))
            self.events.put(("search_done", results))

        threading.Thread(target=task, daemon=True).start()

    def _fill_search_tree(self, results):
        self.search_results = results
        self.search_tree.delete(*self.search_tree.get_children())
        for i, item in enumerate(results):
            self.search_tree.insert(
                "", "end", iid=str(i),
                values=(
                    "☐",
                    item.get("source", ""),
                    item.get("title", ""),
                    item.get("released", ""),
                    item.get("score", ""),
                    item.get("url", ""),
                ),
            )
        self.status.set(f"搜索到 {len(results)} 条 — 勾选后加入网址或浏览器打开")
        self.log(f"搜索完成，共 {len(results)} 条")

    def clear_search(self):
        self.search_tree.delete(*self.search_tree.get_children())
        self.search_results = []

    def _on_search_click(self, event):
        if self.search_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.search_tree.identify_column(event.x) != "#1":
            return
        row = self.search_tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _toggle_search_check(self, event):
        row = self.search_tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _toggle_row(self, iid):
        vals = list(self.search_tree.item(iid, "values"))
        if vals:
            vals[0] = "☑" if vals[0] != "☑" else "☐"
            self.search_tree.item(iid, values=vals)

    def select_all_search(self):
        for iid in self.search_tree.get_children():
            vals = list(self.search_tree.item(iid, "values"))
            vals[0] = "☑"
            self.search_tree.item(iid, values=vals)

    def _checked_urls(self):
        urls = []
        for iid in self.search_tree.get_children():
            vals = self.search_tree.item(iid, "values")
            if vals and vals[0] == "☑" and vals[5]:
                urls.append(vals[5])
        return urls

    def add_selected_to_urls(self):
        urls = self._checked_urls()
        if not urls:
            messagebox.showinfo("提示", "请先勾选结果（点左侧 ☐）")
            return
        existing = self.urls.get("1.0", "end").strip()
        block = "\n".join(urls)
        if existing:
            self.urls.insert("end", "\n" + block + "\n")
        else:
            self.urls.delete("1.0", "end")
            self.urls.insert("1.0", block + "\n")
        self.log(f"已加入 {len(urls)} 个网址")
        self.status.set(f"已加入 {len(urls)} 个网址")

    def open_selected_search(self):
        urls = self._checked_urls()
        if not urls:
            # 若无勾选，打开当前高亮
            sel = self.search_tree.selection()
            if sel:
                vals = self.search_tree.item(sel[0], "values")
                if vals and vals[5]:
                    urls = [vals[5]]
        if not urls:
            messagebox.showinfo("提示", "请先勾选或选中一条结果")
            return
        for u in urls[:10]:
            webbrowser.open(u)

    # ---------- 日志 ----------
    def log(self, message):
        self.events.put(("log", message))

    def progress_update(self, done, total):
        self.events.put(("progress", done, total))

    def _poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", ev[1] + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "progress":
                    done, total = ev[1], ev[2]
                    self.progress["maximum"] = max(total, 1)
                    self.progress["value"] = done
                    self.status.set(f"进度 {done}/{total}")
                elif kind == "search_done":
                    self._fill_search_tree(ev[1])
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    # ---------- 采集 ----------
    def start(self):
        raw = self.urls.get("1.0", "end").strip().splitlines()
        normalized = []
        for line in raw:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(("http://", "https://")):
                line = "https://" + line
            normalized.append(line)
        if not normalized:
            messagebox.showinfo("提示", "请填写网址，或先搜索再加入")
            return
        try:
            low, high = float(self.min_delay.get()), float(self.max_delay.get())
            timeout, max_pages = int(self.timeout.get()), int(self.max_pages.get())
        except ValueError:
            messagebox.showerror("错误", "请填写正确数字")
            return
        if low > high:
            low, high = high, low
        ua = DEFAULT_UA if self.ua.get() == "browser" else self.custom_ua.get().strip()

        self.stop_event.clear()
        self.results = []
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.log(f"开始采集 {len(normalized)} 个网址")

        def task():
            crawler = Crawler(self.log, self.progress_update, self.stop_event.is_set)
            results = crawler.run(
                normalized, ua, low, high, timeout, max_pages,
                self.follow.get(), self.same_domain.get(),
            )
            self.results = results
            self.events.put(("log", f"采集结束，共 {len(results)} 个网页"))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()
        self.root.after(100, self._check_finished)

    def _check_finished(self):
        if self.worker and not self.worker.is_alive():
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            for item in self.results:
                self.tree.insert("", "end", values=(
                    item.get("title") or "(无标题)", item.get("url"),
                    len(item.get("images", [])), len(item.get("videos", [])),
                ))
            self.status.set(f"完成，共 {len(self.results)} 条")
        else:
            self.root.after(200, self._check_finished)

    def stop(self):
        self.stop_event.set()
        self.log("已请求停止")

    def open_selected(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if len(values) > 1 and values[1]:
            webbrowser.open(values[1])

    def export(self, kind):
        if not self.results:
            messagebox.showinfo("暂无数据", "请先采集")
            return
        ext = {"json": ".json", "csv": ".csv", "html": ".html"}[kind]
        path = filedialog.asksaveasfilename(
            defaultextension=ext, filetypes=[(kind.upper(), "*" + ext)],
            initialfile="galgame_results" + ext,
        )
        if not path:
            return
        try:
            if kind == "json":
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.results, f, ensure_ascii=False, indent=2)
            elif kind == "csv":
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        "title", "url", "final_url", "description", "body",
                        "links", "images", "videos", "collected_at",
                    ])
                    writer.writeheader()
                    for item in self.results:
                        row = dict(item)
                        for field in ("links", "images", "videos"):
                            row[field] = json.dumps(row.get(field, []), ensure_ascii=False)
                        writer.writerow(row)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("<!doctype html><meta charset='utf-8'><title>采集报告</title><body>")
                    f.write("<h1>采集报告</h1>")
                    for item in self.results:
                        f.write(f"<hr><h2>{self._esc(item.get('title',''))}</h2>")
                        f.write(f"<p><a href='{self._esc(item.get('url',''))}'>{self._esc(item.get('url',''))}</a></p>")
                        f.write(f"<p>{self._esc(item.get('description',''))}</p>")
                        f.write(f"<pre>{self._esc(item.get('body',''))}</pre>")
                    f.write("</body>")
            messagebox.showinfo("成功", path)
        except Exception as e:
            messagebox.showerror("失败", str(e))

    @staticmethod
    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def download_images(self):
        images, seen = [], set()
        for item in self.results:
            for img in item.get("images", []):
                u = img.get("url")
                if u and u not in seen:
                    seen.add(u)
                    images.append(u)
        if not images:
            messagebox.showinfo("提示", "没有图片")
            return
        folder = filedialog.askdirectory(title="保存图片到")
        if not folder:
            return

        def task():
            ok = 0
            session = requests.Session()
            for i, url in enumerate(images, 1):
                if self.stop_event.is_set():
                    break
                try:
                    name = os.path.basename(urlparse(url).path) or f"img_{i}.jpg"
                    name = re.sub(r'[<>:"/\\|?*]', "_", name)[:80]
                    path = os.path.join(folder, f"{i:03d}_{name}")
                    r = session.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=25, stream=True)
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                f.write(chunk)
                    ok += 1
                    self.events.put(("log", f"图片 {ok}/{len(images)}"))
                except Exception as e:
                    self.events.put(("log", f"图片失败: {e}"))
                time.sleep(random.uniform(0.2, 0.6))
            self.events.put(("log", f"图片完成 {ok}/{len(images)}"))

        self.stop_event.clear()
        threading.Thread(target=task, daemon=True).start()


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
