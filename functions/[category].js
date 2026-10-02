// 自动生成，请勿手改；重新运行 gen_img.py 会覆盖
// 匹配 /<分类> ：随机跳转到该分类下的一张图片
import { COUNTS } from './_counts.js';

export async function onRequest(context) {
  const category = context.params.category;
  const count = COUNTS[category];

  if (!count) {
    return new Response('Not Found', { status: 404 });
  }

  const index = Math.floor(Math.random() * count);
  const url = new URL(`/${category}/${index}.jpg`, context.request.url);

  return new Response(null, {
    status: 302,
    headers: {
      Location: url.pathname,
      // 禁止缓存，否则浏览器会一直跳到同一张图
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    },
  });
}
