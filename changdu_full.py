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


def detail_to_text(data: dict) -> str:
    """把详情 JSON 的 skeleton HTML 转纯文本。"""
    content = json.loads(data["content"])
    text_html = (content.get("skeleton") or {}).get("data", "")
    text = re.sub(r"<[^>]+>", "\n", text_html)
    text = html.unescape(text)
    return re.sub(r"\n{2,}", "\n\n", text).strip()


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

        text = detail_to_text(data)
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
