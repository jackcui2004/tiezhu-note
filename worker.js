// Cloudflare Worker：番茄小说全文下载中转 + 短篇分类浏览中转
// 部署：Cloudflare Dashboard → Workers & Pages → tiezhu-novel → Edit code → 粘贴本文件 → Deploy
// 用法：
//   下载全文（分享链接）：GET https://<你的 worker 域名>/?url=<分享链接>
//   下载全文（book_id）：GET https://<你的 worker 域名>/?book_id=<数字>
//   短篇分类列表：GET https://<你的 worker 域名>/shorts?tag=<分类名>
//     返回 JSON：{source, tag, updated_at, count, items:[{id, position, title, author{name,avatar}, excerpt, cover, horizontal_cover, topics, reading_count, reading_minutes, word_count, metrics{views,likes,comments,favorites}, source_url}]}
//     数据源：FanqieRankTracker 的短篇推荐中转（公开番茄数据，每分类 10 条）；本 Worker 加 1 小时缓存，避免频繁调上游。

const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

function unescapeHtml(s) {
  return s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&nbsp;/g, ' ');
}

function safeFilename(name) {
  // 顺带把「标题末尾句号 + 下划线」压成一个下划线，避免出现「离婚了._作者.txt」这种文件名
  return name.replace(/[\\/:*?"<>|]/g, '_').replace(/\.+_/g, '_').replace(/\.+(\.[^.]+)$/, '$1').trim();
}

function jsonResp(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, 'Content-Type': 'application/json; charset=utf-8' },
  });
}

// 按 book_id 调详情接口，返回全文下载响应
async function downloadByBookId(bookId, cors) {
  const api = `https://changdunovel.com/reading/ugc/postdata/detail/v1/?post_id=${bookId}&aid=1967`;
  const detail = await (await fetch(api, { headers: { 'User-Agent': UA } })).json();
  if (detail.code !== 0) {
    return jsonResp({ error: '接口返回异常：' + detail.message }, 502, cors);
  }
  const data = detail.data;
  const title = (data.title || 'book_' + bookId).trim();
  const author = ((data.user_info || {}).user_name || '').trim();
  const inner = JSON.parse(data.content);
  let text = (inner.skeleton?.data || '').replace(/<[^>]+>/g, '\n');
  text = unescapeHtml(text).replace(/\n{2,}/g, '\n\n').trim();
  if (!text) {
    return jsonResp({ error: '接口有返回，但正文为空' }, 502, cors);
  }
  const filename = safeFilename(author ? `${title}_${author}.txt` : `${title}.txt`);
  return new Response(text, {
    headers: {
      ...cors,
      'Content-Type': 'text/plain; charset=utf-8',
      'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`,
      'X-Title': encodeURIComponent(title),
      'X-Author': encodeURIComponent(author),
      'X-Book-Id': bookId,
      'X-Words': String(text.length),
    },
  });
}

// 短篇分类列表：中转 FanqieRankTracker 的短篇推荐接口，1 小时缓存
async function shortsList(tag, cors) {
  const upstream = `https://fanqie-shorts-proxy.wzj-fzr.workers.dev/api/shorts?tag=${encodeURIComponent(tag)}`;
  // 先读缓存（按 tag 分 key，1 小时）
  const cache = caches.default;
  const cacheKey = new Request(upstream, { method: 'GET' });
  const cached = await cache.match(cacheKey);
  if (cached) return new Response(cached.body, { headers: { ...cors, 'Content-Type': 'application/json; charset=utf-8' } });

  const res = await fetch(upstream, {
    headers: {
      // 上游中转只认它自己页面的来源，必须带上这两个头，否则 403
      'Origin': 'https://wen1701.github.io',
      'Referer': 'https://wen1701.github.io/FanqieRankTracker/shorts.html',
      'User-Agent': UA,
    },
  });
  if (!res.ok) {
    return jsonResp({ error: '短篇数据源暂时不可用（上游 ' + res.status + '），稍后再试' }, 502, cors);
  }
  const data = await res.json();
  if (data.error) {
    return jsonResp({ error: '短篇数据源返回：' + data.error }, 502, cors);
  }
  const body = JSON.stringify({ ...data, fetched_at: new Date().toISOString() });
  // 写缓存（Cache-Control: max-age=3600）
  const toCache = new Response(body, {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=3600' },
  });
  await cache.put(cacheKey, toCache.clone());
  return new Response(body, { headers: { ...cors, 'Content-Type': 'application/json; charset=utf-8' } });
}

export default {
  async fetch(request) {
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      // 关键：没有这一行，浏览器里 fetch 读不到 X-Title 等自定义头，
      // 页面会显示书名/作者/字数全空、文件名 fallback 成「小说.txt」
      'Access-Control-Expose-Headers': 'Content-Disposition, X-Title, X-Author, X-Book-Id, X-Words',
    };
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: cors });
    }

    const url = new URL(request.url);
    const params = url.searchParams;

    // 短篇分类列表
    if (url.pathname === '/shorts' || url.pathname === '/shorts/') {
      const tag = (params.get('tag') || '').trim();
      if (!tag) return jsonResp({ error: '缺少 tag 参数' }, 400, cors);
      try {
        return await shortsList(tag, cors);
      } catch (e) {
        return jsonResp({ error: '失败：' + e.message }, 500, cors);
      }
    }

    // 直接按 book_id 下载
    const directId = params.get('book_id');
    if (directId) {
      if (!/^\d+$/.test(directId)) return jsonResp({ error: 'book_id 不是纯数字' }, 400, cors);
      try {
        return await downloadByBookId(directId, cors);
      } catch (e) {
        return jsonResp({ error: '失败：' + e.message }, 500, cors);
      }
    }

    // 原有：按分享链接下载
    const share = params.get('url');
    if (!share) {
      return new Response('用法：?url=<分享链接> 下载全文；?book_id=<数字> 下载全文；/shorts?tag=<分类> 看短篇列表', { status: 400, headers: cors });
    }

    try {
      // 1. 跟随短链跳转，从最终 URL 抠 book_id
      const res = await fetch(share, { headers: { 'User-Agent': UA }, redirect: 'follow' });
      const qs = new URL(res.url).searchParams;
      let bookId = null;
      const rp = qs.get('report_params');
      if (rp) {
        try {
          const d = JSON.parse(decodeURIComponent(rp));
          if (d.content_id_key === 'book_id' && d.content_id) bookId = String(d.content_id);
        } catch (e) { /* 忽略，继续用 post_id */ }
      }
      if (!bookId) bookId = qs.get('post_id');
      if (!bookId || !/^\d+$/.test(bookId)) {
        return jsonResp({ error: '没找到 book_id' }, 400, cors);
      }
      return await downloadByBookId(bookId, cors);
    } catch (e) {
      return jsonResp({ error: '失败：' + e.message }, 500, cors);
    }
  },
};
