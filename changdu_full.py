#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄小说（changdunovel.com）分享链接 → 全文 txt，直接下载
用法：
    python3 changdu_full.py <分享链接>
    python3 changdu_full.py https://changdunovel.com/t/5YhKq0w46-8/

流程（纯 HTTP，不用浏览器、不用登录、不用密码）：
    1. 跟随短链跳转，从最终 URL 抠 book_id（post_id / report_params）
    2. 调详情接口拿全文 JSON，剥掉 HTML 标签成纯文本
    3. 按「小说名_作者名.txt」存到当前目录

只用 Python 标准库，不用装任何东西。
"""

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")
DETAIL_API = ("https://changdunovel.com/reading/ugc/postdata/detail/v1/"
              "?post_id={book_id}&aid=1967")


def http_get(url: str, timeout: int = 20) -> tuple[str, bytes]:
    """GET，返回 (最终 URL, 响应体)。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl(), resp.read()


def extract_book_id(final_url: str) -> str | None:
    """从最终 URL 里抠 book_id。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)

    rp = qs.get("report_params", [None])[0]
    if rp:
        try:
            data = json.loads(urllib.parse.unquote(rp))
            if data.get("content_id_key") == "book_id" and data.get("content_id"):
                return str(data["content_id"])
        except (json.JSONDecodeError, AttributeError):
            pass

    post_id = qs.get("post_id", [None])[0]
    if post_id and post_id.isdigit():
        return post_id

    m = re.search(r"book_id=(\d+)", final_url)
    return m.group(1) if m else None


def fetch_detail(book_id: str) -> dict:
    """调详情接口，返回 data 字典。"""
    _, body = http_get(DETAIL_API.format(book_id=book_id))
    payload = json.loads(body.decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"接口返回异常：{payload.get('message')}")
    return payload["data"]


def fetch_book_page(relate_book_id: str) -> dict | None:
    # 抓书页 fanqienovel.com/page/{book_id}，取书本级字段
    # wordNumber / readCount / lastPublishTime / abstract
    # 拿不到（书已删 / 无关联书）返回 None，走帖子级回退
    url = "https://fanqienovel.com/page/" + relate_book_id
    try:
        _, body = http_get(url, timeout=20)
        page = body.decode("utf-8", errors="replace")
    except Exception:
        return None

    def _num(pat):
        m = re.search(pat, page)
        return int(m.group(1)) if m else None

    abstract = None
    m = re.search(r'"abstract":"((?:[^"\\]|\\.)*)"', page)
    if m:
        try:
            abstract = json.loads('"' + m.group(1) + '"')
        except Exception:
            abstract = m.group(1)
    return {
        "word_number": _num(r'"wordNumber":(\d+)'),
        "read_count": _num(r'"readCount":(\d+)'),
        "last_publish_time": _num(r'"lastPublishTime":"(\d+)"'),
        "abstract": abstract,
    }


def format_word_count(n: int) -> str:
    # >= 1 万字显示 X.X 万字，否则 N 字
    if n >= 10000:
        s = f"{n / 10000:.1f}".rstrip("0").rstrip(".")
        return s + " 万字"
    return str(n) + " 字"


def format_update_time(ts: int) -> str:
    # Unix 秒 -> 北京时间 YYYY-MM-DD HH:MM
    cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(ts, tz=cst).strftime("%Y-%m-%d %H:%M")


def detail_to_paras(data: dict) -> list[str]:
    """把详情 JSON 的 skeleton HTML 转段落列表（每段一行，已去 HTML 标签）。"""
    content = json.loads(data["content"])
    text_html = (content.get("skeleton") or {}).get("data", "")
    text = re.sub(r"<[^>]+>", "\n", text_html)
    text = html.unescape(text)
    paras = [p.strip() for p in text.split("\n")]
    return [p for p in paras if p]


def detail_to_text(data: dict) -> str:
    """把详情 JSON 的 skeleton HTML 转纯文本（段落间空一行）。"""
    return "\n\n".join(detail_to_paras(data))


SEP_LINE = "—" * 32


def build_synopsis(paras: list[str], truncate_num: int) -> str:
    """简介 = 正文开头按 truncate_word_num 截取，段落对齐，段间单换行。"""
    if not truncate_num or truncate_num <= 0:
        truncate_num = 300
    acc = 0
    out = []
    for p in paras:
        out.append(p)
        acc += len(p)
        if acc >= truncate_num:
            break
    return "\n".join(out)


def count_chapters(text: str) -> int:
    """数正文里的“第X章”标题；没有就是单章短篇，记 1 章。"""
    heads = re.findall(r"第[一二三四五六七八九十百千\d]+章", text)
    return len(heads) if heads else 1


def build_full_text(data: dict, book_id: str, book_page: dict | None = None) -> str:
    # 按铁柱笔记格式组装全文：书籍信息 + 简介 + 分隔线 + 正文
    # 有书页数据（关联书未删）用书本级字段：relate_book_id / wordNumber /
    # readCount / lastPublishTime / abstract；否则用帖子级字段回退
    title = (data.get("title") or f"book_{book_id}").strip()
    author = ((data.get("user_info") or {}).get("user_name") or "").strip()
    relate_id = (data.get("relate_book_id") or "").strip()
    display_id = relate_id if relate_id else book_id
    if book_page and book_page.get("word_number"):
        total_words = book_page["word_number"]
    else:
        total_words = data.get("total_word_num") or 0
    if book_page and book_page.get("read_count") is not None:
        read_count = book_page["read_count"]
    else:
        read_count = data.get("show_pv") or 0
    update_str = ""
    if book_page and book_page.get("last_publish_time"):
        update_str = format_update_time(book_page["last_publish_time"])
    paras = detail_to_paras(data)
    body = "\n\n".join(paras)
    chapter_count = count_chapters(body)
    header_lines = [
        "书籍信息",
        "",
        "书名：" + title,
        "作者：" + author,
        # 这类短篇是一次性发完的单篇，接口不给连载状态，默认已完结
        "连载状态：已完结",
        "字数：" + format_word_count(total_words),
        "章节数：" + str(chapter_count) + " 章",
        "书籍 ID：" + display_id,
        "阅读量：" + str(read_count),
    ]
    if update_str:
        header_lines.append("最后更新时间：" + update_str)
    header_lines += ["", "【简介】"]
    header = "\n".join(header_lines)
    if book_page and book_page.get("abstract"):
        synopsis = book_page["abstract"]
    else:
        synopsis = build_synopsis(paras, data.get("truncate_word_num") or 0)
    if update_str:
        # 书本级：书名 + 章节更新时间 + 正文
        tail = title + "\n\n章节更新时间：" + update_str + "\n\n" + body
    else:
        # 帖子级回退：短篇单章前面补一个“第一章”
        has_head = any(re.fullmatch(r"第[一二三四五六七八九十百千\d]+章", p) for p in paras)
        chapter_head = "" if has_head else "第一章\n\n"
        tail = chapter_head + body
    return header + "\n" + synopsis + "\n\n" + SEP_LINE + "\n\n" + tail + "\n\n"


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python3 changdu_full.py <番茄小说分享链接>")
        print("示例：python3 changdu_full.py https://changdunovel.com/t/5YhKq0w46-8/")
        return 2

    url = argv[1].strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    t0 = time.time()
    try:
        final_url, _ = http_get(url)
        book_id = extract_book_id(final_url)
        if not book_id:
            print("没找到 book_id。最终 URL：")
            print(final_url)
            return 1
        print(f"1: {book_id}")

        data = fetch_detail(book_id)
        title = (data.get("title") or f"book_{book_id}").strip()
        author = ((data.get("user_info") or {}).get("user_name") or "").strip()
        total_words = data.get("total_word_num") or 0
        relate_id = (data.get("relate_book_id") or "").strip()
        book_page = fetch_book_page(relate_id) if relate_id else None
        if book_page and book_page.get("word_number"):
            total_words = book_page["word_number"]

        text = build_full_text(data, book_id, book_page)
        if not text:
            print("接口有返回，但正文为空。")
            return 1

        fname = safe_filename(f"{title}_{author}.txt") if author \
            else safe_filename(f"{title}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"《{title}》 作者：{author}  共 {len(text)} 字")
        print(f"已保存：{fname}")
        if total_words and len(text) < total_words * 0.8:
            print(f"提醒：接口标的总字数约 {total_words}，这次只拿到 {len(text)} 字，"
                  "可能是不完整的长篇。")
        print(f"用时 {time.time() - t0:.1f} 秒")
        return 0
    except Exception as e:  # noqa: BLE001 - 直接报给用户看
        print(f"失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
