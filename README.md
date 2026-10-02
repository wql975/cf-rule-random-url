# Cloudflare Random Image API

基于 **Cloudflare Pages 静态托管 + Pages Functions** 的多分类随机图片 API。
零成本，图片流量由 Pages CDN 提供（免费且不限流量）。

## 原理

1. 每个分类的素材转成标准 JPEG，按 `0.jpg`、`1.jpg` … `N-1.jpg` 放进 `dist/<分类>/`。
   **文件数 = 素材数，不做任何重复填充。**
2. `dist` 作为静态资产部署到 Cloudflare Pages。
3. `functions/[category].js` 收到 `/<分类>` 请求时随机取一个序号，
   302 跳转到 `/<分类>/<序号>.jpg`。函数只做一次取随机数，开销极小。

访问 `https://你的域名/pc` → 302 → `https://你的域名/pc/742.jpg`

### 与旧版（Transform Rules）的区别

| | 旧版 Transform Rules | 本版 Pages Functions |
| --- | --- | --- |
| 文件数 | 必须凑满 16^N（3 位即 4096 个） | = 素材数 |
| 素材少于槽位时 | 大量重复填充 | 无重复 |
| 每张图出现概率 | 不均，部分图高 33% | 完全均等 |
| 图片流量 | Pages CDN 免费 | Pages CDN 免费 |
| 请求开销 | 无 | 每次消耗 1 次 Functions 调用（免费额度 10 万/天） |

## 目录结构

```text
├── oriImg/              # 原始素材，一个子目录 = 一个分类
│   └── pc/              # 例如：电脑壁纸
├── dist/                # 生成的静态资产（部署这个目录）
│   ├── pc/0.jpg … N-1.jpg
│   └── _headers
├── functions/           # Pages Functions（自动生成，勿手改）
│   ├── _counts.js       # 分类 -> 图片数量
│   └── [category].js    # 动态路由：随机 302
├── gen_img.py           # 生成脚本
└── wrangler.jsonc
```

## 使用步骤

### 1. 准备素材

在 `oriImg` 下建分类目录，把图片放进去（支持 jpg / png / webp / gif / bmp）：

```text
oriImg/pc/      电脑壁纸
oriImg/mobile/  手机壁纸
```

### 2. 生成资源

```bash
python gen_img.py
```

脚本会统一转 JPEG、修正 EXIF 方向、透明区补白、缩放压缩，
然后按 `0.jpg … N-1.jpg` 输出，并生成 `functions/`。

常用参数：

```bash
python gen_img.py --only pc                       # 只处理 pc 分类
python gen_img.py --max-width 1600 --quality 80   # 更小体积
python gen_img.py --reuse                         # 复用预处理结果，加快重跑
```

### 3. 部署到 Cloudflare Pages

```bash
npx wrangler pages deploy dist
```

或在 Pages 控制台选择「直接上传」，上传 `dist` 目录内容，
并确保 `functions/` 目录一并部署（Wrangler 会自动处理）。

### 4. 访问

```text
https://你的域名/pc        # 随机一张电脑壁纸
https://你的域名/mobile    # 随机一张手机壁纸
```

## 注意事项

- **Functions 调用额度**：免费版每天 10 万次请求，个人使用足够。
  若需要真正的无限请求，可改用 Transform Rules 方案（代价是每类文件数膨胀到 4096）。
- **分类路径禁止缓存**：`functions/[category].js` 已设置 `Cache-Control: no-store`，
  否则浏览器会缓存 302 结果，导致每次都拿到同一张图。
- **换素材后必须重跑** `gen_img.py`，`functions/_counts.js` 里的数量才会同步更新。
- Pages 免费版单文件上限 25MB、总文件数 20000，脚本会自动检查并告警。
