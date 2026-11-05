// Cloudflare Worker：番茄小说全文下载中转
// 部署：Cloudflare Dashboard → Workers & Pages → Create → 粘贴本文件 → Deploy
// 用法：GET https://<你的 worker 域名>/?url=<分享链接>
// 返回：纯文本全文（Content-Disposition 带文件名「小说名_作者名.txt」）

const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

function unescapeHtml(s) {
  return s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&nbsp;/g, ' ');
}

function safeFilename(name) {
  return name.replace(/[\\/:*?"<>|]/g, '_').trim();
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

    const share = new URL(request.url).searchParams.get('url');
    if (!share) {
      return new Response('缺少 url 参数', { status: 400, headers: cors });
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
        return new Response(JSON.stringify({ error: '没找到 book_id' }), {
          status: 400, headers: { ...cors, 'Content-Type': 'application/json' },
        });
      }

      // 2. 详情接口拿全文
      const api = `https://changdunovel.com/reading/ugc/postdata/detail/v1/?post_id=${bookId}&aid=1967`;
      const detail = await (await fetch(api, { headers: { 'User-Agent': UA } })).json();
      if (detail.code !== 0) {
        return new Response(JSON.stringify({ error: '接口返回异常：' + detail.message }), {
          status: 502, headers: { ...cors, 'Content-Type': 'application/json' },
        });
      }
      const data = detail.data;
      const title = (data.title || 'book_' + bookId).trim();
      const author = ((data.user_info || {}).user_name || '').trim();
      const inner = JSON.parse(data.content);
      let text = (inner.skeleton?.data || '').replace(/<[^>]+>/g, '\n');
      text = unescapeHtml(text).replace(/\n{2,}/g, '\n\n').trim();
      if (!text) {
        return new Response(JSON.stringify({ error: '接口有返回，但正文为空' }), {
          status: 502, headers: { ...cors, 'Content-Type': 'application/json' },
        });
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
    } catch (e) {
      return new Response(JSON.stringify({ error: '失败：' + e.message }), {
        status: 500, headers: { ...cors, 'Content-Type': 'application/json' },
      });
    }
  },
};
